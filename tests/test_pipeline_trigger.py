"""Notify-trigger: fire on qualifying-set CHANGE (new/dropped) + periodic digest,
dedup unchanged sets, JSON backend only."""

import builtins

import pytest

import i2i_watch.pipeline as pipeline
import i2i_watch.storage as storage


@pytest.fixture
def json_backend(tmp_path, monkeypatch):
    real_import = builtins.__import__

    def no_firebase(name, *args, **kwargs):
        if name == "firebase_admin" or name.startswith("firebase_admin."):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_firebase)
    monkeypatch.setattr(storage, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(storage, "_mode", None)
    monkeypatch.setattr(storage, "_app", None)
    monkeypatch.setattr(storage, "_db", None)
    monkeypatch.setenv("STARTUP_JITTER_MS", "0")
    monkeypatch.setenv("NOTIFY_MIN_RATE_PCT", "40")
    monkeypatch.setenv("TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("NTFY_ENABLED", "0")
    monkeypatch.setattr(pipeline, "_dispatch_invest", lambda: None)
    monkeypatch.delenv("I2I_DIGEST", raising=False)
    monkeypatch.delenv("I2I_DIGEST_HOURS", raising=False)
    return tmp_path


@pytest.fixture
def capture_notify(monkeypatch):
    sent = []

    def fake_notify_all(loans, stats, dashboard_url, threshold, **kwargs):
        sent.append([str(ln["loanId"]) for ln in loans])
        return {"telegram": True, "ntfy": False}

    monkeypatch.setattr(pipeline, "notify_all", fake_notify_all)
    return sent


def _loan(lid, rate):
    return {
        "pl_bloan_id": str(lid),
        "pl_id": f"7{lid}",
        "pl_user_id": f"88{lid}",
        "pl_applicable_rate": f"{rate:.2f}",
        "product_name": "Regular Loans",
        "pl_amt": "50000",
        "pl_amt_left": "20000",
        "pl_status": 1,
        "usr_cibil_score": "742",
        "bloan_i2i_category": "b",
    }


def test_first_qualifying_set_notifies(json_backend, capture_notify):
    raw = [_loan("1", 50), _loan("2", 20)]
    pipeline.run(raw_rows=raw)
    assert capture_notify == [["1"]]
    assert storage.load_notify_state()["qualifyingIds"] == ["1"]


def test_unchanged_set_stays_silent(json_backend, capture_notify):
    raw = [_loan("1", 50)]
    pipeline.run(raw_rows=raw)
    pipeline.run(raw_rows=raw)  # same set second time
    assert capture_notify == [["1"]]  # only once


def test_new_qualifying_loan_notifies_new_only(json_backend, capture_notify):
    pipeline.run(raw_rows=[_loan("1", 50)])
    pipeline.run(raw_rows=[_loan("1", 50), _loan("2", 60)])
    assert capture_notify == [["1"], ["2"]]  # new-only: never re-send loan 1
    assert storage.load_notify_state()["qualifyingIds"] == ["1", "2"]


def test_dropped_qualifying_loan_stays_silent(json_backend, capture_notify):
    pipeline.run(raw_rows=[_loan("1", 50), _loan("2", 60)])
    pipeline.run(raw_rows=[_loan("1", 50)])  # 2 dropped, no new
    assert capture_notify == [["1", "2"]]  # no notify on drop-only


def test_sent_list_records_only_notified_loans_not_all_qualifying(json_backend, capture_notify):
    """Regression: marking ALL qualifying loans 'sent' when only the NEW ones were
    notified silently absorbs loans that never fired (the 71.5% loan that never
    reached Telegram). notifications-sent must contain ONLY actually-notified ids."""
    pipeline.run(raw_rows=[_loan("1", 50)])                    # notify loan 1
    pipeline.run(raw_rows=[_loan("1", 50), _loan("2", 71)])    # NEW loan 2 -> notify 2 only
    sent = {str(x) for x in storage._load_json("notifications-sent.json", [])}
    assert sent == {"1", "2"}                                   # both, and via real per-run sends
    # The bug would still pass the above; the real guard: a loan that qualifies but
    # was NEVER in a to_send batch must NOT be pre-marked sent. Simulate by making
    # loan 3 appear together with an already-notified set on a digest-less change:
    pipeline.run(raw_rows=[_loan("1", 50), _loan("2", 71), _loan("3", 45)])
    assert capture_notify[-1] == ["3"]                          # only the new one fired
    sent2 = {str(x) for x in storage._load_json("notifications-sent.json", [])}
    assert "3" in sent2 and sent2 == {"1", "2", "3"}            # 3 recorded because it WAS sent


def test_digest_forces_send_when_unchanged(json_backend, capture_notify, monkeypatch):
    monkeypatch.setenv("I2I_DIGEST_HOURS", "0.0")  # off first
    pipeline.run(raw_rows=[_loan("1", 50)])
    # backdate last notify, enable digest
    storage.save_notify_state(["1"], notified_at="2000-01-01T00:00:00Z")
    monkeypatch.setenv("I2I_DIGEST_HOURS", "1")
    pipeline.run(raw_rows=[_loan("1", 50)])  # unchanged but digest due
    assert capture_notify == [["1"], ["1"]]


def test_no_qualifying_no_send(json_backend, capture_notify):
    pipeline.run(raw_rows=[_loan("1", 10)])
    assert capture_notify == []


def test_notification_gate_is_strictly_above_40(json_backend, capture_notify):
    pipeline.run(raw_rows=[_loan("1", 40.0)])
    assert capture_notify == []
    pipeline.run(raw_rows=[_loan("1", 40.01)])
    assert capture_notify == [["1"]]


def test_empty_snapshot_fails_before_state_is_overwritten(json_backend, capture_notify):
    """A transient auth/API/parser outage must not archive the entire active book."""
    pipeline.run(raw_rows=[_loan("1", 50)])
    with pytest.raises(RuntimeError, match="refusing to overwrite active state"):
        pipeline.run(raw_rows=[])
    assert capture_notify == [["1"]]
    assert [str(x["loanId"]) for x in storage.load_active_loans()] == ["1"]


def test_loud_tier_alerts_new_high_loan_even_when_standard_unchanged(
        json_backend, capture_notify, monkeypatch):
    """A NEW >100% loan fires the loud tier immediately even though the standard
    qualifying set is identical (the exact 'no notifications' bug: unchanged set
    + change-only logic = silence forever)."""
    sent_loud = []

    def fake_send_text(text, silent=False):
        if not silent:
            sent_loud.append(text)
        return True

    monkeypatch.setattr(pipeline, "send_telegram_text", fake_send_text)

    # First run: one >50% loan, no high loans -> standard notify only
    pipeline.run(raw_rows=[_loan("1", 60)])
    assert capture_notify == [["1"]]
    assert sent_loud == []

    # Second run: loan 1 was already notified at 60% and is now 120% — the
    # STANDARD set is unchanged ({1}), so change-only stays silent; but the
    # LOUD tier sees it as new and fires. This is the loud tier's unique value.
    pipeline.run(raw_rows=[_loan("1", 120)])
    assert len(sent_loud) == 1
    assert "1" in sent_loud[0] and "120" in sent_loud[0]
    assert capture_notify == [["1"]]  # standard unchanged -> no standard re-send
    # state records both tiers
    st = storage.load_notify_state()
    assert "1" in st.get("highIds", [])


def test_loud_tier_labels_credit_skip_honestly(json_backend, capture_notify, monkeypatch):
    """The loud-tier 'AUTO-INVEST CANDIDATE' alert must label a >100% loan with
    sub-500 credit as SKIP (the investor applies the credit gate, so announcing
    it as a pure candidate would be a lie)."""
    sent_loud = []

    def fake_send_text(text, silent=False):
        if not silent:
            sent_loud.append(text)
        return True

    monkeypatch.setattr(pipeline, "send_telegram_text", fake_send_text)

    # >100% loan with low credit (499): flagged as SKIP, not "will invest"
    low = _loan("21", 130)
    low["usr_cibil_score"] = "499"
    pipeline.run(raw_rows=[low])
    assert len(sent_loud) == 1
    assert "will invest" not in sent_loud[0]
    assert "SKIP" in sent_loud[0]

    # >100% loan with credit 742: flagged as investable
    sent_loud.clear()
    pipeline.run(raw_rows=[_loan("22", 130)])
    assert len(sent_loud) == 1
    assert "will invest" in sent_loud[0]


def test_loud_tier_does_not_respam_existing_high_loan(json_backend, capture_notify, monkeypatch):
    """An already-alerted >100% loan stays silent on later runs (no every-run spam)."""
    sent_loud = []

    def fake_send_text(text, silent=False):
        if not silent:
            sent_loud.append(text)
        return True

    monkeypatch.setattr(pipeline, "send_telegram_text", fake_send_text)
    pipeline.run(raw_rows=[_loan("9", 120)])
    pipeline.run(raw_rows=[_loan("9", 120)])  # same high loan again
    assert len(sent_loud) == 1  # alerted only on the first appearance


def test_failed_loud_alert_retries_next_poll(json_backend, capture_notify, monkeypatch):
    """A transient loud-tier delivery failure must not consume the high loan."""
    attempts = []

    def fake_send_text(text, silent=False):
        if not silent:
            attempts.append(text)
            return len(attempts) > 1
        return True

    monkeypatch.setattr(pipeline, "send_telegram_text", fake_send_text)
    pipeline.run(raw_rows=[_loan("10", 120)])
    assert len(attempts) == 1
    assert storage.load_notify_state().get("highIds", []) == []

    pipeline.run(raw_rows=[_loan("10", 120)])
    assert len(attempts) == 2
    assert storage.load_notify_state()["highIds"] == ["10"]


def test_loud_tier_high_gate_from_env(json_backend, capture_notify, monkeypatch):
    monkeypatch.setenv("NOTIFY_HIGH_RATE_PCT", "150")
    sent_loud = []

    def fake_send_text(text, silent=False):
        if not silent:
            sent_loud.append(text)
        return True

    monkeypatch.setattr(pipeline, "send_telegram_text", fake_send_text)
    pipeline.run(raw_rows=[_loan("9", 120)])  # >100 but NOT >150 -> no loud
    assert sent_loud == []
    pipeline.run(raw_rows=[_loan("9", 160)])  # >150 -> loud
    assert len(sent_loud) == 1


def test_no_bucket_summary_is_ever_sent(json_backend, capture_notify, monkeypatch):
    """The silent loan-rate bucket summary was removed: a changing market must
    never produce a quiet Telegram push, only the loud tier and standard alerts."""
    silent_messages = []

    def fake_send_text(text, silent=False):
        if silent:
            silent_messages.append(text)
        return True

    monkeypatch.setattr(pipeline, "send_telegram_text", fake_send_text)
    pipeline.run(raw_rows=[_loan("15", 15.0)])
    pipeline.run(raw_rows=[_loan("15", 15.0), _loan("31", 31.0)])
    pipeline.run(raw_rows=[_loan("15", 15.0), _loan("31", 45.0)])
    assert silent_messages == []


def test_notify_state_merges_tiers(json_backend):
    storage.save_notify_state(["q"], notified_at="2000-01-01T00:00:00Z", high_ids=["h"])
    storage.save_notify_state(["q2"])
    state = storage.load_notify_state()
    assert state["qualifyingIds"] == ["q2"]
    assert state["highIds"] == ["h"]


def test_digest_helpers(monkeypatch):
    monkeypatch.delenv("I2I_DIGEST", raising=False)
    monkeypatch.setenv("I2I_DIGEST_HOURS", "6")
    assert pipeline._digest_hours() == 6.0
    monkeypatch.delenv("I2I_DIGEST_HOURS", raising=False)
    monkeypatch.setenv("I2I_DIGEST", "true")
    assert pipeline._digest_hours() == 24.0
    monkeypatch.setenv("I2I_DIGEST", "false")
    assert pipeline._digest_hours() == 0.0
    assert pipeline._digest_due(None, 0) is False
    assert pipeline._digest_due(None, 6) is True
    assert pipeline._digest_due("2000-01-01T00:00:00Z", 6) is True


@pytest.mark.parametrize("loud_ok", [True, False])
def test_loud_state_cannot_consume_failed_detailed_alerts(json_backend, monkeypatch, loud_ok):
    storage.save_notify_state([], notified_at=None, high_ids=["obsolete"])
    detailed = []
    monkeypatch.setattr(pipeline, "send_telegram_text", lambda *a, **k: loud_ok)

    def deliver(loans, *args, **kwargs):
        detailed.append([str(loan["loanId"]) for loan in loans])
        return {"telegram": len(detailed) > 1, "ntfy": False}

    monkeypatch.setattr(pipeline, "notify_all", deliver)
    raw = [_loan("1", 120), _loan("2", 60)]
    first = pipeline.run(raw_rows=raw)
    assert storage.load_notify_state()["qualifyingIds"] == []
    assert first["errors"]
    pipeline.run(raw_rows=raw)
    pipeline.run(raw_rows=raw)
    assert detailed == [["1", "2"], ["1", "2"]]
    assert storage.load_notifications_sent() == {"1", "2"}


@pytest.mark.parametrize("failed_channel", ["telegram", "ntfy"])
def test_failed_channel_retries_without_repeating_successful_channel(
        json_backend, monkeypatch, failed_channel):
    monkeypatch.setenv("NTFY_ENABLED", "1")
    attempts = {"telegram": [], "ntfy": []}

    def deliver(loans, *args, channels=None):
        results = {"telegram": False, "ntfy": False}
        for channel in channels or results.keys():
            attempts[channel].append([str(loan["loanId"]) for loan in loans])
            results[channel] = channel != failed_channel or len(attempts[channel]) > 1
        return results

    monkeypatch.setattr(pipeline, "notify_all", deliver)
    for _ in range(3):
        pipeline.run(raw_rows=[_loan("1", 60)])
    other = "ntfy" if failed_channel == "telegram" else "telegram"
    assert attempts[failed_channel] == [["1"], ["1"]]
    assert attempts[other] == [["1"]]


def test_loud_alert_rearms_after_rate_drops_below_gate(json_backend, capture_notify, monkeypatch):
    loud = []
    monkeypatch.setattr(pipeline, "send_telegram_text", lambda text: loud.append(text) or True)
    pipeline.run(raw_rows=[_loan("1", 120)])
    pipeline.run(raw_rows=[_loan("1", 60)])
    assert storage.load_notify_state()["highIds"] == []
    pipeline.run(raw_rows=[_loan("1", 120)])
    assert len(loud) == 2


def test_drop_only_does_not_reset_channel_digest_clock(json_backend, capture_notify, monkeypatch):
    pipeline.run(raw_rows=[_loan("1", 60), _loan("2", 70)])
    state = storage.load_notify_state()
    timestamp = "2000-01-01T00:00:00Z"
    state["notifiedAt"] = timestamp
    for receipt in state.get("channels", {}).values():
        receipt["notifiedAt"] = timestamp
    storage._write_json("notify-state.json", state)
    pipeline.run(raw_rows=[_loan("1", 60)])
    assert storage.load_notify_state()["notifiedAt"] == timestamp
    monkeypatch.setenv("I2I_DIGEST_HOURS", "6")
    pipeline.run(raw_rows=[_loan("1", 60)])
    assert capture_notify == [["1", "2"], ["1"]]


def test_newly_enabled_channel_receives_current_loans(json_backend, monkeypatch):
    attempts = {"telegram": 0, "ntfy": 0}

    def deliver(loans, *args, channels=None):
        results = {"telegram": False, "ntfy": False}
        for channel in channels or results.keys():
            attempts[channel] += 1
            results[channel] = True
        return results

    monkeypatch.setattr(pipeline, "notify_all", deliver)
    pipeline.run(raw_rows=[_loan("1", 60)])
    monkeypatch.setenv("NTFY_ENABLED", "1")
    pipeline.run(raw_rows=[_loan("1", 60)])
    assert attempts == {"telegram": 1, "ntfy": 1}


def test_disabled_channel_retains_receipts_but_receives_unseen_loans(json_backend, capture_notify, monkeypatch):
    pipeline.run(raw_rows=[_loan("1", 60)])
    timestamp = storage.load_notify_state()["channels"]["telegram"]["notifiedAt"]
    monkeypatch.setenv("TELEGRAM_ENABLED", "0")
    pipeline.run(raw_rows=[_loan("1", 60), _loan("2", 70)])
    state = storage.load_notify_state()["channels"]["telegram"]
    assert state == {"qualifyingIds": ["1"], "notifiedAt": timestamp}
    monkeypatch.setenv("TELEGRAM_ENABLED", "1")
    pipeline.run(raw_rows=[_loan("1", 60), _loan("2", 70)])
    pipeline.run(raw_rows=[_loan("1", 60), _loan("2", 70)])
    assert capture_notify == [["1"], ["2"]]


def test_detailed_retries_do_not_repeat_invest_dispatch(json_backend, monkeypatch):
    dispatched = []
    detailed = []
    monkeypatch.setattr(pipeline, "_dispatch_invest", lambda: dispatched.append(True))
    monkeypatch.setattr(pipeline, "send_telegram_text", lambda *a, **k: True)

    def fail_delivery(loans, *args, **kwargs):
        detailed.append([str(loan["loanId"]) for loan in loans])
        return {"telegram": False, "ntfy": False}

    monkeypatch.setattr(pipeline, "notify_all", fail_delivery)
    for _ in range(3):
        pipeline.run(raw_rows=[_loan("1", 120)])
    assert detailed == [["1"], ["1"], ["1"]]
    assert dispatched == [True]


def test_legacy_snapshot_migrates_without_resending(json_backend, capture_notify):
    storage._write_json("notify-state.json", {
        "qualifyingIds": ["1"], "highIds": [], "notifiedAt": "2026-11-01T00:00:00Z",
    })
    pipeline.run(raw_rows=[_loan("1", 60)])
    assert capture_notify == []
    assert storage.load_notify_state()["channels"]["telegram"] == {
        "qualifyingIds": ["1"], "notifiedAt": "2026-11-01T00:00:00Z",
    }


def test_legacy_receipts_survive_migration_while_disabled(json_backend, capture_notify, monkeypatch):
    storage._write_json("notify-state.json", {
        "qualifyingIds": ["1"], "highIds": [], "notifiedAt": "2026-11-01T00:00:00Z",
    })
    monkeypatch.setenv("TELEGRAM_ENABLED", "0")
    pipeline.run(raw_rows=[_loan("1", 60)])
    monkeypatch.setenv("TELEGRAM_ENABLED", "1")
    pipeline.run(raw_rows=[_loan("1", 60)])
    assert capture_notify == []


def test_lowered_high_gate_dispatches_once_despite_failed_delivery(json_backend, monkeypatch):
    dispatched = []
    monkeypatch.setattr(pipeline, "_dispatch_invest", lambda: dispatched.append(True))
    monkeypatch.setattr(pipeline, "send_telegram_text", lambda *a, **k: False)
    monkeypatch.setattr(pipeline, "notify_all", lambda *a, **k: {"telegram": False, "ntfy": False})
    monkeypatch.setenv("NOTIFY_HIGH_RATE_PCT", "150")
    pipeline.run(raw_rows=[_loan("1", 120)])
    monkeypatch.setenv("NOTIFY_HIGH_RATE_PCT", "100")
    pipeline.run(raw_rows=[_loan("1", 120)])
    pipeline.run(raw_rows=[_loan("1", 120)])
    assert dispatched == [True, True]

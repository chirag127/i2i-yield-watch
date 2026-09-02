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
    monkeypatch.delenv("I2I_DIGEST", raising=False)
    monkeypatch.delenv("I2I_DIGEST_HOURS", raising=False)
    return tmp_path


@pytest.fixture
def capture_notify(monkeypatch):
    sent = []

    def fake_notify_all(loans, stats, dashboard_url, threshold):
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


def test_bucket_boundaries_and_snapshot():
    rows = [
        {"loanId": "5", "interestRate": 5.0},
        {"loanId": "10", "interestRate": 10.0},
        {"loanId": "15", "interestRate": 15.0},
        {"loanId": "18", "interestRate": 18.0},
        {"loanId": "29", "interestRate": 29.0},
        {"loanId": "30", "interestRate": 30.0},
        {"loanId": "31", "interestRate": 30.01},
        {"loanId": "40", "interestRate": 40.0},
        {"loanId": "50", "interestRate": 50.0},
        {"loanId": "70", "interestRate": 70.0},
        {"loanId": "100", "interestRate": 100.0},
        {"loanId": "101", "interestRate": 100.01},
    ]
    snapshot = pipeline._bucket_snapshot(rows)
    assert snapshot["0-10"] == ["5"]
    assert snapshot["10-18"] == ["10", "15"]
    assert snapshot["18-24"] == ["18"]
    assert snapshot["24-30"] == ["29"]
    assert snapshot["30-40"] == ["30", "31"]
    assert snapshot["40-50"] == ["40"]
    assert snapshot["50-70"] == ["50"]
    assert snapshot["70-100"] == ["70"]
    assert snapshot["100+"] == ["100", "101"]


def test_bucket_summary_is_silent_change_only_and_has_new_links(
        json_backend, capture_notify, monkeypatch):
    bucket_messages = []

    def fake_send_text(text, silent=False):
        if silent:
            bucket_messages.append(text)
        return True

    monkeypatch.setattr(pipeline, "send_telegram_text", fake_send_text)
    first = _loan("30", 30.01)
    first["pl_user_id"] = "88030"
    pipeline.run(raw_rows=[first])
    assert len(bucket_messages) == 1
    assert "30-40" in bucket_messages[0]
    assert "88030/30" in bucket_messages[0]

    pipeline.run(raw_rows=[first])
    assert len(bucket_messages) == 1

    second = _loan("40", 40.0)
    second["pl_user_id"] = "88040"
    pipeline.run(raw_rows=[first, second])
    assert len(bucket_messages) == 2
    assert "88040/40" in bucket_messages[-1]
    assert "88030/30" not in bucket_messages[-1]


def test_bucket_summary_includes_low_rate_loans(json_backend, capture_notify, monkeypatch):
    """Loans below 30% must appear in the all-rate bucket summary."""
    bucket_messages = []

    def fake_send_text(text, silent=False):
        if silent:
            bucket_messages.append(text)
        return True

    monkeypatch.setattr(pipeline, "send_telegram_text", fake_send_text)
    low = _loan("low1", 15.0)
    low["pl_user_id"] = "12001"
    pipeline.run(raw_rows=[low])
    assert len(bucket_messages) == 1
    assert "10-18" in bucket_messages[0]
    assert "12001" in bucket_messages[0]
    assert "ALL RATES" in bucket_messages[0]

    mid = _loan("mid1", 25.0)
    mid["pl_user_id"] = "12002"
    pipeline.run(raw_rows=[low, mid])
    assert len(bucket_messages) == 2
    assert "18-24" in bucket_messages[-1] or "24-30" in bucket_messages[-1]
    assert "12002" in bucket_messages[-1]


def test_bucket_message_includes_totals_and_averages(json_backend, capture_notify, monkeypatch):
    """Bucket messages must include per-bucket amount left, avg rate, avg credit,
    and a market-wide summary footer."""
    bucket_messages = []

    def fake_send_text(text, silent=False):
        if silent:
            bucket_messages.append(text)
        return True

    monkeypatch.setattr(pipeline, "send_telegram_text", fake_send_text)
    # Two loans: one at 50% (₹20k left) and one at 35% (₹20k left), both credit 742
    pipeline.run(raw_rows=[_loan("a", 50.0), _loan("b", 35.0)])
    msg = bucket_messages[0]
    # Per-bucket stats present
    assert "Amount left" in msg
    assert "Avg rate" in msg
    assert "Avg credit" in msg
    # Market overview footer
    assert "MARKET OVERVIEW" in msg
    assert "Active: 2" in msg
    # Total left should be sum of two ₹20,000 = ₹40,000
    assert "40,000" in msg
    # Avg rate = (50 + 35) / 2 = 42.5%
    assert "42.5%" in msg
    # Avg credit = 742
    assert "742" in msg


def test_bucket_summary_retries_after_failed_delivery(json_backend, capture_notify, monkeypatch):
    attempts = []

    def fake_send_text(_text, silent=False):
        if silent:
            attempts.append(1)
            return len(attempts) > 1
        return True

    monkeypatch.setattr(pipeline, "send_telegram_text", fake_send_text)
    loan = _loan("31", 31)
    pipeline.run(raw_rows=[loan])
    assert len(attempts) == 1
    assert storage.load_notify_state().get("buckets", {}) == {}
    pipeline.run(raw_rows=[loan])
    assert len(attempts) == 2
    assert storage.load_notify_state()["buckets"]["30-40"] == ["31"]


def test_notify_state_merges_tiers(json_backend):
    storage.save_notify_state(["q"], notified_at="2000-01-01T00:00:00Z", high_ids=["h"])
    storage.save_notify_state(["q2"], buckets={"30-40": ["b"]})
    state = storage.load_notify_state()
    assert state["qualifyingIds"] == ["q2"]
    assert state["highIds"] == ["h"]
    assert state["buckets"] == {"30-40": ["b"]}


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

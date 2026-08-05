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
    monkeypatch.setenv("NOTIFY_RATE_THRESHOLD", "40")
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


def test_new_qualifying_loan_notifies(json_backend, capture_notify):
    pipeline.run(raw_rows=[_loan("1", 50)])
    pipeline.run(raw_rows=[_loan("1", 50), _loan("2", 60)])
    assert capture_notify == [["1"], ["1", "2"]]  # re-fires with full set


def test_dropped_qualifying_loan_notifies(json_backend, capture_notify):
    pipeline.run(raw_rows=[_loan("1", 50), _loan("2", 60)])
    pipeline.run(raw_rows=[_loan("1", 50)])  # 2 dropped
    assert len(capture_notify) == 2
    assert capture_notify[1] == ["1"]


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

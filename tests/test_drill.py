"""Alert drill: synthetic loan through the REAL pipeline, ephemeral storage,
invest dispatch neutralized. Proves Telegram e2e without money movement."""

from pathlib import Path

import pytest

import i2i_watch.pipeline as pipeline
import i2i_watch.storage as storage
from i2i_watch.drill import run as drill_run


@pytest.fixture
def json_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(storage, "_mode", None)
    monkeypatch.setattr(storage, "_app", None)
    monkeypatch.setattr(storage, "_db", None)
    monkeypatch.setenv("I2I_STORAGE", "json")
    monkeypatch.setenv("STARTUP_JITTER_MS", "0")
    monkeypatch.setenv("NOTIFY_MIN_RATE_PCT", "40")
    monkeypatch.setenv("NOTIFY_HIGH_RATE_PCT", "100")
    return tmp_path


def _patch_delivery(monkeypatch, telegram_ok=True, standard_ok=None):
    sent = {"loud": [], "standard": []}
    standard_ok = telegram_ok if standard_ok is None else standard_ok

    def fake_send_text(text, silent=False):
        sent["loud"].append((silent, text))
        return telegram_ok

    def fake_notify_all(loans, stats, dashboard_url, threshold):
        sent["standard"].append([str(ln["loanId"]) for ln in loans])
        return {"telegram": standard_ok, "ntfy": False}

    monkeypatch.setattr(pipeline, "send_telegram_text", fake_send_text)
    monkeypatch.setattr(pipeline, "notify_all", fake_notify_all)
    return sent


def test_drill_fires_loud_and_detailed_alerts(json_backend, monkeypatch):
    sent = _patch_delivery(monkeypatch, telegram_ok=True)

    out = drill_run(rate=120.0)

    assert out["e2eConfirmed"] is True
    assert out["loudAlertSent"] is True
    assert out["detailedAlertSent"] is True
    assert out["loanId"].startswith("DRILL")
    # Loud tier really fired with the candidate header; the DRILL<ts> loan id
    # is the label on the loud message (it has no purpose line).
    assert any(not s and "AUTO-INVEST CANDIDATE" in t for s, t in sent["loud"])
    assert any("DRILL" in t for _s, t in sent["loud"])
    # Detailed tier notified exactly the synthetic loan (its block carries [DRILL])
    assert sent["standard"] == [[out["loanId"]]]


def test_drill_neutralizes_invest_dispatch(json_backend, monkeypatch):
    original = pipeline._dispatch_invest
    _patch_delivery(monkeypatch, telegram_ok=True)

    out = drill_run()

    # The drill swapped out the real-money dispatch before the pipeline ran.
    assert pipeline._dispatch_invest is not original
    assert out["e2eConfirmed"] is True
    # No investment bookkeeping was created anywhere in the drill's storage.
    assert not (Path(out["storageDir"]) / "invested-loans.json").exists()


def test_drill_state_is_ephemeral_no_repo_leak(json_backend, monkeypatch):
    _patch_delivery(monkeypatch, telegram_ok=True)
    repo_data = Path(__file__).parents[1] / "data"
    before = {p.name: p.read_bytes() for p in repo_data.glob("*.json")} \
        if repo_data.exists() else {}

    out = drill_run()

    assert out["storageDir"] != str(repo_data)
    after = {p.name: p.read_bytes() for p in repo_data.glob("*.json")} \
        if repo_data.exists() else {}
    assert before == after  # nothing leaked into the real data dir
    assert (Path(out["storageDir"]) / "notify-state.json").exists()


def test_drill_refuses_below_gate_rate(json_backend):
    with pytest.raises(ValueError, match="below-gate drill proves nothing"):
        drill_run(rate=20.0)


def test_drill_fails_closed_when_telegram_down(json_backend, monkeypatch):
    _patch_delivery(monkeypatch, telegram_ok=False)

    out = drill_run()

    assert out["e2eConfirmed"] is False
    assert out["loudAlertSent"] is False
    assert out["detailedAlertSent"] is False

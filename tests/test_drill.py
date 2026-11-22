"""Synthetic alerts exercise real formatting/storage with offline HTTP replies."""

import json
import os
import socket
import tempfile
from pathlib import Path

import httpx
import pytest

from i2i_watch import drill, pipeline, storage
from i2i_watch.client import I2iClient
from i2i_watch.notify import channels


@pytest.fixture
def isolated_drill(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("NOTIFY_MIN_RATE_PCT", "40")
    monkeypatch.setenv("NOTIFY_HIGH_RATE_PCT", "100")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("TELEGRAM_ENABLED", "1")

    def forbidden(*args, **kwargs):
        pytest.fail("Drill attempted listing, authentication, or investment dispatch")

    monkeypatch.setattr(pipeline, "fetch_all_loans", forbidden)
    monkeypatch.setattr(I2iClient, "from_env", forbidden)
    monkeypatch.setattr(pipeline, "_dispatch_invest", forbidden)
    return tmp_path


def _delivery(monkeypatch, loud=True, detailed=True):
    messages = []

    def post(url, *, json=None, timeout, **kwargs):
        if url != "https://api.telegram.org/bottest-token/sendMessage":
            pytest.fail("Drill attempted a non-Telegram HTTP request")
        assert json["chat_id"] == "123"
        assert json["parse_mode"] == "HTML"
        messages.append(json["text"])
        accepted = loud if "AUTO-INVEST CANDIDATE" in json["text"] else detailed
        body = {"ok": True, "result": {"message_id": len(messages)}} if accepted else {
            "ok": False, "description": "synthetic rejection"}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(channels.httpx, "post", post)
    return messages


def _state():
    return (
        pipeline._dispatch_invest, storage._data_dir,
        storage._mode, storage._app, storage._db,
        {key: os.environ.get(key) for key in
         ("I2I_STORAGE", "TELEGRAM_ENABLED", "NTFY_ENABLED")},
    )


@pytest.mark.parametrize("loud,detailed", [(True, True), (True, False), (False, True), (False, False)])
def test_drill_requires_both_deliveries_and_restores_state(isolated_drill, monkeypatch, loud, detailed):
    messages = _delivery(monkeypatch, loud=loud, detailed=detailed)
    before = _state()

    out = drill.run(rate=120)

    assert out["e2eConfirmed"] is (loud and detailed)
    assert out["loudAlertSent"] is loud
    assert out["detailedAlertSent"] is detailed
    assert len(messages) == 2
    assert all("DRILL" in message for message in messages)
    assert _state() == before
    assert not Path(out["storageDir"]).exists()


@pytest.mark.parametrize("configured", [False, True])
def test_drill_is_telegram_only_and_restores_inherited_settings(isolated_drill, monkeypatch, configured):
    messages = _delivery(monkeypatch)
    if configured:
        monkeypatch.setenv("I2I_STORAGE", "firebase")
        monkeypatch.setenv("TELEGRAM_ENABLED", "0")
        monkeypatch.setenv("NTFY_ENABLED", "1")
        monkeypatch.setenv("NTFY_TOPIC", "must-not-send")
    else:
        for key in ("I2I_STORAGE", "TELEGRAM_ENABLED", "NTFY_ENABLED"):
            monkeypatch.delenv(key, raising=False)
    handles = [object(), object()]
    monkeypatch.setattr(storage, "_mode", "firebase")
    monkeypatch.setattr(storage, "_app", handles[0])
    monkeypatch.setattr(storage, "_db", handles[1])
    before = _state()

    out = drill.run()

    assert out["e2eConfirmed"] is True
    assert len(messages) == 2
    assert _state() == before


def test_drill_real_state_is_temporary_and_removed(isolated_drill, monkeypatch):
    _delivery(monkeypatch)
    original_dir = storage._data_dir()
    original_dir.mkdir()
    sentinel = original_dir / "notify-state.json"
    sentinel.write_text('{"qualifyingIds": ["existing"]}', encoding="utf-8")
    before = sentinel.read_bytes()
    real_pipeline = pipeline.run
    observed = []

    def inspect_pipeline(**kwargs):
        summary = real_pipeline(**kwargs)
        temporary_dir = storage._data_dir()
        observed.append(temporary_dir)
        assert temporary_dir != original_dir
        state = json.loads((temporary_dir / "notify-state.json").read_text(encoding="utf-8"))
        assert state["highIds"] == [kwargs["raw_rows"][0]["pl_bloan_id"]]
        assert (temporary_dir / "active-loans.json").exists()
        assert not (temporary_dir / "invested-loans.json").exists()
        return summary

    monkeypatch.setattr(pipeline, "run", inspect_pipeline)
    drill.run()
    assert observed and all(not directory.exists() for directory in observed)
    assert sentinel.read_bytes() == before
    assert storage._data_dir() == original_dir


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_drill_restores_globals_and_cleans_up_after_pipeline_failure(isolated_drill, monkeypatch, error):
    before = _state()
    observed = []

    def fail_pipeline(**kwargs):
        storage.save_notify_state(["temporary"])
        observed.append(storage._data_dir())
        raise error("synthetic pipeline failure")

    monkeypatch.setattr(pipeline, "run", fail_pipeline)
    with pytest.raises(error, match="synthetic pipeline failure"):
        drill.run()
    assert _state() == before
    assert observed and all(not directory.exists() for directory in observed)


def test_drill_refuses_below_gate_without_mutation(isolated_drill):
    before = _state()
    with pytest.raises(ValueError, match="below-gate drill proves nothing"):
        drill.run(rate=20)
    assert _state() == before
    assert list(isolated_drill.glob("i2i-drill-*")) == []


def test_test_isolation_blocks_real_connections():
    with socket.socket() as connection:
        with pytest.raises(pytest.fail.Exception, match="Unmocked network"):
            connection.connect(("127.0.0.1", 9))


def test_test_isolation_preserves_explicit_http_transport():
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"fixture": True})
    )) as client:
        assert client.get("https://offline.invalid/").json() == {"fixture": True}

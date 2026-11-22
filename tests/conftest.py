"""Shared test fixtures: repo root + raw-loan fixture loader.

The autouse `_isolate_json_storage` fixture redirects the git-as-DB JSON
backend's data dir to a per-test temp dir, so the suite NEVER writes into the
repo's real data/ directory. Without it, tests that exercise the invest loop
(invest.run -> storage.save_idle_state / record_invested) dirty the checkout,
and scrape.yml's "Decrypt git-crypt files" step fails because git-crypt unlock
demands a clean working tree (observed 2026-08-20: 3 consecutive scraper
failures, "Working directory not clean").
"""

import os
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def raw_rows() -> list[dict]:
    import json

    return json.loads((FIXTURES / "loans_raw.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _isolate_json_storage(tmp_path, monkeypatch):
    """Point the JSON backend's data dir at a temp dir for every test.

    storage._write_json/_data_dir already create parents, so no mkdir here
    (a pre-created dir collides with tests that manage their own data dir,
    e.g. test_accounts.py's bare .mkdir())."""
    from i2i_watch import storage

    for name in tuple(os.environ):
        if name.startswith(("I2I_", "FIREBASE_", "TELEGRAM_", "NTFY_", "GITHUB_",
                            "GH_", "GOOGLE_", "BOT_", "NOTIFY_", "AUTOINVEST_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("I2I_STORAGE", "json")
    monkeypatch.setenv("STARTUP_JITTER_MS", "0")
    monkeypatch.setenv("BOT_STATE_PATH", str(tmp_path / "bot-state.json"))
    monkeypatch.setenv("BOT_STATS_PATH", str(tmp_path / "stats.json"))
    for name in ("_mode", "_app", "_db"):
        monkeypatch.setattr(storage, name, None)
    tmp_data = tmp_path / "data"
    monkeypatch.setattr(storage, "_data_dir", lambda: tmp_data)
    return tmp_data


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Unmocked network access is disabled; mock the transport boundary")

    # Leave HTTP clients intact so explicit MockTransport tests still exercise them.
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)

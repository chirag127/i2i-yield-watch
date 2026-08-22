"""Tests for the Telegram command bot (i2i_watch.bot).

Pure functions are tested directly; the poll loop is tested with
monkeypatched transports so no network is touched.
"""

import json
import urllib.error

import pytest

from i2i_watch import bot


# ── parse_command ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("/invest", "/invest"),
    ("/scrape", "/scrape"),
    ("/status", "/status"),
    ("/help", "/help"),
    ("/start", "/start"),
    ("/ping", "/ping"),
    ("/wallet", "/wallet"),
    ("/digest", "/digest"),
    ("/emireport", "/emireport"),
    ("  /INVEST  ", "/invest"),        # case + whitespace normalized
    ("/Invest", "/invest"),
    ("invest", None),                   # must start with /
    ("/cancel", None),                  # unknown command
    ("hello", None),
    ("", None),
    (None, None),
])
def test_parse_command(text, expected):
    assert bot.parse_command(text) == expected


# ── command menu (setMyCommands) ────────────────────────────────────────────

def test_command_menu_has_all_commands_no_slashes():
    menu = bot.command_menu()
    assert [m["command"] for m in menu] == [
        c.lstrip("/") for c in bot.COMMANDS
    ]
    assert all("/" not in m["command"] for m in menu)
    assert all(m["description"] for m in menu)


def test_register_commands_posts_setmycommands(monkeypatch):
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b'{"ok": true, "result": true}'

    def fake_urlopen(req, timeout=20):
        captured["url"] = req.full_url
        captured["body"] = req.data.decode("utf-8")
        return FakeResp()

    monkeypatch.setattr(bot.urllib.request, "urlopen", fake_urlopen)
    bot.register_commands("tok")
    assert "setMyCommands" in captured["url"]
    payload = json.loads(captured["body"])
    assert len(payload["commands"]) == len(bot.COMMANDS)
    assert payload["commands"][0] == {"command": "start", "description": "show commands / how to use the bot"}


def test_register_commands_raises_on_http_error(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 400, "bad", {}, None)
    monkeypatch.setattr(bot.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError):
        bot.register_commands("tok")


# ── is_owner ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("chat_id,allowed,expected", [
    ("123", "123", True),
    (123, "123", True),      # int vs str tolerated
    ("123", "456", False),
    ("123", None, False),
    ("123", "", False),
    ("123", "123,456", True),     # comma-separated co-owners
    ("456", "123,456", True),
    ("789", "123,456", False),
    ("123", " 123 , 456 ", True),  # whitespace tolerated
])
def test_is_owner(chat_id, allowed, expected):
    assert bot.is_owner(chat_id, allowed) is expected


# ── dispatch_url ─────────────────────────────────────────────────────────────

def test_dispatch_url():
    assert bot.dispatch_url("chirag127/i2i-yield-watch", "invest.yml") == (
        "https://api.github.com/repos/chirag127/i2i-yield-watch/actions/"
        "workflows/invest.yml/dispatches"
    )


def test_dispatch_url_quotes_workflow():
    url = bot.dispatch_url("o/r", "my file.yml")
    assert "my%20file.yml" in url


# ── build_help ───────────────────────────────────────────────────────────────

def test_build_help_lists_all_commands():
    help_text = bot.build_help()
    for cmd in bot.COMMANDS:
        if cmd == "/start":  # /start is an alias for /help, not listed in it
            continue
        assert cmd in help_text
    assert "/invest" in help_text
    assert "REAL-MONEY" in help_text
    assert "/" in help_text  # hints to type / to see the menu


# ── build_status_reply ───────────────────────────────────────────────────────

def test_build_status_reply_empty_when_no_file(tmp_path):
    reply = bot.build_status_reply(str(tmp_path / "missing.json"))
    assert "no stats yet" in reply


def test_build_status_reply_reads_stats(tmp_path):
    stats = {
        "lastUpdated": "2026-08-22T12:41:31.485211Z",
        "currentActive": 26,
        "avgInterestRate": 34.42,
        "byPriority": {"VERY_HIGH": 3, "LOW": 23},
    }
    p = tmp_path / "stats.json"
    p.write_text(json.dumps(stats), encoding="utf-8")
    reply = bot.build_status_reply(str(p))
    assert "26" in reply
    assert "34.42" in reply
    assert "3" in reply
    assert "2026-08-22 12:41" in reply


# ── handle / dispatch routing ────────────────────────────────────────────────

def test_handle_dispatch_invest(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "dispatch", lambda repo, wf, tok: calls.append((repo, wf, tok)))
    reply = bot.handle("/invest", "chirag127/i2i-yield-watch", "tok")
    assert calls == [("chirag127/i2i-yield-watch", "invest.yml", "tok")]
    assert "invest.yml" in reply
    assert "actions/workflows/invest.yml" in reply


@pytest.mark.parametrize("cmd,workflow", [
    ("/scrape", "scrape.yml"),
    ("/wallet", "wallet-check.yml"),
    ("/digest", "digest.yml"),
    ("/emireport", "emi-report.yml"),
])
def test_handle_dispatch_routes(monkeypatch, cmd, workflow):
    calls = []
    monkeypatch.setattr(bot, "dispatch", lambda *a: calls.append(a))
    bot.handle(cmd, "o/r", "tok")
    assert calls and calls[0][1] == workflow


def test_handle_unknown_returns_help():
    reply = bot.handle(None, "o/r", "tok")
    assert "/invest" in reply  # help text


@pytest.mark.parametrize("cmd", ["/help", "/start"])
def test_handle_help_no_dispatch(monkeypatch, cmd):
    def boom(*a):
        raise AssertionError(f"must not dispatch for {cmd}")
    monkeypatch.setattr(bot, "dispatch", boom)
    bot.handle(cmd, "o/r", "tok")


def test_handle_status_no_dispatch(monkeypatch):
    def boom(*a):
        raise AssertionError("must not dispatch for /status")
    monkeypatch.setattr(bot, "dispatch", boom)
    bot.handle("/status", "o/r", "tok", stats_path=None)


# ── state persistence ────────────────────────────────────────────────────────

def test_load_save_state_roundtrip(tmp_path):
    p = str(tmp_path / "state.json")
    assert bot.load_state(p) == {}
    bot.save_state(p, {"last_update_id": 42})
    assert bot.load_state(p) == {"last_update_id": 42}


def test_load_state_handles_garbage(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("not json{{", encoding="utf-8")
    assert bot.load_state(str(p)) == {}


# ── run_poll orchestration ───────────────────────────────────────────────────

def test_run_poll_skips_non_owner_and_persists_offset(tmp_path):
    state_path = str(tmp_path / "state.json")

    updates = [
        {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/invest"}},  # not owner
        {"update_id": 2, "message": {"chat": {"id": "123"}, "text": "/ping"}},  # owner
        {"update_id": 3, "message": {"chat": {"id": "123"}, "text": "/status"}},  # owner
    ]
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(bot, "get_updates", lambda tok, off, timeout=50, limit=10: updates)
    monkeypatch.setattr(bot, "dispatch", lambda *a: None)
    sent = []
    monkeypatch.setattr(bot, "send_message", lambda tok, cid, text: sent.append(text))

    acted = bot.run_poll("tok", "123", "gh", "o/r", state_path, timeout=1)
    monkeypatch.undo()

    assert acted == 2  # only owner messages acted on
    assert bot.load_state(state_path) == {"last_update_id": 3}
    assert len(sent) == 2
    assert "pong" in sent[0]


def test_run_poll_offset_resumes(tmp_path):
    state_path = str(tmp_path / "state.json")
    bot.save_state(state_path, {"last_update_id": 5})

    monkeypatch = pytest.MonkeyPatch()
    calls = {}
    def fake_get_updates(tok, off, timeout=50, limit=10):
        calls["offset"] = off
        return [{"update_id": 6, "message": {"chat": {"id": "123"}, "text": "/ping"}}]
    monkeypatch.setattr(bot, "get_updates", fake_get_updates)
    monkeypatch.setattr(bot, "dispatch", lambda *a: None)
    monkeypatch.setattr(bot, "send_message", lambda *a: None)

    bot.run_poll("tok", "123", "gh", "o/r", state_path, timeout=1)
    monkeypatch.undo()

    assert calls["offset"] == 6  # last_update_id+1 — never re-process old
    assert bot.load_state(state_path) == {"last_update_id": 6}


def test_run_poll_reply_failure_does_not_abort(tmp_path, monkeypatch):
    """A failed sendMessage (Telegram outage) must not lose the dispatch."""
    state_path = str(tmp_path / "state.json")
    monkeypatch.setattr(bot, "get_updates", lambda *a, **k: [
        {"update_id": 1, "message": {"chat": {"id": "123"}, "text": "/ping"}},
    ])
    monkeypatch.setattr(bot, "dispatch", lambda *a: None)

    def boom(*a, **k):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(bot, "send_message", boom)

    acted = bot.run_poll("tok", "123", "gh", "o/r", state_path, timeout=1)
    assert acted == 1  # still counted as handled; offset persisted
    assert bot.load_state(state_path) == {"last_update_id": 1}


def test_run_poll_empty_updates_no_state_write(tmp_path, monkeypatch):
    state_path = str(tmp_path / "state.json")
    monkeypatch.setattr(bot, "get_updates", lambda *a, **k: [])
    acted = bot.run_poll("tok", "123", "gh", "o/r", state_path, timeout=1)
    assert acted == 0
    assert bot.load_state(state_path) == {}


# ── main(): all-polls-failed must exit non-zero (loud CI failure) ───────────

def test_main_exit_nonzero_when_all_polls_fail(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(bot, "get_updates", boom)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("GITHUB_TOKEN", "g")
    rc = bot.main(["--iterations", "2", "--timeout", "1"])
    assert rc == 1


def test_main_exit_zero_when_unconfigured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert bot.main(["--iterations", "1"]) == 0

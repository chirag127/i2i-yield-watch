"""Tests for the Telegram command bot (i2i_watch.bot).

Pure functions are tested directly; the poll loop is tested with
monkeypatched transports so no network is touched.
"""

import json
import io
import urllib.error

import pytest

from i2i_watch import bot


@pytest.fixture(autouse=True)
def _block_unmocked_transport(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("bot test reached unmocked urllib transport")
    monkeypatch.setattr(bot.urllib.request, "urlopen", blocked)


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


def _telegram_call(method):
    if method == "setMyCommands":
        return bot.register_commands("fixture-token")
    if method == "getUpdates":
        return bot.get_updates("fixture-token", 1)
    return bot.send_message("fixture-token", "fixture-chat", "fixture-message")


@pytest.mark.parametrize("method", ["setMyCommands", "getUpdates", "sendMessage"])
@pytest.mark.parametrize("payload", [
    {"ok": False, "description": "fixture-private-response"},
    {"ok": 1}, {"ok": "true"}, {}, [], None,
])
def test_telegram_requires_boolean_acknowledgement(monkeypatch, method, payload):
    monkeypatch.setattr(bot.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(
        json.dumps(payload).encode()
    ))
    with pytest.raises(RuntimeError) as error:
        _telegram_call(method)
    assert method in str(error.value)
    assert "fixture-private-response" not in str(error.value)


@pytest.mark.parametrize("method", ["setMyCommands", "getUpdates", "sendMessage"])
def test_telegram_accepts_boolean_acknowledgement(monkeypatch, method):
    monkeypatch.setattr(bot.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(
        b'{"ok": true, "result": []}'
    ))
    assert _telegram_call(method) == ([] if method == "getUpdates" else None)


@pytest.mark.parametrize("method", ["setMyCommands", "getUpdates", "sendMessage"])
@pytest.mark.parametrize("failure_kind,diagnostic", [
    ("http", "HTTP 401"), ("network", "URLError"), ("json", "JSONDecodeError"),
])
def test_telegram_transport_errors_are_sanitized(monkeypatch, method, failure_kind, diagnostic):
    def failed_transport(*args, **kwargs):
        if failure_kind == "json":
            return io.BytesIO(b"fixture-private-response")
        if failure_kind == "http":
            raise urllib.error.HTTPError(
                "https://api.telegram.org/botfixture-token/getUpdates", 401,
                "fixture-chat", {}, io.BytesIO(b"fixture-private-response"),
            )
        raise urllib.error.URLError("https://fixture-token/fixture-chat")
    monkeypatch.setattr(bot.urllib.request, "urlopen", failed_transport)
    with pytest.raises(RuntimeError) as error:
        _telegram_call(method)
    assert diagnostic in str(error.value)
    assert method in str(error.value)
    assert error.value.__suppress_context__
    assert error.value.__cause__ is None
    for sensitive in ("fixture-token", "fixture-chat", "fixture-private-response", "https://"):
        assert sensitive not in str(error.value)


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

def test_run_poll_skips_non_owner_and_persists_offset(tmp_path, monkeypatch):
    state_path = str(tmp_path / "state.json")

    updates = [
        {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/invest"}},  # not owner
        {"update_id": 2, "message": {"chat": {"id": "123"}, "text": "/ping"}},  # owner
        {"update_id": 3, "message": {"chat": {"id": "123"}, "text": "/status"}},  # owner
    ]
    monkeypatch.setattr(bot, "get_updates", lambda tok, off, timeout=50, limit=10: updates)
    monkeypatch.setattr(bot, "dispatch", lambda *a: None)
    sent = []
    monkeypatch.setattr(bot, "send_message", lambda tok, cid, text: sent.append(text))

    acted = bot.run_poll("tok", "123", "gh", "o/r", state_path, timeout=1)

    assert acted == 2  # only owner messages acted on
    assert bot.load_state(state_path) == {"last_update_id": 3}
    assert len(sent) == 2
    assert "pong" in sent[0]


def test_run_poll_offset_resumes(tmp_path, monkeypatch):
    state_path = str(tmp_path / "state.json")
    bot.save_state(state_path, {"last_update_id": 5})

    calls = {}
    def fake_get_updates(tok, off, timeout=50, limit=10):
        calls["offset"] = off
        return [{"update_id": 6, "message": {"chat": {"id": "123"}, "text": "/ping"}}]
    monkeypatch.setattr(bot, "get_updates", fake_get_updates)
    monkeypatch.setattr(bot, "dispatch", lambda *a: None)
    monkeypatch.setattr(bot, "send_message", lambda *a: None)

    bot.run_poll("tok", "123", "gh", "o/r", state_path, timeout=1)

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


def test_run_poll_replies_to_incoming_co_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "get_updates", lambda *a, **k: [
        {"update_id": 1, "message": {"chat": {"id": 456}, "text": "/ping"}},
    ])
    replies = []
    monkeypatch.setattr(bot, "send_message", lambda tok, cid, text: replies.append((cid, text)))

    assert bot.run_poll("tok", "123,456", "gh", "o/r", str(tmp_path / "state.json")) == 1
    assert replies == [("456", "pong 🏓")]


def test_run_poll_does_not_replay_dispatches_after_ambiguous_failure(tmp_path, monkeypatch):
    state_path = str(tmp_path / "state.json")
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 123}, "text": "/invest"}},
        {"update_id": 2, "message": {"chat": {"id": 123}, "text": "/invest"}},
        {"update_id": 3, "message": {"chat": {"id": 123}, "text": "/ping"}},
    ]
    monkeypatch.setattr(bot, "get_updates", lambda tok, off, timeout=50: [
        u for u in updates if u["update_id"] >= off
    ])
    attempts = []
    def dispatch(*args):
        attempts.append(args[1])
        if len(attempts) == 2:
            raise TimeoutError("response lost after server may have accepted command")
    monkeypatch.setattr(bot, "dispatch", dispatch)
    replies = []
    monkeypatch.setattr(bot, "send_message", lambda tok, cid, text: replies.append(text))

    bot.run_poll("tok", "123", "gh", "o/r", state_path)
    bot.run_poll("tok", "123", "gh", "o/r", state_path)

    assert attempts == ["invest.yml", "invest.yml"]
    assert bot.load_state(state_path) == {"last_update_id": 3}
    assert any("not confirmed" in reply.lower() for reply in replies)
    assert replies[-1] == "pong 🏓"


def test_run_poll_checkpoints_before_external_dispatch(tmp_path, monkeypatch):
    state_path = str(tmp_path / "state.json")
    monkeypatch.setattr(bot, "get_updates", lambda *a, **k: [
        {"update_id": 1, "message": {"chat": {"id": 123}, "text": "/invest"}},
    ])
    def dispatch(*args):
        assert bot.load_state(state_path) == {"last_update_id": 1}
        raise KeyboardInterrupt("simulate interruption after remote acceptance")
    monkeypatch.setattr(bot, "dispatch", dispatch)
    with pytest.raises(KeyboardInterrupt):
        bot.run_poll("tok", "123", "gh", "o/r", state_path)
    assert bot.load_state(state_path) == {"last_update_id": 1}


def test_run_poll_checkpoint_failure_prevents_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "get_updates", lambda *a, **k: [
        {"update_id": 1, "message": {"chat": {"id": 123}, "text": "/invest"}},
    ])
    def unavailable(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(bot, "save_state", unavailable)
    attempts = []
    monkeypatch.setattr(bot, "dispatch", lambda *a: attempts.append(a))
    monkeypatch.setattr(bot, "send_message", lambda *a: None)
    with pytest.raises(OSError):
        bot.run_poll("tok", "123", "gh", "o/r", str(tmp_path / "state.json"))
    assert attempts == []


def test_save_state_failed_replace_keeps_previous_offset(tmp_path, monkeypatch):
    state_path = str(tmp_path / "state.json")
    bot.save_state(state_path, {"last_update_id": 5})
    def unavailable(*args):
        raise OSError("replace unavailable")
    monkeypatch.setattr(bot.os, "replace", unavailable)
    with pytest.raises(OSError):
        bot.save_state(state_path, {"last_update_id": 6})
    assert bot.load_state(state_path) == {"last_update_id": 5}
    assert list(tmp_path.iterdir()) == [tmp_path / "state.json"]


# ── main(): all-polls-failed must exit non-zero (loud CI failure) ───────────

def test_main_exit_nonzero_when_all_polls_fail(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(bot, "get_updates", boom)
    monkeypatch.setattr(bot, "register_commands", lambda tok: None)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("GITHUB_TOKEN", "g")
    rc = bot.main(["--iterations", "2", "--timeout", "1"])
    assert rc == 1


def test_main_exit_zero_when_unconfigured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert bot.main(["--iterations", "1"]) == 0


@pytest.mark.parametrize("stage,iterations,expected_rc", [
    ("menu", "1", 0),
    ("poll", "1", 1),
    ("poll", "0", 1),
])
def test_main_failure_diagnostics_exclude_sensitive_exception_data(
    monkeypatch, capsys, stage, iterations, expected_rc,
):
    def failure(*args, **kwargs):
        raise RuntimeError(
            "https://api.telegram.org/botfixture-token/getUpdates "
            "chat_id=fixture-chat body=fixture-private-message"
        )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fixture-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fixture-chat")
    monkeypatch.setattr(bot, "register_commands", failure if stage == "menu" else lambda *a: None)
    monkeypatch.setattr(bot, "get_updates", failure if stage == "poll" else lambda *a, **k: [])
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)

    assert bot.main(["--iterations", iterations, "--max-failures", "1"]) == expected_rc
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err
    assert ("registration failed" if stage == "menu" else "poll 1 failed") in captured.err
    for sensitive in ("fixture-token", "fixture-chat", "fixture-private-message", "https://"):
        assert sensitive not in captured.out + captured.err


# ── main(): continuous mode (--iterations 0, used by the live workflow) ──────

def test_main_continuous_success_polls_forever(monkeypatch):
    """Continuous mode keeps polling (never exits) while polls succeed."""
    calls = []
    def get_updates(*args, **kwargs):
        calls.append(1)
        if len(calls) == 4:
            raise KeyboardInterrupt("stop synchronous test")
        return []
    monkeypatch.setattr(bot, "get_updates", get_updates)
    monkeypatch.setattr(bot, "register_commands", lambda tok: None)  # no network
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("GITHUB_TOKEN", "g")
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)

    with pytest.raises(KeyboardInterrupt, match="stop synchronous test"):
        bot.main(["--iterations", "0", "--timeout", "1"])
    assert len(calls) == 4


def test_main_continuous_exits_1_after_max_consecutive_failures(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(bot, "get_updates", boom)
    monkeypatch.setattr(bot, "register_commands", lambda tok: None)  # no network
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("GITHUB_TOKEN", "g")
    # 3 consecutive failures with max-failures=3 -> exit 1 (workflow alert fires)
    rc = bot.main(["--iterations", "0", "--timeout", "1", "--max-failures", "3"])
    assert rc == 1


def test_main_continuous_recovers_after_transient_failure(monkeypatch):
    """A single failure between successes must NOT trip the max-failures exit."""
    calls = {"n": 0}
    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 7:
            raise KeyboardInterrupt("stop synchronous test")
        if calls["n"] in (1, 2, 4, 5):
            raise RuntimeError("transient")
        return []
    monkeypatch.setattr(bot, "get_updates", flaky)
    monkeypatch.setattr(bot, "register_commands", lambda tok: None)  # no network
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("GITHUB_TOKEN", "g")
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)

    with pytest.raises(KeyboardInterrupt, match="stop synchronous test"):
        bot.main(["--iterations", "0", "--timeout", "1", "--max-failures", "3"])
    assert calls["n"] == 7

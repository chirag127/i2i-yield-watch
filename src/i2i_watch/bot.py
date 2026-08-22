"""Telegram command bot — re-trigger workflows by message.

The owner sends "/invest", "/scrape", "/status", "/help" … to the i2i bot and
this module long-polls Telegram, dispatches the matching GitHub Actions
workflow through the REST API (GITHUB_TOKEN, needs actions:write), and replies
in the same chat. Runs as a self-looping job in telegram-bot.yml: GitHub's
schedule cron floor is 5 minutes, so each run long-polls getUpdates for ~50s
per iteration and repeats — most messages are answered within a minute of a
job starting, and an external tick pinger can keep a job alive continuously.

Stdlib-only (urllib): the workflow needs no venv, no pip install, no cache.

Pure logic (parse_command, is_owner, build_help, …) is separated from I/O
(get_updates / dispatch / send_message) so tests exercise it with
monkeypatched transports.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
GITHUB_API = "https://api.github.com"

# command -> metadata. `workflow: None` = local reply only (no dispatch).
COMMANDS: dict[str, dict] = {
    "/start": {"workflow": None, "help": "show commands / how to use the bot"},
    "/invest": {"workflow": "invest.yml", "help": "run the REAL-MONEY auto-invest right now"},
    "/scrape": {"workflow": "scrape.yml", "help": "force a fresh market scrape + notifications"},
    "/wallet": {"workflow": "wallet-check.yml", "help": "check investable escrow balance"},
    "/digest": {"workflow": "digest.yml", "help": "send the portfolio digest"},
    "/emireport": {"workflow": "emi-report.yml", "help": "refresh the EMI status report"},
    "/status": {"workflow": None, "help": "latest dashboard stats"},
    "/help": {"workflow": None, "help": "show this help"},
    "/ping": {"workflow": None, "help": "pong"},
}


def command_menu() -> list[dict]:
    """Telegram Bot API setMyCommands payload: command -> short description.

    Commands are shown in the chat's command menu when the user types "/"
    ("Menu" button in the message field). Descriptions are truncated to
    Telegram's 256-char limit defensively.
    """
    return [
        {"command": cmd.lstrip("/"), "description": meta["help"][:256]}
        for cmd, meta in COMMANDS.items()
    ]


def register_commands(token: str) -> None:
    """Register the slash-command menu with Telegram (setMyCommands).

    Idempotent: safe to call every poll cycle. Raises on failure so callers
    can decide whether to keep going.
    """
    body = json.dumps({"commands": command_menu()}).encode("utf-8")
    url = TELEGRAM_API.format(token=token, method="setMyCommands")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"setMyCommands failed: HTTP {e.code}") from e
    if not data.get("ok"):
        raise RuntimeError(f"setMyCommands failed: {data}")


def build_help(include_howto: bool = True) -> str:
    """Human-readable command list; optionally with a quick how-to header."""
    lines = ["<b>i2i bot commands</b>"]
    if include_howto:
        lines.append("Type <code>/</code> in any chat to see this menu.")
    for cmd, meta in COMMANDS.items():
        if cmd == "/start":
            continue
        lines.append(f"<code>{cmd}</code> — {meta['help']}")
    return "\n".join(lines)


def parse_command(text: str) -> str | None:
    """Normalize a message into a known command key (e.g. '/invest')."""
    t = (text or "").strip().lower()
    return t if t in COMMANDS else None


def is_owner(chat_id: object, allowed: object) -> bool:
    """Only the configured owner chat(s) may trigger real-money workflows.

    `allowed` may be a single chat id or a comma-separated list (for a second
    phone / co-owner). None or empty -> nobody is the owner.
    """
    if allowed is None:
        return False
    wanted = {a.strip() for a in str(allowed).split(",") if a.strip()}
    return str(chat_id) in wanted


def dispatch_url(repo: str, workflow: str) -> str:
    return f"{GITHUB_API}/repos/{repo}/actions/workflows/{urllib.parse.quote(workflow)}/dispatches"


def build_help() -> str:
    lines = ["<b>i2i bot commands</b>"]
    for cmd, meta in COMMANDS.items():
        lines.append(f"<code>{cmd}</code> — {meta['help']}")
    return "\n".join(lines)


def build_status_reply(stats_path: str | None = None) -> str:
    """Read data/stats.json (kept fresh by the scraper) into a short reply."""
    stats: dict = {}
    if stats_path:
        try:
            if Path(stats_path).exists():
                stats = json.loads(Path(stats_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stats = {}
    if not stats:
        return "📊 <b>Dashboard</b>\n<i>no stats yet — wait for the next scrape</i>"
    updated = str(stats.get("lastUpdated", "?")).replace("T", " ")[:16]
    active = stats.get("currentActive", "?")
    avg = stats.get("avgInterestRate", "?")
    by_priority = stats.get("byPriority", {})
    high = by_priority.get("VERY_HIGH", 0)
    return (
        f"📊 <b>Dashboard</b>\n"
        f"Active loans: {active} | Avg rate: {avg}%\n"
        f"High-yield (VERY_HIGH): {high}\n"
        f"Last scrape: {updated} UTC"
    )


def load_state(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path: str, state: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, indent=2), encoding="utf-8")


def get_updates(token: str, offset: int, timeout: int = 50, limit: int = 10) -> list[dict]:
    q = urllib.parse.urlencode({
        "timeout": timeout,
        "offset": offset,
        "limit": limit,
        "allowed_updates": json.dumps(["message"]),
    })
    url = f"{TELEGRAM_API.format(token=token, method='getUpdates')}?{q}"
    with urllib.request.urlopen(url, timeout=timeout + 20) as r:
        data = json.loads(r.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(f"getUpdates failed: {data}")
    return data.get("result", [])


def dispatch(repo: str, workflow: str, token: str) -> str | None:
    """POST workflow_dispatch (ref main). Returns run id (None = queued, 204)."""
    url = dispatch_url(repo, workflow)
    body = json.dumps({"ref": "main"}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return None  # 204 No Content — queued
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"dispatch {workflow} failed: HTTP {e.code} {e.read()[:200]}") from e


def send_message(token: str, chat_id: str, text: str) -> None:
    body = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    url = TELEGRAM_API.format(token=token, method="sendMessage")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"sendMessage failed: HTTP {e.code}") from e
    if not data.get("ok"):
        raise RuntimeError(f"sendMessage failed: {data}")


def handle(cmd: str | None, repo: str, gh_token: str, stats_path: str | None = None) -> str:
    """Dispatch a command and return the reply text. cmd None -> help hint."""
    if cmd is None or cmd in ("/help", "/start"):
        return build_help()
    meta = COMMANDS[cmd]
    if meta["workflow"] is None:
        if cmd == "/status":
            return build_status_reply(stats_path)
        if cmd == "/ping":
            return "pong 🏓"
        return build_help()
    dispatch(repo, meta["workflow"], gh_token)
    return (
        f"✅ <b>{cmd}</b> — dispatched <code>{meta['workflow']}</code>\n"
        f"https://github.com/{repo}/actions/workflows/{meta['workflow']}"
    )


def run_poll(
    token: str,
    chat_id: str,
    gh_token: str,
    repo: str,
    state_path: str,
    stats_path: str | None = None,
    timeout: int = 50,
) -> int:
    """One long-poll cycle: fetch, process, reply, persist offset.

    Returns the number of messages acted on. Offset persistence (git-as-DB
    state file) means a crashed run never re-processes old messages.
    """
    state = load_state(state_path)
    offset = int(state.get("last_update_id", 0) or 0)
    updates = get_updates(token, offset + 1, timeout)
    acted = 0
    max_id = offset
    for upd in updates:
        upd_id = int(upd.get("update_id", 0) or 0)
        max_id = max(max_id, upd_id)
        msg = upd.get("message") or {}
        if not is_owner((msg.get("chat") or {}).get("id"), chat_id):
            continue
        cmd = parse_command(msg.get("text") or "")
        reply = handle(cmd, repo, gh_token, stats_path)
        acted += 1
        try:
            send_message(token, chat_id, reply)
        except Exception as e:  # noqa: BLE001 — dispatch already done; keep polling
            print(f"reply failed (dispatch already done): {e}", file=sys.stderr)
    if max_id > offset:
        save_state(state_path, {"last_update_id": max_id})
    return acted


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="i2i_watch.bot")
    p.add_argument("--iterations", type=int, default=5, help="long-poll cycles per job")
    p.add_argument("--timeout", type=int, default=50, help="getUpdates long-poll timeout (s)")
    p.add_argument("--state", default=os.environ.get("BOT_STATE_PATH", "data/telegram-bot-state.json"))
    p.add_argument("--stats", default=os.environ.get("BOT_STATS_PATH", "data/stats.json"))
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = p.parse_args(argv)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID unset — idle", file=sys.stderr)
        return 0

    # Register the slash-command menu once per job so the user sees all
    # commands when typing "/". Non-fatal: typed commands still work even if
    # this fails (e.g. Telegram API hiccup).
    try:
        register_commands(token)
        print("registered command menu with Telegram", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"command-menu registration failed (non-fatal): {e}", file=sys.stderr)

    acted_total = 0
    failures = 0
    for i in range(1, args.iterations + 1):
        print(f"=== poll {i}/{args.iterations} ===", flush=True)
        try:
            acted_total += run_poll(token, chat_id, gh_token, args.repo,
                                    args.state, args.stats, timeout=args.timeout)
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"poll {i} failed: {e}", file=sys.stderr)
        if i < args.iterations:
            time.sleep(2)
    print(f"acted on {acted_total} messages ({failures} poll failures)", flush=True)
    # A job where every poll failed is a broken bot — fail loudly so the
    # workflow's failure alert fires instead of silently idling forever.
    return 1 if failures >= args.iterations and acted_total == 0 else 0


if __name__ == "__main__":
    sys.exit(main())

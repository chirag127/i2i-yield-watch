"""Telegram + ntfy notifiers. Both read config from env and no-op cleanly when
unconfigured. Telegram uses HTML parse_mode: each loan is a block whose bold
first line (Rate + Yield) is a clickable link to the loan's i2iFunding profile,
followed by label-free info lines. Messages chunk to Telegram's 4096 limit.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from ..scorer import sort_loans
from ..transform import format_loan_block

log = logging.getLogger("i2i_watch")

SAFE_CHUNK = 3800
DEFAULT_NTFY_BASE = "https://ntfy.sh"


def _esc(s: object) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_loan_line(loan: dict) -> str:
    """One loan as a Telegram HTML block: bold+clickable first line, plain rest.

    The block's trailing URL line is lifted into the first line's href and
    dropped from the plain body so the URL isn't repeated.
    """
    raw = format_loan_block(loan)
    url = loan.get("loanUrl") or ""
    lines = raw
    if raw and raw[-1].lower().startswith(("http://", "https://")):
        url = raw[-1]
        lines = raw[:-1]
    out = []
    for i, ln in enumerate(lines):
        safe = _esc(ln)
        if i == 0:
            out.append(f'<a href="{_esc(url)}"><b>{safe}</b></a>' if url else f"<b>{safe}</b>")
        else:
            out.append(safe)
    return "\n".join(out)


def _enabled(name: str) -> bool:
    """Lenient enable gate: any truthy-ish value counts as on."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def build_header(count: int, rate_threshold: float) -> str:
    plural = "" if count == 1 else "S"
    return f"🚨 <b>{count} HIGH-YIELD LOAN{plural} (rate &gt; {rate_threshold:g}%)</b>\n\n"


def build_footer(stats: dict, dashboard_url: str) -> str:
    active = (stats or {}).get("activeCount", 0)
    return f'\n\n📊 Active: {active} | <a href="{_esc(dashboard_url)}">Dashboard</a>'


def chunk_messages(loans: list[dict], header: str, footer: str) -> list[str]:
    """Split loan blocks into <=4096-char Telegram messages; header on first,
    footer on last. Empty loan list -> single 'no qualifying loans' notice.
    """
    messages: list[str] = []
    current = header
    for loan in loans:
        block = format_loan_line(loan) + "\n\n"
        if len(current) + len(block) > SAFE_CHUNK and len(current) > len(header):
            messages.append(current)
            current = ""
        current += block
        if len(current) > SAFE_CHUNK:
            messages.append(current)
            current = ""
    if current:
        messages.append(current + footer)
    elif messages:
        messages[-1] += footer
    else:
        messages.append(
            header.rstrip()
            + "\n\n<i>No loans currently meet the criteria.</i>\n"
            + footer
        )
    return messages


def send_telegram(
    loans: list[dict], stats: dict, dashboard_url: str, rate_threshold: float = 50.0
) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.info("telegram: TELEGRAM_BOT_TOKEN/CHAT_ID unset — skipping")
        return False

    sorted_loans = sort_loans(loans)
    messages = chunk_messages(
        sorted_loans,
        build_header(len(sorted_loans), rate_threshold),
        build_footer(stats, dashboard_url),
    )
    all_ok = True
    for i, msg in enumerate(messages):
        try:
            r = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": msg,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=20,
            )
            r.raise_for_status()
            log.info("telegram: chunk %d/%d sent", i + 1, len(messages))
        except Exception as e:  # noqa: BLE001
            all_ok = False
            log.warning("telegram: chunk %d/%d failed: %s", i + 1, len(messages), e)
        if i < len(messages) - 1:
            time.sleep(1.1)
    return all_ok


def format_loan_body(loan: dict) -> str:
    """Plain-text block for ntfy (label-free lines joined by newlines)."""
    return "\n".join(format_loan_block(loan))


def send_ntfy(
    loans: list[dict], stats: dict, _dashboard_url: str = "", rate_threshold: float = 50.0
) -> bool:
    if not _enabled("NTFY_ENABLED"):
        return False
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        log.info("ntfy: NTFY_TOPIC unset — skipping")
        return False
    base = os.environ.get("NTFY_BASE_URL", DEFAULT_NTFY_BASE).rstrip("/")
    user = os.environ.get("NTFY_USER", "").strip()
    pw = os.environ.get("NTFY_PASSWORD", "").strip()
    auth = (user, pw) if user and pw else None

    sorted_loans = sort_loans(loans)
    plural = "" if len(sorted_loans) == 1 else "s"
    title = f"i2i Yield Watch — {len(sorted_loans)} high-yield loan{plural} (rate > {rate_threshold:g}%)"
    header = f"🚨 {len(sorted_loans)} HIGH-YIELD LOAN{'' if len(sorted_loans) == 1 else 'S'} (rate > {rate_threshold:g}%)\n\n"
    blocks = "\n\n---\n\n".join(format_loan_body(ln) for ln in sorted_loans)
    footer = f"\n\n📊 Active: {(stats or {}).get('activeCount', 0)}"
    body = header + blocks + footer

    try:
        r = httpx.post(
            f"{base}/{topic}",
            content=body.encode("utf-8"),
            headers={"Title": title, "Priority": "high", "Tags": "rotating_light,money_with_wings"},
            auth=auth,
            timeout=20,
        )
        r.raise_for_status()
        log.info("ntfy: sent %d loans to %s/%s", len(sorted_loans), base, topic)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("ntfy send failed: %s", e)
        return False


def notify_all(
    loans: list[dict], stats: dict, dashboard_url: str, rate_threshold: float = 50.0
) -> dict[str, bool]:
    """Dispatch to enabled channels. Telegram gated by TELEGRAM_ENABLED, ntfy by
    NTFY_ENABLED (each also no-ops without its own credentials).
    """
    results = {"telegram": False, "ntfy": False}
    if not loans:
        log.info("notify: no qualifying loans")
        return results
    if _enabled("TELEGRAM_ENABLED"):
        results["telegram"] = send_telegram(loans, stats, dashboard_url, rate_threshold)
    else:
        log.info("notify: telegram disabled (TELEGRAM_ENABLED not truthy)")
    results["ntfy"] = send_ntfy(loans, stats, dashboard_url, rate_threshold)
    log.info("notification results: %s", results)
    return results


def was_any_channel_successful(results: dict) -> bool:
    return any(results.values())

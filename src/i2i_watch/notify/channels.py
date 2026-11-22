"""Telegram + ntfy notifiers. Both read config from env and no-op cleanly when
unconfigured. Telegram uses HTML parse_mode: each loan is a block whose bold
first line (Rate + Yield) is a clickable link to the loan's i2iFunding profile,
followed by label-free info lines. Messages chunk to Telegram's 4096 limit.
"""

from __future__ import annotations

import logging
import math
import os
import time
from html import escape
from html.parser import HTMLParser

import httpx

from ..scorer import sort_loans
from ..transform import format_loan_block

log = logging.getLogger("i2i_watch")

SAFE_CHUNK = 3800
DEFAULT_NTFY_BASE = "https://ntfy.sh"
TELEGRAM_RETRY_MAX_S = 30.0


class _TelegramRequestLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # HTTPX INFO includes the bot token in its request URL, even on success.
        return "api.telegram.org/bot" not in record.getMessage().lower()


logging.getLogger("httpx").addFilter(_TelegramRequestLogFilter())


def _esc(s: object) -> str:
    return escape(str(s), quote=True)


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


def enabled_channels() -> set[str]:
    return {name for name in ("telegram", "ntfy") if _enabled(f"{name.upper()}_ENABLED")}


def build_header(count: int, rate_threshold: float) -> str:
    plural = "" if count == 1 else "S"
    return f"🚨 <b>{count} HIGH-YIELD LOAN{plural} (rate &gt; {rate_threshold:g}%)</b>\n\n"


def build_footer(stats: dict, dashboard_url: str) -> str:
    active = (stats or {}).get("activeCount", 0)
    return f'\n\n📊 Active: {active} | <a href="{_esc(dashboard_url)}">Dashboard</a>'


class _ReadableHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "a":
            self.links.append(dict(attrs).get("href") or "")
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.links:
            url = self.links.pop()
            if url:
                self.parts.append(f" ({url})")


def _bounded_html(text: str) -> list[str]:
    if len(text.encode("utf-16-le")) // 2 <= 4096:
        return [text]
    # Never cut an HTML tag/entity. Oversized messages retain text and link targets,
    # sacrificing styling rather than dropping loan details or sending invalid HTML.
    parser = _ReadableHTML()
    parser.feed(text)
    parser.close()
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for char in "".join(parser.parts):
        escaped = escape(char, quote=False)
        width = len(escaped.encode("utf-16-le")) // 2
        if size + width > SAFE_CHUNK:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(escaped)
        size += width
    if current:
        chunks.append("".join(current))
    return chunks


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
    return [chunk for message in messages for chunk in _bounded_html(message)]


def _response_json(response: object) -> dict:
    """Malformed responses never establish delivery."""
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


def _telegram_post(payload: dict) -> bool:
    """Require explicit acknowledgement; retry only a bounded server-requested 429."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for attempt in range(2):
        try:
            response = httpx.post(url, json=payload, timeout=20)
            body = _response_json(response)
            status_code = response.status_code
            if status_code == 429 and attempt == 0:
                parameters = body.get("parameters")
                retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = math.nan
                if not math.isfinite(delay) or not 0 <= delay <= TELEGRAM_RETRY_MAX_S:
                    log.warning("telegram: HTTP 429; retry deferred (backoff outside request budget)")
                    return False
                log.warning("telegram: rate limited; retrying in %.1fs", delay)
                if delay:
                    time.sleep(delay)
                continue
            if not 200 <= status_code < 300:
                log.warning("telegram: HTTP %d; message not acknowledged", status_code)
                return False
            if body.get("ok") is not True:
                log.warning("telegram: missing or rejected API acknowledgement")
                return False
            return True
        except Exception as e:  # noqa: BLE001
            # Exception text and Telegram descriptions can contain credential-bearing URLs.
            log.warning("telegram: request failed (%s); delivery unconfirmed", type(e).__name__)
            return False
    return False


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
            if not _telegram_post({
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }):
                raise RuntimeError("Telegram API did not accept the message")
            log.info("telegram: chunk %d/%d sent", i + 1, len(messages))
        except Exception as e:  # noqa: BLE001
            all_ok = False
            log.warning("telegram: chunk %d/%d failed (%s)", i + 1, len(messages), type(e).__name__)
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
    loans: list[dict], stats: dict, dashboard_url: str, rate_threshold: float = 50.0,
    *, channels: set[str] | None = None,
) -> dict[str, bool]:
    """Dispatch to enabled channels. Telegram gated by TELEGRAM_ENABLED, ntfy by
    NTFY_ENABLED (each also no-ops without its own credentials).
    """
    results = {"telegram": False, "ntfy": False}
    if not loans:
        log.info("notify: no qualifying loans")
        return results
    selected = enabled_channels()
    if channels is not None:
        selected.intersection_update(channels)
    if "telegram" in selected:
        results["telegram"] = send_telegram(loans, stats, dashboard_url, rate_threshold)
    else:
        log.info("notify: telegram disabled or not selected")
    if "ntfy" in selected:
        results["ntfy"] = send_ntfy(loans, stats, dashboard_url, rate_threshold)
    log.info("notification results: %s", results)
    return results


def was_any_channel_successful(results: dict) -> bool:
    return any(results.values())


def send_telegram_text(text: str, silent: bool = False) -> bool:
    """Send bounded pre-formatted HTML messages via the fleet Telegram bot (invest /
    top-up / cancel summaries build their own body). No-op if unset.
    silent=True sets disable_notification: a quiet push that still lands in the
    chat but doesn't buzz — for routine summaries. Loud (default) for actionable
    alerts like the >150% top-up trigger."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.info("telegram: TELEGRAM_BOT_TOKEN/CHAT_ID unset — skipping")
        return False
    try:
        messages = _bounded_html(text)
        all_ok = bool(messages)
        for i, message in enumerate(messages):
            payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML",
                       "disable_web_page_preview": True}
            if silent:
                payload["disable_notification"] = True
            if not _telegram_post(payload):
                all_ok = False
            if i < len(messages) - 1:
                time.sleep(1.1)
        return all_ok
    except Exception as e:  # noqa: BLE001
        log.warning("telegram: send_telegram_text failed (%s)", type(e).__name__)
        return False

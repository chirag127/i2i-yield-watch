"""Orchestration: fetch -> transform -> detect new/archived -> save -> stats ->
notify-new-only -> changelog. Mirrors the Node core/index.js flow.
"""

from __future__ import annotations

import logging
import os
import random
import time
from html import escape
from datetime import datetime, timedelta, timezone

from . import config as C
from . import storage
from .notify.channels import notify_all, send_telegram_text, was_any_channel_successful
from .sources.i2i import fetch_all_loans
from .transform import transform_loans
from .scorer import sort_loans

log = logging.getLogger("i2i_watch")


def _dispatch_invest() -> None:
    """Fire a repository_dispatch event so the invest workflow starts immediately
    instead of waiting for its next cron tick. Best-effort: if dispatch fails
    (no GITHUB_TOKEN, network, permissions), the cron fallback still covers it."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repo:
        log.debug("invest dispatch skipped: no GITHUB_TOKEN or GITHUB_REPOSITORY")
        return
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request
    url = f"https://api.github.com/repos/{repo}/dispatches"
    body = _json.dumps({"event_type": "invest", "client_payload": {"triggered_by": "scrape"}}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            log.info("invest workflow dispatched (status %d)", r.status)
    except urllib.error.HTTPError as e:
        log.warning("invest dispatch failed: HTTP %d %s", e.code, e.read()[:120])
    except Exception as e:  # noqa: BLE001
        log.warning("invest dispatch failed: %s", e)


def _rate_threshold() -> float:
    """NOTIFY gate: alert on loans with rate > this. config.NOTIFY_MIN_RATE_PCT
    (default 40), env-overridable via the SAME name NOTIFY_MIN_RATE_PCT."""
    return C._f("NOTIFY_MIN_RATE_PCT", C.NOTIFY_MIN_RATE_PCT)


def _high_rate_threshold() -> float:
    """LOUD tier gate: any loan with rate > this triggers an immediate loud
    Telegram alert (default 100 = the auto-invest money gate), independent of
    the standard change-only tier — the investor must know the moment a
    >100% loan is live. config.NOTIFY_HIGH_RATE_PCT, env-overridable."""
    return C._f("NOTIFY_HIGH_RATE_PCT", C.NOTIFY_HIGH_RATE_PCT)


def _digest_hours() -> float:
    """Notify at least once per N hours even when the qualifying set is
    unchanged. 0/unset = disabled (change-only). Accepts I2I_DIGEST_HOURS, or
    I2I_DIGEST=true as a 24h shortcut.
    """
    raw = os.environ.get("I2I_DIGEST_HOURS", "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            log.warning("I2I_DIGEST_HOURS=%r not a number — ignoring", raw)
    if os.environ.get("I2I_DIGEST", "").strip().lower() in ("1", "true", "yes", "on"):
        return 24.0
    return 0.0


def _digest_due(prev_notified_at: str | None, hours: float) -> bool:
    if hours <= 0:
        return False
    if not prev_notified_at:
        return True
    try:
        prev = datetime.fromisoformat(str(prev_notified_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    return datetime.now(timezone.utc) - prev >= timedelta(hours=hours)


def run(raw_rows: list[dict] | None = None, client=None) -> dict:
    """One scrape cycle. Returns a summary dict. Raises on hard failure.

    ``client`` may be a shared I2iClient to reuse one login across the whole
    poll loop instead of re-logging-in every pass (the historical per-pass
    login added a wasted round-trip to every 30s poll)."""
    run_id = f"run_{int(time.time() * 1000)}"
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    jitter_max = int(os.environ.get("STARTUP_JITTER_MS", "2000"))
    if jitter_max > 0:
        time.sleep(random.randint(0, jitter_max) / 1000)

    raw = raw_rows if raw_rows is not None else fetch_all_loans(client=client)
    log.info("fetched %d raw rows", len(raw))
    fresh = transform_loans(raw)
    log.info("transformed %d loans", len(fresh))

    # A zero-row snapshot is not a valid market state: it usually means an auth,
    # API, parser, or browser regression. Never archive the entire active book
    # or reset notification state on that signal. Fail before any persistence so
    # the workflow's failure alert fires and the next run can recover.
    if len(fresh) < max(1, C.LISTING_MIN_ROWS):
        raise RuntimeError(
            f"market snapshot contained {len(fresh)} loan rows; refusing to "
            "overwrite active state with an empty/invalid listing"
        )

    existing = storage.load_active_loans()
    notified = storage.load_notifications_sent()

    new_loans = storage.detect_new_loans(fresh, existing, notified)
    to_archive = storage.detect_fully_funded(fresh, existing)
    archived = storage.archive_fully_funded_loans(to_archive) if to_archive else 0

    active = [ln for ln in fresh if not ln.get("isFullyFunded")]
    storage.save_active_loans(active)
    storage.update_stats(active, archived)

    threshold = _rate_threshold()
    qualifying = [
        ln for ln in active if (ln.get("interestRate") is not None and ln["interestRate"] > threshold)
    ]
    qual_ids = {str(ln["loanId"]) for ln in qualifying}

    prev_state = storage.load_notify_state()

    # LOUD tier — rate > NOTIFY_HIGH_RATE_PCT (default 100). A brand-new loan in
    # this tier fires an IMMEDIATE loud Telegram alert even when the standard
    # qualifying set is unchanged, so the investor hears the moment a >100%
    # loan posts. Tracked separately in notify-state['highIds'].
    high_threshold = _high_rate_threshold()
    high = [
        ln for ln in active
        if (ln.get("interestRate") is not None and ln["interestRate"] > high_threshold)
    ]
    high_ids = {str(ln["loanId"]) for ln in high}
    prev_high_ids = {str(x) for x in prev_state.get("highIds", [])}
    new_high_ids = high_ids - prev_high_ids

    prev_ids = {str(x) for x in prev_state.get("qualifyingIds", [])}
    new_ids = qual_ids - prev_ids
    dropped_ids = prev_ids - qual_ids
    changed = bool(new_ids or dropped_ids)
    digest_hours = _digest_hours()
    digest_due = _digest_due(prev_state.get("notifiedAt"), digest_hours)

    log.info(
        "qualifying (rate > %g%%): %d (new=%d dropped=%d) changed=%s digest_due=%s | "
        "loud tier >%g%%: %d (new=%d)",
        threshold,
        len(qualifying),
        len(new_ids),
        len(dropped_ids),
        changed,
        digest_due,
        high_threshold,
        len(high),
        len(new_high_ids),
    )

    # ── LOUD tier alert (fires on new >100% loans regardless of standard tier) ──
    high_sent = False
    if new_high_ids:
        from .scorer import has_no_credit, imputed_credit

        credit_gate = C.AUTOINVEST_MIN_CREDIT_SCORE
        high_to_send = sort_loans([
            ln for ln in high if str(ln["loanId"]) in new_high_ids
        ])
        try:
            lines = [f"🔔 <b>NEW LOAN &gt;{high_threshold:g}% — AUTO-INVEST CANDIDATE</b>"]
            for ln in high_to_send:
                url = ln.get("loanUrl") or ""
                name = (f'<a href="{escape(url, quote=True)}">Loan {ln["loanId"]}</a>'
                        if url else f'Loan {ln["loanId"]}')
                # The rate is guaranteed > high_threshold here, but the INVESTOR
                # also applies the credit gate (>=700, no-score imputed 720). Label
                # the loan with its true investability so the "candidate" alert is
                # honest — a >100% loan with sub-700 credit WILL be skipped.
                score = imputed_credit(ln)
                no_credit = has_no_credit(ln)
                cs = "no-credit→720" if no_credit else f"credit {score:.0f}"
                flag = "✅ will invest" if score >= credit_gate \
                    else f"⚠️ credit <{credit_gate:.0f} — auto-invest will SKIP"
                lines.append(f"• {name}: {ln['interestRate']:.2f}% — ₹{ln.get('amountLeft','')} "
                             f"| {cs} {flag}")
            high_sent = send_telegram_text("\n".join(lines))  # loud by default
            log.info("loud-tier alert: %d new loan(s) >%g%% -> %s",
                     len(high_to_send), high_threshold, "sent" if high_sent else "FAILED")
        except Exception as e:  # noqa: BLE001
            log.warning("loud-tier alert failed: %s", e)

    results = {"telegram": False, "ntfy": False}
    should_notify = (changed and new_ids and qualifying) or (digest_due and qualifying)
    if should_notify:
        # New-only on a change (never re-send an already-notified loan); full
        # set only for a periodic digest.
        if digest_due and not (changed and new_ids):
            to_send = qualifying
            why = "digest"
        else:
            to_send = [ln for ln in qualifying if str(ln["loanId"]) in new_ids]
            why = "change"
        log.info(
            "notifying %d loans (reason=%s, new=%d dropped=%d, qualifying=%d)",
            len(to_send),
            why,
            len(new_ids),
            len(dropped_ids),
            len(qualifying),
        )
        dashboard_url = os.environ.get(
            "DASHBOARD_URL", "https://chirag127.github.io/i2i-yield-watch/"
        )
        stats = {
            "activeCount": len(active),
            "qualifyingCount": len(qualifying),
            "newQualifyingCount": len(new_ids),
            "droppedCount": len(dropped_ids),
            "rateThreshold": threshold,
        }
        results = notify_all(to_send, stats, dashboard_url, threshold)
        if was_any_channel_successful(results):
            storage.save_notify_state(sorted(qual_ids))
            # Record ONLY the loans we actually notified in this run — NOT every
            # qualifying loan. Marking all qual_ids "sent" silently absorbs loans
            # that were never individually notified, so they can never fire later.
            storage.mark_notifications_sent(sorted(str(ln["loanId"]) for ln in to_send))
            log.info("sent: %d loans, notify-state updated", len(to_send))
        else:
            log.warning("no channel succeeded — notify-state NOT updated, will retry next run")
    elif qualifying:
        if changed and not new_ids:
            # only drops changed the set — record new state, do NOT notify.
            # CRITICAL: preserve the previous notifiedAt — stamping now() here
            # silently resets the digest clock WITHOUT sending any message, so
            # a market where loans keep getting funded never fires a digest.
            storage.save_notify_state(
                sorted(qual_ids), notified_at=prev_state.get("notifiedAt")
            )
            log.info("qualifying set shrank (drops only) — state updated, no notify")
        else:
            log.info(
                "qualifying set unchanged (%d loans) and no digest due — staying silent",
                len(qualifying),
            )
    else:
        log.info("no qualifying loans — nothing to notify")
        if changed:
            # set went non-empty -> empty; record so we don't re-fire on next run
            storage.save_notify_state([])

    completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    summary = {
        "runId": run_id,
        "startedAt": started_at,
        "completedAt": completed_at,
        "loansFound": len(fresh),
        "newLoans": len(new_loans),
        "loansArchived": archived,
        "qualifyingLoans": len(qualifying),
        "newQualifyingLoans": len(new_ids),
        "droppedQualifyingLoans": len(dropped_ids),
        "loudTierLoans": len(high),
        "loudTierSent": high_sent,
        "rateThreshold": threshold,
        "highRateThreshold": high_threshold,
        "errors": [],
        "notificationsSent": results,
    }
    # Persist only high-tier loans whose loud alert was delivered. If delivery
    # fails, leave the loan out of highIds so the next poll retries it instead
    # of permanently suppressing the alert after one transient Telegram error.
    if high_ids or high_sent:
        try:
            cur = storage.load_notify_state()
            delivered_high_ids = high_ids - new_high_ids if new_high_ids else high_ids
            if high_sent:
                delivered_high_ids = high_ids
            if sorted(delivered_high_ids) != sorted(cur.get("highIds", [])):
                storage.save_notify_state(
                    sorted(qual_ids), notified_at=cur.get("notifiedAt"),
                    high_ids=sorted(delivered_high_ids),
                )
        except Exception as e:  # noqa: BLE001
            log.warning("could not persist loud-tier state: %s", e)

    storage.append_changelog(summary)

    # Dispatch invest workflow immediately when qualifying loans are detected,
    # so real money is placed within seconds instead of waiting for the next
    # cron tick (up to 10 min). Best-effort: cron fallback still covers it.
    if qualifying and (new_ids or new_high_ids):
        try:
            _dispatch_invest()
        except Exception as e:  # noqa: BLE001
            log.warning("invest dispatch failed (cron fallback): %s", e)

    log.info(
        "run complete: active=%d new=%d archived=%d qualifying=%d newQ=%d droppedQ=%d "
        "loud=%d loudSent=%s",
        len(active),
        len(new_loans),
        archived,
        len(qualifying),
        len(new_ids),
        len(dropped_ids),
        len(high),
        high_sent,
    )
    return summary

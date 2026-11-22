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
from .notify.channels import _enabled, notify_all, send_telegram_text
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
    errors = ["telegram loud delivery failed"] if new_high_ids and not high_sent else []
    old_receipts = prev_state.get("channels")
    # Legacy state cannot identify its successful channel. Preserve its dedup
    # baseline for both channels even when migration starts with alerts disabled.
    prior_receipts = old_receipts if old_receipts is not None else {
        name: prev_state for name in results
    }
    receipts = {
        name: {"qualifyingIds": sorted(set(receipt.get("qualifyingIds", [])) & qual_ids),
               "notifiedAt": receipt.get("notifiedAt")}
        for name, receipt in prior_receipts.items()
    }
    delivered_ids = set()
    dashboard_url = os.environ.get("DASHBOARD_URL", "https://chirag127.github.io/i2i-yield-watch/")
    for channel in results:
        if not _enabled(f"{channel.upper()}_ENABLED"):
            continue
        # Preserve legacy dedup once; a newly enabled channel needs its own receipt.
        prior = prior_receipts.get(channel, {})
        acknowledged = {str(x) for x in prior.get("qualifyingIds", [])} & qual_ids
        timestamp = prior.get("notifiedAt")
        pending_ids = qual_ids - acknowledged
        due = _digest_due(timestamp, digest_hours)
        to_send = [ln for ln in qualifying if str(ln["loanId"]) in pending_ids] \
            if pending_ids else qualifying if due else []
        if to_send:
            stats = {"activeCount": len(active), "qualifyingCount": len(qualifying),
                     "newQualifyingCount": len(pending_ids), "droppedCount": len(dropped_ids),
                     "rateThreshold": threshold}
            outcome = notify_all(to_send, stats, dashboard_url, threshold, channels={channel})
            results[channel] = outcome.get(channel) is True
            if results[channel]:
                sent_ids = {str(ln["loanId"]) for ln in to_send}
                acknowledged.update(sent_ids)
                timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                storage.mark_notifications_sent(sorted(sent_ids))
            else:
                errors.append(f"{channel} detailed delivery failed")
                log.warning("%s delivery failed; unacknowledged loans remain pending", channel)
        receipts[channel] = {"qualifyingIds": sorted(acknowledged), "notifiedAt": timestamp}
        delivered_ids.update(acknowledged)

    # Loud acknowledgements cannot consume detailed retries. Empty high sets re-arm the tier.
    delivered_high_ids = high_ids if high_sent else high_ids & prev_high_ids
    timestamps = [receipt["notifiedAt"] for receipt in receipts.values() if receipt["notifiedAt"]]
    notified_at = max(timestamps) if timestamps else prev_state.get("notifiedAt")
    market_thresholds = {"detailed": threshold, "loud": high_threshold}
    if (receipts != old_receipts or delivered_ids != prev_ids
            or delivered_high_ids != prev_high_ids
            or market_thresholds != prev_state.get("marketThresholds")):
        storage.save_notify_state(sorted(delivered_ids), notified_at=notified_at,
                                  high_ids=sorted(delivered_high_ids), channels=receipts,
                                  market_thresholds=market_thresholds)
    if not qualifying:
        log.info("no qualifying loans — nothing to notify")

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
        "errors": errors,
        "notificationsSent": results,
    }
    storage.append_changelog(summary)

    # Dispatch invest workflow immediately when qualifying loans are detected,
    # so real money is placed within seconds instead of waiting for the next
    # cron tick (up to 10 min). Best-effort: cron fallback still covers it.
    previous_thresholds = prev_state.get("marketThresholds", market_thresholds)
    previous_qual_ids = {str(ln["loanId"]) for ln in existing
                         if (ln.get("interestRate") or 0) > previous_thresholds["detailed"]}
    previous_high_ids = {str(ln["loanId"]) for ln in existing
                         if (ln.get("interestRate") or 0) > previous_thresholds["loud"]}
    # Market novelty is independent of delivery receipts; a send failure must not redispatch money workflows.
    if qualifying and (qual_ids - previous_qual_ids or high_ids - previous_high_ids):
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

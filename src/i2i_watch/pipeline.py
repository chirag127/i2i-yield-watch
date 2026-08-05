"""Orchestration: fetch -> transform -> detect new/archived -> save -> stats ->
notify-new-only -> changelog. Mirrors the Node core/index.js flow.
"""

from __future__ import annotations

import logging
import os
import random
import time
from datetime import datetime, timedelta, timezone

from . import storage
from .notify.channels import notify_all, was_any_channel_successful
from .sources.i2i import fetch_all_loans
from .transform import transform_loans

log = logging.getLogger("i2i_watch")


def _rate_threshold() -> float:
    return float(
        os.environ.get("NOTIFY_RATE_THRESHOLD")
        or os.environ.get("MEDIUM_PRIORITY_RATE_THRESHOLD")
        or "40"
    )


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


def run(raw_rows: list[dict] | None = None) -> dict:
    """One scrape cycle. Returns a summary dict. Raises on hard failure."""
    run_id = f"run_{int(time.time() * 1000)}"
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    jitter_max = int(os.environ.get("STARTUP_JITTER_MS", "2000"))
    if jitter_max > 0:
        time.sleep(random.randint(0, jitter_max) / 1000)

    raw = raw_rows if raw_rows is not None else fetch_all_loans()
    log.info("fetched %d raw rows", len(raw))
    fresh = transform_loans(raw)
    log.info("transformed %d loans", len(fresh))

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
    prev_ids = {str(x) for x in prev_state.get("qualifyingIds", [])}
    new_ids = qual_ids - prev_ids
    dropped_ids = prev_ids - qual_ids
    changed = bool(new_ids or dropped_ids)
    digest_hours = _digest_hours()
    digest_due = _digest_due(prev_state.get("notifiedAt"), digest_hours)

    log.info(
        "qualifying (rate > %g%%): %d (new=%d dropped=%d) changed=%s digest_due=%s",
        threshold,
        len(qualifying),
        len(new_ids),
        len(dropped_ids),
        changed,
        digest_due,
    )

    results = {"telegram": False, "ntfy": False}
    should_notify = (changed and qualifying) or (digest_due and qualifying)
    if should_notify:
        why = "change" if changed else "digest"
        log.info(
            "notifying %d qualifying loans (reason=%s, new=%d dropped=%d)",
            len(qualifying),
            why,
            len(new_ids),
            len(dropped_ids),
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
        results = notify_all(qualifying, stats, dashboard_url, threshold)
        if was_any_channel_successful(results):
            storage.save_notify_state(sorted(qual_ids))
            storage.mark_notifications_sent(sorted(qual_ids))
            log.info("sent: %d loans, notify-state updated", len(qualifying))
        else:
            log.warning("no channel succeeded — notify-state NOT updated, will retry next run")
    elif qualifying:
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
        "rateThreshold": threshold,
        "errors": [],
        "notificationsSent": results,
    }
    storage.append_changelog(summary)
    log.info(
        "run complete: active=%d new=%d archived=%d qualifying=%d newQ=%d droppedQ=%d",
        len(active),
        len(new_loans),
        archived,
        len(qualifying),
        len(new_ids),
        len(dropped_ids),
    )
    return summary

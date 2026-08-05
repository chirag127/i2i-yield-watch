"""Orchestration: fetch -> transform -> detect new/archived -> save -> stats ->
notify-new-only -> changelog. Mirrors the Node core/index.js flow.
"""

from __future__ import annotations

import logging
import os
import random
import time
from datetime import datetime, timezone

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
    new_qualifying = storage.filter_unnotified(qualifying, notified)
    log.info(
        "qualifying (rate > %g%%): %d, new: %d",
        threshold,
        len(qualifying),
        len(new_qualifying),
    )

    results = {"telegram": False, "ntfy": False}
    if new_qualifying:
        dashboard_url = os.environ.get(
            "DASHBOARD_URL", "https://chirag127.github.io/i2i-yield-watch/"
        )
        stats = {
            "activeCount": len(active),
            "qualifyingCount": len(qualifying),
            "newQualifyingCount": len(new_qualifying),
            "rateThreshold": threshold,
        }
        results = notify_all(new_qualifying, stats, dashboard_url, threshold)
        if was_any_channel_successful(results):
            storage.mark_notifications_sent([str(ln["loanId"]) for ln in new_qualifying])
        else:
            log.warning("no channel succeeded — loanIds NOT marked, will retry next run")
    else:
        log.info("no new high-yield loans — skipping notifications")

    completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    summary = {
        "runId": run_id,
        "startedAt": started_at,
        "completedAt": completed_at,
        "loansFound": len(fresh),
        "newLoans": len(new_loans),
        "loansArchived": archived,
        "qualifyingLoans": len(qualifying),
        "newQualifyingLoans": len(new_qualifying),
        "rateThreshold": threshold,
        "errors": [],
        "notificationsSent": results,
    }
    storage.append_changelog(summary)
    log.info(
        "run complete: active=%d new=%d archived=%d qualifying=%d notified=%d",
        len(active),
        len(new_loans),
        archived,
        len(qualifying),
        len(new_qualifying),
    )
    return summary

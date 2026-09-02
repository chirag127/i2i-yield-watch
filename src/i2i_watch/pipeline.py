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
from .util import inr

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


def _bucket_min_threshold() -> float:
    """Silent bucket gate, re-read so runtime env overrides behave like the
    detailed and loud notification gates."""
    return C._f("NOTIFY_BUCKET_MIN_RATE_PCT", C.NOTIFY_BUCKET_MIN_RATE_PCT)


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


def _bucket_snapshot(active: list[dict]) -> dict[str, list[str]]:
    """Return stable rate-notification buckets keyed by loan IDs.
    When NOTIFY_BUCKET_MIN_RATE_PCT is 0 (the default), ALL active loans are
    bucketed — this gives a full market view rather than only >30% loans.
    When set to a positive value, loans at or below that rate are excluded.
    """
    min_rate = _bucket_min_threshold()
    snapshot = {name: [] for name, _low, _high in C.NOTIFY_BUCKETS}
    for loan in active:
        rate = loan.get("interestRate")
        if rate is None or rate <= min_rate:
            continue
        for index, (name, low, high) in enumerate(C.NOTIFY_BUCKETS):
            lower_match = rate > low if index == 0 else rate >= low
            if lower_match and (high is None or rate < high):
                snapshot[name].append(str(loan["loanId"]))
                break
    return {name: sorted(ids) for name, ids in snapshot.items()}


def _bucket_stats(loans: list[dict]) -> dict:
    """Compute aggregate stats for a list of loans: count, total amount left,
    average rate, average credit score, and risk-category distribution."""
    n = len(loans)
    if n == 0:
        return {"count": 0, "totalLeft": 0, "avgRate": None, "avgCredit": None,
                "risks": {}}
    rates = [l.get("interestRate") for l in loans if l.get("interestRate") is not None]
    credits = [l.get("creditScoreNumeric") for l in loans
               if l.get("creditScoreNumeric") is not None]
    total_left = sum(l.get("amountLeft") or 0 for l in loans)
    risks: dict[str, int] = {}
    for l in loans:
        rc = l.get("riskCategory")
        key = rc if rc else "Unrated"
        risks[key] = risks.get(key, 0) + 1
    return {
        "count": n,
        "totalLeft": round(total_left, 2),
        "avgRate": round(sum(rates) / len(rates), 2) if rates else None,
        "avgCredit": round(sum(credits) / len(credits), 1) if credits else None,
        "risks": dict(sorted(risks.items(), key=lambda x: -x[1])),
    }


def _bucket_message(active: list[dict], current: dict[str, list[str]],
                    previous: dict[str, list[str]]) -> str:
    """Build a silent bucket summary with per-bucket totals, averages, and a
    market-wide footer. Links are shown only for newly entering loans."""
    by_id = {str(loan["loanId"]): loan for loan in active}
    lines = ["📊 <b>LOAN RATE BUCKET SUMMARY (ALL RATES)</b>"]
    lines.append("")  # blank line after header

    # ── per-bucket stats ──
    any_nonzero = False
    for name, _low, _high in C.NOTIFY_BUCKETS:
        ids = current.get(name, [])
        bucket_loans = [by_id[lid] for lid in ids if lid in by_id]
        st = _bucket_stats(bucket_loans)
        if st["count"] == 0:
            lines.append(f"• <b>{name}%</b>: 0 loans")
            continue
        any_nonzero = True
        total_str = inr(st["totalLeft"]) or "₹0"
        avg_r = f"{st['avgRate']:.1f}%" if st["avgRate"] is not None else "—"
        avg_c = f"{st['avgCredit']:.0f}" if st["avgCredit"] is not None else "—"
        lines.append(
            f"• <b>{name}%</b>: {st['count']} loan(s) | "
            f"Amount left: {total_str} | Avg rate: {avg_r} | Avg credit: {avg_c}"
        )
        # Show risk breakdown if more than one category
        if len(st["risks"]) > 1:
            risk_str = ", ".join(f"{k}:{v}" for k, v in st["risks"].items())
            lines.append(f"    Risk: {risk_str}")
        # Links only for newly entering loans
        entered = [lid for lid in ids if lid not in set(previous.get(name, []))]
        for loan in sort_loans([by_id[lid] for lid in entered if lid in by_id]):
            lid = str(loan["loanId"])
            rate = loan.get("interestRate")
            amt = inr(loan.get("amountLeft")) or "?"
            url = loan.get("loanUrl") or ""
            label = f"Loan {lid}"
            if url:
                label = f'<a href="{escape(url, quote=True)}">{label}</a>'
            credit = loan.get("creditScore") or "No Score"
            lines.append(
                f"    ↳ {label} — {rate:.2f}% | {amt} left | {credit}"
            )

    # ── market-wide summary footer ──
    all_stats = _bucket_stats(active)
    lines.append("")
    lines.append("<b>── MARKET OVERVIEW ──</b>")
    total_str = inr(all_stats["totalLeft"]) or "₹0"
    avg_r = f"{all_stats['avgRate']:.1f}%" if all_stats["avgRate"] is not None else "—"
    avg_c = f"{all_stats['avgCredit']:.0f}" if all_stats["avgCredit"] is not None else "—"
    lines.append(
        f"Active: {all_stats['count']} loans | Total left: {total_str} | "
        f"Avg rate: {avg_r} | Avg credit: {avg_c}"
    )
    if all_stats["risks"]:
        risk_str = ", ".join(f"{k}:{v}" for k, v in all_stats["risks"].items())
        lines.append(f"Risk: {risk_str}")

    lines.append("")
    lines.append("<i>Silent update: links only for loans newly entering a bucket.</i>")
    return "\n".join(lines)


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

    # Silent bucket tier — full market rate coverage across all active loans.
    # State advances only after Telegram delivery, so a transient outage
    # retries the same change on the next scrape.
    bucket_snapshot = _bucket_snapshot(active)
    previous_buckets = {
        str(name): sorted({str(x) for x in ids})
        for name, ids in (prev_state.get("buckets", {}) or {}).items()
    }
    bucket_changed = bucket_snapshot != previous_buckets
    bucket_sent = False
    if bucket_changed:
        try:
            bucket_sent = send_telegram_text(
                _bucket_message(active, bucket_snapshot, previous_buckets),
                silent=True,
            )
            log.info("bucket summary (%d loans >%g%%): %s",
                     sum(len(ids) for ids in bucket_snapshot.values()),
                     _bucket_min_threshold(),
                     "sent" if bucket_sent else "FAILED")
        except Exception as e:  # noqa: BLE001
            log.warning("bucket summary failed: %s", e)
        if bucket_sent:
            storage.save_notify_state(
                list(prev_state.get("qualifyingIds", [])),
                notified_at=prev_state.get("notifiedAt"),
                buckets=bucket_snapshot,
            )

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
        "bucketChanged": bucket_changed,
        "bucketSent": bucket_sent,
        "bucketCounts": {name: len(ids) for name, ids in bucket_snapshot.items()},
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
        "loud=%d loudSent=%s buckets=%s bucketSent=%s",
        len(active),
        len(new_loans),
        archived,
        len(qualifying),
        len(new_ids),
        len(dropped_ids),
        len(high),
        high_sent,
        {name: len(ids) for name, ids in bucket_snapshot.items()},
        bucket_sent,
    )
    return summary

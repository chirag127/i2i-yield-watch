"""Firestore-backed persistence (firebase-admin). Same collections + doc shapes
as the Node version so `dashboard/` reads unchanged:

  loans/{loanId}       status='active'|'archived', yearMonth, all loan fields
  notifications/{id}   loanId, notifiedAt
  stats/current        aggregate counters
  meta/archiveIndex    { generatedAt, files:[{month,count,lastArchivedAt}] }
  runs/{runId}         run summary

Pure helpers (detect_new_loans / detect_fully_funded / filter_unnotified) need
no Firestore and are unit-tested directly.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

log = logging.getLogger("i2i_watch")

_app = None
_db = None


def _sa_path() -> str:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    # repo layout: <repo>/src/i2i_watch/storage.py -> repo root is two up from src
    repo_root = os.path.abspath(os.path.join(root, ".."))
    p = os.environ.get("FIREBASE_SA_PATH", "i2i-yield-watch-sa.json")
    if os.path.isabs(p):
        return p
    return os.path.join(repo_root, p)


def init():
    global _app, _db
    if _app is not None:
        return
    import firebase_admin
    from firebase_admin import credentials, firestore

    sa = _sa_path()
    if not os.path.exists(sa):
        raise FileNotFoundError(
            f"Firebase service account not found at {sa}. "
            "Set FIREBASE_SA_PATH or place the key at the repo root."
        )
    cred = credentials.Certificate(sa)
    try:
        _app = firebase_admin.get_app("i2i-yield-watch")
    except ValueError:
        _app = firebase_admin.initialize_app(cred, name="i2i-yield-watch")
    _db = firestore.client(_app)


def _ts():
    from firebase_admin import firestore

    return firestore.SERVER_TIMESTAMP


def load_active_loans() -> list[dict]:
    init()
    snap = _db.collection("loans").where("status", "==", "active").get()
    return [{**d.to_dict(), "loanId": d.id} for d in snap]


def save_active_loans(loans: list[dict]) -> None:
    init()
    if not loans:
        log.info("no active loans to save")
        return
    for i in range(0, len(loans), 500):
        batch = _db.batch()
        for loan in loans[i : i + 500]:
            ref = _db.collection("loans").document(str(loan["loanId"]))
            batch.set(
                ref,
                {**loan, "status": "active", "yearMonth": None, "updatedAt": _ts()},
                merge=True,
            )
        batch.commit()
    high = sum(1 for ln in loans if ln.get("priority") == "VERY_HIGH")
    log.info("saved %d active loans (%d high priority)", len(loans), high)


def detect_new_loans(fresh: list[dict], existing: list[dict], notified_ids) -> list[dict]:
    """New = loanId not in notified set AND not already active."""
    notified = notified_ids if isinstance(notified_ids, set) else {str(x) for x in (notified_ids or [])}
    existing_ids = {str(ln["loanId"]) for ln in existing}
    return [
        ln
        for ln in fresh
        if str(ln["loanId"]) not in notified and str(ln["loanId"]) not in existing_ids
    ]


def detect_fully_funded(fresh: list[dict], existing: list[dict]) -> list[dict]:
    """Archive-eligible: disappeared from listing, or now isFullyFunded."""
    fresh_ids = {str(ln["loanId"]) for ln in fresh}
    out = []
    for ex in existing:
        if str(ex["loanId"]) not in fresh_ids:
            out.append({**ex, "archivedReason": "disappeared_from_listing"})
    for fr in fresh:
        if fr.get("isFullyFunded"):
            out.append({**fr, "archivedReason": "fully_funded"})
    return out


def archive_fully_funded_loans(loans: list[dict]) -> int:
    init()
    if not loans:
        return 0
    now = datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")
    archived_at = now.isoformat().replace("+00:00", "Z")

    refs = [_db.collection("loans").document(str(ln["loanId"])) for ln in loans]
    current = _db.get_all(refs)
    current_status = {}
    for doc in current:
        if doc.exists:
            current_status[doc.id] = (doc.to_dict() or {}).get("status")

    new_archived = []
    batch = _db.batch()
    for ln in loans:
        lid = str(ln["loanId"])
        if current_status.get(lid) == "archived":
            continue
        ref = _db.collection("loans").document(lid)
        batch.set(
            ref,
            {
                **ln,
                "status": "archived",
                "yearMonth": month,
                "archivedAt": archived_at,
                "archivedReason": ln.get("archivedReason", "fully_funded"),
                "updatedAt": _ts(),
            },
            merge=True,
        )
        new_archived.append(ln)
    if new_archived:
        batch.commit()
        _update_archive_index(month, len(new_archived), archived_at)
    log.info("archived %d loans to %s", len(new_archived), month)
    return len(new_archived)


def _update_archive_index(month: str, added: int, archived_at: str) -> None:
    ref = _db.collection("meta").document("archiveIndex")
    doc = ref.get()
    data = doc.to_dict() if doc.exists else {"generatedAt": None, "files": []}
    files = list(data.get("files", []))
    match = next((f for f in files if f.get("month") == month), None)
    if match:
        match["count"] = match.get("count", 0) + added
        match["lastArchivedAt"] = archived_at
    else:
        files.append({"month": month, "count": added, "lastArchivedAt": archived_at})
    files.sort(key=lambda f: f.get("month", ""))
    ref.set({"files": files, "generatedAt": archived_at, "updatedAt": _ts()})


def load_notifications_sent() -> set[str]:
    init()
    snap = _db.collection("notifications").get()
    return {d.id for d in snap}


def mark_notifications_sent(loan_ids: list[str]) -> None:
    if not loan_ids:
        return
    init()
    for i in range(0, len(loan_ids), 500):
        batch = _db.batch()
        for lid in loan_ids[i : i + 500]:
            if lid is None:
                continue
            ref = _db.collection("notifications").document(str(lid))
            batch.set(ref, {"loanId": str(lid), "notifiedAt": _ts()}, merge=True)
        batch.commit()
    log.info("marked %d loans as notified", len(loan_ids))


def filter_unnotified(loans: list[dict], notified_ids) -> list[dict]:
    seen = notified_ids if isinstance(notified_ids, set) else {str(x) for x in (notified_ids or [])}
    return [ln for ln in loans if str(ln["loanId"]) not in seen]


def update_stats(active: list[dict], newly_archived: int = 0) -> None:
    init()
    doc = _db.collection("stats").document("current").get()
    existing = doc.to_dict() if doc.exists else {}

    avg_rate = (
        sum(ln.get("interestRate") or 0 for ln in active) / len(active) if active else 0
    )
    avg_score = (
        sum(ln.get("yieldScore") or 0 for ln in active) / len(active) if active else 0
    )
    by_product: dict[str, int] = {}
    for ln in active:
        by_product[ln.get("product") or "Unknown"] = by_product.get(ln.get("product") or "Unknown", 0) + 1
    by_priority = {"VERY_HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for ln in active:
        p = ln.get("priority") or "LOW"
        by_priority[p] = by_priority.get(p, 0) + 1

    stats = {
        "lastUpdated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "totalScrapedAllTime": max(existing.get("totalScrapedAllTime", 0), len(active)),
        "currentActive": len(active),
        "totalArchived": existing.get("totalArchived", 0) + newly_archived,
        "avgInterestRate": round(avg_rate, 2),
        "avgYieldScore": round(avg_score, 2),
        "highPriorityCount": by_priority["VERY_HIGH"],
        "byProduct": by_product,
        "byPriority": by_priority,
    }
    _db.collection("stats").document("current").set({**stats, "updatedAt": _ts()})
    log.info("stats updated")


def append_changelog(run_summary: dict) -> None:
    init()
    rid = run_summary.get("runId") or f"run_{int(datetime.now().timestamp() * 1000)}"
    run_summary["runId"] = rid
    _db.collection("runs").document(rid).set({**run_summary, "updatedAt": _ts()})
    log.info("run logged: %s", rid)

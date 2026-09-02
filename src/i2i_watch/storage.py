"""Persistence with two interchangeable backends, same collections + doc shapes
so `dashboard/` reads unchanged:

  loans/{loanId}       status='active'|'archived', yearMonth, all loan fields
  notifications/{id}   loanId, notifiedAt
  stats/current        aggregate counters
  meta/archiveIndex    { generatedAt, files:[{month,count,lastArchivedAt}] }
  runs/{runId}         run summary

Backends:
  firebase  Firestore (firebase-admin) — preferred when the module + a service
            account (FIREBASE_SA_PATH/FIREBASE_SA_JSON or repo-root key) exist.
  json      git-as-DB fallback — data/active-loans.json, data/notifications-sent.json,
            data/stats.json, data/archive/<month>.json. No external DB needed.

init() picks the backend once: Firebase if importable + key present, else JSON
(logs a warning, never raises). Each public fn branches on `_mode`.

Pure helpers (detect_new_loans / detect_fully_funded / filter_unnotified) need
no backend and are unit-tested directly.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("i2i_watch")

_app = None
_db = None
_mode: str | None = None


def _repo_root() -> Path:
    # <repo>/src/i2i_watch/storage.py -> repo root is two up from src
    return Path(__file__).resolve().parent.parent.parent


def _sa_path() -> str:
    p = os.environ.get("FIREBASE_SA_PATH", "i2i-yield-watch-sa.json")
    if os.path.isabs(p):
        return p
    return str(_repo_root() / p)


def _data_dir() -> Path:
    d = _repo_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# --- JSON backend helpers ---------------------------------------------------


def _load_json(name: str, default):
    f = _data_dir() / name
    if not f.exists():
        return default
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("could not read %s: %s", name, e)
        return default


def _write_json(name: str, payload) -> None:
    """Atomic write: temp file + os.replace, so a killed run (timeout, crash)
    can never leave a truncated JSON (which the next run would misread and
    re-fire duplicate notifications from). Same-directory tmp keeps the rename
    atomic on POSIX and Windows."""
    f = _data_dir() / name
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(tmp, f)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:  # noqa: BLE001
                pass


def init():
    global _app, _db, _mode
    if _mode is not None:
        return

    # Force git-as-DB JSON backend (no Firestore quota). Firestore free tier
    # 429s under the 5x/15min self-loop; JSON is the intended backend per the
    # repo rule. Set I2I_STORAGE=json (default here) to skip Firebase entirely.
    if os.environ.get("I2I_STORAGE", "json").strip().lower() == "json":
        log.info("storage backend: json (git-as-DB) — Firestore skipped (I2I_STORAGE=json)")
        _mode = "json"
        return

    sa = _sa_path()
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
    except ImportError:
        log.warning("firebase-admin not installed — using JSON (git-as-DB) backend")
        _mode = "json"
        return

    sa_json = os.environ.get("FIREBASE_SA_JSON")
    cred = None
    if sa_json:
        try:
            cred = credentials.Certificate(json.loads(sa_json))
        except (ValueError, json.JSONDecodeError) as e:
            log.warning("FIREBASE_SA_JSON not usable (%s) — trying SA file", e)
            cred = None

    if cred is None:
        if not os.path.exists(sa):
            log.warning(
                "Firebase service account not found at %s — using JSON (git-as-DB) backend "
                "(set FIREBASE_SA_PATH/FIREBASE_SA_JSON or place the key at the repo root)",
                sa,
            )
            _mode = "json"
            return
        try:
            cred = credentials.Certificate(sa)
        except (ValueError, UnicodeDecodeError, OSError) as e:
            log.warning(
                "Firebase service account at %s not usable (%s) — using JSON (git-as-DB) "
                "backend. Likely git-crypt-locked locally; CI decrypts it.",
                sa,
                e,
            )
            _mode = "json"
            return
    try:
        _app = firebase_admin.get_app("i2i-yield-watch")
    except ValueError:
        _app = firebase_admin.initialize_app(cred, name="i2i-yield-watch")
    _db = firestore.client(_app)
    _mode = "firebase"
    log.info("storage backend: firebase")


def _ts():
    from firebase_admin import firestore

    return firestore.SERVER_TIMESTAMP


def load_active_loans() -> list[dict]:
    init()
    if _mode == "json":
        loans = _load_json("active-loans.json", [])
        return [ln for ln in loans if ln.get("status", "active") == "active"]
    snap = _db.collection("loans").where("status", "==", "active").get()
    return [{**d.to_dict(), "loanId": d.id} for d in snap]


def save_active_loans(loans: list[dict]) -> None:
    init()
    if not loans:
        log.info("no active loans to save")
        if _mode == "json":
            _write_json("active-loans.json", [])
        return
    high = sum(1 for ln in loans if ln.get("priority") == "VERY_HIGH")

    if _mode == "json":
        now = _now_iso()
        docs = [
            {**ln, "status": "active", "yearMonth": None, "updatedAt": now}
            for ln in loans
        ]
        _write_json("active-loans.json", docs)
        log.info("saved %d active loans (%d high priority)", len(loans), high)
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

    if _mode == "json":
        existing = _load_json(f"archive/{month}.json", [])
        by_id = {str(ln.get("loanId")): ln for ln in existing}
        new_archived = []
        for ln in loans:
            lid = str(ln["loanId"])
            if lid in by_id:
                continue
            doc = {
                **ln,
                "status": "archived",
                "yearMonth": month,
                "archivedAt": archived_at,
                "archivedReason": ln.get("archivedReason", "fully_funded"),
                "updatedAt": archived_at,
            }
            by_id[lid] = doc
            new_archived.append(doc)
        if new_archived:
            _write_json(f"archive/{month}.json", list(by_id.values()))
            _update_archive_index(month, len(new_archived), archived_at)
        log.info("archived %d loans to %s", len(new_archived), month)
        return len(new_archived)

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
    if _mode == "json":
        data = _load_json("archive/index.json", {"generatedAt": None, "files": []})
        files = list(data.get("files", []))
        match = next((f for f in files if f.get("month") == month), None)
        if match:
            match["count"] = match.get("count", 0) + added
            match["lastArchivedAt"] = archived_at
        else:
            files.append({"month": month, "count": added, "lastArchivedAt": archived_at})
        files.sort(key=lambda f: f.get("month", ""))
        _write_json(
            "archive/index.json",
            {"files": files, "generatedAt": archived_at, "updatedAt": archived_at},
        )
        return

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
    if _mode == "json":
        return {str(x) for x in _load_json("notifications-sent.json", [])}
    snap = _db.collection("notifications").get()
    return {d.id for d in snap}


def mark_notifications_sent(loan_ids: list[str]) -> None:
    if not loan_ids:
        return
    init()
    if _mode == "json":
        existing = {str(x) for x in _load_json("notifications-sent.json", [])}
        for lid in loan_ids:
            if lid is not None:
                existing.add(str(lid))
        _write_json("notifications-sent.json", sorted(existing))
        log.info("marked %d loans as notified", len(loan_ids))
        return

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


def load_notify_state() -> dict:
    """Last notification snapshot: detailed IDs, loud IDs, bucket IDs, timestamp.

    Used to notify on qualifying-set CHANGES (loan appears/drops) + drive the
    periodic digest. JSON backend only keeps a single doc; firebase mirrors it
    under meta/notifyState.
    """
    init()
    default = {"qualifyingIds": [], "notifiedAt": None, "highIds": [], "buckets": {}}
    if _mode == "json":
        return _load_json("notify-state.json", default)
    doc = _db.collection("meta").document("notifyState").get()
    return doc.to_dict() if doc.exists else default


_UNSET = object()


def save_notify_state(qualifying_ids: list[str], notified_at: str | None | object = _UNSET,
                      high_ids: list[str] | None = None,
                      buckets: dict[str, list[str]] | None = None) -> None:
    """Persist the notification snapshot without dropping another tier.

    ``high_ids`` tracks the loud rate tier and ``buckets`` tracks the silent
    >30% bucket summary. Optional fields are merged with the existing document,
    so a standard-tier update cannot erase either tier's dedup state.
    """
    init()
    current = load_notify_state()
    payload = {
        "qualifyingIds": sorted({str(x) for x in qualifying_ids}),
        "notifiedAt": _now_iso() if notified_at is _UNSET else notified_at,
        "highIds": sorted({str(x) for x in (high_ids if high_ids is not None else current.get("highIds", []))}),
        "buckets": {
            str(name): sorted({str(x) for x in ids})
            for name, ids in (buckets if buckets is not None else current.get("buckets", {})).items()
        },
    }
    if _mode == "json":
        _write_json("notify-state.json", {**payload, "updatedAt": _now_iso()})
        return
    _db.collection("meta").document("notifyState").set({**payload, "updatedAt": _ts()})


def update_stats(active: list[dict], newly_archived: int = 0) -> None:
    init()
    if _mode == "json":
        existing = _load_json("stats.json", {})
    else:
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
        "lastUpdated": _now_iso(),
        "totalScrapedAllTime": max(existing.get("totalScrapedAllTime", 0), len(active)),
        "currentActive": len(active),
        "totalArchived": existing.get("totalArchived", 0) + newly_archived,
        "avgInterestRate": round(avg_rate, 2),
        "avgYieldScore": round(avg_score, 2),
        "highPriorityCount": by_priority["VERY_HIGH"],
        "byProduct": by_product,
        "byPriority": by_priority,
    }
    if _mode == "json":
        _write_json("stats.json", {**stats, "updatedAt": stats["lastUpdated"]})
    else:
        _db.collection("stats").document("current").set({**stats, "updatedAt": _ts()})
    log.info("stats updated")


def _invested_file(account: str | None = None) -> str:
    """Per-account invested-loans file: legacy invested-loans.json for the
    default account; invested-loans-<account>.json for every secondary one, so
    dedup and cancel --all-invested never cross accounts."""
    from . import accounts

    acct = account or accounts.active_account()
    return f"invested-loans{accounts.storage_name(acct)}.json"


def load_invested(account: str | None = None) -> list[int]:
    """loanIds placed by the auto-investor for this account (JSON backend only)."""
    try:
        return [int(x) for x in _load_json(_invested_file(account), [])]
    except Exception:  # noqa: BLE001
        return []


def record_invested(loan_ids: list[int], account: str | None = None) -> None:
    """Append placed loanIds to this account's invested-loans file
    (for cancel --all-invested + cross-run dedup)."""
    merged = sorted(set(load_invested(account)) | {int(x) for x in loan_ids})
    _write_json(_invested_file(account), merged)
    log.info("recorded %d invested loan(s) for %s", len(loan_ids), _invested_file(account))


def _escrow_truth_file(account: str | None = None) -> str:
    """Per-account escrow-truth file: escrow-truth.json (default account),
    escrow-truth-<account>.json for every secondary one — mirrors the
    invested-loans naming so accounts never share balance state."""
    from . import accounts

    acct = account or accounts.active_account()
    return f"escrow-truth{accounts.storage_name(acct)}.json"


def load_escrow_truth(account: str | None = None) -> dict | None:
    """Last balance the platform itself reported on an investorNow rejection:
    {"amount": float, "observedAt": iso}. None when never observed."""
    try:
        d = _load_json(_escrow_truth_file(account), None)
        if isinstance(d, dict) and d.get("amount") is not None:
            return d
    except Exception:  # noqa: BLE001
        pass
    return None


def save_escrow_truth(amount: float, account: str | None = None) -> None:
    """Persist the authoritative investable escrow figure from a rejection, so
    wallet()/wallet-check and the plan sizing see it without a live order."""
    _write_json(_escrow_truth_file(account), {
        "amount": float(amount), "observedAt": _now_iso(),
    })


def load_idle_state() -> dict:
    """Idle-capital watchdog state: {lastQualifiedAt: iso|null} — when the auto-
    investor last saw a qualifying loan. Drives the 'no qualifying loans for N
    days' nudge so idle escrow never goes silently unmonitored."""
    init()
    default = {"lastQualifiedAt": None}
    if _mode == "json":
        return _load_json("invest-idle.json", default)
    doc = _db.collection("meta").document("investIdle").get()
    return doc.to_dict() if doc.exists else default


def save_idle_state(last_qualified_at: str | None) -> None:
    """Persist the last-qualified timestamp. Call with None on a qualifying run
    (resets the idle clock), with the old value on an empty run (idle continues)."""
    init()
    payload = {"lastQualifiedAt": last_qualified_at}
    if _mode == "json":
        _write_json("invest-idle.json", {**payload, "updatedAt": _now_iso()})
        return
    _db.collection("meta").document("investIdle").set({**payload, "updatedAt": _ts()})


def append_changelog(run_summary: dict) -> None:
    init()
    rid = run_summary.get("runId") or f"run_{int(datetime.now().timestamp() * 1000)}"
    run_summary["runId"] = rid
    if _mode == "json":
        runs = _load_json("runs.json", [])
        runs = [r for r in runs if r.get("runId") != rid]
        runs.append({**run_summary, "updatedAt": _now_iso()})
        runs = runs[-200:]
        _write_json("runs.json", runs)
        log.info("run logged: %s", rid)
        return
    _db.collection("runs").document(rid).set({**run_summary, "updatedAt": _ts()})
    log.info("run logged: %s", rid)

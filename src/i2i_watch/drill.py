"""Read-only alert drill: inject ONE synthetic above-threshold loan into the
REAL pipeline and prove the Telegram alert chain fires end-to-end.

Money-safety invariants (the drill never touches real money):
  - No i2i network calls: raw rows are injected; listing endpoints and
    i2i credentials are never touched.
  - Storage is redirected to an ephemeral temp dir; nothing is committed.
  - The real-money invest dispatch is neutralized in-process.
The only real side effect is the Telegram message itself, labeled [DRILL].
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from . import pipeline
from . import storage
from .util import log


def _synthetic_row(loan_id: str, rate: float) -> dict:
    """Raw listing row (HAR-verified field names, same schema as the live feed)."""
    return {
        "pl_bloan_id": loan_id,
        "pl_user_id": None,  # no borrower profile link for a fake loan
        "pl_applicable_rate": f"{rate:.2f}",
        "product_name": "Regular Loans",
        "pl_amt": "50000",
        "pl_amt_left": "20000",
        "pl_status": 1,
        "usr_cibil_score": "742",
        "bloan_i2i_category": "D",
        "bloan_desc": "[DRILL] Synthetic loan — end-to-end alert test, not a real listing",
        "usr_fname": "Alert",
        "usr_lname": "Drill",
    }


def run(rate: float | None = None) -> dict:
    """Run the drill. Returns a summary dict; e2eConfirmed=False means the
    Telegram chain is broken and the caller must exit non-zero."""
    high = pipeline._high_rate_threshold()
    detailed = pipeline._rate_threshold()
    rate = float(rate) if rate is not None else high + 10.0
    if not rate > max(high, detailed):
        raise ValueError(
            f"drill rate {rate} must be > max(loud {high:g}, detailed {detailed:g}) "
            "— a below-gate drill proves nothing"
        )

    pipeline._dispatch_invest = lambda: None  # drill: never dispatch real-money invest

    tmp = Path(tempfile.mkdtemp(prefix="i2i-drill-"))
    os.environ["I2I_STORAGE"] = "json"
    storage._mode = None
    storage._app = None
    storage._db = None
    storage._data_dir = lambda: tmp

    loan_id = f"DRILL{int(time.time())}"
    summary = pipeline.run(raw_rows=[_synthetic_row(loan_id, rate)])
    out = {
        "drill": True,
        "loanId": loan_id,
        "rate": rate,
        "detailedAlertSent": bool(summary["notificationsSent"].get("telegram")),
        "loudAlertSent": bool(summary.get("loudTierSent")),
        "bucketSummarySent": bool(summary.get("bucketSent")),
        "storageDir": str(tmp),
        "e2eConfirmed": bool(summary.get("loudTierSent"))
        and bool(summary["notificationsSent"].get("telegram")),
    }
    log.info("alert drill: %s", out)
    return out

"""EMI-status / default analytics for the investor portfolio.

Fetches the SAME data the SPA's investoraccount/emistatus page shows and turns
it into a structured report:

  - the full per-loan book (loanDetailBorrowerWiseInDetail, paged)
  - the aggregate overview (getEMIStatusOverview + getEMIStatusOverviewInDetail)
  - per-loan delay/denial time (days overdue, from the row's status fields or
    the per-loan emi/loanEMIStatus endpoint) and remaining payment
  - aggregates: how much is still unpaid, how many borrowers are defaulted,
    split into the platform's delay buckets (<30 / 30-90 / >90 days)

Pure functions (normalize_loan, delay_bucket, build_aggregates) take plain
dicts so the analytics unit-test without a network. The report is written as
JSON (one file per account) so it can be committed to a data repo.

    python -m i2i_watch emireport --account chirag --out data/emi-chirag.json

No money moves here — this is a read-only portfolio-health snapshot.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path

from . import accounts
from .client import I2iClient

log = logging.getLogger("i2i_watch")

# ── field-name fallbacks (SPA column headers are the source of truth; the raw
#    row keys vary, so accept several spellings) ──────────────────────────────
LOAN_ID_KEYS = ("loanId", "pl_bloan_id", "bloan_id", "id")
BORROWER_NAME_KEYS = ("borrowerName", "bname", "name", "userName", "usr_name")
AMOUNT_INVESTED_KEYS = ("totalInvestment", "investedAmount", "amountInvested",
                        "totalInvestedAmount", "loanAmount", "pl_amt")
AMOUNT_RECEIVED_KEYS = ("totalAmountRec", "totalReceived", "receivedAmount",
                        "amountReceived", "totalAmountReceived", "principalRec")
PRINCIPAL_PENDING_KEYS = ("totalPrincipalPen", "totalPriPen", "principalPending",
                          "principalPendingAmount", "totalPrincipalPending",
                          "pendingPrincipal")
INTEREST_PENDING_KEYS = ("totalIntPen", "interestPending", "interestPendingAmount",
                         "totalInterestPending", "pendingInterest")
TOTAL_PENDING_KEYS = ("totalAmountPen", "totalPending", "totalAmountPending",
                      "amountPending", "pendingAmount", "remainingAmount")
LAST_PAYMENT_KEYS = ("lastPaymentDate", "lastPaymentReceivedDate", "lastRecDate",
                     "lastPaymentRecDate", "lastPaymentDate1")
RATE_KEYS = ("loanInt", "interestRate", "rate", "pl_current_rate", "avgIntRate")
DISBURSAL_KEYS = ("loanDisbDate", "disbursalDate", "disDate", "loanDisbursalDate")
TENURE_KEYS = ("tenure", "bloan_tenure")
RISK_KEYS = ("loanCat", "riskCategory", "bloan_i2i_category", "category")
STATUS_KEYS = ("currentStatus", "loanStatus", "emiStatus", "status")
DELAY_DAYS_KEYS = ("delayedDays", "delayDays", "daysDelayed", "noOfDaysDelayed",
                   "overdueDays", "daysOverdue", "pendingDays")
EMI_PAID_KEYS = ("paidEMI", "EMIPaid", "emiPaid", "emiReceived")
EMI_PENDING_KEYS = ("unpaidEMI", "EMIPending", "emiPending", "emiPendingAmount")


def _first(d: dict, keys: tuple[str, ...], default=None):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def normalize_loan(row: dict) -> dict:
    """Raw emistatus row -> normalized record with the numbers the analytics
    need. Unknown fields are passed through as None (never invented)."""
    if not isinstance(row, dict):
        return {}
    principal_pending = _number(_first(row, PRINCIPAL_PENDING_KEYS))
    interest_pending = _number(_first(row, INTEREST_PENDING_KEYS))
    pending_value = _first(row, TOTAL_PENDING_KEYS)
    total_pending = _number(pending_value)
    if (pending_value is None and principal_pending is not None
            and interest_pending is not None):
        total_pending = _number(principal_pending + interest_pending)
    status = str(_first(row, STATUS_KEYS, "") or "").strip()
    delay_days = _number(_first(row, DELAY_DAYS_KEYS))
    if delay_days is not None and (delay_days < 0 or (
            delay_days == 0 and ("delay" in status.lower() or "overdue" in status.lower()))):
        delay_days = None
    return {
        "loanId": _first(row, LOAN_ID_KEYS),
        "borrowerName": _first(row, BORROWER_NAME_KEYS),
        "amountInvested": _number(_first(row, AMOUNT_INVESTED_KEYS)),
        "amountReceived": _number(_first(row, AMOUNT_RECEIVED_KEYS)),
        "principalPending": principal_pending,
        "interestPending": interest_pending,
        "totalPending": total_pending,
        "rate": _number(_first(row, RATE_KEYS)),
        "riskCategory": _first(row, RISK_KEYS),
        "tenure": _number(_first(row, TENURE_KEYS)),
        "disbursalDate": _first(row, DISBURSAL_KEYS),
        "lastPaymentDate": _first(row, LAST_PAYMENT_KEYS),
        "currentStatus": status,
        "delayDays": delay_days,
        "emiPaid": _number(_first(row, EMI_PAID_KEYS)),
        "emiPending": _number(_first(row, EMI_PENDING_KEYS)),
        "raw": row,  # keep the full original row — nothing is ever lost
    }


def delay_bucket(days: float | None) -> str:
    """Platform-style bucket for a delay: 'ontime' | 'lt30' | '30to90' | 'gt90'
    | 'unknown'. A loan marked delayed without a day count is 'unknown'
    (counted as defaulted-by-status in the aggregate, not by days)."""
    days = _number(days)
    if days is None or days < 0:
        return "unknown"
    if days == 0:
        return "ontime"
    if days < 30:
        return "lt30"
    if days <= 90:
        return "30to90"
    return "gt90"


def is_defaulted(loan: dict) -> bool:
    """Defaulted = has ANY unpaid delay. Status-derived (Delayed / overdue) or
    day-count-derived (>0 days). On-time loans and fully-closed loans are not
    defaulted."""
    status = str(loan.get("currentStatus") or "").lower()
    if status in ("closed", "fully closed", "pre closed", "paid", "ontime",
                  "on time", "advanced"):
        return False
    if "delay" in status or "overdue" in status:
        return True
    days = _number(loan.get("delayDays"))
    if days is not None and days > 0:
        return True
    return False


def _sum_amounts(loans: list[dict], key: str) -> float | None:
    values = [_number(loan.get(key)) for loan in loans]
    if any(value is None for value in values):
        return None
    total = _number(sum(values))
    return round(total, 2) if total is not None else None


def build_aggregates(loans: list[dict]) -> dict:
    """Aggregate the normalized loan list: counts + money by delay bucket,
    defaulted borrower detail, and the platform's pending splits."""
    loans = [ln for ln in loans if ln]
    total_invested = _sum_amounts(loans, "amountInvested")
    total_received = _sum_amounts(loans, "amountReceived")

    buckets: dict[str, list[dict]] = {}
    defaulted: list[dict] = []
    for ln in loans:
        b = delay_bucket(ln.get("delayDays"))
        buckets.setdefault(b, []).append(ln)
        if is_defaulted(ln):
            defaulted.append(ln)

    return {
        "totalLoans": len(loans),
        "totalInvested": total_invested,
        "totalReceived": total_received,
        "totalPending": _sum_amounts(loans, "totalPending"),
        "principalPending": _sum_amounts(loans, "principalPending"),
        "interestPending": _sum_amounts(loans, "interestPending"),
        "delayedBuckets": {
            bucket: {"count": len(buckets.get(bucket, [])),
                     "pending": _sum_amounts(buckets.get(bucket, []), "totalPending")}
            for bucket in ("ontime", "lt30", "30to90", "gt90", "unknown")
        },
        "defaulted": {
            "count": len(defaulted),
            "totalPending": _sum_amounts(defaulted, "totalPending"),
            "loans": sorted(defaulted,
                            key=lambda d: (_number(d.get("totalPending")) is not None,
                                           _number(d.get("totalPending")) or 0),
                            reverse=True),
        },
        "recoveryRate": (round(total_received / total_invested, 4)
                         if total_invested and total_received is not None else None),
    }


def build_report(client: I2iClient, account: str | None = None,
                 fetch_loan_status: bool = True) -> dict:
    """Full EMI-status snapshot for one account: overview + detail aggregates,
    the normalized loan book, and the default analytics. Loan collection must
    succeed; optional overview and status endpoints remain best-effort."""
    acct = account or accounts.active_account()
    overview = {}
    detail = {}
    try:
        d = client.emi_status_overview()
        overview = d.get("body", d) if isinstance(d, dict) else {}
        log.info("emi overview keys: %s", sorted(overview.keys()))
    except Exception as e:  # noqa: BLE001
        log.warning("emi overview failed: %s", e)
    try:
        d = client.emi_status_detail()
        detail = d.get("body", d) if isinstance(d, dict) else {}
        log.info("emi detail keys: %s", sorted(detail.keys()))
    except Exception as e:  # noqa: BLE001
        log.warning("emi detail failed: %s", e)

    raw_rows = client.emi_loans()
    if not isinstance(raw_rows, list) or any(
            not isinstance(row, dict) or _first(row, LOAN_ID_KEYS) is None
            for row in raw_rows):
        raise ValueError("Invalid EMI loan collection")
    if raw_rows:
        log.info("first emi row keys: %s", sorted(raw_rows[0].keys()))

    loans = [normalize_loan(r) for r in raw_rows]

    # Enrich with per-loan delay/denial time where the row itself lacks it.
    if fetch_loan_status:
        enriched = 0
        for ln in loans:
            if ln.get("delayDays") not in (None, 0) or not ln.get("loanId"):
                continue
            try:
                st = client.emi_loan_status(ln["loanId"])
                body = st.get("body", st) if isinstance(st, dict) else {}
                if isinstance(body, dict):
                    status = normalize_loan({
                        "currentStatus": _first(body, STATUS_KEYS, ln["currentStatus"]),
                        "delayDays": _first(body, DELAY_DAYS_KEYS, ln["delayDays"]),
                    })
                    ln["currentStatus"] = status["currentStatus"]
                    ln["delayDays"] = status["delayDays"]
                    enriched += 1
            except Exception:  # noqa: BLE001
                pass
        if enriched:
            log.info("enriched %d loans with per-loan EMI status", enriched)

    try:
        delayed = client.delayed_loan_status()
        log.info("delayedLoanStatus keys: %s", sorted(delayed.keys())
                 if isinstance(delayed, dict) else type(delayed).__name__)
    except Exception as e:  # noqa: BLE001
        log.warning("delayed loan status failed: %s", e)
        delayed = {}

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "account": acct,
        "source": "https://www.i2ifunding.com/investoraccount/emistatus",
        "overview": overview,
        "overviewDetail": detail,
        "delayedLoanStatus": delayed if isinstance(delayed, dict) else {},
        "loans": loans,
        "aggregates": build_aggregates(loans),
    }
    return report


def write_report(report: dict, out_path: str) -> str:
    """Atomic JSON write; returns the path written."""
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False,
                              default=str), encoding="utf-8")
    os.replace(tmp, p)
    return str(p)


def run(account: str | None = None, out_path: str | None = None,
        quiet: bool = False) -> int:
    """Fetch + write the EMI-status report for one account.
    out_path defaults to data/emi-<account>.json (repo data dir)."""
    acct = account or accounts.active_account()
    try:
        client = I2iClient.from_env(acct)
    except SystemExit as e:  # no auth
        log.error("%s", e)
        print(f"no auth for '{acct}' — set credentials to fetch the EMI report")
        return 1

    try:
        report = build_report(client, acct)
    except Exception as e:  # noqa: BLE001
        log.error("EMI report failed: %s", type(e).__name__)
        print(f"EMI report failed for '{acct}' — no report written")
        return 1
    agg = report["aggregates"]
    path = write_report(report, out_path or f"data/emi-{acct}.json")
    if not quiet:
        def money(amount: float | None) -> str:
            return f"{amount:,.0f}" if amount is not None else "unknown"

        print(f"account={acct} report -> {path}")
        print(f"  loans: {agg['totalLoans']} | invested Rs {money(agg['totalInvested'])} "
              f"| received Rs {money(agg['totalReceived'])}")
        print(f"  pending: Rs {money(agg['totalPending'])} "
              f"(principal Rs {money(agg['principalPending'])} + "
              f"interest Rs {money(agg['interestPending'])})")
        d = agg["delayedBuckets"]
        print(f"  delay buckets: ontime {d['ontime']['count']} | "
              f"<30d {d['lt30']['count']} | 30-90d {d['30to90']['count']} | "
              f">90d {d['gt90']['count']} | unknown {d['unknown']['count']}")
        print(f"  DEFAULTED: {agg['defaulted']['count']} loan(s), "
              f"Rs {money(agg['defaulted']['totalPending'])} remaining")
        if agg["recoveryRate"] is not None:
            print(f"  recovery rate: {agg['recoveryRate'] * 100:.1f}%")
    return 0

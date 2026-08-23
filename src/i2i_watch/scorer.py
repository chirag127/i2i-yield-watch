"""Yield scoring engine — opportunity score (higher rate = better), NOT a risk
score. High rates never penalized.

Ranking importance (both notify sort and auto-invest select):
  1. RETURN — the loan's interest rate / expected return (top weight).
  2. CREDIT SCORE of the borrower.
  3. TENURE — longer locks the high rate in longer (minor factor).
A borrower with NO credit score is IMPUTED as config.NO_CREDIT_IMPUTED_SCORE
(720 — passes the 700 gate, ranks as High Risk / High Uncertainty below any real
750+ score) for ranking, and is flagged "no credit score" in the notification
(see transform.format_loan_block).
"""

from __future__ import annotations

import os

from . import config as C
from .util import tenure_months

BOUNDS = {
    "interest_rate": (0, 200),
    "credit_score": (300, 900),
    "monthly_income": (0, 2_000_000),
    "funding_remaining": (0, 100),
    "loan_amount": (0, 5_000_000),
    "tenure": (0, 36),
}

# Rate first, credit second, tenure third — then the minor factors. Weights sum
# to 1.0. Tenure kept small so it only breaks near-ties, never outranks a
# materially higher rate or a much better credit score.
WEIGHTS = {
    "interest_rate": 0.55,
    "credit_score": 0.28,
    "tenure": 0.07,
    "monthly_income": 0.04,
    "funding_remaining": 0.03,
    "loan_amount": 0.03,
}


def imputed_credit(loan: dict) -> float:
    """Borrower credit score for RANKING: the real 300-900 score, or the imputed
    NO_CREDIT_IMPUTED_SCORE (720, high-risk band) when there is no score."""
    s = loan.get("creditScoreNumeric")
    if s is None or s != s:
        return C.NO_CREDIT_IMPUTED_SCORE
    return float(s)


def has_no_credit(loan: dict) -> bool:
    """True when the borrower has no credit score (drives the notify flag)."""
    s = loan.get("creditScoreNumeric")
    return s is None or s != s


def normalize(value: float | None, lo: float, hi: float) -> float:
    """Min-max to 0..1; 0.5 (neutral) for None/NaN; clamps out-of-bounds."""
    if value is None or value != value:
        return 0.5
    if hi == lo:
        return 0.5
    clamped = max(lo, min(hi, value))
    return (clamped - lo) / (hi - lo)


def calculate_yield_score(loan: dict) -> float:
    """0..100 opportunity score, 2dp. No-credit -> imputed 720 (high-risk)."""
    rate_n = normalize(loan.get("interestRate"), *BOUNDS["interest_rate"])
    credit_n = normalize(imputed_credit(loan), *BOUNDS["credit_score"])
    income_n = normalize(loan.get("monthlyIncome"), *BOUNDS["monthly_income"])
    funding_n = normalize(loan.get("fundingRemaining"), *BOUNDS["funding_remaining"])
    amount_n = normalize(loan.get("loanAmount"), *BOUNDS["loan_amount"])
    tenure_n = normalize(tenure_months(loan.get("tenure")), *BOUNDS["tenure"])
    raw = (
        rate_n * WEIGHTS["interest_rate"]
        + credit_n * WEIGHTS["credit_score"]
        + tenure_n * WEIGHTS["tenure"]
        + income_n * WEIGHTS["monthly_income"]
        + funding_n * WEIGHTS["funding_remaining"]
        + amount_n * WEIGHTS["loan_amount"]
    )
    return round(raw * 100, 2)


def get_priority(interest_rate: float | None) -> str:
    """VERY_HIGH / MEDIUM / LOW priority LABEL from rate (env-tunable). This is a
    display bucket only — NOT the notify gate (NOTIFY_MIN_RATE_PCT)."""
    if interest_rate is None:
        return "LOW"
    high = float(os.environ.get("PRIORITY_HIGH_RATE_PCT", "70"))
    med = float(os.environ.get("PRIORITY_MEDIUM_RATE_PCT", "50"))
    if interest_rate >= high:
        return "VERY_HIGH"
    if interest_rate >= med:
        return "MEDIUM"
    return "LOW"


def _num(v: object) -> float:
    """Numeric or -inf so nulls sort last within a key."""
    if v is None or (isinstance(v, float) and v != v):
        return float("-inf")
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("-inf")


def sort_loans(loans: list[dict]) -> list[dict]:
    """New list sorted by importance: rate desc, credit desc (no-credit=720),
    tenure desc, then income/amount. Funding-remaining is NOT a sort key."""
    return sorted(
        loans,
        key=lambda ln: (
            _num(ln.get("interestRate")),
            imputed_credit(ln),
            _num(tenure_months(ln.get("tenure"))),
            _num(ln.get("monthlyIncome")),
            _num(ln.get("loanAmount")),
        ),
        reverse=True,
    )

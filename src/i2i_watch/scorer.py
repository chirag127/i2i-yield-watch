"""Yield scoring engine — opportunity score (higher rate = better), NOT a risk
score. High rates never penalized. Missing/no-credit is neutral (0.5) for the
score and ranks HIGH for sorting — new-to-credit borrowers are favored.
"""

from __future__ import annotations

import os

BOUNDS = {
    "interest_rate": (0, 200),
    "credit_score": (300, 900),
    "monthly_income": (0, 2_000_000),
    "funding_remaining": (0, 100),
    "loan_amount": (0, 5_000_000),
}

WEIGHTS = {
    "interest_rate": 0.55,
    "credit_score": 0.30,
    "monthly_income": 0.05,
    "funding_remaining": 0.05,
    "loan_amount": 0.05,
}


def normalize(value: float | None, lo: float, hi: float) -> float:
    """Min-max to 0..1; 0.5 (neutral) for None/NaN; clamps out-of-bounds."""
    if value is None or value != value:
        return 0.5
    if hi == lo:
        return 0.5
    clamped = max(lo, min(hi, value))
    return (clamped - lo) / (hi - lo)


def calculate_yield_score(loan: dict) -> float:
    """0..100 opportunity score, 2dp. No-credit -> neutral (never penalized)."""
    rate_n = normalize(loan.get("interestRate"), *BOUNDS["interest_rate"])
    credit_n = 0.5
    cs = loan.get("creditScoreNumeric")
    if cs is not None and cs == cs:
        credit_n = normalize(cs, *BOUNDS["credit_score"])
    income_n = normalize(loan.get("monthlyIncome"), *BOUNDS["monthly_income"])
    funding_n = normalize(loan.get("fundingRemaining"), *BOUNDS["funding_remaining"])
    amount_n = normalize(loan.get("loanAmount"), *BOUNDS["loan_amount"])
    raw = (
        rate_n * WEIGHTS["interest_rate"]
        + credit_n * WEIGHTS["credit_score"]
        + income_n * WEIGHTS["monthly_income"]
        + funding_n * WEIGHTS["funding_remaining"]
        + amount_n * WEIGHTS["loan_amount"]
    )
    return round(raw * 100, 2)


def get_priority(interest_rate: float | None) -> str:
    """VERY_HIGH / MEDIUM / LOW from rate thresholds (env-tunable)."""
    if interest_rate is None:
        return "LOW"
    high = float(os.environ.get("HIGH_PRIORITY_RATE_THRESHOLD", "70"))
    med = float(os.environ.get("MEDIUM_PRIORITY_RATE_THRESHOLD", "50"))
    if interest_rate >= high:
        return "VERY_HIGH"
    if interest_rate >= med:
        return "MEDIUM"
    return "LOW"


def credit_rank(loan: dict) -> float:
    """No-credit ranks ABOVE any real 300-900 score (favored, not penalized)."""
    s = loan.get("creditScoreNumeric")
    if s is None or s != s:
        return 1000.0
    return float(s)


def _num(v: object) -> float:
    """Numeric or -inf so nulls sort last within a key."""
    if v is None or (isinstance(v, float) and v != v):
        return float("-inf")
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("-inf")


def sort_loans(loans: list[dict]) -> list[dict]:
    """New list sorted by: rate desc, income desc, credit-rank desc, amount desc.

    Funding-remaining is deliberately NOT a sort key.
    """
    return sorted(
        loans,
        key=lambda ln: (
            _num(ln.get("interestRate")),
            _num(ln.get("monthlyIncome")),
            credit_rank(ln),
            _num(ln.get("loanAmount")),
        ),
        reverse=True,
    )

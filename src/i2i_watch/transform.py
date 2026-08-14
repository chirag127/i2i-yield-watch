"""Convert raw i2iFunding API rows (pl_*/bloan_*/usr_* fields) into the
normalized loan dict the dashboard, storage, and notifiers expect. Output shape
matches the legacy Node parser exactly so `dashboard/` needs no changes.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from .scorer import calculate_yield_score, get_priority
from .util import (
    bare,
    format_posted_on,
    inr,
    is_na,
    now_iso,
    parse_posted_on,
    to_number,
)

log = logging.getLogger("i2i_watch")


def combine_name(first: object, last: object) -> str | None:
    f = "" if is_na(first) else str(first).strip()
    ln = "" if is_na(last) else str(last).strip()
    out = " ".join(p for p in (f, ln) if p).strip()
    return out or None


def pick_rate(row: dict) -> float | None:
    for c in (row.get("pl_applicable_rate"), row.get("pl_current_rate"), row.get("pl_inital_rate")):
        n = to_number(c)
        if n is not None:
            return n
    return None


def pick_credit(row: dict) -> dict:
    """Borrower usr_cibil_score preferred, bloan fallback. '-1' => No History."""

    def try_value(v: object) -> dict | None:
        if is_na(v):
            return None
        if v in ("-1", -1):
            return {"text": "No History", "numeric": None}
        n = to_number(v)
        return {"text": (str(n) if n is not None else str(v)), "numeric": n}

    return (
        try_value(row.get("usr_cibil_score"))
        or try_value(row.get("bloan_cibil_score"))
        or {"text": None, "numeric": None}
    )


def format_tenure(value: object, type_: object) -> str | None:
    n = to_number(value)
    if n is None:
        return None
    t = (str(type_) if type_ else "d").lower()
    unit = "Days" if t == "d" else "Months" if t == "m" else "Years"
    num = int(n) if float(n).is_integer() else n
    return f"{num} {unit}"


def build_loan_url(borrower_ref: object, loan_id: object) -> str:
    if not borrower_ref:
        return ""
    parts = [
        "https://www.i2ifunding.com/borrower/listing",
        "public-profile",
        quote(str(borrower_ref), safe=""),
    ]
    if loan_id not in (None, ""):
        parts.append(quote(str(loan_id), safe=""))
    return "/".join(parts)


def compute_funding(row: dict) -> dict:
    total = to_number(row.get("pl_amt"))
    left = to_number(row.get("pl_amt_left"))
    if total is None or total <= 0:
        return {
            "loanAmount": None,
            "amountFunded": None,
            "amountLeft": None,
            "fundedPercent": None,
            "fundingRemaining": None,
            "isFullyFunded": False,
        }
    funded = None if left is None else max(total - left, 0)
    pct = None if funded is None else round((funded / total) * 100, 2)
    remaining = None if pct is None else round(100 - pct, 2)
    is_fully_funded = (
        (left is not None and left <= 0)
        or (pct is not None and pct >= 100)
        or row.get("pl_status") != 1
    )
    return {
        "loanAmount": total,
        "amountFunded": funded,
        "amountLeft": left,
        "fundedPercent": pct,
        "fundingRemaining": remaining,
        "isFullyFunded": is_fully_funded,
    }


def _pick_purpose(row: dict) -> str | None:
    for c in (row.get("bloan_desc"), row.get("bloan_other_perpose"), row.get("purpose")):
        if is_na(c):
            continue
        s = str(c).strip()
        if s:
            return s
    return None


def _str_or_none(v: object) -> str | None:
    return None if is_na(v) else str(v).strip()


def transform_loan(row: dict) -> dict:
    """One raw row -> normalized loan dict. Raises on missing id."""
    if not isinstance(row, dict):
        raise ValueError("transform_loan: row is not a dict")
    loan_id = row.get("pl_bloan_id") or row.get("pl_id")
    if loan_id is None:
        raise ValueError("transform_loan: row missing pl_bloan_id / pl_id")
    borrower_ref = row.get("pl_user_id") or None
    credit = pick_credit(row)
    age = to_number(row.get("usr_age"))
    risk = row.get("bloan_i2i_category")
    funding = compute_funding(row)

    base = {
        "loanId": str(loan_id),
        "borrowerRef": (str(borrower_ref) if borrower_ref is not None else None),
        "name": combine_name(row.get("usr_fname"), row.get("usr_lname")),
        "age": (int(age) if age is not None else None),
        "location": _str_or_none(row.get("location")),
        "residenceType": _str_or_none(row.get("residence_type")),
        "purpose": _pick_purpose(row),
        "creditScore": credit["text"],
        "creditScoreNumeric": credit["numeric"],
        "riskCategory": (None if is_na(risk) else str(risk).strip().upper()),
        "interestRate": pick_rate(row),
        "tenure": format_tenure(row.get("bloan_tenure"), row.get("tenure_type")),
        "product": _str_or_none(row.get("product_name")),
        "madeLiveOn": parse_posted_on(row.get("postedOn")),
        "employmentType": _str_or_none(row.get("emp_type")),
        "monthlyIncome": to_number(row.get("fin_monthly_income")),
        "professionName": _str_or_none(row.get("em_self_profession")),
        "businessName": _str_or_none(row.get("emp_comp_name")),
        "loanAmount": funding["loanAmount"],
        "amountFunded": funding["amountFunded"],
        "amountLeft": funding["amountLeft"],
        "fundedPercent": funding["fundedPercent"],
        "fundingRemaining": funding["fundingRemaining"],
        "isFullyFunded": funding["isFullyFunded"],
        "loanUrl": build_loan_url(borrower_ref, loan_id),
    }
    return {
        **base,
        "scrapedAt": now_iso(),
        "yieldScore": calculate_yield_score(base),
        "priority": get_priority(base["interestRate"]),
    }


def transform_loans(raw_rows: list[dict]) -> list[dict]:
    out = []
    for row in raw_rows:
        try:
            out.append(transform_loan(row))
        except Exception as e:  # noqa: BLE001
            log.warning("transform_loans: skipping bad row: %s", e)
    return out


def format_loan_block(loan: dict) -> list[str]:
    """Ordered, label-free display lines (most important first). Missing data
    drops the whole line. Only line 1 carries a '%'. Amounts use inr().
    """
    lines: list[str] = []

    # 1. Rate + Yield
    rate_pieces = []
    rate = loan.get("interestRate")
    if rate is not None and rate == rate:
        rate_pieces.append(f"🔥 {rate:.2f}% p.a.")
    ys = loan.get("yieldScore")
    if ys is not None and ys == ys:
        rate_pieces.append(f"Yield {ys:.2f}/100")
    if rate_pieces:
        lines.append(" · ".join(rate_pieces))

    # 2. Identity
    id_pieces = []
    if not is_na(loan.get("borrowerRef")):
        id_pieces.append(f"i2i-#{loan['borrowerRef']}")
    if not is_na(loan.get("loanId")):
        id_pieces.append(f"Loan {loan['loanId']}")
    if id_pieces:
        lines.append(" · ".join(id_pieces))

    # 3. Funding
    fund_pieces = []
    total_bare = bare(inr(loan.get("loanAmount")))
    if total_bare:
        fund_pieces.append(f"₹{total_bare}")
    funded = loan.get("amountFunded")
    if funded is None and loan.get("loanAmount") is not None and loan.get("amountLeft") is not None:
        funded = loan["loanAmount"] - loan["amountLeft"]
    funded_bare = bare(inr(funded))
    if funded_bare:
        fund_pieces.append(f"₹{funded_bare} funded")
    left_bare = bare(inr(loan.get("amountLeft")))
    if left_bare:
        fund_pieces.append(f"₹{left_bare} left")
    if fund_pieces:
        lines.append(" · ".join(fund_pieces))

    # 4. Credit + Risk
    credit_pieces = []
    if not is_na(loan.get("creditScore")):
        credit_pieces.append(f"Credit {loan['creditScore']}")
    else:
        credit_pieces.append("⚠ No credit score (ranked as 750)")
    if not is_na(loan.get("riskCategory")):
        credit_pieces.append(f"Risk {loan['riskCategory']}")
    if credit_pieces:
        lines.append(" · ".join(credit_pieces))

    # 5. Borrower
    borrower_pieces = []
    if not is_na(loan.get("name")):
        borrower_pieces.append(str(loan["name"]))
    age = loan.get("age")
    if age is not None and age == age:
        borrower_pieces.append(f"Age {int(age)}")
    if not is_na(loan.get("location")):
        borrower_pieces.append(str(loan["location"]))
    if borrower_pieces:
        lines.append(" · ".join(borrower_pieces))

    # 6. Employment
    emp_pieces = []
    if not is_na(loan.get("employmentType")):
        emp_pieces.append(str(loan["employmentType"]))
    if not is_na(loan.get("professionName")):
        emp_pieces.append(str(loan["professionName"]))
    elif not is_na(loan.get("businessName")):
        emp_pieces.append(str(loan["businessName"]))
    if emp_pieces:
        lines.append(" — ".join(emp_pieces))

    # 7. Income / Tenure / Residence
    rep_pieces = []
    income_bare = bare(inr(loan.get("monthlyIncome")))
    if income_bare:
        rep_pieces.append(f"₹{income_bare}/mo")
    if not is_na(loan.get("tenure")):
        rep_pieces.append(str(loan["tenure"]))
    if not is_na(loan.get("residenceType")):
        rep_pieces.append(str(loan["residenceType"]))
    if rep_pieces:
        lines.append(" · ".join(rep_pieces))

    # 8. Purpose
    if not is_na(loan.get("purpose")):
        lines.append(str(loan["purpose"]))

    # 9. Made live
    made_live = format_posted_on(loan.get("madeLiveOn"))
    if made_live:
        lines.append(f"Live {made_live}")

    # 10. URL (last, never omitted when present)
    if not is_na(loan.get("loanUrl")):
        lines.append(str(loan["loanUrl"]))

    return lines

"""PII-boundary canary.

The public getActiveFilteredBorrowers feed carries ~240 fields per row including
borrower PII (pan_card, aadhar_card, cibil_report, bank statements, ITR/Form16
docs, addresses). This repo is PUBLIC, so `transform_loan` and `invest.select`
must project that away. These tests fail if a future edit leaks a raw field into
the normalized loan or the invest candidate.
"""

from __future__ import annotations

import json

from i2i_watch.invest import select
from i2i_watch.transform import transform_loan, transform_loans

# Closed whitelists — the ONLY keys allowed out of each stage.
SAFE_TRANSFORM_KEYS = {
    "loanId", "borrowerRef", "name", "age", "location", "residenceType",
    "purpose", "creditScore", "creditScoreNumeric", "riskCategory",
    "interestRate", "tenure", "product", "madeLiveOn", "employmentType",
    "monthlyIncome", "professionName", "businessName", "loanAmount",
    "amountFunded", "amountLeft", "fundedPercent", "fundingRemaining",
    "isFullyFunded", "loanUrl", "scrapedAt", "yieldScore", "priority",
}

SAFE_SELECT_KEYS = {
    "loanId", "borrowerUserId", "rate", "score", "noCredit", "tenure", "amtLeft",
}


def _raw_row_with_pii() -> dict:
    return {
        "pl_bloan_id": 1471718, "pl_id": 123, "pl_user_id": 1459576,
        "pl_amt": "53355.00", "pl_amt_left": "1000.00", "pl_final_amt": "52355.00",
        "pl_status": 1, "pl_applicable_rate": "100.08", "pl_current_rate": "100.08",
        "pl_inital_rate": "100.08", "bloan_tenure": 15, "tenure_type": "m",
        "bloan_i2i_category": "D", "bloan_desc": "Stock", "product_name": "Regular Loans",
        "postedOn": "12-08-2026", "emp_type": "Business", "fin_monthly_income": "40000",
        "em_self_profession": "2500", "emp_comp_name": "Confectionery",
        "location": "Jyotiba Phule Nagar", "residence_type": "own_house",
        "usr_age": "30", "usr_fname": "Hemraj", "usr_lname": "Singh",
        "usr_cibil_score": "695", "bloan_cibil_score": "695",
        # PII the real feed carries — must never reach the output:
        "pan_card": "ABCDE1234F", "aadhar_card": "1234-5678-9012",
        "cibil_report": "https://x/cibil.pdf", "bank_statement": "https://x/stmt.pdf",
        "form16_current_year": "https://x/form16.pdf", "itr_current_year": "https://x/itr.pdf",
        "salary_slips_3": "https://x/slips.pdf", "address_proof": "https://x/addr.pdf",
        "credit_card_statements": "https://x/cc.pdf", "bank_name": "SBI",
        "account_type": "savings", "cheque_bounces": "0",
    }


def test_transform_output_keys_are_closed_whitelist():
    out = transform_loan(_raw_row_with_pii())
    assert set(out) == SAFE_TRANSFORM_KEYS


def test_transform_output_values_drop_pii():
    dumped = json.dumps(transform_loan(_raw_row_with_pii())).lower()
    for secret in ("ABCDE1234F", "1234-5678-9012", "cibil.pdf", "stmt.pdf",
                   "form16", "itr.pdf", "slips.pdf", "addr.pdf", "cc.pdf"):
        assert secret.lower() not in dumped, f"PII leaked into normalized loan: {secret}"


def test_select_output_is_closed_whitelist():
    sel = select([_raw_row_with_pii(), _raw_row_with_pii()], 100.0)
    assert len(sel) == 2  # 100.08 > 100
    for s in sel:
        assert set(s) == SAFE_SELECT_KEYS


def test_fixture_rows_transform_without_pii(raw_rows):
    out = transform_loans(raw_rows)
    assert out
    for ln in out:
        assert set(ln) == SAFE_TRANSFORM_KEYS

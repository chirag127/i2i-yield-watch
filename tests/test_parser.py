"""Parser: raw API rows -> normalized loan dict. Validates the data shape the
dashboard reads (loanId/borrowerRef/interestRate/loanUrl/... plus derived
yieldScore + priority + funding). Runs offline against a captured fixture.
"""

from i2i_watch.transform import transform_loan, transform_loans

# The exact key set the dashboard (dashboard/app.js) reads off each loan doc.
EXPECTED_KEYS = {
    "loanId", "borrowerRef", "name", "age", "location", "residenceType",
    "purpose", "creditScore", "creditScoreNumeric", "riskCategory",
    "interestRate", "tenure", "product", "madeLiveOn", "employmentType",
    "monthlyIncome", "professionName", "businessName", "loanAmount",
    "amountFunded", "amountLeft", "fundedPercent", "fundingRemaining",
    "isFullyFunded", "loanUrl", "scrapedAt", "yieldScore", "priority",
}


def test_transform_all_fixture_rows(raw_rows):
    loans = transform_loans(raw_rows)
    assert len(loans) == len(raw_rows)


def test_data_shape_matches_dashboard(raw_rows):
    loan = transform_loans(raw_rows)[0]
    assert EXPECTED_KEYS.issubset(loan.keys())


def test_loanid_from_pl_bloan_id(raw_rows):
    loan = transform_loan(raw_rows[0])
    assert loan["loanId"] == "500123"
    assert loan["borrowerRef"] == "88001"


def test_loan_url_public_profile_pattern(raw_rows):
    loan = transform_loan(raw_rows[0])
    assert loan["loanUrl"] == (
        "https://www.i2ifunding.com/borrower/listing/public-profile/88001/500123"
    )


def test_rate_and_derived_priority(raw_rows):
    loan = transform_loan(raw_rows[0])
    assert loan["interestRate"] == 88.5
    assert loan["priority"] == "VERY_HIGH"  # >= 70
    assert isinstance(loan["yieldScore"], float)


def test_no_history_credit_sentinel(raw_rows):
    # row index 1 (usr_cibil_score '-1') and index 4 (bloan_cibil_score '-1')
    loan1 = transform_loan(raw_rows[1])
    assert loan1["creditScore"] == "No History"
    assert loan1["creditScoreNumeric"] is None
    loan4 = transform_loan(raw_rows[4])
    assert loan4["creditScore"] == "No History"


def test_funding_computation(raw_rows):
    loan = transform_loan(raw_rows[0])  # 50000 total, 20000 left
    assert loan["loanAmount"] == 50000
    assert loan["amountFunded"] == 30000
    assert loan["fundedPercent"] == 60.0
    assert loan["fundingRemaining"] == 40.0
    assert loan["isFullyFunded"] is False


def test_fully_funded_when_no_amount_left(raw_rows):
    loan = transform_loan(raw_rows[3])  # pl_amt_left = 0
    assert loan["isFullyFunded"] is True


def test_dedup_by_loanid_across_rows(raw_rows):
    # duplicate the first row; transform + id-dedup should collapse to unique ids
    dup = raw_rows + [raw_rows[0]]
    loans = transform_loans(dup)
    ids = [ln["loanId"] for ln in loans]
    assert len(ids) == len(dup)  # transform keeps all; dedup is storage's job
    assert len(set(ids)) == len(raw_rows)  # but only N unique ids exist

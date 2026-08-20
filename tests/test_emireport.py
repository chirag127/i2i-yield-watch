"""EMI-status report tests: per-loan normalization, delay buckets, default
classification, aggregates + the paged emi_loans client path.

No network — the pure functions are tested with plain dicts and the client
paginator with a faked _post. The fixture mirrors the live response shapes
(loanDetailBorrowerWiseInDetail rows carry borrower name / loan id / amounts /
EMI status; getEMIStatusOverview + InDetail carry the aggregate numbers).
"""

from __future__ import annotations

import pytest

from i2i_watch.client import I2iClient
from i2i_watch.emireport import (
    build_aggregates,
    delay_bucket,
    is_defaulted,
    normalize_loan,
)


# ── delay buckets ────────────────────────────────────────────────────────────
def test_delay_bucket_boundaries():
    assert delay_bucket(None) == "unknown"
    assert delay_bucket(0) == "ontime"
    assert delay_bucket(5) == "lt30"
    assert delay_bucket(29) == "lt30"
    assert delay_bucket(30) == "30to90"
    assert delay_bucket(90) == "30to90"
    assert delay_bucket(91) == "gt90"


# ── normalize_loan ───────────────────────────────────────────────────────────
def test_normalize_loan_maps_common_field_names():
    row = {
        "borrowerName": "Rajesh Kumar",
        "loanId": 1446097,
        "totalInvestment": 5000.0,
        "totalReceived": 1500.0,
        "totalPriPen": 2500.0,
        "totalIntPen": 1000.0,
        "interestRate": 110.5,
        "riskCategory": "C",
        "tenure": 12,
        "lastPaymentReceivedDate": "2026-07-15",
        "currentStatus": "Delayed",
        "delayDays": 12,
    }
    ln = normalize_loan(row)
    assert ln["loanId"] == 1446097
    assert ln["borrowerName"] == "Rajesh Kumar"
    assert ln["amountInvested"] == pytest.approx(5000.0)
    assert ln["amountReceived"] == pytest.approx(1500.0)
    assert ln["principalPending"] == pytest.approx(2500.0)
    assert ln["interestPending"] == pytest.approx(1000.0)
    assert ln["totalPending"] == pytest.approx(3500.0)  # derived from split
    assert ln["currentStatus"] == "Delayed"
    assert ln["delayDays"] == pytest.approx(12.0)
    assert ln["raw"] is row  # original row is preserved


def test_normalize_loan_live_field_names():
    # LIVE row schema (probe 2026-08-20): totalAmountRec / totalAmountPen /
    # totalPrincipalPen / totalIntPen / loanInt / loanCat / loanDisbDate /
    # delayedDays / currentStatus / paidEMI / unpaidEMI / loanAmount
    row = {
        "name": "Mohd Anas",
        "loanId": 1363676,
        "loanAmount": 1000.0,
        "totalAmountRec": 798.91,
        "totalAmountPen": 400.14,
        "totalPrincipalPen": 233.25,
        "totalIntPen": 166.89,
        "loanInt": 76.4,
        "loanCat": "X",
        "loanDisbDate": 1777919400,
        "tenure": 5,
        "lastPaymentDate": "2026-08-17",
        "currentStatus": "Delayed",
        "delayedDays": 76,
        "paidEMI": 0,
        "unpaidEMI": 5,
        "emiAmount": 239.76,
    }
    ln = normalize_loan(row)
    assert ln["borrowerName"] == "Mohd Anas"
    assert ln["amountInvested"] == pytest.approx(1000.0)
    assert ln["amountReceived"] == pytest.approx(798.91)
    assert ln["principalPending"] == pytest.approx(233.25)
    assert ln["interestPending"] == pytest.approx(166.89)
    assert ln["totalPending"] == pytest.approx(400.14)  # direct field wins
    assert ln["rate"] == pytest.approx(76.4)
    assert ln["riskCategory"] == "X"
    assert ln["delayDays"] == pytest.approx(76.0)
    assert ln["emiPaid"] == pytest.approx(0.0)
    assert ln["emiPending"] == pytest.approx(5.0)
    assert is_defaulted(ln)
    assert delay_bucket(ln["delayDays"]) == "30to90"


def test_normalize_loan_total_pending_taken_directly_when_present():
    ln = normalize_loan({"loanId": 1, "totalAmountPending": 800.0})
    assert ln["totalPending"] == pytest.approx(800.0)


def test_normalize_loan_marks_delayed_without_day_count():
    # Delayed status but no delayDays field -> delayDays None (bucket 'unknown')
    ln = normalize_loan({"loanId": 2, "currentStatus": "Delayed"})
    assert ln["delayDays"] is None
    assert ln["totalPending"] == 0.0


# ── is_defaulted ─────────────────────────────────────────────────────────────
def test_is_defaulted_by_status():
    assert is_defaulted({"currentStatus": "Delayed", "delayDays": 0})
    assert is_defaulted({"currentStatus": "Overdue"})
    assert is_defaulted({"currentStatus": "Partial Paid"}) is False


def test_is_defaulted_by_days():
    assert is_defaulted({"currentStatus": "", "delayDays": 40})
    assert is_defaulted({"currentStatus": "", "delayDays": 0}) is False


def test_is_defaulted_closed_or_ontime_is_false():
    for st in ("Closed", "Fully Closed", "On Time", "Advanced", "Paid"):
        assert is_defaulted({"currentStatus": st, "delayDays": 0}) is False


def test_is_defaulted_by_money_when_no_status():
    # no status/days but received < invested and pending > 0 -> defaulted
    assert is_defaulted({"currentStatus": "", "amountInvested": 5000.0,
                         "amountReceived": 500.0, "totalPending": 4500.0})
    assert is_defaulted({"currentStatus": "", "amountInvested": 5000.0,
                         "amountReceived": 5000.0, "totalPending": 0.0}) is False


# ── build_aggregates ─────────────────────────────────────────────────────────
def test_aggregates_split_by_delay_bucket_and_sum_pending():
    loans = [
        normalize_loan({"loanId": 1, "currentStatus": "On Time", "delayDays": 0,
                        "totalInvestment": 5000.0, "totalReceived": 5000.0,
                        "totalPending": 0.0}),
        normalize_loan({"loanId": 2, "currentStatus": "Delayed", "delayDays": 12,
                        "totalInvestment": 5000.0, "totalReceived": 1000.0,
                        "totalPriPen": 2000.0, "totalIntPen": 2000.0}),
        normalize_loan({"loanId": 3, "currentStatus": "Delayed", "delayDays": 60,
                        "totalInvestment": 5000.0, "totalReceived": 0.0,
                        "totalPending": 5500.0}),
        normalize_loan({"loanId": 4, "currentStatus": "Delayed", "delayDays": 120,
                        "totalInvestment": 5000.0, "totalReceived": 0.0,
                        "totalPending": 6000.0}),
    ]
    agg = build_aggregates(loans)
    assert agg["totalLoans"] == 4
    assert agg["totalInvested"] == pytest.approx(20000.0)
    d = agg["delayedBuckets"]
    assert d["ontime"]["count"] == 1
    assert d["lt30"]["count"] == 1
    assert d["30to90"]["count"] == 1
    assert d["gt90"]["count"] == 1
    # defaulted = 3 (loan 1 is on-time): 4000 + 5500 + 6000
    assert agg["defaulted"]["count"] == 3
    assert agg["defaulted"]["totalPending"] == pytest.approx(15500.0)
    assert len(agg["defaulted"]["loans"]) == 3
    # sorted by pending desc -> loan 4 first
    assert agg["defaulted"]["loans"][0]["loanId"] == 4
    assert agg["recoveryRate"] == pytest.approx(6000.0 / 20000.0)


def test_aggregates_empty_input():
    agg = build_aggregates([])
    assert agg["totalLoans"] == 0
    assert agg["defaulted"]["count"] == 0
    assert agg["recoveryRate"] is None


def test_aggregates_unknown_bucket_for_delayed_without_days():
    loans = [normalize_loan({"loanId": 9, "currentStatus": "Delayed"})]
    agg = build_aggregates(loans)
    assert agg["delayedBuckets"]["unknown"]["count"] == 1
    assert agg["defaulted"]["count"] == 1  # status-derived default


# ── client: paged emi_loans ──────────────────────────────────────────────────
def _client_with_pages(pages: list[list[dict]], totals: list[int],
                       grand_total: int | None = None) -> I2iClient:
    # `totals` are the PER-PAGE `total` values; `grand_total` is the response's
    # totalRows (mirrors the live API: total=10 per page, totalRows=174 grand
    # total). Default: grand total = sum of all page rows.
    c = I2iClient("csrf", "sid")
    calls: list[dict] = []
    if grand_total is None:
        grand_total = sum(len(p) for p in pages)

    def _post(host: str, path: str, body: dict, **kw):
        calls.append(body)
        idx = len(calls) - 1
        return {"body": pages[idx], "total": totals[idx], "totalRows": grand_total}

    c._post = _post  # type: ignore[method-assign]
    c._calls = calls  # type: ignore[attr-defined]
    return c


def test_emi_loans_paginates_until_grand_total():
    # LIVE-VERIFIED: response `total` is the PER-PAGE count (10) while
    # `totalRows` is the grand total (174). Pagination MUST use totalRows —
    # using `total` capped the book at page 1 (~95% of loans dropped).
    p1 = [{"loanId": 1, "borrowerName": "A"}, {"loanId": 2, "borrowerName": "B"}]
    p2 = [{"loanId": 3, "borrowerName": "C"}, {"loanId": 4, "borrowerName": "D"}]
    p3 = [{"loanId": 5, "borrowerName": "E"}]
    c = _client_with_pages([p1, p2, p3], [2, 2, 1], grand_total=5)
    rows = c.emi_loans(limit=2)
    assert [r["loanId"] for r in rows] == [1, 2, 3, 4, 5]
    assert c._calls[0]["skip"] == 0
    assert c._calls[1]["skip"] == 2
    assert c._calls[2]["skip"] == 4


def test_emi_loans_stops_at_grand_total_even_with_full_pages():
    # totalRows = 4 but every page is full (10) — must stop at 4 rows, not
    # keep fetching (the old `total` bug would have stopped at 10 = one page)
    p1 = [{"loanId": 1}, {"loanId": 2}, {"loanId": 3}, {"loanId": 4}]
    c = _client_with_pages([p1], [10], grand_total=4)
    rows = c.emi_loans(limit=10)
    assert [r["loanId"] for r in rows] == [1, 2, 3, 4]


def test_emi_loans_dedupes_across_pages():
    p1 = [{"loanId": 1}, {"loanId": 2}]
    p2 = [{"loanId": 2}, {"loanId": 3}]
    c = _client_with_pages([p1, p2], [3, 3], grand_total=3)
    rows = c.emi_loans(limit=2)
    assert [r["loanId"] for r in rows] == [1, 2, 3]


def test_emi_loans_stops_on_empty_page():
    c = _client_with_pages([[], []], [0, 0])
    assert c.emi_loans() == []


def test_emi_url_appends_auth_to_query_path():
    # EMI endpoints carry their own ?isFilterApply= — auth must append with '&'
    c = I2iClient("csrf123", "sid456")
    url = c._url("https://apiv1.i2ifunding.com",
                 "investor/getEMIStatusOverview?isFilterApply=")
    assert url == ("https://apiv1.i2ifunding.com/investor/getEMIStatusOverview"
                   "?isFilterApply=&csrf_token=csrf123&session_id=sid456")
    # plain paths keep the original behavior
    url2 = c._url("https://apiv1.i2ifunding.com", "investor/walletAndFund")
    assert url2 == ("https://apiv1.i2ifunding.com/investor/walletAndFund"
                    "?csrf_token=csrf123&session_id=sid456")

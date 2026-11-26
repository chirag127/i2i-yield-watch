"""EMI-status report tests: per-loan normalization, delay buckets, default
classification, aggregates + the paged emi_loans client path.

No network — the pure functions are tested with plain dicts and the client
paginator with a faked _post. The fixture mirrors the live response shapes
(loanDetailBorrowerWiseInDetail rows carry borrower name / loan id / amounts /
EMI status; getEMIStatusOverview + InDetail carry the aggregate numbers).
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from i2i_watch import emireport
from i2i_watch.client import I2iClient
from i2i_watch.emireport import (
    build_aggregates,
    build_report,
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
    assert ln["totalPending"] is None


@pytest.mark.parametrize("value", [None, "", "unavailable", "NaN", "Infinity", True])
def test_normalize_unknown_numbers_are_not_zero(value):
    ln = normalize_loan({"loanId": 1, "totalInvestment": value,
                         "totalReceived": value, "totalAmountPen": value,
                         "delayedDays": value})
    for key in ("amountInvested", "amountReceived", "totalPending", "delayDays"):
        assert ln[key] is None
    assert delay_bucket(ln["delayDays"]) == "unknown"


def test_normalize_preserves_explicit_zero_pending_over_splits():
    ln = normalize_loan({"loanId": 1, "totalAmountPen": 0,
                         "totalPrincipalPen": 100, "totalIntPen": 20})
    assert ln["totalPending"] == 0


@pytest.mark.parametrize("row", [
    {"totalPrincipalPen": 100},
    {"totalIntPen": 20},
    {"totalAmountPen": "invalid", "totalPrincipalPen": 100, "totalIntPen": 20},
])
def test_normalize_does_not_guess_pending_from_incomplete_or_invalid_amounts(row):
    assert normalize_loan(row)["totalPending"] is None


@pytest.mark.parametrize("status", ["Delayed", "Overdue"])
def test_zero_delay_with_overdue_status_is_unknown(status):
    ln = normalize_loan({"currentStatus": status, "delayedDays": 0})
    assert delay_bucket(ln["delayDays"]) == "unknown"


@pytest.mark.parametrize("days", [-1, float("nan"), float("inf")])
def test_invalid_delay_is_unknown(days):
    assert delay_bucket(days) == "unknown"


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


def test_pending_money_alone_is_not_evidence_of_default():
    assert is_defaulted({"currentStatus": "", "amountInvested": 5000.0,
                         "amountReceived": 500.0, "totalPending": 4500.0}) is False
    assert is_defaulted({"currentStatus": "", "amountInvested": 5000.0,
                         "amountReceived": 5000.0, "totalPending": 0.0}) is False


def test_not_due_status_is_not_defaulted():
    assert is_defaulted({"currentStatus": "Not Due"}) is False


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


def test_missing_amounts_propagate_to_totals_instead_of_partial_sums():
    loans = [
        normalize_loan({"loanId": 1, "totalInvestment": 100,
                        "totalReceived": 20, "totalAmountPen": 80,
                        "totalPrincipalPen": 70, "totalIntPen": 10,
                        "currentStatus": "Delayed"}),
        normalize_loan({"loanId": 2, "currentStatus": "Delayed"}),
    ]
    agg = build_aggregates(loans)
    for key in ("totalInvested", "totalReceived", "totalPending",
                "principalPending", "interestPending", "recoveryRate"):
        assert agg[key] is None
    assert agg["delayedBuckets"]["unknown"] == {"count": 2, "pending": None}
    assert agg["defaulted"]["totalPending"] is None
    assert agg["delayedBuckets"]["ontime"] == {"count": 0, "pending": 0}


def _report_client(rows, status=None):
    client = Mock(spec=I2iClient)
    client.emi_loans.return_value = rows
    client.emi_status_overview.return_value = {"body": {}}
    client.emi_status_detail.return_value = {"body": {}}
    client.emi_loan_status.return_value = {"body": status or {}}
    client.delayed_loan_status.return_value = {}
    return client


def test_report_propagates_failed_loan_collection():
    client = _report_client([])
    client.emi_loans.side_effect = RuntimeError("incomplete loan collection")
    with pytest.raises(RuntimeError, match="incomplete loan collection"):
        build_report(client, "synthetic")


@pytest.mark.parametrize("rows", [None, {}, {"body": []}, [None], [{}]])
def test_report_rejects_malformed_loan_collection(rows):
    with pytest.raises(ValueError, match="loan"):
        build_report(_report_client(rows), "synthetic")


def test_report_accepts_confirmed_empty_book():
    report = build_report(_report_client([]), "synthetic")
    assert report["loans"] == []
    assert report["aggregates"]["totalLoans"] == 0
    assert report["aggregates"]["totalPending"] == 0


@pytest.mark.parametrize("delay_key", ["delayedDays", "delayDays", "daysDelayed",
                                      "noOfDaysDelayed", "overdueDays",
                                      "daysOverdue", "pendingDays"])
@pytest.mark.parametrize("status_key", ["currentStatus", "loanStatus", "emiStatus", "status"])
def test_report_enrichment_normalizes_aliases(delay_key, status_key):
    row = {"loanId": 1, "currentStatus": "Partial Paid"}
    client = _report_client([row], {delay_key: "45", status_key: "Overdue"})
    report = build_report(client, "synthetic")
    loan = report["loans"][0]
    assert loan["delayDays"] == 45.0
    assert loan["currentStatus"] == "Overdue"
    assert loan["raw"] == row
    assert report["aggregates"]["delayedBuckets"]["30to90"]["count"] == 1


@pytest.mark.parametrize("status", [{}, {"currentStatus": "Delayed"},
                                  {"delayedDays": "unknown"}])
def test_report_enrichment_never_turns_unknown_delay_into_ontime(status):
    client = _report_client([{"loanId": 1, "currentStatus": "Delayed"}], status)
    report = build_report(client, "synthetic")
    assert report["loans"][0]["delayDays"] is None
    assert report["aggregates"]["delayedBuckets"]["unknown"]["count"] == 1
    assert report["aggregates"]["defaulted"]["count"] == 1


def test_report_failed_enrichment_keeps_unknown_delay():
    client = _report_client([{"loanId": 1, "currentStatus": "Delayed"}])
    client.emi_loan_status.side_effect = RuntimeError("unavailable")
    report = build_report(client, "synthetic")
    assert report["loans"][0]["delayDays"] is None


def test_run_failed_book_preserves_existing_report(tmp_path, monkeypatch, capsys):
    client = _report_client([])
    client.emi_loans.side_effect = RuntimeError("incomplete loan collection")
    monkeypatch.setattr(I2iClient, "from_env", lambda account: client)
    target = tmp_path / "report.json"
    target.write_text('{"previous": true}', encoding="utf-8")
    assert emireport.run("synthetic", str(target)) == 1
    assert target.read_text(encoding="utf-8") == '{"previous": true}'
    assert not target.with_suffix(".json.tmp").exists()
    assert "failed" in capsys.readouterr().out.lower()


def test_run_prints_unknown_amounts_without_inventing_zero(tmp_path, monkeypatch, capsys):
    client = _report_client([{"loanId": 1, "currentStatus": "Delayed"}])
    monkeypatch.setattr(I2iClient, "from_env", lambda account: client)
    target = tmp_path / "report.json"
    assert emireport.run("synthetic", str(target)) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["aggregates"]["totalPending"] is None
    output = capsys.readouterr().out
    assert "unknown" in output
    assert "Rs 0" not in output


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

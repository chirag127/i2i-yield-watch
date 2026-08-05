"""Scorer: yield-score neutrality + strict multi-key sort ordering.

Sort priority: rate desc > income desc > credit-rank desc (no-credit ranks HIGH)
> amount desc. Mirrors the Node compareLoans/sortLoans exactly.
"""

from i2i_watch.scorer import calculate_yield_score, credit_rank, get_priority, sort_loans


def test_no_credit_is_neutral_not_penalized():
    with_credit = {"interestRate": 50, "creditScoreNumeric": 750}
    no_credit = {"interestRate": 50, "creditScoreNumeric": None}
    # neutral (0.5) credit -> lower than a high real score, but never a penalty
    assert calculate_yield_score(no_credit) < calculate_yield_score(with_credit)
    # no-credit rank is above the max real score (900)
    assert credit_rank(no_credit) == 1000.0
    assert credit_rank(with_credit) == 750.0


def test_higher_rate_scores_higher():
    lo = {"interestRate": 20}
    hi = {"interestRate": 90}
    assert calculate_yield_score(hi) > calculate_yield_score(lo)


def test_priority_thresholds():
    assert get_priority(90) == "VERY_HIGH"
    assert get_priority(60) == "MEDIUM"
    assert get_priority(20) == "LOW"
    assert get_priority(None) == "LOW"


def test_sort_rate_is_primary_key():
    loans = [
        {"loanId": "a", "interestRate": 40, "monthlyIncome": 999, "loanAmount": 999},
        {"loanId": "b", "interestRate": 88, "monthlyIncome": 1, "loanAmount": 1},
    ]
    assert [ln["loanId"] for ln in sort_loans(loans)] == ["b", "a"]


def test_sort_tiebreak_income_then_credit_then_amount():
    # equal rate -> income desc wins first
    same_rate = 88
    loans = [
        {"loanId": "lowInc", "interestRate": same_rate, "monthlyIncome": 40000,
         "creditScoreNumeric": 800, "loanAmount": 100000},
        {"loanId": "hiInc", "interestRate": same_rate, "monthlyIncome": 90000,
         "creditScoreNumeric": 300, "loanAmount": 1},
    ]
    assert [ln["loanId"] for ln in sort_loans(loans)] == ["hiInc", "lowInc"]

    # equal rate + income -> credit-rank desc, no-credit ranks HIGH
    loans2 = [
        {"loanId": "realCredit", "interestRate": same_rate, "monthlyIncome": 50000,
         "creditScoreNumeric": 850, "loanAmount": 5},
        {"loanId": "noCredit", "interestRate": same_rate, "monthlyIncome": 50000,
         "creditScoreNumeric": None, "loanAmount": 5},
    ]
    assert [ln["loanId"] for ln in sort_loans(loans2)] == ["noCredit", "realCredit"]

    # equal rate + income + credit -> amount desc
    loans3 = [
        {"loanId": "small", "interestRate": same_rate, "monthlyIncome": 50000,
         "creditScoreNumeric": 700, "loanAmount": 10000},
        {"loanId": "big", "interestRate": same_rate, "monthlyIncome": 50000,
         "creditScoreNumeric": 700, "loanAmount": 500000},
    ]
    assert [ln["loanId"] for ln in sort_loans(loans3)] == ["big", "small"]


def test_sort_does_not_mutate_input():
    loans = [{"loanId": "a", "interestRate": 1}, {"loanId": "b", "interestRate": 9}]
    original = list(loans)
    sort_loans(loans)
    assert loans == original

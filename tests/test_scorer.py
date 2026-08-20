"""Scorer: yield-score + strict multi-key sort ordering.

Ranking importance: rate desc > credit desc (no-credit imputed 720) > tenure desc
> income desc > amount desc. Rate/return is always the top factor.
"""

from i2i_watch.scorer import (
    calculate_yield_score,
    has_no_credit,
    imputed_credit,
    get_priority,
    sort_loans,
)


def test_no_credit_imputed_as_720():
    with_credit = {"interestRate": 50, "creditScoreNumeric": 800}
    no_credit = {"interestRate": 50, "creditScoreNumeric": None}
    # no-credit is ranked AS IF the score were 720 (passes the 720 gate, but is
    # high-risk/uncertain vs a real 750+ score — never treated as a 0)
    assert imputed_credit(no_credit) == 720.0
    assert imputed_credit(with_credit) == 800.0
    assert has_no_credit(no_credit) is True
    assert has_no_credit(with_credit) is False
    # a real 800 outscores the imputed-720 no-credit loan at equal rate
    assert calculate_yield_score(no_credit) < calculate_yield_score(with_credit)
    # a real 720 also outscores it (high risk — real 750+ always wins)
    worse_credit = {"interestRate": 50, "creditScoreNumeric": 720}
    assert calculate_yield_score(no_credit) <= calculate_yield_score(worse_credit)


def test_higher_rate_scores_higher():
    lo = {"interestRate": 20}
    hi = {"interestRate": 90}
    assert calculate_yield_score(hi) > calculate_yield_score(lo)


def test_longer_tenure_scores_higher_at_equal_rate():
    short = {"interestRate": 50, "creditScoreNumeric": 700, "tenure": "3 Months"}
    long = {"interestRate": 50, "creditScoreNumeric": 700, "tenure": "24 Months"}
    assert calculate_yield_score(long) > calculate_yield_score(short)


def test_priority_thresholds():
    assert get_priority(90) == "VERY_HIGH"
    assert get_priority(60) == "MEDIUM"
    assert get_priority(20) == "LOW"
    assert get_priority(None) == "LOW"


def test_sort_rate_is_primary_key():
    loans = [
        {"loanId": "a", "interestRate": 40, "creditScoreNumeric": 900, "monthlyIncome": 999},
        {"loanId": "b", "interestRate": 88, "creditScoreNumeric": 300, "monthlyIncome": 1},
    ]
    assert [ln["loanId"] for ln in sort_loans(loans)] == ["b", "a"]


def test_sort_tiebreak_credit_then_tenure_then_income_then_amount():
    same = 88
    # equal rate -> higher credit wins (credit is 2nd key now)
    loans = [
        {"loanId": "hiCredit", "interestRate": same, "creditScoreNumeric": 850,
         "monthlyIncome": 1, "loanAmount": 1},
        {"loanId": "loCredit", "interestRate": same, "creditScoreNumeric": 600,
         "monthlyIncome": 99999, "loanAmount": 99999},
    ]
    assert [ln["loanId"] for ln in sort_loans(loans)] == ["hiCredit", "loCredit"]

    # equal rate: real 850 > (no-credit=720 imputed ties real 720; stable order)
    loans2 = [
        {"loanId": "real850", "interestRate": same, "creditScoreNumeric": 850},
        {"loanId": "noCredit", "interestRate": same, "creditScoreNumeric": None},
        {"loanId": "real720", "interestRate": same, "creditScoreNumeric": 720},
    ]
    assert [ln["loanId"] for ln in sort_loans(loans2)] == ["real850", "noCredit", "real720"]

    # equal rate + credit -> longer tenure wins
    loans3 = [
        {"loanId": "shortT", "interestRate": same, "creditScoreNumeric": 700, "tenure": "3 Months"},
        {"loanId": "longT", "interestRate": same, "creditScoreNumeric": 700, "tenure": "24 Months"},
    ]
    assert [ln["loanId"] for ln in sort_loans(loans3)] == ["longT", "shortT"]

    # equal rate + credit + tenure -> income desc, then amount desc
    loans4 = [
        {"loanId": "small", "interestRate": same, "creditScoreNumeric": 700,
         "tenure": "6 Months", "monthlyIncome": 50000, "loanAmount": 10000},
        {"loanId": "big", "interestRate": same, "creditScoreNumeric": 700,
         "tenure": "6 Months", "monthlyIncome": 50000, "loanAmount": 500000},
    ]
    assert [ln["loanId"] for ln in sort_loans(loans4)] == ["big", "small"]


def test_sort_does_not_mutate_input():
    loans = [{"loanId": "a", "interestRate": 1}, {"loanId": "b", "interestRate": 9}]
    original = list(loans)
    sort_loans(loans)
    assert loans == original

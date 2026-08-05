"""loanId dedup + new-loan detection (pure storage helpers, no Firestore)."""

from i2i_watch.storage import detect_new_loans, detect_fully_funded, filter_unnotified


def test_detect_new_loans_excludes_notified_and_existing():
    fresh = [{"loanId": "1"}, {"loanId": "2"}, {"loanId": "3"}]
    existing = [{"loanId": "2"}]
    notified = {"3"}
    new = detect_new_loans(fresh, existing, notified)
    assert [ln["loanId"] for ln in new] == ["1"]


def test_detect_new_loans_accepts_list_or_set_notified():
    fresh = [{"loanId": "10"}, {"loanId": "11"}]
    assert [ln["loanId"] for ln in detect_new_loans(fresh, [], ["10"])] == ["11"]
    assert [ln["loanId"] for ln in detect_new_loans(fresh, [], {"10"})] == ["11"]


def test_filter_unnotified_dedups_by_loanid():
    loans = [{"loanId": "1"}, {"loanId": "2"}, {"loanId": "3"}]
    assert [ln["loanId"] for ln in filter_unnotified(loans, {"2"})] == ["1", "3"]


def test_loanid_dedup_is_string_normalized():
    # int vs str loanId must be treated as the same id
    fresh = [{"loanId": 5}]
    assert detect_new_loans(fresh, [], {"5"}) == []
    assert detect_new_loans(fresh, [{"loanId": "5"}], set()) == []


def test_detect_fully_funded_disappeared_and_funded():
    fresh = [{"loanId": "1", "isFullyFunded": True}, {"loanId": "2", "isFullyFunded": False}]
    existing = [{"loanId": "9"}]  # 9 disappeared from the listing
    out = detect_fully_funded(fresh, existing)
    reasons = {ln["loanId"]: ln["archivedReason"] for ln in out}
    assert reasons["9"] == "disappeared_from_listing"
    assert reasons["1"] == "fully_funded"
    assert "2" not in reasons

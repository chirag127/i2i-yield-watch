"""Storage JSON (git-as-DB) backend works without firebase_admin importable."""

import builtins
import json

import pytest

import i2i_watch.storage as storage


@pytest.fixture
def json_backend(tmp_path, monkeypatch):
    """Force JSON mode: block firebase_admin import + redirect data dir to tmp."""
    real_import = builtins.__import__

    def no_firebase(name, *args, **kwargs):
        if name == "firebase_admin" or name.startswith("firebase_admin."):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_firebase)
    monkeypatch.setattr(storage, "_data_dir", lambda: tmp_path)
    # reset module state so init() re-picks the backend
    monkeypatch.setattr(storage, "_mode", None)
    monkeypatch.setattr(storage, "_app", None)
    monkeypatch.setattr(storage, "_db", None)
    return tmp_path


def test_init_falls_back_to_json_without_firebase(json_backend):
    storage.init()
    assert storage._mode == "json"


def test_save_and_load_active_loans_roundtrip(json_backend):
    loans = [
        {"loanId": "1", "interestRate": 50, "product": "P", "priority": "VERY_HIGH"},
        {"loanId": "2", "interestRate": 20, "product": "Q", "priority": "LOW"},
    ]
    storage.save_active_loans(loans)
    active = storage.load_active_loans()
    assert {ln["loanId"] for ln in active} == {"1", "2"}
    assert (json_backend / "active-loans.json").exists()


def test_notifications_sent_roundtrip(json_backend):
    assert storage.load_notifications_sent() == set()
    storage.mark_notifications_sent(["1", "2", 3])
    assert storage.load_notifications_sent() == {"1", "2", "3"}
    storage.mark_notifications_sent(["2", "4"])
    assert storage.load_notifications_sent() == {"1", "2", "3", "4"}


def test_update_stats_writes_json(json_backend):
    storage.update_stats(
        [{"loanId": "1", "interestRate": 60, "yieldScore": 40, "priority": "VERY_HIGH"}],
        newly_archived=2,
    )
    stats = json.loads((json_backend / "stats.json").read_text(encoding="utf-8"))
    assert stats["currentActive"] == 1
    assert stats["totalArchived"] == 2
    assert stats["highPriorityCount"] == 1


def test_archive_fully_funded_writes_month_and_index(json_backend):
    n = storage.archive_fully_funded_loans(
        [{"loanId": "9", "interestRate": 30, "archivedReason": "fully_funded"}]
    )
    assert n == 1
    files = list((json_backend / "archive").glob("*.json"))
    assert any(f.name != "index.json" for f in files)
    idx = json.loads((json_backend / "archive" / "index.json").read_text(encoding="utf-8"))
    assert idx["files"][0]["count"] == 1
    # idempotent: same loan not re-archived
    assert storage.archive_fully_funded_loans(
        [{"loanId": "9", "interestRate": 30}]
    ) == 0


def test_append_changelog_appends(json_backend):
    storage.append_changelog({"runId": "r1", "loansFound": 3})
    storage.append_changelog({"runId": "r2", "loansFound": 5})
    runs = json.loads((json_backend / "runs.json").read_text(encoding="utf-8"))
    assert [r["runId"] for r in runs] == ["r1", "r2"]


def test_detect_helpers_pure_without_backend():
    # no init() — pure functions never touch a backend
    fresh = [{"loanId": "1"}, {"loanId": "2", "isFullyFunded": True}]
    existing = [{"loanId": "9"}]
    assert [ln["loanId"] for ln in storage.detect_new_loans(fresh, [], {"1"})] == ["2"]
    reasons = {ln["loanId"]: ln["archivedReason"] for ln in storage.detect_fully_funded(fresh, existing)}
    assert reasons["9"] == "disappeared_from_listing"
    assert reasons["2"] == "fully_funded"

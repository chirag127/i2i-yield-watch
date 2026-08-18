"""Tests for the multi-account registry: env resolution, gates, storage names."""

from __future__ import annotations

import pytest

from i2i_watch import accounts


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("I2I_ACCOUNT", "I2I_ACCOUNTS",
              "I2I_EMAIL", "I2I_PASSWORD",
              "I2I_NEERU_EMAIL", "I2I_NEERU_PASSWORD",
              "I2I_NEERU_AUTOINVEST_MIN_RATE_PCT"):
        monkeypatch.delenv(k, raising=False)


def test_default_account_is_chirag():
    assert accounts.account_names() == ["chirag"]
    assert accounts.active_account() == "chirag"
    assert accounts.default_account() == "chirag"


def test_accounts_list_from_env(monkeypatch):
    monkeypatch.setenv("I2I_ACCOUNTS", "chirag,neeru")
    assert accounts.account_names() == ["chirag", "neeru"]


def test_active_account_from_env(monkeypatch):
    monkeypatch.setenv("I2I_ACCOUNTS", "chirag,neeru")
    monkeypatch.setenv("I2I_ACCOUNT", "neeru")
    assert accounts.active_account() == "neeru"


def test_env_key_default_account_legacy_names():
    # default account keeps legacy unprefixed names so existing .env works
    assert accounts.env_key("chirag", "EMAIL") == "I2I_EMAIL"
    assert accounts.env_key("chirag", "PASSWORD") == "I2I_PASSWORD"
    assert accounts.env_key("chirag", "TXN_PIN") == "I2I_TXN_PIN"
    assert accounts.env_key("chirag", "AUTOINVEST_MIN_RATE_PCT") == "AUTOINVEST_MIN_RATE_PCT"


def test_env_key_secondary_account_namespaced(monkeypatch):
    monkeypatch.setenv("I2I_ACCOUNTS", "chirag,neeru")
    assert accounts.env_key("neeru", "EMAIL") == "I2I_NEERU_EMAIL"
    assert accounts.env_key("neeru", "PASSWORD") == "I2I_NEERU_PASSWORD"
    assert accounts.env_key("neeru", "TXN_PIN") == "I2I_NEERU_TXN_PIN"
    assert accounts.env_key("neeru", "AUTOINVEST_MIN_RATE_PCT") == "I2I_NEERU_AUTOINVEST_MIN_RATE_PCT"


def test_get_float_per_account_gate(monkeypatch):
    monkeypatch.setenv("I2I_ACCOUNTS", "chirag,neeru")
    monkeypatch.setenv("I2I_NEERU_AUTOINVEST_MIN_RATE_PCT", "150")
    assert accounts.get_float("neeru", "AUTOINVEST_MIN_RATE_PCT", 100.0) == 150.0
    # chirag unaffected
    assert accounts.get_float("chirag", "AUTOINVEST_MIN_RATE_PCT", 100.0) == 100.0


def test_get_float_falls_back_to_default_account(monkeypatch):
    monkeypatch.setenv("I2I_ACCOUNTS", "chirag,neeru")
    # neeru has no gate set -> uses the default account's value
    assert accounts.get_float("neeru", "AUTOINVEST_MIN_RATE_PCT", 100.0) == 100.0


def test_storage_name_isolation(monkeypatch):
    monkeypatch.setenv("I2I_ACCOUNTS", "chirag,neeru")
    assert accounts.storage_name("chirag") == ""           # legacy path
    assert accounts.storage_name("neeru") == "-neeru"      # per-account file


def test_invested_loans_per_account_isolation(monkeypatch, tmp_path):
    """record_invested writes per-account files; dedup never crosses accounts."""
    import json

    from i2i_watch import storage

    monkeypatch.setenv("I2I_ACCOUNTS", "chirag,neeru")
    # point storage at the tmp data dir
    monkeypatch.setattr(storage, "_repo_root", lambda: tmp_path)
    (tmp_path / "data").mkdir()

    storage.record_invested([1, 2], account="chirag")
    storage.record_invested([2, 3], account="neeru")

    chirag_file = tmp_path / "data" / "invested-loans.json"
    neeru_file = tmp_path / "data" / "invested-loans-neeru.json"
    assert json.loads(chirag_file.read_text()) == [1, 2]
    assert json.loads(neeru_file.read_text()) == [2, 3]
    assert storage.load_invested(account="chirag") == [1, 2]
    assert storage.load_invested(account="neeru") == [2, 3]


def test_client_from_env_uses_account_namespaced_creds(monkeypatch):
    """from_env('neeru') reads I2I_NEERU_EMAIL/PASSWORD, never I2I_EMAIL."""
    from unittest.mock import patch

    from i2i_watch.client import I2iClient

    monkeypatch.setenv("I2I_ACCOUNTS", "chirag,neeru")
    for k in ("I2I_EMAIL", "I2I_PASSWORD", "I2I_CSRF_TOKEN", "I2I_SESSION_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("I2I_NEERU_EMAIL", "neeru@x.com")
    monkeypatch.setenv("I2I_NEERU_PASSWORD", "pw-neeru")
    monkeypatch.setenv("I2I_NEERU_CSRF_TOKEN", "c")
    monkeypatch.setenv("I2I_NEERU_SESSION_ID", "s")
    with patch("i2i_watch.client.login", return_value=("nc", "ns")) as m:
        c = I2iClient.from_env("neeru")
    m.assert_called_once_with("neeru@x.com", "pw-neeru")
    assert (c.csrf, c.sid) == ("nc", "ns")

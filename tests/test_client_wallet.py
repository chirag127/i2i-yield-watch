"""Wallet balance tests: investable = availableWallet − committed funds.

Locks the live-observed bug: walletAndFund returned availableWallet=₹50,000
but only ~₹21k was actually investable (the platform defines Available
Balance as Current − Funds Under Proposal/Disbursal). The plan overshot the
real escrow and ADD BALANCE fired early. No network — _get is faked.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from i2i_watch import storage
from i2i_watch.client import I2iClient
from i2i_watch.invest import show_wallet


def _client(resp: object, funds_resp: object | None = None) -> I2iClient:
    c = I2iClient("csrf", "sid")
    calls = []

    def _get(host: str, path: str, **kw):
        calls.append((host, path))
        if path == "investor/availableFunds":
            if funds_resp is not None:
                return funds_resp
            raise ConnectionError("no funds endpoint (test)")
        return resp

    c._get = _get  # type: ignore[method-assign]
    c._calls = calls  # type: ignore[attr-defined]
    return c


def test_wallet_subtracts_funds_under_proposal_and_disbursal():
    # live shape: availableWallet 50000, but ~28.6k committed -> ~21.4k real
    c = _client({"data": {
        "availableWallet": 50000.0,
        "fundUnderProposal": 26440.0,
        "disbursalPending": 1560.0,
    }})
    assert c.wallet() == pytest.approx(22000.0)


def test_wallet_clamps_at_zero_when_committed_exceeds_wallet():
    c = _client({"data": {
        "availableWallet": 5000.0,
        "fundUnderProposal": 8000.0,
        "disbursalPending": 0.0,
    }})
    assert c.wallet() == pytest.approx(0.0)


def test_wallet_returns_wallet_when_nothing_committed():
    c = _client({"data": {"availableWallet": 12500.0}})
    assert c.wallet() == pytest.approx(12500.0)


def test_wallet_prefers_explicit_escrow_field():
    # an explicit escrow/investable field wins over the subtraction
    c = _client({"data": {
        "availableEscrow": 999.0,
        "availableWallet": 50000.0,
        "fundUnderProposal": 40000.0,
    }})
    assert c.wallet() == pytest.approx(999.0)


def test_wallet_falls_back_to_availableFunds_endpoint():
    c = _client({"data": {"noWalletHere": 1}},
                funds_resp={"body": {"fundAvailable": 777.0}})
    assert c.wallet() == pytest.approx(777.0)


def test_wallet_zero_when_everything_fails():
    c = _client({"data": {"noWalletHere": 1}})
    assert c.wallet() == pytest.approx(0.0)


def test_wallet_prefers_fresh_escrow_truth(monkeypatch, tmp_path):
    """The platform'sOWN rejection figure (Rs 1,093) wins over the phantom
    availableWallet=50,000 — the live-scenario lock."""
    monkeypatch.setattr(storage, "_data_dir", lambda: tmp_path)
    storage.save_escrow_truth(1093.0, "chirag")
    c = _client({"data": {
        "availableWallet": 50000.0,
        "fundUnderProposal": 0.0,
        "disbursalPending": 0.0,
    }})
    c._account = "chirag"
    assert c.wallet() == pytest.approx(1093.0)


def test_wallet_falls_back_when_truth_stale(monkeypatch, tmp_path):
    """A stale truth (older than ESCROW_TRUTH_TTL_HOURS) must NOT block a
    top-up windfall — fall back to the API estimate until a new rejection."""
    monkeypatch.setattr(storage, "_data_dir", lambda: tmp_path)
    storage.save_escrow_truth(1093.0, "chirag")
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    (tmp_path / "escrow-truth.json").write_text(json.dumps(
        {"amount": 1093.0, "observedAt": old}), encoding="utf-8")
    c = _client({"data": {
        "availableWallet": 50000.0,
        "fundUnderProposal": 0.0,
        "disbursalPending": 0.0,
    }})
    c._account = "chirag"
    assert c.wallet() == pytest.approx(50000.0)


def test_escrow_truth_is_per_account(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "_data_dir", lambda: tmp_path)
    storage.save_escrow_truth(1000.0, "chirag")
    storage.save_escrow_truth(2000.0, "neeru")
    assert (tmp_path / "escrow-truth.json").exists()
    assert (tmp_path / "escrow-truth-neeru.json").exists()
    assert storage.load_escrow_truth("chirag")["amount"] == pytest.approx(1000.0)
    assert storage.load_escrow_truth("neeru")["amount"] == pytest.approx(2000.0)
    assert storage.load_escrow_truth("unknown-acct") is None


def test_show_wallet_prints_real_investable(monkeypatch, capsys):
    # show_wallet uses the SAME wallet() the plan sizes against, so the CLI
    # reflects the corrected (subtracted) balance, not the raw availableWallet
    monkeypatch.setenv("I2I_ACCOUNT", "chirag")
    monkeypatch.setenv("I2I_EMAIL", "a@b.com")
    monkeypatch.setenv("I2I_PASSWORD", "pw")

    class _Fake:
        @classmethod
        def from_env(cls, account=None):
            c = I2iClient("c", "s")
            c.wallet = lambda: 22000.0  # type: ignore[method-assign]
            return c

    monkeypatch.setattr("i2i_watch.invest.I2iClient", _Fake)
    assert show_wallet("chirag") == 0
    assert "investable escrow = Rs 22,000.00" in capsys.readouterr().out


def test_lending_overview_parses_common_field_names():
    c = _client({"data": {
        "totalAmountLent": 661140.0,
        "totalNoBorrowers": 166,
        "averageInterestRate": 110.03,
        "interestReceived": 6042.52,
        "principalReceived": 233317.12,
        "totalAmountPending": 508223.82,
        "expectedTotalInterestIncome": 86443.46,
    }})
    ov = c._overview_amounts(c.lending_overview())
    assert ov["totalLent"] == pytest.approx(661140.0)
    assert ov["borrowers"] == pytest.approx(166.0)
    assert ov["interestReceived"] == pytest.approx(6042.52)
    assert ov["totalPending"] == pytest.approx(508223.82)
    assert ov["avgRate"] == pytest.approx(110.03)


def test_lending_overview_parses_live_investor_overview_fields():
    # LIVE-verified field set (2026-08-20): investor/overview returns
    # avgROI / totalAmountInvested / totalEMI / totalInterestIncome /
    # totalNumOfBorrowers / xirr — the digest must map these too.
    c = _client({"data": {
        "avgROI": 18.4,
        "totalAmountInvested": 661140.0,
        "totalEMI": 181540.88,
        "totalInterestIncome": 86443.46,
        "totalNumOfBorrowers": 166,
        "xirr": 21.1,
    }})
    ov = c._overview_amounts(c.lending_overview())
    assert ov["totalLent"] == pytest.approx(661140.0)
    assert ov["borrowers"] == pytest.approx(166.0)
    assert ov["interestReceived"] == pytest.approx(86443.46)
    assert ov["avgRate"] == pytest.approx(18.4)


def test_lending_overview_empty_when_no_endpoint_responds():
    c = I2iClient("csrf", "sid")

    def _get(host, path, **kw):
        raise ConnectionError("no overview endpoint (test)")

    c._get = _get  # type: ignore[method-assign]
    assert c.lending_overview() == {}
    assert c._overview_amounts({}) == {
        "totalLent": 0.0, "interestReceived": 0.0, "principalReceived": 0.0,
        "totalPending": 0.0, "interestPending": 0.0, "borrowers": 0.0,
        "avgRate": 0.0, "expectedInterest": 0.0,
    }

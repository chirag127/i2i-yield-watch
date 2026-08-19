"""Wallet balance tests: investable = availableWallet − committed funds.

Locks the live-observed bug: walletAndFund returned availableWallet=₹50,000
but only ~₹21k was actually investable (the platform defines Available
Balance as Current − Funds Under Proposal/Disbursal). The plan overshot the
real escrow and ADD BALANCE fired early. No network — _get is faked.
"""

from __future__ import annotations

import pytest

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

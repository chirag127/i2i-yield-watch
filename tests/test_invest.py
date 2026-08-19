"""Unit tests for the auto-investor's pure logic (no browser/network)."""

from __future__ import annotations

import base64
import hashlib

from unittest.mock import patch

from i2i_watch import config as C
from i2i_watch import invest as INV
from i2i_watch.auth import encrypt_password
from i2i_watch.invest import (
    build_invest_payload,
    emi,
    exclude_invested,
    is_loan_maxed,
    is_low_balance,
    parse_amount,
    parse_max_amount,
    select,
    size_amount,
)


def _decrypt(b64: str, passphrase: str) -> bytes:
    from Crypto.Cipher import AES

    raw = base64.b64decode(b64)
    assert raw[:8] == b"Salted__"
    salt, ct = raw[8:16], raw[16:]
    d, prev = b"", b""
    while len(d) < 48:
        prev = hashlib.md5(prev + passphrase.encode() + salt).digest()
        d += prev
    key, iv = d[:32], d[32:48]
    pt = AES.new(key, AES.MODE_CBC, iv).decrypt(ct)
    return pt[: -pt[-1]]


def test_emi_matches_spa():
    assert emi(1000, 46.66, 7) == 165.92


def test_encrypt_password_cryptojs_format_roundtrips():
    enc = encrypt_password("hello123")
    assert base64.b64decode(enc)[:8] == b"Salted__"
    assert _decrypt(enc, "kXyb3gzU") == b"hello123"


def test_encrypt_passphrase_decrypts_real_login_blob():
    # real captured usr_password blob -> printable 9-char password (proves passphrase)
    pt = _decrypt("U2FsdGVkX181ALpuYsiab+9yIfYClzB90b8/qw8omsw=", "kXyb3gzU")
    assert all(32 <= c < 127 for c in pt) and len(pt) == 9


def test_select_filters_and_ranks():
    rows = [
        {"pl_bloan_id": 1, "pl_applicable_rate": "46.66", "bloan_cibil_score": 700, "pl_amt_left": "5000"},
        {"pl_bloan_id": 2, "pl_applicable_rate": "46.66", "bloan_cibil_score": 780, "pl_amt_left": "5000"},
        {"pl_bloan_id": 3, "pl_applicable_rate": "38.0", "bloan_cibil_score": 900, "pl_amt_left": "5000"},
        {"pl_bloan_id": 4, "pl_applicable_rate": "50.0", "bloan_cibil_score": 600, "pl_amt_left": "5000"},
    ]
    # min_score=0: this test is about RATE filtering + ranking, not the credit gate
    assert [s["loanId"] for s in select(rows, 40.0, min_score=0)] == [4, 2, 1]


def test_select_credit_gate_filters_below_600():
    # real scores below the 600 gate are never invested; 600 and above pass
    rows = [
        {"pl_bloan_id": 1, "pl_applicable_rate": "120.0", "bloan_cibil_score": 599, "pl_amt_left": "5000"},
        {"pl_bloan_id": 2, "pl_applicable_rate": "120.0", "bloan_cibil_score": 600, "pl_amt_left": "5000"},
        {"pl_bloan_id": 3, "pl_applicable_rate": "120.0", "bloan_cibil_score": 750, "pl_amt_left": "5000"},
        {"pl_bloan_id": 4, "pl_applicable_rate": "120.0", "bloan_cibil_score": 800, "pl_amt_left": "5000"},
    ]
    assert [s["loanId"] for s in select(rows, 110.0)] == [4, 3, 2]  # 599 dropped


def test_select_credit_gate_no_credit_imputed_750_passes():
    # a loan with NO credit score is imputed 750, which MEETS the 600 gate -> kept
    rows = [
        {"pl_bloan_id": 1, "pl_applicable_rate": "120.0", "bloan_cibil_score": None, "pl_amt_left": "5000"},
        {"pl_bloan_id": 2, "pl_applicable_rate": "120.0", "bloan_cibil_score": "", "pl_amt_left": "5000"},
        {"pl_bloan_id": 3, "pl_applicable_rate": "120.0", "bloan_cibil_score": 0, "pl_amt_left": "5000"},
    ]
    sel = select(rows, 110.0)
    assert [s["loanId"] for s in sel] == [1, 2, 3]
    assert all(s["noCredit"] is True and s["score"] == 750.0 for s in sel)


def test_select_credit_gate_configurable():
    # the gate is a parameter, so it is tunable (e.g. per-account via
    # AUTOINVEST_MIN_CREDIT_SCORE read in invest.run())
    rows = [
        {"pl_bloan_id": 1, "pl_applicable_rate": "120.0", "bloan_cibil_score": 750, "pl_amt_left": "5000"},
        {"pl_bloan_id": 2, "pl_applicable_rate": "120.0", "bloan_cibil_score": 800, "pl_amt_left": "5000"},
    ]
    assert [s["loanId"] for s in select(rows, 110.0, min_score=800)] == [2]
    assert [s["loanId"] for s in select(rows, 110.0, min_score=750)] == [2, 1]


def test_select_strictly_above_gate():
    rows = [{"pl_bloan_id": 1, "pl_applicable_rate": "40.0", "bloan_cibil_score": 700, "pl_amt_left": "5000"}]
    assert select(rows, 40.0) == []  # 40 is NOT > 40


def test_autoinvest_gate_default_is_110():
    # lock the real-money threshold: place only on loans STRICTLY > 110%
    assert C.AUTOINVEST_MIN_RATE_PCT == 110.0


def test_select_gate_110_keeps_only_above_110():
    # >110% qualifies; exactly 110% and below do not (strict >)
    rows = [
        {"pl_bloan_id": 1, "pl_applicable_rate": "110.08", "bloan_cibil_score": 800, "pl_amt_left": "5000"},
        {"pl_bloan_id": 2, "pl_applicable_rate": "110.0", "bloan_cibil_score": 800, "pl_amt_left": "5000"},
        {"pl_bloan_id": 3, "pl_applicable_rate": "46.66", "bloan_cibil_score": 900, "pl_amt_left": "5000"},
    ]
    assert [s["loanId"] for s in select(rows, 110.0, min_score=0)] == [1]


def test_select_no_credit_imputed_750():
    # equal rate: real 800 > no-credit(=750) > real 700
    rows = [
        {"pl_bloan_id": 1, "pl_applicable_rate": "46.0", "bloan_cibil_score": 700, "pl_amt_left": "5000"},
        {"pl_bloan_id": 2, "pl_applicable_rate": "46.0", "bloan_cibil_score": None, "pl_amt_left": "5000"},
        {"pl_bloan_id": 3, "pl_applicable_rate": "46.0", "bloan_cibil_score": 800, "pl_amt_left": "5000"},
    ]
    # min_score=0: this test is about the 750 IMPUTATION ranking, not the gate
    sel = select(rows, 40.0, min_score=0)
    assert [s["loanId"] for s in sel] == [3, 2, 1]
    no_credit = next(s for s in sel if s["loanId"] == 2)
    assert no_credit["noCredit"] is True and no_credit["score"] == 750.0


def test_select_tenure_breaks_rate_and_credit_tie():
    # equal rate + equal credit -> longer tenure wins
    rows = [
        {"pl_bloan_id": 1, "pl_applicable_rate": "46.0", "bloan_cibil_score": 700,
         "bloan_tenure": "3 Months", "pl_amt_left": "5000"},
        {"pl_bloan_id": 2, "pl_applicable_rate": "46.0", "bloan_cibil_score": 700,
         "bloan_tenure": "24 Months", "pl_amt_left": "5000"},
    ]
    assert [s["loanId"] for s in select(rows, 40.0, min_score=0)] == [2, 1]


def test_size_caps_and_floors():
    assert size_amount(9000, 3200, 1000, 5000, 1) == 3200
    assert size_amount(500, 5000, 1000, 5000, 1) == 0
    assert size_amount(9000, 9000, 1000, 5000, 1) == 5000   # PER_LOAN_CAP 5000
    assert size_amount(9000, 3333, 1000, 5000, 100) == 3300  # multiple flooring


def test_sizing_anchors_cap_5000_floor_1000():
    # the sizing rule's two money anchors
    assert C.PER_LOAN_CAP == 5000.0
    assert C.INVEST_MIN_AMOUNT == 1000.0


def test_size_lends_remaining_or_caps_at_5000():
    # remaining > 5000 -> 5000; else lend the full remaining; < 1000 -> skip
    assert size_amount(7000, 25000, 1000, 5000, 1) == 5000
    assert size_amount(3000, 25000, 1000, 5000, 1) == 3000
    assert size_amount(1000, 25000, 1000, 5000, 1) == 1000
    assert size_amount(999, 25000, 1000, 5000, 1) == 0


def test_build_invest_payload_replicates_har_fields():
    detail = {
        "pl_bloan_id": 1439214, "bloan_tenure": 7, "pl_current_rate": "46.66",
        "bname": "Shivani ", "bloan_i2i_category": "X", "purpose": "Beauty Kit",
    }
    p = build_invest_payload(detail, 1000, 46.66)
    assert set(p) == {
        "loanId", "amount", "principalProtectionId", "monthlyEMI", "intRate",
        "tenure", "borrowerName", "riskCategory", "revisedEMI", "loanPurpose",
        "borrowerEmail", "transactionPin",
    }
    assert p["monthlyEMI"] == 165.92 and p["intRate"] == "46.66" and p["tenure"] == 7
    assert p["transactionPin"] is None  # filled only at placement


def test_investorNow_no_retry_on_timeout_prevents_double_spend():
    """CRITICAL money-safety: a timeout on the non-idempotent investorNow call must
    NOT fall back to a browser re-POST — the order may already be placed upstream."""
    from unittest.mock import patch
    from i2i_watch.client import I2iClient
    c = I2iClient.__new__(I2iClient)
    c._force_browser = False
    c._url = lambda h, p: "http://x/" + p
    called = {"browser": False}
    c._browser_post = lambda *a, **k: called.__setitem__("browser", True) or {}
    import urllib.error
    with patch("urllib.request.urlopen", side_effect=TimeoutError("boom")):
        try:
            c._post("h", "investor/investorNow/", {"a": 1}, no_retry=True)
            assert False, "should have raised, not retried"
        except TimeoutError:
            pass
    assert called["browser"] is False  # never re-POSTed the money call


# ── money-loop orchestration (fake client, no network) ───────────────────────


class _Reject(Exception):
    """Simulates an i2i HTTPError carrying a rejection body (client attaches .i2i_body)."""

    def __init__(self, body: str):
        self.i2i_body = body
        super().__init__(body)


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.invest_calls: list[dict] = []

    def loan_detail(self, uid, lid):
        return {
            "pl_bloan_id": lid, "pl_user_id": uid, "bloan_tenure": 6,
            "pl_current_rate": "100.08", "bname": "x", "bloan_i2i_category": "X",
            "purpose": "x", "min_invest_loan_amount": 1000,
            "max_invest_loan_amount": 5000, "invest_multiple_value": 1,
        }

    def principal_protection(self, *a):
        return {}

    def invest(self, payload):
        self.invest_calls.append(dict(payload))
        if not self._responses:
            raise AssertionError("unexpected invest call")
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


class _NoAuthClient:
    @classmethod
    def from_env(cls, account=None):
        raise SystemExit("no auth (test)")


def _sel():
    return [{"loanId": 123, "borrowerUserId": 999, "rate": 100.08,
             "score": 750.0, "noCredit": True, "tenure": 6.0, "amtLeft": 5000.0}]


def test_exclude_invested_filters_ids():
    sel = [{"loanId": 1}, {"loanId": 2}, {"loanId": 3}]
    assert [s["loanId"] for s in exclude_invested(sel, {2})] == [1, 3]
    assert exclude_invested(sel, set()) == sel


def test_parse_amount_and_max_amount():
    assert parse_amount("Available Balance is Rs. 3000.00") == 3000.0
    assert parse_max_amount("you can invest maximum up to ₹2000.00") == 2000.0
    assert parse_max_amount("already invested ₹3000 in this loan") == 0.0


def test_message_classifiers():
    assert is_loan_maxed("you can invest maximum up to ₹2000") is True
    assert is_loan_maxed("already invested") is True
    assert is_low_balance("Available Balance is Rs 100") is True
    assert is_low_balance("you can invest maximum up to ₹2000") is False


def test_plan_dedups_within_run():
    plan = INV._plan(_FakeClient([]), _sel() + _sel(), 5000.0)
    assert len(plan) == 1 and plan[0]["amount"] == 5000


def test_place_retries_reduced_on_low_balance(monkeypatch):
    c = _FakeClient([_Reject("Available Balance is Rs. 3000.00"),
                     {"data": "Invested Successfully", "message": "Fund added successfully."}])
    monkeypatch.setenv("I2I_TXN_PIN", "1234")
    with patch.object(INV.storage, "record_invested"), patch.object(INV, "send_telegram_text"):
        assert INV._place(c, [], _sel(), 5000.0, 100.0, True) == 0
    assert [x["amount"] for x in c.invest_calls] == [5000, 3000]


def test_place_retries_reduced_on_maxed(monkeypatch):
    c = _FakeClient([{"message": "you can invest maximum up to ₹2000.00"},
                     {"data": "Invested Successfully", "message": "Fund added successfully."}])
    monkeypatch.setenv("I2I_TXN_PIN", "1234")
    with patch.object(INV.storage, "record_invested"), patch.object(INV, "send_telegram_text"):
        assert INV._place(c, [], _sel(), 5000.0, 100.0, True) == 0
    assert [x["amount"] for x in c.invest_calls] == [5000, 2000]


def test_place_skips_maxed_below_min(monkeypatch):
    c = _FakeClient([{"message": "you can invest maximum up to ₹500.00"}])
    monkeypatch.setenv("I2I_TXN_PIN", "1234")
    with patch.object(INV.storage, "record_invested"), patch.object(INV, "send_telegram_text"):
        assert INV._place(c, [], _sel(), 5000.0, 100.0, True) == 0
    assert len(c.invest_calls) == 1  # below the 1000 floor -> skip, no retry


def test_place_success_telegram_failure_returns_0(monkeypatch):
    c = _FakeClient([{"data": "Invested Successfully", "message": "Fund added successfully."}])
    monkeypatch.setenv("I2I_TXN_PIN", "1234")
    with patch.object(INV.storage, "record_invested"), \
         patch.object(INV, "send_telegram_text", side_effect=RuntimeError("tg down")):
        assert INV._place(c, [], _sel(), 5000.0, 100.0, True) == 0


def test_run_excludes_invested(monkeypatch, capsys):
    import i2i_watch.sources.i2i as src

    rows = [
        {"pl_bloan_id": 1, "pl_user_id": 9, "pl_applicable_rate": "110.08",
         "bloan_cibil_score": 800, "pl_amt_left": "5000", "bloan_tenure": 6},
        {"pl_bloan_id": 2, "pl_user_id": 9, "pl_applicable_rate": "110.08",
         "bloan_cibil_score": 800, "pl_amt_left": "5000", "bloan_tenure": 6},
    ]
    monkeypatch.setattr(src, "fetch_all_loans", lambda: rows)
    monkeypatch.setattr(INV.storage, "load_invested", lambda **kw: [1])
    monkeypatch.setattr(INV, "I2iClient", _NoAuthClient)
    assert INV.run(live=False) == 0
    assert "1 loans >110%" in capsys.readouterr().out

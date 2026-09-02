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
    credit_near_misses,
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


def test_select_credit_gate_filters_below_500():
    # real scores below the 500 gate are never invested; 500 and above pass
    rows = [
        {"pl_bloan_id": 1, "pl_applicable_rate": "120.0", "bloan_cibil_score": 499, "pl_amt_left": "5000"},
        {"pl_bloan_id": 2, "pl_applicable_rate": "120.0", "bloan_cibil_score": 500, "pl_amt_left": "5000"},
        {"pl_bloan_id": 3, "pl_applicable_rate": "120.0", "bloan_cibil_score": 750, "pl_amt_left": "5000"},
        {"pl_bloan_id": 4, "pl_applicable_rate": "120.0", "bloan_cibil_score": 800, "pl_amt_left": "5000"},
    ]
    assert [s["loanId"] for s in select(rows, 100.0)] == [4, 3, 2]  # 499 dropped


def test_select_credit_gate_no_credit_imputed_720_passes():
    # a loan with NO credit score is imputed NO_CREDIT_IMPUTED_SCORE (720), which
    # MEETS the 500 gate -> kept, but ranks below any real 750+ score
    rows = [
        {"pl_bloan_id": 1, "pl_applicable_rate": "120.0", "bloan_cibil_score": None, "pl_amt_left": "5000"},
        {"pl_bloan_id": 2, "pl_applicable_rate": "120.0", "bloan_cibil_score": "", "pl_amt_left": "5000"},
        {"pl_bloan_id": 3, "pl_applicable_rate": "120.0", "bloan_cibil_score": 0, "pl_amt_left": "5000"},
    ]
    sel = select(rows, 100.0)
    assert [s["loanId"] for s in sel] == [1, 2, 3]
    assert all(s["noCredit"] is True and s["score"] == 720.0 for s in sel)


def test_select_credit_gate_configurable():
    # the gate is a parameter, so it is tunable (e.g. per-account via
    # AUTOINVEST_MIN_CREDIT_SCORE read in invest.run())
    rows = [
        {"pl_bloan_id": 1, "pl_applicable_rate": "120.0", "bloan_cibil_score": 750, "pl_amt_left": "5000"},
        {"pl_bloan_id": 2, "pl_applicable_rate": "120.0", "bloan_cibil_score": 800, "pl_amt_left": "5000"},
    ]
    assert [s["loanId"] for s in select(rows, 100.0, min_score=800)] == [2]
    assert [s["loanId"] for s in select(rows, 100.0, min_score=750)] == [2, 1]


def test_select_strictly_above_gate():
    rows = [{"pl_bloan_id": 1, "pl_applicable_rate": "40.0", "bloan_cibil_score": 700, "pl_amt_left": "5000"}]
    assert select(rows, 40.0) == []  # 40 is NOT > 40


def test_autoinvest_gate_defaults():
    # lock the real-money thresholds: strictly >100% rate and >=500 credit
    assert C.AUTOINVEST_MIN_RATE_PCT == 100.0
    assert C.AUTOINVEST_MIN_CREDIT_SCORE == 500.0


def test_select_gate_100_keeps_only_above_100():
    # >100% qualifies; exactly 100% and below do not (strict >)
    rows = [
        {"pl_bloan_id": 1, "pl_applicable_rate": "100.08", "bloan_cibil_score": 800, "pl_amt_left": "5000"},
        {"pl_bloan_id": 2, "pl_applicable_rate": "100.0", "bloan_cibil_score": 800, "pl_amt_left": "5000"},
        {"pl_bloan_id": 3, "pl_applicable_rate": "46.66", "bloan_cibil_score": 900, "pl_amt_left": "5000"},
    ]
    assert [s["loanId"] for s in select(rows, 100.0, min_score=0)] == [1]


def test_select_no_credit_imputed_720():
    # equal rate: real 800 > real 720 >= no-credit(=720 imputed high risk)
    rows = [
        {"pl_bloan_id": 1, "pl_applicable_rate": "46.0", "bloan_cibil_score": 720, "pl_amt_left": "5000"},
        {"pl_bloan_id": 2, "pl_applicable_rate": "46.0", "bloan_cibil_score": None, "pl_amt_left": "5000"},
        {"pl_bloan_id": 3, "pl_applicable_rate": "46.0", "bloan_cibil_score": 800, "pl_amt_left": "5000"},
    ]
    # min_score=0: this test is about the 720 IMPUTATION ranking, not the gate
    sel = select(rows, 40.0, min_score=0)
    assert [s["loanId"] for s in sel] == [3, 1, 2]
    no_credit = next(s for s in sel if s["loanId"] == 2)
    assert no_credit["noCredit"] is True and no_credit["score"] == 720.0


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
             "score": 720.0, "noCredit": True, "tenure": 6.0, "amtLeft": 5000.0}]


def test_cancel_returns_1_when_live_cancel_fails(monkeypatch):
    """A live cancel that does not fully complete must return non-zero, or the
    workflow reports success (green run) while money stays invested.

    live-observed 2026-09-02: cancel of 1486852 hit a TimeoutError + missing
    playwright; cancel.py logged the error but returned 0 -> completed/success
    with 0 cancelled. This test locks the fix."""
    from i2i_watch.cancel import run as cancel_run

    class _FailClient:
        @classmethod
        def from_env(cls, account=None):
            return cls()

        def cancel(self, lid, pin):
            raise RuntimeError("simulated timeout + no fallback")

    monkeypatch.setattr("i2i_watch.cancel.I2iClient", _FailClient)
    monkeypatch.setenv("I2I_TXN_PIN", "1234")
    monkeypatch.setattr("i2i_watch.cancel.send_telegram_text", lambda *a, **k: True)
    assert cancel_run([1486852], live=True, account="chirag") == 1


def test_exclude_invested_filters_ids():
    sel = [{"loanId": 1}, {"loanId": 2}, {"loanId": 3}]
    assert [s["loanId"] for s in exclude_invested(sel, {2})] == [1, 3]
    assert exclude_invested(sel, set()) == sel


def test_parse_amount_and_max_amount():
    assert parse_amount("Available Balance is Rs. 3000.00") == 3000.0
    assert parse_max_amount("you can invest maximum up to ₹2000.00") == 2000.0
    assert parse_max_amount("already invested ₹3000 in this loan") == 0.0


def test_parse_amount_live_low_escrow_rejection():
    # LIVE 2026-09-02: walletAndFund said availableWallet=50000 but the real
    # investable escrow (from i2i's own rejection) was Rs 1,093.00.
    body = ("You can invest in this loan, only if you have sufficient balance in "
            "your Escrow Account. Available Balance in your Escrow Account for "
            "investment is Rs. 1093.00.")
    assert is_low_balance(body) is True
    assert parse_amount(body) == 1093.0


def test_place_persists_escrow_truth_on_low_balance(monkeypatch, tmp_path):
    """A low-balance rejection must persist the platform's REAL escrow figure,
    overriding the phantom availableWallet so wallet() trusts it next run."""
    monkeypatch.setattr(INV.storage, "_data_dir", lambda: tmp_path)
    c = _FakeClient([_Reject("Available Balance ... for investment is Rs. 1093.00"),
                     {"data": "Invested Successfully", "message": "Fund added successfully."}])
    monkeypatch.setenv("I2I_TXN_PIN", "1234")
    with patch.object(INV.storage, "record_invested"), patch.object(INV, "send_telegram_text"):
        assert INV._place(c, [], _sel(), 5000.0, 100.0, True, account="chirag") == 0
    truth = INV.storage.load_escrow_truth("chirag")
    assert truth and truth["amount"] == 1093.0


def test_place_persists_zero_truth_when_drained(monkeypatch, tmp_path):
    """A REAL 'Rs 0.00' rejection is truth too — escrow drained — and must be
    persisted so wallet-check stops trusting a stale availableWallet."""
    monkeypatch.setattr(INV.storage, "_data_dir", lambda: tmp_path)
    c = _FakeClient([_Reject("Available Balance in your Escrow Account for investment is Rs. 0.00")])
    monkeypatch.setenv("I2I_NEERU_TXN_PIN", "1234")
    with patch.object(INV.storage, "record_invested"), patch.object(INV, "send_telegram_text"):
        assert INV._place(c, [], _sel(), 5000.0, 100.0, True, account="neeru") == 0
    truth = INV.storage.load_escrow_truth("neeru")
    assert truth and truth["amount"] == 0.0


def test_message_classifiers():
    assert is_loan_maxed("you can invest maximum up to ₹2000") is True
    assert is_loan_maxed("already invested") is True
    assert is_low_balance("Available Balance is Rs 100") is True
    assert is_low_balance("you can invest maximum up to ₹2000") is False


def test_plan_dedups_within_run():
    plan = INV._plan(_FakeClient([]), _sel() + _sel(), 5000.0)
    assert len(plan) == 1 and plan[0]["amount"] == 5000


def test_place_retries_reduced_on_low_balance(monkeypatch, tmp_path):
    monkeypatch.setattr(INV.storage, "_data_dir", lambda: tmp_path)  # truth persist stays isolated
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
         "usr_cibil_score": 800, "pl_amt_left": "5000", "bloan_tenure": 6},
        {"pl_bloan_id": 2, "pl_user_id": 9, "pl_applicable_rate": "110.08",
         "usr_cibil_score": 800, "pl_amt_left": "5000", "bloan_tenure": 6},
    ]
    monkeypatch.setattr(src, "fetch_all_loans", lambda: rows)
    monkeypatch.setattr(INV.storage, "load_invested", lambda **kw: [1])
    monkeypatch.setattr(INV, "I2iClient", _NoAuthClient)
    assert INV.run(live=False) == 0
    assert "1 loans >100%" in capsys.readouterr().out


# ── near-miss visibility + idle watchdog + config/digest ─────────────────────


def test_credit_near_misses_flags_rate_ok_credit_low():
    rows = [
        # qualifies (rate > 100 AND credit >= 700) -> NOT a near-miss
        {"pl_bloan_id": 1, "pl_applicable_rate": "120.0", "usr_cibil_score": 800, "pl_amt_left": "5000"},
        # near-miss: rate ok, real credit too low
        {"pl_bloan_id": 2, "pl_applicable_rate": "118.0", "usr_cibil_score": 650, "pl_amt_left": "5000"},
        # no credit (imputed 720 >= 700 passes) -> NOT a near-miss
        {"pl_bloan_id": 3, "pl_applicable_rate": "116.0", "usr_cibil_score": None, "pl_amt_left": "5000"},
        # below rate gate -> not a near-miss either
        {"pl_bloan_id": 4, "pl_applicable_rate": "100.0", "usr_cibil_score": 500, "pl_amt_left": "5000"},
    ]
    misses = credit_near_misses(rows, 100.0, 720.0)
    # only loan 2: rate 118 > 100 but credit 650 < 720. Loan 3 imputed 720 >= 720
    # so it QUALIFIES (not a near-miss); loan 1 qualifies; loan 4 below rate gate.
    assert [m["loanId"] for m in misses] == [2]
    assert misses[0]["rate"] == 118.0 and misses[0]["score"] == 650.0


def test_credit_near_misses_sorted_by_rate_desc():
    rows = [
        {"pl_bloan_id": 1, "pl_applicable_rate": "115.0", "usr_cibil_score": 600, "pl_amt_left": "5000"},
        {"pl_bloan_id": 2, "pl_applicable_rate": "125.0", "usr_cibil_score": 500, "pl_amt_left": "5000"},
    ]
    assert [m["loanId"] for m in credit_near_misses(rows, 100.0, 720.0)] == [2, 1]


def test_select_reads_usr_cibil_score_with_bloan_fallback():
    # live feed carries the score in usr_cibil_score; bloan is the fallback
    rows = [
        {"pl_bloan_id": 1, "pl_applicable_rate": "120.0", "usr_cibil_score": 800, "pl_amt_left": "5000"},
        {"pl_bloan_id": 2, "pl_applicable_rate": "120.0", "bloan_cibil_score": 750, "pl_amt_left": "5000"},
        {"pl_bloan_id": 3, "pl_applicable_rate": "120.0", "usr_cibil_score": "-1", "pl_amt_left": "5000"},
        {"pl_bloan_id": 4, "pl_applicable_rate": "120.0", "usr_cibil_score": 650, "pl_amt_left": "5000"},
    ]
    sel = select(rows, 100.0, 720.0)
    # 1 (800), 2 (750 fallback), 3 (no-credit imputed 720) qualify; 4 (650) dropped at this explicit 720 threshold
    assert [s["loanId"] for s in sel] == [1, 2, 3]
    assert next(s for s in sel if s["loanId"] == 3)["noCredit"] is True


def test_idle_watchdog_nudges_after_threshold(monkeypatch):
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat().replace("+00:00", "Z")
    sent = []

    def fake_save(state):
        return None

    monkeypatch.setattr(INV.storage, "load_idle_state", lambda: {"lastQualifiedAt": old})
    monkeypatch.setattr(INV.storage, "save_idle_state", fake_save)
    monkeypatch.setattr(INV, "send_telegram_text", lambda text, silent=False: sent.append(text) or True)
    INV._watchdog_idle("chirag", 720.0)
    assert sent and "market idle" in sent[0]


def test_idle_watchdog_silent_before_threshold(monkeypatch):
    from datetime import datetime, timezone

    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    sent = []
    monkeypatch.setattr(INV.storage, "load_idle_state", lambda: {"lastQualifiedAt": fresh})
    monkeypatch.setattr(INV.storage, "save_idle_state", lambda state: None)
    monkeypatch.setattr(INV, "send_telegram_text", lambda text, silent=False: sent.append(text) or True)
    INV._watchdog_idle("chirag", 720.0)
    assert sent == []


def test_idle_watchdog_loud_when_configured(monkeypatch):
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat().replace("+00:00", "Z")
    flags = []
    monkeypatch.setattr(INV.storage, "load_idle_state", lambda: {"lastQualifiedAt": old})
    monkeypatch.setattr(INV.storage, "save_idle_state", lambda state: None)
    monkeypatch.setattr(C, "IDLE_WATCHDOG_LOUD", True)
    monkeypatch.setattr(INV, "send_telegram_text",
                        lambda text, silent=False: flags.append(silent) or True)
    INV._watchdog_idle("chirag", 720.0)
    assert flags == [False]  # loud alert (silent=False) when IDLE_WATCHDOG_LOUD=1


def test_show_wallet_fires_low_escrow_alert(monkeypatch, capsys):
    sent = []

    class _C:
        @classmethod
        def from_env(cls, account=None):
            return cls()

        def wallet(self):
            return 5000.0  # below the 10k threshold

    monkeypatch.setattr(INV, "I2iClient", _C)
    monkeypatch.setattr(INV, "send_telegram_text",
                        lambda text, silent=False: sent.append((text, silent)) or True)
    assert INV.show_wallet() == 0
    assert "escrow LOW" in sent[0][0] and sent[0][1] is False
    assert "Rs 5,000" in capsys.readouterr().out


def test_show_wallet_no_alert_above_threshold(monkeypatch, capsys):
    sent = []

    class _C:
        @classmethod
        def from_env(cls, account=None):
            return cls()

        def wallet(self):
            return 50000.0

    monkeypatch.setattr(INV, "I2iClient", _C)
    monkeypatch.setattr(INV, "send_telegram_text",
                        lambda text, silent=False: sent.append(text) or True)
    assert INV.show_wallet() == 0
    assert sent == []


def test_show_config_prints_effective_gates(capsys):
    assert INV.show_config() == 0
    out = capsys.readouterr().out
    assert "account=" in out and "rate >" in out and "credit >=" in out
    assert "idle watchdog" in out


def test_portfolio_digest_no_auth_does_not_raise(monkeypatch):
    monkeypatch.setattr(INV, "I2iClient", _NoAuthClient)
    monkeypatch.setattr(INV, "send_telegram_text", lambda *a, **k: True)
    assert INV.portfolio_digest() == 0


# ── fixture-based end-to-end: full invest pipeline, no network ───────────────


def test_end_to_end_plan_from_fixture(monkeypatch):
    """Locks the FULL invest path: fetch (fixture) -> select -> plan -> place.
    Uses a fake client whose invest() returns success, and asserts the final
    investorNow payloads carry the right amounts. No network, real fixture rows."""
    import json
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "loans_raw.json").read_text(encoding="utf-8")
    )
    # Make every fixture row qualify: rate > 100 AND credit >= 720 (or no-credit).
    rows = []
    for i, ln in enumerate(fixture):
        row = dict(ln)
        row["pl_applicable_rate"] = f"{120.0 + i}"
        row["pl_amt_left"] = "20000"  # fixture has one row with 0 left; make all investable
        if i % 2 == 0:
            row["usr_cibil_score"] = 780
        else:
            row["usr_cibil_score"] = None  # no-credit imputed 720, passes
        rows.append(row)

    import i2i_watch.sources.i2i as src
    monkeypatch.setattr(src, "fetch_all_loans", lambda: rows)
    monkeypatch.setattr(INV.storage, "load_invested", lambda **kw: [])

    placed_payloads = []

    class _E2EClient:
        @classmethod
        def from_env(cls, account=None):
            return cls()

        def wallet(self):
            return 30000.0

        def loan_detail(self, uid, lid):
            return {
                "pl_bloan_id": int(lid), "pl_user_id": uid, "bloan_tenure": 6,
                "pl_current_rate": "120.0", "bname": "x", "bloan_i2i_category": "X",
                "purpose": "x", "min_invest_loan_amount": 1000,
                "max_invest_loan_amount": 5000, "invest_multiple_value": 1,
            }

        def principal_protection(self, *a):
            return {}

        def invest(self, payload):
            placed_payloads.append(dict(payload))
            return {"data": "Invested Successfully", "message": "Fund added successfully."}

    monkeypatch.setattr(INV, "I2iClient", _E2EClient)
    monkeypatch.setenv("I2I_TXN_PIN", "1234")
    with patch.object(INV.storage, "record_invested"), patch.object(INV, "send_telegram_text") as notify:
        assert INV.run(live=True) == 0
    assert 'href="https://www.i2ifunding.com/borrower/listing/public-profile/' in notify.call_args.args[0]
    # All 5 fixture loans placed at min(5000, remaining) — 5 x Rs 5,000
    assert len(placed_payloads) == 5
    assert all(p["amount"] <= 5000 for p in placed_payloads)
    assert all(p["amount"] >= 1000 for p in placed_payloads)
    total = sum(p["amount"] for p in placed_payloads)
    assert total == 25000 and total <= 30000  # 5 x cap, within the wallet
    assert all(p["transactionPin"] == "1234" for p in placed_payloads)
    assert all("loanId" in p and "monthlyEMI" in p for p in placed_payloads)

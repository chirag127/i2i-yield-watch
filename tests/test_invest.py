"""Unit tests for the auto-investor's pure logic (no browser/network)."""

from __future__ import annotations

import base64
import hashlib

from i2i_watch.auth import encrypt_password
from i2i_watch.invest import build_invest_payload, emi, select, size_amount


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
    assert [s["loanId"] for s in select(rows, 40.0)] == [4, 2, 1]  # 50; 46.66 by score desc; 38 dropped


def test_select_strictly_above_gate():
    rows = [{"pl_bloan_id": 1, "pl_applicable_rate": "40.0", "bloan_cibil_score": 700, "pl_amt_left": "5000"}]
    assert select(rows, 40.0) == []  # 40 is NOT > 40


def test_size_caps_and_floors():
    assert size_amount(9000, 3200, 25000, 1000, 5000, 1) == 3200
    assert size_amount(500, 5000, 25000, 1000, 5000, 1) == 0
    assert size_amount(9000, 9000, 25000, 1000, 5000, 1) == 5000   # PER_LOAN_CAP 5000
    assert size_amount(9000, 3333, 25000, 1000, 5000, 100) == 3300  # multiple flooring


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

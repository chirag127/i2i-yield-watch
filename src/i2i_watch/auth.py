"""i2iFunding auth — pure login. AES-encrypt the password the way the SPA does
(CryptoJS AES.encrypt(pw, passphrase), passphrase from i2i's main.js — proven by
decrypting a captured login blob), POST /login/, return fresh tokens.

Fresh session_id + csrf_token EVERY run removes the short-lived-session expiry
that made earlier direct calls 401. One job; the crypto is pure + testable.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import urllib.request

from . import config as C

log = logging.getLogger("i2i_watch")


def _evp_bytes_to_key(passphrase: bytes, salt: bytes,
                      klen: int = 32, ivlen: int = 16) -> tuple[bytes, bytes]:
    """OpenSSL EVP_BytesToKey (MD5) — the KDF CryptoJS uses for AES.encrypt(str, pass)."""
    d = b""
    prev = b""
    while len(d) < klen + ivlen:
        prev = hashlib.md5(prev + passphrase + salt).digest()
        d += prev
    return d[:klen], d[klen:klen + ivlen]


def encrypt_password(plaintext: str, passphrase: str = C.AES_PASSPHRASE) -> str:
    """Replicate CryptoJS `AES.encrypt(plaintext, passphrase).toString()`:
    OpenSSL 'Salted__' format, random 8-byte salt, EVP_BytesToKey MD5 KDF,
    AES-256-CBC, PKCS7 pad, base64. Verified vs the captured i2i login blob."""
    from Crypto.Cipher import AES  # pycryptodome — lazy (only login needs it)
    salt = os.urandom(8)
    key, iv = _evp_bytes_to_key(passphrase.encode(), salt)
    data = plaintext.encode()
    pad = 16 - (len(data) % 16)
    data += bytes([pad]) * pad
    ct = AES.new(key, AES.MODE_CBC, iv).encrypt(data)
    return base64.b64encode(b"Salted__" + salt + ct).decode()


def browser_headers() -> dict[str, str]:
    """Headers a real i2i browser tab sends. Origin/Referer/UA are what keep the
    API from 502-ing fragile/synthetic direct requests."""
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": C.ORIGIN,
        "Referer": C.REFERER,
        "User-Agent": C.USER_AGENT,
    }


def login(email: str, plaintext_password: str) -> tuple[str, str]:
    """Direct POST /login/ with the AES-encrypted password; return
    (csrf_token, session_id). Verified response carries both plus id/userType."""
    body = {
        "usr_email": email,
        "usr_password": encrypt_password(plaintext_password),
        "source": "WEB",
    }
    url = f"{C.OPEN_LOANS_HOST}/{C.LOGIN_ENDPOINT}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers=browser_headers(),
    )
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.loads(r.read())
    sid = data.get("session_id")
    csrf = data.get("csrf_token")
    if not (sid and csrf):
        raise SystemExit(f"login failed (no tokens): {data.get('message', data)}")
    log.info("i2i auto-login OK (fresh session, investor id %s)", data.get("id"))
    return csrf, sid

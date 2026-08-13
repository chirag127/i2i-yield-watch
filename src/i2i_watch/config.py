"""Central config — the SINGLE source of ALL i2i tunables + constants. Every
module imports from here; nothing hardcoded elsewhere (no magic numbers, no
scattered endpoints/field names).

Two DISTINCT rate gates:
  - MIN_INTEREST_PCT      NOTIFY/monitor threshold (alert on loans >= this).
  - MIN_INVEST_RATE_PCT   AUTO-INVEST gate (place money only on rate STRICTLY >).

HARD SAFETY RAILS (caps) are circuit breakers — real money moves through them.
Each numeric is env-overridable so CI can tune without a code change.
"""

from __future__ import annotations

import os


def _f(env: str, default: float) -> float:
    try:
        return float(os.environ.get(env, "") or default)
    except ValueError:
        return default


# ── rate gates (two distinct thresholds — do NOT conflate) ──────────────────
MIN_INTEREST_PCT: float = _f("MIN_INTEREST_PCT", 40.0)          # NOTIFY gate (>=)
MIN_INVEST_RATE_PCT: float = _f("MIN_INVEST_RATE_PCT", 150.0)   # INVEST gate (strictly >)

# ── hard caps / sizing ──────────────────────────────────────────────────────
PER_LOAN_CAP: float = _f("PER_LOAN_CAP", 5000.0)             # never exceed per loan
PER_RUN_CAP: float = _f("PER_RUN_CAP", 25000.0)              # max deployed per run (breaker)
MIN_WALLET_BUFFER: float = _f("MIN_WALLET_BUFFER", 0.0)      # leave untouched
INVEST_MIN_AMOUNT: float = _f("INVEST_MIN_AMOUNT", 1000.0)   # min per investment
INVEST_MAX_AMOUNT: float = _f("INVEST_MAX_AMOUNT", 5000.0)   # platform max/loan
INVEST_MULTIPLE: float = _f("INVEST_MULTIPLE", 1.0)          # amount granularity

# ── hosts / endpoints ───────────────────────────────────────────────────────
OPEN_LOANS_HOST = "https://api.i2ifunding.com/api/v1"   # login, feed, wallet, investorNow
API_BASE = "https://apiv1.i2ifunding.com"               # cancel/funding
LOGIN_ENDPOINT = "login/"

# ── raw-row field names (HAR-verified) ──────────────────────────────────────
RATE_FIELDS = ("pl_applicable_rate", "pl_current_rate", "pl_inital_rate")
LOAN_ID_FIELDS = ("pl_bloan_id", "pl_id")
AMOUNT_FIELDS = ("pl_amt_left", "pl_final_amt", "pl_amt")

# ── browser-parity headers (keep the API from 502-ing direct requests) ──────
ORIGIN = "https://www.i2ifunding.com"
REFERER = "https://www.i2ifunding.com/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ── auth ────────────────────────────────────────────────────────────────────
# CryptoJS passphrase from i2i's main.js (var u=…) — PROVEN by decrypting the
# captured login blob back to the real password. Public frontend constant.
AES_PASSPHRASE = "kXyb3gzU"
LOGIN_EMAIL_ENV = "I2I_EMAIL"          # plaintext email  (env / CI secret)
LOGIN_PASSWORD_ENV = "I2I_PASSWORD"    # plaintext password (AES-encrypted before send)
TXN_PIN_ENV = "I2I_TXN_PIN"            # transaction PIN — required for --live

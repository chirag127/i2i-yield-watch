"""Central config — the SINGLE source of ALL i2i tunables + constants. Every
module imports from here; nothing hardcoded elsewhere (no magic numbers, no
scattered endpoints/field names).

THREE DISTINCT notification/investment gates — DO NOT CONFLATE (env var == config name, one each):

  name                    | default | meaning                       | operator
  ------------------------|---------|-------------------------------|---------
  NOTIFY_MIN_RATE_PCT     | 40      | DETAILED ALERT                | rate >  this
  NOTIFY_BUCKET_MIN_RATE_PCT| 0      | SILENT BUCKET SUMMARY (ALL)   | rate >  this (0 = all loans)
  AUTOINVEST_MIN_RATE_PCT | 100     | PLACE REAL MONEY              | rate >  this

NOTIFY = free/read-only (Telegram/ntfy). BUCKET notifications are silent summaries. AUTOINVEST = spends money; 100 places
money only on loans with rate STRICTLY > 100% (the user's chosen gate).

HARD SAFETY RAILS (caps) are circuit breakers — real money moves through them.
Each numeric is env-overridable so CI can tune without a code change.
"""

from __future__ import annotations

import json
import os


def _f(env: str, default: float) -> float:
    try:
        return float(os.environ.get(env, "") or default)
    except ValueError:
        return default


# ── rate gates (two distinct thresholds — do NOT conflate; see table above) ──
NOTIFY_MIN_RATE_PCT: float = _f("NOTIFY_MIN_RATE_PCT", 40.0)          # detailed alert gate (rate >)
NOTIFY_BUCKET_MIN_RATE_PCT: float = _f("NOTIFY_BUCKET_MIN_RATE_PCT", 0.0) # silent bucket gate (rate >); 0 = ALL loans
NOTIFY_HIGH_RATE_PCT: float = _f("NOTIFY_HIGH_RATE_PCT", 100.0)       # LOUD alert gate (rate >)
# Bucket boundaries are lower-inclusive and upper-exclusive, except the final
# bucket. Keep these stable so a loan moving across a boundary is observable.
# Covers the full i2i marketplace rate spectrum from 0% to 100%+.
NOTIFY_BUCKETS: tuple[tuple[str, float, float | None], ...] = (
    ("0-10", 0.0, 10.0),
    ("10-18", 10.0, 18.0),
    ("18-24", 18.0, 24.0),
    ("24-30", 24.0, 30.0),
    ("30-40", 30.0, 40.0),
    ("40-50", 40.0, 50.0),
    ("50-70", 50.0, 70.0),
    ("70-100", 70.0, 100.0),
    ("100+", 100.0, None),
)
AUTOINVEST_MIN_RATE_PCT: float = _f("AUTOINVEST_MIN_RATE_PCT", 100.0) # MONEY gate (rate >)
# Credit gate: skip loans with score BELOW this (score >= 500 qualifies).
# Loans with NO credit score are IMPUTED as NO_CREDIT_IMPUTED_SCORE (720), so
# they PASS the 500 gate (a missing bureau file is not a 0) but rank as
# high-risk/uncertain below any real 750+ score — never treated as a 0 score.
# SINGLE SOURCE OF TRUTH: change this value to change the credit gate for every account and workflow.
AUTOINVEST_MIN_CREDIT_SCORE: float = 500.0
TOPUP_MIN_RATE_PCT: float = _f("TOPUP_MIN_RATE_PCT", 150.0)          # ADD-FUNDS trigger (rate >)
# Idle-capital watchdog: after this many days with NO qualifying loan, the
# auto-investor pings Telegram so idle escrow never goes silently unmonitored.
# IDLE_WATCHDOG_LOUD=1 makes the nudge a loud (buzzing) alert.
IDLE_WATCHDOG_DAYS: float = _f("IDLE_WATCHDOG_DAYS", 3.0)
IDLE_WATCHDOG_LOUD: bool = (os.environ.get("IDLE_WATCHDOG_LOUD", "").strip().lower()
                            in ("1", "true", "yes", "on"))
# Low-escrow threshold (Rs): the wallet-check ping becomes a LOUD alert when the
# investable balance drops below this, so a drained escrow is never missed.
WALLET_ALERT_THRESHOLD: float = _f("WALLET_ALERT_THRESHOLD", 10000.0)
# Escrow-truth TTL (hours): after an i2i investorNow rejection reports the REAL
# investable escrow balance, trust that figure for this long. availableWallet is
# a lending-limit-style field, NOT the escrow balance (live-verified 2026-09-02:
# it read Rs 50,000 while the rejection said Rs 1,093). After this window the
# API estimate is used again until the next rejection refreshes the truth.
ESCROW_TRUTH_TTL_HOURS: float = _f("ESCROW_TRUTH_TTL_HOURS", 2.0)

# ── hard caps / sizing ──────────────────────────────────────────────────────
PER_LOAN_CAP: float = _f("PER_LOAN_CAP", 5000.0)             # never exceed per loan
MIN_WALLET_BUFFER: float = _f("MIN_WALLET_BUFFER", 0.0)      # leave untouched
INVEST_MIN_AMOUNT: float = _f("INVEST_MIN_AMOUNT", 1000.0)   # min per investment
INVEST_MAX_AMOUNT: float = _f("INVEST_MAX_AMOUNT", 5000.0)   # platform max/loan
INVEST_MULTIPLE: float = _f("INVEST_MULTIPLE", 1.0)          # amount granularity
TOPUP_MAX_AMOUNT: float = _f("TOPUP_MAX_AMOUNT", 25000.0)   # max per add-funds request

# ── hosts / endpoints ───────────────────────────────────────────────────────
OPEN_LOANS_HOST = "https://api.i2ifunding.com/api/v1"   # login, feed, wallet, investorNow
API_BASE = "https://apiv1.i2ifunding.com"               # cancel/funding
LOGIN_ENDPOINT = "login/"
LISTING_ENDPOINT = "getActiveFilteredBorrowers/"        # marketplace loan feed (paged)
LISTING_PAGE_SIZE = 10                                  # rows/page (server-fixed)
LISTING_MAX_PAGES = 60                                  # pagination safety cap
# Never treat an empty marketplace response as a real empty market. The
# pipeline refuses to overwrite active state when fewer than this many rows are
# returned; a scraper/auth outage must fail loudly instead of archiving everything.
LISTING_MIN_ROWS: int = int(_f("LISTING_MIN_ROWS", 1.0))

# Exact filter body the SPA POSTs to getActiveFilteredBorrowers (HAR-verified).
# ROOT CAUSE of the old direct-HTTP "timeout": the endpoint HANGS unless the body
# is this COMPLETE filter object with all options present (empty {} / partial /
# empty-options bodies all block for 15s+). All active=false => "no filter". We
# only mutate pageNo per request. Auth is pure query-param (csrf/session_id) — no
# cookie or Authorization header is involved.
LISTING_FILTER_BODY: dict = json.loads(
    '{"riskCategory":{"label":"Risk Category","options":[{"text":"A","active":false,"value":"A"},{"text":"B","active":false,"value":"B"},{"text":"C","active":false,"value":"C"},{"text":"D","active":false,"value":"D"},{"text":"E","active":false,"value":"E"},{"text":"F","active":false,"value":"F"},{"text":"X","active":false,"value":"X"}]},"employement":{"label":"Employement","options":[{"text":"Salaried Employee","active":false,"value":"salaried"},{"text":"Self Emp Business","active":false,"value":"business"},{"text":"Self Emp Professional","active":false,"value":"selfEmployed"}]},"product":{"label":"Product","options":[{"text":"Regular Loans","active":false,"value":"Regular Loans","id":1},{"text":"Employer Partnership","active":false,"value":"Employer Partnership","id":2},{"text":"Loan Against Invoice","active":false,"value":"Loan Against Invoice","id":3},{"text":"Course Subscription Fee","active":false,"value":"Course Subscription Fee","id":4},{"text":"NBFC Backed","active":false,"value":"NBFC Backed","id":5},{"text":"Urban Clap","active":false,"value":"Urban Clap","id":6},{"text":"Backed by Partner Company","active":false,"value":"Backed by Partner Company","id":8}]},"cibilScore":{"label":"Credit Bureau Score","options":[{"text":">=700","active":false,"min":700,"max":-1},{"text":"650-700","active":false,"min":651,"max":700},{"text":"600-650","active":false,"min":601,"max":650},{"text":"No History","active":false,"min":0,"max":0}]},"preferredInterestRate":{"label":"Interest Rate","options":[{"text":"<18%","active":false,"min":0,"max":17},{"text":"18%-24%","active":false,"min":18,"max":23},{"text":"24%-30%","active":false,"min":24,"max":30}]},"tenure":{"label":"Tenure","options":[{"text":"<3 Months","active":false,"min":0,"max":2},{"text":"3 Months - 6 Months","active":false,"min":3,"max":5},{"text":"6 Months - 12 Months","active":false,"min":6,"max":11},{"text":"12 Months - 18 Months","active":false,"min":12,"max":17},{"text":"18 Months - 24 Months","active":false,"min":18,"max":23},{"text":">24 Months","active":false,"min":24,"max":-1}]},"income":{"label":"Income","options":[{"text":"<25,000","active":false,"min":0,"max":24999},{"text":"25,000 - 50,000","active":false,"min":25000,"max":49999},{"text":"50,000-75,000","active":false,"min":50000,"max":74999},{"text":"75,000+","active":false,"min":75000,"max":-1}]},"funded":{"label":"% Funded","options":[{"text":"<25%","active":false,"min":0,"max":24},{"text":"25%-50%","active":false,"min":25,"max":49},{"text":"50%-75%","active":false,"min":50,"max":74},{"text":"75%-100%","active":false,"min":75,"max":100},{"text":"All Live Loan","active":false,"min":0,"max":100}]},"daysLeft":{"label":"Days Left","options":[{"text":"0-7 Days","active":false,"min":0,"max":6},{"text":"7-15 Days","active":false,"min":7,"max":14},{"text":"> 15 Days","active":false,"min":15,"max":-1}]},"location":"","pageNo":1}'
)

# ── raw-row field names (HAR-verified) ──────────────────────────────────────
RATE_FIELDS = ("pl_applicable_rate", "pl_current_rate", "pl_inital_rate")
LOAN_ID_FIELDS = ("pl_bloan_id", "pl_id")
AMOUNT_FIELDS = ("pl_amt_left", "pl_final_amt", "pl_amt")

# ── ranking policy (shared by notify sort + auto-invest select) ──────────────
# Order of importance: RETURN (rate) first, then borrower CREDIT SCORE, then
# loan TENURE (longer = better for locking in a high rate). A borrower with NO
# credit score is IMPUTED as this value (720 — passes the 700 gate but ranks as
# high-risk/uncertain below any real 750+ score) and is FLAGGED as "no credit
# score" in the notification.
NO_CREDIT_IMPUTED_SCORE = 720.0

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
UPI_ID_ENV = "I2I_UPI_ID"              # UPI ID for add-money collect requests

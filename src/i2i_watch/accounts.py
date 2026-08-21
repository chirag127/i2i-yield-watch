"""Multi-account portfolio support.

Each i2i investor account is a named profile with its OWN auth, rate gates and
storage namespace, so a portfolio can run N accounts in parallel (the platform
caps ~Rs 5,000 per loan per investor — more accounts = more capital per loan).

ENV MODEL — one variable per account, namespaced as I2I_<ACCOUNT>_<BASE>:

  chirag (DEFAULT account)          neeru (secondary account)
  --------------------------------- ----------------------------------------
  I2I_EMAIL                         I2I_NEERU_EMAIL
  I2I_PASSWORD                      I2I_NEERU_PASSWORD
  I2I_TXN_PIN                       I2I_NEERU_TXN_PIN
  I2I_UPI_ID                        I2I_NEERU_UPI_ID
  I2I_CSRF_TOKEN (fallback auth)    I2I_NEERU_CSRF_TOKEN
  I2I_SESSION_ID (fallback auth)    I2I_NEERU_SESSION_ID
  AUTOINVEST_MIN_RATE_PCT           I2I_NEERU_AUTOINVEST_MIN_RATE_PCT
  AUTOINVEST_MIN_CREDIT_SCORE       I2I_NEERU_AUTOINVEST_MIN_CREDIT_SCORE
  TOPUP_MIN_RATE_PCT                I2I_NEERU_TOPUP_MIN_RATE_PCT
  PER_LOAN_CAP (optional)           I2I_NEERU_PER_LOAN_CAP

The DEFAULT account (I2I_ACCOUNTS list's first entry, default "chirag") keeps
the LEGACY unprefixed names so existing .env / CI secrets keep working
unchanged. Secondary accounts use the I2I_<ACCOUNT>_ prefix.

SELECTION: `I2I_ACCOUNT` env names the account for a run; unset = default.
`I2I_ACCOUNTS` (comma-separated) declares the whole portfolio for tooling/CI.
Adding a third account = add its name to I2I_ACCOUNTS + set its I2I_<NAME>_*
vars + add a matrix row in .github/workflows/invest.yml.

STORAGE NAMESPACES: invested-loans.json (default account, legacy path) and
data/invested-loans-<account>.json for each secondary account, so dedup and
`cancel --all-invested` never cross accounts.
"""

from __future__ import annotations

import os

# Base names that use the legacy I2I_ prefix (auth) vs unprefixed (gates).
_I2I_PREFIXED = ("EMAIL", "PASSWORD", "TXN_PIN", "UPI_ID", "CSRF_TOKEN", "SESSION_ID")


def account_names() -> list[str]:
    """All accounts in the portfolio, from I2I_ACCOUNTS (default: chirag)."""
    raw = (os.environ.get("I2I_ACCOUNTS") or "chirag").strip()
    return [n.strip().lower() for n in raw.split(",") if n.strip()]


def default_account() -> str:
    return account_names()[0]


def active_account() -> str:
    """Account for this run: I2I_ACCOUNT env, else the default account."""
    return (os.environ.get("I2I_ACCOUNT") or default_account()).strip().lower()


def is_default(name: str) -> bool:
    return name.lower() == default_account()


def env_key(account: str, base: str) -> str:
    """Env var name for `base` under `account`.

    Default account keeps the legacy names (I2I_EMAIL, AUTOINVEST_MIN_RATE_PCT,
    ...) so nothing breaks; secondary accounts use I2I_<ACCOUNT>_<BASE>.
    """
    base = base.upper()
    if base.startswith("I2I_"):
        base = base[4:]
    acct = account.strip().lower()
    if is_default(acct):
        return f"I2I_{base}" if base in _I2I_PREFIXED else base
    return f"I2I_{acct.upper()}_{base}"


def get_float(account: str, base: str, default: float) -> float:
    """Per-account float config: I2I_<ACCT>_<BASE> env, falling back to the
    default account's value, then the given default."""
    for acct in (account, default_account()):
        try:
            v = os.environ.get(env_key(acct, base), "")
            if v.strip():
                return float(v)
        except ValueError:
            continue
    return default


def storage_name(account: str) -> str:
    """Storage file/collection suffix for this account. The default account
    keeps the legacy path ("" -> invested-loans.json); others get a suffix."""
    if is_default(account):
        return ""
    return f"-{account.strip().lower()}"

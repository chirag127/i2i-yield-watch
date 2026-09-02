"""Cancel (reverse) i2iFunding fundings by loanId — thin wrapper over the client
(subcommand: `python -m i2i_watch cancel`).

POST investor/cancel/funding {loanId, transactionPin} on the legacy host
(HAR-verified success: {"status":"Success","message":"Funding reversed
successfully"}). Default is a DRY RUN; --live actually reverses. Any error
mid-run STOPS. --all-invested pulls loanIds from data/invested-loans.json.
Needs I2I_EMAIL/I2I_PASSWORD (login) + I2I_TXN_PIN (--live).

    python -m i2i_watch cancel 1439214 1440111        # DRY RUN
    python -m i2i_watch cancel 1439214 --live          # REAL cancel
    python -m i2i_watch cancel --all-invested --live    # cancel all this session placed
"""

from __future__ import annotations

import logging
import os

from . import accounts
from . import config as C
from . import storage
from .client import I2iClient
from .notify.channels import send_telegram_text

log = logging.getLogger("i2i_watch")


def run(loan_ids: list[int], live: bool = False, all_invested: bool = False,
        account: str | None = None) -> int:
    acct = account or accounts.active_account()
    ids = list(loan_ids or [])
    if all_invested:
        ids = sorted(set(ids) | set(storage.load_invested(account=acct)))
    if not ids:
        print("no loanIds given (pass loanId(s) or --all-invested) -> nothing to cancel")
        return 0

    print(f"CANCEL ({'LIVE' if live else 'DRY RUN'}): {len(ids)} funding(s) -> {ids}")
    if not live:
        print("DRY RUN — cancelled nothing. Pass --live to cancel for real.")
        return 0

    pin = (os.environ.get(accounts.env_key(acct, "TXN_PIN")) or "").strip()
    if not pin:
        print(f"ERR --live needs {accounts.env_key(acct, 'TXN_PIN')} "
              f"(transaction PIN) — STOP, cancelled nothing")
        return 1

    client = I2iClient.from_env(acct)
    done = []
    for lid in ids:
        try:
            resp = client.cancel(lid, pin)
        except Exception as e:  # noqa: BLE001
            log.error("ERR cancel loan %s: %s — STOP (%d cancelled)", lid, e, len(done))
            break
        ok = isinstance(resp, dict) and str(resp.get("status", "")).lower() == "success"
        msg = resp.get("message", resp) if isinstance(resp, dict) else resp
        if not ok:
            log.error("ERR loan %s not confirmed: %s — STOP (%d cancelled)", lid, msg, len(done))
            break
        done.append(lid)
        print(f"  OK cancelled loan {lid} — {msg}")

    failed = [lid for lid in ids if lid not in done]
    if done:
        send_telegram_text("♻️ <b>i2i cancel: reversed "
                           f"{len(done)} funding(s)</b>\n"
                           + "\n".join(f"• Loan {x}" for x in done))
    if failed:
        # A live cancel that did NOT fully complete must fail the workflow —
        # otherwise the run reports success while money stays invested.
        print(f"cancelled {len(done)}/{len(ids)} funding(s); failed: {failed}")
        return 1
    return 0

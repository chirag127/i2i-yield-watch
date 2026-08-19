"""CLI: python -m i2i_watch [--iterations N] [--interval S] [-v]

Default (no subcommand): scrape/monitor loop. Self-loop (--iterations>1)
approximates sub-hourly polling inside one GitHub Actions run.

Subcommands:
  invest [--live]            place investments (dry-run unless --live)
  cancel <loanId>… [--live]  reverse fundings (dry-run unless --live)
  wallet                     print the real investable escrow balance
"""

from __future__ import annotations

import argparse
import sys
import time

from .pipeline import run
from .util import configure_logging, log


def _cmd_invest(args) -> int:
    from .invest import run as invest_run

    return invest_run(live=args.live, account=args.account)


def _cmd_cancel(args) -> int:
    from .cancel import run as cancel_run

    return cancel_run(loan_ids=args.loan_ids, live=args.live,
                      all_invested=args.all_invested, account=args.account)


def _cmd_topup(args) -> int:
    from .topup import run as topup_run

    return topup_run(live=args.live, account=args.account)


def _cmd_wallet(args) -> int:
    from .invest import show_wallet

    return show_wallet(account=args.account)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="i2i_watch", description="i2iFunding high-yield loan watch")
    p.add_argument("--iterations", type=int, default=1, help="self-loop count")
    p.add_argument("--interval", type=int, default=60, help="seconds between iterations")
    p.add_argument("--reset-notify-state", action="store_true",
                   help="clear notify-state before running so all currently-qualifying loans re-announce once")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--account", default=None,
                   help="portfolio account to run for (I2I_ACCOUNT env, else default; e.g. neeru)")

    sub = p.add_subparsers(dest="cmd")
    pi = sub.add_parser("invest", help="place investments (dry-run unless --live)")
    pi.add_argument("--live", action="store_true", help="place REAL money (default: dry-run)")
    pi.add_argument("--account", default=None, help="portfolio account (default: I2I_ACCOUNT env)")
    pi.add_argument("-v", "--verbose", action="store_true")
    pc = sub.add_parser("cancel", help="reverse fundings (dry-run unless --live)")
    pc.add_argument("loan_ids", nargs="*", type=int, help="loanId(s) to cancel")
    pc.add_argument("--all-invested", action="store_true",
                    help="cancel every loanId in this account's invested-loans file")
    pc.add_argument("--account", default=None, help="portfolio account (default: I2I_ACCOUNT env)")
    pc.add_argument("--live", action="store_true", help="cancel for REAL (default: dry-run)")
    pc.add_argument("-v", "--verbose", action="store_true")
    pt = sub.add_parser("topup", help="initiate escrow top-up for >TOPUP_MIN_RATE_PCT loans (dry-run unless --live)")
    pt.add_argument("--live", action="store_true", help="initiate the UPI/PayU payment request (default: dry-run)")
    pt.add_argument("--account", default=None, help="portfolio account (default: I2I_ACCOUNT env)")
    pt.add_argument("-v", "--verbose", action="store_true")
    pw = sub.add_parser("wallet", help="print the real investable escrow balance (availableWallet minus committed funds)")
    pw.add_argument("--account", default=None, help="portfolio account (default: I2I_ACCOUNT env)")
    pw.add_argument("-v", "--verbose", action="store_true")

    args = p.parse_args(argv)
    configure_logging(getattr(args, "verbose", False))

    if args.cmd == "invest":
        return _cmd_invest(args)
    if args.cmd == "cancel":
        return _cmd_cancel(args)
    if args.cmd == "topup":
        return _cmd_topup(args)
    if args.cmd == "wallet":
        return _cmd_wallet(args)

    # default: scrape/monitor loop
    if args.reset_notify_state:
        from . import storage
        storage.save_notify_state([], notified_at="1970-01-01T00:00:00Z")
        # FULL reset: also clear the ever-notified history so EVERY currently-
        # qualifying loan re-announces once, not just ones not in the last
        # notify-state snapshot. Without this, loans marked sent weeks ago stay
        # silent forever even after --reset-notify-state.
        storage._write_json("notifications-sent.json", [])
        log.info("notify-state + notifications-sent RESET — next run re-announces all qualifying loans")

    rc = 0
    for i in range(1, args.iterations + 1):
        log.info("=== iteration %d/%d ===", i, args.iterations)
        try:
            summary = run()
            log.info("iteration %d done: %s", i, summary["notificationsSent"])
        except Exception as e:  # noqa: BLE001
            log.error("iteration %d failed: %s", i, e)
            rc = 1
            break
        if i < args.iterations:
            time.sleep(args.interval)
    return rc


if __name__ == "__main__":
    sys.exit(main())

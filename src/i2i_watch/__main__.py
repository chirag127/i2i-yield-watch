"""CLI: python -m i2i_watch [--iterations N] [--interval S] [-v]

Self-loop (--iterations>1) approximates sub-hourly polling inside one GitHub
Actions run, since GitHub throttles high-frequency cron. Each iteration diffs
against storage the previous iteration wrote, so notifications chain correctly.
"""

from __future__ import annotations

import argparse
import sys
import time

from .pipeline import run
from .util import configure_logging, log


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="i2i_watch", description="i2iFunding high-yield loan watch")
    p.add_argument("--iterations", type=int, default=1, help="self-loop count")
    p.add_argument("--interval", type=int, default=60, help="seconds between iterations")
    p.add_argument("--reset-notify-state", action="store_true",
                   help="clear notify-state before running so all currently-qualifying loans re-announce once")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    configure_logging(args.verbose)

    if args.reset_notify_state:
        from . import storage
        storage.save_notify_state([], notified_at="1970-01-01T00:00:00Z")
        log.info("notify-state RESET — next run re-announces all qualifying loans")

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

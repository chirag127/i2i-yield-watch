#!/usr/bin/env python3
"""ONE-SHOT manual test: invest EXACTLY ₹1000 into the single highest-rate live
loan. Real money. Prints confirmation + the exact cancel command.

Run (live):  I2I_EMAIL=.. I2I_PASSWORD=.. I2I_TXN_PIN=.. python scripts/invest_one_1000.py --live
Dry-run:     (omit --live) — picks the loan + builds the payload, places nothing.
"""
from __future__ import annotations

import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from i2i_watch.client import I2iClient          # noqa: E402
from i2i_watch import invest as INV             # noqa: E402
from i2i_watch import config as C               # noqa: E402

AMOUNT = 1000.0
LIVE = "--live" in sys.argv


def _rate(ln: dict) -> float:
    for k in C.RATE_FIELDS:
        v = ln.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    client = I2iClient.from_env()

    # Loan LISTING: try the direct getActiveFilteredBorrowers POST first; if it
    # blocks (this endpoint resists direct urllib), fall back to the proven
    # browser scraper (Playwright) — the one call that genuinely needs the browser.
    loans: list[dict] = []
    base = {"riskCategory": {}, "employement": {}, "product": {}, "cibilScore": {},
            "preferredInterestRate": {}, "tenure": {}, "income": {}, "funded": {},
            "daysLeft": {}, "location": {}}
    try:
        for page in range(1, 40):
            body = {**base, "pageNo": page}
            rows = client._post(C.OPEN_LOANS_HOST, "getActiveFilteredBorrowers/", body)
            rows = rows if isinstance(rows, list) else next(
                (v for v in rows.values() if isinstance(v, list)), []) if isinstance(rows, dict) else []
            if not rows:
                break
            loans += [r for r in rows if isinstance(r, dict)]
            if len(rows) < 10:
                break
    except Exception as e:  # noqa: BLE001 — direct listing blocked → browser scraper
        print(f"direct listing failed ({str(e)[:60]}); using browser scraper fallback")
        from i2i_watch.sources.i2i import fetch_all_loans
        loans = [r for r in fetch_all_loans() if isinstance(r, dict)]
    if not loans:
        print("no open loans returned"); return 1
    top = max(loans, key=_rate)
    rate = _rate(top)
    loan_id = top.get("pl_bloan_id") or top.get("pl_id")
    borrower_uid = top.get("pl_user_id")
    print(f"{len(loans)} open loans. TOP: id={loan_id} rate={rate}% borrower_uid={borrower_uid} "
          f"amt_left={top.get('pl_amt_left')} cibil={top.get('bloan_cibil_score')} cat={top.get('bloan_i2i_category')}")

    detail = client.loan_detail(borrower_uid, loan_id)
    payload = INV.build_invest_payload(detail, AMOUNT, rate)
    # inject the txn pin for a live order
    pin = os.environ.get(C.TXN_PIN_ENV)
    if LIVE and not pin:
        print(f"--live needs {C.TXN_PIN_ENV}; STOP, placed nothing"); return 2
    if pin:
        payload["transactionPin"] = pin
    safe = {k: ("***" if k == "transactionPin" else v) for k, v in payload.items()}
    print("PAYLOAD:", safe)

    if not LIVE:
        print("\nDRY RUN — placed nothing. Add --live to invest ₹1000 for real.")
        return 0

    resp = client.invest(payload)
    print("\nINVEST RESPONSE:", resp)
    ok = str(resp.get("data", "")).lower().startswith("invested") or resp.get("status") == "Success"
    if ok:
        print(f"\n✅ Invested ₹{AMOUNT:.0f} into loan {loan_id} @ {rate}%.")
        print(f"TO CANCEL:  I2I_EMAIL=.. I2I_PASSWORD=.. I2I_TXN_PIN=.. python -m i2i_watch cancel {loan_id} --live")
    else:
        print("\n⚠️ Response did not confirm success — verify on i2ifunding.com before assuming it placed.")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())

"""REAL-MONEY auto-investor — PURE selection/ranking/sizing/EMI + a thin
orchestrator. All HTTP goes through client.I2iClient; all tunables come from
config. The pure functions (emi, select, size_amount, build_invest_payload) take
plain dicts so the money-safety logic unit-tests without a network.

Loan LISTING is scraped by the EXISTING browser scraper (sources/i2i.py — the one
endpoint that reliably blocks direct HTTP). Selection keeps rate STRICTLY ABOVE
config.AUTOINVEST_MIN_RATE_PCT (150 => 0 candidates vs a ~46.7%-max market: safe
no-op). Default is a DRY RUN that prints the plan and places nothing.

    python -m i2i_watch invest           # DRY RUN — prints plan, places nothing
    python -m i2i_watch invest --live     # REAL money — places investorNow orders

Auth: I2I_EMAIL + I2I_PASSWORD (auto-login). --live also needs I2I_TXN_PIN.
Any HTTP/response error mid-run STOPS (no further spending). Placed loanIds go to
data/invested-loans.json (storage) so `cancel --all-invested` can reverse them.
"""

from __future__ import annotations

import logging
import math
import os

from . import config as C
from . import storage
from .client import I2iClient, to_float
from .notify.channels import send_telegram_text
from .util import tenure_months

log = logging.getLogger("i2i_watch")


# ── pure logic (no network — unit-tested directly) ───────────────────────────
def emi(principal: float, annual_rate_pct: float, tenure_months: int) -> float:
    """Reducing-balance EMI — matches the i2i SPA exactly (HAR-verified:
    1000 @ 46.66% / 7m => 165.92)."""
    if tenure_months <= 0:
        return 0.0
    r = annual_rate_pct / 12.0 / 100.0
    if r == 0:
        return round(principal / tenure_months, 2)
    f = (1 + r) ** tenure_months
    return round(principal * r * f / (f - 1), 2)


def _first(d: dict, keys: tuple[str, ...]) -> object | None:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def select(loans: list[dict], min_rate: float = C.AUTOINVEST_MIN_RATE_PCT) -> list[dict]:
    """Raw rows -> candidates with rate STRICTLY > min_rate, ranked by importance:
    rate desc, then credit score desc (no credit -> imputed 750), then tenure desc.
    Field names per config (HAR-verified)."""
    out = []
    for ln in loans:
        if not isinstance(ln, dict):
            continue
        rate = to_float(_first(ln, C.RATE_FIELDS))
        if rate <= min_rate:
            continue
        raw_score = ln.get("bloan_cibil_score")
        no_credit = raw_score in (None, "") or to_float(raw_score) <= 0
        score = C.NO_CREDIT_IMPUTED_SCORE if no_credit else to_float(raw_score)
        out.append({
            "loanId": _first(ln, C.LOAN_ID_FIELDS),
            "borrowerUserId": ln.get("pl_user_id"),
            "rate": rate,
            "score": score,
            "noCredit": no_credit,
            "tenure": to_float(tenure_months(ln.get("bloan_tenure") or ln.get("tenure"))),
            "amtLeft": to_float(ln.get("pl_amt_left")),
        })
    return sorted(out, key=lambda x: (x["rate"], x["score"], x["tenure"]), reverse=True)


def size_amount(amt_left: float, wallet: float, run_left: float,
                lo: float, hi: float, mult: float) -> float:
    """min(max, PER_LOAN_CAP, amt_left, wallet, per-run remaining), floored to a
    multiple of `mult` and to whole rupees; 0 if it can't reach the minimum."""
    cap = min(hi, C.PER_LOAN_CAP, amt_left, wallet, run_left)
    if cap < lo:
        return 0.0
    step = mult if mult and mult > 0 else 1.0
    amt = math.floor(math.floor(cap / step) * step)
    return float(amt) if amt >= lo else 0.0


def build_invest_payload(detail: dict, amount: float, rate: float) -> dict:
    """Exact investorNow payload (HAR-verified field names). transactionPin is
    left None and filled at placement time (never logged)."""
    tenure = int(to_float(detail.get("bloan_tenure")))
    int_rate = detail.get("pl_current_rate") or f"{rate:.2f}"
    return {
        "loanId": int(to_float(detail.get("pl_bloan_id"))),
        "amount": int(amount),
        "principalProtectionId": False,
        "monthlyEMI": emi(amount, to_float(int_rate, rate), tenure),
        "intRate": int_rate,
        "tenure": tenure,
        "borrowerName": detail.get("bname", ""),
        "riskCategory": detail.get("bloan_i2i_category", ""),
        "revisedEMI": None,
        "loanPurpose": detail.get("purpose", ""),
        "borrowerEmail": "",
        "transactionPin": None,
    }


# ── orchestrator (thin — all I/O via the client) ────────────────────────────
def _plan(client: I2iClient, sel: list[dict], wallet0: float) -> list[dict]:
    """Ordered plan; pulls per-loan detail to size + build the payload. Deducts
    each amount from a running wallet + per-run budget; de-dupes within the run."""
    plan: list[dict] = []
    wallet = wallet0 - C.MIN_WALLET_BUFFER
    run_left = C.PER_RUN_CAP
    seen: set[str] = set()
    for s in sel:
        lid = s["loanId"]
        if lid is None or str(lid) in seen:
            continue
        if wallet < C.INVEST_MIN_AMOUNT or run_left < C.INVEST_MIN_AMOUNT:
            break
        d = client.loan_detail(s["borrowerUserId"], lid)
        lo = to_float(d.get("min_invest_loan_amount"), C.INVEST_MIN_AMOUNT)
        hi = to_float(d.get("max_invest_loan_amount"), C.INVEST_MAX_AMOUNT)
        mult = to_float(d.get("invest_multiple_value"), C.INVEST_MULTIPLE)
        amt = size_amount(s["amtLeft"], wallet, run_left, lo, hi, mult)
        if amt <= 0:
            continue
        try:  # UI-parity call the browser makes before investing (harmless)
            client.principal_protection(d.get("bloan_i2i_category"), d.get("pl_current_rate"))
        except Exception:  # noqa: BLE001
            pass
        plan.append({
            "loanId": lid,
            "rate": to_float(d.get("pl_current_rate"), s["rate"]),
            "score": s["score"],
            "noCredit": s.get("noCredit", False),
            "amount": amt,
            "payload": build_invest_payload(d, amt, s["rate"]),
        })
        seen.add(str(lid))
        wallet -= amt
        run_left -= amt
    return plan


def _place(client: I2iClient, loans: list[dict], sel: list[dict],
           wallet: float, gate: float, live: bool) -> int:
    print(f"{len(loans)} open loans | {len(sel)} loans >{gate:.0f}% | wallet Rs {wallet:,.0f}")
    if wallet < C.INVEST_MIN_AMOUNT + C.MIN_WALLET_BUFFER:
        print(f"wallet Rs {wallet:,.0f} below min invest + buffer -> nothing to invest")
        return 0

    try:
        plan = _plan(client, sel, wallet)
    except Exception as e:  # noqa: BLE001
        log.error("ERR building plan: %s — STOP, placed nothing", e)
        return 1
    if not plan:
        print("no loan clears the sizing floor -> nothing to invest, exiting")
        return 0

    total = sum(p["amount"] for p in plan)
    print(f"\nPLAN ({'LIVE' if live else 'DRY RUN'}): {len(plan)} loan(s), Rs {total:,.0f} total")
    for p in plan:
        cs = "no-credit→750" if p.get("noCredit") else f"score {p['score']:.0f}"
        print(f"  Loan {p['loanId']}: {p['rate']:.2f}% {cs} -> Rs {p['amount']:,.0f}")

    if not live:
        print("\nDRY RUN — placed nothing. Pass --live to invest for real.")
        return 0

    pin = (os.environ.get(C.TXN_PIN_ENV) or "").strip()
    if not pin:
        print(f"ERR --live needs {C.TXN_PIN_ENV} (transaction PIN) — STOP, placed nothing")
        return 1

    placed, invested, skipped = [], 0.0, 0
    # A per-loan "you can invest maximum up to ₹X / already invested" rejection means
    # THIS loan is maxed for this investor — skip it and keep going. Only genuinely
    # dangerous errors (auth, network, unknown) abort the whole run.
    def _is_loan_maxed(text: str) -> bool:
        t = str(text).lower()
        return ("maximum up to" in t) or ("already invested" in t) or ("max" in t and "in this loan" in t)

    def _is_low_balance(text: str) -> bool:
        t = str(text).lower()
        return ("escrow" in t and "balance" in t) or ("sufficient balance" in t) or ("available balance" in t)

    low_balance_msg = ""
    for p in plan:
        p["payload"]["transactionPin"] = pin
        try:
            resp = client.invest(p["payload"])
        except Exception as e:  # noqa: BLE001
            body = getattr(e, "i2i_body", "") or str(e)
            if _is_low_balance(body):
                low_balance_msg = str(body)[:200]
                log.warning("LOW BALANCE on loan %s: %s — STOP placing, will notify", p["loanId"], low_balance_msg)
                break
            if _is_loan_maxed(body):
                log.warning("SKIP loan %s: already maxed for this investor (%s) — continuing",
                            p["loanId"], str(body)[:120])
                skipped += 1
                continue
            log.error("ERR investorNow loan %s: %s — STOP (invested Rs %.0f, %d loan(s))",
                      p["loanId"], e, invested, len(placed))
            break
        ok = isinstance(resp, dict) and (
            "success" in str(resp.get("message", "")).lower()
            or "success" in str(resp.get("data", "")).lower())
        msg = (resp.get("message") or resp.get("data") or "") if isinstance(resp, dict) else str(resp)
        if not ok:
            if _is_low_balance(msg):
                low_balance_msg = str(msg)[:200]
                log.warning("LOW BALANCE on loan %s: %s — STOP placing, will notify", p["loanId"], low_balance_msg)
                break
            if _is_loan_maxed(msg):
                log.warning("SKIP loan %s: already maxed (%s) — continuing", p["loanId"], str(msg)[:120])
                skipped += 1
                continue
            log.error("ERR loan %s not confirmed: %s — STOP (invested Rs %.0f)",
                      p["loanId"], msg, invested)
            break
        invested += p["amount"]
        placed.append(p)
        print(f"  OK loan {p['loanId']}: Rs {p['amount']:,.0f} — {msg}")

    # Low-balance alert: tell the operator to top up the i2i escrow account.
    if low_balance_msg:
        try:
            send_telegram_text(
                "⚠️ <b>i2i auto-invest: LOW ESCROW BALANCE</b>\n"
                f"Could not place all planned investments — top up your i2i Escrow Account.\n"
                f"i2i said: <i>{low_balance_msg}</i>"
                + (f"\nPlaced Rs {invested:,.0f} in {len(placed)} loan(s) before running low." if placed else "")
            )
        except Exception:  # noqa: BLE001
            log.warning("failed to send low-balance Telegram alert")

    if placed:
        storage.record_invested([int(p["loanId"]) for p in placed])
        lines = [f"\U0001f4b8 <b>i2i auto-invest: Rs {invested:,.0f} across "
                 f"{len(placed)} loan(s)</b> (&gt;{gate:.0f}%)"]
        for p in placed:
            cs = "⚠ no credit score (ranked as 750)" if p.get("noCredit") else f"score {p['score']:.0f}"
            lines.append(f"• Loan {p['loanId']}: {p['rate']:.2f}% "
                         f"{cs} — Rs {p['amount']:,.0f}")
        send_telegram_text("\n".join(lines))
    else:
        print(f"placed nothing ({skipped} loan(s) skipped — already maxed for this investor)"
              if skipped else "placed nothing")
    return 0


def run(live: bool = False) -> int:
    """One auto-invest cycle. Dry-run unless live=True. Returns a process rc."""
    gate = C.AUTOINVEST_MIN_RATE_PCT

    from .sources.i2i import fetch_all_loans
    try:
        loans = fetch_all_loans()
    except Exception as e:  # noqa: BLE001
        log.error("ERR scraping marketplace: %s — STOP, placed nothing", e)
        return 1

    sel = select(loans, gate)
    if not sel:
        # e.g. gate=150 vs a ~46.7%-max market -> 0 candidates, the safe no-op.
        msg = (f"{len(loans)} open loans | {len(sel)} loans >{gate:.0f}%: 0 "
               f"-> nothing to invest, exiting")
        log.info(msg)
        print(msg)
        return 0

    try:
        client = I2iClient.from_env()
    except SystemExit as e:  # no creds: scrape + ranking path ran; place nothing
        log.warning("%s", e)
        print(f"{len(loans)} open loans | {len(sel)} loans >{gate:.0f}% | "
              f"no auth -> wallet 0, placing nothing")
        return 0

    wallet = client.wallet()
    return _place(client, loans, sel, wallet, gate, live)

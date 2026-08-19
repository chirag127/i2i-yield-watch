"""REAL-MONEY auto-investor — PURE selection/ranking/sizing/EMI + a thin
orchestrator. All HTTP goes through client.I2iClient; all tunables come from
config. The pure functions (emi, select, size_amount, build_invest_payload) take
plain dicts so the money-safety logic unit-tests without a network.

Loan LISTING is scraped by the EXISTING browser scraper (sources/i2i.py — the one
endpoint that reliably blocks direct HTTP). Selection keeps rate STRICTLY ABOVE
config.AUTOINVEST_MIN_RATE_PCT (rate strictly > gate; default 100). Default is a
DRY RUN that prints the plan and places nothing.

    python -m i2i_watch invest           # DRY RUN — prints plan, places nothing
    python -m i2i_watch invest --live     # REAL money — places investorNow orders

Auth: I2I_EMAIL + I2I_PASSWORD (auto-login). --live also needs I2I_TXN_PIN.
Any HTTP/response error mid-run STOPS (no further spending). Placed loanIds go to
data/invested-loans.json (storage) so `cancel --all-invested` can reverse them and
later runs exclude already-funded loans (dedup). A run deploys down the ranked
list until the wallet is exhausted — no per-run cap beyond the wallet itself.
"""

from __future__ import annotations

import logging
import math
import os
import re

from . import accounts
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


def select(loans: list[dict], min_rate: float = C.AUTOINVEST_MIN_RATE_PCT,
           min_score: float = C.AUTOINVEST_MIN_CREDIT_SCORE) -> list[dict]:
    """Raw rows -> candidates with rate STRICTLY > min_rate AND credit score
    >= min_score, ranked by importance: rate desc, then credit score desc
    (no credit -> imputed 750), then tenure desc. Field names per config
    (HAR-verified). No-credit loans are imputed NO_CREDIT_IMPUTED_SCORE (750),
    which meets the default min_score=750 gate — a missing bureau file is NOT
    treated as a 0 and is NOT filtered out."""
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
        if score < min_score:
            continue  # credit gate: below threshold -> never invest
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


def size_amount(amt_left: float, wallet: float, lo: float, hi: float, mult: float) -> float:
    """min(platform max, PER_LOAN_CAP, amt_left, wallet), floored to a multiple of
    `mult` and to whole rupees; 0 if it can't reach the minimum."""
    cap = min(hi, C.PER_LOAN_CAP, amt_left, wallet)
    if cap < lo:
        return 0.0
    step = mult if mult and mult > 0 else 1.0
    amt = math.floor(math.floor(cap / step) * step)
    return float(amt) if amt >= lo else 0.0


def exclude_invested(sel: list[dict], invested_ids: set[int]) -> list[dict]:
    """Drop candidates already funded (data/invested-loans.json) so a later run
    never re-attempts the same loan."""
    return [s for s in sel if s["loanId"] is not None and int(s["loanId"]) not in invested_ids]


def is_loan_maxed(text: str) -> bool:
    t = str(text).lower()
    return ("maximum up to" in t) or ("already invested" in t) or ("max" in t and "in this loan" in t)


def is_low_balance(text: str) -> bool:
    t = str(text).lower()
    return ("escrow" in t and "balance" in t) or ("sufficient balance" in t) or ("available balance" in t)


def parse_amount(text: str) -> float:
    """First ₹/Rs amount in i2i's message -> float; 0 if none."""
    m = re.search(r"(?:rs\.?|₹)\s*([\d,]+(?:\.\d+)?)", str(text), re.I)
    if not m:
        return 0.0
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return 0.0


def parse_max_amount(text: str) -> float:
    """Remaining 'maximum up to ₹X' a maxed loan can still take; 0 if none."""
    m = re.search(r"max(?:imum)?\D{0,40}(?:rs\.?|₹)\s*([\d,]+(?:\.\d+)?)", str(text), re.I)
    if not m:
        return 0.0
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return 0.0


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
    each amount from a running wallet until it can't meet the minimum; de-dupes
    within the run."""
    plan: list[dict] = []
    wallet = wallet0 - C.MIN_WALLET_BUFFER
    seen: set[str] = set()
    for s in sel:
        lid = s["loanId"]
        if lid is None or str(lid) in seen:
            continue
        if wallet < C.INVEST_MIN_AMOUNT:
            break
        d = client.loan_detail(s["borrowerUserId"], lid)
        lo = to_float(d.get("min_invest_loan_amount"), C.INVEST_MIN_AMOUNT)
        hi = to_float(d.get("max_invest_loan_amount"), C.INVEST_MAX_AMOUNT)
        mult = to_float(d.get("invest_multiple_value"), C.INVEST_MULTIPLE)
        amt = size_amount(s["amtLeft"], wallet, lo, hi, mult)
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
    return plan


def _place(client: I2iClient, loans: list[dict], sel: list[dict],
           wallet: float, gate: float, live: bool, account: str | None = None) -> int:
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

    acct = account or accounts.active_account()
    pin = (os.environ.get(accounts.env_key(acct, "TXN_PIN")) or "").strip()
    if not pin:
        print(f"ERR --live needs {accounts.env_key(acct, 'TXN_PIN')} "
              f"(transaction PIN) — STOP, placed nothing")
        return 1

    placed, invested, skipped = [], 0.0, 0
    # A per-loan "you can invest maximum up to ₹X / already invested" rejection means
    # THIS loan is maxed for this investor — retry at the remaining ₹X if it clears
    # the floor, else skip. Only genuinely dangerous errors (auth, network, unknown)
    # abort the whole run.

    def _try_invest(payload: dict) -> tuple[bool, str, str]:
        """Returns (ok, message, error_body). Never raises."""
        try:
            r = client.invest(payload)
        except Exception as e:  # noqa: BLE001
            return False, "", (getattr(e, "i2i_body", "") or str(e))
        m = (r.get("message") or r.get("data") or "") if isinstance(r, dict) else str(r)
        ml = str(m).lower()
        # Exact success signal — NOT a substring ('success' in 'unsuccessful' is a
        # false positive). i2i success bodies: "Invested Successfully" / "Fund added
        # successfully". Require a word-boundary success AND no negation.
        okr = isinstance(r, dict) and (
            "successfully" in ml or "invested success" in ml or "fund added" in ml
        ) and "unsuccess" not in ml and "not " not in ml and "fail" not in ml
        return okr, str(m), ""

    low_balance_msg = ""
    reduced_retry_used = False
    for p in plan:
        p["payload"]["transactionPin"] = pin
        ok, msg, body = _try_invest(p["payload"])
        # If it failed purely on low escrow balance, retry THIS loan with the
        # available amount only (rounded down to the invest multiple), if that
        # still meets the platform minimum. "Invest the remaining amount only."
        if not ok and body and is_low_balance(body):
            avail = parse_amount(body)
            reduced = math.floor(min(avail, p["amount"]) / C.INVEST_MULTIPLE) * C.INVEST_MULTIPLE \
                if C.INVEST_MULTIPLE else min(avail, p["amount"])
            if reduced >= C.INVEST_MIN_AMOUNT and reduced < p["amount"]:
                log.warning("loan %s: low escrow (Rs %.0f avail) — retrying with Rs %.0f",
                            p["loanId"], avail, reduced)
                p["payload"]["amount"] = int(reduced)
                p["amount"] = reduced
                reduced_retry_used = True
                ok, msg, body = _try_invest(p["payload"])
        # Loan maxed for this investor: lend the remaining it can still take.
        if not ok and ((body and is_loan_maxed(body)) or is_loan_maxed(msg)):
            max_amt = parse_max_amount(body) or parse_max_amount(msg)
            reduced = math.floor(min(max_amt, p["amount"]) / C.INVEST_MULTIPLE) * C.INVEST_MULTIPLE \
                if C.INVEST_MULTIPLE else min(max_amt, p["amount"])
            if max_amt > 0 and reduced >= C.INVEST_MIN_AMOUNT and reduced < p["amount"]:
                log.warning("loan %s: maxed (Rs %.0f left) — retrying with Rs %.0f",
                            p["loanId"], max_amt, reduced)
                p["payload"]["amount"] = int(reduced)
                p["amount"] = reduced
                ok, msg, body = _try_invest(p["payload"])
        if not ok:
            if body and is_low_balance(body):
                low_balance_msg = str(body)[:200]
                log.warning("LOW BALANCE on loan %s: %s — STOP placing, will notify", p["loanId"], low_balance_msg)
                break
            if (body and is_loan_maxed(body)) or is_loan_maxed(msg):
                log.warning("SKIP loan %s: already maxed (%s) — continuing",
                            p["loanId"], str(body or msg)[:120])
                skipped += 1
                continue
            log.error("ERR investorNow loan %s: %s%s — STOP (invested Rs %.0f, %d loan(s))",
                      p["loanId"], msg, (" / " + body if body else ""), invested, len(placed))
            break
        invested += p["amount"]
        placed.append(p)
        # Record IMMEDIATELY (not just after the loop): if a later loan crashes the
        # run, an already-placed loan must still be in this account's invested-loans
        # file so the next run's dedup won't re-invest it.
        try:
            storage.record_invested([int(p["loanId"])], account=account)
        except Exception:  # noqa: BLE001
            log.warning("failed to record invested loan %s immediately (will retry after loop)", p["loanId"])
        print(f"  OK loan {p['loanId']}: Rs {p['amount']:,.0f} — {msg}")

    # Low-balance alert: tell the operator to ADD BALANCE to the i2i escrow account.
    # Fires when escrow ran dry with more qualifying loans still available — whether
    # the run stopped on a low-balance 400 OR invested the last rupees via the
    # reduced-amount retry (escrow now ~0 but candidates remain unfunded).
    ran_dry = bool(low_balance_msg) or (len(placed) < len(plan) and reduced_retry_used)
    if ran_dry:
        remaining_loans = len(plan) - len(placed) - skipped
        try:
            send_telegram_text(
                "⚠️ <b>i2i auto-invest: ADD BALANCE</b>\n"
                f"Your i2i Escrow Account is low — please add funds so pending high-rate "
                f"loans can be invested.\n"
                + (f"Placed Rs {invested:,.0f} in {len(placed)} loan(s) this run; "
                   f"{remaining_loans} qualifying loan(s) still need funding.\n" if placed
                   else f"Placed nothing this run; {remaining_loans} qualifying loan(s) waiting on balance.\n")
                + (f"i2i said: <i>{low_balance_msg}</i>" if low_balance_msg else "")
            )
        except Exception:  # noqa: BLE001
            log.warning("failed to send add-balance Telegram alert")

    if placed:
        storage.record_invested([int(p["loanId"]) for p in placed], account=account)
        acct_label = f" ({account})" if account else ""
        lines = [f"\U0001f4b8 <b>i2i auto-invest{acct_label}: Rs {invested:,.0f} across "
                 f"{len(placed)} loan(s)</b> (&gt;{gate:.0f}%)"]
        for p in placed:
            cs = "⚠ no credit score (ranked as 750)" if p.get("noCredit") else f"score {p['score']:.0f}"
            lines.append(f"• Loan {p['loanId']}: {p['rate']:.2f}% "
                         f"{cs} — Rs {p['amount']:,.0f}")
        try:
            send_telegram_text("\n".join(lines), silent=True)
        except Exception:  # noqa: BLE001
            log.warning("failed to send invest summary Telegram alert")
    else:
        print(f"placed nothing ({skipped} loan(s) skipped — already maxed for this investor)"
              if skipped else "placed nothing")
    return 0


def run(live: bool = False, account: str | None = None) -> int:
    """One auto-invest cycle for ONE account. Dry-run unless live=True.
    account=None -> accounts.active_account() (I2I_ACCOUNT env, else default).
    Returns a process rc."""
    acct = account or accounts.active_account()
    gate = accounts.get_float(acct, "AUTOINVEST_MIN_RATE_PCT", C.AUTOINVEST_MIN_RATE_PCT)
    credit_gate = accounts.get_float(
        acct, "AUTOINVEST_MIN_CREDIT_SCORE", C.AUTOINVEST_MIN_CREDIT_SCORE
    )

    from .sources.i2i import fetch_all_loans
    try:
        loans = fetch_all_loans()
    except Exception as e:  # noqa: BLE001
        log.error("ERR scraping marketplace: %s — STOP, placed nothing", e)
        return 1

    sel = select(loans, gate, credit_gate)
    sel = exclude_invested(sel, set(storage.load_invested(account=acct)))
    if not sel:
        # gate above the market max, or every qualifying loan already funded
        msg = (f"{len(loans)} open loans | {len(sel)} loans >{gate:.0f}% "
               f"after dedup: 0 -> nothing to invest, exiting")
        log.info(msg)
        print(msg)
        return 0

    try:
        client = I2iClient.from_env(acct)
    except SystemExit as e:  # no creds: scrape + ranking path ran; place nothing
        log.warning("%s", e)
        print(f"{len(loans)} open loans | {len(sel)} loans >{gate:.0f}% | "
              f"no auth for '{acct}' -> wallet 0, placing nothing")
        return 0

    wallet = client.wallet()
    return _place(client, loans, sel, wallet, gate, live, account=acct)

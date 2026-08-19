"""Semi-auto escrow top-up via Paytm (HAR-verified + live-tested).

Detects loans above `TOPUP_MIN_RATE_PCT` (default 150) and, when the wallet can't
cover them, INITIATES the real i2i add-money call — POST apiv1.i2ifunding.com/
paytm/paynow (multipart: TXN_AMOUNT / CHANNEL=WEB / pageurl) — then sends a LOUD
Telegram so the operator opens the Paytm checkout, picks UPI, and approves the
collect request on their phone.

Not a silent debit: money moves only after the operator's on-device UPI/Paytm
approval. The bot prepares the payment and surfaces the link/instructions.

e-NACH is deliberately NOT used (operator decision). The `paytm/paynow` response
shape is LIVE-VERIFIED (2026-08-18): `{"restdata": {ORDER_ID, CUST_ID, MID,
CHECKSUMHASH, CALLBACK_URL, url: secure.paytmpayments.com/theia/processTransaction}}`.
Because Paytm's checkout accepts a form POST (not a GET link), `run()` also writes
an auto-submitting HTML page so the operator can just open it in a browser.

Dry-run by default; `--live` needs I2I creds (auto-login).
"""

from __future__ import annotations

import logging
import os

from . import accounts
from . import config as C
from .client import I2iClient
from .invest import select
from .notify.channels import send_telegram_text

log = logging.getLogger("i2i_watch")


def topup_amount(sel: list[dict], wallet: float) -> float:
    """Rupees to add so every qualifying loan can take min(PER_LOAN_CAP, left).
    Capped at TOPUP_MAX_AMOUNT; 0 when the wallet already covers the shortfall."""
    needed = sum(min(C.PER_LOAN_CAP, s["amtLeft"]) for s in sel if s["amtLeft"] > 0)
    shortfall = max(0.0, needed - wallet)
    return float(min(shortfall, C.TOPUP_MAX_AMOUNT))


def _first_url(mapping: dict) -> str:
    for k in ("url", "paymentUrl", "payment_url", "redirect_url", "redirectUrl",
              "paymenturl", "link"):
        v = mapping.get(k)
        if v and "http" in str(v):
            return str(v)
    return ""


def extract_payment_url(resp: object) -> str:
    """Paytm checkout URL from the paynow response. Live shape (verified
    2026-08-18): {"restdata": {..., "url": "https://secure.paytm.../theia/processTransaction"}}.
    Also tolerates older/alt shapes."""
    if not isinstance(resp, dict):
        return ""
    rd = resp.get("restdata")
    if isinstance(rd, dict):
        u = _first_url(rd)
        if u:
            return u
    u = _first_url(resp)
    if u:
        return u
    if resp.get("action") == "redirect":
        u = _first_url(resp)
        if u:
            return u
    d = resp.get("data")
    if isinstance(d, dict):
        u = _first_url(d)
        if u:
            return u
    if isinstance(d, str) and "http" in d:
        return d
    return ""


def build_checkout_page(resp: object) -> str:
    """Auto-submitting HTML form for the Paytm checkout, so the operator can
    open one file and land directly on the payment page (theia/processTransaction
    needs a form POST with the order fields — a bare GET link renders nothing).
    Returns "" if the response isn't the expected restdata shape."""
    if not isinstance(resp, dict):
        return ""
    rd = resp.get("restdata")
    if not isinstance(rd, dict) or not rd.get("url"):
        return ""
    fields = "".join(
        f'<input type="hidden" name="{k}" value="{v}">'
        for k, v in rd.items() if k != "url" and not isinstance(v, (dict, list))
    )
    return (
        "<!doctype html><html><head><meta charset=utf-8>"
        "<title>i2i top-up — Paytm checkout</title></head>"
        "<body onload=\"document.forms[0].submit()\">"
        "<p>Redirecting to Paytm… if nothing happens, click:</p>"
        f'<form method="post" action="{rd["url"]}">{fields}'
        '<button type="submit">Open Paytm checkout</button></form></body></html>'
    )


def build_topup_message(sel: list[dict], amount: float, payment_ref: str,
                        upi_id: str = "") -> str:
    lines = [f"🔝 <b>i2i top-up: approve ₹{amount:,.0f}</b>"]
    lines.append(f"{len(sel)} loan(s) above the top-up gate:")
    for s in sel[:10]:
        lines.append(f"• Loan {s['loanId']}: {s['rate']:.2f}% — ₹{s['amtLeft']:,.0f} left")
    if payment_ref:
        lines.append(f"\n{payment_ref}")
    if upi_id:
        lines.append(f"\nUPI collect → enter <b>{upi_id}</b> on the checkout's UPI tab, "
                     f"then approve the collect request in your UPI app.")
    elif not payment_ref:
        lines.append("\nOpen the i2iFunding app → Add Money → Payment Gateway and "
                     "approve via UPI/Paytm, or NEFT/IMPS/RTGS to the nodal account.")
    return "\n".join(lines)


def run(live: bool = False, account: str | None = None) -> int:
    acct = account or accounts.active_account()
    gate = accounts.get_float(acct, "TOPUP_MIN_RATE_PCT", C.TOPUP_MIN_RATE_PCT)
    upi_id = (os.environ.get(accounts.env_key(acct, "UPI_ID")) or "").strip()

    from .sources.i2i import fetch_all_loans
    try:
        loans = fetch_all_loans()
    except Exception as e:  # noqa: BLE001
        log.error("ERR scraping marketplace: %s — STOP, initiated nothing", e)
        return 1

    sel = select(loans, gate)
    if not sel:
        print(f"{len(loans)} open loans | 0 loans >{gate:.0f}% -> no top-up")
        return 0

    try:
        client = I2iClient.from_env(acct)
    except SystemExit as e:  # no creds -> can't initiate a payment
        log.warning("%s", e)
        print(f"{len(loans)} open loans | {len(sel)} loans >{gate:.0f}% | "
              f"no auth -> cannot top up")
        return 0

    wallet = client.wallet()
    amt = topup_amount(sel, wallet)
    if amt <= 0:
        print(f"{len(sel)} loans >{gate:.0f}% but wallet ₹{wallet:,.0f} already "
              f"covers them -> no top-up")
        return 0

    print(f"\nTOP-UP ({'LIVE' if live else 'DRY RUN'}): add ₹{amt:,.0f} to fund "
          f"{len(sel)} loan(s) >{gate:.0f}%")
    if not live:
        print("DRY RUN — no payment initiated. Pass --live to initiate the Paytm request.")
        return 0

    resp = client.paytm_paynow(amt)
    url = extract_payment_url(resp)
    page = build_checkout_page(resp)
    page_path = ""
    if page:
        import tempfile, webbrowser  # noqa: PLC0415

        fd, page_path = tempfile.mkstemp(suffix=".html", prefix="i2i_topup_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(page)
        try:
            webbrowser.open(f"file://{page_path}")
        except Exception:  # noqa: BLE001
            pass
    try:
        send_telegram_text(build_topup_message(sel, amt, url or "", upi_id=upi_id))
    except Exception:  # noqa: BLE001
        log.warning("failed to send top-up Telegram alert")
    print("paynow response:", resp)
    if page_path:
        print(f"checkout page written + opened: {page_path} (auto-POSTs to Paytm)")
    print("approve at:", url or "(no URL parsed — open the Paytm checkout / use nodal transfer)")
    return 0

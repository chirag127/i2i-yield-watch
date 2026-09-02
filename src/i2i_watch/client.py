"""Thin i2iFunding HTTP client — the SINGLE place money endpoints are called.

DIRECT HTTP (per user preference), auth via fresh auto-login (auth.login) so
tokens never go stale. Every request carries browser-parity headers. If a money
POST 502s/403s even with fresh login + headers, that ONE call retries inside a
Playwright browser context (browser origin + cookies); the scraper already ships
Playwright, so it's a zero-extra-dep fallback. Set I2I_FORCE_BROWSER to force it.

Endpoint/field names come straight from the captured HAR (verified):
  login/                              -> session_id + csrf_token
  investor/walletAndFund              -> data.availableWallet
  investor/loandetailtoinvest/{u}/{l} -> data.{pl_bloan_id,bloan_tenure,...}
  investor/investorNow/               <- {loanId,amount,...,transactionPin}
  investor/cancel/funding (legacy)    <- {loanId,transactionPin}
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import config as C
from .auth import browser_headers, login

log = logging.getLogger("i2i_watch")


def to_float(v: object, default: float = 0.0) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


class I2iClient:
    """Session-authed client for both i2i hosts. All money I/O lives here."""

    def __init__(self, csrf: str, sid: str, force_browser: bool = False,
                 account: str | None = None):
        self.csrf, self.sid = csrf, sid
        self._force_browser = force_browser
        self._account = account

    @classmethod
    def from_env(cls, account: str | None = None) -> "I2iClient":
        """Auth chain, best-first: (1) AUTO-LOGIN when creds are present — fresh
        tokens every run, so session expiry can never strand a money run;
        (2) the manual CSRF/SESSION token pair, used as a FALLBACK when login
        is unavailable or transiently fails (e.g. a network blip at run time),
        and directly when no creds are set. Raises SystemExit only when NO
        auth is available at all.

        Account-aware: env vars resolve per-account (accounts.env_key) —
        I2I_NEERU_EMAIL/I2I_NEERU_PASSWORD for account 'neeru', legacy
        I2I_EMAIL/I2I_PASSWORD for the default account."""
        import os

        from . import accounts

        acct = account or accounts.active_account()
        force = bool(os.environ.get("I2I_FORCE_BROWSER", "").strip())
        email = (os.environ.get(accounts.env_key(acct, "EMAIL")) or "").strip()
        pw = (os.environ.get(accounts.env_key(acct, "PASSWORD")) or "").strip()
        csrf = (os.environ.get(accounts.env_key(acct, "CSRF_TOKEN")) or "").strip()
        sid = (os.environ.get(accounts.env_key(acct, "SESSION_ID")) or "").strip()
        if email and pw:
            try:
                fresh_csrf, fresh_sid = login(email, pw)
                return cls(fresh_csrf, fresh_sid, force, acct)
            except Exception:  # noqa: BLE001 — login failed (network, bad creds)
                if csrf and sid:
                    log.warning("auto-login failed for account %s; falling back to "
                                "manual CSRF/SESSION tokens", acct)
                    return cls(csrf, sid, force, acct)
                raise
        if csrf and sid:
            return cls(csrf, sid, force, acct)
        raise SystemExit(
            f"no i2i auth for account '{acct}': set "
            f"{accounts.env_key(acct, 'EMAIL')}+{accounts.env_key(acct, 'PASSWORD')} "
            "(preferred, auto-login) or CSRF/SESSION tokens"
        )

    def _url(self, host: str, path: str) -> str:
        """Auth'd URL. Paths may already carry a query string (the EMI-status
        endpoints take ?isFilterApply=) — in that case the auth params are
        appended with '&', otherwise a fresh '?{auth}' is used."""
        q = urllib.parse.urlencode({"csrf_token": self.csrf, "session_id": self.sid})
        if "?" in path:
            return f"{host}/{path}&{q}"
        return f"{host}/{path}?{q}"

    def _get(self, host: str, path: str, timeout: int = 40) -> dict:
        req = urllib.request.Request(self._url(host, path), headers=browser_headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def _post(self, host: str, path: str, body: dict, timeout: int = 60,
              no_retry: bool = False) -> dict:
        """Direct POST; on 502/403 OR timeout/connection error fall back to a
        browser-context POST (the listing endpoint hangs on a fragile direct
        socket in some networks — widen the trigger so the browser catches it).

        no_retry=True DISABLES every fallback/retry — required for NON-idempotent
        money calls (investorNow): a timeout AFTER the order was placed must NOT
        silently re-POST (double-spend). The caller re-raises and the run STOPs."""
        if self._force_browser and not no_retry:
            return self._browser_post(host, path, body, timeout)
        req = urllib.request.Request(
            self._url(host, path), data=json.dumps(body).encode("utf-8"),
            method="POST", headers=browser_headers(),
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (502, 403) and not no_retry:
                log.warning("direct POST %s -> %d; browser-context fallback", path, e.code)
                return self._browser_post(host, path, body, timeout)
            # Surface the upstream error body (e.g. investorNow 400 rejection reason)
            # so the exact failing field is visible in logs. Never log the request
            # payload (may carry transactionPin); log only i2i's response text.
            try:
                err_body = e.read().decode("utf-8", "replace")[:500]
            except Exception:  # noqa: BLE001
                err_body = "(could not read error body)"
            log.error("direct POST %s -> HTTP %d; i2i response: %s", path, e.code, err_body)
            # Attach the response body + code so callers can classify the failure
            # (e.g. a per-loan "max ₹5,000/already invested" 400 → skip, not abort).
            e.i2i_body = err_body  # type: ignore[attr-defined]
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if no_retry:
                # Non-idempotent money call timed out — the order MAY have been
                # placed upstream. NEVER re-POST. Raise so the run stops and the
                # operator reconciles manually rather than risking a double-spend.
                log.error("direct POST %s -> %s; no_retry set (money call) — NOT retrying, raising",
                          path, type(e).__name__)
                raise
            log.warning("direct POST %s -> %s; browser-context fallback", path, type(e).__name__)
            return self._browser_post(host, path, body, timeout)

    def _browser_post(self, host: str, path: str, body: dict, timeout: int) -> dict:
        """Fallback: POST inside Playwright so the call carries the i2i origin +
        session cookies. Only used if direct HTTP actually blocks."""
        from playwright.sync_api import sync_playwright

        url = self._url(host, path)
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
            )
            try:
                ctx = browser.new_context(user_agent=C.USER_AGENT)
                resp = ctx.request.post(
                    url, data=json.dumps(body),
                    headers={k: v for k, v in browser_headers().items()
                             if k != "User-Agent"},
                    timeout=timeout * 1000,
                )
                return resp.json()
            finally:
                browser.close()

    # ── reads ────────────────────────────────────────────────────────────────
    def wallet(self) -> float:
        """Investable escrow balance (Rs) — a TWO-layer answer.

        1. TRUTH (authoritative): the last balance i2i's own investorNow
           rejection reported ("Available Balance ... for investment is Rs X"),
           persisted per-account, trusted while fresh within ESCROW_TRUTH_TTL_HOURS.
           Live-verified 2026-09-02: availableWallet read Rs 50,000 while the
           rejection said Rs 1,093 — the platform validates orders against the
           real escrow, so the rejection figure is the only honest balance.
        2. ESTIMATE (fallback): i2i's investorNow validates against the ESCROW
           account balance, NOT data.availableWallet alone. Live capture showed
           the SPA reports "Available Balance = Current − Funds Under Proposal/Disbursal"
           (availableWallet ₹50,000 but only ~₹21k actually investable — the rest was
           committed to open proposals). So the fallback estimate is:

               availableWallet − fundUnderProposal − disbursalPending

           computed when availableWallet is present; an explicit escrow/investable
           field (availableEscrow / escrowBalance / availableForInvestment) wins if
           present; falls back to availableWallet alone, then availableFunds. Logs
           the raw keys once so the exact field set is discoverable from run logs."""
        from . import storage

        truth = storage.load_escrow_truth(self._account)
        if truth:
            age_h = 0.0
            try:
                obs = datetime.fromisoformat(truth["observedAt"].replace("Z", "+00:00"))
                age_h = (datetime.now(timezone.utc) - obs).total_seconds() / 3600.0
            except (KeyError, TypeError, ValueError):  # malformed/missing date
                age_h = 0.0  # treat as fresh: the figure itself is the point
            ttl_h = C._f("ESCROW_TRUTH_TTL_HOURS", C.ESCROW_TRUTH_TTL_HOURS)
            if age_h <= ttl_h:
                log.info("wallet(): using escrow-truth Rs %.2f (observed %sh ago)",
                         truth["amount"], round(age_h, 2))
                return float(truth["amount"])
            log.info("wallet(): escrow-truth Rs %.2f is %sh old — stale, "
                     "falling back to the API estimate",
                     truth["amount"], round(age_h, 2))
        try:
            d = self._get(C.OPEN_LOANS_HOST, "investor/walletAndFund", timeout=30)
            data = d.get("data", {}) if isinstance(d, dict) else {}
            if isinstance(data, dict):
                log.info("walletAndFund fields: %s", sorted(data.keys()))
                for k in ("availableEscrow", "escrowBalance", "availableForInvestment"):
                    if data.get(k) is not None:
                        val = to_float(data.get(k))
                        log.info("wallet(): using %s = Rs %.2f", k, val)
                        return val
                if data.get("availableWallet") is not None:
                    committed = (to_float(data.get("fundUnderProposal"))
                                 + to_float(data.get("disbursalPending")))
                    investable = max(0.0, to_float(data.get("availableWallet")) - committed)
                    log.info("wallet(): availableWallet %.2f − committed %.2f = Rs %.2f",
                             to_float(data.get("availableWallet")), committed, investable)
                    return investable
        except Exception:  # noqa: BLE001
            pass
        try:
            d = self._get(C.OPEN_LOANS_HOST, "investor/availableFunds", timeout=30)
            return to_float(d.get("body", {}).get("fundAvailable"))
        except Exception:  # noqa: BLE001
            return 0.0

    def loan_detail(self, borrower_user_id: object, loan_id: object) -> dict:
        """GET loandetailtoinvest/{pl_user_id}/{loanId} -> data dict. First path
        segment is the borrower's pl_user_id (HAR-verified), NOT the investor id."""
        d = self._get(
            C.OPEN_LOANS_HOST,
            f"investor/loandetailtoinvest/{borrower_user_id}/{loan_id}",
        )
        return d.get("data", {}) if isinstance(d, dict) else {}

    def principal_protection(self, risk_cat: object, rate: object) -> dict:
        """UI-parity GET the browser fires before investing (harmless)."""
        return self._get(
            C.OPEN_LOANS_HOST, f"investor/principalProtection/{risk_cat}/{rate}")

    def list_loans(self) -> list[dict]:
        """Marketplace loan feed over PURE DIRECT HTTP — paginated.

        ROOT CAUSE of the historical "direct listing times out": the endpoint
        HANGS unless the POST body is the COMPLETE filter object (config
        .LISTING_FILTER_BODY); an empty/partial body blocks for 15s+. With the
        real body it returns 200 in ~3s/page. Auth is pure query-param, same as
        every other endpoint — no cookie/Authorization needed.

        Pages `pageNo` 1..N (10 rows/page) until a short/empty page. Raises on any
        HTTP/URL/timeout error so the caller (sources.i2i) can fall back to the
        Playwright scraper — Playwright stays the belt-and-suspenders fallback."""
        import copy

        rows: list[dict] = []
        seen: set[str] = set()
        url = self._url(C.OPEN_LOANS_HOST, C.LISTING_ENDPOINT)
        body = copy.deepcopy(C.LISTING_FILTER_BODY)
        for pg in range(1, C.LISTING_MAX_PAGES + 1):
            body["pageNo"] = pg
            data = json.dumps(body).encode("utf-8")
            page = None
            for attempt in range(3):  # per-page retry: the endpoint is socket-flaky
                req = urllib.request.Request(
                    url, data=data, method="POST", headers=browser_headers())
                try:
                    with urllib.request.urlopen(req, timeout=40) as r:
                        page = json.loads(r.read())
                    break
                except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                    if attempt == 2:
                        raise
                    log.warning("listing page %d %s; retry %d", pg, type(e).__name__, attempt + 1)
            if not isinstance(page, list) or not page:
                break
            for item in page:
                rid = str(item.get("pl_bloan_id") or item.get("pl_id") or "")
                if rid and rid not in seen:
                    seen.add(rid)
                    rows.append(item)
            if len(page) < C.LISTING_PAGE_SIZE:
                break
        log.info("direct-HTTP listing: %d loans over %d page(s)", len(rows), pg)
        return rows

    # ── add funds / top-up (initiates a payment the USER approves on-device) ──
    def paytm_paynow(self, amount: float, pageurl: str = "investoraccount/overview") -> dict:
        """POST apiv1.i2ifunding.com/paytm/paynow (multipart form) — Paytm
        checkout initiation, HAR-verified from a live session: fields
        TXN_AMOUNT / CHANNEL=WEB / pageurl. The SPA forwards the response's
        order + checksum to secure.paytmpayments.com/theia/processTransaction,
        where the operator picks UPI and approves the collect request. Money
        moves only after that on-device approval. Non-idempotent: never retried."""
        import httpx

        url = self._url(C.API_BASE, "paytm/paynow")
        form = {"TXN_AMOUNT": str(int(amount)), "CHANNEL": "WEB", "pageurl": pageurl}
        # multipart: DROP Content-Type so httpx sets the boundary itself.
        # browser_headers() hardcodes application/json — keeping it makes the
        # server body-parser try JSON.parse() on the multipart body (400
        # "Unexpected token - in JSON at position 0"). Live-verified.
        headers = {k: v for k, v in browser_headers().items() if k != "Content-Type"}
        r = httpx.post(url, files={k: (None, v) for k, v in form.items()},
                       headers=headers, timeout=60, follow_redirects=False)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return {"raw": r.text[:500]}

    def nodal_account_detail(self) -> dict:
        """GET apiv1.i2ifunding.com/investor/bank/escrowDetails — the nodal
        account (NEFT/IMPS/RTGS) details shown on the escrow screen."""
        return self._get(C.API_BASE, "investor/bank/escrowDetails")

    # ── EMI-status / portfolio health (read-only) ────────────────────────────
    # Endpoint + filter shapes reverse-engineered from the SPA (investoraccount/
    # emistatus page): getEMIStatusOverview (monthly repayment list + loan
    # counts), getEMIStatusOverviewInDetail (delayed buckets + pending splits),
    # loanDetailBorrowerWiseInDetail (paged per-loan rows), emi/loanEMIStatus/
    # {id} (per-loan EMI status), getDelayedLoanStatus (delayed overview).
    def emi_status_overview(self) -> dict:
        """Aggregate EMI-status overview: loan counts, total invested, average
        rates + the monthly repayment schedule. Returns the response body dict
        (keys logged once so field discovery stays cheap)."""
        d = self._get(C.API_BASE, "investor/getEMIStatusOverview?isFilterApply=")
        if isinstance(d, dict) and "body" in d:
            return d
        return {}

    def emi_status_detail(self) -> dict:
        """Detailed aggregate: on-time/delayed counts, delayed buckets
        (<30 / 30-90 / >90 days) and principal/interest received-vs-pending."""
        d = self._get(C.API_BASE, "investor/getEMIStatusOverviewInDetail?isFilterApply=")
        if isinstance(d, dict) and "body" in d:
            return d
        return {}

    def emi_loans(self, limit: int = 10, max_pages: int = 500) -> list[dict]:
        """Every loan row the EMI-status page shows, paged via
        loanDetailBorrowerWiseInDetail (POST). LIVE-VERIFIED pagination (probe
        2026-08-20): the response's `total` is the PER-PAGE count (10) while
        `totalRows` is the grand total — pagination must run until `totalRows`
        (or a short/empty page), NOT `total` (which capped the book at page 1
        and silently dropped ~95% of loans). The SPA filter body is
        {categories, employmentTypes, isFilterApply, currentStatus,
        disStartDate, disEndDate, name, loanId, skip, limit, searchValue};
        empty filters return the full book. Each raw row is kept (borrower
        name, loan id, amounts, EMI status, delayedDays) so the caller can
        compute per-loan + aggregate default numbers."""
        rows: list[dict] = []
        seen: set[str] = set()
        grand_total: int | None = None
        for pg in range(max_pages):
            body = {
                "categories": [], "employmentTypes": [], "isFilterApply": False,
                "currentStatus": "", "disStartDate": "", "disEndDate": "",
                "name": "", "loanId": "",
                "skip": pg * limit, "limit": limit, "searchValue": "",
            }
            d = self._post(C.API_BASE, "investor/loanDetailBorrowerWiseInDetail",
                           body, no_retry=True)
            if not isinstance(d, dict):
                break
            page = d.get("body", []) if isinstance(d.get("body"), list) else []
            total_rows = d.get("totalRows")
            if total_rows not in (None, 0):
                grand_total = int(total_rows)
            if not page:
                break
            for r in page:
                rid = str(r.get("loanId") or r.get("pl_bloan_id") or r.get("bloan_id") or "")
                if rid and rid not in seen:
                    seen.add(rid)
                    rows.append(r)
            if grand_total is not None and len(seen) >= grand_total:
                break
            if len(page) < limit:
                break
        log.info("emi_loans: %d loan rows of grand total %s (host %s)",
                 len(rows), grand_total, C.API_BASE)
        return rows

    def emi_loan_status(self, loan_id: object) -> dict:
        """Per-loan EMI status detail (delayed days, paid/pending per loan).
        Best-effort: returns {} on any failure — the caller keeps going."""
        try:
            d = self._get(C.API_BASE, f"investor/emi/loanEMIStatus/{loan_id}")
            if isinstance(d, dict):
                return d
        except Exception:  # noqa: BLE001
            pass
        return {}

    def delayed_loan_status(self) -> dict:
        """Delayed-loan overview (the 'denial mode' time buckets). Best-effort."""
        try:
            d = self._get(C.API_BASE, "investor/getDelayedLoanStatus?isFilterApply=")
            if isinstance(d, dict):
                return d
        except Exception:  # noqa: BLE001
            pass
        return {}

    def lending_overview(self) -> dict:
        """BEST-EFFORT lending summary (total lent, interest received/pending,
        EMI buckets) for the portfolio digest. The exact overview endpoint is
        not HAR-captured, so this probes a short list of candidate paths and
        parses whatever comes back; returns {} when none respond, and the
        digest then falls back to wallet-only. Never raises."""
        candidates = (
            "investor/lendingOverview",
            "investor/accountOverview",
            "investor/overview",
            "investor/lenderDashboard",
        )
        for path in candidates:
            try:
                d = self._get(C.OPEN_LOANS_HOST, path, timeout=20)
                data = d.get("data", d) if isinstance(d, dict) else {}
                if isinstance(data, dict) and data:
                    log.info("lending_overview: %s -> %s", path, sorted(data.keys()))
                    return data
            except Exception:  # noqa: BLE001
                continue
        log.info("lending_overview: no candidate endpoint responded — digest uses wallet only")
        return {}

    def _overview_amounts(self, overview: dict) -> dict:
        """Pull the common lending-summary numbers out of whatever the overview
        endpoint returned (field names vary; accept several spellings)."""
        def pick(*keys: str) -> float:
            for k in keys:
                if overview.get(k) is not None:
                    return to_float(overview.get(k))
            return 0.0

        total_lent = pick("totalAmountLent", "totalLentAmount", "amountLent",
                          "totalInvestedAmount", "totalPrincipalInvested",
                          "totalAmountInvested")
        interest_recv = pick("interestReceived", "interestEarned", "totalInterestReceived",
                             "interestReceivedAmount", "totalInterestIncome")
        principal_recv = pick("principalReceived", "principalReceivedAmount",
                              "totalPrincipalReceived")
        pending = pick("totalAmountPending", "amountPending", "totalPendingAmount")
        interest_pending = pick("interestPending", "pendingInterest", "interestPendingAmount")
        borrowers = pick("totalNoBorrowers", "totalBorrowers", "noOfBorrowers",
                         "borrowerCount", "totalNumOfBorrowers")
        avg_rate = pick("averageInterestRate", "avgInterestRate", "averageRate", "avgROI")
        expected_interest = pick("expectedTotalInterestIncome", "expectedInterestIncome",
                                 "totalExpectedInterest", "expectedInterest")
        return {
            "totalLent": total_lent,
            "interestReceived": interest_recv,
            "principalReceived": principal_recv,
            "totalPending": pending,
            "interestPending": interest_pending,
            "borrowers": borrowers,
            "avgRate": avg_rate,
            "expectedInterest": expected_interest,
        }

    # ── writes (REAL MONEY) ────────────────────────────────────────────────────
    def invest(self, payload: dict) -> dict:
        """POST investor/investorNow/ (market host). payload carries transactionPin.
        Success resp: {"data":"Invested Successfully","message":"Fund added successfully."}."""
        return self._post(C.OPEN_LOANS_HOST, "investor/investorNow/", payload, no_retry=True)

    def cancel(self, loan_id: int, pin: str) -> dict:
        """POST investor/cancel/funding (LEGACY apiv1 host). Success:
        {"status":"Success","message":"Funding reversed successfully"}."""
        return self._post(
            C.API_BASE, "investor/cancel/funding",
            {"loanId": int(loan_id), "transactionPin": pin},
        )

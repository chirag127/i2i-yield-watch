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

    def __init__(self, csrf: str, sid: str, force_browser: bool = False):
        self.csrf, self.sid = csrf, sid
        self._force_browser = force_browser

    @classmethod
    def from_env(cls) -> "I2iClient":
        """Auto-login when creds present (fresh tokens => no expiry); else the
        manual I2I_CSRF_TOKEN / I2I_SESSION_ID pair if set."""
        import os

        force = bool(os.environ.get("I2I_FORCE_BROWSER", "").strip())
        email = (os.environ.get(C.LOGIN_EMAIL_ENV) or "").strip()
        pw = (os.environ.get(C.LOGIN_PASSWORD_ENV) or "").strip()
        if email and pw:
            csrf, sid = login(email, pw)
            return cls(csrf, sid, force)
        csrf = (os.environ.get("I2I_CSRF_TOKEN") or "").strip()
        sid = (os.environ.get("I2I_SESSION_ID") or "").strip()
        if csrf and sid:
            return cls(csrf, sid, force)
        raise SystemExit(
            f"no i2i auth: set {C.LOGIN_EMAIL_ENV}+{C.LOGIN_PASSWORD_ENV} "
            "(preferred, auto-login) or I2I_CSRF_TOKEN+I2I_SESSION_ID"
        )

    def _url(self, host: str, path: str) -> str:
        q = urllib.parse.urlencode({"csrf_token": self.csrf, "session_id": self.sid})
        return f"{host}/{path}?{q}"

    def _get(self, host: str, path: str, timeout: int = 40) -> dict:
        req = urllib.request.Request(self._url(host, path), headers=browser_headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def _post(self, host: str, path: str, body: dict, timeout: int = 60) -> dict:
        """Direct POST; on 502/403 OR timeout/connection error fall back to a
        browser-context POST (the listing endpoint hangs on a fragile direct
        socket in some networks — widen the trigger so the browser catches it)."""
        if self._force_browser:
            return self._browser_post(host, path, body, timeout)
        req = urllib.request.Request(
            self._url(host, path), data=json.dumps(body).encode("utf-8"),
            method="POST", headers=browser_headers(),
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (502, 403):
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
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
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
        """Idle wallet balance (Rs) = data.availableWallet (HAR-verified)."""
        try:
            d = self._get(C.OPEN_LOANS_HOST, "investor/walletAndFund", timeout=30)
            w = d.get("data", {}).get("availableWallet")
            if w is not None:
                return to_float(w)
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

    # ── writes (REAL MONEY) ────────────────────────────────────────────────────
    def invest(self, payload: dict) -> dict:
        """POST investor/investorNow/ (market host). payload carries transactionPin.
        Success resp: {"data":"Invested Successfully","message":"Fund added successfully."}."""
        return self._post(C.OPEN_LOANS_HOST, "investor/investorNow/", payload)

    def cancel(self, loan_id: int, pin: str) -> dict:
        """POST investor/cancel/funding (LEGACY apiv1 host). Success:
        {"status":"Success","message":"Funding reversed successfully"}."""
        return self._post(
            C.API_BASE, "investor/cancel/funding",
            {"loanId": int(loan_id), "transactionPin": pin},
        )

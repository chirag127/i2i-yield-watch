"""i2iFunding source. The Angular listing page fires getActiveFilteredBorrowers
XHR requests from its own browser context. Direct fetch() via page.evaluate()
is CORS-blocked (api.i2ifunding.com rejects synthetic fetches, signalling abort).
Strategy: attach a response listener BEFORE navigation so the initial page-load
XHR is captured, then navigate, wait for the first batch, then click
"Show More" to paginate until stable.

Launch is flaky here — retry the whole browser session up to 3x.
"""

from __future__ import annotations

import json
import logging
import time

log = logging.getLogger("i2i_watch")

TARGET_URL = "https://www.i2ifunding.com/borrower/listing"
API_MARKER = "getActiveFilteredBorrowers"
NAV_TIMEOUT_MS = 30000
INITIAL_XHR_WAIT_MS = 8000  # wait after navigation for the page's own XHR to fire
CLICK_WAIT_MS = 3500
MAX_SHOW_MORE = 30
MAX_RETRIES = 3
RETRY_DELAY_S = 5
LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-first-run",
    "--no-zygote",
]
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _row_id(r: dict) -> str:
    return str(r.get("pl_bloan_id") or r.get("pl_id") or "")


def _scrape_once() -> list[dict]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=LAUNCH_ARGS)
        try:
            ctx = browser.new_context(
                user_agent=USER_AGENT, viewport={"width": 1366, "height": 768}
            )

            def block(route):
                if route.request.resource_type in ("image", "media", "font", "stylesheet"):
                    return route.abort()
                return route.continue_()

            ctx.route("**/*", block)
            page = ctx.new_page()

            # Attach XHR listener BEFORE navigation so the page-1 XHR is not missed.
            # page.evaluate() fetch is CORS-blocked; we rely on the page's own XHR.
            rows_by_id: dict[str, dict] = {}

            def on_response(resp):
                if API_MARKER not in resp.url or resp.status != 200:
                    return
                try:
                    data = resp.json()
                except Exception:  # noqa: BLE001
                    return
                if not isinstance(data, list):
                    return
                for r in data:
                    rid = _row_id(r)
                    if rid and rid not in rows_by_id:
                        rows_by_id[rid] = r

            page.on("response", on_response)

            log.info("navigating to %s", TARGET_URL)
            try:
                page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            except Exception as e:  # noqa: BLE001
                log.warning("goto issue (continuing): %s", str(e)[:80])

            # Wait for the Angular app to fire its initial XHR batch.
            page.wait_for_timeout(INITIAL_XHR_WAIT_MS)
            log.info("after initial wait: %d rows captured", len(rows_by_id))

            # Paginate via Show More until stable.
            prev_total = -1
            for click in range(1, MAX_SHOW_MORE + 1):
                total = len(rows_by_id)
                if total == prev_total:
                    log.info("dataset stable at %d rows after %d clicks", total, click - 1)
                    break
                prev_total = total
                try:
                    btn = page.locator("text=Show More").first
                    if not btn.count() or not btn.is_visible():
                        log.info("no more 'Show More' at click %d", click)
                        break
                    btn.click(timeout=5000)
                    page.wait_for_timeout(CLICK_WAIT_MS)
                except Exception as e:  # noqa: BLE001
                    log.info("Show More click %d ended: %s", click, str(e)[:60])
                    break

            rows = list(rows_by_id.values())
            log.info("captured %d unique raw rows via XHR interception", len(rows))
            return rows
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass


def fetch_all_loans() -> list[dict]:
    """Return raw loan rows.

    PRIMARY: pure direct-HTTP paginated feed (client.list_loans) — no browser.
    FALLBACK (belt-and-suspenders): the Playwright XHR-interception scraper below,
    retried up to 3x. The direct path needs i2i creds (auto-login); with none, or
    on ANY direct failure (timeout/connection/HTTP), we drop straight to the
    browser scraper — which needs no auth for the public listing."""
    try:
        from ..client import I2iClient

        client = I2iClient.from_env()
        rows = client.list_loans()
        if rows:
            log.info("listing via direct HTTP: %d loans (no browser)", len(rows))
            return rows
        log.warning("direct-HTTP listing returned 0 rows; browser fallback")
    except SystemExit as e:  # no creds -> public browser scrape
        log.info("no i2i creds for direct listing (%s); browser fallback", e)
    except Exception as e:  # noqa: BLE001 — timeout/conn/HTTP -> browser fallback
        log.warning("direct-HTTP listing failed (%s); browser fallback", str(e)[:120])

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info("browser scrape attempt %d/%d", attempt, MAX_RETRIES)
            rows = _scrape_once()
            if rows:
                return rows
            last_err = ValueError("no rows captured")
            log.warning("attempt %d captured 0 rows", attempt)
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.error("attempt %d failed: %s", attempt, str(e)[:120])
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_S * attempt)
    raise RuntimeError(f"all {MAX_RETRIES} browser scrape attempts failed: {last_err}")


def load_raw_fixture(path: str) -> list[dict]:
    """Load raw rows from a JSON file (offline/testing)."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)

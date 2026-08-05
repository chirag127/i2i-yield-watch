"""i2iFunding source. The listing API (getActiveFilteredBorrowers) blocks
direct HTTP (502), but the same POST issued from INSIDE the Angular listing
page's browser context succeeds — the browser carries the right cookies,
Referer, and Origin. So: launch chromium, navigate to the listing (to set
cookies), then `page.evaluate(fetch)` the API page-by-page. If that path
fails, fall back to clicking "Show More" and capturing the page's own XHR
responses.

Launch is flaky here — retry the whole session up to 3x.
"""

from __future__ import annotations

import json
import logging
import time

log = logging.getLogger("i2i_watch")

TARGET_URL = "https://www.i2ifunding.com/borrower/listing"
API_HOST = "api.i2ifunding.com"
API_PATH = "/api/v1/getActiveFilteredBorrowers/?csrf_token=undefined&session_id=undefined"
API_MARKER = "getActiveFilteredBorrowers"
NAV_TIMEOUT_MS = 30000
SETTLE_MS = 600
PAGE_SIZE_HINT = 10
MAX_PAGES = 50
IN_BATCH = 3
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

# In-page fetch: JS runs in the browser, so `undefined` and template strings
# are valid there. Kept minimal — the server reads only pageNo + location.
_IN_PAGE_FETCH_JS = r"""
async ({ url, pageNo }) => {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 15000);
    const r = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/plain, */*',
      },
      credentials: 'include',
      body: JSON.stringify({ location: '', pageNo }),
      signal: ctrl.signal,
    });
    clearTimeout(t);
    if (!r.ok) return { __error: 'HTTP ' + r.status };
    const txt = await r.text();
    try { return JSON.parse(txt); }
    catch { return { __error: 'non-JSON' }; }
  } catch (e) { return { __error: String(e && e.message || e) }; }
}
"""


def _row_id(r: dict) -> str:
    return str(r.get("pl_bloan_id") or r.get("pl_id") or "")


def _merge(rows: list, seen: set, out: list) -> bool:
    """Add unique rows; return True if this page looks like the last one."""
    if not rows:
        return True
    for r in rows:
        rid = _row_id(r)
        if not rid or rid in seen:
            continue
        seen.add(rid)
        out.append(r)
    return len(rows) < PAGE_SIZE_HINT


def _fetch_via_api(page) -> list[dict]:
    """Primary path: paginate the API from inside the page context."""
    url = f"https://{API_HOST}{API_PATH}"
    out: list[dict] = []
    seen: set[str] = set()
    next_page = 1
    while next_page <= MAX_PAGES:
        batch = [next_page + i for i in range(IN_BATCH) if next_page + i <= MAX_PAGES]
        reached_end = False
        for pno in batch:
            res = page.evaluate(_IN_PAGE_FETCH_JS, {"url": url, "pageNo": pno})
            if not isinstance(res, list):
                err = res.get("__error") if isinstance(res, dict) else "unknown"
                log.warning("in-page fetch page %d failed: %s — treating as end", pno, err)
                reached_end = True
                break
            log.info("in-page fetch page %d: %d rows", pno, len(res))
            if _merge(res, seen, out):
                reached_end = True
                break
        if reached_end:
            break
        next_page += len(batch)
    return out


def _fetch_via_show_more(page) -> list[dict]:
    """Fallback: capture the page's own XHR responses while clicking Show More."""
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
    page.wait_for_timeout(6000)

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
    return list(rows_by_id.values())


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

            log.info("navigating to %s", TARGET_URL)
            try:
                page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            except Exception as e:  # noqa: BLE001
                log.warning("goto issue (continuing): %s", str(e)[:80])
            page.wait_for_timeout(SETTLE_MS)

            rows = _fetch_via_api(page)
            if rows:
                log.info("captured %d unique raw rows via API intercept", len(rows))
                return rows

            log.warning("API intercept empty — falling back to Show More DOM capture")
            rows = _fetch_via_show_more(page)
            log.info("captured %d unique raw rows via Show More", len(rows))
            return rows
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass


def fetch_all_loans() -> list[dict]:
    """Return raw loan rows. Retries the whole browser session up to 3x."""
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info("scrape attempt %d/%d", attempt, MAX_RETRIES)
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
    raise RuntimeError(f"all {MAX_RETRIES} scrape attempts failed: {last_err}")


def load_raw_fixture(path: str) -> list[dict]:
    """Load raw rows from a JSON file (offline/testing)."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)

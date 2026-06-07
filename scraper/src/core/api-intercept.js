// scraper/core/api-intercept.js
// Primary data source: open the i2iFunding
// borrower-listing page in a real browser to set
// the right cookies + Referer, then call the
// listing API from inside the page context with
// `page.evaluate(fetch)`.
//
// Why this is the primary path:
//   - The public REST endpoint
//     (api.i2ifunding.com/api/v1/getActiveFilteredBorrowers)
//     returns HTTP 502 to direct Node.js fetch calls
//     from our runner. The same call works fine when
//     issued from inside a browser that has visited
//     the listing page (the browser carries the
//     right cookies, Referer, and Origin).
//   - The browser-context response is a clean JSON
//     array with every field the DOM would have
//     shown — and more: usr_fname / usr_lname
//     (real name), bloan_desc (purpose narrative),
//     nature_of_work (profession), etc.
//   - No DOM parsing, no Show More loop, no fragile
//     selector maintenance.
//
// We paginate pages 1..N in parallel batches of 3
// from inside the page. The listing page's own XHR
// is irrelevant — we never wait for it.

const { chromium } = require('playwright');
const logger = require('../utils/logger');
const { buildFilterBody,
  API_HOST, API_PATH, MAX_PAGES,
} = require('./api');
const { closeBrowserSafely } = require('../browser/close-browser');

const TARGET_URL =
  'https://www.i2ifunding.com/borrower/listing';
const NAV_TIMEOUT_MS = 30000;
const IN_BATCH = 3;
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 10000;

/**
 * Run a single in-page fetch with timeout + JSON
 * parse. Returns the parsed array, or { __error }
 * for any failure.
 */
function inPageFetch(page, p) {
  return page.evaluate(
    async ({ url, body }) => {
      try {
        const ctrl = new AbortController();
        const t = setTimeout(
          () => ctrl.abort(), 15000
        );
        const r = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Accept':
              'application/json, text/plain, */*',
          },
          credentials: 'include',
          body: JSON.stringify(body),
          signal: ctrl.signal,
        });
        clearTimeout(t);
        if (!r.ok) return {
          __error: 'HTTP ' + r.status,
        };
        const t2 = await r.text();
        try {
          const parsed = JSON.parse(t2);
          return parsed;
        } catch {
          return { __error: 'non-JSON' };
        }
      } catch (e) {
        return { __error: e.message };
      }
    },
    {
      url: `https://${API_HOST}${API_PATH}`,
      body: buildFilterBody(p),
    }
  );
}

/**
 * Open one Playwright session and paginate the
 * listing API from inside the page context. Returns
 * the merged, de-duplicated list of raw loan rows.
 */
async function fetchAllLoansViaBrowser() {
  let browser = null;
  let lastError = null;

  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      logger.info(
        `Intercept attempt ${attempt}/${MAX_RETRIES}`
      );
      browser = await chromium.launch({
        headless: true,
        args: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-gpu',
          '--no-first-run',
          '--no-zygote',
        ],
      });
      const context = await browser.newContext({
        viewport: { width: 1366, height: 768 },
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; '
          + 'Win64; x64) AppleWebKit/537.36 '
          + '(KHTML, like Gecko) Chrome/124.0.0.0 '
          + 'Safari/537.36',
      });

      // Block heavy resources for speed; the API
      // call is XHR, not blocked here.
      await context.route('**/*', (route) => {
        const t = route.request().resourceType();
        if (['image', 'media', 'font', 'stylesheet']
          .includes(t)) return route.abort();
        return route.continue();
      });

      const page = await context.newPage();
      logger.info(`Navigating to ${TARGET_URL}`);
      // The listing page issues its own XHR (which
      // we ignore). We just need the visit to set
      // the cookies + Referer for our own calls.
      await page.goto(TARGET_URL, {
        waitUntil: 'domcontentloaded',
        timeout: NAV_TIMEOUT_MS,
      });
      // Tiny settle delay so cookies are committed
      // before we issue our own fetch.
      await page.waitForTimeout(500);

      const all = [];
      const seen = new Set();
      // We stop only on a truly empty page. A page
      // that returns < PAGE_SIZE_HINT rows is NOT a
      // signal that we're at the end — the actual
      // page size from i2iFunding is 8-9, not 10,
      // so the previous "< PAGE_SIZE_HINT => done"
      // heuristic caused us to miss page 2+ entirely
      // (e.g. 9 loans on page 1, more on page 2).
      const merge = (rows) => {
        if (rows.length === 0) return true;
        for (const r of rows) {
          const id = String(r.pl_bloan_id || r.pl_id
            || '');
          if (!id || seen.has(id)) continue;
          seen.add(id);
          all.push(r);
        }
        return false;
      };

      // Paginate from page 1 onward, in parallel
      // batches of 3.
      let nextPage = 1;
      while (nextPage <= MAX_PAGES) {
        const batch = [];
        for (let i = 0;
          i < IN_BATCH && nextPage + i <= MAX_PAGES;
          i++) {
          batch.push(nextPage + i);
        }
        const results = await Promise.all(
          batch.map((p) => inPageFetch(page, p))
        );
        let reachedEnd = false;
        for (let i = 0; i < results.length; i++) {
          const pno = batch[i];
          const res = results[i];
          if (!res || res.__error) {
            logger.warn(
              `In-page fetch page ${pno} failed: ${
                (res && res.__error) || 'unknown'}`
              + ' — treating as end'
            );
            reachedEnd = true;
            break;
          }
          logger.info(
            `In-page fetch page ${pno}: ${
              res.length} rows`
          );
          if (res.length === 0) {
            reachedEnd = true;
            break;
          }
          const isLast = merge(res);
          if (isLast) {
            reachedEnd = true;
            break;
          }
        }
        if (reachedEnd) break;
        nextPage += batch.length;
      }

      await closeBrowserSafely(browser);
      logger.info(
        `Intercept total unique rows: ${all.length}`
      );
      return all;

    } catch (err) {
      lastError = err;
      logger.error(
        `Intercept attempt ${attempt} failed: ${
          err.message}`
      );
      if (browser) {
        await closeBrowserSafely(browser);
      }
      if (attempt < MAX_RETRIES) {
        const delay = RETRY_DELAY_MS * attempt;
        logger.info(`Retrying in ${delay / 1000}s...`);
        await new Promise(
          (r) => setTimeout(r, delay)
        );
      }
    }
  }
  throw new Error(
    `All ${MAX_RETRIES} intercept attempts failed. `
    + `Last error: ${lastError?.message}`
  );
}

module.exports = {
  fetchAllLoansViaBrowser,
  TARGET_URL,
  IN_BATCH,
};


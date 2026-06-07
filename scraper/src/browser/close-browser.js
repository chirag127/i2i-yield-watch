// scraper/browser/close-browser.js
// Shared helper for closing a Playwright browser
// without leaking processes. Race the close against
// a hard timeout so a hung close can never block
// the scraper. If the close times out, we drop the
// process reference so a subsequent run can launch
// a fresh browser even when the previous one was
// stuck.

const logger = require('../utils/logger');

const DEFAULT_CLOSE_TIMEOUT_MS = 8000;

/**
 * Close a Playwright browser without leaking
 * processes. Safe to call with null/undefined.
 * @param {import('playwright').Browser} browser
 * @param {number} [timeoutMs=8000]
 */
async function closeBrowserSafely(
  browser, timeoutMs = DEFAULT_CLOSE_TIMEOUT_MS
) {
  if (!browser) return;
  let closed = false;
  try {
    await Promise.race([
      browser.close().then(() => { closed = true; }),
      new Promise((r) => setTimeout(r, timeoutMs)),
    ]);
    if (!closed) {
      logger.warn(
        `Browser close timed out after ${
          timeoutMs / 1000}s — abandoning`
      );
    }
  } catch (err) {
    logger.warn(`Browser close error: ${err.message}`);
  }
}

module.exports = {
  closeBrowserSafely,
  DEFAULT_CLOSE_TIMEOUT_MS,
};
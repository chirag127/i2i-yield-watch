// scraper/showmore.js
// Handles clicking the "Show More" button repeatedly
// until all loans are loaded. The i2iFunding listing
// page loads borrowers in batches via an Angular SPA.

const logger = require('../utils/logger');

/**
 * Keep clicking "Show More" until no new rows appear
 * or the safety limit is reached.
 * @param {import('playwright').Page} page
 * @returns {Promise<number>} Total clicks performed
 */
async function clickShowMoreUntilDone(page) {
  let totalClicks = 0;
  let unchangedStreak = 0;
  const MAX_CLICKS = parseInt(
    process.env.MAX_SHOW_MORE_CLICKS || '150', 10
  );
  const MAX_UNCHANGED = 3;

  while (totalClicks < MAX_CLICKS) {
    // Count current visible borrower rows
    const before = await page.$$eval(
      '.active-borrwer-list tbody tr',
      (rows) => rows.filter(
        (r) => r.querySelectorAll('td').length >= 2
      ).length
    );

    // Locate the Show More button
    const btn = await page.$(
      'button.btn.btn-warning'
    );
    if (!btn) {
      logger.info(
        'Show More button not found — stopping'
      );
      break;
    }

    // Verify button text matches "Show More"
    const btnText = await btn.innerText();
    if (!/show\s*more/i.test(btnText)) {
      logger.info(
        `Button text "${btnText}" ≠ Show More — stopping`
      );
      break;
    }

    // Verify button is visible
    const isVisible = await btn.isVisible();
    if (!isVisible) {
      logger.info(
        'Show More button not visible — stopping'
      );
      break;
    }

    // Scroll into view and click with random delay
    // for anti-detection (300–700ms)
    await btn.scrollIntoViewIfNeeded();
    await page.waitForTimeout(
      300 + Math.random() * 400
    );
    await btn.click();
    totalClicks++;

    // Wait 1.5s for initial DOM update
    await page.waitForTimeout(1500);

    // Wait up to 6.5s for new rows to appear
    try {
      await page.waitForFunction(
        (prevCount) => {
          const rows = document.querySelectorAll(
            '.active-borrwer-list tbody tr'
          );
          return rows.length > prevCount;
        },
        before,
        { timeout: 6500 }
      );
    } catch {
      // Timeout = no new rows loaded
    }

    // Count rows after click
    const after = await page.$$eval(
      '.active-borrwer-list tbody tr',
      (rows) => rows.filter(
        (r) => r.querySelectorAll('td').length >= 2
      ).length
    );

    logger.info(
      `Click ${totalClicks}: ${before} → ${after} rows`
    );

    // Track consecutive unchanged clicks
    if (after <= before) {
      unchangedStreak++;
      if (unchangedStreak >= MAX_UNCHANGED) {
        logger.info(
          `No new rows after ${MAX_UNCHANGED} ` +
          'consecutive clicks — done'
        );
        break;
      }
    } else {
      unchangedStreak = 0;
    }
  }

  logger.info(
    `Show More complete: ${totalClicks} total clicks`
  );
  return totalClicks;
}

module.exports = { clickShowMoreUntilDone };

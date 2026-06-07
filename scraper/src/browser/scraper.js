// scraper/scraper.js
// Playwright scraping logic with retry support.
// Navigates to the public i2iFunding borrower listing,
// expands all rows via Show More, then parses loans.

const { chromium } = require('playwright');
const logger = require('../utils/logger');
const { clickShowMoreUntilDone } = require('./showmore');
const { parseLoans } = require('./parser');
const { closeBrowserSafely } = require('./close-browser');

/**
 * Rotate user agents based on current timestamp
 * for basic anti-detection.
 */
const USER_AGENTS = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    + 'AppleWebKit/537.36 (KHTML, like Gecko) '
    + 'Chrome/124.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    + 'AppleWebKit/537.36 (KHTML, like Gecko) '
    + 'Chrome/124.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64; '
    + 'rv:125.0) Gecko/20100101 Firefox/125.0',
];

/**
 * Random delay utility for anti-detection.
 * @param {number} min Minimum ms
 * @param {number} max Maximum ms
 */
const randomDelay = (min = 500, max = 1500) =>
  new Promise((r) =>
    setTimeout(r, min + Math.random() * (max - min))
  );

const TARGET_URL =
  'https://www.i2ifunding.com/borrower/listing';

const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 10000;

/**
 * Scrape all loans from i2iFunding public listing.
 * Implements retry logic with exponential backoff.
 * @returns {Promise<Array>} Array of loan objects
 */
async function scrapeLoans() {
  let lastError = null;

  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    let browser = null;

    try {
      logger.info(
        `Scrape attempt ${attempt}/${MAX_RETRIES}`
      );

      // Select user agent based on timestamp
      const uaIndex = Math.floor(
        Date.now() / 1000
      ) % USER_AGENTS.length;
      const userAgent = USER_AGENTS[uaIndex];

      // Launch headless Chromium with stealth args
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
        userAgent,
        viewport: { width: 1366, height: 768 },
      });

      // Block unnecessary resources for speed
      await context.route('**/*', (route) => {
        const type =
          route.request().resourceType();
        if ([
          'image', 'media', 'font', 'stylesheet',
        ].includes(type)) {
          return route.abort();
        }
        return route.continue();
      });

      const page = await context.newPage();

      // Navigate to the public listing page
      logger.info(`Navigating to ${TARGET_URL}`);
      await page.goto(TARGET_URL, {
        waitUntil: 'domcontentloaded',
        timeout: 30000,
      });

      // Wait for the loan table to appear
      try {
        await page.waitForSelector(
          '.active-borrwer-list',
          { timeout: 30000 }
        );
        logger.info('Loan table detected');
      } catch {
        // Table might not exist if no loans
        logger.warn(
          'Loan table selector not found — '
          + 'page may have no loans or different '
          + 'structure. Continuing with visible data.'
        );
      }

      // Small delay to let Angular render
      await randomDelay(1000, 2000);

      // Click Show More until all loans are loaded
      const clicks =
        await clickShowMoreUntilDone(page);
      logger.info(
        `Show More phase: ${clicks} clicks`
      );

      // Another delay for final render
      await randomDelay(500, 1000);

      // Parse all visible loans
      const loans = await parseLoans(page);
      logger.info(
        `Parsed ${loans.length} loans successfully`
      );

      // Close browser with timeout to prevent
      // hanging on Windows
      await closeBrowserSafely(browser);
      return loans;

    } catch (err) {
      lastError = err;
      logger.error(
        `Attempt ${attempt} failed: ${err.message}`
      );

      if (browser) {
        await closeBrowserSafely(browser, 3000);
      }

      if (attempt < MAX_RETRIES) {
        const delay = RETRY_DELAY_MS * attempt;
        logger.info(
          `Retrying in ${delay / 1000}s...`
        );
        await new Promise(
          (r) => setTimeout(r, delay)
        );
      }
    }
  }

  throw new Error(
    `All ${MAX_RETRIES} scrape attempts failed. `
    + `Last error: ${lastError?.message}`
  );
}

module.exports = { scrapeLoans };

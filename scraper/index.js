// scraper/index.js
// Main entry point — full orchestration flow.
// Scrape → Compare → Archive → Save → Notify → Log

// Load .env for local development (optional)
try {
  require('dotenv').config({
    path: require('path').join(
      __dirname, '..', '.env'
    ),
  });
} catch {
  // dotenv not available in CI — that's fine,
  // GitHub Actions sets env vars directly.
}

const logger = require('./logger');
const { scrapeLoans } = require('./scraper');
const {
  loadActiveLoans,
  saveActiveLoans,
  detectNewLoans,
  detectFullyFunded,
  archiveFullyFundedLoans,
  markNotificationsSent,
  updateStats,
  appendChangelog,
} = require('./storage');
const {
  sendNotifications,
} = require('./notifier');

/**
 * Main orchestration function.
 * Runs the complete scrape-analyze-notify pipeline.
 */
async function main() {
  const runId = `run_${Date.now()}`;
  const startedAt = new Date().toISOString();
  logger.info(`Starting scrape run: ${runId}`);

  // Startup jitter for anti-detection
  // (0–30 second random delay)
  const jitter = Math.floor(
    Math.random() * 30000
  );
  logger.info(
    `Startup jitter: ${(jitter / 1000).toFixed(1)}s`
  );
  await new Promise(
    (r) => setTimeout(r, jitter)
  );

  try {
    // 1. Scrape fresh loans from i2iFunding
    const freshLoans = await scrapeLoans();
    logger.info(
      `Scraped ${freshLoans.length} loans`
    );

    // 2. Load existing active loans
    const existingLoans = loadActiveLoans();
    logger.info(
      `Existing active: ${existingLoans.length}`
    );

    // 3. Detect new loans (not previously notified)
    const newLoans = detectNewLoans(
      freshLoans, existingLoans
    );
    logger.info(
      `New loans detected: ${newLoans.length}`
    );

    // 4. Detect and archive fully funded loans
    const fullyFundedLoans = detectFullyFunded(
      freshLoans, existingLoans
    );
    if (fullyFundedLoans.length > 0) {
      archiveFullyFundedLoans(fullyFundedLoans);
      logger.info(
        `Archived ${fullyFundedLoans.length}`
        + ' fully funded loans'
      );
    }

    // 5. Save updated active loans (exclude funded)
    const activeLoans = freshLoans.filter(
      (l) => !l.isFullyFunded
    );
    saveActiveLoans(activeLoans);

    // 6. Update aggregate statistics
    updateStats(activeLoans);

    // 7. Send notifications for new loans
    let notificationResults = {};
    if (newLoans.length > 0) {
      const dashboardUrl =
        process.env.DASHBOARD_URL
        || 'https://chirag127.github.io/'
           + 'i2i-yield-watch/';

      notificationResults =
        await sendNotifications(
          newLoans,
          {
            activeCount: activeLoans.length,
          },
          dashboardUrl
        );

      // 8. Mark as notified to prevent duplicates
      markNotificationsSent(
        newLoans.map((l) => l.loanId)
      );
    } else {
      logger.info(
        'No new loans — skipping notifications'
      );
    }

    // 9. Log run to changelog
    const completedAt = new Date().toISOString();
    appendChangelog({
      runId,
      startedAt,
      completedAt,
      duration_ms:
        new Date(completedAt).getTime()
        - new Date(startedAt).getTime(),
      loansFound: freshLoans.length,
      newLoans: newLoans.length,
      loansArchived: fullyFundedLoans.length,
      errors: [],
      notificationsSent: notificationResults,
    });

    logger.info(
      `Run complete. Active: ${activeLoans.length}`
      + `, New: ${newLoans.length}`
      + `, Archived: ${fullyFundedLoans.length}`
    );

    process.exit(0);

  } catch (err) {
    logger.error(`Run failed: ${err.message}`);
    logger.error(err.stack);

    // Still try to log the failed run
    try {
      appendChangelog({
        runId,
        startedAt,
        completedAt: new Date().toISOString(),
        loansFound: 0,
        newLoans: 0,
        loansArchived: 0,
        errors: [err.message],
        notificationsSent: {},
      });
    } catch {
      // Can't log — just exit
    }

    process.exit(1);
  }
}

main();

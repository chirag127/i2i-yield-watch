// scraper/index.js
// Main entry point — full orchestration flow.
// Fetch → Transform → Compare → Archive → Save → Notify → Log

try {
  require('dotenv').config({
    path: require('path').join(
      __dirname, '..', '..', '..', '.env'
    ),
  });
} catch {
  // dotenv not available in CI — env vars set by workflow.
}

const logger = require('../utils/logger');
const { fetchAllLoans } = require('./api');
const { transformLoans } = require('./transform');
const {
  loadActiveLoans,
  saveActiveLoans,
  detectNewLoans,
  detectFullyFunded,
  archiveFullyFundedLoans,
  loadNotificationsSent,
  markNotificationsSent,
  filterUnnotified,
  updateStats,
  appendChangelog,
} = require('./storage');
const {
  sendNotifications,
  wasAnyChannelSuccessful,
} = require('../notifiers/notifier');

const JITTER_MAX_MS = parseInt(
  process.env.STARTUP_JITTER_MS || '2000', 10
);

/**
 * Fetch + transform via the pure-HTTP API.
 */
async function fetchAndTransform() {
  const raw = await fetchAllLoans();
  logger.info(`API returned ${raw.length} raw rows`);
  return transformLoans(raw);
}

/**
 * Fallback path: Playwright + DOM parsing.
 */
async function fetchAndTransformDomFallback() {
  logger.warn('Falling back to Playwright/DOM scraper');
  const { scrapeLoans } = require('../browser/scraper');
  return scrapeLoans();
}

/**
 * Main orchestration function.
 */
async function main() {
  const runId = `run_${Date.now()}`;
  const startedAt = new Date().toISOString();
  logger.setRunId(runId);
  logger.info('Starting scrape run', { runId });

  const jitter = Math.floor(
    Math.random() * Math.max(JITTER_MAX_MS, 0)
  );
  if (jitter > 0) {
    logger.info(
      `Startup jitter: ${(jitter / 1000).toFixed(1)}s`
    );
    await new Promise((r) => setTimeout(r, jitter));
  }

  const phases = {};

  try {
    const useDomOnly =
      process.env.USE_PLAYWRIGHT_FALLBACK === 'true'
      || process.env.USE_DOM_FALLBACK === 'true';

    const fetchT0 = Date.now();
    let freshLoans;
    if (useDomOnly) {
      freshLoans = await fetchAndTransformDomFallback();
    } else {
      try {
        freshLoans = await fetchAndTransform();
      } catch (apiErr) {
        logger.warn(
          `API fetch failed: ${apiErr.message} — `
          + 'falling back to Playwright/DOM'
        );
        freshLoans = await fetchAndTransformDomFallback();
      }
    }
    phases.fetch_ms = Date.now() - fetchT0;
    logger.info(`Got ${freshLoans.length} loans`);

    const storageT0 = Date.now();
    const existingLoans = await loadActiveLoans();
    const notifiedIds = await loadNotificationsSent();

    const newLoans = detectNewLoans(
      freshLoans, existingLoans, notifiedIds
    );

    const fullyFundedLoans = detectFullyFunded(
      freshLoans, existingLoans
    );
    const archivedCount = fullyFundedLoans.length > 0
      ? await archiveFullyFundedLoans(fullyFundedLoans)
      : 0;

    const activeLoans = freshLoans.filter(
      (l) => !l.isFullyFunded
    );
    await saveActiveLoans(activeLoans);
    await updateStats(activeLoans, archivedCount);
    phases.storage_ms = Date.now() - storageT0;

    const rateThreshold = parseFloat(
      process.env.NOTIFY_RATE_THRESHOLD
      || process.env.MEDIUM_PRIORITY_RATE_THRESHOLD
      || '50'
    );

    const qualifyingLoans = activeLoans.filter(
      (loan) => {
        const rate = parseFloat(loan.interestRate);
        return !isNaN(rate) && rate > rateThreshold;
      }
    );

    const newQualifying = filterUnnotified(
      qualifyingLoans, notifiedIds
    );

    logger.info(
      `Qualifying (rate > ${rateThreshold}%):`
      + ` ${qualifyingLoans.length},`
      + ` new (unseen loanId):`
      + ` ${newQualifying.length}`
    );

    let notificationResults = {
      telegram: false,
      email: false,
      discord: false,
      ntfy: false,
    };

    const notifyT0 = Date.now();
    if (newQualifying.length > 0) {
      const dashboardUrl =
        process.env.DASHBOARD_URL
        || 'https://chirag127.github.io/'
           + 'i2i-yield-watch/';

      notificationResults = await sendNotifications(
        newQualifying,
        {
          activeCount: activeLoans.length,
          qualifyingCount: qualifyingLoans.length,
          newQualifyingCount: newQualifying.length,
          rateThreshold,
        },
        dashboardUrl,
        { rateThreshold }
      );

      if (wasAnyChannelSuccessful(notificationResults)) {
        await markNotificationsSent(
          newQualifying.map((l) => String(l.loanId))
        );
      } else {
        logger.warn(
          'No channel reported a successful send — '
          + 'loanIds NOT marked, future run can retry'
        );
      }
    } else {
      logger.info(
        'No new high-yield loans — '
        + 'skipping notifications'
      );
    }
    phases.notify_ms = Date.now() - notifyT0;

    const completedAt = new Date().toISOString();
    const durationMs =
      new Date(completedAt).getTime()
      - new Date(startedAt).getTime();

    await appendChangelog({
      runId,
      startedAt,
      completedAt,
      duration_ms: durationMs,
      phases,
      loansFound: freshLoans.length,
      newLoans: newLoans.length,
      loansArchived: archivedCount,
      qualifyingLoans: qualifyingLoans.length,
      newQualifyingLoans: newQualifying.length,
      rateThreshold,
      errors: [],
      notificationsSent: notificationResults,
    });

    logger.info('Run complete', {
      active: activeLoans.length,
      new: newLoans.length,
      archived: archivedCount,
      qualifying: qualifyingLoans.length,
      notified: newQualifying.length,
      duration_ms: durationMs,
      phases,
    });

    process.exit(0);

  } catch (err) {
    logger.error(`Run failed: ${err.message}`);
    logger.error(err.stack);

    try {
      await appendChangelog({
        runId,
        startedAt,
        completedAt: new Date().toISOString(),
        loansFound: 0,
        newLoans: 0,
        loansArchived: 0,
        qualifyingLoans: 0,
        newQualifyingLoans: 0,
        rateThreshold: parseFloat(
          process.env.NOTIFY_RATE_THRESHOLD || '50'
        ),
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

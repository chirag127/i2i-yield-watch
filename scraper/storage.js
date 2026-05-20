// scraper/storage.js
// JSON read/write logic for all data persistence.
// Uses flat JSON files — no database required.
// Data directory is at ../data/ relative to scraper.

const fs = require('fs');
const path = require('path');
const logger = require('./logger');

const DATA_DIR = path.join(__dirname, '..', 'data');
const ARCHIVE_DIR = path.join(DATA_DIR, 'archive');

const ACTIVE_FILE = path.join(
  DATA_DIR, 'active_loans.json'
);
const NOTIFICATIONS_FILE = path.join(
  DATA_DIR, 'notifications_sent.json'
);
const STATS_FILE = path.join(
  DATA_DIR, 'stats.json'
);
const CHANGELOG_FILE = path.join(
  DATA_DIR, 'changelog.json'
);

const MAX_CHANGELOG_ENTRIES = 200;

/**
 * Safely read and parse a JSON file.
 * Returns fallback on any error.
 * @param {string} filePath
 * @param {*} fallback
 * @returns {*}
 */
function safeReadJSON(filePath, fallback) {
  try {
    if (!fs.existsSync(filePath)) {
      return fallback;
    }
    const raw = fs.readFileSync(filePath, 'utf-8');
    if (!raw.trim()) return fallback;
    return JSON.parse(raw);
  } catch (err) {
    logger.warn(
      `Failed to read ${filePath}: ${err.message}`
      + ' — using fallback'
    );
    return fallback;
  }
}

/**
 * Safely write JSON to a file, creating parent
 * directories if needed.
 * @param {string} filePath
 * @param {*} data
 */
function safeWriteJSON(filePath, data) {
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  fs.writeFileSync(
    filePath,
    JSON.stringify(data, null, 2),
    'utf-8'
  );
}

/**
 * Load currently active loans from disk.
 * @returns {Array} Loan objects
 */
function loadActiveLoans() {
  const data = safeReadJSON(ACTIVE_FILE, {
    loans: [],
  });
  return data.loans || [];
}

/**
 * Save active loans to disk with metadata.
 * @param {Array} loansArray
 */
function saveActiveLoans(loansArray) {
  const highPriority = loansArray.filter(
    (l) => l.priority === 'VERY_HIGH'
  ).length;

  const data = {
    generatedAt: new Date().toISOString(),
    scrapeRunId: `run_${Date.now()}`,
    totalActive: loansArray.length,
    highPriority,
    loans: loansArray,
  };

  safeWriteJSON(ACTIVE_FILE, data);
  logger.info(
    `Saved ${loansArray.length} active loans `
    + `(${highPriority} high priority)`
  );
}

/**
 * Detect loans that are new and haven't been
 * notified about yet. Checks both existing active
 * loans AND notifications_sent.json.
 * @param {Array} freshLoans
 * @param {Array} existingLoans
 * @returns {Array} Loans not previously notified
 */
function detectNewLoans(freshLoans, existingLoans) {
  const notified = loadNotificationsSent();
  const notifiedSet = new Set(
    notified.notifiedLoanIds || []
  );

  // Also consider existing active loan IDs as
  // "known" (even if not yet notified, they
  // existed before this run)
  const existingIds = new Set(
    existingLoans.map((l) => l.loanId)
  );

  const newLoans = freshLoans.filter(
    (loan) =>
      !notifiedSet.has(loan.loanId)
      && !existingIds.has(loan.loanId)
  );

  return newLoans;
}

/**
 * Detect loans that have become fully funded since
 * the last scrape. A loan is fully funded if:
 * 1. It was active before but now has
 *    fundedPercent >= 100, OR
 * 2. It was active before but disappeared from
 *    the fresh listing
 * @param {Array} freshLoans
 * @param {Array} existingLoans
 * @returns {Array} Loans to archive
 */
function detectFullyFunded(
  freshLoans, existingLoans
) {
  const freshIds = new Set(
    freshLoans.map((l) => l.loanId)
  );

  const toArchive = [];

  // Loans that existed before but are gone now
  for (const existing of existingLoans) {
    if (!freshIds.has(existing.loanId)) {
      toArchive.push({
        ...existing,
        archivedReason: 'disappeared_from_listing',
      });
    }
  }

  // Loans that are now fully funded in fresh data
  for (const fresh of freshLoans) {
    if (fresh.isFullyFunded) {
      toArchive.push({
        ...fresh,
        archivedReason: 'fully_funded',
      });
    }
  }

  return toArchive;
}

/**
 * Archive fully funded loans to monthly file.
 * Appends to data/archive/fully_funded_YYYY_MM.json.
 * @param {Array} loansToArchive
 */
function archiveFullyFundedLoans(loansToArchive) {
  if (!loansToArchive.length) return;

  if (!fs.existsSync(ARCHIVE_DIR)) {
    fs.mkdirSync(ARCHIVE_DIR, { recursive: true });
  }

  const now = new Date();
  const month = now.toISOString().slice(0, 7);
  const archiveFile = path.join(
    ARCHIVE_DIR,
    `fully_funded_${month.replace('-', '_')}.json`
  );

  const existing = safeReadJSON(archiveFile, {
    month,
    archivedLoans: [],
  });

  const archivedAt = now.toISOString();
  const newArchived = loansToArchive.map((loan) => ({
    ...loan,
    archivedAt,
  }));

  existing.archivedLoans.push(...newArchived);
  safeWriteJSON(archiveFile, existing);

  logger.info(
    `Archived ${loansToArchive.length} loans to `
    + path.basename(archiveFile)
  );
}

/**
 * Load the notifications sent tracking file.
 * @returns {object}
 */
function loadNotificationsSent() {
  return safeReadJSON(NOTIFICATIONS_FILE, {
    notifiedLoanIds: [],
    lastUpdated: null,
  });
}

/**
 * Mark loan IDs as notified so they won't
 * trigger duplicate alerts.
 * @param {Array<string>} loanIds
 */
function markNotificationsSent(loanIds) {
  const data = loadNotificationsSent();
  const idSet = new Set(
    data.notifiedLoanIds || []
  );

  for (const id of loanIds) {
    idSet.add(id);
  }

  data.notifiedLoanIds = Array.from(idSet);
  data.lastUpdated = new Date().toISOString();

  safeWriteJSON(NOTIFICATIONS_FILE, data);
  logger.info(
    `Marked ${loanIds.length} loans as notified `
    + `(total tracked: ${data.notifiedLoanIds.length})`
  );
}

/**
 * Recalculate and write aggregate statistics.
 * @param {Array} activeLoans
 */
function updateStats(activeLoans) {
  const existingStats = safeReadJSON(STATS_FILE, {
    totalScrapedAllTime: 0,
    totalArchived: 0,
  });

  const avgRate = activeLoans.length > 0
    ? activeLoans.reduce(
        (s, l) => s + (l.interestRate || 0), 0
      ) / activeLoans.length
    : 0;

  const avgScore = activeLoans.length > 0
    ? activeLoans.reduce(
        (s, l) => s + (l.yieldScore || 0), 0
      ) / activeLoans.length
    : 0;

  // Count by product
  const byProduct = {};
  for (const loan of activeLoans) {
    const key = loan.product || 'Unknown';
    byProduct[key] = (byProduct[key] || 0) + 1;
  }

  // Count by priority
  const byPriority = {
    VERY_HIGH: 0,
    MEDIUM: 0,
    LOW: 0,
  };
  for (const loan of activeLoans) {
    const p = loan.priority || 'LOW';
    byPriority[p] = (byPriority[p] || 0) + 1;
  }

  const stats = {
    lastUpdated: new Date().toISOString(),
    totalScrapedAllTime: Math.max(
      existingStats.totalScrapedAllTime || 0,
      activeLoans.length
    ),
    currentActive: activeLoans.length,
    totalArchived:
      existingStats.totalArchived || 0,
    avgInterestRate: parseFloat(
      avgRate.toFixed(2)
    ),
    avgYieldScore: parseFloat(
      avgScore.toFixed(2)
    ),
    highPriorityCount: byPriority.VERY_HIGH,
    byProduct,
    byPriority,
  };

  safeWriteJSON(STATS_FILE, stats);
  logger.info('Stats updated');
}

/**
 * Append a run summary to the changelog.
 * Keeps only the latest MAX_CHANGELOG_ENTRIES.
 * @param {object} runSummary
 */
function appendChangelog(runSummary) {
  const data = safeReadJSON(CHANGELOG_FILE, {
    runs: [],
  });

  data.runs.push(runSummary);

  // Trim to max entries
  if (data.runs.length > MAX_CHANGELOG_ENTRIES) {
    data.runs = data.runs.slice(
      data.runs.length - MAX_CHANGELOG_ENTRIES
    );
  }

  safeWriteJSON(CHANGELOG_FILE, data);
  logger.info(
    `Changelog updated `
    + `(${data.runs.length} entries)`
  );
}

module.exports = {
  loadActiveLoans,
  saveActiveLoans,
  detectNewLoans,
  detectFullyFunded,
  archiveFullyFundedLoans,
  loadNotificationsSent,
  markNotificationsSent,
  updateStats,
  appendChangelog,
};

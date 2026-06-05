// scraper/storage.js
// Firestore-backed persistence layer.
// All I/O is async (Firestore SDK is async).
// Service account JSON loaded from FIREBASE_SA_PATH or ./i2i-yield-watch-sa.json
// at the project root (encrypted via git-crypt).

const fs = require('fs');
const path = require('path');
const admin = require('firebase-admin');
const logger = require('../utils/logger');

const ROOT = path.resolve(__dirname, '..', '..', '..');
const SA_PATH = process.env.FIREBASE_SA_PATH
  ? (path.isAbsolute(process.env.FIREBASE_SA_PATH)
      ? process.env.FIREBASE_SA_PATH
      : path.join(ROOT, process.env.FIREBASE_SA_PATH))
  : path.join(ROOT, 'i2i-yield-watch-sa.json');

let app = null;
let db = null;

function init() {
  if (app) return;
  if (!fs.existsSync(SA_PATH)) {
    throw new Error(
      `Firebase service account not found at ${SA_PATH}. `
      + 'Set FIREBASE_SA_PATH or place the key at '
      + 'the project root.'
    );
  }
  const sa = JSON.parse(fs.readFileSync(SA_PATH, 'utf8'));
  app = admin.initializeApp(
    { credential: admin.credential.cert(sa) },
    'i2i-yield-watch'
  );
  db = admin.firestore(app);
  db.settings({ ignoreUndefinedProperties: true });
}

function ts() {
  return admin.firestore.FieldValue.serverTimestamp();
}

/**
 * Load all currently active loans from /loans where status='active'.
 * @returns {Promise<Array>}
 */
async function loadActiveLoans() {
  init();
  const snap = await db.collection('loans')
    .where('status', '==', 'active')
    .get();
  return snap.docs.map((d) => ({ ...d.data(), loanId: d.id }));
}

/**
 * Upsert the current active set.
 * Each loan doc is keyed by loanId with status='active', yearMonth=null.
 * @param {Array} loansArray
 */
async function saveActiveLoans(loansArray) {
  init();
  if (!loansArray.length) {
    logger.info('No active loans to save');
    return;
  }
  const batchSize = 500;
  for (let i = 0; i < loansArray.length; i += batchSize) {
    const batch = db.batch();
    const slice = loansArray.slice(i, i + batchSize);
    for (const loan of slice) {
      const ref = db.collection('loans').doc(String(loan.loanId));
      batch.set(ref, {
        ...loan,
        status: 'active',
        yearMonth: null,
        updatedAt: ts(),
      }, { merge: true });
    }
    await batch.commit();
  }
  const highPriority = loansArray.filter(
    (l) => l.priority === 'VERY_HIGH'
  ).length;
  logger.info(
    `Saved ${loansArray.length} active loans `
    + `(${highPriority} high priority)`
  );
}

/**
 * Detect loans that are new and haven't been notified about yet.
 * A loan is "new" if its loanId is not in the notified set AND
 * not already in the existing active loans set.
 * @param {Array} freshLoans
 * @param {Array} existingLoans
 * @param {Set<string>} notifiedIds
 * @returns {Array}
 */
function detectNewLoans(
  freshLoans, existingLoans, notifiedIds
) {
  const notified = notifiedIds instanceof Set
    ? notifiedIds
    : new Set((notifiedIds || []).map(String));
  const existing = new Set(
    existingLoans.map((l) => String(l.loanId))
  );
  return freshLoans.filter(
    (loan) => !notified.has(String(loan.loanId))
      && !existing.has(String(loan.loanId))
  );
}

/**
 * Detect loans that have become fully funded since the last scrape.
 * - "disappeared_from_listing": was active, no longer in fresh set
 * - "fully_funded": in fresh set with isFullyFunded=true
 * @param {Array} freshLoans
 * @param {Array} existingLoans
 * @returns {Array}
 */
function detectFullyFunded(freshLoans, existingLoans) {
  const freshIds = new Set(
    freshLoans.map((l) => String(l.loanId))
  );
  const toArchive = [];
  for (const existing of existingLoans) {
    if (!freshIds.has(String(existing.loanId))) {
      toArchive.push({
        ...existing,
        archivedReason: 'disappeared_from_listing',
      });
    }
  }
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
 * Mark loans as archived in Firestore.
 * Groups by current YYYY-MM month; updates each loan's status + yearMonth.
 * Refreshes /meta/archiveIndex in a single write.
 * @param {Array} loansToArchive
 * @returns {Promise<number>} count of newly archived loans
 */
async function archiveFullyFundedLoans(loansToArchive) {
  if (!loansToArchive.length) return 0;
  init();

  const now = new Date();
  const month = now.toISOString().slice(0, 7);
  const archivedAt = now.toISOString();

  // Filter out already-archived by querying current state
  const docs = await db.getAll(
    ...loansToArchive.map(
      (l) => db.collection('loans').doc(String(l.loanId))
    )
  );
  const newArchived = [];
  const batch = db.batch();
  for (let i = 0; i < loansToArchive.length; i++) {
    const loan = loansToArchive[i];
    const current = docs[i].data();
    if (current && current.status === 'archived') continue;
    const ref = db.collection('loans').doc(String(loan.loanId));
    batch.set(ref, {
      ...loan,
      status: 'archived',
      yearMonth: month,
      archivedAt,
      archivedReason: loan.archivedReason || 'fully_funded',
      updatedAt: ts(),
    }, { merge: true });
    newArchived.push(loan);
  }
  if (newArchived.length) await batch.commit();

  // Update archive index
  if (newArchived.length) {
    const indexRef = db.collection('meta').doc('archiveIndex');
    const indexDoc = await indexRef.get();
    const indexData = indexDoc.exists
      ? indexDoc.data()
      : { generatedAt: null, files: [] };
    const files = (indexData.files || []).slice();
    const existing = files.find((f) => f.month === month);
    if (existing) {
      existing.count = (existing.count || 0) + newArchived.length;
      existing.lastArchivedAt = archivedAt;
    } else {
      files.push({
        month,
        count: newArchived.length,
        lastArchivedAt: archivedAt,
      });
    }
    files.sort((a, b) => a.month.localeCompare(b.month));
    indexData.files = files;
    indexData.generatedAt = archivedAt;
    await indexRef.set({ ...indexData, updatedAt: ts() });
  }

  logger.info(
    `Archived ${newArchived.length} loans to ${month}`
  );
  return newArchived.length;
}

/**
 * Re-scan /loans where status='archived', group by yearMonth,
 * rebuild /meta/archiveIndex. Used by the dashboard if needed.
 * @returns {Promise<object>} the index
 */
async function rebuildArchiveIndex() {
  init();
  const snap = await db.collection('loans')
    .where('status', '==', 'archived')
    .get();
  const byMonth = new Map();
  for (const doc of snap.docs) {
    const data = doc.data();
    const m = data.yearMonth;
    if (!m) continue;
    if (!byMonth.has(m)) byMonth.set(m, []);
    byMonth.get(m).push(data);
  }
  const files = Array.from(byMonth.entries())
    .map(([month, loans]) => {
      const lastArchivedAt = loans.reduce(
        (acc, l) => (
          l.archivedAt && l.archivedAt > acc
            ? l.archivedAt : acc
        ),
        loans[0].archivedAt || ''
      );
      return { month, count: loans.length, lastArchivedAt };
    })
    .sort((a, b) => a.month.localeCompare(b.month));
  const indexData = {
    generatedAt: new Date().toISOString(),
    files,
  };
  await db.collection('meta').doc('archiveIndex').set({
    ...indexData,
    updatedAt: ts(),
  });
  return indexData;
}

/**
 * Load the archive index manifest from /meta/archiveIndex.
 * @returns {Promise<object>}
 */
async function loadArchiveIndex() {
  init();
  const doc = await db.collection('meta').doc('archiveIndex').get();
  return doc.exists
    ? doc.data()
    : { generatedAt: null, files: [] };
}

/**
 * Load the set of loanIds that have been notified.
 * @returns {Promise<Set<string>>}
 */
async function loadNotificationsSent() {
  init();
  const snap = await db.collection('notifications').get();
  return new Set(snap.docs.map((d) => d.id));
}

/**
 * Mark loan IDs as notified. Idempotent (overwrites).
 * @param {Array<string>} loanIds
 */
async function markNotificationsSent(loanIds) {
  if (!loanIds || loanIds.length === 0) return;
  init();
  const batchSize = 500;
  for (let i = 0; i < loanIds.length; i += batchSize) {
    const batch = db.batch();
    const slice = loanIds.slice(i, i + batchSize);
    for (const id of slice) {
      if (id == null) continue;
      const ref = db.collection('notifications').doc(String(id));
      batch.set(ref, {
        loanId: String(id),
        notifiedAt: ts(),
      }, { merge: true });
    }
    await batch.commit();
  }
  logger.info(
    `Marked ${loanIds.length} loans as notified`
  );
}

/**
 * Filter qualifying loans to those not yet notified.
 * @param {Array} qualifyingLoans
 * @param {Set<string>} notifiedIds
 * @returns {Array}
 */
function filterUnnotified(qualifyingLoans, notifiedIds) {
  const seen = notifiedIds instanceof Set
    ? notifiedIds
    : new Set((notifiedIds || []).map(String));
  return qualifyingLoans.filter(
    (l) => !seen.has(String(l.loanId))
  );
}

/**
 * Recalculate and write aggregate statistics to /stats/current.
 * @param {Array} activeLoans
 * @param {number} [newlyArchived=0]
 */
async function updateStats(activeLoans, newlyArchived = 0) {
  init();
  const existingDoc = await db.collection('stats').doc('current').get();
  const existing = existingDoc.exists ? existingDoc.data() : {};

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

  const byProduct = {};
  for (const loan of activeLoans) {
    const key = loan.product || 'Unknown';
    byProduct[key] = (byProduct[key] || 0) + 1;
  }

  const byPriority = { VERY_HIGH: 0, MEDIUM: 0, LOW: 0 };
  for (const loan of activeLoans) {
    const p = loan.priority || 'LOW';
    byPriority[p] = (byPriority[p] || 0) + 1;
  }

  const stats = {
    lastUpdated: new Date().toISOString(),
    totalScrapedAllTime: Math.max(
      existing.totalScrapedAllTime || 0,
      activeLoans.length
    ),
    currentActive: activeLoans.length,
    totalArchived:
      (existing.totalArchived || 0) + newlyArchived,
    avgInterestRate: parseFloat(avgRate.toFixed(2)),
    avgYieldScore: parseFloat(avgScore.toFixed(2)),
    highPriorityCount: byPriority.VERY_HIGH,
    byProduct,
    byPriority,
  };

  await db.collection('stats').doc('current').set({
    ...stats,
    updatedAt: ts(),
  });
  logger.info('Stats updated');
}

/**
 * Append a run summary to /runs/{runId}.
 * @param {object} runSummary
 */
async function appendChangelog(runSummary) {
  init();
  if (!runSummary.runId) {
    runSummary.runId = `run_${Date.now()}`;
  }
  await db.collection('runs').doc(runSummary.runId).set({
    ...runSummary,
    updatedAt: ts(),
  });
  logger.info(
    `Run logged: ${runSummary.runId}`
  );
}

module.exports = {
  loadActiveLoans,
  saveActiveLoans,
  detectNewLoans,
  detectFullyFunded,
  archiveFullyFundedLoans,
  rebuildArchiveIndex,
  loadArchiveIndex,
  loadNotificationsSent,
  markNotificationsSent,
  filterUnnotified,
  updateStats,
  appendChangelog,
  ts,
  init,
};

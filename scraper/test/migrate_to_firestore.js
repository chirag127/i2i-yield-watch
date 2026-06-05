const path = require('path');
const fs = require('fs');
const admin = require('firebase-admin');

const ROOT = path.resolve(__dirname, '..', '..');
const DATA_DIR = path.join(ROOT, 'data');
const SA_PATH = process.env.FIREBASE_SA_PATH
  || path.join(ROOT, 'i2i-yield-watch-sa.json');

function readJson(name) {
  const p = path.join(DATA_DIR, name);
  if (!fs.existsSync(p)) return null;
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}

async function batchSet(docRef, data) {
  return docRef.set(data, { merge: false });
}

async function migrateLoans(db, loans, status, yearMonth, now) {
  let n = 0;
  for (const loan of loans) {
    const id = String(loan.loanId || loan.pl_bloan_id);
    if (!id) continue;
    await batchSet(db.collection('loans').doc(id), {
      ...loan,
      status,
      yearMonth,
      updatedAt: now,
    });
    n++;
  }
  return n;
}

async function main() {
  const sa = require(SA_PATH);
  admin.initializeApp({ credential: admin.credential.cert(sa) });
  const db = admin.firestore();

  const now = admin.firestore.FieldValue.serverTimestamp();
  const active = readJson('active_loans.json');
  const stats = readJson('stats.json');
  const notifications = readJson('notifications_sent.json');
  const changelog = readJson('changelog.json');
  const archiveIndex = readJson(path.join('archive', 'index.json'));
  const archiveMay = readJson(path.join('archive', 'fully_funded_2026_05.json'));
  const archiveJun = readJson(path.join('archive', 'fully_funded_2026_06.json'));

  console.log('Loaded local data files.');

  let total = 0;

  if (active && Array.isArray(active.loans)) {
    const n = await migrateLoans(db, active.loans, 'active', null, now);
    console.log(`Migrated ${n} active loans.`);
    total += n;
  }

  if (archiveMay && Array.isArray(archiveMay.archivedLoans)) {
    const n = await migrateLoans(db, archiveMay.archivedLoans, 'archived', '2026-05', now);
    console.log(`Migrated ${n} archived loans (2026-05).`);
    total += n;
  }

  if (archiveJun && Array.isArray(archiveJun.archivedLoans)) {
    const n = await migrateLoans(db, archiveJun.archivedLoans, 'archived', '2026-06', now);
    console.log(`Migrated ${n} archived loans (2026-06).`);
    total += n;
  }

  if (notifications && Array.isArray(notifications.notifiedLoanIds)) {
    let n = 0;
    for (const id of notifications.notifiedLoanIds) {
      await batchSet(db.collection('notifications').doc(String(id)), {
        loanId: String(id),
        notifiedAt: now,
      });
      n++;
    }
    console.log(`Migrated ${n} notification dedup records.`);
    total += n;
  }

  if (changelog && Array.isArray(changelog.runs)) {
    let n = 0;
    for (const run of changelog.runs) {
      if (!run.runId) continue;
      await batchSet(db.collection('runs').doc(run.runId), run);
      n++;
    }
    console.log(`Migrated ${n} changelog runs.`);
    total += n;
  }

  if (stats) {
    await batchSet(db.collection('stats').doc('current'), {
      ...stats,
      updatedAt: now,
    });
    console.log('Migrated stats/current.');
    total++;
  }

  if (archiveIndex) {
    await batchSet(db.collection('meta').doc('archiveIndex'), {
      ...archiveIndex,
      updatedAt: now,
    });
    console.log('Migrated meta/archiveIndex.');
    total++;
  }

  await batchSet(db.collection('meta').doc('scraper'), {
    runId: `migration_${Date.now()}`,
    lastUpdatedAt: now,
    source: 'migrate_to_firestore.js',
  });
  total++;

  console.log(`Total writes: ${total}`);
  console.log('Done.');
  process.exit(0);
}

main().catch((e) => {
  console.error('FATAL', e);
  process.exit(1);
});

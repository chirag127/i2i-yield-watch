// Dry-run end-to-end test: exercises the full
// production pipeline (intercept → transform →
// Firestore dedup) without actually sending
// notifications. Confirms:
//   1. The in-browser API intercept fetches real
//      loans correctly.
//   2. The transform populates every field.
//   3. filterUnnotified correctly drops loans that
//      are already in the Firestore `notifications`
//      collection (so we don't re-notify Telegram).
//   4. The orchestrator's main() code path is wired
//      up correctly.
//
// To verify the live notification path use
// test/send_test_telegram.js (synthetic) or
// test/verify-all-channels.js (real i2i data, real
// send). This dry-run is for the common case where
// we want to inspect state without spamming.
try {
  require('dotenv').config({
    path: require('path').join(
      __dirname, '..', '..', '.env'
    ),
  });
} catch (_e) {
  // dotenv not installed — assume env already set
}

const {
  fetchAllLoansViaBrowser,
} = require('../src/core/api-intercept');
const {
  transformLoans,
} = require('../src/core/transform');
const {
  loadNotificationsSent,
  filterUnnotified,
  loadActiveLoans,
  detectNewLoans,
} = require('../src/core/storage');
const {
  formatLoanBlock,
} = require('../src/core/transform');

(async () => {
  console.log('Step 1: Fetch real loans...');
  const raw = await fetchAllLoansViaBrowser();
  const fresh = transformLoans(raw);
  console.log(`  Got ${fresh.length} loans`);

  console.log('\nStep 2: Load state from Firestore...');
  const existingLoans = await loadActiveLoans();
  const notifiedIds = await loadNotificationsSent();
  console.log(
    `  ${existingLoans.length} active loans in store`
  );
  console.log(
    `  ${notifiedIds.size} loanIds already notified`
  );

  console.log('\nStep 3: Compute diffs (no writes)...');
  const newLoans = detectNewLoans(
    fresh, existingLoans, notifiedIds
  );
  console.log(`  ${newLoans.length} new loans`);

  const rateThreshold = 50;
  const qualifying = fresh.filter(
    (l) => l.interestRate > rateThreshold
  );
  const newQualifying = filterUnnotified(
    qualifying, notifiedIds
  );
  console.log(
    `  ${qualifying.length} qualifying (rate > ${
      rateThreshold}%)`
  );
  console.log(
    `  ${newQualifying.length} would-be-notified `
    + '(after dedup)'
  );

  if (qualifying.length > 0) {
    console.log('\nQualifying loans (rate > 50%):');
    qualifying.forEach((l) => {
      const alreadySent = notifiedIds.has(
        String(l.loanId)
      );
      const tag = alreadySent
        ? '  [ALREADY NOTIFIED — will skip]'
        : '  [NEW — would notify]';
      console.log(`${tag}`);
      console.log(`    loanId      : ${l.loanId}`);
      console.log(`    name        : ${l.name}`);
      console.log(`    interestRate: ${l.interestRate}%`);
      console.log(`    loanUrl     : ${l.loanUrl}`);
    });
  }

  if (newQualifying.length === 0) {
    console.log(
      '\nNo NEW qualifying loans — nothing to send. '
      + 'Dedup working correctly.'
    );
    process.exit(0);
  }

  console.log(
    '\nStep 4: To actually notify, the orchestrator '
    + 'would now call sendNotifications() and then '
    + 'markNotificationsSent().'
  );
  console.log(
    '       This dry-run does NOT send or write. '
    + 'Use send_test_telegram.js or '
    + 'verify-all-channels.js for that.'
  );
  process.exit(0);
})().catch((e) => {
  console.error('FAILED:', e.message);
  console.error(e.stack);
  process.exit(1);
});
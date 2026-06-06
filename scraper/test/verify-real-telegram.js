// End-to-end smoke test: fetch real loans via the
// browser intercept, transform them, then send a
// real Telegram message with the first real
// qualifying loan. Confirms:
//   1. New api-intercept primary path works
//   2. The canonical public-profile URL is built
//   3. Telegram receives a clickable rate link
//      pointing at a real i2iFunding loan
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
  sendTelegram,
  formatLoanLine,
} = require('../src/notifiers/telegram');

(async () => {
  console.log('Step 1: Fetch real loans via browser intercept...');
  const raw = await fetchAllLoansViaBrowser();
  const loans = transformLoans(raw);
  console.log(`Got ${loans.length} loans, ${loans.filter((l) => l.interestRate > 50).length} qualifying`);

  // Pick the highest-rate qualifying loan (most
  // visually interesting for the test).
  const qualifying = loans
    .filter((l) => l.interestRate != null && l.interestRate > 50)
    .sort((a, b) => b.interestRate - a.interestRate);
  if (qualifying.length === 0) {
    console.log('No qualifying loans — using highest-rate loan anyway');
  }
  const top = qualifying[0] || loans.sort((a, b) => (b.interestRate || 0) - (a.interestRate || 0))[0];
  if (!top) {
    console.log('No loans at all — aborting');
    process.exit(1);
  }

  console.log('\nStep 2: Real loan to send:');
  console.log('  loanId      :', top.loanId);
  console.log('  borrowerRef :', top.borrowerRef);
  console.log('  name        :', top.name);
  console.log('  interestRate:', top.interestRate);
  console.log('  loanUrl     :', top.loanUrl);
  console.log('  yieldScore  :', top.yieldScore);

  console.log('\nStep 3: Telegram HTML preview:');
  console.log(formatLoanLine(top));

  console.log('\nStep 4: Sending real Telegram message...');
  const ok = await sendTelegram(
    [top],
    { activeCount: loans.length, qualifyingCount: loans.filter((l) => l.interestRate > 50).length },
    'https://chirag127.github.io/i2i-yield-watch/',
    { rateThreshold: 50 }
  );
  console.log(ok ? 'SENT OK' : 'SEND FAILED');
  process.exit(ok ? 0 : 1);
})().catch((e) => {
  console.error('FAILED:', e.message);
  console.error(e.stack);
  process.exit(1);
});

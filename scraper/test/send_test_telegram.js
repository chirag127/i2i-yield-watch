// One-off real Telegram test — sends a single
// hand-built test loan through the production
// sendTelegram() pipeline to verify end-to-end.
//
//   node test/send_test_telegram.js
//
// Loads TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID from
// the encrypted .env at the project root (same
// dotenv config as the production scraper).
try {
  require('dotenv').config({
    path: require('path').join(
      __dirname, '..', '..', '.env'
    ),
  });
} catch {
  // dotenv not installed — assume env already set
}

const {
  sendTelegram,
} = require('../src/notifiers/telegram');
const {
  formatLoanBlock,
  buildLoanUrl,
} = require('../src/core/transform');

const testId = 'TEST-' + Date.now();
const testBorrower = '999999';
const loan = {
  loanId: testId,
  borrowerRef: testBorrower,
  name: 'TEST BORROWER',
  age: 30,
  location: 'Bengaluru',
  residenceType: 'Own House',
  purpose: 'This is a pipeline verification message.',
  creditScore: '750',
  creditScoreNumeric: 750,
  riskCategory: 'X',
  interestRate: 75.22,
  tenure: '12 Months',
  product: 'Test Loan',
  madeLiveOn: new Date().toISOString(),
  employmentType: 'Salaried',
  monthlyIncome: 60000,
  professionName: 'Engineer',
  businessName: null,
  loanAmount: 100000,
  amountFunded: 50000,
  amountLeft: 50000,
  fundedPercent: 50,
  fundingRemaining: 50,
  isFullyFunded: false,
  // Use the same canonical public-profile URL the
  // real scraper constructs, so the first-line link
  // matches what a real notification looks like.
  loanUrl: buildLoanUrl(testBorrower, testId),
  yieldScore: 38.19,
  priority: 'VERY_HIGH',
};

console.log('--- TEST LOAN ---');
const lines = formatLoanBlock(loan);
lines.forEach((l, i) => console.log(`${i+1}. ${l}`));
console.log('--- TELEGRAM HTML (first line clickable) ---');
const {
  formatLoanLine,
} = require('../src/notifiers/telegram');
console.log(formatLoanLine(loan));
console.log('-----------------');

(async () => {
  const ok = await sendTelegram(
    [loan],
    { activeCount: 1, qualifyingCount: 1 },
    'https://chirag127.github.io/i2i-yield-watch/',
    { rateThreshold: 50 }
  );
  console.log(ok ? 'SENT OK' : 'SEND FAILED');
  process.exit(ok ? 0 : 1);
})();


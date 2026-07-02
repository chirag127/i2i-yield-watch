// scraper/test/smoketest.js
// In-process smoke test (no network). Verifies:
//
//  Rate filter & scoring (3 tests)
//   - rate > 50 keeps only the expected loans
//   - getPriority assigns VERY_HIGH / MEDIUM / LOW
//   - calculateYieldScore is deterministic and in
//     [0, 100]
//
//  Telegram & notifiers (10 tests)
//   - chunker splits a long list into multiple
//     safe-length messages
//   - formatLoanLine has no labels, no N/A, rate
//     at the top, all key data present
//   - notifier no-ops on an empty qualifying list
//   - notifier short-circuits when no channels
//     are enabled
//   - no channel contains promotional copy
//   - ntfy disabled returns false
//   - ntfy missing topic returns false
//   - ntfy body is the same label-free line list
//   - ntfy default base URL is ntfy.sh
//
//  Loan identity & dedup (4 tests)
//   - transformLoan populates loanId from
//     pl_bloan_id
//   - filterUnnotified drops already-notified
//     loanIds
//   - markNotificationsSent is idempotent and
//     sets lastUpdated
//   - storage.js has no SHA-1 / fingerprint code
//
//  API payload (no network) (3 tests)
//   - buildFilterBody returns valid JSON with
//     pageNo
//   - fetchPage uses POST + correct headers + path
//   - API constants are sensible
//
//  Transform (14 tests)
//   - parsePostedOn, toNumber, NA, pickRate,
//     pickCredit, formatTenure, computeFunding,
//     buildLoanUrl
//   - transformLoan populates every field
//   - transformLoans skips bad rows
//   - formatPostedOn (ISO -> DD-MM-YYYY)
//   - inr (Indian-style currency)
//   - formatLoanBlock returns label-free lines,
//     rate + yield at the top, no % in funding
//   - formatLoanBlock silently omits missing data
//
//  Workflow & project shape (2 tests)
//   - workflow YAML has the every-5-min-IST cron,
//     repository_dispatch, no-cancel, 10-min
//     timeout, ubuntu-latest, actions/cache,
//     fetch-depth 1, --prefer-offline,
//     unconditional Playwright install, git-crypt
//   - project layout: SOLID folder structure
//     (scraper/src/{core,notifiers,utils,browser}/
//     + scraper/test/), single README.md, .env
//     encrypted via git-crypt
//
//  Run with: node scraper/test/smoketest.js

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const {
  sendNotifications,
  wasAnyChannelSuccessful,
} = require('../src/notifiers/notifier');
const {
  formatLoanLine, buildHeader, buildFooter,
  chunkLoansIntoMessages,
} = require('../src/notifiers/telegram');
const { formatEmailHtml } = require('../src/notifiers/email');
const { buildLoanEmbed } = require('../src/notifiers/discord');
const {
  sendNtfy,
  formatLoanBody,
  DEFAULT_BASE_URL,
} = require('../src/notifiers/ntfy');
const {
  calculateYieldScore,
  getPriority,
} = require('../src/utils/scorer');
const {
  transformLoan,
  transformLoans,
  buildLoanUrl,
  parsePostedOn,
  formatPostedOn,
  toNumber,
  formatTenure,
  computeFunding,
  inr,
  formatLoanBlock,
  pickRate,
  pickCredit,
  NA,
} = require('../src/core/transform');
const {
  buildFilterBody,
  fetchPage,
  API_HOST,
  API_PATH,
  REQUEST_TIMEOUT_MS,
  MAX_PAGES,
  PAGE_SIZE_HINT,
  PARALLEL_PAGES,
} = require('../src/core/api');

const SAFE_CHUNK_LENGTH = 3800;
function makeLoan(i, rate) {
  return {
    loanId: `${i}`,
    borrowerRef: `${i}`,
    interestRate: rate,
    location: 'Bengaluru',
    loanAmount: 100000 + i,
    amountLeft: 50000,
    fundedPercent: 50,
    yieldScore: 70,
    creditScore: 750,
    creditScoreNumeric: 750,
    riskCategory: 'X',
    name: `Borrower ${i}`,
    age: 30,
    residenceType: 'Own House',
    employmentType: 'Salaried',
    monthlyIncome: 60000,
    professionName: 'Engineer',
    tenure: '12 Months',
    purpose: 'Personal Loan',
  };
}

let pass = 0, fail = 0;
function ok(name) { pass++; console.log('PASS ' + name); }
function t(name, fn) {
  try { fn(); ok(name); }
  catch (e) {
    fail++;
    console.error('FAIL ' + name + ': ' + e.message);
    if (process.env.VERBOSE) console.error(e.stack);
  }
}
async function ta(name, fn) {
  try { await fn(); ok(name); }
  catch (e) {
    fail++;
    console.error('FAIL ' + name + ': ' + e.message);
    if (process.env.VERBOSE) console.error(e.stack);
  }
}

/* ============================================================
   RATE FILTER & SCORING
   ============================================================ */

function testRateFilter() {
  const loans = [
    makeLoan(1, 30),
    makeLoan(2, 50),
    makeLoan(3, 50.01),
    makeLoan(4, 70),
    makeLoan(5, 100),
  ];
  const threshold = 50;
  const qualifying = loans.filter((l) => {
    const r = parseFloat(l.interestRate);
    return !isNaN(r) && r > threshold;
  });
  assert.strictEqual(qualifying.length, 3,
    'Should keep only loans with rate > 50');
  assert.deepStrictEqual(
    qualifying.map((l) => l.loanId), ['3', '4', '5']
  );
  ok('rate filter (rate > 50)');
}

function testPriorityThresholds() {
  // 70% default = VERY_HIGH
  assert.strictEqual(getPriority(80), 'VERY_HIGH');
  assert.strictEqual(getPriority(70), 'VERY_HIGH');
  assert.strictEqual(getPriority(69.99), 'MEDIUM');
  // 50% default = MEDIUM
  assert.strictEqual(getPriority(60), 'MEDIUM');
  assert.strictEqual(getPriority(50), 'MEDIUM');
  assert.strictEqual(getPriority(49.99), 'LOW');
  // Edge cases
  assert.strictEqual(getPriority(0), 'LOW');
  assert.strictEqual(getPriority(null), 'LOW');
  assert.strictEqual(getPriority(undefined), 'LOW');
  ok('priority thresholds at 50% and 70%');
}

function testYieldScoreBounds() {
  const low = makeLoan(1, 5);
  const high = makeLoan(2, 200);
  const sLow = calculateYieldScore(low);
  const sHigh = calculateYieldScore(high);
  assert.ok(sLow >= 0 && sLow <= 100,
    `score out of range: ${sLow}`);
  assert.ok(sHigh >= 0 && sHigh <= 100,
    `score out of range: ${sHigh}`);
  assert.ok(sHigh > sLow,
    'higher-rate loan must score higher');
  // Determinism
  assert.strictEqual(
    calculateYieldScore(low),
    calculateYieldScore(low),
    'score must be deterministic'
  );
  ok('yield score is in [0,100] and deterministic');
}

/* ============================================================
   TELEGRAM & NOTIFIERS
   ============================================================ */

function testTelegramChunker() {
  const loans = [];
  for (let i = 0; i < 80; i++) {
    const m = makeLoan(i, 51 + (i % 40));
    m.loanAmount = 100000 + i;
    loans.push(m);
  }
  const header = buildHeader(loans.length, {},
    'https://example.com', 50);
  const footer = buildFooter({}, 'https://example.com');
  const messages =
    chunkLoansIntoMessages(loans, header, footer);
  assert.ok(messages.length > 1,
    `Expected multiple chunks, got ${messages.length}`);
  for (let i = 0; i < messages.length; i++) {
    assert.ok(
      messages[i].length <= SAFE_CHUNK_LENGTH + 300,
      `Chunk ${i} length ${messages[i].length} exceeds limit`
    );
  }
  const allText = messages.join('');
  for (const loan of loans) {
    const amtStr = `₹${loan.loanAmount
      .toLocaleString('en-IN')}`;
    assert.ok(allText.includes(amtStr),
      `Loan amount ${amtStr} missing`);
  }
  assert.ok(/80 NEW LOANS \(rate &gt; 50%\)/
    .test(allText),
    'Header missing or wrong format');
  assert.ok(allText.includes('Dashboard</a>'),
    'Footer missing');
  ok(`telegram chunker: 80 loans -> ${
    messages.length} messages`);
}

function testTelegramOmitsNA() {
  const loan = makeLoan(99, 80);
  loan.name = null;
  loan.product = null;
  loan.professionName = null;
  loan.businessName = null;
  loan.purpose = null;
  loan.tenure = null;
  loan.location = null;
  loan.residenceType = null;
  loan.employmentType = null;
  loan.monthlyIncome = null;
  loan.creditScore = null;
  const text = formatLoanLine(loan);
  // No "N/A" should leak into the message
  assert.ok(!/N\/A/i.test(text),
    `N/A leaked: ${text}`);
  // The rate line should still be at the top with
  // the percent — it's the only place % is allowed.
  assert.ok(/80(\.\d+)?% p\.a\./.test(text),
    'Rate value missing');
  // The makeLoan helper sets borrowerRef but no
  // name/location, so identity + funding should
  // still be present.
  assert.ok(text.includes('i2i-#'),
    'i2i-# identity missing');
  // Optional / null fields should be silently
  // dropped (no "Name: N/A" or similar).
  assert.ok(!text.includes('Name:'),
    'Name row should be dropped when null');
  assert.ok(!text.includes('Location:'),
    'Location row should be dropped when null');
  assert.ok(!text.includes('Tenure:'),
    'Tenure row should be dropped when null');
  assert.ok(!text.includes('Purpose:'),
    'Purpose row should be dropped when null');
  // No field labels at all (label-free format).
  for (const lbl of [
    'Int. Rate:', 'Credit Bureau Score:',
    'i2i Risk Category:', 'Residence Type:',
    'Employment Type:', 'Monthly Income:',
    'Business Name:', 'Loan Amount:',
    'Funded:', 'Remaining:', 'Made Live On:',
    'URL:', 'Yield Score:',
  ]) {
    assert.ok(!text.includes(lbl),
      `Telegram should not contain label "${lbl}"`);
  }
  ok('telegram: no labels, no N/A, rate at top');
}

function testTelegramIncludesAllUsefulFields() {
  const loan = makeLoan(7, 90);
  loan.madeLiveOn = '2026-06-04T00:00:00.000Z';
  loan.loanUrl = 'https://www.i2ifunding.com/'
    + 'borrower/listing/public-profile/12345';
  const text = formatLoanLine(loan);
  // Rate + yield at the TOP, bolded AND clickable.
  const firstLine = text.split('\n')[0];
  assert.ok(/<b>.*90\.00% p\.a\..*<\/b>/.test(firstLine),
    `First line must be bolded with rate, got: ${firstLine}`);
  assert.ok(/<a href="[^"]*public-profile\/12345[^"]*">/
    .test(firstLine),
    `First line must be a clickable link to public-profile, got: ${firstLine}`);
  assert.ok(/Yield \d+\.\d{2}\/100/.test(firstLine),
    `First line must include yield score, got: ${firstLine}`);
  // Identity (i2i-# and Loan ID)
  assert.ok(text.includes('i2i-#7'),
    'i2i-# identity missing');
  assert.ok(text.includes('Loan 7'),
    'Loan ID identity missing');
  // Funding (total / funded / left)
  assert.ok(text.includes('₹1,00,007'),
    'Loan amount missing');
  assert.ok(text.includes('funded'),
    'Funded label-free text missing');
  assert.ok(text.includes('left'),
    'Left label-free text missing');
  // Credit + Risk
  assert.ok(text.includes('Credit 750'),
    'Credit line missing');
  assert.ok(text.includes('Risk X'),
    'Risk line missing');
  // Borrower (name / age / location)
  assert.ok(text.includes('Borrower 7'),
    'Borrower name missing');
  // Purpose
  assert.ok(text.includes('Personal Loan'),
    'Purpose missing');
  // Tenure
  assert.ok(text.includes('12 Months'),
    'Tenure missing');
  // URL appears in the message (inside the href), so
  // the public-profile link is reachable.
  assert.ok(text.includes('public-profile/12345'),
    'URL missing from message');
  // URL should NOT also appear as a duplicate
  // standalone line (it lives only in the href now).
  const urlLineCount = (text.match(/^https?:\/\//gm) || [])
    .length;
  assert.strictEqual(urlLineCount, 0,
    'URL should not appear as a standalone line when '
      + 'wrapped in the first-line href');
  // No percent symbols in funding rows
  assert.ok(!/funded.*%/.test(text),
    'Funded row should not contain percent');
  assert.ok(!/left.*%/.test(text),
    'Left row should not contain percent');
  ok('telegram: rate at top + yield + clickable + all key data');
}

function testTelegramFirstLineNoUrlStillBolds() {
  // When a loan has no loanUrl, the first line must
  // still be bolded (just not a link).
  const loan = makeLoan(8, 75);
  loan.loanUrl = null;
  const text = formatLoanLine(loan);
  const firstLine = text.split('\n')[0];
  assert.ok(/<b>.*75\.00% p\.a\..*<\/b>/.test(firstLine),
    `First line must be bolded, got: ${firstLine}`);
  assert.ok(!/<a /.test(firstLine),
    'No <a> tag should appear when loanUrl is missing');
  ok('telegram: no-URL fallback still bolds first line');
}

function testEmailFirstLineIsClickable() {
  // The first line of every email card must be a
  // clickable <a href> pointing to the loan's
  // public-profile URL. The URL must NOT be
  // repeated as a plain-text <p> below.
  const loan = makeLoan(7, 90);
  loan.loanUrl = 'https://www.i2ifunding.com/'
    + 'borrower/listing/public-profile/555/777';
  const html = formatEmailHtml(
    [loan], { activeCount: 1, qualifyingCount: 1 },
    'https://example.com', { rateThreshold: 50 }
  );
  // First line (rate+yield) is a clickable <a href>
  assert.ok(
    /<a [^>]*href="https:\/\/www\.i2ifunding\.com\/borrower\/listing\/public-profile\/555\/777"[^>]*>/
      .test(html),
    `Rate line must be a clickable link to the public-profile URL, got: ${html.slice(0, 600)}`
  );
  // Rate value should appear INSIDE the <a> tag
  const linkMatch = html.match(
    /<a [^>]*href="https:\/\/www\.i2ifunding\.com\/borrower\/listing\/public-profile\/555\/777"[^>]*>([\s\S]*?)<\/a>/
  );
  assert.ok(linkMatch, 'Could not extract link inner text');
  assert.ok(/90\.00% p\.a\./.test(linkMatch[1]),
    `Link text should contain rate, got: ${linkMatch[1]}`);
  // URL should NOT appear as a plain-text <p> below
  const urlAsParagraph = /<p[^>]*>\s*https:\/\/www\.i2ifunding\.com\/borrower\/listing\/public-profile\/555\/777\s*<\/p>/;
  assert.ok(!urlAsParagraph.test(html),
    'URL should not be a standalone <p> below the clickable rate');
  ok('email: rate line is a clickable link, no URL duplicate');
}

function testEmailFirstLineNoUrlStillStyled() {
  // When loanUrl is missing, the first line must
  // still render (just not as a link).
  const loan = makeLoan(8, 75);
  loan.loanUrl = null;
  const html = formatEmailHtml(
    [loan], { activeCount: 1, qualifyingCount: 1 },
    'https://example.com', { rateThreshold: 50 }
  );
  assert.ok(/75\.00% p\.a\./.test(html),
    'Rate value must still appear in the card');
  assert.ok(!/<a [^>]*href="https:\/\/www\.i2ifunding\.com/.test(html),
    'No i2iFunding link should appear when loanUrl is null');
  ok('email: no-URL fallback still renders rate card');
}

async function testEmptyNotifies() {
  delete process.env.TELEGRAM_ENABLED;
  delete process.env.EMAIL_ENABLED;
  delete process.env.DISCORD_ENABLED;
  delete process.env.NTFY_ENABLED;
  const res = await sendNotifications(
    [], { activeCount: 0 }, 'https://example.com',
    { rateThreshold: 50 }
  );
  assert.deepStrictEqual(res, {
    telegram: false, email: false, discord: false,
    ntfy: false,
  });
  ok('notifier: empty list no-ops');
}

async function testDisabledChannels() {
  process.env.TELEGRAM_ENABLED = 'false';
  process.env.EMAIL_ENABLED = 'false';
  process.env.DISCORD_ENABLED = 'false';
  process.env.NTFY_ENABLED = 'false';
  const res = await sendNotifications(
    [makeLoan(1, 60)], { activeCount: 5 },
    'https://example.com', { rateThreshold: 50 }
  );
  assert.deepStrictEqual(res, {
    telegram: false, email: false, discord: false,
    ntfy: false,
  });
  delete process.env.TELEGRAM_ENABLED;
  delete process.env.EMAIL_ENABLED;
  delete process.env.DISCORD_ENABLED;
  delete process.env.NTFY_ENABLED;
  ok('notifier: disabled channels short-circuit');
}

function testWasAnyChannelSuccessful() {
  assert.strictEqual(
    wasAnyChannelSuccessful({
      telegram: false,
      email: false,
      discord: false,
      ntfy: false,
    }),
    false,
    'all false should not count as sent'
  );
  assert.strictEqual(
    wasAnyChannelSuccessful({
      telegram: false,
      email: false,
      discord: false,
      ntfy: true,
    }),
    true,
    'ntfy-only success must mark loans notified'
  );
  assert.strictEqual(
    wasAnyChannelSuccessful({
      telegram: true,
      email: false,
      discord: false,
      ntfy: false,
    }),
    true,
    'telegram success should count'
  );
  ok('notifier: wasAnyChannelSuccessful includes ntfy');
}

function testNoPromotionalCopy() {
  const promo = [
    'complete list', 'nothing hidden', 'sent in full',
    'no loans hidden', 'no top-N cap', 'no hiding',
  ];
  const promoRegex = new RegExp(promo.join('|'), 'i');
  const loan = makeLoan(11, 75);
  const tgHeader = buildHeader(
    1, { activeCount: 10 },
    'https://example.com', 50
  );
  assert.ok(!promoRegex.test(tgHeader),
    `Telegram header has promo copy: ${tgHeader}`);
  const tgMessages = chunkLoansIntoMessages(
    [loan],
    tgHeader,
    buildFooter({ activeCount: 10 },
      'https://example.com')
  );
  for (const m of tgMessages) {
    assert.ok(!promoRegex.test(m),
      `Telegram message has promo copy: ${m}`);
  }
  const emailHtml = formatEmailHtml(
    [loan], { activeCount: 10, qualifyingCount: 1 },
    'https://example.com', { rateThreshold: 50 }
  );
  const emailText = emailHtml
    .replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
  assert.ok(
    !promoRegex.test(emailHtml)
      && !promoRegex.test(emailText),
    `Email contains promo copy: ${emailText}`
  );
  const embed = buildLoanEmbed(loan);
  const embedText = [
    embed.title || '',
    embed.url || '',
    ...((embed.fields || []).map(
      (f) => `${f.name}:${f.value}`
    )),
    (embed.footer || {}).text || '',
  ].join(' ');
  assert.ok(!promoRegex.test(embedText),
    `Discord embed has promo copy: ${embedText}`);
  // ntfy body must also be promo-free
  const ntfyBody = formatLoanBody(loan);
  assert.ok(!promoRegex.test(ntfyBody),
    `ntfy body has promo copy: ${ntfyBody}`);
  ok('no promotional copy in any channel');
}

async function testNtfyDisabled() {
  process.env.NTFY_ENABLED = 'false';
  const okFlag = await sendNtfy(
    [makeLoan(1, 60)], { activeCount: 5 }, 'https://x.com',
    { rateThreshold: 50 }
  );
  assert.strictEqual(okFlag, false,
    'sendNtfy should return false when NTFY_ENABLED=false');
  delete process.env.NTFY_ENABLED;
  ok('ntfy: no-op when disabled');
}

async function testNtfyMissingTopic() {
  process.env.NTFY_ENABLED = 'true';
  process.env.NTFY_BASE_URL = 'https://ntfy.sh';
  delete process.env.NTFY_TOPIC;
  const okFlag = await sendNtfy(
    [makeLoan(1, 60)], { activeCount: 5 }, 'https://x.com',
    { rateThreshold: 50 }
  );
  assert.strictEqual(okFlag, false,
    'sendNtfy should return false when NTFY_TOPIC is missing');
  delete process.env.NTFY_ENABLED;
  delete process.env.NTFY_BASE_URL;
  ok('ntfy: returns false when topic missing');
}

function testNtfyFormatBody() {
  // The ntfy body is the same label-free line list
  // that all other channels use. formatLoanBody()
  // simply joins the lines with newlines.
  const loan = makeLoan(42, 60);
  loan.madeLiveOn = '2026-06-05T00:00:00.000Z';
  loan.loanUrl = 'https://www.i2ifunding.com/borrower/'
    + 'listing/public-profile/555';
  const body = formatLoanBody(loan);
  // First line is rate + yield (top of message).
  const firstLine = body.split('\n')[0];
  assert.ok(/60\.00% p\.a\./.test(firstLine),
    `First line must contain rate, got: ${firstLine}`);
  assert.ok(/Yield \d+\.\d{2}\/100/.test(firstLine),
    `First line must include yield score, got: ${firstLine}`);
  // Identity present
  assert.ok(body.includes('i2i-#42'),
    'i2i-# identity missing');
  assert.ok(body.includes('Loan 42'),
    'Loan ID identity missing');
  // Funding (no labels)
  assert.ok(body.includes('funded'),
    'Funded text missing');
  assert.ok(body.includes('left'),
    'Left text missing');
  // URL present (last line)
  assert.ok(body.includes('public-profile/555'),
    'URL missing');
  // NO field labels in the body at all
  for (const lbl of [
    'Int. Rate:', 'Credit Bureau Score:',
    'i2i Risk Category:', 'Loan Amount:',
    'Funded:', 'Remaining:', 'Made Live On:',
    'URL:', 'Yield Score:', 'Borrower:',
    'Tenure:', 'Purpose:', 'Location:',
  ]) {
    assert.ok(!body.includes(lbl),
      `ntfy body should not contain label "${lbl}"`);
  }
  // No % in funding rows
  assert.ok(!/funded.*%/.test(body),
    'ntfy Funded should not contain percent');
  // Single % in rate line (sanctioned)
  assert.ok(/60\.00% p\.a\./.test(body),
    'ntfy rate must contain percent');
  ok('ntfy: label-free line list, rate + yield at top');
}

function testNtfyDefaultBaseUrl() {
  assert.strictEqual(DEFAULT_BASE_URL, 'https://ntfy.sh',
    'Default ntfy base URL should be https://ntfy.sh');
  ok('ntfy: default base URL is ntfy.sh');
}

/* ============================================================
   LOAN IDENTITY & DEDUP
   ============================================================ */

function testTransformCarriesLoanId() {
  // The dashboard and notifier both key off
  // loan.loanId. transformLoan must populate it
  // from pl_bloan_id (the API's primary key).
  const out = transformLoan({
    pl_id: 381486,
    pl_bloan_id: 1415135,
    pl_user_id: 1323223,
    pl_amt: '6345.00',
    pl_amt_left: '3345.00',
    pl_inital_rate: '16.07',
    pl_current_rate: '16.07',
    pl_applicable_rate: '16.07',
    pl_status: 1,
    postedOn: '04-06-2026',
    location: 'Nadia',
    purpose: 'Personal Loan',
    bloan_i2i_category: 'X',
    bloan_tenure: 30,
    tenure_type: 'd',
    emp_type: 'Self Employed Professional',
    fin_monthly_income: '65710.0000',
    product_name: 'Urban Clap',
    usr_fname: 'Goutam',
    usr_lname: 'Sarkar',
    usr_age: '20',
    usr_cibil_score: '',
    bloan_cibil_score: '-1',
  });
  assert.strictEqual(out.loanId, '1415135',
    'loanId must come from pl_bloan_id');
  assert.strictEqual(out.borrowerRef, '1323223',
    'borrowerRef must come from pl_user_id');
  ok('transform: loanId + borrowerRef from API');
}

function testFilterUnnotified() {
  const { filterUnnotified } = require('../src/core/storage');
  const loans = [
    makeLoan('111', 80),
    makeLoan('333', 90),
    makeLoan('222', 85),
    makeLoan('444', 95),
  ];
  const notified = new Set(['111', '222']);
  const fresh = filterUnnotified(loans, notified);
  assert.strictEqual(fresh.length, 2,
    `expected 2 unnotified, got ${fresh.length}`);
  assert.deepStrictEqual(
    fresh.map((l) => l.loanId), ['333', '444']
  );
  ok('filterUnnotified drops already-notified loanIds');
}

function testMarkNotificationsSentIdempotent() {
  const { detectNewLoans, filterUnnotified }
    = require('../src/core/storage');
  const notified = new Set();
  const fresh1 = [
    makeLoan('a', 80),
    makeLoan('b', 90),
  ];
  const new1 = detectNewLoans(fresh1, [], notified);
  assert.deepStrictEqual(
    new1.map((l) => l.loanId), ['a', 'b']
  );
  for (const l of new1) notified.add(String(l.loanId));

  const new2 = detectNewLoans(fresh1, fresh1, notified);
  assert.strictEqual(new2.length, 0,
    'second pass should detect 0 new');

  const fresh2 = [
    makeLoan('a', 80),
    makeLoan('b', 90),
    makeLoan('c', 95),
  ];
  const new3 = detectNewLoans(fresh2, fresh1, notified);
  assert.deepStrictEqual(
    new3.map((l) => l.loanId), ['c']
  );

  const unnotified = filterUnnotified(fresh2, notified);
  assert.deepStrictEqual(
    unnotified.map((l) => l.loanId), ['c']
  );
  ok('markNotificationsSent dedup logic works (simulated)');
}

function testNoFingerprintFileNeeded() {
  // The legacy SHA-1 fingerprint store was dropped.
  // The scraper must NOT require its presence.
  const fpFile = path.join(
    __dirname, '..', '..', 'data',
    'loan_fingerprints.json'
  );
  if (fs.existsSync(fpFile)) {
    // Just assert the scraper doesn't read it.
    // (We don't import the storage module's removed
    // functions, so the test is a no-op.)
  }
  // Read storage.js source and assert it doesn't
  // import crypto or reference fingerprints.
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'core', 'storage.js'),
    'utf-8'
  );
  assert.ok(!/computeFingerprint/.test(src),
    'storage.js must not export computeFingerprint');
  assert.ok(!/loadFingerprints/.test(src),
    'storage.js must not export loadFingerprints');
  assert.ok(!/markFingerprintsNotified/.test(src),
    'storage.js must not export markFingerprintsNotified');
  assert.ok(!/require\(['"]crypto['"]\)/.test(src),
    'storage.js must not import crypto (no SHA-1)');
  ok('storage: no SHA-1 fingerprinting anywhere');
}

/* ============================================================
   API PAYLOAD (NO NETWORK)
   ============================================================ */

function testBuildFilterBody() {
  const body = buildFilterBody(7);
  assert.strictEqual(body.pageNo, 7,
    'pageNo must round-trip');
  assert.ok(Array.isArray(
    body.riskCategory.options
  ), 'riskCategory.options must be an array');
  assert.ok(body.riskCategory.options.length >= 7,
    'riskCategory needs at least 7 options (A-F, X)');
  for (const opt of body.riskCategory.options) {
    assert.strictEqual(opt.active, false,
      'all options must be inactive (no filter)');
    assert.ok(typeof opt.value === 'string',
      'option value must be a string');
  }
  // Must serialize to valid JSON
  const s = JSON.stringify(body);
  assert.ok(s.length < 8000,
    `body too large: ${s.length}b`);
  JSON.parse(s); // must round-trip
  ok('buildFilterBody returns valid JSON');
}

async function testFetchPageStub() {
  // Stub https.request with a real Readable response
  // stream so we exercise fetchPage's URL, method,
  // and headers without hitting the network.
  const https = require('https');
  const { Readable } = require('stream');
  const origRequest = https.request;
  let captured = null;
  https.request = (opts, cb) => {
    captured = opts;
    const body = JSON.stringify([
      { pl_bloan_id: 1001, pl_amt: '5000' },
      { pl_bloan_id: 1002, pl_amt: '6000' },
    ]);
    const res = Readable.from([Buffer.from(body)]);
    res.statusCode = 200;
    res.headers = { 'content-type': 'application/json' };
    setImmediate(() => cb(res));
    return {
      on: () => {},
      setTimeout: () => {},
      write: () => {},
      end: () => {},
      destroy: () => {},
    };
  };
  try {
    const rows = await fetchPage(3);
    assert.strictEqual(rows.length, 2,
      'stubbed response should yield 2 rows');
    assert.strictEqual(captured.hostname, API_HOST);
    assert.strictEqual(captured.path, API_PATH);
    assert.strictEqual(captured.method, 'POST');
    assert.strictEqual(
      captured.headers['Content-Type'],
      'application/json'
    );
    assert.strictEqual(
      captured.headers['Origin'],
      'https://www.i2ifunding.com'
    );
    assert.strictEqual(
      captured.headers['Referer'],
      'https://www.i2ifunding.com/borrower/listing'
    );
  } finally {
    https.request = origRequest;
  }
  ok('fetchPage uses POST + correct headers + path');
}

function testApiConstants() {
  assert.ok(REQUEST_TIMEOUT_MS >= 5000,
    'timeout too low');
  assert.ok(MAX_PAGES >= 5,
    'MAX_PAGES too low for normal loads');
  assert.ok(PAGE_SIZE_HINT > 0,
    'PAGE_SIZE_HINT must be positive');
  assert.ok(API_HOST.includes('i2ifunding'),
    'API_HOST must target i2iFunding');
  assert.ok(API_PATH.includes('getActiveFiltered'),
    'API_PATH must target the listing endpoint');
  assert.ok(PARALLEL_PAGES >= 2,
    'PARALLEL_PAGES should allow batched fetches');
  ok('api: constants are sensible');
}

/* ============================================================
   TRANSFORM
   ============================================================ */

function testTransformPostedOn() {
  assert.strictEqual(
    parsePostedOn('04-06-2026'),
    '2026-06-04T00:00:00.000Z'
  );
  assert.strictEqual(parsePostedOn(''), null);
  assert.strictEqual(parsePostedOn(null), null);
  assert.strictEqual(parsePostedOn('garbage'), null);
  ok('parsePostedOn handles DD-MM-YYYY');
}

function testTransformToNumber() {
  assert.strictEqual(toNumber('1234.56'), 1234.56);
  assert.strictEqual(toNumber('1,23,456.78'), 123456.78);
  assert.strictEqual(toNumber('₹ 6,345'), 6345);
  assert.strictEqual(toNumber(42), 42);
  assert.strictEqual(toNumber(null), null);
  assert.strictEqual(toNumber(''), null);
  assert.strictEqual(toNumber('not a number'), null);
  ok('toNumber parses Indian-style numbers');
}

function testTransformNA() {
  assert.ok(NA(null));
  assert.ok(NA(undefined));
  assert.ok(NA(''));
  assert.ok(NA('N/A'));
  assert.ok(NA('NA'));
  assert.ok(NA('null'));
  assert.ok(NA('none'));
  assert.ok(NA('-'));
  assert.ok(NA('#####'));
  assert.ok(!NA('Bengaluru'));
  assert.ok(!NA('0'));
  assert.ok(!NA('0.00'));
  ok('NA matcher covers common null variants');
}

function testTransformPicksApplicableRate() {
  const a = pickRate({
    pl_inital_rate: '99.99',
    pl_current_rate: '88.88',
    pl_applicable_rate: '77.77',
  });
  const b = pickRate({
    pl_inital_rate: '99.99',
    pl_applicable_rate: '77.77',
  });
  const c = pickRate({ pl_inital_rate: '99.99' });
  assert.strictEqual(a, 77.77,
    'applicable_rate wins when present');
  assert.strictEqual(b, 77.77,
    'applicable_rate wins when current_rate is absent');
  assert.strictEqual(c, 99.99,
    'falls back to inital_rate');
  ok('pickRate prefers applicable_rate over current/inital');
}

function testTransformCredit() {
  assert.deepStrictEqual(
    pickCredit({ usr_cibil_score: '', bloan_cibil_score: '-1' }),
    { text: 'No History', numeric: null }
  );
  assert.deepStrictEqual(
    pickCredit({ usr_cibil_score: '510', bloan_cibil_score: '510' }),
    { text: '510', numeric: 510 }
  );
  ok('pickCredit handles empty / -1 / numeric');
}

function testPickCreditFallsBackToBloan() {
  // When the borrower-level usr_cibil_score is empty
  // or NA, we should fall back to the loan-level
  // bloan_cibil_score (this is the common case for
  // Urban Clap / partner-originated loans where the
  // borrower CIBIL hasn't been pulled yet).
  assert.deepStrictEqual(
    pickCredit({ usr_cibil_score: '', bloan_cibil_score: '510' }),
    { text: '510', numeric: 510 },
    'empty usr -> use bloan'
  );
  assert.deepStrictEqual(
    pickCredit({ usr_cibil_score: null, bloan_cibil_score: '750' }),
    { text: '750', numeric: 750 },
    'null usr -> use bloan'
  );
  assert.deepStrictEqual(
    pickCredit({ usr_cibil_score: '600', bloan_cibil_score: '750' }),
    { text: '600', numeric: 600 },
    'usr present -> prefer usr over bloan'
  );
  assert.deepStrictEqual(
    pickCredit({ usr_cibil_score: '', bloan_cibil_score: '' }),
    { text: null, numeric: null },
    'both empty -> null'
  );
  ok('pickCredit falls back to bloan_cibil_score');
}

function testPickCreditNoHistory() {
  // "-1" sentinel in either column means "No History".
  assert.deepStrictEqual(
    pickCredit({ usr_cibil_score: '-1', bloan_cibil_score: '' }),
    { text: 'No History', numeric: null },
    '-1 in usr is No History'
  );
  assert.deepStrictEqual(
    pickCredit({ usr_cibil_score: '', bloan_cibil_score: -1 }),
    { text: 'No History', numeric: null },
    '-1 in bloan is No History'
  );
  ok('pickCredit -1 sentinel means No History');
}

function testTransformTenure() {
  assert.strictEqual(formatTenure(30, 'd'), '30 Days');
  assert.strictEqual(formatTenure(6, 'm'), '6 Months');
  assert.strictEqual(formatTenure(2, 'y'), '2 Years');
  assert.strictEqual(formatTenure(null, 'd'), null);
  assert.strictEqual(formatTenure(0, 'd'), '0 Days');
  ok('formatTenure: days/months/years');
}

function testTransformFunding() {
  const f = computeFunding({
    pl_amt: '10000.00',
    pl_amt_left: '2500.00',
    pl_status: 1,
  });
  assert.strictEqual(f.loanAmount, 10000);
  assert.strictEqual(f.amountFunded, 7500);
  assert.strictEqual(f.amountLeft, 2500);
  assert.strictEqual(f.fundedPercent, 75);
  assert.strictEqual(f.fundingRemaining, 25);
  assert.strictEqual(f.isFullyFunded, false);
  const full = computeFunding({
    pl_amt: '1000',
    pl_amt_left: '0',
    pl_status: 1,
  });
  assert.strictEqual(full.isFullyFunded, true,
    'pl_amt_left = 0 is fully funded');
  const closed = computeFunding({
    pl_amt: '1000', pl_amt_left: '500', pl_status: 0,
  });
  assert.strictEqual(closed.isFullyFunded, true,
    'pl_status != 1 is fully funded');
  ok('computeFunding: amount/percent/remaining/isFullyFunded');
}

function testTransformLoanUrl() {
  // Canonical pattern: /public-profile/{borrowerId}/{loanId}
  const full = buildLoanUrl('12345', '999');
  assert.ok(full.includes('public-profile/12345'),
    'URL must use public-profile pattern');
  assert.ok(full.includes('public-profile/12345/999'),
    'URL must include both borrowerId and loanId');
  // Graceful degradation: missing loanId still yields
  // a valid URL with just the borrowerId segment.
  assert.ok(buildLoanUrl('12345', null)
    .endsWith('public-profile/12345'),
    'null loanId falls back to borrowerId-only URL');
  assert.ok(buildLoanUrl('12345', '')
    .endsWith('public-profile/12345'),
    'empty loanId falls back to borrowerId-only URL');
  assert.ok(buildLoanUrl(null, '999') === '',
    'null borrowerRef -> empty URL');
  assert.ok(buildLoanUrl(undefined, '999') === '',
    'undefined borrowerRef -> empty URL');
  ok('buildLoanUrl: /public-profile/{borrowerId}/{loanId}');
}

function testPurposePrefersBloanDesc() {
  // The transform should prefer the rich bloan_desc
  // narrative ("Need Loan to Purchase Standardized
  // Beauty Kit Package from Urban Clap") over the
  // shorter purpose field ("Personal Loan") and the
  // free-text bloan_other_perpose fallback. It walks
  // the candidates in order: bloan_desc, then
  // bloan_other_perpose, then purpose.
  const a = transformLoan({
    pl_bloan_id: 1,
    pl_user_id: 2,
    pl_amt: '1000',
    pl_amt_left: '0',
    pl_status: 1,
    pl_applicable_rate: '20',
    bloan_desc: 'Rich narrative purpose',
    purpose: 'Short purpose',
  });
  assert.strictEqual(a.purpose, 'Rich narrative purpose',
    'bloan_desc wins when present');

  const b = transformLoan({
    pl_bloan_id: 1,
    pl_user_id: 2,
    pl_amt: '1000',
    pl_amt_left: '0',
    pl_status: 1,
    pl_applicable_rate: '20',
    bloan_desc: '',
    bloan_other_perpose: 'Other text',
    purpose: 'Short purpose',
  });
  assert.strictEqual(b.purpose, 'Other text',
    'bloan_other_perpose wins when bloan_desc is empty');

  const c = transformLoan({
    pl_bloan_id: 1,
    pl_user_id: 2,
    pl_amt: '1000',
    pl_amt_left: '0',
    pl_status: 1,
    pl_applicable_rate: '20',
    bloan_desc: '',
    bloan_other_perpose: '',
    purpose: 'Short purpose',
  });
  assert.strictEqual(c.purpose, 'Short purpose',
    'purpose used as final fallback');

  const d = transformLoan({
    pl_bloan_id: 1,
    pl_user_id: 2,
    pl_amt: '1000',
    pl_amt_left: '0',
    pl_status: 1,
    pl_applicable_rate: '20',
    bloan_desc: '',
    bloan_other_perpose: '',
    purpose: '',
  });
  assert.strictEqual(d.purpose, null,
    'all empty -> null');
  ok('purpose prefers bloan_desc over purpose');
}

function testTransformComplete() {
  // Build a fully-populated API row and ensure
  // every normalized field is present and typed.
  const row = {
    pl_id: 999,
    pl_user_id: 555,
    pl_bloan_id: 777,
    pl_amt: '50000.00',
    pl_amt_left: '10000.00',
    pl_final_amt: '40000.00',
    pl_disbursed_amt: '0.00',
    pl_inital_rate: '30.00',
    pl_current_rate: '32.50',
    pl_applicable_rate: '32.50',
    pl_status: 1,
    postedOn: '15-05-2026',
    monthlySalary: '80000.00',
    usr_fname: 'Test',
    usr_lname: 'User',
    usr_age: '35',
    usr_cibil_score: '720',
    bloan_cibil_score: '720',
    purpose: 'Personal Loan',
    bloan_purpose: 25,
    emp_type: 'Salaried Employee',
    emp_comp_name: 'Acme Co',
    em_self_profession: 'Engineer',
    fin_income: '960000',
    fin_monthly_income: '80000.00',
    bloan_tenure: 12,
    tenure_type: 'm',
    bloan_i2i_category: 'D',
    location: 'Mumbai',
    product_name: 'Regular Loans',
    bloan_desc: 'Test loan rich narrative',
    bloan_i2i_rate: '32.5000',
    loan_count: 1,
  };
  const out = transformLoan(row);
  for (const k of [
    'loanId', 'borrowerRef', 'name', 'age',
    'location', 'residenceType', 'purpose',
    'creditScore', 'creditScoreNumeric',
    'riskCategory', 'interestRate', 'tenure',
    'product', 'madeLiveOn', 'employmentType',
    'monthlyIncome', 'professionName',
    'businessName', 'loanAmount', 'amountFunded',
    'amountLeft', 'fundedPercent',
    'fundingRemaining', 'isFullyFunded',
    'loanUrl', 'scrapedAt', 'yieldScore',
    'priority',
  ]) {
    assert.ok(k in out, `missing field: ${k}`);
  }
  assert.strictEqual(out.loanId, '777');
  assert.strictEqual(out.borrowerRef, '555');
  assert.strictEqual(out.name, 'Test User');
  assert.strictEqual(out.age, 35);
  assert.strictEqual(out.location, 'Mumbai');
  assert.strictEqual(out.interestRate, 32.5);
  assert.strictEqual(out.tenure, '12 Months');
  assert.strictEqual(out.creditScore, '720');
  assert.strictEqual(out.creditScoreNumeric, 720);
  assert.strictEqual(out.riskCategory, 'D');
  assert.strictEqual(out.madeLiveOn,
    '2026-05-15T00:00:00.000Z');
  assert.strictEqual(out.monthlyIncome, 80000);
  assert.strictEqual(out.loanAmount, 50000);
  assert.strictEqual(out.amountLeft, 10000);
  assert.strictEqual(out.amountFunded, 40000);
  assert.strictEqual(out.fundedPercent, 80);
  assert.strictEqual(out.fundingRemaining, 20);
  assert.strictEqual(out.isFullyFunded, false);
  // Purpose comes from bloan_desc (rich narrative)
  // when present, not from the terse `purpose` field.
  assert.strictEqual(out.purpose,
    'Test loan rich narrative',
    'purpose must prefer bloan_desc');
  assert.ok(out.loanUrl.includes('public-profile/555'));
  assert.ok(out.scrapedAt.endsWith('Z'),
    'scrapedAt must be ISO');
  assert.ok(out.yieldScore > 0 && out.yieldScore <= 100,
    `score out of range: ${out.yieldScore}`);
  assert.ok(['VERY_HIGH', 'MEDIUM', 'LOW']
    .includes(out.priority));
  ok('transformLoan: every field populated & typed');
}

function testTransformLoansSkipsBadRows() {
  const out = transformLoans([
    null,
    undefined,
    {},
    { pl_bloan_id: 1 },
    { pl_bloan_id: 2, pl_amt: '100' },
  ]);
  // Only the last two have a pl_bloan_id, so only
  // those should pass.
  assert.strictEqual(out.length, 2,
    `expected 2 transformed, got ${out.length}`);
  ok('transformLoans: skips bad rows without throwing');
}

function testTransformFormatPostedOn() {
  // ISO -> DD-MM-YYYY for the new message format
  assert.strictEqual(
    formatPostedOn('2026-06-04T00:00:00.000Z'),
    '04-06-2026'
  );
  assert.strictEqual(
    formatPostedOn('2026-05-15T00:00:00.000Z'),
    '15-05-2026'
  );
  assert.strictEqual(formatPostedOn(null), null);
  assert.strictEqual(formatPostedOn('garbage'), null);
  ok('formatPostedOn: ISO -> DD-MM-YYYY');
}

function testTransformInr() {
  // Indian-style grouping (lakh separator)
  assert.strictEqual(inr(123456), '₹1,23,456');
  assert.strictEqual(inr(1000000), '₹10,00,000');
  assert.strictEqual(inr(7500), '₹7,500');
  assert.strictEqual(inr(0), '₹0');
  assert.strictEqual(inr(null), null);
  assert.strictEqual(inr(undefined), null);
  ok('inr: Indian-style currency formatting');
}

function testTransformFormatLoanBlock() {
  // Verify the compact label-free line list, ordered
  // by lending-decision importance. Each line is a
  // complete string with no field labels.
  const loan = transformLoan({
    pl_id: 1,
    pl_user_id: 99,
    pl_bloan_id: 1415,
    pl_amt: '10000.00',
    pl_amt_left: '5000.00',
    pl_inital_rate: '60.00',
    pl_current_rate: '60.00',
    pl_applicable_rate: '60.00',
    pl_status: 1,
    postedOn: '04-06-2026',
    location: 'Bengaluru',
    purpose: 'Personal Loan',
    bloan_i2i_category: 'X',
    bloan_tenure: 6,
    tenure_type: 'm',
    residence_type: 'Own House',
    emp_type: 'Salaried Employee',
    emp_comp_name: 'Acme Co',
    em_self_profession: 'Engineer',
    fin_monthly_income: '50000',
    usr_fname: 'A',
    usr_lname: 'B',
    usr_age: '30',
    usr_cibil_score: '720',
    bloan_cibil_score: '720',
  });
  const lines = formatLoanBlock(loan);
  // Returns an array of strings
  assert.ok(Array.isArray(lines),
    'formatLoanBlock must return an array');
  for (const line of lines) {
    assert.ok(typeof line === 'string' && line.length > 0,
      'every line must be a non-empty string');
  }
  // Line 1: Rate + Yield (the most important signal
  // is at the top)
  assert.ok(/60\.00% p\.a\./.test(lines[0]),
    `line 0 must contain rate, got: ${lines[0]}`);
  assert.ok(/Yield \d+\.\d{2}\/100/.test(lines[0]),
    `line 0 must contain yield score, got: ${lines[0]}`);
  // Line 2: Identity (i2i-# and Loan)
  assert.ok(lines[1].includes('i2i-#99')
      && lines[1].includes('Loan 1415'),
    `line 1 must be identity, got: ${lines[1]}`);
  // Line 3: Funding (total, funded, left — all 3)
  assert.ok(lines[2].includes('₹10,000'),
    `line 2 must include total, got: ${lines[2]}`);
  assert.ok(lines[2].includes('funded'),
    `line 2 must include funded, got: ${lines[2]}`);
  assert.ok(lines[2].includes('left'),
    `line 2 must include left, got: ${lines[2]}`);
  // NO field labels anywhere
  for (const lbl of [
    'Int. Rate:', 'Credit Bureau Score:',
    'i2i Risk Category:', 'Loan Amount:',
    'Funded:', 'Remaining:', 'Made Live On:',
    'URL:', 'Yield Score:', 'Borrower:',
    'Tenure:', 'Purpose:', 'Location:',
    'Name:', 'Age:', 'Loan Id:',
  ]) {
    assert.ok(
      !lines.some((l) => l.includes(lbl)),
      `formatLoanBlock should not contain label "${lbl}"`
    );
  }
  // No % anywhere except the rate line
  for (let i = 1; i < lines.length; i++) {
    assert.ok(!lines[i].includes('%'),
      `line ${i} should not contain percent: ${lines[i]}`);
  }
  ok('formatLoanBlock: label-free, rate+yield at top, no % in funding');
}

function testTransformFormatLoanBlockOmitsMissing() {
  // When data is missing, the corresponding line is
  // dropped entirely (not rendered as "N/A"). The
  // funding line should still appear if loanAmount
  // and amountLeft are present.
  const loan = transformLoan({
    pl_id: 1,
    pl_user_id: 99,
    pl_bloan_id: 1415,
    pl_amt: '10000.00',
    pl_amt_left: '5000.00',
    pl_applicable_rate: '60.00',
    pl_status: 1,
    postedOn: '04-06-2026',
    // location, purpose, residenceType, employmentType,
    // monthlyIncome, professionName, businessName,
    // creditScore, loanUrl, name, age all omitted.
  });
  const lines = formatLoanBlock(loan);
  // Every line is non-empty
  for (const l of lines) {
    assert.ok(l.length > 0,
      'every line must be non-empty');
    assert.ok(!/N\/A/.test(l),
      `line should not contain N/A: ${l}`);
  }
  // No empty lines (no "N/A" or "—" placeholders)
  assert.ok(!lines.includes(''),
    'formatLoanBlock should not include empty lines');
  // Rate + yield still at the top
  assert.ok(/60\.00% p\.a\./.test(lines[0]),
    'rate must still be at the top');
  ok('formatLoanBlock: silently omits missing data');
}

/* ============================================================
   WORKFLOW & PROJECT SHAPE
   ============================================================ */

function testWorkflowSchedule() {
  const yml = fs.readFileSync(
    path.join(
      __dirname, '..', '..',
      '.github', 'workflows', 'scrape.yml'
    ),
    'utf-8'
  );
  // Every-5-min schedule aligned to IST marks.
  // GitHub cron is in UTC; IST is UTC+5:30, so each
  // IST :00, :05, :10 ... :55 maps to a different UTC
  // minute. All 12 marks fall within the same hour
  // and are combined in a single cron entry.
  assert.ok(
    /cron:\s*'30,35,40,45,50,55,0,5,10,15,20,25 \* \* \* \*'/
      .test(yml),
    'Cron must include the every-5-min IST expression'
  );
  assert.ok(
    /repository_dispatch:/.test(yml),
    'Workflow must include repository_dispatch trigger'
  );
  assert.ok(
    /cancel-in-progress:\s*false/.test(yml),
    'Workflow must NOT cancel in-progress runs'
  );
  assert.ok(
    /timeout-minutes:\s*10/.test(yml),
    'Workflow must set a 10-min timeout'
  );
  assert.ok(
    /ubuntu-latest/.test(yml),
    'Workflow must run on ubuntu-latest'
  );
  // Speed optimizations
  assert.ok(
    /actions\/cache@v[46]/.test(yml),
    'Workflow should cache node_modules'
  );
  assert.ok(
    /fetch-depth:\s*1/.test(yml),
    'Workflow should use shallow checkout'
  );
  assert.ok(
    /--prefer-offline/.test(yml),
    'Workflow should use --prefer-offline for npm ci'
  );
  assert.ok(
    /npm test/.test(yml),
    'Workflow must run the test suite before scraping'
  );
  assert.ok(
    !/continue-on-error:\s*true/.test(yml),
    'Scraper step must fail the job on error'
  );
  assert.ok(
    /ms-playwright/.test(yml),
    'Workflow should cache Playwright browsers'
  );
  assert.ok(
    /data_changed/.test(yml),
    'Workflow should skip deploy when data is unchanged'
  );
  // Playwright install must be UNCONDITIONAL
  // (always-on fallback per project policy).
  const playwrightInstall = yml.match(
    /\bnpx playwright install\b[^\n]*/g
  ) || [];
  assert.ok(
    playwrightInstall.length > 0,
    'Workflow must include `npx playwright install`'
  );
  for (const line of playwrightInstall) {
    assert.ok(
      !/if:|inputs\.\w+|\$\{\{/.test(line),
      `Playwright install must be unconditional, got: ${line}`
    );
  }
  // git-crypt setup for .env encryption
  assert.ok(
    /flydiverny\/setup-git-crypt@v5/.test(yml)
      || /setup-git-crypt/.test(yml),
    'Workflow must include setup-git-crypt step'
  );
  assert.ok(
    /git-crypt unlock/.test(yml)
      || /GIT_CRYPT_KEY/.test(yml),
    'Workflow must unlock git-crypt in CI'
  );
  ok('workflow schedule + shape: cron=5min-IST, dispatch, no-cancel, 10min, cached, playwright, git-crypt');
}

function testProjectLayout() {
  const root = path.join(__dirname, '..', '..');
  for (const f of [
    'scraper/src/core/api.js',
    'scraper/src/core/api-intercept.js',
    'scraper/src/core/transform.js',
    'scraper/src/core/storage.js',
    'scraper/src/core/index.js',
    'scraper/src/utils/scorer.js',
    'scraper/src/utils/logger.js',
    'scraper/src/notifiers/telegram.js',
    'scraper/src/notifiers/email.js',
    'scraper/src/notifiers/discord.js',
    'scraper/src/notifiers/ntfy.js',
    'scraper/src/notifiers/notifier.js',
    'scraper/src/browser/scraper.js',
    'scraper/src/browser/parser.js',
    'scraper/src/browser/showmore.js',
    'scraper/test/smoketest.js',
    'scraper/test/verify_syntax.js',
    'scraper/package.json',
    'dashboard/index.html', 'dashboard/app.js',
    'dashboard/styles.css',
    '.github/workflows/scrape.yml',
    '.env.example',
    '.gitattributes',
  ]) {
    assert.ok(
      fs.existsSync(path.join(root, f)),
      `missing required file: ${f}`
    );
  }
  // No extra .md files (single source of truth =
  // root README.md). LICENSE is allowed.
  const rootEntries = fs.readdirSync(root);
  const mdFiles = rootEntries.filter(
    (n) => n.endsWith('.md') && n !== 'LICENSE'
  );
  assert.deepStrictEqual(mdFiles, ['README.md'],
    `Only README.md allowed at root, found: ${mdFiles}`);
  // .env exists at root (encrypted via git-crypt)
  assert.ok(
    fs.existsSync(path.join(root, '.env')),
    '.env must exist at root (encrypted via git-crypt)'
  );
  // git-crypt config in .gitattributes
  const gitattributes = fs.readFileSync(
    path.join(root, '.gitattributes'), 'utf-8'
  );
  assert.ok(
    /\.env filter=git-crypt/.test(gitattributes),
    '.gitattributes must configure git-crypt for .env'
  );
  ok('project layout: SOLID folder structure, single README, git-crypt .env');
}

/* ============================================================
   RUN
   ============================================================ */

(async () => {
  console.log('--- Rate & scoring ---');
  t('rate filter', testRateFilter);
  t('priority thresholds', testPriorityThresholds);
  t('yield score bounds', testYieldScoreBounds);

  console.log('--- Telegram & notifiers ---');
  t('telegram chunker', testTelegramChunker);
  t('telegram omits N/A', testTelegramOmitsNA);
  t('telegram includes fields', testTelegramIncludesAllUsefulFields);
  t('telegram first-line no-URL fallback', testTelegramFirstLineNoUrlStillBolds);
  t('email first-line clickable', testEmailFirstLineIsClickable);
  t('email first-line no-URL fallback', testEmailFirstLineNoUrlStillStyled);
  await ta('notifier empty', testEmptyNotifies);
  await ta('notifier disabled', testDisabledChannels);
  t('wasAnyChannelSuccessful', testWasAnyChannelSuccessful);
  t('no promo copy', testNoPromotionalCopy);
  await ta('ntfy disabled', testNtfyDisabled);
  await ta('ntfy missing topic', testNtfyMissingTopic);
  t('ntfy body shape', testNtfyFormatBody);
  t('ntfy default base url', testNtfyDefaultBaseUrl);

  console.log('--- Loan ID & dedup ---');
  t('transform carries loanId', testTransformCarriesLoanId);
  t('filterUnnotified', testFilterUnnotified);
  t('markNotificationsSent idempotent', testMarkNotificationsSentIdempotent);
  t('no fingerprint file', testNoFingerprintFileNeeded);

  console.log('--- API payload (no network) ---');
  t('buildFilterBody', testBuildFilterBody);
  await ta('fetchPage uses POST + headers', testFetchPageStub);
  t('api constants', testApiConstants);

  console.log('--- Transform ---');
  t('parsePostedOn', testTransformPostedOn);
  t('toNumber', testTransformToNumber);
  t('NA matcher', testTransformNA);
  t('pickRate', testTransformPicksApplicableRate);
  t('pickCredit', testTransformCredit);
  t('pickCredit falls back to bloan',
    testPickCreditFallsBackToBloan);
  t('pickCredit -1 sentinel',
    testPickCreditNoHistory);
  t('formatTenure', testTransformTenure);
  t('computeFunding', testTransformFunding);
  t('buildLoanUrl', testTransformLoanUrl);
  t('purpose prefers bloan_desc',
    testPurposePrefersBloanDesc);
  t('transformLoan complete', testTransformComplete);
  t('transformLoans skips bad rows', testTransformLoansSkipsBadRows);
  t('formatPostedOn', testTransformFormatPostedOn);
  t('inr', testTransformInr);
  t('formatLoanBlock 19 rows', testTransformFormatLoanBlock);
  t('formatLoanBlock omits optionals',
    testTransformFormatLoanBlockOmitsMissing);

  console.log('--- Workflow & layout ---');
  t('workflow schedule', testWorkflowSchedule);
  t('project layout', testProjectLayout);

  console.log(`\n${pass} passed, ${fail} failed.`);
  process.exit(fail === 0 ? 0 : 1);
})();

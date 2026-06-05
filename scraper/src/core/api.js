// scraper/api.js
// Pure-HTTP fetcher for the i2iFunding borrower
// listing. Paginates with `pageNo` until empty.

const https = require('https');
const zlib = require('zlib');
const logger = require('../utils/logger');

const API_HOST = 'api.i2ifunding.com';
const API_PATH = '/api/v1/getActiveFilteredBorrowers/'
  + '?csrf_token=undefined&session_id=undefined';

const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; '
  + 'Win64; x64) AppleWebKit/537.36 (KHTML, like '
  + 'Gecko) Chrome/145.0.0.0 Safari/537.36';

const PAGE_SIZE_HINT = 10;
const MAX_PAGES = 50;
const REQUEST_TIMEOUT_MS = 30000;
const MAX_RETRIES = 3;
const PARALLEL_PAGES = 5;

const httpsAgent = new https.Agent({
  keepAlive: true,
  maxSockets: PARALLEL_PAGES,
});

/**
 * Decompress response body based on Content-Encoding.
 * @param {import('http').IncomingMessage} res
 * @returns {import('stream').Readable}
 */
function decompressResponse(res) {
  const encoding = (
    res.headers['content-encoding'] || ''
  ).toLowerCase();
  if (encoding.includes('br')) {
    return res.pipe(zlib.createBrotliDecompress());
  }
  if (encoding.includes('gzip')) {
    return res.pipe(zlib.createGunzip());
  }
  if (encoding.includes('deflate')) {
    return res.pipe(zlib.createInflate());
  }
  return res;
}

/**
 * Minimal filter payload that the server actually
 * reads.
 */
function buildFilterBody(pageNo) {
  return {
    riskCategory: {
      label: 'Risk Category',
      options: [
        { text: 'A', active: false, value: 'A' },
        { text: 'B', active: false, value: 'B' },
        { text: 'C', active: false, value: 'C' },
        { text: 'D', active: false, value: 'D' },
        { text: 'E', active: false, value: 'E' },
        { text: 'F', active: false, value: 'F' },
        { text: 'X', active: false, value: 'X' },
      ],
    },
    employement: {
      label: 'Employement',
      options: [
        { text: 'Salaried Employee', active: false,
          value: 'salaried' },
        { text: 'Self Emp Business', active: false,
          value: 'business' },
        { text: 'Self Emp Professional', active: false,
          value: 'selfEmployed' },
      ],
    },
    product: {
      label: 'Product',
      options: [
        { text: 'Regular Loans', active: false,
          value: 'Regular Loans', id: 1 },
        { text: 'Employer Partnership', active: false,
          value: 'Employer Partnership', id: 2 },
        { text: 'Loan Against Invoice', active: false,
          value: 'Loan Against Invoice', id: 3 },
        { text: 'Course Subscription Fee',
          active: false,
          value: 'Course Subscription Fee', id: 4 },
        { text: 'NBFC Backed', active: false,
          value: 'NBFC Backed', id: 5 },
        { text: 'Urban Clap', active: false,
          value: 'Urban Clap', id: 6 },
        { text: 'Backed by Partner Company',
          active: false,
          value: 'Backed by Partner Company', id: 8 },
      ],
    },
    cibilScore: {
      label: 'Credit Bureau Score',
      options: [
        { text: '>700', active: false, min: 701, max: -1 },
        { text: '650-700', active: false, min: 651, max: 700 },
        { text: '600-650', active: false, min: 601, max: 650 },
        { text: 'No History', active: false, min: 0, max: 0 },
      ],
    },
    preferredInterestRate: {
      label: 'Interest Rate',
      options: [
        { text: '<18%', active: false, min: 0, max: 17 },
        { text: '18%-24%', active: false, min: 18, max: 23 },
        { text: '24%-30%', active: false, min: 24, max: 30 },
      ],
    },
    tenure: {
      label: 'Tenure',
      options: [
        { text: '<3 Months', active: false, min: 0, max: 2 },
        { text: '3 Months - 6 Months', active: false,
          min: 3, max: 5 },
        { text: '6 Months - 12 Months', active: false,
          min: 6, max: 11 },
        { text: '12 Months - 18 Months', active: false,
          min: 12, max: 17 },
        { text: '18 Months - 24 Months', active: false,
          min: 18, max: 23 },
        { text: '>24 Months', active: false, min: 24, max: -1 },
      ],
    },
    income: {
      label: 'Income',
      options: [
        { text: '<25,000', active: false, min: 0, max: 24999 },
        { text: '25,000 - 50,000', active: false,
          min: 25000, max: 49999 },
        { text: '50,000-75,000', active: false,
          min: 50000, max: 74999 },
        { text: '75,000+', active: false, min: 75000, max: -1 },
      ],
    },
    funded: {
      label: '% Funded',
      options: [
        { text: '<25%', active: false, min: 0, max: 24 },
        { text: '25%-50%', active: false, min: 25, max: 49 },
        { text: '50%-75%', active: false, min: 50, max: 74 },
        { text: '75%-100%', active: false, min: 75, max: 100 },
        { text: 'All Live Loan', active: false,
          min: 0, max: 100 },
      ],
    },
    daysLeft: {
      label: 'Days Left',
      options: [
        { text: '0-7 Days', active: false, min: 0, max: 6 },
        { text: '7-15 Days', active: false, min: 7, max: 14 },
        { text: '> 15 Days', active: false, min: 15, max: -1 },
      ],
    },
    location: '',
    pageNo,
  };
}

/**
 * POST the filter body for one page.
 */
function fetchPage(pageNo) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(buildFilterBody(pageNo));
    const req = https.request({
      hostname: API_HOST,
      port: 443,
      path: API_PATH,
      method: 'POST',
      agent: httpsAgent,
      headers: {
        'Origin': 'https://www.i2ifunding.com',
        'Referer': 'https://www.i2ifunding.com/borrower/listing',
        'User-Agent': USER_AGENT,
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'en-US,en;q=0.9',
      },
    }, (res) => {
      const chunks = [];
      const input = decompressResponse(res);
      input.on('data', (c) => chunks.push(c));
      input.on('end', () => {
        const body = Buffer.concat(chunks).toString('utf-8');
        if (res.statusCode !== 200) {
          return reject(new Error(
            `HTTP ${res.statusCode}: ${body.slice(0, 200)}`
          ));
        }
        let parsed;
        try {
          parsed = JSON.parse(body);
        } catch (_e) {
          return reject(new Error(
            `Non-JSON response: ${body.slice(0, 200)}`
          ));
        }
        if (!Array.isArray(parsed)) {
          return reject(new Error(
            `Expected JSON array, got ${
              typeof parsed} (${body.slice(0, 200)})`
          ));
        }
        resolve(parsed);
      });
      input.on('error', reject);
    });
    req.on('error', reject);
    req.setTimeout(REQUEST_TIMEOUT_MS, () =>
      req.destroy(new Error(`Timeout after ${
        REQUEST_TIMEOUT_MS}ms`))
    );
    req.write(payload);
    req.end();
  });
}

/**
 * Fetch one page with exponential backoff retries.
 */
async function fetchPageWithRetry(pageNo) {
  let lastErr = null;
  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      return await fetchPage(pageNo);
    } catch (e) {
      lastErr = e;
      const backoff = 1000 * Math.pow(2, attempt - 1);
      logger.warn(
        `API page ${pageNo} attempt ${attempt} failed: `
        + `${e.message} — retry in ${backoff}ms`
      );
      await new Promise((r) => setTimeout(r, backoff));
    }
  }
  throw new Error(
    `API page ${pageNo} failed after ${MAX_RETRIES} `
    + `retries: ${lastErr?.message}`
  );
}

/**
 * Merge page rows into the aggregate list.
 * @returns {boolean} true if this page looks like the last
 */
function mergePageRows(pageRows, all, seen) {
  if (pageRows.length === 0) return true;
  for (const row of pageRows) {
    const id = String(row.pl_bloan_id || row.pl_id || '');
    if (!id || seen.has(id)) continue;
    seen.add(id);
    all.push(row);
  }
  return pageRows.length < PAGE_SIZE_HINT;
}

/**
 * Fetch every active loan by walking pageNo upward.
 * Page 1 is fetched first; subsequent pages run in
 * parallel batches for speed.
 */
async function fetchAllLoans() {
  const all = [];
  const seen = new Set();

  const firstPage = await fetchPageWithRetry(1);
  logger.info(`API page 1: ${firstPage.length} rows`);
  const doneAfterFirst = mergePageRows(firstPage, all, seen);
  if (doneAfterFirst) {
    return all;
  }

  let nextPage = 2;
  while (nextPage <= MAX_PAGES) {
    const batch = [];
    for (
      let i = 0;
      i < PARALLEL_PAGES && nextPage + i <= MAX_PAGES;
      i++
    ) {
      batch.push(nextPage + i);
    }

    const results = await Promise.all(
      batch.map((pageNo) => fetchPageWithRetry(pageNo))
    );

    let reachedEnd = false;
    for (let i = 0; i < results.length; i++) {
      const pageNo = batch[i];
      const pageRows = results[i];
      logger.info(
        `API page ${pageNo}: ${pageRows.length} rows `
        + `(total unique: ${all.length})`
      );
      if (pageRows.length === 0) {
        logger.info(`API page ${pageNo} empty — done`);
        reachedEnd = true;
        break;
      }
      const isLast = mergePageRows(pageRows, all, seen);
      if (isLast) {
        logger.info(
          `API page ${pageNo} returned fewer than ${
            PAGE_SIZE_HINT} rows — last page`
        );
        reachedEnd = true;
        break;
      }
    }

    if (reachedEnd) break;
    nextPage += batch.length;
  }

  return all;
}

module.exports = {
  fetchAllLoans,
  fetchPage,
  buildFilterBody,
  PAGE_SIZE_HINT,
  MAX_PAGES,
  MAX_RETRIES,
  PARALLEL_PAGES,
  REQUEST_TIMEOUT_MS,
  API_HOST,
  API_PATH,
  USER_AGENT,
};

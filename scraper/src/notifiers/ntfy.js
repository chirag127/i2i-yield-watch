// scraper/src/notifiers/ntfy.js
// ntfy.sh (or self-hosted ntfy) push notification channel.
// Free, open-source, HTTP-based pub-sub service.
// Subscribe to a topic in the ntfy mobile/desktop app,
// then this notifier POSTs to <baseUrl>/<topic>.
//
// Public default: https://ntfy.sh
// Self-hosted:    https://your-ntfy-server.com
//
// Auth (optional): set NTFY_USER + NTFY_PASSWORD for
// access-controlled topics. Public topics only need
// the topic name (which acts as a shared secret).
//
// Message body is the standardized 19-field block
// defined in transform.js -> formatLoanBlock().
// The same shape and field order as Telegram, Email,
// and Discord; ntfy just renders plain text.

const logger = require('../utils/logger');
const { formatLoanBlock } = require('../core/transform');

const DEFAULT_BASE_URL = 'https://ntfy.sh';

/**
 * Render a single loan as a plain-text ntfy body.
 * The same compact line list is used (from
 * transform.formatLoanBlock); lines are already
 * pre-formatted and label-free, so we just join
 * them with newlines.
 * @param {object} loan
 * @returns {string}
 */
function formatLoanBody(loan) {
  const lines = formatLoanBlock(loan);
  return lines.join('\n');
}

/**
 * Build the header for the periodic broadcast.
 */
function buildHeader(count, rateThreshold) {
  return (
    `🚨 ${count} NEW LOAN${count === 1 ? '' : 'S'} `
    + `(rate > ${rateThreshold}%)\n\n`
  );
}

/**
 * Build the footer.
 */
function buildFooter(stats) {
  return (
    `\n\n📊 Active: ${stats.activeCount || 0}`
  );
}

/**
 * Build the Authorization header from user/pass.
 * Returns null if no credentials are set.
 */
function buildAuthHeader() {
  const user = process.env.NTFY_USER;
  const pass = process.env.NTFY_PASSWORD;
  if (!user || !pass) return null;
  const encoded = Buffer.from(`${user}:${pass}`)
    .toString('base64');
  return `Basic ${encoded}`;
}

/**
 * Send a single message via ntfy HTTP API.
 * @param {string} baseUrl
 * @param {string} topic
 * @param {string} title
 * @param {string} body
 * @param {string|null} authHeader
 * @returns {Promise<boolean>}
 */
async function postNtfyMessage(
  baseUrl, topic, title, body, authHeader
) {
  const url = `${baseUrl.replace(/\/$/, '')}`
    + `/${encodeURIComponent(topic)}`;
  const headers = {
    'Title': title,
    'Priority': 'high',
    'Tags': 'rotating_light,money_with_wings',
  };
  if (authHeader) {
    headers['Authorization'] = authHeader;
  }
  const res = await fetch(url, {
    method: 'POST',
    headers,
    body,
  });
  if (res.ok) return true;
  const errBody = await res.text();
  logger.error(
    `ntfy: API error ${res.status} — ${errBody}`
  );
  return false;
}

/**
 * Send ntfy push notification for the new high-yield
 * loans. ntfy accepts long messages (no chunking
 * needed) and shows them as a single push on the
 * device. The first line becomes the title, the rest
 * becomes the body.
 *
 * @param {Array} loans - New high-yield loans
 * @param {object} stats - { activeCount, ... }
 * @param {string} dashboardUrl - unused; kept for
 *   signature compatibility with other notifiers
 * @param {object} options - { rateThreshold }
 * @returns {Promise<boolean>}
 */
async function sendNtfy(
  loans, stats, _dashboardUrl = '', options = {}
) {
  const enabled = process.env.NTFY_ENABLED === 'true';
  if (!enabled) {
    return false;
  }
  const topic = process.env.NTFY_TOPIC;
  if (!topic) {
    logger.warn('ntfy: missing NTFY_TOPIC');
    return false;
  }
  const baseUrl = process.env.NTFY_BASE_URL
    || DEFAULT_BASE_URL;
  const authHeader = buildAuthHeader();

  const rateThreshold = options.rateThreshold != null
    ? options.rateThreshold : 50;

  try {
    // Sort by interest rate descending so the best
    // opportunities appear first.
    const sorted = [...loans].sort(
      (a, b) => (b.interestRate || 0)
        - (a.interestRate || 0)
    );

    // Build the full body — ntfy accepts long messages.
    const header = buildHeader(
      sorted.length, rateThreshold
    );
    const loanBlocks = sorted
      .map(formatLoanBody)
      .join('\n\n---\n\n');
    const footer = buildFooter(stats);
    const title = `i2i Yield Watch — ${sorted.length} `
      + `new loan${sorted.length === 1 ? '' : 's'} `
      + `(rate > ${rateThreshold}%)`;
    const body = header + loanBlocks + footer;

    const ok = await postNtfyMessage(
      baseUrl, topic, title, body, authHeader
    );
    if (ok) {
      logger.info(
        `ntfy: sent ${sorted.length} loans `
        + `to ${baseUrl}/${topic}`
      );
    }
    return ok;
  } catch (err) {
    logger.error(`ntfy: failed — ${err.message}`);
    return false;
  }
}

module.exports = {
  sendNtfy,
  formatLoanBody,
  buildHeader,
  buildFooter,
  DEFAULT_BASE_URL,
};

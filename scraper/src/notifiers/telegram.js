// scraper/telegram.js
// Telegram Bot API notification channel.
// Uses native fetch (Node 18+). No external deps.
// Sends ONLY the new high-yield loans that have
// not been seen before (loanId dedup is applied
// in scraper/index.js before this is called).
// Long lists are chunked across multiple messages
// to respect Telegram's 4096 char per-message limit.
//
// Message format is the compact line-based block
// defined in transform.js -> formatLoanBlock().
// The first line (Rate + Yield) is bolded; the
// remaining lines are plain text. NO field labels
// are rendered — the value's own content carries
// the meaning (e.g. "2 Months" is tenure, a
// personal-loan narrative is purpose).

const logger = require('../utils/logger');
const { formatLoanBlock } = require('../core/transform');

// Telegram hard limit for a single text message
// is 4096 chars; keep a safety margin.
const SAFE_CHUNK_LENGTH = 3800;

/**
 * Escape HTML special chars in a string so we
 * never break Telegram's parse_mode.
 */
function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/**
 * Render a single loan as a Telegram HTML block.
 * The standardized line list is the source of
 * truth (see transform.formatLoanBlock). The
 * first line (Rate + Yield) is bolded AND wrapped
 * in a clickable hyperlink pointing to the loan's
 * public profile page, so tapping the rate line
 * opens the loan on i2iFunding. Every other line
 * is plain. No field labels.
 * @param {object} loan
 * @returns {string}
 */
function formatLoanLine(loan) {
  const raw = formatLoanBlock(loan);
  // The block always puts the URL as its last line
  // (when present). We extract it so we can:
  //   1. wrap the first line in <a href="..."> so
  //      the rate line is the clickable link the
  //      user actually taps, and
  //   2. drop the URL line so the URL isn't repeated
  //      as plain text below the clickable rate.
  // loan.loanUrl is used as a fallback when the
  // block somehow didn't include the URL line.
  let url = loan.loanUrl || '';
  let lines = raw;
  if (raw.length > 0
    && /^https?:\/\//i.test(raw[raw.length - 1])) {
    url = raw[raw.length - 1];
    lines = raw.slice(0, -1);
  }
  return lines
    .map((l, i) => {
      const safe = escHtml(l);
      // First line: bold + clickable. This is the
      // only place HTML is used; everything else is
      // plain text.
      if (i === 0) {
        return url
          ? `<a href="${escHtml(url)}">`
            + `<b>${safe}</b></a>`
          : `<b>${safe}</b>`;
      }
      return safe;
    })
    .join('\n');
}

/**
 * Build the header for the periodic broadcast.
 * Kept short so each Telegram chunk has more room
 * for the loan blocks themselves.
 */
function buildHeader(count, stats, dashboardUrl, rateThreshold) {
  return (
    `🚨 <b>${count} NEW LOAN${count === 1 ? '' : 'S'} `
    + `(rate &gt; ${rateThreshold}%)</b>\n\n`
  );
}

/**
 * Build the footer for the final message chunk.
 */
function buildFooter(stats, dashboardUrl) {
  return (
    `\n\n📊 Active: ${stats.activeCount || 0} | `
    + `<a href="${dashboardUrl}">Dashboard</a>`
  );
}

/**
 * Split a list of loans into multiple message
 * chunks that each fit within the Telegram limit.
 * The header is prepended to the first chunk and
 * the footer is appended to the last chunk.
 * @param {Array} loans - already sorted
 * @param {string} header
 * @param {string} footer
 * @returns {Array<string>} Array of message strings
 */
function chunkLoansIntoMessages(loans, header, footer) {
  const messages = [];
  let current = header;

  for (let i = 0; i < loans.length; i++) {
    const loan = loans[i];
    const block = formatLoanLine(loan) + '\n\n';

    // If adding this block would overflow a chunk
    // (and current isn't empty), flush first.
    if (
      current.length + block.length
        > SAFE_CHUNK_LENGTH
      && current.length > header.length
    ) {
      messages.push(current);
      current = '';
    }

    current += block;

    // If the block alone is larger than the safe
    // length, flush immediately after appending.
    if (current.length > SAFE_CHUNK_LENGTH) {
      messages.push(current);
      current = '';
    }
  }

  // Append footer to the last message
  if (current.length > 0) {
    messages.push(current + footer);
  } else if (messages.length > 0) {
    // Replace last message with header-less
    // continuation + footer.
    messages[messages.length - 1] += footer;
  } else {
    // No loans — send a minimal "no qualifying
    // loans" notice so the user knows the run
    // happened.
    messages.push(
      header.trimEnd() + '\n\n'
      + '<i>No loans currently meet the criteria.</i>\n'
      + footer
    );
  }

  return messages;
}

/**
 * Send a single message via Telegram Bot API.
 * @param {string} token
 * @param {string} chatId
 * @param {string} text
 * @returns {Promise<boolean>}
 */
async function postTelegramMessage(
  token, chatId, text
) {
  const apiUrl =
    `https://api.telegram.org/bot${token}`
    + '/sendMessage';

  const res = await fetch(apiUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      chat_id: chatId,
      text,
      parse_mode: 'HTML',
      disable_web_page_preview: true,
    }),
  });

  if (res.ok) return true;

  const errBody = await res.text();
  logger.error(
    `Telegram: API error ${res.status} — `
    + errBody
  );
  return false;
}

/**
 * Send Telegram notification containing the new
 * high-yield loans that have not been seen before
 * (fingerprint dedup is applied in
 * scraper/index.js). The message is split into
 * multiple chunks when needed.
 * @param {Array} loans - New high-yield loans
 * @param {object} stats - { activeCount, ... }
 * @param {string} dashboardUrl
 * @param {object} options - { rateThreshold }
 * @returns {Promise<boolean>}
 */
async function sendTelegram(
  loans, stats, dashboardUrl, options = {}
) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_CHAT_ID;

  if (!token || !chatId) {
    logger.warn(
      'Telegram: missing BOT_TOKEN or CHAT_ID'
    );
    return false;
  }

  try {
    const rateThreshold = options.rateThreshold
      != null
      ? options.rateThreshold
      : 50;

    // Sort by interest rate descending so the
    // best opportunities appear first.
    const sorted = [...loans].sort(
      (a, b) =>
        (b.interestRate || 0)
        - (a.interestRate || 0)
    );

    const header = buildHeader(
      sorted.length, stats, dashboardUrl, rateThreshold
    );
    const footer = buildFooter(stats, dashboardUrl);

    const messages = chunkLoansIntoMessages(
      sorted, header, footer
    );

    let allOk = true;
    for (let i = 0; i < messages.length; i++) {
      const ok = await postTelegramMessage(
        token, chatId, messages[i]
      );
      if (!ok) {
        allOk = false;
        // Continue sending remaining chunks so
        // partial delivery still happens, but log.
        logger.error(
          `Telegram: chunk ${i + 1}/`
          + `${messages.length} failed`
        );
      } else {
        logger.info(
          `Telegram: chunk ${i + 1}/`
          + `${messages.length} sent `
          + `(${sorted.length} loans)`
        );
      }

      // Small delay between chunks to respect
      // Telegram rate limits (30 msg/sec global,
      // 1 msg/sec per chat for bots).
      if (i < messages.length - 1) {
        await new Promise(
          (r) => setTimeout(r, 1100)
        );
      }
    }

    if (allOk) {
      logger.info(
        `Telegram: sent all ${sorted.length} loans `
        + `in ${messages.length} message(s)`
      );
    }
    return allOk;

  } catch (err) {
    logger.error(
      `Telegram: failed — ${err.message}`
    );
    return false;
  }
}

module.exports = {
  sendTelegram,
  formatLoanLine,
  buildHeader,
  buildFooter,
  chunkLoansIntoMessages,
};

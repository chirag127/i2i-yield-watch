// scraper/telegram.js
// Telegram Bot API notification channel.
// Uses native fetch (Node 18+). No external deps.

const logger = require('./logger');

/**
 * Format a loan entry for Telegram HTML message.
 * @param {object} loan
 * @returns {string}
 */
function formatLoanLine(loan) {
  const rate = loan.interestRate || 0;
  const loc = loan.location || 'Unknown';
  const amt = loan.loanAmount
    ? `₹${loan.loanAmount.toLocaleString('en-IN')}`
    : 'N/A';
  const score = loan.yieldScore || 0;
  const credit = loan.creditScore || 'N/A';
  const remaining = loan.fundingRemaining != null
    ? `${loan.fundingRemaining}%`
    : 'N/A';
  const product = loan.product || '';

  const url = (loan.borrowerRef && loan.loanId)
    ? `https://www.i2ifunding.com/borrower/listing/public-profile/${loan.borrowerRef}/${loan.loanId}`
    : (loan.loanUrl || `https://www.i2ifunding.com/invest/loan-detail/${loan.loanId}`);

  return (
    `🔥 <a href="${url}"><b>${rate}% p.a.</b></a> | ${loc} `
    + `| ${amt} | Score: ${score}\n`
    + `   Credit: ${credit} `
    + `| ${remaining} remaining `
    + `| ${product}`
  );
}

/**
 * Format the complete Telegram notification message.
 * @param {Array} newLoans
 * @param {object} stats
 * @param {string} dashboardUrl
 * @returns {string}
 */
function formatTelegramMessage(
  newLoans, stats, dashboardUrl
) {
  // Sort by interest rate descending
  const sorted = [...newLoans].sort(
    (a, b) =>
      (b.interestRate || 0)
      - (a.interestRate || 0)
  );
  const top = sorted.slice(0, 10);

  let msg =
    '🚨 <b>NEW HIGH-YIELD LOANS DETECTED</b>'
    + ' — i2i-yield-watch\n\n';

  for (const loan of top) {
    msg += formatLoanLine(loan) + '\n\n';
  }

  if (sorted.length > 10) {
    msg += `<i>...and ${sorted.length - 10}`
      + ' more loans</i>\n\n';
  }

  msg +=
    `📊 Active: ${stats.activeCount || 0} loans\n`
    + `🔗 <a href="${dashboardUrl}">Dashboard</a>\n`
    + `⏰ Scraped: ${new Date().toUTCString()}`;

  return msg;
}

/**
 * Send a Telegram notification with new loans.
 * @param {Array} newLoans
 * @param {object} stats
 * @param {string} dashboardUrl
 * @returns {Promise<boolean>}
 */
async function sendTelegram(
  newLoans, stats, dashboardUrl
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
    const message = formatTelegramMessage(
      newLoans, stats, dashboardUrl
    );

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
        text: message,
        parse_mode: 'HTML',
        disable_web_page_preview: false,
      }),
    });

    if (res.ok) {
      logger.info(
        `Telegram: sent ${newLoans.length} loans`
      );
      return true;
    }

    const errBody = await res.text();
    logger.error(
      `Telegram: API error ${res.status} — `
      + errBody
    );
    return false;

  } catch (err) {
    logger.error(
      `Telegram: failed — ${err.message}`
    );
    return false;
  }
}

module.exports = { sendTelegram };

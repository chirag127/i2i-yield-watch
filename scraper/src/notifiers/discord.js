// scraper/discord.js
// Discord Webhook notification channel.
// Uses native fetch (Node 18+). No external deps.
// Sends ONLY the new high-yield loans that have
// not been seen before (loanId dedup is applied
// in scraper/index.js before this is called).
// Discord limits each webhook message to 10 embeds
// (and 25 fields per embed), so the list is
// chunked across multiple sequential messages
// when needed.
//
// Embed content matches the compact line-based
// block defined in transform.js -> formatLoanBlock().
// First line is the embed title; the rest become
// the embed description (joined with \n). The URL
// line, when present, becomes the embed URL so
// the title is clickable. NO field labels.

const logger = require('../utils/logger');
const { formatLoanBlock } = require('../core/transform');

// Discord: 10 embeds per webhook message.
const MAX_EMBEDS_PER_MESSAGE = 10;
// Discord: 4096 chars per embed description.
const MAX_DESCRIPTION_LENGTH = 4096;

/**
 * Build a single embed for a loan. First line is
 * the embed title; the rest become the embed
 * description (joined with \n). The URL line, when
 * present, becomes the embed URL. NO labels.
 */
function buildLoanEmbed(loan) {
  const rate = loan.interestRate || 0;
  const color = rate >= 70
    ? 0xFF4444
    : rate >= 50
      ? 0xFF8800
      : 0x6B7280;

  const lines = formatLoanBlock(loan);
  const title = lines.length > 0
    ? lines[0]
    : `🔥 ${rate}% p.a.`;

  // Find the URL line (always last per formatLoanBlock
  // order, but we search for robustness).
  let url = '';
  let descLines = lines.slice(1);
  for (let i = descLines.length - 1; i >= 0; i--) {
    if (/^https?:\/\//i.test(descLines[i])) {
      url = descLines[i];
      descLines = descLines
        .slice(0, i)
        .concat(descLines.slice(i + 1));
      break;
    }
  }
  const description = descLines
    .join('\n')
    .slice(0, MAX_DESCRIPTION_LENGTH);

  return {
    title: String(title).slice(0, 256),
    url: url || undefined,
    color,
    description: description || undefined,
    footer: loan.loanId
      ? { text: `Loan ${loan.loanId}` }
      : undefined,
  };
}

/**
 * Send a single Discord webhook payload.
 * @param {string} webhookUrl
 * @param {object} payload
 * @returns {Promise<boolean>}
 */
async function postDiscordMessage(
  webhookUrl, payload
) {
  const res = await fetch(webhookUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  if (res.ok || res.status === 204) return true;

  const errBody = await res.text();
  logger.error(
    `Discord: API error ${res.status} — `
    + errBody
  );
  return false;
}

/**
 * Send Discord notification containing the new
 * high-yield loans that have not been seen before
 * (fingerprint dedup is applied in
 * scraper/index.js). The payload is split into
 * multiple sequential webhook messages, each
 * carrying up to MAX_EMBEDS_PER_MESSAGE embeds.
 * @param {Array} loans
 * @param {object} stats
 * @param {string} dashboardUrl
 * @param {object} options - { rateThreshold }
 * @returns {Promise<boolean>}
 */
async function sendDiscord(
  loans, stats, dashboardUrl, options = {}
) {
  const webhookUrl =
    process.env.DISCORD_WEBHOOK_URL;

  if (!webhookUrl) {
    logger.warn(
      'Discord: missing DISCORD_WEBHOOK_URL'
    );
    return false;
  }

  try {
    const rateThreshold = options.rateThreshold
      != null
      ? options.rateThreshold
      : 50;

    // Sort by interest rate descending
    const sorted = [...loans].sort(
      (a, b) =>
        (b.interestRate || 0)
        - (a.interestRate || 0)
    );

    // Build all loan embeds
    const loanEmbeds = sorted.map(buildLoanEmbed);

    // Split into chunks of MAX_EMBEDS_PER_MESSAGE
    const chunks = [];
    for (let i = 0; i < loanEmbeds.length;
      i += MAX_EMBEDS_PER_MESSAGE) {
      chunks.push(
        loanEmbeds.slice(i, i + MAX_EMBEDS_PER_MESSAGE)
      );
    }

    let allOk = true;
    for (let i = 0; i < chunks.length; i++) {
      const isFirst = i === 0;
      const isLast = i === chunks.length - 1;

      const content = isFirst
        ? `🚨 **${sorted.length} new high-yield loan`
          + `${sorted.length === 1 ? '' : 's'} `
          + `(rate > ${rateThreshold}%) `
          + `(part ${i + 1}/${chunks.length})**\n`
          + `📊 Active: ${stats.activeCount || 0} `
          + `| 🎯 New: ${sorted.length}\n`
          + `🔗 [Dashboard](${dashboardUrl})`
        : `📦 Continued — part ${i + 1}/`
          + `${chunks.length}`;

      const payload = {
        username: 'i2i Yield Watch',
        content,
        embeds: chunks[i],
      };

      const ok = await postDiscordMessage(
        webhookUrl, payload
      );
      if (!ok) {
        allOk = false;
        logger.error(
          `Discord: chunk ${i + 1}/`
          + `${chunks.length} failed`
        );
      } else {
        logger.info(
          `Discord: chunk ${i + 1}/`
          + `${chunks.length} sent `
          + `(${chunks[i].length} embeds)`
        );
      }

      // Small delay between webhook calls to
      // respect Discord rate limits.
      if (!isLast) {
        await new Promise(
          (r) => setTimeout(r, 600)
        );
      }
    }

    if (allOk) {
      logger.info(
        `Discord: sent all ${sorted.length} loans `
        + `in ${chunks.length} message(s)`
      );
    }
    return allOk;

  } catch (err) {
    logger.error(
      `Discord: failed — ${err.message}`
    );
    return false;
  }
}

module.exports = {
  sendDiscord,
  buildLoanEmbed,
};

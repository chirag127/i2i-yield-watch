// scraper/discord.js
// Discord Webhook notification channel.
// Uses native fetch (Node 18+). No external deps.
// Sends rich embeds with loan details.

const logger = require('./logger');

/**
 * Send Discord notification with embed cards.
 * @param {Array} newLoans
 * @param {object} stats
 * @param {string} dashboardUrl
 * @returns {Promise<boolean>}
 */
async function sendDiscord(
  newLoans, stats, dashboardUrl
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
    // Sort by interest rate descending
    const sorted = [...newLoans].sort(
      (a, b) =>
        (b.interestRate || 0)
        - (a.interestRate || 0)
    );

    // Create embeds for top 10 loans
    // Discord limit: max 10 embeds per message
    const embeds = sorted.slice(0, 10).map(
      (loan) => {
        const rate = loan.interestRate || 0;
        // Red for ≥70%, orange for others
        const color = rate >= 70
          ? 0xFF4444 : 0xFF8800;

        const amt = loan.loanAmount
          ? `₹${loan.loanAmount
              .toLocaleString('en-IN')}`
          : 'N/A';

        const url = (loan.borrowerRef && loan.loanId)
          ? `https://www.i2ifunding.com/borrower/listing/public-profile/${loan.borrowerRef}/${loan.loanId}`
          : (loan.loanUrl || `https://www.i2ifunding.com/invest/loan-detail/${loan.loanId}`);

        return {
          title:
            `🔥 ${rate}% p.a. `
            + `| ${loan.location || 'Unknown'}`,
          url,
          color,
          fields: [
            {
              name: 'Loan Amount',
              value: amt,
              inline: true,
            },
            {
              name: 'Credit Score',
              value: loan.creditScore || 'N/A',
              inline: true,
            },
            {
              name: 'Yield Score',
              value: `${loan.yieldScore || 0}/100`,
              inline: true,
            },
            {
              name: 'Remaining',
              value: loan.fundingRemaining != null
                ? `${loan.fundingRemaining}%`
                : 'N/A',
              inline: true,
            },
            {
              name: 'Product',
              value: loan.product || 'N/A',
              inline: true,
            },
            {
              name: 'Loan ID',
              value: loan.loanId || 'N/A',
              inline: true,
            },
          ],
          footer: {
            text: `Priority: ${loan.priority}`,
          },
        };
      }
    );

    const payload = {
      username: 'i2i Yield Watch',
      content:
        `🚨 **${newLoans.length} new high-yield `
        + 'loans detected!**\n'
        + `📊 Active: ${stats.activeCount || 0} `
        + `| 🔗 [Dashboard](${dashboardUrl})`,
      embeds,
    };

    const res = await fetch(webhookUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });

    if (res.ok || res.status === 204) {
      logger.info(
        `Discord: sent ${newLoans.length} loans`
      );
      return true;
    }

    const errBody = await res.text();
    logger.error(
      `Discord: API error ${res.status} — `
      + errBody
    );
    return false;

  } catch (err) {
    logger.error(
      `Discord: failed — ${err.message}`
    );
    return false;
  }
}

module.exports = { sendDiscord };

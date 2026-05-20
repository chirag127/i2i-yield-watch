// scraper/notifier.js
// Multi-channel notification dispatcher.
// Each channel is independently optional — controlled
// by env vars. One grouped summary per scrape run.

const logger = require('./logger');
const { sendTelegram } = require('./telegram');
const { sendEmail } = require('./email');
const { sendDiscord } = require('./discord');

/**
 * Dispatch notifications to all enabled channels.
 * Each channel is independent — one failure does
 * not affect others.
 * @param {Array} newLoans - Loans to notify about
 * @param {object} stats - { activeCount, ... }
 * @param {string} dashboardUrl
 * @returns {Promise<object>} Results per channel
 */
async function sendNotifications(
  newLoans, stats, dashboardUrl
) {
  const results = {
    telegram: false,
    email: false,
    discord: false,
  };

  // Telegram
  if (process.env.TELEGRAM_ENABLED === 'true') {
    try {
      results.telegram = await sendTelegram(
        newLoans, stats, dashboardUrl
      );
    } catch (err) {
      logger.error(
        `Notifier: Telegram error — ${err.message}`
      );
    }
  } else {
    logger.info('Notifier: Telegram disabled');
  }

  // Email
  if (process.env.EMAIL_ENABLED === 'true') {
    try {
      results.email = await sendEmail(
        newLoans, stats, dashboardUrl
      );
    } catch (err) {
      logger.error(
        `Notifier: Email error — ${err.message}`
      );
    }
  } else {
    logger.info('Notifier: Email disabled');
  }

  // Discord
  if (process.env.DISCORD_ENABLED === 'true') {
    try {
      results.discord = await sendDiscord(
        newLoans, stats, dashboardUrl
      );
    } catch (err) {
      logger.error(
        `Notifier: Discord error — ${err.message}`
      );
    }
  } else {
    logger.info('Notifier: Discord disabled');
  }

  logger.info(
    'Notification results: '
    + JSON.stringify(results)
  );

  return results;
}

module.exports = { sendNotifications };

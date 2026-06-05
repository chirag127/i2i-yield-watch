// scraper/notifier.js
// Multi-channel notification dispatcher.
// Channels run in parallel; one failure does not
// block the others.

const logger = require('../utils/logger');
const { sendTelegram } = require('./telegram');
const { sendEmail } = require('./email');
const { sendDiscord } = require('./discord');
const { sendNtfy } = require('./ntfy');

/**
 * True when at least one channel reported success.
 * Used by the orchestrator to mark loanIds notified.
 * @param {object} results
 * @returns {boolean}
 */
function wasAnyChannelSuccessful(results) {
  return !!(
    results.telegram
    || results.email
    || results.discord
    || results.ntfy
  );
}

/**
 * Dispatch notifications to all enabled channels.
 * @param {Array} qualifyingLoans
 * @param {object} stats
 * @param {string} dashboardUrl
 * @param {object} options
 * @returns {Promise<object>}
 */
async function sendNotifications(
  qualifyingLoans, stats, dashboardUrl, options = {}
) {
  const results = {
    telegram: false,
    email: false,
    discord: false,
    ntfy: false,
  };

  if (!Array.isArray(qualifyingLoans)
    || qualifyingLoans.length === 0) {
    logger.info(
      'Notifier: no qualifying loans to send'
    );
    return results;
  }

  const rateThreshold = options.rateThreshold
    != null
    ? options.rateThreshold
    : 50;

  const tasks = [];

  if (process.env.TELEGRAM_ENABLED === 'true') {
    tasks.push(
      sendTelegram(
        qualifyingLoans, stats, dashboardUrl,
        { rateThreshold }
      )
        .then((ok) => ({ channel: 'telegram', ok }))
        .catch((err) => {
          logger.error(
            `Notifier: Telegram error — ${err.message}`
          );
          return { channel: 'telegram', ok: false };
        })
    );
  } else {
    logger.info('Notifier: Telegram disabled');
  }

  if (process.env.EMAIL_ENABLED === 'true') {
    tasks.push(
      sendEmail(
        qualifyingLoans, stats, dashboardUrl,
        { rateThreshold }
      )
        .then((ok) => ({ channel: 'email', ok }))
        .catch((err) => {
          logger.error(
            `Notifier: Email error — ${err.message}`
          );
          return { channel: 'email', ok: false };
        })
    );
  } else {
    logger.info('Notifier: Email disabled');
  }

  if (process.env.DISCORD_ENABLED === 'true') {
    tasks.push(
      sendDiscord(
        qualifyingLoans, stats, dashboardUrl,
        { rateThreshold }
      )
        .then((ok) => ({ channel: 'discord', ok }))
        .catch((err) => {
          logger.error(
            `Notifier: Discord error — ${err.message}`
          );
          return { channel: 'discord', ok: false };
        })
    );
  } else {
    logger.info('Notifier: Discord disabled');
  }

  if (process.env.NTFY_ENABLED === 'true') {
    tasks.push(
      sendNtfy(
        qualifyingLoans, stats, dashboardUrl,
        { rateThreshold }
      )
        .then((ok) => ({ channel: 'ntfy', ok }))
        .catch((err) => {
          logger.error(
            `Notifier: ntfy error — ${err.message}`
          );
          return { channel: 'ntfy', ok: false };
        })
    );
  } else {
    logger.info('Notifier: ntfy disabled');
  }

  const settled = await Promise.all(tasks);
  for (const { channel, ok } of settled) {
    results[channel] = ok;
  }

  logger.info(
    'Notification results: '
    + JSON.stringify(results)
  );

  return results;
}

module.exports = {
  sendNotifications,
  wasAnyChannelSuccessful,
};

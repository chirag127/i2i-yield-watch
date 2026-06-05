// scraper/email.js
// Gmail SMTP notification via nodemailer.

const nodemailer = require('nodemailer');
const logger = require('../utils/logger');
const { formatLoanBlock } = require('../core/transform');

let cachedTransporter = null;
let cachedAuthKey = null;

/**
 * Reuse a single SMTP transporter per credentials pair.
 */
function getTransporter(from, pass) {
  const key = `${from}:${pass}`;
  if (cachedTransporter && cachedAuthKey === key) {
    return cachedTransporter;
  }
  cachedTransporter = nodemailer.createTransport({
    service: 'gmail',
    auth: { user: from, pass },
  });
  cachedAuthKey = key;
  return cachedTransporter;
}

/**
 * Escape a string for safe insertion into HTML.
 */
function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * Render a single loan as a card.
 */
function renderLoanCard(loan, _rateThreshold) {
  const lines = formatLoanBlock(loan);
  const rate = loan.interestRate || 0;
  const borderColor = rate >= 70
    ? '#ff4444' : rate >= 50
      ? '#ff8800' : '#6b7280';

  const header = lines.length > 0
    ? `<div style="
         font-size: 22px;
         font-weight: bold;
         color: ${borderColor};
         margin-bottom: 8px;
       ">${esc(lines[0])}</div>`
    : '';

  const body = lines.slice(1)
    .map((l) => (
      `<p style="
         margin: 4px 0;
         color: #222;
         font-size: 13px;
         line-height: 1.5;
         word-wrap: break-word;
         overflow-wrap: anywhere;
       ">${esc(l)}</p>`
    )).join('');

  return `
    <div style="
      border-left: 4px solid ${borderColor};
      background: #fafafa;
      border-radius: 6px;
      padding: 12px 16px;
      margin: 12px 0;
    ">
      ${header}
      <div>${body}</div>
    </div>`;
}

/**
 * Generate styled HTML email body.
 */
function formatEmailHtml(
  loans, stats, dashboardUrl, options = {}
) {
  const rateThreshold = options.rateThreshold != null
    ? options.rateThreshold
    : 50;

  const sorted = [...loans].sort(
    (a, b) => (b.interestRate || 0) - (a.interestRate || 0)
  );

  const cards = sorted.map(
    (l) => renderLoanCard(l, rateThreshold)
  ).join('');

  return `
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport"
    content="width=device-width, initial-scale=1">
</head>
<body style="
  font-family: Arial, sans-serif;
  background: #f5f5f5;
  margin: 0;
  padding: 20px;
">
  <div style="
    max-width: 800px;
    margin: 0 auto;
    background: #fff;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
  ">
    <div style="
      background: linear-gradient(135deg, #ff4444, #ff6b35);
      padding: 24px;
      color: #fff;
    ">
      <h1 style="margin: 0; font-size: 22px;">
        🚨 ${sorted.length} New High-Yield Loan${sorted.length === 1 ? '' : 's'}
        (rate &gt; ${rateThreshold}%)
      </h1>
      <p style="margin: 8px 0 0; opacity: 0.9;">
        i2i Yield Watch — ${new Date().toUTCString()}
      </p>
    </div>
    <div style="padding: 20px;">
      ${cards}
      <div style="
        margin-top: 24px;
        padding: 16px;
        background: #f8f9fa;
        border-radius: 6px;
        text-align: center;
      ">
        <p style="margin: 0;">
          📊 Total Active: ${stats.activeCount || 0}
           &nbsp;|&nbsp;
           🎯 Qualifying: ${stats.qualifyingCount != null
             ? stats.qualifyingCount
             : sorted.length}
        </p>
        <a href="${esc(dashboardUrl)}" style="
          display: inline-block;
          margin-top: 12px;
          padding: 10px 24px;
          background: #ff4444;
          color: #fff;
          text-decoration: none;
          border-radius: 6px;
          font-weight: bold;
        ">View Dashboard →</a>
      </div>
    </div>
  </div>
</body>
</html>`;
}

/**
 * Send email notification via Gmail SMTP.
 */
async function sendEmail(
  loans, stats, dashboardUrl, options = {}
) {
  const from = process.env.EMAIL_FROM;
  const pass = process.env.EMAIL_APP_PASSWORD;
  const to = process.env.EMAIL_TO;

  if (!from || !pass || !to) {
    logger.warn(
      'Email: missing FROM, APP_PASSWORD, or TO'
    );
    return false;
  }

  try {
    const transporter = getTransporter(from, pass);

    const topRate = loans.length > 0
      ? Math.max(
          ...loans.map(
            (l) => l.interestRate || 0
          )
        )
      : 0;

    const rateThreshold = options.rateThreshold
      != null
      ? options.rateThreshold
      : 50;

    const htmlBody = formatEmailHtml(
      loans, stats, dashboardUrl, options
    );

    await transporter.sendMail({
      from: `"i2i Yield Watch" <${from}>`,
      to,
      subject:
        `🚨 ${loans.length} New High-Yield Loan`
        + `${loans.length === 1 ? '' : 's'}`
        + ` (rate > ${rateThreshold}%)`
        + ` | Top: ${topRate}% p.a.`,
      html: htmlBody,
    });

    logger.info(
      `Email: sent to ${to} `
      + `(${loans.length} loans)`
    );
    return true;

  } catch (err) {
    logger.error(
      `Email: failed — ${err.message}`
    );
    return false;
  }
}

module.exports = {
  sendEmail,
  formatEmailHtml,
  esc,
  getTransporter,
};

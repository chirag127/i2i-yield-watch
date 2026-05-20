// scraper/email.js
// Gmail SMTP notification via nodemailer.
// Uses Gmail App Password (NOT account password).
// Requires 2FA enabled on the Google account.

const nodemailer = require('nodemailer');
const logger = require('./logger');

/**
 * Generate styled HTML email body with loan cards.
 * @param {Array} newLoans
 * @param {object} stats
 * @param {string} dashboardUrl
 * @returns {string}
 */
function formatEmailHtml(
  newLoans, stats, dashboardUrl
) {
  // Sort by interest rate descending
  const sorted = [...newLoans].sort(
    (a, b) =>
      (b.interestRate || 0)
      - (a.interestRate || 0)
  );
  const top = sorted.slice(0, 10);

  const loanRows = top.map((loan) => {
    const rate = loan.interestRate || 0;
    const color = rate >= 70
      ? '#ff4444' : rate >= 50
        ? '#ff8800' : '#6b7280';

    return `
      <tr>
        <td style="
          padding: 12px;
          border-bottom: 1px solid #eee;
          text-align: center;
        ">
          <span style="
            color: ${color};
            font-weight: bold;
            font-size: 18px;
          ">${rate}%</span>
        </td>
        <td style="
          padding: 12px;
          border-bottom: 1px solid #eee;
        ">${loan.location || 'N/A'}</td>
        <td style="
          padding: 12px;
          border-bottom: 1px solid #eee;
        ">₹${
  loan.loanAmount
    ? loan.loanAmount.toLocaleString('en-IN')
    : 'N/A'
}</td>
        <td style="
          padding: 12px;
          border-bottom: 1px solid #eee;
          text-align: center;
        ">${loan.yieldScore || 0}/100</td>
        <td style="
          padding: 12px;
          border-bottom: 1px solid #eee;
        ">${loan.creditScore || 'N/A'}</td>
        <td style="
          padding: 12px;
          border-bottom: 1px solid #eee;
          text-align: center;
        ">${
  loan.fundingRemaining != null
    ? loan.fundingRemaining + '%'
    : 'N/A'
}</td>
        <td style="
          padding: 12px;
          border-bottom: 1px solid #eee;
        ">${loan.product || 'N/A'}</td>
      </tr>`;
  }).join('');

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
      background: linear-gradient(135deg,
        #ff4444, #ff6b35);
      padding: 24px;
      color: #fff;
    ">
      <h1 style="margin: 0; font-size: 22px;">
        🚨 ${newLoans.length} New High-Yield Loans
      </h1>
      <p style="margin: 8px 0 0; opacity: 0.9;">
        i2i Yield Watch — ${new Date().toUTCString()}
      </p>
    </div>
    <div style="padding: 20px;">
      <table style="
        width: 100%;
        border-collapse: collapse;
        font-size: 14px;
      ">
        <thead>
          <tr style="background: #f8f9fa;">
            <th style="padding: 10px;">Rate</th>
            <th style="padding: 10px;">Location</th>
            <th style="padding: 10px;">Amount</th>
            <th style="padding: 10px;">Score</th>
            <th style="padding: 10px;">Credit</th>
            <th style="padding: 10px;">Remaining</th>
            <th style="padding: 10px;">Product</th>
          </tr>
        </thead>
        <tbody>${loanRows}</tbody>
      </table>
      ${sorted.length > 10
    ? `<p style="
            color: #666;
            text-align: center;
            margin-top: 16px;
          ">...and ${sorted.length - 10} more</p>`
    : ''}
      <div style="
        margin-top: 24px;
        padding: 16px;
        background: #f8f9fa;
        border-radius: 6px;
        text-align: center;
      ">
        <p style="margin: 0;">
          📊 Active: ${stats.activeCount || 0}
        </p>
        <a href="${dashboardUrl}" style="
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
 * @param {Array} newLoans
 * @param {object} stats
 * @param {string} dashboardUrl
 * @returns {Promise<boolean>}
 */
async function sendEmail(
  newLoans, stats, dashboardUrl
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
    const transporter = nodemailer.createTransport({
      service: 'gmail',
      auth: {
        user: from,
        pass: pass,
      },
    });

    const topRate = newLoans.length > 0
      ? Math.max(
          ...newLoans.map(
            (l) => l.interestRate || 0
          )
        )
      : 0;

    const htmlBody = formatEmailHtml(
      newLoans, stats, dashboardUrl
    );

    await transporter.sendMail({
      from: `"i2i Yield Watch" <${from}>`,
      to,
      subject:
        `🚨 ${newLoans.length} New High-Yield `
        + `Loans | Top Rate: ${topRate}% p.a.`,
      html: htmlBody,
    });

    logger.info(
      `Email: sent to ${to} `
      + `(${newLoans.length} loans)`
    );
    return true;

  } catch (err) {
    logger.error(
      `Email: failed — ${err.message}`
    );
    return false;
  }
}

module.exports = { sendEmail };

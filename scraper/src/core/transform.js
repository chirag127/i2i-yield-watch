// scraper/transform.js
// Convert the raw API response shape (lots of pl_*
// and bloan_* fields) into the normalized loan shape
// the rest of the scraper expects. The output matches
// what the legacy DOM parser produced so the
// notifiers, dashboard, archive, and changelog don't
// need any changes.

const {
  calculateYieldScore,
  getPriority,
} = require('../utils/scorer');
const logger = require('../utils/logger');

const NA = (v) => (
  v === null || v === undefined || v === ''
    || (typeof v === 'string'
      && /^(n\/?a|na|null|none|-|unknown|#####)$/i
        .test(v.trim()))
);

/**
 * Convert "DD-MM-YYYY" → ISO string. Returns null on
 * bad input.
 */
function parsePostedOn(s) {
  if (!s || typeof s !== 'string') return null;
  const m = s.match(/^(\d{2})-(\d{2})-(\d{4})$/);
  if (!m) return null;
  const dd = parseInt(m[1], 10);
  const mm = parseInt(m[2], 10);
  const yyyy = parseInt(m[3], 10);
  if (
    !Number.isFinite(dd) || !Number.isFinite(mm)
      || !Number.isFinite(yyyy)
  ) return null;
  const d = new Date(Date.UTC(yyyy, mm - 1, dd));
  if (isNaN(d.getTime())) return null;
  return d.toISOString();
}

/**
 * Combine a first + last name into a single string,
 * skipping NA parts.
 */
function combineName(first, last) {
  const f = NA(first) ? '' : String(first).trim();
  const l = NA(last) ? '' : String(last).trim();
  const out = [f, l].filter(Boolean).join(' ').trim();
  return out || null;
}

/**
 * Parse a numeric string like "6345.00" or "₹ 6,345"
 * to a JS number. Returns null on failure or NA.
 */
function toNumber(v) {
  if (v === null || v === undefined) return null;
  if (typeof v === 'number') {
    return Number.isFinite(v) ? v : null;
  }
  const s = String(v).replace(/[,₹\s]/g, '');
  if (!s) return null;
  const n = parseFloat(s);
  return Number.isFinite(n) ? n : null;
}

/**
 * Normalize the rate field. The API uses string
 * decimals like "16.07" — we want a JS number. Some
 * rows use pl_applicable_rate, others pl_current_rate,
 * others pl_inital_rate. The applicable rate is the
 * one currently offered to lenders.
 */
function pickRate(row) {
  const candidates = [
    row.pl_applicable_rate,
    row.pl_current_rate,
    row.pl_inital_rate,
  ];
  for (const c of candidates) {
    const n = toNumber(c);
    if (n !== null) return n;
  }
  return null;
}

/**
 * Build the public loan-detail URL. The listing page
 * is an Angular SPA where "View Details" is JS-driven
 * (href is empty). The most useful fallback is the
 * public profile for the borrower; that page lists
 * every loan the borrower has applied for.
 */
function buildLoanUrl(borrowerRef, _loanId) {
  if (!borrowerRef) return '';
  return (
    'https://www.i2ifunding.com/borrower/listing/'
    + 'public-profile/'
    + encodeURIComponent(String(borrowerRef))
  );
}

/**
 * Normalize the CIBIL score. The API uses:
 *   ""      → not pulled
 *   "-1"    → no history
 *   "510"   → real number
 */
function pickCredit(row) {
  const cibil = row.usr_cibil_score;
  const bloan = row.bloan_cibil_score;
  if (NA(cibil)) {
    if (bloan === '-1' || bloan === -1) return {
      text: 'No History', numeric: null,
    };
    return { text: null, numeric: null };
  }
  const n = toNumber(cibil);
  return {
    text: n !== null ? String(n) : String(cibil),
    numeric: n,
  };
}

/**
 * Tenure: the API gives `bloan_tenure` as a number
 * (e.g. 30) and `tenure_type` as "d" / "m" / "y"
 * (day / month / year). We render it like
 * "30 Days" / "6 Months" / "1 Years" so it matches
 * the legacy DOM output the dashboard already
 * understands.
 */
function formatTenure(value, type) {
  const n = toNumber(value);
  if (n === null) return null;
  const t = (type || 'd').toLowerCase();
  const unit = t === 'd' ? 'Days'
    : t === 'm' ? 'Months' : 'Years';
  return `${n} ${unit}`;
}

/**
 * Compute funding progress. The API has
 *   pl_amt        = total loan amount
 *   pl_amt_left   = amount still to be raised
 *   pl_final_amt  = total amount actually funded
 *                   (same as pl_amt - pl_amt_left
 *                    for non-disbursed loans)
 *   pl_disbursed_amt = amount paid to borrower
 */
function computeFunding(row) {
  const total = toNumber(row.pl_amt);
  const left = toNumber(row.pl_amt_left);
  if (total === null || total <= 0) {
    return {
      loanAmount: null,
      amountFunded: null,
      amountLeft: null,
      fundedPercent: null,
      fundingRemaining: null,
      isFullyFunded: false,
    };
  }
  const funded = left === null
    ? null : Math.max(total - left, 0);
  const pct = funded === null
    ? null : parseFloat(
      ((funded / total) * 100).toFixed(2)
    );
  const remaining = pct === null
    ? null : parseFloat((100 - pct).toFixed(2));
  const isFullyFunded = (left !== null && left <= 0)
    || (pct !== null && pct >= 100)
    || row.pl_status !== 1;
  return {
    loanAmount: total,
    amountFunded: funded,
    amountLeft: left,
    fundedPercent: pct,
    fundingRemaining: remaining,
    isFullyFunded,
  };
}

/**
 * Convert one raw API loan row into the normalized
 * shape. Throws nothing — every field is normalized
 * to `null` on missing/NA so the output is always
 * JSON-safe.
 */
function transformLoan(row) {
  if (!row || typeof row !== 'object') {
    throw new Error('transformLoan: row is not an object');
  }
  const loanId = row.pl_bloan_id || row.pl_id;
  if (loanId === undefined || loanId === null) {
    throw new Error(
      'transformLoan: row missing pl_bloan_id / pl_id'
    );
  }
  const borrowerRef = row.pl_user_id || null;
  const name = combineName(row.usr_fname, row.usr_lname);
  const age = toNumber(row.usr_age);
  const credit = pickCredit(row);
  const tenure = formatTenure(
    row.bloan_tenure, row.tenure_type
  );
  const interestRate = pickRate(row);
  const funding = computeFunding(row);
  const madeLiveOn = parsePostedOn(row.postedOn);

  const base = {
    loanId: String(loanId),
    borrowerRef: borrowerRef !== null
      ? String(borrowerRef) : null,
    name,
    age: age !== null ? Math.trunc(age) : null,
    location: NA(row.location) ? null
      : String(row.location).trim(),
    residenceType: NA(row.residence_type) ? null
      : String(row.residence_type).trim(),
    purpose: NA(row.purpose) ? null
      : String(row.purpose).trim(),
    creditScore: credit.text,
    creditScoreNumeric: credit.numeric,
    riskCategory: NA(row.bloan_i2i_category) ? null
      : String(row.bloan_i2i_category).trim().toUpperCase(),
    interestRate,
    tenure,
    product: NA(row.product_name) ? null
      : String(row.product_name).trim(),
    madeLiveOn,
    employmentType: NA(row.emp_type) ? null
      : String(row.emp_type).trim(),
    monthlyIncome: toNumber(row.fin_monthly_income),
    professionName: NA(row.em_self_profession) ? null
      : String(row.em_self_profession).trim(),
    businessName: NA(row.emp_comp_name) ? null
      : String(row.emp_comp_name).trim(),
    loanAmount: funding.loanAmount,
    amountFunded: funding.amountFunded,
    amountLeft: funding.amountLeft,
    fundedPercent: funding.fundedPercent,
    fundingRemaining: funding.fundingRemaining,
    isFullyFunded: funding.isFullyFunded,
    loanUrl: buildLoanUrl(borrowerRef, loanId),
  };

  return {
    ...base,
    scrapedAt: new Date().toISOString(),
    yieldScore: calculateYieldScore(base),
    priority: getPriority(base.interestRate),
  };
}

/**
 * Apply transformLoan to a whole array, dropping any
 * rows that fail to transform (with a warning).
 */
function transformLoans(rawRows) {
  const out = [];
  for (const row of rawRows) {
    try {
      out.push(transformLoan(row));
    } catch (e) {
      logger.warn(
        `transformLoans: skipping bad row: ${e.message}`
      );
    }
  }
  return out;
}

/**
 * Format an ISO date (or any parseable string) as
 * "DD-MM-YYYY" for display in messages. Returns
 * null on failure.
 */
function formatPostedOn(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  const dd = String(d.getUTCDate()).padStart(2, '0');
  const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
  const yyyy = d.getUTCFullYear();
  return `${dd}-${mm}-${yyyy}`;
}

/**
 * Format an integer (rupees) with Indian-style
 * thousand separators. Returns null on invalid.
 */
function inr(n) {
  if (n == null || !Number.isFinite(n)) return null;
  return '₹' + Math.round(n).toLocaleString('en-IN');
}

/**
 * Strip the leading "₹" from an inr()-formatted
 * amount so it can be embedded inline in a line
 * (we add the ₹ once at the start of the group).
 * e.g. "₹1,23,456" -> "1,23,456"
 */
function bare(x) {
  if (!x) return null;
  return String(x).replace(/^₹\s*/, '').trim();
}

/**
 * Build the shared "label-free" line list used by
 * every notification channel. Each line is a
 * complete string with no field labels — the user
 * understands from context (e.g. "2 Months" is
 * tenure, a personal-loan narrative is purpose).
 *
 * Lines are ordered by lending-decision importance
 * (most important first):
 *
 *   1.  Rate + Yield Score       ← TOP (the ROI signal)
 *   2.  Identity                 ← i2i-# / Loan ID
 *   3.  Funding                  ← total / funded / left
 *   4.  Credit + Risk            ← default-risk signals
 *   5.  Borrower                 ← name / age / location
 *   6.  Employment               ← type — profession
 *   7.  Income / Tenure / Home   ← ability to repay
 *   8.  Purpose                  ← what the loan is for
 *   9.  Made Live On             ← freshness
 *  10.  URL                      ← link to the listing
 *
 * Rules:
 *   - NO field labels. Words like "Loan", "Credit",
 *     "Borrower", "Employment", "Tenure", "Purpose",
 *     "Location" are dropped — the value's own
 *     content carries the meaning.
 *   - The ONLY `%` anywhere in the message is in
 *     line 1 (the interest rate). Funding rows show
 *     only Indian-formatted amounts (no %).
 *   - Missing data drops the whole line (no empty
 *     rows, no "N/A" leaks).
 *   - All amounts use inr() (₹1,23,456 style).
 *
 * Channel renderers receive this list verbatim:
 *   - telegram:  first line bolded, the rest plain,
 *                joined with newlines, loans separated
 *                by a blank line.
 *   - email:     each line becomes a <p>, first line
 *                styled larger with a rate-colored
 *                left border.
 *   - discord:   first line becomes embed title,
 *                remaining lines become embed
 *                description (joined with \n), URL
 *                line becomes the embed URL.
 *   - ntfy:      joined with \n into a single body.
 *
 * @param {object} loan
 * @returns {string[]} ordered list of lines
 */
function formatLoanBlock(loan) {
  const lines = [];

  // Line 1 — Rate + Yield (most important)
  const ratePieces = [];
  if (loan.interestRate != null
    && Number.isFinite(loan.interestRate)) {
    ratePieces.push(
      `🔥 ${loan.interestRate.toFixed(2)}% p.a.`
    );
  }
  if (loan.yieldScore != null
    && Number.isFinite(loan.yieldScore)) {
    ratePieces.push(
      `Yield ${loan.yieldScore.toFixed(2)}/100`
    );
  }
  if (ratePieces.length) {
    lines.push(ratePieces.join(' · '));
  }

  // Line 2 — Identity (i2i-# / Loan ID)
  const idPieces = [];
  if (!NA(loan.borrowerRef)) {
    idPieces.push(`i2i-#${loan.borrowerRef}`);
  }
  if (!NA(loan.loanId)) {
    idPieces.push(`Loan ${loan.loanId}`);
  }
  if (idPieces.length) {
    lines.push(idPieces.join(' · '));
  }

  // Line 3 — Funding (total / funded / left)
  // All three pieces are independently optional,
  // but we join them with "·" so a single line
  // carries the full funding story.
  const fundPieces = [];
  const totalBare = bare(inr(loan.loanAmount));
  if (totalBare) fundPieces.push(`₹${totalBare}`);
  // Derive amountFunded from loanAmount - amountLeft
  // if not already set (so test loans that bypassed
  // transformLoan still render the Funded row).
  const fundedAmount = loan.amountFunded != null
    ? loan.amountFunded
    : (loan.loanAmount != null && loan.amountLeft != null
      ? loan.loanAmount - loan.amountLeft
      : null);
  const fundedBare = bare(inr(fundedAmount));
  if (fundedBare) {
    fundPieces.push(`₹${fundedBare} funded`);
  }
  const leftBare = bare(inr(loan.amountLeft));
  if (leftBare) {
    fundPieces.push(`₹${leftBare} left`);
  }
  if (fundPieces.length) {
    lines.push(fundPieces.join(' · '));
  }

  // Line 4 — Credit + Risk
  const creditPieces = [];
  if (!NA(loan.creditScore)) {
    creditPieces.push(`Credit ${loan.creditScore}`);
  }
  if (!NA(loan.riskCategory)) {
    creditPieces.push(`Risk ${loan.riskCategory}`);
  }
  if (creditPieces.length) {
    lines.push(creditPieces.join(' · '));
  }

  // Line 5 — Borrower (name / age / location)
  const borrowerPieces = [];
  if (!NA(loan.name)) {
    borrowerPieces.push(String(loan.name));
  }
  if (loan.age != null && Number.isFinite(loan.age)) {
    borrowerPieces.push(`Age ${Math.trunc(loan.age)}`);
  }
  if (!NA(loan.location)) {
    borrowerPieces.push(String(loan.location));
  }
  if (borrowerPieces.length) {
    lines.push(borrowerPieces.join(' · '));
  }

  // Line 6 — Employment (type — profession / business)
  const empPieces = [];
  if (!NA(loan.employmentType)) {
    empPieces.push(String(loan.employmentType));
  }
  if (!NA(loan.professionName)) {
    empPieces.push(String(loan.professionName));
  } else if (!NA(loan.businessName)) {
    empPieces.push(String(loan.businessName));
  }
  if (empPieces.length) {
    lines.push(empPieces.join(' — '));
  }

  // Line 7 — Income / Tenure / Residence (ability to
  // repay). Residence is optional; only show this
  // line if at least one piece is present.
  const repPieces = [];
  const incomeBare = bare(inr(loan.monthlyIncome));
  if (incomeBare) {
    repPieces.push(`₹${incomeBare}/mo`);
  }
  if (!NA(loan.tenure)) {
    repPieces.push(String(loan.tenure));
  }
  if (!NA(loan.residenceType)) {
    repPieces.push(String(loan.residenceType));
  }
  if (repPieces.length) {
    lines.push(repPieces.join(' · '));
  }

  // Line 8 — Purpose (the loan's narrative reason)
  if (!NA(loan.purpose)) {
    lines.push(String(loan.purpose));
  }

  // Line 9 — Made Live On
  const madeLive = formatPostedOn(loan.madeLiveOn);
  if (madeLive) {
    lines.push(`Live ${madeLive}`);
  }

  // Line 10 — URL (always last; never omitted)
  if (!NA(loan.loanUrl)) {
    lines.push(String(loan.loanUrl));
  }

  return lines;
}

module.exports = {
  transformLoan,
  transformLoans,
  buildLoanUrl,
  parsePostedOn,
  formatPostedOn,
  combineName,
  toNumber,
  pickRate,
  pickCredit,
  formatTenure,
  computeFunding,
  inr,
  formatLoanBlock,
  NA,
};

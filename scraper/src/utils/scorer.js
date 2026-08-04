// scraper/scorer.js
// Yield scoring engine — opportunity score, NOT
// risk score. Higher interest = better score.
// Category X is treated neutrally. High rates are
// NEVER penalized. No credit history is neutral
// (0.5) — new-to-credit borrowers are not
// penalized.

/**
 * Normalization bounds for scoring dimensions.
 * These are intentionally wide to accommodate
 * extreme values seen on the platform.
 */
const BOUNDS = {
  interestRate: { min: 0, max: 200 },
  creditScore: { min: 300, max: 900 },
  monthlyIncome: { min: 0, max: 2000000 },
  fundingRemaining: { min: 0, max: 100 },
  loanAmount: { min: 0, max: 5000000 },
};

/**
 * Weights for each scoring dimension.
 * interestRate is the dominant yield signal (55%).
 * creditScore is second priority (30%).
 * Remaining 15% split across income, funding,
 * and loan amount.
 * Sum = 1.0
 */
const WEIGHTS = {
  interestRate: 0.55,
  creditScore: 0.30,
  monthlyIncome: 0.05,
  fundingRemaining: 0.05,
  loanAmount: 0.05,
};

/**
 * Min-max normalize a value to 0–1 range.
 * Returns 0.5 (neutral) for null/undefined/NaN.
 * Clamps to [0, 1] for out-of-bounds values.
 * @param {number|null} value
 * @param {number} min
 * @param {number} max
 * @returns {number} Normalized value 0–1
 */
function normalize(value, min, max) {
  if (
    value === null
    || value === undefined
    || isNaN(value)
  ) {
    return 0.5;
  }
  if (max === min) return 0.5;
  const clamped = Math.max(
    min, Math.min(max, value)
  );
  return (clamped - min) / (max - min);
}

/**
 * Calculate yield opportunity score (0–100).
 * Higher score = better lending opportunity.
 * No credit history / null credit score is treated
 * as neutral (0.5) — never penalized.
 * @param {Object} loan
 * @returns {number} Score 0–100, rounded to 2dp
 */
function calculateYieldScore(loan) {
  // Interest rate: primary yield signal
  const rateNorm = normalize(
    loan.interestRate,
    BOUNDS.interestRate.min,
    BOUNDS.interestRate.max
  );

  // Credit score: higher = better repayment.
  // Null / "No History" / "No Credit" = neutral
  // (0.5). This is intentional: new-to-credit
  // borrowers are NOT penalized.
  let creditNorm = 0.5;
  if (
    loan.creditScoreNumeric !== null
    && loan.creditScoreNumeric !== undefined
    && !isNaN(loan.creditScoreNumeric)
  ) {
    creditNorm = normalize(
      loan.creditScoreNumeric,
      BOUNDS.creditScore.min,
      BOUNDS.creditScore.max
    );
  }

  // Monthly income: higher = better capacity
  const incomeNorm = normalize(
    loan.monthlyIncome,
    BOUNDS.monthlyIncome.min,
    BOUNDS.monthlyIncome.max
  );

  // Funding remaining: more remaining = more
  // opportunity to invest
  const fundingNorm = normalize(
    loan.fundingRemaining,
    BOUNDS.fundingRemaining.min,
    BOUNDS.fundingRemaining.max
  );

  // Loan amount: slight preference for variety
  const amountNorm = normalize(
    loan.loanAmount,
    BOUNDS.loanAmount.min,
    BOUNDS.loanAmount.max
  );

  // Weighted sum
  const raw =
    rateNorm * WEIGHTS.interestRate
    + creditNorm * WEIGHTS.creditScore
    + incomeNorm * WEIGHTS.monthlyIncome
    + fundingNorm * WEIGHTS.fundingRemaining
    + amountNorm * WEIGHTS.loanAmount;

  // Scale to 0–100
  return parseFloat((raw * 100).toFixed(2));
}

/**
 * Determine notification priority level based on
 * interest rate thresholds.
 * @param {number|null} interestRate
 * @returns {'VERY_HIGH'|'MEDIUM'|'LOW'} Priority
 */
function getPriority(interestRate) {
  if (interestRate === null || interestRate === undefined) {
    return 'LOW';
  }
  const highThreshold = parseFloat(
    process.env.HIGH_PRIORITY_RATE_THRESHOLD || '70'
  );
  const medThreshold = parseFloat(
    process.env.MEDIUM_PRIORITY_RATE_THRESHOLD
    || '50'
  );
  if (interestRate >= highThreshold) {
    return 'VERY_HIGH';
  }
  if (interestRate >= medThreshold) return 'MEDIUM';
  return 'LOW';
}

/**
 * Credit-score rank for sorting. Higher rank = higher priority.
 * Design choice (per user): a MISSING / "no credit history" score
 * ranks HIGH — new-to-credit borrowers are FAVORED, not penalized.
 * Among borrowers WITH a known score, higher score = higher rank.
 *
 * Returns a comparable number:
 *   - no-credit  -> 1000  (above any real 300–900 score)
 *   - known      -> the numeric score itself (300–900)
 * @param {Object} loan
 * @returns {number}
 */
function creditRank(loan) {
  const s = loan.creditScoreNumeric;
  if (s === null || s === undefined || isNaN(s)) {
    return 1000; // no-credit ranks above the max real score (900)
  }
  return s;
}

/**
 * Strict multi-key comparator for loan priority (for Array.sort).
 * Priority order (each key only breaks ties of the previous one):
 *   1. Interest rate      (higher first)  — primary yield signal
 *   2. Monthly income     (higher first)  — repayment capacity (ranked
 *                                           ABOVE credit per user)
 *   3. Credit rank        (higher first)  — no-credit ranks HIGH
 *   4. Loan amount        (higher first)  — final tiebreaker
 * Funding-remaining is intentionally NOT a sort key (per user: it is not a
 * priority requirement — a nearly-funded high-rate loan still ranks high).
 * Nulls sort last within each numeric key (treated as -Infinity),
 * EXCEPT credit, where no-credit is deliberately ranked high.
 * @param {Object} a
 * @param {Object} b
 * @returns {number}
 */
function compareLoans(a, b) {
  const num = (v) =>
    v === null || v === undefined || isNaN(v) ? -Infinity : Number(v);

  const rate = num(b.interestRate) - num(a.interestRate);
  if (rate !== 0) return rate;

  const income = num(b.monthlyIncome) - num(a.monthlyIncome);
  if (income !== 0) return income;

  const credit = creditRank(b) - creditRank(a);
  if (credit !== 0) return credit;

  return num(b.loanAmount) - num(a.loanAmount);
}

/**
 * Return a NEW array of loans sorted by compareLoans (does not mutate).
 * @param {Object[]} loans
 * @returns {Object[]}
 */
function sortLoans(loans) {
  return [...loans].sort(compareLoans);
}

module.exports = {
  normalize,
  calculateYieldScore,
  getPriority,
  compareLoans,
  sortLoans,
  creditRank,
  BOUNDS,
  WEIGHTS,
};

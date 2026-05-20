// scraper/scorer.js
// Yield scoring engine — OPPORTUNITY score, NOT risk.
// Higher interest rate = higher score. Always.
// Category X is treated neutrally — never penalized.

/**
 * Fixed normalization bounds for all scoring
 * dimensions.
 */
const BOUNDS = {
  interestRate: { min: 0, max: 100 },
  creditScore: { min: 300, max: 900 },
  monthlyIncome: { min: 5000, max: 200000 },
  fundingRemaining: { min: 0, max: 100 },
  loanAmount: { min: 1000, max: 500000 },
};

/**
 * Weights for each scoring dimension.
 * interestRate is the primary yield signal.
 * Sum ≈ 1.0
 */
const WEIGHTS = {
  interestRate: 0.40,
  creditScore: 0.20,
  monthlyIncome: 0.15,
  fundingRemaining: 0.15,
  loanAmount: 0.10,
};

/**
 * Min-max normalize a value to [0, 1].
 * Returns 0.5 (neutral) for missing data.
 * @param {number|null} value
 * @param {number} min
 * @param {number} max
 * @returns {number}
 */
function normalize(value, min, max) {
  if (value === null || value === undefined) {
    return 0.5;
  }
  if (max === min) return 0.5;
  return Math.max(
    0,
    Math.min(1, (value - min) / (max - min))
  );
}

/**
 * Calculate yield score for a loan (0–100 scale).
 * Higher score = better opportunity.
 * @param {object} loan
 * @returns {number}
 */
function calculateYieldScore(loan) {
  const scores = {
    interestRate: normalize(
      loan.interestRate,
      BOUNDS.interestRate.min,
      BOUNDS.interestRate.max
    ),
    creditScore: normalize(
      loan.creditScoreNumeric,
      BOUNDS.creditScore.min,
      BOUNDS.creditScore.max
    ),
    monthlyIncome: normalize(
      loan.monthlyIncome,
      BOUNDS.monthlyIncome.min,
      BOUNDS.monthlyIncome.max
    ),
    fundingRemaining: normalize(
      loan.fundingRemaining,
      BOUNDS.fundingRemaining.min,
      BOUNDS.fundingRemaining.max
    ),
    loanAmount: normalize(
      loan.loanAmount,
      BOUNDS.loanAmount.min,
      BOUNDS.loanAmount.max
    ),
  };

  const raw = Object.entries(WEIGHTS).reduce(
    (sum, [key, weight]) => {
      return sum + (scores[key] * weight);
    },
    0
  );

  // Scale to 0–100 and round to 2 decimals
  return parseFloat((raw * 100).toFixed(2));
}

/**
 * Determine priority based on interest rate.
 * ≥70% = VERY_HIGH (primary targets)
 * ≥50% = MEDIUM
 * <50% = LOW (still stored, not highlighted)
 * @param {number|null} interestRate
 * @returns {string}
 */
function getPriority(interestRate) {
  if (!interestRate) return 'LOW';
  const threshold = parseFloat(
    process.env.HIGH_PRIORITY_RATE_THRESHOLD || '70'
  );
  const medThreshold = parseFloat(
    process.env.MEDIUM_PRIORITY_RATE_THRESHOLD || '50'
  );
  if (interestRate >= threshold) return 'VERY_HIGH';
  if (interestRate >= medThreshold) return 'MEDIUM';
  return 'LOW';
}

module.exports = {
  normalize,
  calculateYieldScore,
  getPriority,
  BOUNDS,
  WEIGHTS,
};

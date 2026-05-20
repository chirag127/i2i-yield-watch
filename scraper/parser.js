// scraper/parser.js
// DOM parsing and field extraction.
// Extracts ALL loan fields from each borrower row
// using page.evaluate() in a single DOM pass.
// The i2iFunding page uses an Angular SPA where
// each borrower row has labeled fields within
// nested elements. Text is extracted and parsed
// via regex patterns matched against the full
// cell text content.

const logger = require('./logger');
const {
  calculateYieldScore,
  getPriority,
} = require('./scorer');

/**
 * Parse all visible loan rows from the page.
 * @param {import('playwright').Page} page
 * @returns {Promise<Array>} Parsed and scored loans
 */
async function parseLoans(page) {
  const rawLoans = await page.evaluate(() => {
    const rows = Array.from(
      document.querySelectorAll(
        '.active-borrwer-list tbody tr'
      )
    ).filter(
      (row) =>
        row.querySelectorAll('td').length >= 4
    );

    /**
     * Extract and normalize text from element.
     */
    function txt(el) {
      if (!el) return '';
      return (el.innerText || el.textContent || '')
        .replace(/\s+/g, ' ')
        .trim();
    }

    /**
     * Return null if value looks blurred (#####).
     */
    function nullIfBlurred(value) {
      if (!value) return null;
      const v = value.trim();
      if (!v) return null;
      if (/^[#]+$/.test(v)) return null;
      return v;
    }

    /**
     * Parse a currency string to a number.
     */
    function parseNum(str) {
      if (!str) return null;
      const m = String(str)
        .replace(/,/g, '')
        .match(/[\d.]+/);
      return m ? parseFloat(m[0]) : null;
    }

    /**
     * Extract a field value using label-based
     * approach. Finds "Label :" pattern and
     * extracts until the next known label.
     */
    function extractField(text, label, nextLabels) {
      // Build regex: label followed by : then
      // capture until next label or end
      const nextPat = nextLabels.length > 0
        ? nextLabels.map(
            (l) => l.replace(
              /[.*+?^${}()|[\]\\]/g, '\\$&'
            )
          ).join('|')
        : '';
      const pattern = nextPat
        ? new RegExp(
            label
              + '\\s*:?\\s*(.+?)\\s*(?='
              + nextPat + '|$)',
            'i'
          )
        : new RegExp(
            label + '\\s*:?\\s*(.+?)\\s*$', 'i'
          );
      const match = text.match(pattern);
      return match ? match[1].trim() : null;
    }

    return rows.map((row) => {
      const cells = row.querySelectorAll('td');

      // ---- TD[0]: Personal Details ----
      const p = cells[0] ? txt(cells[0]) : '';

      const refMatch = p.match(
        /i2i-?\s*#?\s*(\d+)/i
      );
      const borrowerRef = refMatch
        ? refMatch[1] : null;

      // Extract name (between "Name :" and "Age")
      const nameField = extractField(
        p, 'Name', ['Age']
      );
      const name = nullIfBlurred(nameField);

      const ageMatch = p.match(
        /Age\s*:?\s*(\d+)/i
      );
      const age = ageMatch
        ? parseInt(ageMatch[1]) : null;

      // Location: extract between "Location :"
      // and "Residence Type"
      const locationField = extractField(
        p, 'Location',
        ['Residence Type', 'Guarantor']
      );
      const location = locationField || null;

      const residenceField = extractField(
        p, 'Residence Type',
        ['Guarantor', 'Name', '$']
      );
      const residenceType = residenceField
        ? residenceField.replace(
            /\s*Guarantor.*$/i, ''
          ).trim()
        : null;

      // ---- TD[1]: Loan Details ----
      const lc = cells[1] ? txt(cells[1]) : '';

      const loanIdMatch = lc.match(
        /Loan\s*Id\s*:?\s*(\d+)/i
      );
      const loanId = loanIdMatch
        ? loanIdMatch[1] : null;

      const purposeField = extractField(
        lc, 'Purpose',
        ['Credit Bureau', 'i2i Risk', 'Int']
      );
      const purpose = purposeField || null;

      const creditField = extractField(
        lc, 'Credit Bureau Score',
        ['i2i Risk', 'Int', 'Rate']
      );
      let creditScore = null;
      let creditScoreNumeric = null;
      if (creditField) {
        if (/no\s*history/i.test(creditField)) {
          creditScore = 'No History';
        } else {
          const csm = creditField.match(/(\d+)/);
          if (csm) {
            creditScore = csm[1];
            creditScoreNumeric = parseInt(csm[1]);
          } else {
            creditScore = creditField;
          }
        }
      }

      const riskField = extractField(
        lc, 'i2i Risk Category',
        ['Int\\.', 'Rate', 'Tenure', 'Product']
      );
      // Extract just the category letter(s)
      const riskCatMatch = riskField
        ? riskField.match(/^([A-Z]+)/i)
        : null;
      const riskCategory = riskCatMatch
        ? riskCatMatch[1].trim() : riskField;

      const rateMatch = lc.match(
        /Int\.\s*Rate\s*:?\s*([\d.]+)/i
      );
      const interestRate = rateMatch
        ? parseFloat(rateMatch[1]) : null;

      const tenureField = extractField(
        lc, 'Tenure',
        ['Product', 'Made Live', '$']
      );
      const tenure = tenureField || null;

      const productField = extractField(
        lc, 'Product',
        ['Made Live', 'Tenure', '$']
      );
      const product = productField || null;

      const liveField = extractField(
        lc, 'Made Live On',
        ['Product', 'Tenure', '$']
      );
      const madeLiveOn = liveField || null;

      // ---- TD[2]: Employment Details ----
      const ec = cells[2] ? txt(cells[2]) : '';

      const empField = extractField(
        ec, 'Employment Type',
        [
          'Monthly Income', 'Profession',
          'Business Name',
        ]
      );
      const employmentType = empField || null;

      const incomeMatch = ec.match(
        /Monthly Income\s*:?\s*[₹\s]*([\d,]+)/i
      );
      const monthlyIncome = incomeMatch
        ? parseNum(incomeMatch[1]) : null;

      const profField = extractField(
        ec, 'Profession Name',
        ['Business Name', 'Monthly Income', '$']
      );
      const professionName =
        nullIfBlurred(profField);

      const bizField = extractField(
        ec, 'Business Name',
        ['Monthly Income', 'Profession', '$']
      );
      const businessName = nullIfBlurred(bizField);

      // ---- TD[3]: Funding Status ----
      const fc = cells[3] ? txt(cells[3]) : '';

      const loanAmtMatch = fc.match(
        /Loan Amount\s*:?\s*[₹\s]*([\d,]+)/i
      );
      const loanAmount = loanAmtMatch
        ? parseNum(loanAmtMatch[1]) : null;

      const fundedMatch = fc.match(
        /[₹\s]*([\d,]+)\s*Funded/i
      );
      const amountFunded = fundedMatch
        ? parseNum(fundedMatch[1]) : null;

      const leftMatch = fc.match(
        /[₹\s]*([\d,]+)\s*Left/i
      );
      const amountLeft = leftMatch
        ? parseNum(leftMatch[1]) : null;

      // Progress bar percentage
      const bar = cells[3]
        ?.querySelector('.progress-bar');
      const barText = bar ? txt(bar) : '';
      const barStyle = bar
        ? bar.getAttribute('style') || '' : '';
      const widthMatch = barStyle.match(
        /width\s*:\s*([\d.]+)%/
      );
      const pctFromText = barText.match(
        /([\d.]+)/
      );
      const pctFromStyle = widthMatch
        ? parseFloat(widthMatch[1]) : null;
      const pctFromBar = pctFromText
        ? parseFloat(pctFromText[1]) : null;
      // Also try extracting from cell text
      const pctFromCell = fc.match(
        /([\d.]+)\s*%/
      );
      const fundedPercent =
        pctFromBar
        || pctFromStyle
        || (pctFromCell
          ? parseFloat(pctFromCell[1]) : null);

      const fundingRemaining =
        fundedPercent !== null
          ? parseFloat(
              (100 - fundedPercent).toFixed(2)
            )
          : null;
      const isFullyFunded =
        fundedPercent !== null
        && fundedPercent >= 100;

      // ---- TD[5/4]: Details Link ----
      const dc = cells[5] || cells[4];
      const link = dc?.querySelector('a');
      const loanUrl = link
        ? link.href : null;

      return {
        loanId,
        borrowerRef,
        name,
        age,
        location,
        residenceType,
        purpose,
        creditScore,
        creditScoreNumeric,
        riskCategory,
        interestRate,
        tenure,
        product,
        madeLiveOn,
        employmentType,
        monthlyIncome,
        professionName,
        businessName,
        loanAmount,
        amountFunded,
        amountLeft,
        fundedPercent,
        fundingRemaining,
        isFullyFunded,
        loanUrl,
      };
    }).filter(
      (loan) => loan.loanId
        && loan.interestRate !== null
    );
  });

  logger.info(
    `Extracted ${rawLoans.length} raw loan rows`
  );

  // Enrich in Node.js context
  const enriched = rawLoans.map((loan) => ({
    ...loan,
    scrapedAt: new Date().toISOString(),
    yieldScore: calculateYieldScore(loan),
    priority: getPriority(loan.interestRate),
  }));

  return enriched;
}

module.exports = { parseLoans };

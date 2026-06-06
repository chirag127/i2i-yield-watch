// Quick smoke test for the new api-intercept path.
// Opens a browser, intercepts the first listing XHR,
// paginates the rest, and prints the merged shape
// (just loanId, rate, name, and the first few fields
// for each row — so we can verify everything is
// populated correctly).
const {
  fetchAllLoansViaBrowser,
} = require('../src/core/api-intercept');
const {
  transformLoans,
} = require('../src/core/transform');

(async () => {
  console.log('Fetching via browser intercept...');
  const t0 = Date.now();
  const raw = await fetchAllLoansViaBrowser();
  const t1 = Date.now();
  console.log(
    `Got ${raw.length} raw rows in ${t1 - t0}ms`
  );

  console.log('\nFirst 3 raw rows (truncated keys):');
  raw.slice(0, 3).forEach((r, i) => {
    console.log(`\n--- Row ${i + 1} ---`);
    console.log('  pl_bloan_id :', r.pl_bloan_id);
    console.log('  pl_user_id  :', r.pl_user_id);
    console.log('  usr_fname   :', r.usr_fname);
    console.log('  usr_mname   :', r.usr_mname);
    console.log('  usr_lname   :', r.usr_lname);
    console.log('  usr_age     :', r.usr_age);
    console.log('  location    :', r.location);
    console.log('  bloan_i2i_rate  :', r.bloan_i2i_rate);
    console.log('  bloan_tenure    :', r.bloan_tenure,
      'type:', r.tenure_type);
    console.log('  bloan_cibil_score:', r.bloan_cibil_score);
    console.log('  usr_cibil_score  :', r.usr_cibil_score);
    console.log('  bloan_i2i_category:',
      r.bloan_i2i_category);
    console.log('  bloan_desc       :', r.bloan_desc);
    console.log('  nature_of_work   :', r.nature_of_work);
    console.log('  pl_amt           :', r.pl_amt);
    console.log('  pl_amt_left      :', r.pl_amt_left);
    console.log('  pl_applicable_rate:',
      r.pl_applicable_rate);
    console.log('  postedOn         :', r.postedOn);
    console.log('  fin_monthly_income:',
      r.fin_monthly_income);
    console.log('  product_name     :', r.product_name);
  });

  const loans = transformLoans(raw);
  console.log(`\nTransformed ${loans.length} loans`);
  console.log('\nFirst 3 normalized loans:');
  loans.slice(0, 3).forEach((l, i) => {
    console.log(`\n--- Loan ${i + 1} ---`);
    console.log('  loanId      :', l.loanId);
    console.log('  borrowerRef :', l.borrowerRef);
    console.log('  name        :', l.name);
    console.log('  age         :', l.age);
    console.log('  location    :', l.location);
    console.log('  interestRate:', l.interestRate);
    console.log('  tenure      :', l.tenure);
    console.log('  creditScore :', l.creditScore,
      `(${l.creditScoreNumeric})`);
    console.log('  riskCategory:', l.riskCategory);
    console.log('  purpose     :', l.purpose);
    console.log('  product     :', l.product);
    console.log('  monthlyIncome:', l.monthlyIncome);
    console.log('  loanAmount  :', l.loanAmount);
    console.log('  amountLeft  :', l.amountLeft);
    console.log('  fundedPct   :', l.fundedPercent);
    console.log('  yieldScore  :', l.yieldScore);
    console.log('  priority    :', l.priority);
    console.log('  loanUrl     :', l.loanUrl);
  });

  process.exit(0);
})().catch((e) => {
  console.error('FAILED:', e.message);
  console.error(e.stack);
  process.exit(1);
});

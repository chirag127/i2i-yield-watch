// explore-site.js — One-off Playwright exploration of i2iFunding
// Dumps: rendered HTML structure, network API calls, login form details
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const OUT = path.join(__dirname, 'site-exploration');
if (!fs.existsSync(OUT)) fs.mkdirSync(OUT, { recursive: true });

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    viewport: { width: 1366, height: 768 },
  });

  const apiCalls = [];

  // Intercept ALL network requests to find API endpoints
  context.on('request', (req) => {
    const url = req.url();
    if (url.includes('/api/') || url.includes('/borrower') || url.includes('/loan') || url.includes('/auth') || url.includes('/login') || url.includes('/account')) {
      apiCalls.push({
        method: req.method(),
        url: url,
        postData: req.postData() ? req.postData().slice(0, 500) : null,
        resourceType: req.resourceType(),
      });
    }
  });

  const page = await context.newPage();

  // 1. Navigate to borrower listing
  console.log('=== NAVIGATING TO BORROWER LISTING ===');
  await page.goto('https://www.i2ifunding.com/borrower/listing', {
    waitUntil: 'networkidle',
    timeout: 30000,
  });
  await page.waitForTimeout(3000);

  // Dump the full rendered HTML
  const listingHtml = await page.content();
  fs.writeFileSync(path.join(OUT, 'listing-page.html'), listingHtml);
  console.log('Saved listing-page.html');

  // Dump the DOM structure (key elements)
  const domStructure = await page.evaluate(() => {
    const result = {};

    // Page title and meta
    result.title = document.title;
    result.url = window.location.href;

    // All forms on the page
    result.forms = Array.from(document.querySelectorAll('form')).map((f) => ({
      action: f.action,
      method: f.method,
      id: f.id,
      className: f.className,
      inputs: Array.from(f.querySelectorAll('input, select, textarea')).map((i) => ({
        type: i.type,
        name: i.name,
        id: i.id,
        placeholder: i.placeholder,
        required: i.required,
      })),
    }));

    // All buttons
    result.buttons = Array.from(document.querySelectorAll('button')).map((b) => ({
      text: b.innerText.trim(),
      id: b.id,
      className: b.className,
      type: b.type,
    }));

    // All links
    result.links = Array.from(document.querySelectorAll('a')).slice(0, 50).map((a) => ({
      text: a.innerText.trim().slice(0, 80),
      href: a.href,
    }));

    // Table structure
    const table = document.querySelector('.active-borrwer-list') || document.querySelector('table');
    if (table) {
      result.tableExists = true;
      result.tableHTML = table.outerHTML.slice(0, 5000);
      result.tableRows = Array.from(table.querySelectorAll('tbody tr')).length;
      result.tableHeaders = Array.from(table.querySelectorAll('th')).map((th) => th.innerText.trim());
      // First row detail
      const firstRow = table.querySelector('tbody tr');
      if (firstRow) {
        result.firstRowCells = Array.from(firstRow.querySelectorAll('td')).map((td, i) => ({
          index: i,
          text: td.innerText.trim().slice(0, 300),
        }));
      }
    } else {
      result.tableExists = false;
      // Check for any Angular-rendered content
      result.bodyText = document.body.innerText.slice(0, 3000);
      result.mainContent = document.querySelector('main')?.innerText?.slice(0, 2000) || '';
      result.appRoot = document.querySelector('[ng-app]')?.outerHTML?.slice(0, 1000) || '';
      result.routerOutlet = document.querySelector('router-outlet')?.outerHTML?.slice(0, 500) || '';
    }

    // Check for "Show More" button
    const showMoreBtn = document.querySelector('button.btn.btn-warning');
    result.showMoreButton = showMoreBtn ? {
      text: showMoreBtn.innerText,
      visible: showMoreBtn.offsetParent !== null,
    } : null;

    // Check for login link
    const loginLink = Array.from(document.querySelectorAll('a')).find((a) =>
      /login|sign\s*in/i.test(a.innerText) || /login|signin/i.test(a.href)
    );
    result.loginLink = loginLink ? { text: loginLink.innerText, href: loginLink.href } : null;

    // Check for register link
    const regLink = Array.from(document.querySelectorAll('a')).find((a) =>
      /register|sign\s*up/i.test(a.innerText) || /register|signup/i.test(a.href)
    );
    result.registerLink = regLink ? { text: regLink.innerText, href: regLink.href } : null;

    // Angular-specific: check for ng-repeat or *ngFor
    const ngRepeatEls = document.querySelectorAll('[ng-repeat]');
    const ngForEls = document.querySelectorAll('[ngFor]');
    result.angularRepeats = [
      ...Array.from(ngRepeatEls).map((el) => ({
        tag: el.tagName,
        attr: el.getAttribute('ng-repeat') || '',
        text: el.innerText?.slice(0, 100),
      })),
      ...Array.from(ngForEls).map((el) => ({
        tag: el.tagName,
        attr: el.getAttribute('ngFor') || '',
        text: el.innerText?.slice(0, 100),
      })),
    ].slice(0, 5);

    // All script tags (to find API base URLs)
    result.scripts = Array.from(document.querySelectorAll('script')).map((s) => ({
      src: s.src || null,
      inline: s.src ? null : s.textContent.slice(0, 200),
    })).filter((s) => s.src || s.inline);

    return result;
  });

  fs.writeFileSync(path.join(OUT, 'dom-structure.json'), JSON.stringify(domStructure, null, 2));
  console.log('Saved dom-structure.json');
  console.log('Table exists:', domStructure.tableExists);
  console.log('Table rows:', domStructure.tableRows);
  console.log('Show More button:', JSON.stringify(domStructure.showMoreButton));
  console.log('Login link:', JSON.stringify(domStructure.loginLink));

  // 2. Click Show More a few times and observe
  if (domStructure.showMoreButton) {
    console.log('\n=== CLICKING SHOW MORE ===');
    for (let i = 0; i < 3; i++) {
      const before = await page.$$eval('.active-borrwer-list tbody tr', (rows) => rows.length);
      const btn = await page.$('button.btn.btn-warning');
      if (btn && (await btn.isVisible())) {
        await btn.click();
        await page.waitForTimeout(2000);
        const after = await page.$$eval('.active-borrwer-list tbody tr', (rows) => rows.length);
        console.log(`Click ${i + 1}: ${before} → ${after} rows`);
      }
    }
  }

  // 3. Navigate to login page
  console.log('\n=== NAVIGATING TO LOGIN ===');
  if (domStructure.loginLink?.href) {
    await page.goto(domStructure.loginLink.href, { waitUntil: 'networkidle', timeout: 15000 });
  } else {
    await page.goto('https://www.i2ifunding.com/login', { waitUntil: 'networkidle', timeout: 15000 });
  }
  await page.waitForTimeout(2000);

  const loginHtml = await page.content();
  fs.writeFileSync(path.join(OUT, 'login-page.html'), loginHtml);
  console.log('Saved login-page.html');

  const loginStructure = await page.evaluate(() => {
    const result = { url: window.location.href, title: document.title };

    // Login form details
    result.forms = Array.from(document.querySelectorAll('form')).map((f) => ({
      action: f.action,
      method: f.method,
      id: f.id,
      className: f.className,
      innerHTML: f.innerHTML.slice(0, 3000),
      inputs: Array.from(f.querySelectorAll('input, select, textarea')).map((i) => ({
        type: i.type,
        name: i.name,
        id: i.id,
        placeholder: i.placeholder,
        required: i.required,
        className: i.className,
      })),
    }));

    // All input fields on the page
    result.allInputs = Array.from(document.querySelectorAll('input, select, textarea')).map((i) => ({
      type: i.type,
      name: i.name,
      id: i.id,
      placeholder: i.placeholder,
      required: i.required,
    }));

    // Check for Angular form directives
    result.angularForms = Array.from(document.querySelectorAll('[ngForm], [formGroup], [formGroupName], [ngModel]')).map((el) => ({
      tag: el.tagName,
      attrs: Array.from(el.attributes).map((a) => `${a.name}=${a.value}`).join(', '),
      text: el.innerText?.slice(0, 100),
    }));

    // Check for CSRF token
    result.csrfToken = document.querySelector('input[name*="csrf"], input[name*="_token"], input[name*="authenticity"]')?.outerHTML || null;

    // Check for reCAPTCHA
    result.hasRecaptcha = !!document.querySelector('.g-recaptcha, [data-sitekey], iframe[src*="recaptcha"]');

    // Check for OTP / 2FA
    result.hasOtp = !!document.querySelector('input[name*="otp"], input[name*="2fa"], input[name*="verification"]');

    // Page text for context
    result.bodyText = document.body.innerText.slice(0, 2000);

    return result;
  });

  fs.writeFileSync(path.join(OUT, 'login-structure.json'), JSON.stringify(loginStructure, null, 2));
  console.log('Saved login-structure.json');
  console.log('Login forms:', loginStructure.forms.length);
  console.log('Has reCAPTCHA:', loginStructure.hasRecaptcha);
  console.log('Has OTP:', loginStructure.hasOtp);
  console.log('CSRF token:', loginStructure.csrfToken);

  // 4. Navigate to register page
  console.log('\n=== NAVIGATING TO REGISTER ===');
  await page.goto('https://www.i2ifunding.com/register', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(2000);

  const regStructure = await page.evaluate(() => {
    return {
      url: window.location.href,
      title: document.title,
      forms: Array.from(document.querySelectorAll('form')).map((f) => ({
        action: f.action,
        method: f.method,
        inputs: Array.from(f.querySelectorAll('input, select, textarea')).map((i) => ({
          type: i.type,
          name: i.name,
          id: i.id,
          placeholder: i.placeholder,
        })),
      })),
      bodyText: document.body.innerText.slice(0, 2000),
    };
  });

  fs.writeFileSync(path.join(OUT, 'register-structure.json'), JSON.stringify(regStructure, null, 2));
  console.log('Saved register-structure.json');

  // 5. Navigate to "How It Works" / lender page
  console.log('\n=== NAVIGATING TO LENDER PAGE ===');
  await page.goto('https://www.i2ifunding.com/lend', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(2000);

  const lendStructure = await page.evaluate(() => {
    return {
      url: window.location.href,
      title: document.title,
      bodyText: document.body.innerText.slice(0, 3000),
      links: Array.from(document.querySelectorAll('a')).slice(0, 30).map((a) => ({
        text: a.innerText.trim().slice(0, 60),
        href: a.href,
      })),
    };
  });

  fs.writeFileSync(path.join(OUT, 'lend-structure.json'), JSON.stringify(lendStructure, null, 2));
  console.log('Saved lend-structure.json');

  // 6. Dump all captured API calls
  fs.writeFileSync(path.join(OUT, 'api-calls.json'), JSON.stringify(apiCalls, null, 2));
  console.log('\n=== API CALLS CAPTURED ===');
  apiCalls.forEach((c) => console.log(`  ${c.method} ${c.url.slice(0, 120)}`));

  // 7. Try to find the internal API by checking XHR after page load
  console.log('\n=== CHECKING FOR INTERNAL API ===');
  const internalApiCalls = apiCalls.filter((c) =>
    c.resourceType === 'xhr' || c.resourceType === 'fetch'
  );
  console.log('XHR/Fetch calls:', internalApiCalls.length);
  internalApiCalls.forEach((c) => console.log(`  ${c.method} ${c.url}`));

  await browser.close();
  console.log('\nExploration complete. Files saved to:', OUT);
})().catch((e) => { console.error(e); process.exit(1); });

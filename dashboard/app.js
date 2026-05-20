// dashboard/app.js
// i2i Yield Watch — Command Center Dashboard
// Pure vanilla JS. Reads ../data/active_loans.json.

/* ================================================
   State
   ================================================ */
let allLoans = [];
let filteredLoans = [];
let currentPage = 1;
const LOANS_PER_PAGE = 50;
const charts = {};
let refreshInterval = null;
let countdownInterval = null;
let refreshCountdown = 300;

const filters = {
  interestRateMin: 0,
  interestRateMax: 200,
  priorities: ['VERY_HIGH', 'MEDIUM', 'LOW'],
  creditScore: 'all',
  location: '',
  product: 'all',
  fundingRemainingMin: 0,
  searchQuery: '',
};

let currentSort = 'interestRate_desc';

/* ================================================
   Utilities
   ================================================ */

function debounce(fn, delay = 300) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

/**
 * Format number as Indian currency ₹X,XX,XXX.
 */
function formatCurrency(num) {
  if (num == null || isNaN(num)) return '—';
  return '₹' + Number(num).toLocaleString('en-IN');
}

/**
 * Format date string to compact form.
 */
function formatDate(dateStr) {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    return d.toLocaleString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return dateStr;
  }
}

/**
 * Show toast notification.
 */
function showToast(message, type = 'info') {
  const c = document.getElementById(
    'toast-container'
  );
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = message;
  c.appendChild(t);
  setTimeout(() => {
    t.classList.add('fadeout');
    setTimeout(() => t.remove(), 300);
  }, 4000);
}

/* ================================================
   Data Loading
   ================================================ */

async function loadData() {
  const loading = document.getElementById('loading');
  const grid = document.getElementById('loans-grid');

  try {
    loading.style.display = 'flex';
    grid.innerHTML = '';

    const res = await fetch(
      './data/active_loans.json'
    ).catch(() => fetch(
      '../data/active_loans.json'
    ));
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const data = await res.json();
    allLoans = data.loans || [];

    // Update timestamp
    const el = document.getElementById(
      'last-updated'
    );
    el.textContent = data.generatedAt
      ? formatDate(data.generatedAt)
      : 'NO DATA';

    populateProductFilter(allLoans);
    runPipeline();
    loading.style.display = 'none';

  } catch (err) {
    loading.innerHTML = `
      <div class="empty-state">
        <div class="emoji">⊘</div>
        <p>Failed to load loan data</p>
        <p style="font-size:12px;margin-top:6px;
          color:var(--text-muted);
          font-family:var(--font-data)">
          ${err.message}
        </p>
      </div>`;
    showToast('Data load failed: ' + err.message,
      'error');
  }
}

function populateProductFilter(loans) {
  const sel = document.getElementById(
    'filter-product'
  );
  const products = [
    ...new Set(
      loans.map((l) => l.product).filter(Boolean)
    ),
  ].sort();
  sel.innerHTML =
    '<option value="all">All</option>';
  for (const p of products) {
    const o = document.createElement('option');
    o.value = p;
    o.textContent = p;
    sel.appendChild(o);
  }
}

/* ================================================
   Render Pipeline
   ================================================ */

function runPipeline() {
  filteredLoans = applyFilters(allLoans);
  filteredLoans = applySort(
    filteredLoans, currentSort
  );
  currentPage = 1;
  renderStats(filteredLoans);
  renderLoans(filteredLoans, currentPage);
  renderPagination(filteredLoans.length);
  renderCharts(filteredLoans);
  updateLoanCount(filteredLoans.length);
}

/* ================================================
   Stats
   ================================================ */

function renderStats(loans) {
  const total = loans.length;
  const high = loans.filter(
    (l) => l.priority === 'VERY_HIGH'
  ).length;
  const rates = loans.map(
    (l) => l.interestRate || 0
  );
  const scores = loans.map(
    (l) => l.yieldScore || 0
  );
  const avgRate = total
    ? (rates.reduce((a, b) => a + b, 0)
        / total).toFixed(1)
    : '0';
  const avgScore = total
    ? (scores.reduce((a, b) => a + b, 0)
        / total).toFixed(1)
    : '0';
  const highest = total
    ? Math.max(...rates).toFixed(1) : '0';

  animateValue('stat-total-value', total);
  animateValue('stat-high-value', high);
  document.getElementById(
    'stat-avg-rate-value'
  ).textContent = avgRate + '%';
  document.getElementById(
    'stat-avg-score-value'
  ).textContent = avgScore;
  document.getElementById(
    'stat-highest-value'
  ).textContent = highest + '%';
}

function animateValue(id, target) {
  const el = document.getElementById(id);
  const start = parseInt(el.textContent) || 0;
  const diff = target - start;
  if (diff === 0) { el.textContent = target; return; }
  const duration = 500;
  const t0 = performance.now();
  function step(now) {
    const p = Math.min((now - t0) / duration, 1);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(
      start + diff * eased
    );
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function updateLoanCount(count) {
  document.getElementById('loan-count').innerHTML =
    `<strong>${count}</strong> LOAN`
    + (count !== 1 ? 'S' : '');
}

/* ================================================
   Filters
   ================================================ */

function applyFilters(loans) {
  return loans.filter((loan) => {
    const rate = loan.interestRate || 0;
    if (rate < filters.interestRateMin) return false;
    if (rate > filters.interestRateMax) return false;

    if (!filters.priorities.includes(
      loan.priority || 'LOW'
    )) return false;

    if (filters.creditScore !== 'all') {
      const cs = loan.creditScoreNumeric;
      const csStr = loan.creditScore || '';
      switch (filters.creditScore) {
        case 'no_history':
          if (!/no.?history/i.test(csStr)
            && cs !== null) return false;
          break;
        case 'below_550':
          if (cs === null || cs >= 550) return false;
          break;
        case '550_699':
          if (cs === null || cs < 550 || cs > 699)
            return false;
          break;
        case '700_749':
          if (cs === null || cs < 700 || cs > 749)
            return false;
          break;
        case '750_plus':
          if (cs === null || cs < 750) return false;
          break;
      }
    }

    if (filters.location) {
      const loc = (loan.location || '').toLowerCase();
      if (!loc.includes(
        filters.location.toLowerCase()
      )) return false;
    }

    if (filters.product !== 'all') {
      if (loan.product !== filters.product)
        return false;
    }

    if (filters.fundingRemainingMin > 0) {
      const rem = loan.fundingRemaining || 0;
      if (rem < filters.fundingRemainingMin)
        return false;
    }

    if (filters.searchQuery) {
      const q = filters.searchQuery.toLowerCase();
      const hay = [
        loan.location, loan.purpose, loan.product,
        loan.creditScore, loan.riskCategory,
        loan.loanId, loan.employmentType,
      ].filter(Boolean).join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }

    return true;
  });
}

function applySort(loans, sortKey) {
  const sorted = [...loans];
  const [field, dir] = sortKey.split('_');
  const mult = dir === 'desc' ? -1 : 1;

  sorted.sort((a, b) => {
    let va = a[field];
    let vb = b[field];
    if (field === 'madeLiveOn') {
      va = va ? new Date(va).getTime() : 0;
      vb = vb ? new Date(vb).getTime() : 0;
    }
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === 'string')
      return mult * va.localeCompare(vb);
    return mult * (va - vb);
  });

  return sorted;
}

/* ================================================
   Render Loans
   ================================================ */

function renderLoans(loans, page) {
  const grid = document.getElementById('loans-grid');
  const start = (page - 1) * LOANS_PER_PAGE;
  const end = start + LOANS_PER_PAGE;
  const pageLoans = loans.slice(start, end);

  if (pageLoans.length === 0) {
    grid.innerHTML = `
      <div class="empty-state"
        style="grid-column:1/-1">
        <div class="emoji">⊘</div>
        <p>No loans match current filters</p>
      </div>`;
    return;
  }

  grid.innerHTML = pageLoans.map(
    (loan, i) => renderLoanCard(loan, i)
  ).join('');
}

function renderLoanCard(loan, index) {
  const rate = loan.interestRate || 0;
  const priority = loan.priority || 'LOW';
  const funded = loan.fundedPercent || 0;

  // Progress bar class
  let barClass = 'low';
  if (funded >= 100) barClass = 'full';
  else if (funded >= 76) barClass = 'high';
  else if (funded >= 51) barClass = 'mid';

  // Animation delay
  const delay = (index % LOANS_PER_PAGE) * 25;

  const url = loan.loanUrl
    || `https://www.i2ifunding.com/invest/\
loan-detail/${loan.loanId}`;

  // Credit display
  const creditDisplay =
    loan.creditScore === 'No History'
      ? 'NEW'
      : (loan.creditScore || '—');

  return `
    <article class="loan-card priority-${priority}"
      style="animation-delay:${delay}ms"
      id="loan-${loan.loanId}">

      <div class="card-eyebrow">
        <span class="priority-indicator ${priority}">
          ${priority === 'VERY_HIGH'
            ? '<span class="pulse-dot"></span>' : ''}
          ${priority === 'VERY_HIGH' ? 'VERY HIGH'
            : priority}
        </span>
        <span class="yield-badge">
          SCORE ${loan.yieldScore || 0}
        </span>
      </div>

      <div class="card-rate">
        <span class="rate-value">${rate}</span>
        <span class="rate-unit">% p.a.</span>
      </div>

      <div class="card-divider"></div>

      <div class="card-meta">
        <div class="meta-row">
          <span class="meta-label">Amount</span>
          <span class="meta-value">
            ${formatCurrency(loan.loanAmount)}
          </span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Credit</span>
          <span class="meta-value">
            ${creditDisplay}
          </span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Location</span>
          <span class="meta-value">
            ${loan.location || '—'}
          </span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Tenure</span>
          <span class="meta-value">
            ${loan.tenure || '—'}
          </span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Risk</span>
          <span class="meta-value">
            ${loan.riskCategory || '—'}
          </span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Income</span>
          <span class="meta-value">
            ${formatCurrency(loan.monthlyIncome)}
          </span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Employment</span>
          <span class="meta-value">
            ${loan.employmentType || '—'}
          </span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Purpose</span>
          <span class="meta-value">
            ${loan.purpose || '—'}
          </span>
        </div>
      </div>

      <div class="funding-bar">
        <div class="bar-header">
          <span>FUNDED</span>
          <span>${funded.toFixed(1)}%</span>
        </div>
        <div class="bar-track">
          <div class="bar-fill ${barClass}"
            style="width:${Math.min(funded, 100)}%">
          </div>
        </div>
        <div class="bar-amounts">
          <span>
            ${formatCurrency(loan.amountFunded)}
          </span>
          <span>
            ${formatCurrency(loan.amountLeft)} left
          </span>
        </div>
      </div>

      <a class="card-action"
        href="${url}"
        target="_blank" rel="noopener">
        VIEW DETAILS →
      </a>
    </article>`;
}

/* ================================================
   Pagination
   ================================================ */

function renderPagination(totalLoans) {
  const c = document.getElementById('pagination');
  const totalPages = Math.ceil(
    totalLoans / LOANS_PER_PAGE
  );

  if (totalPages <= 1) { c.innerHTML = ''; return; }

  let html = '';

  html += `<button class="page-btn"
    id="page-prev"
    ${currentPage <= 1 ? 'disabled' : ''}
    onclick="goToPage(${currentPage - 1})">
    ←
  </button>`;

  const maxVisible = 7;
  let startP = Math.max(
    1, currentPage - Math.floor(maxVisible / 2)
  );
  let endP = Math.min(
    totalPages, startP + maxVisible - 1
  );
  if (endP - startP < maxVisible - 1) {
    startP = Math.max(1, endP - maxVisible + 1);
  }

  if (startP > 1) {
    html += `<button class="page-btn"
      onclick="goToPage(1)">1</button>`;
    if (startP > 2) {
      html += `<span class="page-btn"
        style="border:none;cursor:default">
        ⋯</span>`;
    }
  }

  for (let i = startP; i <= endP; i++) {
    html += `<button class="page-btn
      ${i === currentPage ? 'active' : ''}"
      onclick="goToPage(${i})">${i}</button>`;
  }

  if (endP < totalPages) {
    if (endP < totalPages - 1) {
      html += `<span class="page-btn"
        style="border:none;cursor:default">
        ⋯</span>`;
    }
    html += `<button class="page-btn"
      onclick="goToPage(${totalPages})">
      ${totalPages}</button>`;
  }

  html += `<button class="page-btn"
    id="page-next"
    ${currentPage >= totalPages ? 'disabled' : ''}
    onclick="goToPage(${currentPage + 1})">
    →
  </button>`;

  c.innerHTML = html;
}

window.goToPage = function (page) {
  const tp = Math.ceil(
    filteredLoans.length / LOANS_PER_PAGE
  );
  if (page < 1 || page > tp) return;
  currentPage = page;
  renderLoans(filteredLoans, currentPage);
  renderPagination(filteredLoans.length);
  window.scrollTo({ top: 0, behavior: 'smooth' });
};

/* ================================================
   Charts
   ================================================ */

function renderCharts(loans) {
  renderRateChart(loans);
  renderCreditChart(loans);
  renderFundingChart(loans);
  renderLocationChart(loans);
}

function chartTextColor() {
  const t = document.documentElement
    .getAttribute('data-theme');
  return t === 'light' ? '#5a5a60' : '#8a8b8e';
}

function chartGridColor() {
  const t = document.documentElement
    .getAttribute('data-theme');
  return t === 'light'
    ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.04)';
}

function chartOpts() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        labels: {
          color: chartTextColor(),
          font: { family: "'DM Sans'" },
        },
      },
    },
    scales: {
      x: {
        ticks: {
          color: chartTextColor(),
          font: {
            family: "'JetBrains Mono'",
            size: 10,
          },
        },
        grid: { color: chartGridColor() },
      },
      y: {
        ticks: {
          color: chartTextColor(),
          font: {
            family: "'JetBrains Mono'",
            size: 10,
          },
        },
        grid: { color: chartGridColor() },
      },
    },
  };
}

// Gold-toned chart palette
const CHART_COLORS = [
  '#4a4b50', '#5f9fd4', '#c9a962',
  '#ffa502', '#ff6348', '#ff4757',
];

function renderRateChart(loans) {
  const ctx = document.getElementById(
    'chart-rate'
  ).getContext('2d');

  const buckets = {
    '<20%': 0, '20–39%': 0, '40–49%': 0,
    '50–69%': 0, '70–99%': 0, '100%+': 0,
  };
  for (const l of loans) {
    const r = l.interestRate || 0;
    if (r < 20) buckets['<20%']++;
    else if (r < 40) buckets['20–39%']++;
    else if (r < 50) buckets['40–49%']++;
    else if (r < 70) buckets['50–69%']++;
    else if (r < 100) buckets['70–99%']++;
    else buckets['100%+']++;
  }

  if (charts.rate) charts.rate.destroy();
  charts.rate = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: Object.keys(buckets),
      datasets: [{
        label: 'Loans',
        data: Object.values(buckets),
        backgroundColor: CHART_COLORS,
        borderRadius: 4,
        borderSkipped: false,
      }],
    },
    options: {
      ...chartOpts(),
      plugins: { legend: { display: false } },
    },
  });
}

function renderCreditChart(loans) {
  const ctx = document.getElementById(
    'chart-credit'
  ).getContext('2d');

  const buckets = {
    'No History': 0, '300–499': 0,
    '500–599': 0, '600–699': 0,
    '700–799': 0, '800+': 0,
  };
  for (const l of loans) {
    const cs = l.creditScoreNumeric;
    const csStr = l.creditScore || '';
    if (cs === null || /no.?history/i.test(csStr)) {
      buckets['No History']++;
    } else if (cs < 500) buckets['300–499']++;
    else if (cs < 600) buckets['500–599']++;
    else if (cs < 700) buckets['600–699']++;
    else if (cs < 800) buckets['700–799']++;
    else buckets['800+']++;
  }

  if (charts.credit) charts.credit.destroy();
  charts.credit = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: Object.keys(buckets),
      datasets: [{
        label: 'Loans',
        data: Object.values(buckets),
        backgroundColor: [
          '#8a8b8e', '#ff4757', '#ff6348',
          '#ffa502', '#2ed573', '#5f9fd4',
        ],
        borderRadius: 4,
        borderSkipped: false,
      }],
    },
    options: {
      ...chartOpts(),
      plugins: { legend: { display: false } },
    },
  });
}

function renderFundingChart(loans) {
  const ctx = document.getElementById(
    'chart-funding'
  ).getContext('2d');

  const buckets = {
    '0–25%': 0, '26–50%': 0,
    '51–75%': 0, '76–99%': 0,
  };
  for (const l of loans) {
    const f = l.fundedPercent || 0;
    if (f <= 25) buckets['0–25%']++;
    else if (f <= 50) buckets['26–50%']++;
    else if (f <= 75) buckets['51–75%']++;
    else buckets['76–99%']++;
  }

  if (charts.funding) charts.funding.destroy();
  charts.funding = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: Object.keys(buckets),
      datasets: [{
        data: Object.values(buckets),
        backgroundColor: [
          '#5f9fd4', '#c9a962',
          '#ffa502', '#ff4757',
        ],
        borderWidth: 0,
        hoverOffset: 6,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '65%',
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            color: chartTextColor(),
            padding: 14,
            font: {
              family: "'JetBrains Mono'",
              size: 10,
            },
          },
        },
      },
    },
  });
}

function renderLocationChart(loans) {
  const ctx = document.getElementById(
    'chart-location'
  ).getContext('2d');

  const counts = {};
  for (const l of loans) {
    const loc = l.location || 'Unknown';
    counts[loc] = (counts[loc] || 0) + 1;
  }
  const sorted = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10);

  if (charts.location) charts.location.destroy();
  charts.location = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: sorted.map((s) => s[0]),
      datasets: [{
        label: 'Loans',
        data: sorted.map((s) => s[1]),
        backgroundColor: '#c9a962',
        borderRadius: 4,
        borderSkipped: false,
      }],
    },
    options: {
      ...chartOpts(),
      indexAxis: 'y',
      plugins: { legend: { display: false } },
    },
  });
}

/* ================================================
   Filter Initialization
   ================================================ */

function initFilters() {
  // Toggle
  const toggleBtn = document.getElementById(
    'filter-toggle'
  );
  const panel = document.getElementById(
    'filter-panel'
  );
  toggleBtn.addEventListener('click', () => {
    panel.classList.toggle('open');
    toggleBtn.textContent = panel.classList
      .contains('open')
      ? '△ FILTERS' : '▽ FILTERS';
  });

  // Rate range
  const rateMin = document.getElementById(
    'filter-rate-min'
  );
  const rateMax = document.getElementById(
    'filter-rate-max'
  );
  rateMin.addEventListener('input', () => {
    filters.interestRateMin =
      parseFloat(rateMin.value) || 0;
    runPipeline();
  });
  rateMax.addEventListener('input', () => {
    filters.interestRateMax =
      parseFloat(rateMax.value) || 200;
    runPipeline();
  });

  // Presets
  document.querySelectorAll('.btn-preset')
    .forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.btn-preset')
          .forEach((b) =>
            b.classList.remove('active'));
        btn.classList.add('active');
        const min = parseFloat(
          btn.dataset.min
        ) || 0;
        const max = parseFloat(
          btn.dataset.max
        ) || 200;
        filters.interestRateMin = min;
        filters.interestRateMax = max;
        rateMin.value = min;
        rateMax.value = max;
        runPipeline();
      });
    });

  // Priority checkboxes
  const pIds = [
    'filter-priority-very-high',
    'filter-priority-medium',
    'filter-priority-low',
  ];
  for (const id of pIds) {
    document.getElementById(id)
      .addEventListener('change', () => {
        filters.priorities = pIds
          .map((cid) =>
            document.getElementById(cid)
          )
          .filter((cb) => cb.checked)
          .map((cb) => cb.value);
        runPipeline();
      });
  }

  // Credit
  document.getElementById('filter-credit')
    .addEventListener('change', (e) => {
      filters.creditScore = e.target.value;
      runPipeline();
    });

  // Location
  const locIn = document.getElementById(
    'filter-location'
  );
  locIn.addEventListener(
    'input',
    debounce(() => {
      filters.location = locIn.value;
      runPipeline();
    }, 250)
  );

  // Product
  document.getElementById('filter-product')
    .addEventListener('change', (e) => {
      filters.product = e.target.value;
      runPipeline();
    });

  // Funding slider
  const slider = document.getElementById(
    'filter-funding'
  );
  const sliderLabel = document.getElementById(
    'funding-remaining-label'
  );
  slider.addEventListener('input', () => {
    const v = parseInt(slider.value);
    filters.fundingRemainingMin = v;
    sliderLabel.textContent = v + '%';
    runPipeline();
  });

  // Reset
  document.getElementById('filter-reset')
    .addEventListener('click', resetFilters);

  // Sort
  document.getElementById('sort-select')
    .addEventListener('change', (e) => {
      currentSort = e.target.value;
      runPipeline();
    });

  // Search
  const searchIn = document.getElementById(
    'search-input'
  );
  searchIn.addEventListener(
    'input',
    debounce(() => {
      filters.searchQuery = searchIn.value;
      runPipeline();
    }, 250)
  );
}

function resetFilters() {
  filters.interestRateMin = 0;
  filters.interestRateMax = 200;
  filters.priorities = [
    'VERY_HIGH', 'MEDIUM', 'LOW',
  ];
  filters.creditScore = 'all';
  filters.location = '';
  filters.product = 'all';
  filters.fundingRemainingMin = 0;
  filters.searchQuery = '';

  document.getElementById(
    'filter-rate-min'
  ).value = 0;
  document.getElementById(
    'filter-rate-max'
  ).value = 200;
  document.getElementById(
    'filter-priority-very-high'
  ).checked = true;
  document.getElementById(
    'filter-priority-medium'
  ).checked = true;
  document.getElementById(
    'filter-priority-low'
  ).checked = true;
  document.getElementById(
    'filter-credit'
  ).value = 'all';
  document.getElementById(
    'filter-location'
  ).value = '';
  document.getElementById(
    'filter-product'
  ).value = 'all';
  document.getElementById(
    'filter-funding'
  ).value = 0;
  document.getElementById(
    'funding-remaining-label'
  ).textContent = '0%';
  document.getElementById(
    'search-input'
  ).value = '';

  document.querySelectorAll('.btn-preset')
    .forEach((b) => b.classList.remove('active'));
  document.getElementById('preset-all')
    .classList.add('active');

  runPipeline();
}

/* ================================================
   Theme Toggle
   ================================================ */

function initTheme() {
  const saved = localStorage.getItem('theme');
  if (saved) {
    document.documentElement
      .setAttribute('data-theme', saved);
  }
  updateThemeBtn();
  document.getElementById('theme-toggle')
    .addEventListener('click', toggleTheme);
}

function toggleTheme() {
  const html = document.documentElement;
  const cur = html.getAttribute('data-theme');
  const next = cur === 'light' ? 'dark' : 'light';
  html.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
  updateThemeBtn();
  renderCharts(filteredLoans);
}

function updateThemeBtn() {
  const btn = document.getElementById(
    'theme-toggle'
  );
  const theme = document.documentElement
    .getAttribute('data-theme');
  btn.textContent = theme === 'light' ? '☀' : '☾';
}

/* ================================================
   Auto-Refresh
   ================================================ */

function initAutoRefresh() {
  refreshCountdown = 300;
  const el = document.getElementById(
    'refresh-countdown'
  );

  if (countdownInterval)
    clearInterval(countdownInterval);
  if (refreshInterval)
    clearInterval(refreshInterval);

  countdownInterval = setInterval(() => {
    refreshCountdown--;
    if (refreshCountdown <= 0)
      refreshCountdown = 300;
    const m = Math.floor(refreshCountdown / 60);
    const s = refreshCountdown % 60;
    el.textContent =
      `↻ ${m}:${String(s).padStart(2, '0')}`;
  }, 1000);

  refreshInterval = setInterval(() => {
    loadData();
    refreshCountdown = 300;
  }, 300000);
}

/* ================================================
   Init
   ================================================ */

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  initFilters();
  loadData();
  initAutoRefresh();
});

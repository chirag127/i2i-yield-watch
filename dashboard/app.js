// dashboard/app.js
// Complete dashboard logic for i2i Yield Watch.
// Pure vanilla JS — no frameworks, no build tools.
// Reads ../data/active_loans.json via fetch.

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
  interestRateMax: 100,
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

/**
 * Debounce a function call.
 */
function debounce(fn, delay = 300) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

/**
 * Format number as Indian currency (₹X,XX,XXX).
 */
function formatCurrency(num) {
  if (num == null || isNaN(num)) return '—';
  return '₹' + Number(num).toLocaleString('en-IN');
}

/**
 * Format a date string to human-readable form.
 */
function formatDate(dateStr) {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    return d.toLocaleString('en-IN', {
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  } catch {
    return dateStr;
  }
}

/**
 * Show a toast notification.
 */
function showToast(message, type = 'info') {
  const container = document.getElementById(
    'toast-container'
  );
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('fadeout');
    setTimeout(() => toast.remove(), 300);
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
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    const data = await res.json();
    allLoans = data.loans || [];

    // Update last-updated timestamp
    const lastUpdatedEl = document.getElementById(
      'last-updated'
    );
    if (data.generatedAt) {
      lastUpdatedEl.textContent =
        'Updated: ' + formatDate(data.generatedAt);
    } else {
      lastUpdatedEl.textContent =
        'No data yet';
    }

    // Populate product filter dropdown
    populateProductFilter(allLoans);

    // Run the render pipeline
    runPipeline();

    loading.style.display = 'none';

  } catch (err) {
    loading.innerHTML = `
      <div class="empty-state">
        <div class="emoji">📭</div>
        <p>Could not load loan data.</p>
        <p style="font-size:13px;margin-top:8px;
          color:var(--text-muted)">
          ${err.message}
        </p>
      </div>`;
    showToast(
      'Failed to load data: ' + err.message,
      'error'
    );
  }
}

/**
 * Populate the product dropdown with unique values
 * from the loaded loans.
 */
function populateProductFilter(loans) {
  const select = document.getElementById(
    'filter-product'
  );
  const products = [
    ...new Set(
      loans
        .map((l) => l.product)
        .filter(Boolean)
    ),
  ].sort();

  // Keep the "All" option, remove old dynamics
  select.innerHTML =
    '<option value="all">All Products</option>';
  for (const p of products) {
    const opt = document.createElement('option');
    opt.value = p;
    opt.textContent = p;
    select.appendChild(opt);
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
  const avgRate = total > 0
    ? (loans.reduce(
        (s, l) => s + (l.interestRate || 0), 0
      ) / total).toFixed(1)
    : '0';
  const avgScore = total > 0
    ? (loans.reduce(
        (s, l) => s + (l.yieldScore || 0), 0
      ) / total).toFixed(1)
    : '0';
  const highest = total > 0
    ? Math.max(
        ...loans.map((l) => l.interestRate || 0)
      ).toFixed(1)
    : '0';

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
  if (diff === 0) {
    el.textContent = target;
    return;
  }
  const duration = 400;
  const startTime = performance.now();

  function step(now) {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    el.textContent = Math.round(
      start + diff * eased
    );
    if (progress < 1) {
      requestAnimationFrame(step);
    }
  }
  requestAnimationFrame(step);
}

function updateLoanCount(count) {
  document.getElementById('loan-count').innerHTML =
    `Showing <strong>${count}</strong> loan`
    + (count !== 1 ? 's' : '');
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

    // Credit score filter
    if (filters.creditScore !== 'all') {
      const cs = loan.creditScoreNumeric;
      const csStr = loan.creditScore || '';
      switch (filters.creditScore) {
        case 'no_history':
          if (!/no history/i.test(csStr)
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

    // Location filter
    if (filters.location) {
      const loc = (loan.location || '').toLowerCase();
      if (!loc.includes(
        filters.location.toLowerCase()
      )) return false;
    }

    // Product filter
    if (filters.product !== 'all') {
      if (loan.product !== filters.product)
        return false;
    }

    // Funding remaining filter
    if (filters.fundingRemainingMin > 0) {
      const remaining = loan.fundingRemaining || 0;
      if (remaining < filters.fundingRemainingMin)
        return false;
    }

    // Global search
    if (filters.searchQuery) {
      const q = filters.searchQuery.toLowerCase();
      const haystack = [
        loan.location,
        loan.purpose,
        loan.product,
        loan.creditScore,
        loan.riskCategory,
        loan.loanId,
        loan.employmentType,
      ].filter(Boolean).join(' ').toLowerCase();
      if (!haystack.includes(q)) return false;
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

    // Handle madeLiveOn as date
    if (field === 'madeLiveOn') {
      va = va ? new Date(va).getTime() : 0;
      vb = vb ? new Date(vb).getTime() : 0;
    }

    // Nulls go last
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;

    if (typeof va === 'string') {
      return mult * va.localeCompare(vb);
    }
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
        style="grid-column: 1 / -1;">
        <div class="emoji">🔍</div>
        <p>No loans match your filters.</p>
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
  const priorityEmoji =
    priority === 'VERY_HIGH' ? '🔥' :
    priority === 'MEDIUM' ? '🟡' : '⚪';
  const priorityText =
    priority === 'VERY_HIGH' ? 'Very High' :
    priority === 'MEDIUM' ? 'Medium' : 'Low';

  // Progress bar class
  const funded = loan.fundedPercent || 0;
  let progressClass = 'low';
  if (funded >= 100) progressClass = 'full';
  else if (funded >= 76) progressClass = 'high';
  else if (funded >= 51) progressClass = 'mid';

  // Rate color
  let rateColor = 'var(--priority-low)';
  if (rate >= 70) rateColor = 'var(--accent-red)';
  else if (rate >= 50) {
    rateColor = 'var(--accent-amber)';
  }

  // Animation delay for staggered entrance
  const delay = (index % LOANS_PER_PAGE) * 30;

  const loanUrl = loan.loanUrl
    || `https://www.i2ifunding.com/invest/loan-detail/${loan.loanId}`;

  return `
    <article class="loan-card priority-${priority}"
      style="animation-delay: ${delay}ms"
      id="loan-${loan.loanId}">
      <div class="card-header">
        <span class="priority-badge ${priority}">
          ${priorityEmoji} ${priorityText}
        </span>
        <span class="yield-score">
          Score: ${loan.yieldScore || 0}/100
        </span>
      </div>
      <div class="interest-rate"
        style="color: ${rateColor}">
        ${rate}%<span class="unit">p.a.</span>
      </div>
      <div class="card-divider"></div>
      <div class="card-details">
        <div class="detail-item">
          <span class="emoji">📍</span>
          <span class="value">
            ${loan.location || '—'}
          </span>
        </div>
        <div class="detail-item">
          <span class="emoji">🎂</span>
          <span class="value">
            Age: ${loan.age || '—'}
          </span>
        </div>
        <div class="detail-item">
          <span class="emoji">💰</span>
          <span class="value">
            ${formatCurrency(loan.loanAmount)}
          </span>
        </div>
        <div class="detail-item">
          <span class="emoji">📅</span>
          <span class="value">
            ${loan.tenure || '—'}
          </span>
        </div>
        <div class="detail-item">
          <span class="emoji">📊</span>
          <span class="value">
            Credit: ${loan.creditScore || '—'}
          </span>
        </div>
        <div class="detail-item">
          <span class="emoji">🏢</span>
          <span class="value">
            ${loan.product || '—'}
          </span>
        </div>
        <div class="detail-item">
          <span class="emoji">⚠️</span>
          <span class="value">
            Risk: ${loan.riskCategory || '—'}
          </span>
        </div>
        <div class="detail-item">
          <span class="emoji">💼</span>
          <span class="value">
            ${loan.employmentType || '—'}
          </span>
        </div>
      </div>
      <div class="card-divider"></div>
      <div class="funding-section">
        <div class="funding-header">
          <span>Funding Progress</span>
          <span>${funded.toFixed(1)}%</span>
        </div>
        <div class="progress-track">
          <div class="progress-fill ${progressClass}"
            style="width: ${Math.min(funded, 100)}%">
          </div>
        </div>
        <div class="funding-amounts">
          <span>
            ${formatCurrency(loan.amountFunded)}
            funded
          </span>
          <span>
            ${formatCurrency(loan.amountLeft)} left
          </span>
        </div>
      </div>
      <a class="card-link"
        href="${loanUrl}"
        target="_blank" rel="noopener">
        View on i2iFunding →
      </a>
    </article>`;
}

/* ================================================
   Pagination
   ================================================ */

function renderPagination(totalLoans) {
  const container = document.getElementById(
    'pagination'
  );
  const totalPages = Math.ceil(
    totalLoans / LOANS_PER_PAGE
  );

  if (totalPages <= 1) {
    container.innerHTML = '';
    return;
  }

  let html = '';

  // Previous button
  html += `<button class="page-btn"
    id="page-prev"
    ${currentPage <= 1 ? 'disabled' : ''}
    onclick="goToPage(${currentPage - 1})">
    ← Prev
  </button>`;

  // Page numbers with ellipsis
  const maxVisible = 7;
  let startPage = Math.max(
    1, currentPage - Math.floor(maxVisible / 2)
  );
  let endPage = Math.min(
    totalPages, startPage + maxVisible - 1
  );
  if (endPage - startPage < maxVisible - 1) {
    startPage = Math.max(
      1, endPage - maxVisible + 1
    );
  }

  if (startPage > 1) {
    html += `<button class="page-btn"
      onclick="goToPage(1)">1</button>`;
    if (startPage > 2) {
      html += `<span class="page-btn"
        style="border:none;cursor:default">
        …
      </span>`;
    }
  }

  for (let i = startPage; i <= endPage; i++) {
    html += `<button class="page-btn
      ${i === currentPage ? 'active' : ''}"
      onclick="goToPage(${i})">${i}</button>`;
  }

  if (endPage < totalPages) {
    if (endPage < totalPages - 1) {
      html += `<span class="page-btn"
        style="border:none;cursor:default">
        …
      </span>`;
    }
    html += `<button class="page-btn"
      onclick="goToPage(${totalPages})">
      ${totalPages}
    </button>`;
  }

  // Next button
  html += `<button class="page-btn"
    id="page-next"
    ${currentPage >= totalPages ? 'disabled' : ''}
    onclick="goToPage(${currentPage + 1})">
    Next →
  </button>`;

  container.innerHTML = html;
}

// Global function for onclick handlers
window.goToPage = function (page) {
  const totalPages = Math.ceil(
    filteredLoans.length / LOANS_PER_PAGE
  );
  if (page < 1 || page > totalPages) return;
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

function getChartTextColor() {
  const theme = document.documentElement
    .getAttribute('data-theme');
  return theme === 'light'
    ? '#4a4a5e' : '#a0a0b0';
}

function getChartGridColor() {
  const theme = document.documentElement
    .getAttribute('data-theme');
  return theme === 'light'
    ? '#e0e0e8' : '#2a2a3e';
}

function chartDefaults() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        labels: { color: getChartTextColor() },
      },
    },
    scales: {
      x: {
        ticks: { color: getChartTextColor() },
        grid: { color: getChartGridColor() },
      },
      y: {
        ticks: { color: getChartTextColor() },
        grid: { color: getChartGridColor() },
      },
    },
  };
}

function renderRateChart(loans) {
  const ctx = document.getElementById(
    'chart-rate'
  ).getContext('2d');

  const buckets = {
    '<20%': 0, '20-39%': 0, '40-49%': 0,
    '50-69%': 0, '70-84%': 0, '85%+': 0,
  };
  for (const l of loans) {
    const r = l.interestRate || 0;
    if (r < 20) buckets['<20%']++;
    else if (r < 40) buckets['20-39%']++;
    else if (r < 50) buckets['40-49%']++;
    else if (r < 70) buckets['50-69%']++;
    else if (r < 85) buckets['70-84%']++;
    else buckets['85%+']++;
  }

  if (charts.rate) charts.rate.destroy();
  charts.rate = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: Object.keys(buckets),
      datasets: [{
        label: 'Loans',
        data: Object.values(buckets),
        backgroundColor: [
          '#6b7280', '#3b82f6', '#8b5cf6',
          '#f59e0b', '#ff6b35', '#ff4444',
        ],
        borderRadius: 6,
        borderSkipped: false,
      }],
    },
    options: {
      ...chartDefaults(),
      plugins: {
        legend: { display: false },
      },
    },
  });
}

function renderCreditChart(loans) {
  const ctx = document.getElementById(
    'chart-credit'
  ).getContext('2d');

  const buckets = {
    'No History': 0, '300-499': 0,
    '500-599': 0, '600-699': 0,
    '700-799': 0, '800+': 0,
  };
  for (const l of loans) {
    const cs = l.creditScoreNumeric;
    const csStr = l.creditScore || '';
    if (cs === null || /no history/i.test(csStr)) {
      buckets['No History']++;
    } else if (cs < 500) buckets['300-499']++;
    else if (cs < 600) buckets['500-599']++;
    else if (cs < 700) buckets['600-699']++;
    else if (cs < 800) buckets['700-799']++;
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
          '#6b7280', '#ef4444', '#f97316',
          '#f59e0b', '#10b981', '#3b82f6',
        ],
        borderRadius: 6,
        borderSkipped: false,
      }],
    },
    options: {
      ...chartDefaults(),
      plugins: {
        legend: { display: false },
      },
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
          '#3b82f6', '#10b981',
          '#f59e0b', '#ff4444',
        ],
        borderWidth: 0,
        hoverOffset: 8,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '60%',
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            color: getChartTextColor(),
            padding: 16,
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

  // Count by location, take top 10
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
        backgroundColor: '#ff6b35',
        borderRadius: 6,
        borderSkipped: false,
      }],
    },
    options: {
      ...chartDefaults(),
      indexAxis: 'y',
      plugins: {
        legend: { display: false },
      },
    },
  });
}

/* ================================================
   Filter Initialization
   ================================================ */

function initFilters() {
  // Toggle filter panel
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
      ? '🔼 Filters' : '🔽 Filters';
  });

  // Interest rate range
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
      parseFloat(rateMax.value) || 100;
    runPipeline();
  });

  // Rate presets
  document.querySelectorAll('.btn-preset')
    .forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.btn-preset')
          .forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        const min = parseFloat(
          btn.dataset.min
        ) || 0;
        const max = parseFloat(
          btn.dataset.max
        ) || 100;
        filters.interestRateMin = min;
        filters.interestRateMax = max;
        rateMin.value = min;
        rateMax.value = max;
        runPipeline();
      });
    });

  // Priority checkboxes
  const priorityCheckboxes = [
    'filter-priority-very-high',
    'filter-priority-medium',
    'filter-priority-low',
  ];
  for (const id of priorityCheckboxes) {
    document.getElementById(id)
      .addEventListener('change', () => {
        filters.priorities = priorityCheckboxes
          .map((cid) =>
            document.getElementById(cid)
          )
          .filter((cb) => cb.checked)
          .map((cb) => cb.value);
        runPipeline();
      });
  }

  // Credit score dropdown
  document.getElementById('filter-credit')
    .addEventListener('change', (e) => {
      filters.creditScore = e.target.value;
      runPipeline();
    });

  // Location search
  const locInput = document.getElementById(
    'filter-location'
  );
  locInput.addEventListener(
    'input',
    debounce(() => {
      filters.location = locInput.value;
      runPipeline();
    }, 250)
  );

  // Product dropdown
  document.getElementById('filter-product')
    .addEventListener('change', (e) => {
      filters.product = e.target.value;
      runPipeline();
    });

  // Funding remaining slider
  const fundingSlider = document.getElementById(
    'filter-funding'
  );
  const fundingLabel = document.getElementById(
    'funding-remaining-label'
  );
  fundingSlider.addEventListener('input', () => {
    const val = parseInt(fundingSlider.value);
    filters.fundingRemainingMin = val;
    fundingLabel.textContent = val + '%';
    runPipeline();
  });

  // Reset button
  document.getElementById('filter-reset')
    .addEventListener('click', resetFilters);

  // Sort dropdown
  document.getElementById('sort-select')
    .addEventListener('change', (e) => {
      currentSort = e.target.value;
      runPipeline();
    });

  // Global search
  const searchInput = document.getElementById(
    'search-input'
  );
  searchInput.addEventListener(
    'input',
    debounce(() => {
      filters.searchQuery = searchInput.value;
      runPipeline();
    }, 250)
  );
}

function resetFilters() {
  filters.interestRateMin = 0;
  filters.interestRateMax = 100;
  filters.priorities = [
    'VERY_HIGH', 'MEDIUM', 'LOW',
  ];
  filters.creditScore = 'all';
  filters.location = '';
  filters.product = 'all';
  filters.fundingRemainingMin = 0;
  filters.searchQuery = '';

  // Reset UI elements
  document.getElementById(
    'filter-rate-min'
  ).value = 0;
  document.getElementById(
    'filter-rate-max'
  ).value = 100;
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

  // Reset presets
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
  updateThemeButton();

  document.getElementById('theme-toggle')
    .addEventListener('click', toggleDarkMode);
}

function toggleDarkMode() {
  const html = document.documentElement;
  const current = html.getAttribute('data-theme');
  const next = current === 'light'
    ? 'dark' : 'light';
  html.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
  updateThemeButton();

  // Redraw charts with new colors
  renderCharts(filteredLoans);
}

function updateThemeButton() {
  const btn = document.getElementById(
    'theme-toggle'
  );
  const theme = document.documentElement
    .getAttribute('data-theme');
  btn.textContent = theme === 'light' ? '☀️' : '🌙';
}

/* ================================================
   Auto-Refresh
   ================================================ */

function initAutoRefresh() {
  refreshCountdown = 300;
  const el = document.getElementById(
    'refresh-countdown'
  );

  if (countdownInterval) {
    clearInterval(countdownInterval);
  }
  if (refreshInterval) {
    clearInterval(refreshInterval);
  }

  countdownInterval = setInterval(() => {
    refreshCountdown--;
    if (refreshCountdown <= 0) {
      refreshCountdown = 300;
    }
    const min = Math.floor(refreshCountdown / 60);
    const sec = refreshCountdown % 60;
    el.textContent =
      `↻ ${min}:${String(sec).padStart(2, '0')}`;
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

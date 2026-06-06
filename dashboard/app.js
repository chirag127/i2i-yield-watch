// dashboard/app.js
// i2i Yield Watch — Command Center Dashboard
// Pure vanilla JS + Firebase Firestore SDK.
// Reads:
//   Firestore /loans  (where status='active')
//   Firestore /loans  (where status='archived' AND yearMonth='YYYY-MM')
//   Firestore /meta/archiveIndex
//   Firestore /stats/current
//   Firestore /meta/scraper
// Two views: ACTIVE (default) and ARCHIVED, with
// shared filters, search, sort, and pagination.

import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.13.2/firebase-app.js';
import {
  getFirestore, collection, query, where, getDocs, doc, getDoc
} from 'https://www.gstatic.com/firebasejs/10.13.2/firebase-firestore.js';

const FIREBASE_CONFIG = {
  apiKey: 'AIzaSyBJEYOXJ6ZVDlOy9NM6-sLlqFq3VHElJ7M',
  authDomain: 'i2i-yield-watch-db.firebaseapp.com',
  projectId: 'i2i-yield-watch-db',
  storageBucket: 'i2i-yield-watch-db.firebasestorage.app',
  messagingSenderId: '501450484305',
  appId: '1:501450484305:web:d270e046d4f3d523dba670',
};

const fbApp = initializeApp(FIREBASE_CONFIG);
const db = getFirestore(fbApp);

/* ================================================
   State
   ================================================ */
const DATASETS = {
  active: { raw: [], loans: [] },
  archived: { raw: [], loans: [] },
};
let currentView = 'active';
let allLoans = DATASETS.active.loans;
let filteredLoans = [];
let currentPage = 1;
const LOANS_PER_PAGE = 50;
const charts = {};
let refreshInterval = null;
let countdownInterval = null;
let refreshCountdown = 300;
let archiveLoadPromise = null;
let archiveMonthFilter = null;

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
 * Escape text for safe HTML insertion.
 */
function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Build loan URL (mirrors scraper transform.js).
 * Canonical pattern: /public-profile/{borrowerId}/{loanId}.
 * loanId is included when available so the user lands
 * directly on the specific loan's profile. Falls back
 * to borrower-only when loanId is missing.
 */
function buildLoanUrl(borrowerRef, loanId) {
  if (!borrowerRef) {
    return 'https://www.i2ifunding.com/borrower/listing';
  }
  const parts = [
    'https://www.i2ifunding.com/borrower/listing',
    'public-profile',
    encodeURIComponent(String(borrowerRef)),
  ];
  if (loanId !== null && loanId !== undefined
    && loanId !== '') {
    parts.push(encodeURIComponent(String(loanId)));
  }
  return parts.join('/');
}

const debouncedRenderCharts = debounce(renderCharts, 150);

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
 * Compact "X days ago" / "X hours ago" formatter.
 * Used for the "made live" indicator on each card.
 */
function timeSince(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  const ms = Date.now() - d.getTime();
  if (ms < 0) return null;
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  const years = Math.floor(months / 12);
  return `${years}y ago`;
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

    // Load active loans from Firestore
    const q = query(
      collection(db, 'loans'),
      where('status', '==', 'active')
    );
    const snap = await getDocs(q);
    const loans = snap.docs.map((d) => d.data());
    DATASETS.active.raw = loans;
    DATASETS.active.loans = loans;
    allLoans = DATASETS[currentView].loans;

    // Load stats from Firestore (lastUpdated only)
    let lastUpdated = 'NO DATA';
    try {
      const statsDoc = await getDoc(doc(db, 'stats', 'current'));
      if (statsDoc.exists()) {
        const s = statsDoc.data();
        lastUpdated = s.lastUpdated
          ? formatDate(s.lastUpdated)
          : 'NO DATA';
      }
    } catch {
      // stats not available
    }

    const el = document.getElementById('last-updated');
    el.textContent = lastUpdated;

    populateProductFilter(DATASETS.active.loans);
    updateTabCounts();
    runPipeline();
    loading.style.display = 'none';

    loadArchiveData().catch((err) => {
      console.warn('Archive load failed:', err);
    });

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

async function loadArchiveData() {
  if (archiveLoadPromise) return archiveLoadPromise;
  archiveLoadPromise = (async () => {
    // Load archive index from Firestore
    let months = [];
    try {
      const idxDoc = await getDoc(
        doc(db, 'meta', 'archiveIndex')
      );
      if (idxDoc.exists()) {
        months = (idxDoc.data().files || []).slice();
      }
    } catch {
      months = [];
    }

    if (months.length === 0) {
      DATASETS.archived.raw = [];
      DATASETS.archived.loans = [];
      updateTabCounts();
      return;
    }

    // Load archived loans for each month
    const all = [];
    for (const monthEntry of months) {
      try {
        const q = query(
          collection(db, 'loans'),
          where('status', '==', 'archived'),
          where('yearMonth', '==', monthEntry.month)
        );
        const snap = await getDocs(q);
        for (const d of snap.docs) {
          const l = d.data();
          all.push({
            ...l,
            priority: l.priority || 'LOW',
            yieldScore: l.yieldScore || 0,
          });
        }
      } catch {
        // skip month on error
      }
    }
    DATASETS.archived.raw = all;
    DATASETS.archived.loans = all;
    updateTabCounts();
    renderMonthPills();
    if (currentView === 'archived') {
      allLoans = DATASETS.archived.loans;
      runPipeline();
    }
  })();
  return archiveLoadPromise;
}

function updateTabCounts() {
  const ac = document.getElementById(
    'tab-active-count'
  );
  if (ac) {
    ac.textContent = DATASETS.active.raw.length
      .toLocaleString('en-IN');
  }
  const ar = document.getElementById(
    'tab-archived-count'
  );
  if (ar) {
    ar.textContent = DATASETS.archived.raw.length
      .toLocaleString('en-IN');
  }
}

/**
 * Populate the month-pills bar with one pill per
 * month that has archived loans. Pills are clickable
 * and filter the archived view to a single month.
 */
function renderMonthPills() {
  const bar = document.getElementById('month-pills-bar');
  const wrap = document.getElementById('month-pills');
  const clearBtn = document.getElementById(
    'month-pills-clear'
  );
  if (!bar || !wrap) return;

  if (currentView !== 'archived') {
    bar.hidden = true;
    return;
  }
  if (DATASETS.archived.raw.length === 0) {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;

  // Count loans per month. We derive the month from
  // each loan's `archivedAt` timestamp so we don't
  // need the manifest's `files[].month` mapping
  // (which is also fine, but per-loan is more
  // accurate if a single file has straddled months).
  const counts = {};
  for (const loan of DATASETS.archived.raw) {
    const at = loan.archivedAt;
    if (!at) continue;
    const month = at.slice(0, 7); // YYYY-MM
    if (!/^\d{4}-\d{2}$/.test(month)) continue;
    counts[month] = (counts[month] || 0) + 1;
  }
  const months = Object.keys(counts).sort().reverse();
  if (months.length === 0) {
    bar.hidden = true;
    return;
  }

  wrap.innerHTML = months.map((m) => {
    const [y, mo] = m.split('-');
    const label = new Date(
      parseInt(y), parseInt(mo) - 1, 1
    ).toLocaleString('en-IN', {
      month: 'short', year: 'numeric',
    });
    return `<button class="month-pill${
      archiveMonthFilter === m ? ' active' : ''}"
      data-month="${m}">
      ${label}
      <span class="month-pill-count">
        ${counts[m]}
      </span>
    </button>`;
  }).join('');

  for (const pill of wrap.querySelectorAll('.month-pill')) {
    pill.addEventListener('click', () => {
      const m = pill.dataset.month;
      archiveMonthFilter =
        (archiveMonthFilter === m) ? null : m;
      renderMonthPills();
      runPipeline();
    });
  }

  if (clearBtn) {
    clearBtn.hidden = archiveMonthFilter === null;
    clearBtn.onclick = () => {
      archiveMonthFilter = null;
      renderMonthPills();
      runPipeline();
    };
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

function switchView(view) {
  if (view !== 'active' && view !== 'archived') {
    return;
  }
  currentView = view;
  allLoans = DATASETS[view].loans;
  document.querySelectorAll('.view-tab')
    .forEach((t) => {
      t.classList.toggle(
        'active',
        t.dataset.view === view
      );
    });
  // Update stat-card labels for the current view.
  const lblTotal = document.getElementById(
    'stat-total-label'
  );
  const lblHigh = document.getElementById(
    'stat-high-label'
  );
  if (lblTotal) {
    lblTotal.textContent = view === 'archived'
      ? 'Archived' : 'Active';
  }
  if (lblHigh) {
    lblHigh.textContent = view === 'archived'
      ? 'High Yield' : 'High Priority';
  }
  // If the archived view is selected but archive
  // data is still loading, trigger an explicit
  // load so the UI doesn't sit empty.
  if (view === 'archived'
    && DATASETS.archived.raw.length === 0) {
    loadArchiveData().catch(() => {});
  }
  renderMonthPills();
  runPipeline();
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
  debouncedRenderCharts(filteredLoans);
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
  // Apply the month pill filter first when in the
  // archived view — it's the only filter that
  // operates on the loan's archivedAt, not on
  // any of the standard rate/score/credit fields.
  if (currentView === 'archived' && archiveMonthFilter) {
    loans = loans.filter((loan) => {
      const at = loan.archivedAt;
      return at && at.slice(0, 7) === archiveMonthFilter;
    });
  }

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

  const url = escapeHtml(
    loan.loanUrl
      || buildLoanUrl(loan.borrowerRef, loan.loanId)
  );
  const loanId = escapeHtml(loan.loanId);
  const priorityLabel = escapeHtml(
    priority === 'VERY_HIGH' ? 'VERY HIGH' : priority
  );

  // "Made live X days ago" badge — only for active
  // loans with a valid madeLiveOn date.
  const liveAgo = (currentView === 'active')
    ? timeSince(loan.madeLiveOn) : null;

  // Credit display
  const creditDisplay = escapeHtml(
    loan.creditScore === 'No History'
      ? 'NEW'
      : (loan.creditScore || '—')
  );

  return `
    <article class="loan-card priority-${priority}"
      style="animation-delay:${delay}ms"
      id="loan-${loanId}">

      <div class="card-eyebrow">
        <span class="priority-indicator ${priority}">
          ${priority === 'VERY_HIGH'
            ? '<span class="pulse-dot"></span>' : ''}
          ${priorityLabel}
        </span>
        <span class="yield-badge">
          SCORE ${escapeHtml(loan.yieldScore || 0)}
        </span>
      </div>

      ${liveAgo ? `<div class="card-freshness"
        title="Listed on i2iFunding">
        <span class="freshness-dot"></span>
        LIVE ${escapeHtml(liveAgo)}
      </div>` : ''}

      <a class="card-rate" href="${url}"
        target="_blank" rel="noopener"
        title="Open ${escapeHtml(loan.name || 'loan')}
          on i2iFunding (${loanId})">
        <span class="rate-value">${rate}</span>
        <span class="rate-unit">% p.a.</span>
      </a>

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
            ${escapeHtml(loan.location || '—')}
          </span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Tenure</span>
          <span class="meta-value">
            ${escapeHtml(loan.tenure || '—')}
          </span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Risk</span>
          <span class="meta-value">
            ${escapeHtml(loan.riskCategory || '—')}
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
            ${escapeHtml(loan.employmentType || '—')}
          </span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Purpose</span>
          <span class="meta-value">
            ${escapeHtml(loan.purpose || '—')}
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
        target="_blank" rel="noopener"
        title="Open on i2iFunding.com (${escapeHtml(
          loan.borrowerRef
            ? 'borrower profile' : 'listing'
        )})">
        VIEW ON I2IFUNDING
        <svg class="external-icon" viewBox="0 0 12 12"
          width="10" height="10" aria-hidden="true">
          <path d="M3 1h8v8M11 1L4 8M1 3v8h8"
            stroke="currentColor" stroke-width="1.4"
            fill="none" stroke-linecap="round"
            stroke-linejoin="round"/>
        </svg>
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

function upsertBarChart(
  key, canvasId, labels, data, colors, extraOptions = {}
) {
  const ctx = document.getElementById(canvasId)
    .getContext('2d');
  if (charts[key]) {
    charts[key].data.labels = labels;
    charts[key].data.datasets[0].data = data;
    charts[key].data.datasets[0].backgroundColor = colors;
    charts[key].update('none');
    return;
  }
  charts[key] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Loans',
        data,
        backgroundColor: colors,
        borderRadius: 4,
        borderSkipped: false,
      }],
    },
    options: {
      ...chartOpts(),
      plugins: { legend: { display: false } },
      ...extraOptions,
    },
  });
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
  upsertBarChart(
    'rate', 'chart-rate',
    Object.keys(buckets),
    Object.values(buckets),
    CHART_COLORS
  );
}

function renderCreditChart(loans) {
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
  upsertBarChart(
    'credit', 'chart-credit',
    Object.keys(buckets),
    Object.values(buckets),
    [
      '#8a8b8e', '#ff4757', '#ff6348',
      '#ffa502', '#2ed573', '#5f9fd4',
    ]
  );
}

function renderFundingChart(loans) {
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
  const labels = Object.keys(buckets);
  const data = Object.values(buckets);
  const colors = [
    '#5f9fd4', '#c9a962',
    '#ffa502', '#ff4757',
  ];
  const canvasId = 'chart-funding';
  if (charts.funding) {
    charts.funding.data.labels = labels;
    charts.funding.data.datasets[0].data = data;
    charts.funding.update('none');
    return;
  }
  const ctx = document.getElementById(canvasId)
    .getContext('2d');
  charts.funding = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: colors,
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
  const counts = {};
  for (const l of loans) {
    const loc = l.location || 'Unknown';
    counts[loc] = (counts[loc] || 0) + 1;
  }
  const sorted = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10);
  upsertBarChart(
    'location', 'chart-location',
    sorted.map((s) => s[0]),
    sorted.map((s) => s[1]),
    '#c9a962',
    { indexAxis: 'y' }
  );
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
   View Tabs (Active / Archived)
   ================================================ */

function initTabs() {
  document.querySelectorAll('.view-tab').forEach(
    (btn) => {
      btn.addEventListener('click', () => {
        switchView(btn.dataset.view);
      });
    }
  );
}

/**
 * Global keyboard shortcuts.
 *   /  → focus the search box
 *   ← → previous pagination page
 *   → → next pagination page
 *   R  → reset all filters
 *   1 → switch to Active view
 *   2 → switch to Archived view
 *   F → toggle filter panel
 * Skips when an editable element has focus so we
 * don't hijack typing.
 */
function initKeyboard() {
  document.addEventListener('keydown', (e) => {
    const tag = (e.target && e.target.tagName || '')
      .toUpperCase();
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) {
      // Esc clears the focused input
      if (e.key === 'Escape' && e.target.id) {
        e.target.blur();
      }
      return;
    }
    const tp = Math.max(1, Math.ceil(
      filteredLoans.length / LOANS_PER_PAGE
    ));
    switch (e.key) {
      case '/':
        e.preventDefault();
        document.getElementById('search-input')
          .focus();
        break;
      case 'ArrowLeft':
        if (currentPage > 1) {
          goToPage(currentPage - 1);
        }
        break;
      case 'ArrowRight':
        if (currentPage < tp) {
          goToPage(currentPage + 1);
        }
        break;
      case 'r':
      case 'R':
        resetFilters();
        break;
      case '1':
        switchView('active');
        break;
      case '2':
        switchView('archived');
        break;
      case 'f':
      case 'F': {
        const panel = document.getElementById(
          'filter-panel'
        );
        panel.classList.toggle('open');
        const btn = document.getElementById(
          'filter-toggle'
        );
        btn.textContent = panel.classList.contains('open')
          ? '△ FILTERS' : '▽ FILTERS';
        break;
      }
    }
  });
}

/**
 * Toggle the sticky filter class when the panel
 * is open and the user scrolls past the header.
 * This makes the open filter panel stick to the
 * bottom of the sticky header so controls never
 * disappear while scrolling the long loan list.
 */
function initStickyFilter() {
  const panel = document.getElementById('filter-panel');
  const header = document.querySelector('.header');
  if (!panel || !header) return;
  const onScroll = () => {
    if (!panel.classList.contains('open')) {
      panel.classList.remove('sticky');
      return;
    }
    const h = header.getBoundingClientRect();
    // When the header bottom hits the top, pin the
    // panel to the header.
    panel.classList.toggle('sticky', h.bottom <= 0);
  };
  window.addEventListener('scroll', onScroll, {
    passive: true,
  });
  onScroll();
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
    archiveLoadPromise = null;
    loadArchiveData().catch(() => {});
    refreshCountdown = 300;
  }, 300000);
}

/* ================================================
   Init
   ================================================ */

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  initFilters();
  initTabs();
  initKeyboard();
  initStickyFilter();
  loadData();
  initAutoRefresh();
});

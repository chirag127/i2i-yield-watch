# i2i-yield-watch 🔍

> **Automated high-yield loan intelligence platform
> for i2iFunding.** Polls the public borrower listing
> every 5 minutes aligned to IST quarter-fives
> (1:00, 1:05, 1:10, ... 1:55 PM IST, etc.) via a
> single HTTP call to i2iFunding's own API, scores
> loans by yield potential, and dispatches a
> standardized 19-field alert the first time a loan
> crosses the high-yield threshold (rate > 50%) to
> **Telegram, Gmail, Discord, and ntfy.sh**. Each
> unique loan is announced exactly once.

[![Auto Scraper](https://github.com/chirag127/i2i-yield-watch/actions/workflows/scrape.yml/badge.svg)](https://github.com/chirag127/i2i-yield-watch/actions/workflows/scrape.yml)

## ✨ Features

- 🤖 **Fully Automated** — Polls i2iFunding
  every **5 minutes** via GitHub Actions
  (aligned to IST :00, :05, :10, ..., :55)
- ⚡ **Fast Path** — A single POST to
  `api.i2ifunding.com` (the same call the
  site's Angular SPA makes). ~1 second per
  run, no headless browser required.
- 🛡️ **Resilient** — Pure-HTTP path
  automatically falls back to the legacy
  Playwright/DOM path if the API ever
  changes shape or returns an error.
  Playwright is **always installed** in CI
  (cached for ~5s on warm runs) so the
  fallback is hot.
- 📊 **Live Dashboard** — Premium dark-mode
  dashboard with **Active** and **Archived**
  tabs, month pills, filters, charts, search,
  keyboard shortcuts, and pagination
- 🔥 **Yield Scoring** — Custom 0–100 opportunity
  score (higher interest = better)
- 🔔 **New-Loan-Only Alerts** — Telegram, Gmail,
  Discord, **and ntfy.sh** notifications fire
  only for loans with interest rate **strictly
  above 50%** (configurable). Each unique loan
  is **announced exactly once**.
- 📋 **Standardized 19-Field Format** — Every
  channel (Telegram, Gmail, Discord, ntfy)
  renders the same shared 19-field block: 15
  critical fields always shown + 4 best-effort
  optional fields silently omitted when missing.
  No percent symbols in the funding breakdown —
  only Indian-formatted amounts (`₹1,23,456`).
- 🔐 **Encrypted Secrets** — `.env` is encrypted
  with **git-crypt symmetric mode** and committed
  to the repo. CI unlocks it with
  `flydiverny/setup-git-crypt@v5`. No more
  scattered GitHub Secrets.
- 🏛️ **SOLID Architecture** — Cleanly folderized
  into `core/`, `notifiers/`, `utils/`, `browser/`,
  and `test/` layers. The 19-field block is built
  **once** (`formatLoanBlock`) and rendered by all
  four channels, so format changes ripple to every
  channel in one PR.
- 📁 **Historical Data** — Monthly archives of
  funded loans, plus a manifest (`index.json`)
  the dashboard reads for the Archived tab
- 🆓 **100% Free** — GitHub Actions + Pages, no
  paid services, no servers

## 🚀 Live Dashboard

**[View Dashboard →](https://chirag127.github.io/i2i-yield-watch/)**

The dashboard has two views, switched by the
top-left tab bar:

- **ACTIVE** — every loan currently in
  `data/active_loans.json`. Includes ALL rates,
  not just high-yield. Default view.
- **ARCHIVED** — every loan from every monthly
  archive file (`fully_funded_YYYY_MM.json`).
  Lazy-loaded on first switch; the manifest at
  `data/archive/index.json` lists all available
  months.

Both views share the same filter / search / sort
controls and the same 50-per-page client-side
pagination.

## 📋 How It Works

```
                   ┌─────────────────────┐
                   │   i2iFunding API    │
                   │  (public borrower   │
                   │   listing endpoint) │
                   └──────────┬──────────┘
                              │ POST getActiveFilteredBorrowers
                              ▼
                   ┌─────────────────────┐
                   │ src/core/api.js     │  ← fast path
                   │   (pure Node https) │     ~1s per run
                   └──────────┬──────────┘
                              │ if API throws…
                              ▼
                   ┌─────────────────────┐
                   │ src/browser/        │  ← fallback
                   │   scraper.js        │  (Playwright)
                   │   (DOM parse)       │
                   └──────────┬──────────┘
                              │
                              ▼
                   ┌─────────────────────┐
                   │ src/core/transform  │  (API/DOM → normal)
                   │ formatLoanBlock()   │  (one 19-row block)
                   └──────────┬──────────┘
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
        ┌──────────┐   ┌──────────┐    ┌──────────┐
        │ src/core │   │ src/     │    │ src/core │
        │ storage  │   │ notifiers│    │ storage  │
        │ (loanId  │   │ TG/Email/│    │ archive  │
        │  dedup)  │   │ Discord/ │    │ (monthly)│
        │          │   │ ntfy     │    │          │
        └────┬─────┘   └────┬─────┘    └──────────┘
             │              │
             ▼              ▼
      ┌───────────────┐  ┌──────────┐
      │  data/ JSON   │  │ Telegram │
      │ (committed)   │  │  / Gmail │
      │               │  │  / Discord│
      │               │  │  / ntfy  │
      └──────┬────────┘  └──────────┘
             │
             ▼
      ┌───────────────┐
      │   Dashboard   │
      │ (GitHub Pages)│
      └───────────────┘
```

The fast path is a single POST to the same endpoint
the i2iFunding Angular SPA calls when you load
`/borrower/listing`. No headless browser, no
pagination-clicking, no brittle DOM selectors. The
legacy Playwright/DOM path is kept as a fallback
(triggered automatically on API failure, or manually
via `USE_PLAYWRIGHT_FALLBACK=true`) so the scraper
keeps working even if the i2iFunding backend ever
changes shape.

## 🏛️ Project Architecture (SOLID)

The scraper follows **SOLID principles** with a
clean folderized structure:

```
scraper/
├── src/
│   ├── core/            ← business logic
│   │   ├── api.js       ← fast-path HTTP fetcher
│   │   ├── transform.js ← normalization + formatLoanBlock
│   │   ├── storage.js   ← JSON I/O + loanId dedup
│   │   └── index.js     ← orchestrator (main entry)
│   ├── notifiers/       ← presentation (one file per channel)
│   │   ├── telegram.js
│   │   ├── email.js
│   │   ├── discord.js
│   │   ├── ntfy.js
│   │   └── notifier.js  ← multi-channel dispatcher
│   ├── utils/           ← stateless helpers
│   │   ├── logger.js
│   │   └── scorer.js
│   └── browser/         ← Playwright fallback (DOM path)
│       ├── scraper.js
│       ├── parser.js
│       └── showmore.js
├── test/                ← smoke + syntax tests
│   ├── smoketest.js     ← offline smoke tests (6 categories)
│   ├── verify_syntax.js
│   └── explore-site.js
├── package.json
└── node_modules/        ← gitignored
```

Key design choices:

- **Single source of truth for messages** — the
  same `formatLoanBlock(loan)` function in
  `src/core/transform.js` builds the 19-row block
  that **every** channel renders. Change the order
  or add a field once, and all four channels
  (Telegram, Email, Discord, ntfy) update
  automatically.
- **Interface segregation** — every notifier
  exports a single `send*(loans, stats, url, opts)`
  function with the same signature. The dispatcher
  in `notifier.js` calls each enabled one
  independently; a single channel failure does not
  affect the others.
- **Dependency inversion** — `core/` and `utils/`
  have no dependencies on `notifiers/`. The notifier
  layer imports core (transform), never the other
  way around.
- **No `crypto`** — fingerprinting is gone. Dedup
  is a single sorted `Set` of `loanId`s in
  `data/notifications_sent.json`.

## 📋 Notification Message Format

Every channel (Telegram, Gmail, Discord, ntfy)
renders the **same compact, label-free line list**
built once by
`scraper/src/core/transform.js → formatLoanBlock()`.
This keeps the channels consistent and the format
easy to evolve — change the function, all four
channels update.

### Example output

A real loan renders as:

```
🔥 75.22% p.a. · Yield 38.19/100
i2i-#1323223 · Loan 1415135
₹9,781 · ₹5,000 funded · ₹4,781 left
Credit No History · Risk X
Ashish Ram · Age 24 · Darbhanga Kiara
Self Employed Professional — Men's Grooming Professional
₹20,000/mo · 2 Months
To purchase Men's grooming/spa kit
Live 05-06-2026
https://www.i2ifunding.com/borrower/listing/public-profile/1323223
```

### Line order (lending-decision importance)

Lines appear in the order a lender would scan them:

| Line | What it carries | Why first |
|------|-----------------|-----------|
| 1 | **Rate + Yield Score** (e.g. `🔥 75.22% p.a. · Yield 38.19/100`) | The single ROI signal — always at the top, always bolded in Telegram |
| 2 | **Identity** (`i2i-#XXXX · Loan YYYY`) | Uniquely identifies the listing |
| 3 | **Funding** (total / funded / left) | How much exposure, how much is open |
| 4 | **Credit + Risk** (e.g. `Credit 720 · Risk D`) | Default-risk signals |
| 5 | **Borrower** (name · age · location) | Who they are |
| 6 | **Employment** (type — profession / business) | Income source |
| 7 | **Income / Tenure / Home** (e.g. `₹20,000/mo · 2 Months · Own House`) | Ability to repay |
| 8 | **Purpose** (the loan's narrative reason) | What it's for |
| 9 | **Made Live On** (e.g. `Live 05-06-2026`) | Freshness |
| 10 | **URL** | Click-to-open the listing |

### Key rules

- **NO field labels.** Words like "Loan", "Credit",
  "Borrower", "Employment", "Tenure", "Purpose",
  "Location", "Name", "Age", "URL" are dropped —
  the value's own content carries the meaning
  ("2 Months" is tenure, a personal-loan narrative
  is purpose, a city name is location, etc.).
- **The only `%` anywhere in the message** is in
  the rate line. Funding rows show only
  Indian-formatted amounts (`₹9,781`,
  `₹5,000 funded`, `₹4,781 left`) — no `51%`.
- **Related info is grouped on one line** with `·`
  separators (e.g. `Ashish Ram · Age 24 · Darbhanga
  Kiara`).
- **Missing data drops the whole line** (no
  `"N/A"`, no empty rows, no `—` placeholders).
- **All amounts use Indian-style grouping**
  (`₹1,23,456`).
- **Yield Score is back** at the top with the rate.
  It is a 0–100 opportunity score weighted 55% on
  interest rate, 30% on credit score, 15% on
  income/funding/loan size (see the next section).

### Channel-specific rendering

- **Telegram** — first line bolded via `<b>...</b>`,
  rest plain. Long lists auto-chunked into
  multiple messages (≤ 4096 chars each, 1.1s
  delay between sends).
- **Gmail** — each loan becomes a card with a
  rate-colored left border. The rate + yield line
  is a large colored header; the remaining lines
  stack as `<p>` rows.
- **Discord** — first line becomes the embed title,
  the rest become the embed description (joined
  with `\n`). The URL line, when present, becomes
  the embed URL so the title is clickable. 10
  embeds per webhook call.
- **ntfy** — all lines joined with `\n` into one
  push body. The push title shows the count +
  threshold.

## 📊 Understanding the Yield Score

The **Yield Score** (0–100) is an **opportunity
score**, not a risk score. It intentionally favors
high-interest rate loans:

| Factor            | Weight | Bounds (Min–Max)        | Logic |
|-------------------|--------|--------------------------|-------|
| Interest Rate     | **55%** | 0% – 200% (No limit)   | Higher = Better |
| Credit Score      | **30%** | 300 – 900               | Higher = Better (Null / No History treated neutrally) |
| Monthly Income    | **5%**  | ₹0 – ₹20,00,000         | Higher = Better |
| Funding Remaining | **5%**  | 0% – 100%               | More left = Better |
| Loan Amount       | **5%**  | ₹0 – ₹50,00,000         | Larger = Better |

**Credit Score Neutralization**:
- If a borrower has **No History** or no credit
  score grade is available, they are **not
  penalized** (they receive a neutral `0.5` score
  component value).

**Priority Levels:**
- 🔥 **VERY HIGH** — Interest rate ≥ 70% p.a.
- 🟡 **MEDIUM** — Interest rate 50–69% p.a.
- ⚪ **LOW** — Interest rate < 50% p.a.

> **Important:** Category X risk grade is treated
> neutrally. High interest rates are never penalized
> — they are the primary yield signal.

## 🔔 Notification Behavior

Every 5 minutes the scraper runs (at IST :00,
:05, :10, ..., :55). The notifier fires for
**active loans** whose interest rate is
**strictly greater than**
`NOTIFY_RATE_THRESHOLD` (default `50`). Each
unique loan is announced exactly once.

Key properties:

- **Rate filter** — only loans with
  `interestRate > NOTIFY_RATE_THRESHOLD` are
  notified. Below-threshold loans are ignored
  entirely.
- **loanId dedup** — every qualifying loan is
  identified by its public `loanId` (the same
  `pl_bloan_id` the API returns in
  `active_loans.json`). A loan is sent only on
  the first run where its `loanId` is unseen;
  subsequent runs skip it. The dedup store
  lives at `data/notifications_sent.json` and
  is committed back to the repo each run.
  *(i2iFunding's own borrower FAQ states "one
  borrower can apply for only one loan at a
  time", so a single `loanId` is sufficient to
  uniquely identify a live listing.)*
- **Standardized 19-field block** — every
  channel renders the same row order with the
  same critical/optional split (see the
  "Notification Message Format" section above).
  Optional fields are silently omitted when
  missing; critical fields are always shown
  when their source value is present. **No
  percent symbols in the funding breakdown**
  (only formatted amounts); the only `%` in the
  message is the single interest rate.
- **No promotional copy** — messages contain
  only the count, threshold, and loan data.
  Phrases like "complete list", "nothing
  hidden", or "sent in full" are intentionally
  not used.
- **Auto-chunked** — long lists are split into
  multiple Telegram messages (≤ 4096 chars each)
  or multiple Discord embeds (≤ 10 embeds per
  webhook call). Telegram chunks respect the
  1 msg/sec/chat bot rate limit with a small
  delay between sends.
- **Channels** — Telegram, Gmail, Discord, and
  ntfy.sh are all independent. Enable whichever
  you want via the `*_ENABLED` secrets.

## 🛠️ Setup Guide (Step by Step)

### Prerequisites

- GitHub account (free)
- For Telegram: Telegram account
- For Email: Gmail account with 2FA enabled
- For Discord: Discord server with webhook access
- For ntfy.sh: nothing — the default
  `https://ntfy.sh` is free, anonymous, and needs
  no signup. Subscribe in the ntfy mobile/desktop
  app or at `https://ntfy.sh/<your-topic>`

### Step 1 — Fork / Clone the Repository

```bash
git clone https://github.com/chirag127/i2i-yield-watch.git
cd i2i-yield-watch
```

### Step 2 — Set Up Telegram Bot (Optional)

1. Open Telegram, search for **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **Bot Token**
   (format: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)
4. Start your bot (send it `/start`)
5. Get your **Chat ID** by visiting:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
   Look for `"chat":{"id": XXXXXXXX}` in the JSON
6. Add to GitHub Secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `TELEGRAM_ENABLED` = `true`

### Step 3 — Set Up Gmail Notifications (Optional)

1. Enable **2FA** on your Google account
   ([Security Settings](https://myaccount.google.com/security))
2. Go to
   [App Passwords](https://myaccount.google.com/apppasswords)
3. Create App Password for **"Mail"**
4. Copy the **16-character password**
5. Add to GitHub Secrets:
   - `EMAIL_FROM` = `your_gmail@gmail.com`
   - `EMAIL_APP_PASSWORD` = `16-char password`
   - `EMAIL_TO` = `recipient@email.com`
   - `EMAIL_ENABLED` = `true`

### Step 4 — Set Up Discord Webhook (Optional)

1. Open your Discord server
2. **Server Settings** → **Integrations**
   → **Webhooks** → **New Webhook**
3. Name it `i2i Yield Watch`, select channel
4. Click **Copy Webhook URL**
5. Add to GitHub Secrets:
   - `DISCORD_WEBHOOK_URL` = webhook URL
   - `DISCORD_ENABLED` = `true`

### Step 5 — Set Up ntfy.sh (Optional)

1. Pick a unique topic name — it acts as your
   shared password. Example:
   `i2i-yield-watch-my-secret-12345`
2. Subscribe to it:
   - **Web:** open
     `https://ntfy.sh/i2i-yield-watch-my-secret-12345`
     in any browser
   - **Mobile:** install the ntfy app (iOS / Android),
     add a subscription with the topic name
   - **Desktop:** use the ntfy CLI or web
3. Add to GitHub Secrets:
   - `NTFY_ENABLED` = `true`
   - `NTFY_TOPIC` = `your-unique-topic`
   - `NTFY_BASE_URL` (optional) = your self-hosted
     ntfy server, or leave unset for the default
     `https://ntfy.sh`
   - `NTFY_USER` / `NTFY_PASSWORD` (optional) — only
     if you're using an access-controlled self-hosted
     ntfy

### Step 6 — Add GitHub Secrets

Go to: **Repository** → **Settings** →
**Secrets and Variables** → **Actions** →
**New repository secret**

| Secret Name             | Required       | Description |
|-------------------------|----------------|-------------|
| `TELEGRAM_ENABLED`      | No             | `"true"` or `"false"` |
| `TELEGRAM_BOT_TOKEN`    | If Telegram    | From @BotFather |
| `TELEGRAM_CHAT_ID`      | If Telegram    | Your chat ID |
| `EMAIL_ENABLED`         | No             | `"true"` or `"false"` |
| `EMAIL_FROM`            | If Email       | Gmail address |
| `EMAIL_APP_PASSWORD`    | If Email       | 16-char App Password |
| `EMAIL_TO`              | If Email       | Recipient email |
| `DISCORD_ENABLED`       | No             | `"true"` or `"false"` |
| `DISCORD_WEBHOOK_URL`   | If Discord     | Webhook URL |
| `NTFY_ENABLED`          | No             | `"true"` or `"false"` |
| `NTFY_TOPIC`            | If ntfy        | Your unique topic name (the shared "password") |
| `NTFY_BASE_URL`         | No             | `https://ntfy.sh` (default) or your self-hosted URL |
| `NTFY_USER`             | No             | Only for access-controlled self-hosted ntfy |
| `NTFY_PASSWORD`         | No             | Only for access-controlled self-hosted ntfy |
| `DASHBOARD_URL`         | Recommended    | GitHub Pages URL |

> **Alternative — git-crypt encrypted `.env`**
> If you'd rather not maintain 10+ separate GitHub
> Secrets, see the **git-crypt Setup** section
> below. The repository's `.env` is encrypted with
> git-crypt symmetric mode and committed to the
> repo. CI unlocks it automatically.

### Step 7 — Enable GitHub Pages

1. Repository → **Settings** → **Pages**
2. Source: **GitHub Actions**
3. After first workflow run, your dashboard will
   be live at:
   `https://YOUR_USERNAME.github.io/i2i-yield-watch/`

### Step 8 — Enable GitHub Actions

1. Repository → **Actions** tab
2. Click **"Enable Actions"** if prompted
3. Run manually: **Actions** →
   **"i2i Yield Watch — Auto Scraper"** →
   **Run workflow**
4. After confirming it works, it auto-runs
   every 5 minutes at exact IST marks
   (1:00, 1:05, 1:10, ..., 1:55 PM IST, etc.)

> **How the schedule works**
> GitHub Actions cron runs in UTC. IST is
> UTC+5:30, so each IST mark maps to a different
> UTC minute. All 12 marks (`:00`, `:05`, `:10`,
> ..., `:55`) fit inside a single cron expression
> using a comma-separated minute list:
>
> ```
> cron: '30,35,40,45,50,55,0,5,10,15,20,25 * * * *'
> ```
>
> The list spans two UTC hours (`...50,55` and
> `0,5,...25`) but a cron list is interpreted
> per-field, so the workflow fires 12 times per
> hour, once per IST mark.
>
> The workflow also accepts a `repository_dispatch`
> trigger on event type `tick` for an external
> cron fallback (see Troubleshooting below).

### Step 9 — Update Dashboard URL Secret

After your first deployment, update the
`DASHBOARD_URL` secret with your actual
GitHub Pages URL.

## 🔐 git-crypt Setup (Optional)

By default the repo uses GitHub Secrets for
credentials. For a more ergonomic workflow,
the repo supports **git-crypt symmetric mode**:
the real `.env` is encrypted with a single key
file and committed to the repo. CI unlocks it
via `flydiverny/setup-git-crypt@v5`.

**Note:** `git-crypt` does not have an official
Windows binary. Run the one-time setup on Linux,
macOS, or WSL.

### One-time setup (on Linux/macOS/WSL)

```bash
# 1. Install git-crypt
sudo apt install git-crypt   # Debian/Ubuntu
brew install git-crypt        # macOS

# 2. Initialize git-crypt in the repo
git-crypt init

# 3. (Already done in this repo) Add to
#    .gitattributes:
#      .env filter=git-crypt diff=git-crypt

# 4. Export the symmetric key
git-crypt export-key .git/git-crypt-key

# 5. Base64-encode the key for GitHub Secrets
base64 -w 0 .git/git-crypt-key > git-crypt-key.b64
# Copy the contents of git-crypt-key.b64

# 6. Add the base64 string as a GitHub Secret
#    named GIT_CRYPT_KEY
```

### CI integration

The workflow already has:

```yaml
- name: Unlock git-crypt
  uses: flydiverny/setup-git-crypt@v5
  with:
    git-crypt-key: ${{ secrets.GIT_CRYPT_KEY }}
```

When `GIT_CRYPT_KEY` is set, CI unlocks the repo
and the scraper reads `.env` via dotenv. When
`GIT_CRYPT_KEY` is **not** set, the step is a
no-op and the scraper falls back to GitHub
Secrets. The two methods are fully
interchangeable — pick one, or use both as
backup.

## 📁 Data Structure

```
data/
├── active_loans.json        ← Current active loans
├── notifications_sent.json  ← loanId dedup store
├── stats.json               ← Aggregate statistics
├── changelog.json           ← Scrape run history
└── archive/
    ├── index.json           ← Manifest of monthly files
    └── fully_funded_YYYY_MM.json  ← Monthly archive
```

- **active_loans.json** — Single source of truth
  for the dashboard. Updated every scrape run.
- **notifications_sent.json** — Sorted set of
  `loanId`s that have already been announced.
  Each unique loan lives here forever once
  announced; nothing is re-sent.
- **archive/index.json** — Manifest of every
  monthly archive file with its record count.
  The dashboard reads this to populate the
  Archived tab.
- **archive/** — Fully funded or disappeared loans
  are moved here monthly.
- **changelog.json** — Last 200 scrape runs logged.

## ⚙️ Configuration

All configuration is via environment variables
(set in `.env` or GitHub Secrets):

| Variable                       | Default | Description |
|--------------------------------|---------|-------------|
| `NOTIFY_RATE_THRESHOLD`        | `50`    | Strictly-greater rate gate for **notifications** (each unique loan is announced once) |
| `HIGH_PRIORITY_RATE_THRESHOLD` | `70`    | Min rate for VERY_HIGH priority |
| `MEDIUM_PRIORITY_RATE_THRESHOLD` | `50`  | Min rate for MEDIUM priority |
| `MAX_SHOW_MORE_CLICKS`         | `150`   | Safety limit for Playwright Show More clicks |
| `USE_PLAYWRIGHT_FALLBACK`      | `true`  | Use the Playwright DOM fallback path when API fails |
| `TELEGRAM_ENABLED`             | `false` | Enable Telegram channel |
| `EMAIL_ENABLED`                | `false` | Enable Gmail SMTP channel |
| `DISCORD_ENABLED`              | `false` | Enable Discord webhook channel |
| `NTFY_ENABLED`                 | `false` | Enable ntfy.sh channel |
| `DASHBOARD_URL`                | —       | Your GitHub Pages URL |

> The notification filter is intentionally
> **strictly greater than** (`>`), not
> greater-than-or-equal. A loan with
> `interestRate == 50` will NOT be notified by
> default; raise the threshold to allow
> borderline loans through.

## 🔧 Running Locally

```bash
# 1. Clone the repo
git clone https://github.com/chirag127/i2i-yield-watch.git
cd i2i-yield-watch

# 2. (If using git-crypt) unlock the .env
git-crypt unlock /path/to/git-crypt-key
# Otherwise, copy the template:
cp .env.example .env
# Edit .env with your credentials

# 3. Install scraper dependencies
cd scraper
npm install

# 4. (Optional) Install Playwright browser
# The default fast path is pure HTTP and
# needs no browser. Playwright is only used
# by the DOM fallback path.
npx playwright install chromium

# 5. Run the scraper (default: fast HTTP path)
node src/core/index.js
# Or force the legacy DOM/Playwright path:
USE_PLAYWRIGHT_FALLBACK=true node src/core/index.js

# 6. (Optional) Run smoke + syntax tests
node test/smoketest.js
node test/verify_syntax.js
```

To view the dashboard locally, serve the project
root with any static file server:

```bash
# From project root
npx serve .
# Then open http://localhost:3000/dashboard/
```

## ❓ Troubleshooting

### GitHub Actions not running on schedule
- Confirm Actions are enabled in repo settings.
- The workflow uses
  `concurrency.cancel-in-progress: false` so a
  long run cannot be silently killed by the next
  tick. If you see a run stuck in *queued* for
  more than ~10 minutes, cancel the previous
  in-progress run from the Actions UI.
- The schedule is a single cron entry that
  fires every 5 minutes at IST :00, :05, :10,
  ..., :55 (see the "How the schedule works"
  block in Step 8 above). If you want a
  different cadence, edit
  `.github/workflows/scrape.yml` and remember
  that GitHub cron is in UTC.
- GitHub may pause scheduled workflows on a
  public repo after 60 days of no activity. Push
  any commit (or trigger a manual `workflow_dispatch`
  run) to reset the timer.

### External ping (fallback cron)
GitHub's built-in cron can drop runs during
high-load periods. For a reliable 5-minute
schedule, set up a free external cron service
to ping the workflow's `repository_dispatch`
endpoint:

1. Create a GitHub PAT with `repo` scope
   ([Settings → Developer settings → PAT](https://github.com/settings/tokens)).
2. Sign up for a free cron service such as
   [cron-job.org](https://cron-job.org),
   [EasyCron](https://www.easycron.com), or
   [UptimeRobot](https://uptimerobot.com).
3. Add a job that runs every 5 minutes and
   POSTs to:
   ```
   POST https://api.github.com/repos/chirag127/i2i-yield-watch/dispatches
   Accept: application/vnd.github+json
   Authorization: Bearer <YOUR_PAT>
   Content-Type: application/json

   {"event_type": "tick"}
   ```
4. The workflow's `repository_dispatch: [tick]`
   trigger fires `node src/core/index.js` exactly
   the same way as the built-in schedule. This
   gives you a belt-and-braces 5-minute cadence.

### Dashboard not deploying
- Set Pages source to "GitHub Actions" in Settings
- Check that `actions/deploy-pages@v4` has proper
  permissions (`pages: write`, `id-token: write`)

### No notifications received
- Verify the `*_ENABLED` secret is exactly `"true"`
- Check GitHub Actions logs for error messages
- Confirm the active loan set contains at least
  one loan with `interestRate > NOTIFY_RATE_THRESHOLD`
- For Telegram: ensure you started the bot
  with `/start`
- For Email: ensure 2FA is on and you're using an
  App Password (not your account password)
- For ntfy: check the topic is correct and you
  are subscribed on the device that should
  receive the push

### Only seeing partial list on Telegram
- The new-loan digest is split across multiple
  consecutive messages when it exceeds 4096
  characters per message. Scroll up in your chat
  to see all chunks. A 1.1-second delay is
  inserted between chunks to respect Telegram's
  per-chat bot rate limit.

### Scraper fails with timeout
- The site may be temporarily down
- The fast path (pure HTTP) will fail → the
  scraper auto-falls back to the Playwright
  DOM path. If both fail, GitHub Actions will
  retry on the next scheduled tick.
- Check if the site structure has changed
  (compare scraper/src/browser/parser.js
  selectors against the live page)

### git-crypt not unlocking in CI
- Verify the `GIT_CRYPT_KEY` secret is set and
  is the **base64** of the exported key file
  (not the raw key file contents)
- Use `base64 -w 0` (Linux) or
  `base64 -i key | tr -d '\n'` (macOS) to
  produce a single-line base64 string
- The setup-git-crypt step is a no-op if
  `GIT_CRYPT_KEY` is empty — the workflow
  falls back to GitHub Secrets

## 🏗️ Tech Stack

| Component              | Technology |
|------------------------|------------|
| Scraper (fast path)    | Node.js `https` POST |
| Scraper (fallback)     | Node.js + Playwright |
| Scheduler              | GitHub Actions (cron) |
| Storage                | JSON files in repo |
| Secrets                | git-crypt symmetric OR GitHub Secrets |
| Dashboard              | HTML + CSS + Vanilla JS |
| Charts                 | Chart.js 4.5.1 |
| Hosting                | GitHub Pages |
| Notifications          | Telegram + Gmail + Discord + ntfy.sh |

## 🛠️ Skills & Tools Used

- **playwright** — powers the DOM fallback path
  (`scraper/src/browser/`) and the live-page
  exploration script (`scraper/test/explore-site.js`)
- **webapp-testing** — used during initial
  development to verify the live i2iFunding page
  structure and selectors

## ⚡ Speed Optimizations

The default workflow run is **~30 seconds** (cold)
or **~15 seconds** (warm cache). The biggest
savings come from skipping the Playwright browser
install when the pure-HTTP fast path is used, and
caching it for when the fallback is needed.

| Optimization                            | Saves         | Where |
|-----------------------------------------|---------------|-------|
| `actions/cache@v4` for `node_modules`   | ~10-20s on warm runs | keyed on `scraper/package-lock.json` |
| `actions/cache` for Playwright Chromium | ~60s on warm runs | (warm cache → ~5s install) |
| Shallow checkout (`fetch-depth: 1`)     | ~5-10s        | First `actions/checkout` step |
| `npm ci --prefer-offline --no-audit`    | ~2-5s         | Cached tarball resolution, skips audit metadata fetch |
| Pure-HTTP fetch (no Playwright launch)  | ~30s/run      | `scraper/src/core/api.js` fast path |
| Startup jitter (0–2s)                   | spreads load  | `STARTUP_JITTER_MS` (default 2000) in `main()` |
| Parallel API page fetch (5 at a time)   | ~2–5s on large catalogs | `scraper/src/core/api.js` |
| Parallel notification channels          | ~1–3s         | `scraper/src/notifiers/notifier.js` |
| Skip Pages deploy when data unchanged   | ~30–60s       | `data_changed` output in `scrape.yml` |
| `concurrency.cancel-in-progress: false` | no dropped ticks | Queueing instead of cancelling |

The Playwright install is **unconditional** in CI
(per project policy — fallback must be hot). On
warm cache it's ~5 seconds; on cold cache it's
~60 seconds. The previous workflow's
`if: env.USE_PLAYWRIGHT_FALLBACK == 'true'` gate
has been removed: even when the fast path is used
on the happy day, we want the browser ready for
the next 5-min tick that hits the API fallback.

## 🧪 Testing

Two no-network test suites ship with the repo:

```bash
cd scraper
npm test                   # smoketest + syntax checks
node test/smoketest.js     # offline smoke tests
node test/verify_syntax.js # parse checks for all source files
```

The smoke test covers:
- **Rate filter & scoring** — `>50` filter, priority
  bands, yield score bounds
- **Notifiers (Telegram / Email / Discord / ntfy)**
  — chunker, N/A omission, 19-field block, ntfy
  body shape, ntfy disabled, ntfy missing topic,
  no promo copy
- **Loan ID & dedup** — `loanId` from `pl_bloan_id`,
  `filterUnnotified`, idempotent `markNotificationsSent`,
  no SHA-1 fingerprinting
- **API payload (no network)** — `buildFilterBody`
  JSON, `fetchPage` POST + headers, constants
- **Transform** — 14 helpers + 19-row block + silent
  optional omission
- **Workflow & layout** — cron = 5-min IST, dispatch,
  no-cancel, 10-min, cached, unconditional Playwright,
  git-crypt, SOLID folder structure, single README,
  encrypted `.env`

Run them locally before pushing. CI runs `npm test`
before every scrape on `main`, scheduled ticks, and
manual dispatch.

## 📄 License

MIT

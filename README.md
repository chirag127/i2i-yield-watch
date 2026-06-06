# i2i-yield-watch 🔍

> **Automated high-yield loan intelligence platform
> for i2iFunding.** Polls the public borrower listing
> every 5 minutes aligned to IST quarter-fives
> (1:00, 1:05, 1:10, ... 1:55 PM IST, etc.) via a
> single HTTP call to i2iFunding's own API, scores
> loans by yield potential, and dispatches a compact
> label-free line list the first time a loan crosses
> the high-yield threshold (rate > 50%) to
> **Telegram, Gmail, Discord, and ntfy.sh**. Each
> unique loan is announced exactly once. State lives
> in **Cloud Firestore** (free, unlimited projects)
> — no JSON files in the repo, no static data.

[![Auto Scraper](https://github.com/chirag127/i2i-yield-watch/actions/workflows/scrape.yml/badge.svg)](https://github.com/chirag127/i2i-yield-watch/actions/workflows/scrape.yml)

## ✨ Features

- 🤖 **Fully Automated** — Polls i2iFunding
  every **5 minutes** via GitHub Actions
  (aligned to IST :00, :05, :10, ..., :55)
- ⚡ **In-Browser API Fetch** — A single
  Playwright session opens the listing
  page, then `page.evaluate(fetch)` calls
  the same `getActiveFilteredBorrowers`
  endpoint the i2iFunding Angular SPA
  uses. The browser context bypasses the
  502 block that direct Node.js `fetch`
  hits, and we get clean JSON with more
  data than the DOM (real borrower name,
  purpose narrative, nature-of-work, etc.).
  ~5-15 seconds for 20 loans.
- 🛡️ **Resilient** — The in-browser API
  path automatically falls back to the
  legacy Playwright/DOM path if the API
  ever changes shape or returns an error.
  Playwright is **always installed** in CI
  (cached for ~5s on warm runs) so the
  fallback is hot.
- 📊 **Live Dashboard** — Premium dark-mode
  dashboard with **Active** and **Archived**
  tabs, month pills, filters, charts, search,
  keyboard shortcuts, and pagination. Reads
  **directly from Firestore** at runtime — no
  static JSON, always fresh.
- 🔥 **Yield Scoring** — Custom 0–100 opportunity
  score (higher interest = better)
- 🔔 **New-Loan-Only Alerts** — Telegram, Gmail,
  Discord, **and ntfy.sh** notifications fire
  only for loans with interest rate **strictly
  above 50%** (configurable). Each unique loan
  is **announced exactly once**.
- 📋 **Label-Free Line List** — Every channel
  (Telegram, Gmail, Discord, ntfy) renders the
  same compact 10-line block built once by
  `formatLoanBlock()`: rate + yield at the top,
  identity, funding, credit, borrower,
  employment, income, purpose, date, URL. No
  field labels, no `%` in the funding breakdown
  — only Indian-formatted amounts (`₹1,23,456`).
- 🔐 **Encrypted Secrets** — `.env` AND the
  Firebase service account JSON are encrypted
  with **git-crypt symmetric mode** and committed
  to the repo. CI unlocks both with
  `flydiverny/setup-git-crypt@v5` +
  `GIT_CRYPT_KEY` secret. No scattered GitHub
  Secrets, no leaked tokens in history.
- 🏛️ **SOLID Architecture** — Cleanly folderized
  into `core/`, `notifiers/`, `utils/`, `browser/`,
  and `test/` layers. The line list is built
  **once** (`formatLoanBlock`) and rendered by all
  four channels, so format changes ripple to every
  channel in one PR.
- 📁 **Historical Data** — Monthly archives of
  funded loans in Firestore, queried per-month
  from the dashboard's Archived tab
- 🆓 **100% Free** — GitHub Actions + Pages +
  Firestore Spark tier, no paid services, no
  servers

## 🚀 Live Dashboard

**[View Dashboard →](https://chirag127.github.io/i2i-yield-watch/)**

The dashboard has two views, switched by the
top-left tab bar:

- **ACTIVE** — every loan currently in the
  Firestore `loans` collection with
  `status == "active"`. Includes ALL rates, not
  just high-yield. Default view.
- **ARCHIVED** — every loan from every monthly
  archive in Firestore. Lazy-loaded on first
  switch; the manifest at `meta/archiveIndex`
  lists all available months.

Both views share the same filter / search / sort
controls and the same 50-per-page client-side
pagination. All data is fetched live from
Firestore via the Firebase JS SDK — no static
JSON, always fresh.

## 📋 How It Works

```
                   ┌─────────────────────┐
                   │   i2iFunding        │
                   │   borrower listing  │
                   │   (Angular SPA)     │
                   └──────────┬──────────┘
                              │ 1. open in Playwright
                              │    (sets cookies)
                              ▼
                   ┌─────────────────────┐
                   │ src/core/           │  ← primary
                   │  api-intercept.js   │     ~5-15s
                   │  (in-page fetch)    │     bypasses 502
                   └──────────┬──────────┘
                              │ if intercept throws…
                              ▼
                   ┌─────────────────────┐
                   │ src/browser/        │  ← fallback
                   │   scraper.js        │  (Playwright)
                   │   (DOM parse +      │
                   │    Show More)       │
                   └──────────┬──────────┘
                              │
                              ▼
                   ┌─────────────────────┐
                   │ src/core/transform  │  (raw API/DOM → normal)
                   │ formatLoanBlock()   │  (one 10-line block)
                   └──────────┬──────────┘
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
        ┌──────────┐   ┌──────────┐    ┌──────────┐
        │ src/core │   │ src/     │    │ src/core │
        │ storage  │   │ notifiers│    │ storage  │
        │ (Firestore│  │ TG/Email/│    │ Firestore│
        │  +loanId  │  │ Discord/ │    │ archive  │
        │  dedup)  │   │ ntfy     │    │ (monthly)│
        └────┬─────┘   └────┬─────┘    └────┬─────┘
             │              │              │
             ▼              ▼              ▼
      ┌───────────────┐  ┌──────────┐
      │   Firestore   │  │ Telegram │
      │  (Spark tier) │  │  / Gmail │
      │               │  │  / Discord│
      │               │  │  / ntfy  │
      └──────┬────────┘  └──────────┘
             │
             ▼
      ┌───────────────┐
      │   Dashboard   │
      │ (GitHub Pages)│
      │  reads live   │
      │  from Firestore│
      └───────────────┘
```

The primary path opens the i2iFunding borrower
listing in a real Chromium browser, then calls
the same `getActiveFilteredBorrowers` endpoint
the site's own Angular SPA calls — but from
inside the page context (`page.evaluate(fetch)`).
This bypasses the 502 block that any direct
Node.js `fetch` hits, and gives us clean JSON
with more data than the DOM (real borrower name,
`bloan_desc` purpose narrative, `nature_of_work`).
Pages 1..N are paginated in parallel batches of 3.
The legacy Playwright/DOM path is kept as a
fallback (triggered automatically on API failure,
or manually via `USE_PLAYWRIGHT_FALLBACK=true`)
so the scraper keeps working even if the
i2iFunding backend ever changes shape.

## 🏛️ Project Architecture (SOLID)

The scraper follows **SOLID principles** with a
clean folderized structure:

```
scraper/
├── src/
│   ├── core/            ← business logic
│   │   ├── api.js         ← legacy pure-HTTP fetcher (always 502s; kept for reference)
│   │   ├── api-intercept.js ← primary in-browser API fetcher (Playwright + page.evaluate)
│   │   ├── transform.js   ← normalization + formatLoanBlock
│   │   ├── storage.js     ← Firestore I/O + loanId dedup
│   │   └── index.js       ← orchestrator (main entry)
│   ├── notifiers/       ← presentation (one file per channel)
│   │   ├── telegram.js
│   │   ├── email.js
│   │   ├── discord.js
│   │   ├── ntfy.js
│   │   └── notifier.js  ← multi-channel dispatcher
│   ├── utils/           ← stateless helpers
│   │   ├── logger.js
│   │   └── scorer.js
│   └── browser/         ← Playwright DOM fallback (last resort)
│       ├── scraper.js
│       ├── parser.js
│       └── showmore.js
├── test/                ← smoke + syntax tests
│   ├── smoketest.js     ← offline smoke tests
│   ├── verify_syntax.js
│   ├── verify-intercept.js   ← real i2iFunding intercept smoke test
│   ├── verify-real-telegram.js ← real end-to-end (intercept → Telegram)
│   └── send_test_telegram.js  ← synthetic Telegram test
├── package.json
└── node_modules/        ← gitignored
```

Key design choices:

- **Single source of truth for messages** — the
  same `formatLoanBlock(loan)` function in
  `src/core/transform.js` builds the 10-line block
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
  is a single Firestore collection
  (`/notifications/{loanId}`) with the `loanId` as
  the doc ID.

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
  `pl_bloan_id` the API returns). A loan is
  sent only on the first run where its `loanId`
  is unseen; subsequent runs skip it. The dedup
  store lives in Firestore at
  `/notifications/{loanId}` and is written
  atomically with each send.
  *(i2iFunding's own borrower FAQ states "one
  borrower can apply for only one loan at a
  time", so a single `loanId` is sufficient to
  uniquely identify a live listing.)*
- **Label-free line list** — every channel
  renders the same 10 lines in the same order
  (see the "Notification Message Format"
  section above). Missing data drops the whole
  line silently — no `"N/A"`, no empty rows.
  **No percent symbols in the funding breakdown**
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
| `GIT_CRYPT_KEY`         | If git-crypt   | Base64-encoded git-crypt symmetric key (unlocks `.env` + `i2i-yield-watch-sa.json`) |
| `FIREBASE_SA_JSON`      | If no git-crypt | Full service account JSON as a string (alternative to committed + encrypted file) |
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

## 🔐 git-crypt Setup (Recommended)

By default the repo uses GitHub Secrets for
credentials. For a more ergonomic workflow,
the repo supports **git-crypt symmetric mode**:
the real `.env` AND the Firebase service account
JSON (`i2i-yield-watch-sa.json`) are encrypted with
a single key file and committed to the repo. CI
unlocks both via `flydiverny/setup-git-crypt@v5`.

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

# 3. (Already done in this repo) The
#    .gitattributes file already encrypts both
#    .env and i2i-yield-watch-sa.json:
#      .env filter=git-crypt diff=git-crypt
#      i2i-yield-watch-sa.json filter=git-crypt diff=git-crypt

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

- name: Decrypt git-crypt files
  env:
    GIT_CRYPT_KEY: ${{ secrets.GIT_CRYPT_KEY }}
  run: |
    echo "$GIT_CRYPT_KEY" | base64 -d > git-crypt-key
    git-crypt unlock git-crypt-key
```

When `GIT_CRYPT_KEY` is set, CI unlocks the repo
and the scraper reads `.env` (channel creds) and
`i2i-yield-watch-sa.json` (Firebase admin SDK) from
the working tree. When `GIT_CRYPT_KEY` is **not**
set, the unlock step is a no-op and you must
fall back to GitHub Secrets for both. The two
methods are fully interchangeable — pick one, or
use both as backup.

## 🗄️ Database (Cloud Firestore)

All state lives in **Cloud Firestore** on the free
**Spark** tier — no JSON files in the repo, no
static data. The service account JSON is committed
to the repo encrypted via git-crypt; CI decrypts it
on every run.

### Collections

```
firestore/
├── loans/{loanId}             ← Single doc per loan
│   ├── loanId                 (string, doc ID)
│   ├── interestRate           (number, %)
│   ├── yieldScore             (number, 0–100)
│   ├── priority               ("VERY_HIGH"|"MEDIUM"|"LOW")
│   ├── status                 ("active"|"archived")
│   ├── yearMonth              (string "YYYY-MM", null when active)
│   ├── name, age, location    (borrower fields)
│   ├── creditScore            (string|"No History")
│   ├── loanAmount             (number)
│   ├── amountFunded           (number, derived)
│   ├── amountLeft             (number)
│   ├── tenure, product        (string)
│   ├── ... ~25 fields total
│   └── updatedAt              (serverTimestamp)
│
├── notifications/{loanId}     ← Dedup store
│   ├── loanId                 (doc ID)
│   └── notifiedAt             (serverTimestamp)
│
├── runs/{runId}               ← Scrape run history
│   ├── runId                  (string, doc ID)
│   ├── timestamp              (serverTimestamp)
│   ├── active, new, archived  (counts)
│   ├── qualifying, notified   (counts)
│   └── duration_ms, phases    (metrics)
│
├── stats/current              ← Aggregate stats (singleton)
│   ├── lastUpdated            (serverTimestamp)
│   ├── currentActive          (number)
│   ├── totalArchived          (number)
│   ├── avgInterestRate        (number)
│   ├── avgYieldScore          (number)
│   ├── highPriorityCount      (number)
│   ├── totalScrapedAllTime    (number)
│   ├── byProduct              (map: product → count)
│   └── byPriority             (map: priority → count)
│
└── meta/
    ├── archiveIndex           ← { "2026-05": 224, "2026-06": 210 }
    └── scraper                ← { lastRunId, lastRunAt }
```

### Security rules

Public read-only access (the dashboard reads
client-side); no public write. The scraper uses a
service account (admin SDK) that bypasses rules.

```javascript
// firestore.rules
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /loans/{loanId} { allow read: if true; allow write: if false; }
    match /notifications/{id} { allow read: if false; allow write: if false; }
    match /runs/{runId} { allow read: if true; allow write: if false; }
    match /stats/{document=**} { allow read: if true; allow write: if false; }
    match /meta/{document=**} { allow read: if true; allow write: if false; }
  }
}
```

### Free tier limits (Spark)

- **1 GB** storage per project
- **50K reads / day** (dashboard + API quota)
- **20K writes / day** (scraper writes)
- **Unlimited** number of projects (one per repo
  fork is fine)

### Local development

For local dev, place the service account JSON at
the project root as `i2i-yield-watch-sa.json` (it
will be encrypted by git-crypt on commit). The
scraper reads it via `FIREBASE_SA_PATH` (or the
default `./i2i-yield-watch-sa.json`). Set
`FIREBASE_SA_PATH` in `.env` to override.

### One-time data migration

If you fork this repo and want to seed your own
Firestore with historical data, see
`scraper/test/migrate_to_firestore.js` — it's a
one-time script that reads from JSON files and
bulk-writes to Firestore in 500-doc batches.

## ⚙️ Configuration

All configuration is via environment variables
(set in `.env` or GitHub Secrets):

| Variable                       | Default | Description |
|--------------------------------|---------|-------------|
| `NOTIFY_RATE_THRESHOLD`        | `50`    | Strictly-greater rate gate for **notifications** (each unique loan is announced once) |
| `HIGH_PRIORITY_RATE_THRESHOLD` | `70`    | Min rate for VERY_HIGH priority |
| `MEDIUM_PRIORITY_RATE_THRESHOLD` | `50`  | Min rate for MEDIUM priority |
| `MAX_SHOW_MORE_CLICKS`         | `150`   | Safety limit for Playwright Show More clicks (fallback only) |
| `USE_PLAYWRIGHT_FALLBACK`      | `false` | Force the legacy Playwright/DOM path instead of the in-browser API intercept |
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

# 4. Install Playwright browser
# The primary in-browser API path requires
# Chromium. The DOM fallback also needs it.
npx playwright install chromium

# 5. Run the scraper (default: in-browser API path)
node src/core/index.js
# Or force the legacy DOM/Playwright path:
USE_PLAYWRIGHT_FALLBACK=true node src/core/index.js

# 6. (Optional) Real end-to-end smoke tests
node test/smoketest.js
node test/verify_syntax.js
node test/verify-intercept.js   # real i2iFunding intercept
node test/verify-real-telegram.js  # real Telegram send
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
| Scraper (primary)      | Node.js + Playwright + in-page `fetch()` |
| Scraper (fallback)     | Node.js + Playwright + DOM parse |
| Scheduler              | GitHub Actions (cron) |
| Database               | Cloud Firestore (Spark tier) |
| Secrets                | git-crypt symmetric OR GitHub Secrets |
| Dashboard              | HTML + CSS + Vanilla JS + Firebase JS SDK |
| Charts                 | Chart.js 4.5.1 |
| Hosting                | GitHub Pages |
| Notifications          | Telegram + Gmail + Discord + ntfy.sh |

## 🛠️ Skills & Tools Used

- **playwright** — powers both the primary
  in-browser API path (`scraper/src/core/api-intercept.js`)
  and the legacy DOM fallback
  (`scraper/src/browser/`)
- **webapp-testing** — used during initial
  development to verify the live i2iFunding page
  structure and selectors

## ⚡ Speed Optimizations

The default workflow run is **~30 seconds** (cold)
or **~15 seconds** (warm cache). The biggest
savings come from caching the Playwright Chromium
install, the parallel in-page pagination, and
Firestore batched writes.

| Optimization                            | Saves         | Where |
|-----------------------------------------|---------------|-------|
| `actions/cache@v4` for `node_modules`   | ~10-20s on warm runs | keyed on `scraper/package-lock.json` |
| `actions/cache` for Playwright Chromium | ~60s on warm runs | (warm cache → ~5s install) |
| Shallow checkout (`fetch-depth: 1`)     | ~5-10s        | First `actions/checkout` step |
| `npm ci --prefer-offline --no-audit`    | ~2-5s         | Cached tarball resolution, skips audit metadata fetch |
| In-page API fetch (vs. DOM parse)       | ~25-50s/run   | `scraper/src/core/api-intercept.js` — single JSON, no selectors |
| Startup jitter (0–2s)                   | spreads load  | `STARTUP_JITTER_MS` (default 2000) in `main()` |
| Parallel in-page API page fetch (3/batch) | ~2-5s on large catalogs | `scraper/src/core/api-intercept.js` |
| Parallel notification channels          | ~1–3s         | `scraper/src/notifiers/notifier.js` |
| Firestore batched writes (500/batch)    | ~1–3s on large catalogs | `scraper/src/core/storage.js` |
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
  — chunker, N/A omission, label-free line list,
  ntfy body shape, ntfy disabled, ntfy missing
  topic, no promo copy
- **Loan ID & dedup** — `loanId` from `pl_bloan_id`,
  `filterUnnotified`, idempotent `markNotificationsSent`,
  no SHA-1 fingerprinting
- **API payload (no network)** — `buildFilterBody`
  JSON, `fetchPage` POST + headers, constants
- **Transform** — 14 helpers + 10-line block + silent
  optional omission
- **Workflow & layout** — cron = 5-min IST, dispatch,
  no-cancel, 10-min, cached, unconditional Playwright,
  git-crypt, SOLID folder structure, single README,
  encrypted `.env`, Firestore storage

Run them locally before pushing. CI runs `npm test`
before every scrape on `main`, scheduled ticks, and
manual dispatch.

## 📄 License

MIT

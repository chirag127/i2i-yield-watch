# i2i-yield-watch 🔍

> **Automated high-yield loan intelligence platform
> for i2iFunding.** Scrapes public borrower listings
> every 15 minutes, scores loans by yield potential,
> and sends instant multi-channel notifications for
> new opportunities.

[![Auto Scraper](https://github.com/chirag127/i2i-yield-watch/actions/workflows/scrape.yml/badge.svg)](https://github.com/chirag127/i2i-yield-watch/actions/workflows/scrape.yml)

## ✨ Features

- 🤖 **Fully Automated** — Scrapes i2iFunding
  every 15 minutes via GitHub Actions
- 📊 **Live Dashboard** — Premium dark-mode
  dashboard with filters, charts, and search
- 🔥 **Yield Scoring** — Custom 0–100 opportunity
  score (higher interest = better)
- 🔔 **Multi-Channel Alerts** — Telegram, Email
  (Gmail), and Discord notifications
- 📁 **Historical Data** — Monthly archives of
  funded loans
- 🆓 **100% Free** — GitHub Actions + Pages, no
  paid services, no servers

## 🚀 Live Dashboard

**[View Dashboard →](https://chirag127.github.io/i2i-yield-watch/)**

## 📋 How It Works

```
┌──────────────┐     ┌──────────────┐
│  i2iFunding  │────▶│   Scraper    │
│  (public)    │     │  (Playwright)│
└──────────────┘     └──────┬───────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        ┌──────────┐ ┌──────────┐ ┌────────────┐
        │   JSON   │ │  Notify  │ │  Archive   │
        │  Storage │ │  (TG/    │ │  (Monthly) │
        │          │ │  Email/  │ │            │
        │          │ │  Discord)│ │            │
        └────┬─────┘ └──────────┘ └────────────┘
             │
             ▼
     ┌───────────────┐
     │   Dashboard   │
     │  (GitHub      │
     │   Pages)      │
     └───────────────┘
```

## 📊 Understanding the Yield Score

The **Yield Score** (0–100) is an **opportunity score**, not a risk score. It intentionally favors high-interest rate loans:

| Factor | Weight | Bounds (Min–Max) | Logic |
|---|---|---|---|
| Interest Rate | **55%** | 0% – 200% (No limit) | Higher = Better |
| Credit Score | **30%** | 300 – 900 | Higher = Better (Null / No History treated neutrally) |
| Monthly Income | **5%** | ₹0 – ₹20,00,000 | Higher = Better |
| Funding Remaining | **5%** | 0% – 100% | More left = Better |
| Loan Amount | **5%** | ₹0 – ₹50,00,000 | Larger = Better |

**Credit Score Neutralization**:
- If a borrower has **No History** or no credit score grade is available, they are **not penalized** (they receive a neutral `0.5` score component value).

**Priority Levels:**
- 🔥 **VERY HIGH** — Interest rate ≥ 70% p.a.
- 🟡 **MEDIUM** — Interest rate 50–69% p.a.
- ⚪ **LOW** — Interest rate < 50% p.a.

> **Important:** Category X risk grade is treated neutrally. High interest rates are never penalized — they are the primary yield signal.

## 🛠️ Setup Guide (Step by Step)

### Prerequisites

- GitHub account (free)
- For Telegram: Telegram account
- For Email: Gmail account with 2FA enabled
- For Discord: Discord server with webhook access

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

### Step 5 — Add GitHub Secrets

Go to: **Repository** → **Settings** →
**Secrets and Variables** → **Actions** →
**New repository secret**

| Secret Name | Required | Description |
|---|---|---|
| `TELEGRAM_ENABLED` | No | `"true"` or `"false"` |
| `TELEGRAM_BOT_TOKEN` | If Telegram | From @BotFather |
| `TELEGRAM_CHAT_ID` | If Telegram | Your chat ID |
| `EMAIL_ENABLED` | No | `"true"` or `"false"` |
| `EMAIL_FROM` | If Email | Gmail address |
| `EMAIL_APP_PASSWORD` | If Email | 16-char App Password |
| `EMAIL_TO` | If Email | Recipient email |
| `DISCORD_ENABLED` | No | `"true"` or `"false"` |
| `DISCORD_WEBHOOK_URL` | If Discord | Webhook URL |
| `DASHBOARD_URL` | Recommended | GitHub Pages URL |

### Step 6 — Enable GitHub Pages

1. Repository → **Settings** → **Pages**
2. Source: **GitHub Actions**
3. After first workflow run, your dashboard will
   be live at:
   `https://YOUR_USERNAME.github.io/i2i-yield-watch/`

### Step 7 — Enable GitHub Actions

1. Repository → **Actions** tab
2. Click **"Enable Actions"** if prompted
3. Run manually: **Actions** →
   **"i2i Yield Watch — Auto Scraper"** →
   **Run workflow**
4. After confirming it works, it auto-runs
   every 15 minutes

### Step 8 — Update Dashboard URL Secret

After your first deployment, update the
`DASHBOARD_URL` secret with your actual
GitHub Pages URL.

## 📁 Data Structure

```
data/
├── active_loans.json        ← Current active loans
├── notifications_sent.json  ← Notified loan IDs
├── stats.json               ← Aggregate statistics
├── changelog.json           ← Scrape run history
└── archive/
    └── fully_funded_YYYY_MM.json  ← Monthly archive
```

- **active_loans.json** — Single source of truth
  for the dashboard. Updated every scrape run.
- **archive/** — Fully funded or disappeared loans
  are moved here monthly.
- **changelog.json** — Last 200 scrape runs logged.

## ⚙️ Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|---|---|---|
| `MAX_SHOW_MORE_CLICKS` | `150` | Safety limit for Show More clicks |
| `HIGH_PRIORITY_RATE_THRESHOLD` | `70` | Min rate for VERY_HIGH priority |
| `MEDIUM_PRIORITY_RATE_THRESHOLD` | `50` | Min rate for MEDIUM priority |
| `DASHBOARD_URL` | — | Your GitHub Pages URL |

## 🔧 Running Locally

```bash
# 1. Clone the repo
git clone https://github.com/chirag127/i2i-yield-watch.git
cd i2i-yield-watch

# 2. Copy environment template
cp .env.example .env
# Edit .env with your credentials

# 3. Install scraper dependencies
cd scraper
npm install

# 4. Install Playwright browser
npx playwright install chromium

# 5. Run the scraper
node index.js
```

To view the dashboard locally, serve the project
root with any static file server:

```bash
# From project root
npx serve .
# Then open http://localhost:3000/dashboard/
```

## ❓ Troubleshooting

### GitHub Actions not running
- Check that Actions are enabled in repo settings
- Verify workflow file is at
  `.github/workflows/scrape.yml`
- Ensure `permissions: contents: write` is set

### Dashboard not deploying
- Set Pages source to "GitHub Actions" in Settings
- Check that `actions/deploy-pages@v4` has proper
  permissions (`pages: write`, `id-token: write`)

### No notifications received
- Verify the `*_ENABLED` secret is exactly `"true"`
- Check GitHub Actions logs for error messages
- For Telegram: ensure you started the bot
  with `/start`
- For Email: ensure 2FA is on and you're using an
  App Password (not your account password)

### Scraper fails with timeout
- The site may be temporarily down
- GitHub Actions will retry on next schedule
- Check if the site structure has changed

### Rate limiting concerns
- 15-minute intervals are safe for a single page
- Startup jitter (0–30s) prevents exact timing
- User agent rotation provides basic stealth

## 🏗️ Tech Stack

| Component | Technology |
|---|---|
| Scraper | Node.js + Playwright |
| Scheduler | GitHub Actions (cron) |
| Storage | JSON files in repo |
| Dashboard | HTML + CSS + Vanilla JS |
| Charts | Chart.js 4.5.1 |
| Hosting | GitHub Pages |
| Notifications | Telegram + Gmail + Discord |

## 📄 License

MIT

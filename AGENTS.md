# i2i-yield-watch — agent guide

i2iFunding high-yield loan watch.

> Self-contained rules. Source of truth: chirag127/workspace/knowledge/. Manual sync.

<!-- CANONICAL-RULES v1 (manual sync — source of truth: chirag127/workspace/knowledge/) -->
<!--
  This block is copied verbatim into every active repo's AGENTS.md so rules
  enforce even when the repo is cloned/opened standalone (outside the workspace
  umbrella). MANUAL SYNC: when a rule changes, edit the source in
  workspace/knowledge/ AND hand-update this block in each repo. The v1 marker
  makes stale copies greppable. Full rule text lives in workspace/knowledge/.
-->

## Fleet rules (canonical — apply on every task)

### Prose + output
- **Caveman/terse.** Drop articles, filler, pleasantries, hedging. Fragments > sentences. Answer in word 1 — no preamble, no restatement. Code/data BEFORE prose. Explanation ≤3 lines trivial, ≤10 complex. Concrete not abstract (file:line, exact command, next action). Same terseness for commit messages, PR/issue bodies, code comments. Full sentences ONLY for irreversible-action confirmations (`rm -rf`, force-push, `DROP TABLE`, prod deploy).
- **Terse GitHub issues.** Bug ≤150 words, feature ≤100, comment ≤50. Use repo's template. No speculation/unverified versions/API names. Shorter = fewer hallucinations.

### Code
- **Minimum everything.** Smallest unit that works. LOC/tool-calls/files/imports = what the task needs, not one more. Zero comments unless the line is non-obvious. Trivial fix ≤3 tool calls, routine ≤10, multi-step ≤30 (else delegate).
- **The ladder** (stop at first rung): does it need to exist? → native platform/OS/browser? → already in codebase (reuse)? → stdlib? → one line? → only then minimal own code. Trace the problem end-to-end before coding.
- **No speculative scaffolding, no defensive code for impossible cases, no premature optimization.** `// shouldn't happen` → delete the code. **Edit > Write** (Write only for new files / full replacement). Reuse existing patterns/style even if suboptimal. Don't re-read unchanged files.
- **MAXIMIZE community packages, MINIMIZE own code.** Reach for a well-kept package before writing logic; every line not written is a line not maintained. Own code only where no package fits. Shared own-code = the atomic `@chirag127/*` set — reuse mechanism, theme each site's OWN look.
- **Build COMPLETE, not MVP.** Full feature set, latest dep versions (beta/alpha ok when newest), unit + integration tests everywhere. Ship same session.

### Code intelligence — codebase-memory-mcp FIRST
- On ANY code question use a **cbm** tool BEFORE Grep/Glob/Read: `search_graph` (find symbol), `trace_path` (callers/callees/blast-radius), `get_code_snippet` (exact source), `get_architecture` (overview), `query_graph` (openCypher), `search_code` (grep over indexed), `detect_changes` (diff impact). If the repo isn't indexed → `index_repository` first. Grep/Read only for non-code files or a file you're about to edit. **Use cbm VERY frequently** — 120× fewer tokens than grep/read; many calls per task is good.

### Git
- **main only.** Direct commit on own repos (`chirag127/*`), push by default, never force-push main. Conventional commits (they ARE the changelog). Branches only for upstream PRs. Identity = chirag127 noreply. Scan for secrets before push (no hardcoded secrets; sops+age vault).

### Web + facts
- **Search the web ≥2× before any non-trivial decision** on tools/pricing/library-status/URLs (two phrasings, cross-check). No memory-only answers on externally-knowable, mutable facts.

### Product + security posture
- **No auth on FREE surfaces** — free features 100% public; auth ONLY gates paid goods. Clerk = shared `*.oriz.in` SSO; `PUBLIC_CLERK_PUBLISHABLE_KEY` client-side, secret key server/deploy only, never `PUBLIC_*_SECRET`.
- **No card-on-file for own tooling** (donations via BMC/GH-Sponsors/UPI); customers may pay any method. Never hit free-tier quotas.
- **Every site its OWN distinct visual identity** — reuse `@chirag127/*` for mechanism/a11y/token-contract; never reuse another site's palette/type/layout/motion/signature. Run the frontend-design process per site.

### Interaction (STT-friendly)
- User uses speech-to-text: infer intent from typos/homophones, pick the most-likely reading, STATE it, proceed. Don't ask the user to re-type. Ask only when truly blocked.

<!-- /CANONICAL-RULES v1 -->

## Project-specific (this repo)

Python scraper repo (git-as-DB, GitHub Actions cron).
- **Ranking policy (shared by notify sort + auto-invest select)** — order of importance: **1) RETURN (interest rate)**, **2) borrower CREDIT SCORE**, **3) loan TENURE** (longer = better; locks the high rate in longer), then income/amount as minor tie-breaks. A borrower with **NO credit score is ranked AS IF the score were 750** (`config.NO_CREDIT_IMPUTED_SCORE` — neither favored nor buried) but is **flagged "no credit score" in the notification**. `scorer.py` owns the weighted score + `sort_loans` (notify path); `invest.select` owns the auto-invest candidate ranking — both apply this same policy. Tenure parsing = `util.tenure_months` ("6 Months"/"1 Year"/int → months).
- **Verify website structure BEFORE writing/updating scrapers** — FETCH the real page (httpx for server-rendered, Playwright for JS-rendered) and inspect the actual DOM. NEVER guess selectors, column names, or URLs. Confirm the URL resolves first.
- **git-as-DB**: scraped state lives in `data/*.json` committed back to the repo each run. The cron workflow MUST `git add data && git commit && git pull --rebase --autostash origin main && git push` — without committing state back, dedup breaks and notifications repeat.
- **Notifications**: single Telegram bot `oriz127_bot`; token = `TELEGRAM_BOT_TOKEN`, chat = `TELEGRAM_CHAT_ID` (GitHub secrets, sourced from the workspace vault). Notify on NEW items only — never re-send an already-notified item.
- **cron reliability**: `concurrency` queue (cancel-in-progress: false), rebase-before-push guard, and an `if: failure()` step that pings Telegram. GitHub schedule cron is best-effort (late is fine; the queue + next run handle it).
- **Tests**: pytest. On this Windows box the working interpreter is the `py` launcher: `py -m pytest -q`.
- Playwright on Windows needs `args=["--no-sandbox","--disable-gpu","--disable-dev-shm-usage"]`.

### Related repos + the PII boundary (read before moving any data)
- **`chirag127/i2i-portfolio-data` (PRIVATE)** — the sibling repo holding the personal i2iFunding **account data**: 151 borrowers' names + per-loan amounts, investor ID, escrow a/c no, PAN, CIBIL report, agreements, EMI/return analysis. Its README states this **must stay private**.
- **THIS repo (`i2i-yield-watch`) is PUBLIC** (live GitHub Pages dashboard). Therefore: **NEVER commit or move borrower PII / PAN / CIBIL / escrow / account data into this repo.** Direct such data to the private `i2i-portfolio-data` only. This repo holds tooling + anonymised aggregate stats (avg rate, counts) — no personal data.
- Split of concerns: `i2i-yield-watch` = automation (scrape, score, notify, invest/cancel) + public dashboard; `i2i-portfolio-data` = the operator's own private account records + analysis. Keep them separate; do not merge PII across the public/private boundary.

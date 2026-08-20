#!/usr/bin/env bash
# Fire a `repository_dispatch` tick at chirag127/i2i-yield-watch.
#
# Used by an external cron pinger (cron-job.org / healthchecks.io / GitHub
# itself) to get TRUE sub-15-min polling, because GitHub's free-tier cron is
# best-effort (ticks can be late or skipped under load).
#
# Setup:
#   1. Create a fine-grained PAT with Actions:write on this repo.
#   2. Store it as the I2I_DISPATCH_TOKEN secret (or pass via env here).
#   3. Point your cron pinger at this script (or run the curl directly).
#
# Idempotent: each tick fires one scrape run; the scraper's self-loop + per-run
# dedup make a late or duplicate ping harmless.
set -euo pipefail

TOKEN="${I2I_DISPATCH_TOKEN:?set I2I_DISPATCH_TOKEN (fine-grained PAT, Actions:write)}"
REPO="${I2I_DISPATCH_REPO:-chirag127/i2i-yield-watch}"

curl -sS -X POST \
  "https://api.github.com/repos/${REPO}/dispatches" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -d '{"event_type":"tick"}'

echo "tick dispatched to ${REPO}"

"""Static guard against workflow env overrides drifting from code policy."""

from pathlib import Path

from i2i_watch import config as C


ROOT = Path(__file__).parents[1]


def _text(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_scraper_notification_overrides_match_policy():
    workflow = _text("scrape.yml")
    # Continuous runner: --iterations 0 = poll forever, self-handoff before
    # the 340-min timeout. No more 8x30s burst.
    assert "--iterations 0" in workflow
    assert "--interval ${{ vars.POLL_INTERVAL_S || '30' }}" in workflow
    assert "timeout-minutes: 340" in workflow
    assert "SCRAPE_HANDOFF_MIN" in workflow
    assert "SCRAPE_STATE_PUSH_S" in workflow
    assert "gh workflow run scrape.yml" in workflow  # self-handoff
    # Deploy is now in a separate workflow file (deploy.yml).
    assert "schedule:" not in workflow
    assert "NOTIFY_MIN_RATE_PCT: ${{ vars.NOTIFY_MIN_RATE_PCT || '40' }}" in workflow
    assert "NOTIFY_BUCKET_MIN_RATE_PCT: ${{ vars.NOTIFY_BUCKET_MIN_RATE_PCT || '0' }}" in workflow
    assert "NOTIFY_HIGH_RATE_PCT: ${{ vars.NOTIFY_HIGH_RATE_PCT || '100' }}" in workflow
    assert "I2I_DIGEST_HOURS: ${{ vars.I2I_DIGEST_HOURS || '6' }}" in workflow
    assert "STARTUP_JITTER_MS: ${{ vars.STARTUP_JITTER_MS || '2000' }}" in workflow


def test_investment_overrides_match_policy():
    workflow = _text("invest.yml")
    assert workflow.count("|| '100'") >= 3
    assert "I2I_NEERU_AUTOINVEST_MIN_RATE_PCT" in workflow
    assert "AUTOINVEST_MIN_CREDIT_SCORE" not in workflow
    assert C.AUTOINVEST_MIN_CREDIT_SCORE == 500.0


def test_digest_cannot_lower_the_real_money_gate():
    workflow = _text("digest.yml")
    assert "AUTOINVEST_MIN_RATE_PCT: ${{ vars.AUTOINVEST_MIN_RATE_PCT || '100' }}" in workflow
    assert "AUTOINVEST_MIN_CREDIT_SCORE" not in workflow


def test_telegram_bot_can_dispatch_and_tracks_offset():
    """The bot must be able to re-trigger workflows (actions:write) and must
    persist its getUpdates offset so a crashed run never re-dispatches."""
    workflow = _text("telegram-bot.yml")
    assert "actions: write" in workflow          # GITHUB_TOKEN can dispatch
    assert "GITHUB_TOKEN: ${{ github.token }}" in workflow
    assert "telegram-bot-state.json" in workflow  # offset dedup (git-as-DB)
    assert "python3 -m i2i_watch.bot" in workflow


def test_telegram_bot_is_continuously_alive_with_tick_pinger():
    """The bot must not rely on cron windows: it runs continuously inside one
    long-lived job, self-hands-off before its timeout, and a tick pinger
    restarts it if it dies."""
    bot_wf = _text("telegram-bot.yml")
    assert "timeout-minutes: 350" in bot_wf          # long-lived job
    assert "--iterations 0" in bot_wf                # continuous mode
    assert "gh workflow run telegram-bot.yml" in bot_wf  # self-handoff
    assert "BOT_HANDOFF_MIN" in bot_wf
    assert "BOT_STATE_PUSH_S" in bot_wf              # periodic offset pushes
    assert "schedule:" not in bot_wf                 # no own cron (pinger owns it)

    tick = _text("tick.yml")
    assert "gh workflow run telegram-bot.yml" in tick   # pinger dispatches bot
    assert 'select(.status != "completed")' in tick     # only if not alive
    assert "actions: write" in tick
    assert "schedule:" in tick


def test_scraper_is_continuously_alive_with_tick_pinger():
    """The scraper must not rely on cron windows: it runs continuously inside
    one long-lived job, self-hands-off before its timeout, and the tick
    pinger restarts it if it dies."""
    scrape_wf = _text("scrape.yml")
    assert "timeout-minutes: 340" in scrape_wf          # long-lived job
    assert "--iterations 0" in scrape_wf                 # continuous mode
    assert "gh workflow run scrape.yml" in scrape_wf     # self-handoff
    assert "SCRAPE_HANDOFF_MIN" in scrape_wf
    assert "SCRAPE_STATE_PUSH_S" in scrape_wf           # periodic state pushes
    # A scraper crash must not be masked as rc=0: the failure alert only fires
    # if the job fails, so the true exit code must survive the `wait`.
    assert 'if wait "$SCRAPE_PID" 2>/dev/null; then' in scrape_wf
    assert "SCRAPE_RC=$?" in scrape_wf

    tick = _text("tick.yml")
    assert "gh workflow run scrape.yml" in tick          # pinger dispatches scraper
    assert 'select(.status != "completed")' in tick      # only if not alive


def test_deploy_is_separate_workflow():
    """The Pages deploy must be in its own workflow file so the scraper's
    340-min concurrency group can never block dashboard deploys."""
    deploy_wf = _text("deploy.yml")
    assert "cron: '*/5 * * * *'" in deploy_wf
    assert "group: scraper-deploy" in deploy_wf
    assert "cancel-in-progress: true" in deploy_wf
    assert "actions/deploy-pages" in deploy_wf
    # scrape.yml must NOT contain a deploy job anymore.
    scrape_wf = _text("scrape.yml")
    assert "deploy:" not in scrape_wf
    assert "schedule:" not in scrape_wf


def test_every_workflow_alert_is_self_identifying():
    """Failure alerts must carry run start time + failing step so a stale
    alert (old failed run) can never look like a live one."""
    for name in ("scrape.yml", "invest.yml", "digest.yml",
                 "emi-report.yml", "wallet-check.yml", "telegram-bot.yml",
                 "tick.yml"):
        workflow = _text(name)
        assert "Alert on failure" in workflow, f"{name}: missing failure alert"
        assert "gh api \"repos/${GITHUB_REPOSITORY}/actions/runs" in workflow, \
            f"{name}: alert missing run-start timestamp"
        assert "gh run view" in workflow, f"{name}: alert missing failing-step lookup"


def test_supervisor_is_event_driven_with_breaker_and_no_self_loop():
    """The self-supervisor must hook workflow_run (cron is coalesced to ~3.5h),
    be able to re-dispatch dead runners, and never trigger on itself."""
    wf = _text("supervisor.yml")
    assert "workflow_run:" in wf
    assert '"i2i Yield Watch — Auto Scraper"' in wf
    assert '"i2i Telegram Command Bot"' in wf
    assert "types: [completed]" in wf
    assert "actions: write" in wf               # dispatch scrape/bot
    assert "gh workflow run" in wf
    assert 'select(.status != "completed")' in wf  # restart only when truly dead
    assert "circuit breaker" in wf.lower()       # bounded recovery attempts
    assert '- "i2i Self-Supervisor"' not in wf   # no self-loop


def test_alert_drill_is_read_only():
    """The drill must be manual-only, credential-free for i2i, and structurally
    unable to trigger the real-money invest workflow."""
    wf = _text("alert-drill.yml")
    assert "workflow_dispatch:" in wf            # manual only, never scheduled
    assert "schedule:" not in wf
    assert "contents: read" in wf                # cannot dispatch other workflows
    assert "actions: write" not in wf
    assert "i2i_watch drill-alert" in wf
    assert "I2I_EMAIL" not in wf                 # zero i2i credentials on the job
    assert "I2I_TXN_PIN" not in wf
    assert "i2i_watch invest" not in wf          # never runs the money path

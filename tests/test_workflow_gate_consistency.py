"""Static guard against workflow env overrides drifting from code policy."""

from pathlib import Path

from i2i_watch import config as C


ROOT = Path(__file__).parents[1]


def _text(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_scraper_notification_overrides_match_policy():
    workflow = _text("scrape.yml")
    assert "POLL_INTERVAL_S: '60'" in workflow
    assert "POLL_ITERATIONS: '25'" in workflow
    assert "--iterations ${{ vars.POLL_ITERATIONS || '25' }} --interval ${{ vars.POLL_INTERVAL_S || '60' }}" in workflow
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
    assert C.AUTOINVEST_MIN_CREDIT_SCORE == 700.0


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

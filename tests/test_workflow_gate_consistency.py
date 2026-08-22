"""Static guard against workflow env overrides drifting from code policy."""

from pathlib import Path

from i2i_watch import config as C


ROOT = Path(__file__).parents[1]


def _text(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_scraper_notification_overrides_match_policy():
    workflow = _text("scrape.yml")
    assert "NOTIFY_MIN_RATE_PCT: '40'" in workflow
    assert "NOTIFY_HIGH_RATE_PCT: '100'" in workflow
    assert "STARTUP_JITTER_MS: '2000'" in workflow


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

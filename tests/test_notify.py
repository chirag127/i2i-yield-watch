"""Notifier: Telegram HTML block format + no-op-without-env behavior."""

import i2i_watch.notify.channels as ch

LOAN = {
    "loanId": "500123",
    "borrowerRef": "88001",
    "interestRate": 88.5,
    "yieldScore": 61.2,
    "loanAmount": 50000,
    "amountLeft": 20000,
    "creditScore": "742",
    "creditScoreNumeric": 742,
    "riskCategory": "B",
    "name": "Ravi Kumar",
    "loanUrl": "https://www.i2ifunding.com/borrower/listing/public-profile/88001/500123",
}


def test_telegram_block_first_line_bold_and_clickable():
    html = ch.format_loan_line(LOAN)
    first = html.splitlines()[0]
    # first line is an <a href=...><b>...</b></a> wrapping the rate line
    assert first.startswith('<a href="')
    assert "<b>" in first and "</b></a>" in first
    assert LOAN["loanUrl"] in first
    assert "88.50% p.a." in first


def test_telegram_block_has_no_field_labels():
    html = ch.format_loan_line(LOAN)
    # label-free: no "Rate:" / "Amount:" style prefixes
    assert "Rate:" not in html
    assert "Amount:" not in html


def test_telegram_url_not_repeated_in_body():
    html = ch.format_loan_line(LOAN)
    # url appears once (in the href), not again as a trailing plain line
    assert html.count(LOAN["loanUrl"]) == 1


def test_telegram_escapes_html_entities():
    loan = {**LOAN, "name": "A & <B>", "location": "X <Y>"}
    html = ch.format_loan_line(loan)
    assert "&amp;" in html and "&lt;" in html and "&gt;" in html


def test_telegram_noop_without_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert ch.send_telegram([LOAN], {}, "https://d/", 40) is False


def test_ntfy_noop_without_env(monkeypatch):
    monkeypatch.delenv("NTFY_ENABLED", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert ch.send_ntfy([LOAN], {}, "", 40) is False


def test_notify_all_all_channels_off_by_default(monkeypatch):
    for k in ("TELEGRAM_ENABLED", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
              "NTFY_ENABLED", "NTFY_TOPIC"):
        monkeypatch.delenv(k, raising=False)
    results = ch.notify_all([LOAN], {}, "https://d/", 40)
    assert results == {"telegram": False, "ntfy": False}
    assert ch.was_any_channel_successful(results) is False

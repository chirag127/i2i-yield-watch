"""Notifier: Telegram HTML block format + no-op-without-env behavior."""

import i2i_watch.notify.channels as ch


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"ok": True}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

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
    "product": "Regular Loans",
    "name": "Ravi Kumar",
    "location": "Delhi",
    "employmentType": "Salaried",
    "monthlyIncome": 50000,
    "tenure": "12 Months",
    "purpose": "Business",
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


def test_telegram_block_contains_all_available_loan_details():
    html = ch.format_loan_line(LOAN)
    for value in (
        "Yield 61.20/100", "i2i-#88001", "Loan 500123", "₹50,000",
        "₹20,000 left", "Regular Loans", "Credit 742", "Risk B",
        "Ravi Kumar", "Delhi", "Salaried", "₹50,000/mo", "12 Months",
        "Business",
    ):
        assert value in html


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


def test_enabled_gate_is_lenient(monkeypatch):
    for val in ("true", "TRUE", "True", "1", "yes", "on", " true "):
        monkeypatch.setenv("TELEGRAM_ENABLED", val)
        assert ch._enabled("TELEGRAM_ENABLED") is True
    for val in ("false", "0", "no", "off", ""):
        monkeypatch.setenv("TELEGRAM_ENABLED", val)
        assert ch._enabled("TELEGRAM_ENABLED") is False


def test_notify_all_telegram_gate_accepts_nonlowercase(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "TRUE")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("NTFY_ENABLED", raising=False)
    # gate passes -> send_telegram runs, then no-ops False without token/chat
    calls = {}
    orig = ch.send_telegram
    def spy(*a, **k):
        calls["ran"] = True
        return orig(*a, **k)
    monkeypatch.setattr(ch, "send_telegram", spy)
    ch.notify_all([LOAN], {}, "https://d/", 40)
    assert calls.get("ran") is True


def test_sorting_is_rate_then_credit(monkeypatch):
    loans = [
        {**LOAN, "loanId": "low-rate-high-credit", "interestRate": 50, "creditScoreNumeric": 900},
        {**LOAN, "loanId": "high-rate-low-credit", "interestRate": 60, "creditScoreNumeric": 700},
        {**LOAN, "loanId": "high-rate-high-credit", "interestRate": 60, "creditScoreNumeric": 800},
    ]
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    sent = []
    monkeypatch.setattr(ch, "_telegram_post", lambda payload: sent.append(payload["text"]) or True)
    assert ch.send_telegram(loans, {}, "https://d/", 40) is True
    text = sent[0]
    assert text.index("high-rate-high-credit") < text.index("high-rate-low-credit")
    assert text.index("high-rate-low-credit") < text.index("low-rate-high-credit")


def test_telegram_api_ok_false_is_delivery_failure(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(ch.httpx, "post", lambda *a, **k: FakeResponse(body={"ok": False, "description": "blocked"}))
    assert ch.send_telegram_text("hello") is False


def test_telegram_rate_limit_retries_once(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    responses = iter([
        FakeResponse(status_code=429, body={"ok": False, "parameters": {"retry_after": 0}}),
        FakeResponse(body={"ok": True}),
    ])
    calls = []
    monkeypatch.setattr(ch.httpx, "post", lambda *a, **k: calls.append(1) or next(responses))
    assert ch.send_telegram_text("hello") is True
    assert len(calls) == 2


def test_header_says_high_yield_not_new():
    hdr = ch.build_header(3, 40)
    assert "HIGH-YIELD LOANS" in hdr
    assert "rate &gt; 40%" in hdr

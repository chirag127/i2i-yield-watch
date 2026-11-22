"""Notifier formatting, delivery acknowledgements and isolated HTTP transport."""

import json
import logging
from html.parser import HTMLParser

import httpx
import pytest

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


@pytest.fixture
def telegram_transport(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:synthetic-secret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "synthetic-chat")
    calls = []

    def install(handler):
        def handle_request(self, request):
            calls.append(request)
            return handler(request)

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
        return calls

    return install


def test_telegram_api_ok_false_is_delivery_failure(telegram_transport):
    calls = telegram_transport(lambda request: httpx.Response(
        200, json={"ok": False, "description": "blocked"}, request=request,
    ))
    assert ch.send_telegram_text("hello") is False
    assert len(calls) == 1


@pytest.mark.parametrize("body", [b"invalid", b"{}", b"[]", b"null", b'{"result":{}}',
                                  b'{"ok":1}', b'{"ok":"true"}'])
def test_telegram_requires_explicit_boolean_acknowledgement(telegram_transport, body):
    calls = telegram_transport(lambda request: httpx.Response(200, content=body, request=request))
    assert ch.send_telegram_text("hello") is False
    assert len(calls) == 1


@pytest.mark.parametrize("status", [400, 403, 500])
def test_telegram_http_failure_is_not_retried_or_acknowledged(telegram_transport, status):
    calls = telegram_transport(lambda request: httpx.Response(
        status, json={"ok": False, "description": "rejected"}, request=request,
    ))
    assert ch.send_telegram_text("hello") is False
    assert len(calls) == 1


def test_telegram_timeout_is_not_acknowledged_or_immediately_replayed(telegram_transport):
    def timeout(request):
        raise httpx.ReadTimeout(f"timeout sending {request.url}", request=request)

    calls = telegram_transport(timeout)
    assert ch.send_telegram_text("hello") is False
    assert len(calls) == 1


@pytest.mark.parametrize("status", [200, 400, 403])
def test_telegram_logs_never_disclose_bot_url_or_response_body(
        telegram_transport, caplog, status):
    caplog.set_level(logging.INFO)
    calls = telegram_transport(lambda request: httpx.Response(
        status, json={"ok": status == 200, "description": str(request.url)}, request=request,
    ))
    assert ch.send_telegram_text("hello") is (status == 200)
    assert len(calls) == 1
    assert "123456:synthetic-secret" not in caplog.text
    if status != 200:
        assert str(status) in caplog.text


def test_telegram_exception_logs_do_not_disclose_token(telegram_transport, caplog):
    def timeout(request):
        raise httpx.ReadTimeout(f"timeout sending {request.url}", request=request)

    telegram_transport(timeout)
    assert ch.send_telegram_text("hello") is False
    assert "ReadTimeout" in caplog.text
    assert "123456:synthetic-secret" not in caplog.text


def test_telegram_rejection_description_is_not_logged(telegram_transport, caplog):
    telegram_transport(lambda request: httpx.Response(
        200, json={"ok": False, "error_code": 400, "description": str(request.url)},
        request=request,
    ))
    assert ch.send_telegram_text("hello") is False
    assert "123456:synthetic-secret" not in caplog.text


def test_httpx_nontelegram_logging_is_preserved(telegram_transport, caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    telegram_transport(lambda request: httpx.Response(200, json={"ok": True}, request=request))
    assert ch.send_telegram_text("hello") is True
    httpx.get("https://example.invalid/health")
    assert "https://example.invalid/health" in caplog.text
    assert "123456:synthetic-secret" not in caplog.text


def test_telegram_429_waits_requested_delay_then_retries_once(telegram_transport, monkeypatch):
    responses = iter([
        httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 2}}),
        httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}),
    ])
    calls = telegram_transport(lambda request: next(responses))
    waits = []
    monkeypatch.setattr(ch.time, "sleep", waits.append)
    assert ch.send_telegram_text("hello") is True
    assert len(calls) == 2
    assert waits == [2]


@pytest.mark.parametrize("parameters", [
    {"retry_after": 31}, {"retry_after": 3600}, {"retry_after": -1},
    {"retry_after": "nan"}, {"retry_after": "inf"}, {"retry_after": "bad"},
    {}, None,
])
def test_telegram_429_does_not_retry_before_allowed_window(
        telegram_transport, monkeypatch, parameters):
    calls = telegram_transport(lambda request: httpx.Response(
        429, json={"ok": False, "parameters": parameters}, request=request,
    ))
    waits = []
    monkeypatch.setattr(ch.time, "sleep", waits.append)
    assert ch.send_telegram_text("hello") is False
    assert len(calls) == 1
    assert waits == []


def test_telegram_repeated_429_is_bounded(telegram_transport, monkeypatch):
    calls = telegram_transport(lambda request: httpx.Response(
        429, json={"ok": False, "parameters": {"retry_after": 1}}, request=request,
    ))
    waits = []
    monkeypatch.setattr(ch.time, "sleep", waits.append)
    assert ch.send_telegram_text("hello") is False
    assert len(calls) == 2
    assert waits == [1]


@pytest.mark.parametrize("selected, expected", [
    ({"telegram"}, {"telegram": True, "ntfy": False}),
    ({"ntfy"}, {"telegram": False, "ntfy": True}),
    (set(), {"telegram": False, "ntfy": False}),
    (None, {"telegram": True, "ntfy": True}),
])
def test_notify_all_only_attempts_selected_enabled_channels(monkeypatch, selected, expected):
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("NTFY_ENABLED", "true")
    attempted = []
    monkeypatch.setattr(ch, "send_telegram", lambda *a: attempted.append("telegram") or True)
    monkeypatch.setattr(ch, "send_ntfy", lambda *a: attempted.append("ntfy") or True)
    assert ch.notify_all([LOAN], {}, "https://d/", channels=selected) == expected
    assert set(attempted) == {name for name, sent in expected.items() if sent}


def test_notify_all_selection_cannot_enable_disabled_channel(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)
    monkeypatch.delenv("NTFY_ENABLED", raising=False)
    monkeypatch.setattr(ch, "send_telegram", lambda *a: pytest.fail("disabled Telegram invoked"))
    monkeypatch.setattr(ch, "send_ntfy", lambda *a: pytest.fail("disabled ntfy invoked"))
    assert ch.notify_all([LOAN], {}, "https://d/", channels={"telegram", "ntfy"}) == {
        "telegram": False, "ntfy": False,
    }


def test_enabled_channels_includes_unconfigured_enabled_destination(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "on")
    monkeypatch.setenv("NTFY_ENABLED", "yes")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert ch.enabled_channels() == {"telegram", "ntfy"}


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


def test_html_link_attributes_escape_quotes():
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.links.append(dict(attrs))

    url = 'https://example.invalid/?q="quoted"&next=1'
    parser = Links()
    parser.feed(ch.format_loan_line({**LOAN, "loanUrl": url}))
    parser.feed(ch.build_footer({}, url))
    assert parser.links == [{"href": url}, {"href": url}]


@pytest.mark.parametrize("case", ["large_loan", "large_footer", "loud_text", "unicode_text"])
def test_oversized_telegram_messages_preserve_content_within_limit(
        telegram_transport, monkeypatch, case):
    calls = telegram_transport(lambda request: httpx.Response(
        200, json={"ok": True, "result": {"message_id": 1}}, request=request,
    ))
    monkeypatch.setattr(ch.time, "sleep", lambda _: None)
    if case == "large_loan":
        marker = "purpose-start " + "A & B " * 900 + " purpose-end"
        assert ch.send_telegram([{**LOAN, "purpose": marker}], {}, "https://d/", 40)
    elif case == "large_footer":
        marker = "https://example.invalid/" + "x" * 4100
        assert ch.send_telegram([LOAN], {}, marker, 40)
    else:
        marker = "start " + ("🚨" * 2300 if case == "unicode_text" else "a" * 4300) + " end"
        assert ch.send_telegram_text(f"<b>{marker}</b>", silent=True)
    payloads = [json.loads(request.content) for request in calls]
    assert len(payloads) >= 2
    assert all(0 < len(payload["text"].encode("utf-16-le")) // 2 <= 4096
               for payload in payloads)
    # Lossless fallback uses escaped text, never fragments of HTML tags or entities.
    class Text(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

        def handle_starttag(self, tag, attrs):
            pytest.fail(f"oversized fallback contains unexpected tag {tag}")

    parser = Text()
    for payload in payloads:
        assert payload["parse_mode"] == "HTML"
        parser.feed(payload["text"])
        parser.close()
    text = "".join(parser.parts)
    assert marker in text
    if case in ("large_loan", "large_footer"):
        assert LOAN["loanUrl"] in text
    else:
        assert all(payload.get("disable_notification") is True for payload in payloads)


def test_telegram_partial_chunk_failure_is_not_acknowledged(telegram_transport, monkeypatch):
    responses = iter([
        httpx.Response(200, json={"ok": True}),
        httpx.Response(403, json={"ok": False}),
    ])
    calls = telegram_transport(lambda request: next(responses))
    monkeypatch.setattr(ch.time, "sleep", lambda _: None)
    assert ch.send_telegram_text("<b>" + "x" * 5000 + "</b>") is False
    assert len(calls) == 2

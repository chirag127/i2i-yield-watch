"""Unit tests for the semi-auto escrow top-up module (no network)."""

from __future__ import annotations

from i2i_watch import topup as T
from i2i_watch.client import I2iClient
from i2i_watch.notify import channels


def _sel(*items):
    out = []
    for i, (rate, left) in enumerate(items):
        out.append({"loanId": i + 1, "rate": rate, "amtLeft": left,
                    "borrowerUserId": 9, "score": 720.0, "noCredit": True,
                    "tenure": 6.0})
    return out


def test_topup_amount_shortfall():
    # 5000 + 3000 needed = 8000; wallet 2000 -> 6000
    sel = _sel((150.0, 5000.0), (160.0, 3000.0))
    assert T.topup_amount(sel, 2000.0) == 6000.0


def test_topup_amount_zero_when_wallet_covers():
    assert T.topup_amount(_sel((150.0, 5000.0)), 5000.0) == 0.0


def test_topup_amount_capped_at_max():
    # 10 loans x 5000 = 50000 -> capped at TOPUP_MAX_AMOUNT 25000
    assert T.topup_amount(_sel(*[(150.0, 5000.0)] * 10), 0.0) == 25000.0


def test_extract_payment_url_shapes():
    assert T.extract_payment_url({"paymentUrl": "https://paytm/x"}) == "https://paytm/x"
    assert T.extract_payment_url({"action": "redirect", "url": "https://paytm/x"}) == "https://paytm/x"
    assert T.extract_payment_url({"data": {"paymentUrl": "https://paytm/y"}}) == "https://paytm/y"
    assert T.extract_payment_url({"data": "https://paytm/z"}) == "https://paytm/z"
    assert T.extract_payment_url({"data": {"foo": 1}}) == ""
    assert T.extract_payment_url("nope") == ""


def test_extract_payment_url_live_restdata_shape():
    # Exact shape captured live on 2026-08-18 (secrets stripped).
    live = {"restdata": {
        "ORDER_ID": "1304413-1787066505293", "CUST_ID": "CUST1304413",
        "TXN_AMOUNT": "1000", "MID": "LENRNV71647944499761",
        "CHECKSUMHASH": "x", "CALLBACK_URL": "https://apiv1.i2ifunding.com/...",
        "url": "https://secure.paytmpayments.com/theia/processTransaction"}}
    assert T.extract_payment_url(live) == \
        "https://secure.paytmpayments.com/theia/processTransaction"


def test_build_checkout_page_autosubmits_form():
    live = {"restdata": {
        "ORDER_ID": "O1", "CUST_ID": "C1", "TXN_AMOUNT": "1000",
        "CHECKSUMHASH": "ch", "CALLBACK_URL": "https://cb/x",
        "url": "https://secure.paytmpayments.com/theia/processTransaction"}}
    html = T.build_checkout_page(live)
    assert 'action="https://secure.paytmpayments.com/theia/processTransaction"' in html
    assert 'method="post"' in html
    assert 'name="ORDER_ID" value="O1"' in html
    assert 'name="CHECKSUMHASH" value="ch"' in html
    assert 'name="CALLBACK_URL" value="https://cb/x"' in html
    assert 'onload="document.forms[0].submit()"' in html
    # url itself is the action, not a hidden field
    assert 'name="url"' not in html
    assert T.build_checkout_page({"data": {"foo": 1}}) == ""
    assert T.build_checkout_page("nope") == ""


def test_build_topup_message_includes_amount_upi_and_loan_link():
    sel = _sel((150.0, 5000.0))
    sel[0]["borrowerUserId"] = "1304413"
    msg = T.build_topup_message(sel, 5000.0, "",
                                upi_id="chirag@okbank")
    assert "5,000" in msg
    assert "chirag@okbank" in msg
    assert "150.00%" in msg
    assert 'href="https://www.i2ifunding.com/borrower/listing/public-profile/1304413/1"' in msg


def test_build_topup_message_upi_hint_with_checkout_url():
    # The UPI ID hint must appear even when a checkout URL is present — that's
    # the ID the operator enters on Paytm's UPI tab.
    msg = T.build_topup_message(_sel((150.0, 5000.0)), 5000.0,
                                "https://paytm.checkout/x", upi_id="chirag@okbank")
    assert "https://paytm.checkout/x" in msg
    assert "chirag@okbank" in msg
    assert "UPI tab" in msg


def test_paytm_paynow_posts_multipart_form(monkeypatch):
    import httpx

    c = I2iClient.__new__(I2iClient)
    c.csrf, c.sid = "t", "s"
    captured = {}

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"isError": False, "message": "success", "data": {}}

    def fake_post(url, files, headers, timeout, follow_redirects):
        captured["url"] = url
        captured["files"] = {k: v[1] for k, v in files.items()}
        captured["headers"] = headers
        return _R()

    monkeypatch.setattr(httpx, "post", fake_post)
    c.paytm_paynow(1000.0)
    assert "apiv1.i2ifunding.com/paytm/paynow" in captured["url"]
    assert captured["files"] == {"TXN_AMOUNT": "1000", "CHANNEL": "WEB",
                                 "pageurl": "investoraccount/overview"}
    # Live-verified fix: the JSON Content-Type must be dropped or the server's
    # body-parser fails on the multipart body with a 400.
    assert "Content-Type" not in {k.title() for k in captured["headers"]}
    assert "Content-type" not in captured["headers"]


def test_send_telegram_text_silent_flag(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    calls: list[dict] = []

    class _R:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        calls.append(json)
        return _R()

    monkeypatch.setattr(channels.httpx, "post", fake_post)
    assert channels.send_telegram_text("hi", silent=True) is True
    assert calls[-1].get("disable_notification") is True
    assert channels.send_telegram_text("hi") is True
    assert "disable_notification" not in calls[-1]

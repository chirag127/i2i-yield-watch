"""Offline tests for the Playwright fallback helpers; no browser or network."""

from i2i_watch.sources import i2i


class _FakePage:
    def __init__(self, state, advance_on_wait=None):
        self.state = state
        self.advance_on_wait = advance_on_wait
        self.waits = []

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)
        if self.advance_on_wait:
            self.advance_on_wait(self.state, milliseconds)


def test_row_id_prefers_bloan_id_and_falls_back_to_pl_id():
    assert i2i._row_id({"pl_bloan_id": 123, "pl_id": 456}) == "123"
    assert i2i._row_id({"pl_id": 456}) == "456"
    assert i2i._row_id({}) == ""


def test_wait_for_response_returns_as_soon_as_valid_response_arrives():
    state = {"valid_responses": 0}

    def arrive_after_first_slice(current, _milliseconds):
        current["valid_responses"] = 1

    page = _FakePage(state, arrive_after_first_slice)
    assert i2i._wait_for_response_generation(page, state, 0, 8000) is True
    assert page.waits == [i2i.WAIT_POLL_MS]


def test_wait_for_response_times_out_at_ceiling():
    state = {"valid_responses": 0}
    page = _FakePage(state)
    assert i2i._wait_for_response_generation(page, state, 0, 250) is False
    assert sum(page.waits) == 250


def test_wait_for_response_ignores_previous_generation():
    state = {"valid_responses": 2}
    page = _FakePage(state)
    assert i2i._wait_for_response_generation(page, state, 2, 100) is False


def test_fetch_all_loans_reuses_passed_client_no_relogin(monkeypatch):
    """A passed-in client is used as-is — no fresh I2iClient.from_env() call,
    so the poll loop logs in once per run, not once per pass."""
    calls = {"list_loans": 0, "from_env": 0}

    class _Fake:
        def list_loans(self):
            calls["list_loans"] += 1
            return [{"pl_bloan_id": 1}]

    # fetch_all_loans lazily imports I2iClient only when client is None; with a
    # client passed in it must never construct one. Patch the import target so
    # an accidental construction fails the test.
    import types
    fake_mod = types.ModuleType("i2i_watch.client")

    def boom_from_env(*a, **k):
        calls["from_env"] += 1
        raise AssertionError("from_env must not be called when a client is passed")

    fake_mod.I2iClient = type("I2iClient", (), {"from_env": staticmethod(boom_from_env)})
    monkeypatch.setitem(__import__("sys").modules, "i2i_watch.client", fake_mod)

    rows = i2i.fetch_all_loans(client=_Fake())
    assert rows == [{"pl_bloan_id": 1}]
    assert calls["list_loans"] == 1
    assert calls["from_env"] == 0


def test_fetch_all_loans_raises_without_browser_fallback_flag(monkeypatch):
    """A direct-HTTP failure must RAISE (so the run fails loudly) unless the
    browser fallback is explicitly enabled — Playwright is never in the hot
    path by default."""
    monkeypatch.delenv("I2I_ALLOW_BROWSER_FALLBACK", raising=False)

    class _Broken:
        def list_loans(self):
            raise TimeoutError("direct listing timed out")

    import pytest
    with pytest.raises(RuntimeError, match="I2I_ALLOW_BROWSER_FALLBACK"):
        i2i.fetch_all_loans(client=_Broken())


def test_fetch_all_loans_browser_fallback_enabled_uses_browser(monkeypatch):
    """With I2I_ALLOW_BROWSER_FALLBACK=1, a direct failure falls back to the
    Playwright XHR-interception scraper (debugging an API regression)."""
    monkeypatch.setenv("I2I_ALLOW_BROWSER_FALLBACK", "1")

    class _Broken:
        def list_loans(self):
            raise TimeoutError("direct listing timed out")

    # The fallback presence-check imports playwright.sync_api; in CI (scrape.yml's
    # venv has no browser extra) it is NOT installed, so simulate it so this test
    # exercises the fallback logic regardless of the environment.
    import sys, types
    pw = types.ModuleType("playwright")
    pw_sync = types.ModuleType("playwright.sync_api")
    pw.sync_api = pw_sync
    monkeypatch.setitem(sys.modules, "playwright", pw)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", pw_sync)

    monkeypatch.setattr(i2i, "_scrape_once", lambda: [{"pl_bloan_id": 42}])
    rows = i2i.fetch_all_loans(client=_Broken())
    assert rows == [{"pl_bloan_id": 42}]

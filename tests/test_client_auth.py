"""Tests for I2iClient.from_env auth chain: auto-login primary, session-token fallback.

Locks the rule the user cares about: auto-login (fresh tokens, no expiry) must
win whenever creds are present; I2I_CSRF_TOKEN/I2I_SESSION_ID are only a
fallback for when login fails or creds are absent.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from i2i_watch.client import I2iClient


def _clear_auth_env(monkeypatch):
    for k in ("I2I_EMAIL", "I2I_PASSWORD", "I2I_CSRF_TOKEN", "I2I_SESSION_ID"):
        monkeypatch.delenv(k, raising=False)


def test_from_env_prefers_auto_login_over_session_tokens(monkeypatch):
    """Creds present => login() is called, session tokens IGNORED (fresh wins)."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("I2I_EMAIL", "a@b.com")
    monkeypatch.setenv("I2I_PASSWORD", "pw")
    monkeypatch.setenv("I2I_CSRF_TOKEN", "stale_csrf")
    monkeypatch.setenv("I2I_SESSION_ID", "stale_sid")
    with patch("i2i_watch.client.login", return_value=("fresh_csrf", "fresh_sid")) as m:
        c = I2iClient.from_env()
    m.assert_called_once_with("a@b.com", "pw")
    assert (c.csrf, c.sid) == ("fresh_csrf", "fresh_sid")


def test_from_env_falls_back_to_session_when_login_fails(monkeypatch):
    """Login raising (network blip) => manual session tokens are used, no crash."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("I2I_EMAIL", "a@b.com")
    monkeypatch.setenv("I2I_PASSWORD", "pw")
    monkeypatch.setenv("I2I_CSRF_TOKEN", "csrf_x")
    monkeypatch.setenv("I2I_SESSION_ID", "sid_x")
    with patch("i2i_watch.client.login", side_effect=TimeoutError("login down")):
        c = I2iClient.from_env()
    assert (c.csrf, c.sid) == ("csrf_x", "sid_x")


def test_from_env_session_only_when_no_creds(monkeypatch):
    """No email/password => session tokens used directly."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("I2I_CSRF_TOKEN", "csrf_y")
    monkeypatch.setenv("I2I_SESSION_ID", "sid_y")
    c = I2iClient.from_env()
    assert (c.csrf, c.sid) == ("csrf_y", "sid_y")


def test_from_env_raises_when_no_auth_at_all(monkeypatch):
    """Nothing set => clear SystemExit, never a silent empty-token client."""
    _clear_auth_env(monkeypatch)
    with pytest.raises(SystemExit, match="no i2i auth"):
        I2iClient.from_env()


def test_from_env_login_failure_without_session_still_raises(monkeypatch):
    """Login fails AND no session fallback => the login error propagates
    (better than silently proceeding with a broken client)."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("I2I_EMAIL", "a@b.com")
    monkeypatch.setenv("I2I_PASSWORD", "pw")
    with patch("i2i_watch.client.login", side_effect=ConnectionError("down")):
        with pytest.raises(ConnectionError):
            I2iClient.from_env()

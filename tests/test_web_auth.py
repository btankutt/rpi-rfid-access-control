"""Tests for the web admin authentication module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.config import Settings, get_settings
from src.web.auth import (
    SESSION_KEY_CSRF,
    SESSION_KEY_USERNAME,
    current_admin,
    hash_password,
    login_session,
    logout_session,
    require_admin,
    verify_credentials,
    verify_csrf,
    verify_password,
)


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """A Settings with a known admin/password pair."""
    monkeypatch.setenv("ADMIN_USERNAME", "rootadmin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("correct horse"))
    monkeypatch.setenv("SESSION_SECRET", "x" * 48)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    return Settings()  # type: ignore[call-arg]


class TestHashAndVerify:
    def test_hash_produces_bcrypt_format(self):
        h = hash_password("hunter2")
        assert h.startswith(("$2a$", "$2b$", "$2y$"))

    def test_hash_unique_per_call(self):
        a = hash_password("same")
        b = hash_password("same")
        assert a != b  # different salts

    def test_verify_correct_password(self):
        h = hash_password("right")
        assert verify_password("right", h) is True

    def test_verify_wrong_password(self):
        h = hash_password("right")
        assert verify_password("wrong", h) is False

    def test_verify_empty_password_false(self):
        h = hash_password("right")
        assert verify_password("", h) is False

    def test_verify_malformed_hash_false(self):
        assert verify_password("any", "not-a-bcrypt-hash") is False

    def test_hash_empty_raises(self):
        with pytest.raises(ValueError):
            hash_password("")


class TestVerifyCredentials:
    def test_correct_credentials(self, admin_settings: Settings):
        assert verify_credentials("rootadmin", "correct horse", admin_settings) is True

    def test_wrong_username(self, admin_settings: Settings):
        assert verify_credentials("someoneelse", "correct horse", admin_settings) is False

    def test_wrong_password(self, admin_settings: Settings):
        assert verify_credentials("rootadmin", "wrong", admin_settings) is False

    def test_both_wrong(self, admin_settings: Settings):
        assert verify_credentials("nope", "wrong", admin_settings) is False


class TestSessionHelpers:
    def _make_request(self) -> MagicMock:
        req = MagicMock()
        req.session = {}
        return req

    def test_login_sets_username_and_csrf(self):
        req = self._make_request()
        csrf = login_session(req, "rootadmin")
        assert req.session[SESSION_KEY_USERNAME] == "rootadmin"
        assert req.session[SESSION_KEY_CSRF] == csrf
        assert len(csrf) > 30  # token_urlsafe(32) produces ~43 chars

    def test_logout_clears_session(self):
        req = self._make_request()
        login_session(req, "rootadmin")
        logout_session(req)
        assert req.session == {}

    def test_current_admin_returns_username(self):
        req = self._make_request()
        login_session(req, "rootadmin")
        assert current_admin(req) == "rootadmin"

    def test_current_admin_returns_none_when_logged_out(self):
        req = self._make_request()
        assert current_admin(req) is None


class TestRequireAdminDependency:
    def test_authenticated_returns_username(self):
        req = MagicMock()
        req.session = {SESSION_KEY_USERNAME: "rootadmin"}
        assert require_admin(req) == "rootadmin"

    def test_unauthenticated_raises_401(self):
        req = MagicMock()
        req.session = {}
        with pytest.raises(HTTPException) as exc_info:
            require_admin(req)
        assert exc_info.value.status_code == 401


class TestCsrf:
    def test_valid_token_accepted(self):
        req = MagicMock()
        req.session = {}
        token = login_session(req, "rootadmin")
        assert verify_csrf(req, token) is True

    def test_wrong_token_rejected(self):
        req = MagicMock()
        req.session = {}
        login_session(req, "rootadmin")
        assert verify_csrf(req, "wrong-token") is False

    def test_no_session_token_rejected(self):
        req = MagicMock()
        req.session = {}
        assert verify_csrf(req, "anything") is False

    def test_empty_submitted_rejected(self):
        req = MagicMock()
        req.session = {}
        login_session(req, "rootadmin")
        assert verify_csrf(req, "") is False

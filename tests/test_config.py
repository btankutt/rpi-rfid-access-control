"""Tests for the Settings configuration module."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import Settings, get_settings


VALID_BCRYPT = "$2b$12$abcdefghijklmnopqrstuuMOJ7vSPGdEd0K0NWmd4Z9b1g5fXrZ0pe"
VALID_SECRET = "x" * 48  # 48 random-looking chars; long enough


@pytest.fixture
def minimal_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Set the minimum required environment variables for Settings()."""
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", VALID_BCRYPT)
    monkeypatch.setenv("SESSION_SECRET", VALID_SECRET)
    # Point file paths into the tmp_path so tests don't touch the real fs
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "access.db"))
    monkeypatch.setenv("BACKUP_PATH", str(tmp_path / "backups"))
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "logs" / "access.log"))
    # Prevent any local .env from leaking into the test
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()


class TestRequiredFields:
    """The two secret fields have no default — startup must fail without them."""

    def test_missing_password_hash_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("SESSION_SECRET", VALID_SECRET)
        monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]

    def test_missing_session_secret_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("ADMIN_PASSWORD_HASH", VALID_BCRYPT)
        monkeypatch.delenv("SESSION_SECRET", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]


class TestSecretValidation:
    def test_short_session_secret_rejected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("ADMIN_PASSWORD_HASH", VALID_BCRYPT)
        monkeypatch.setenv("SESSION_SECRET", "tooshort")
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValidationError, match="at least 32 characters"):
            Settings()  # type: ignore[call-arg]

    def test_placeholder_session_secret_rejected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("ADMIN_PASSWORD_HASH", VALID_BCRYPT)
        monkeypatch.setenv(
            "SESSION_SECRET",
            "change_this_to_a_long_random_string_in_production",
        )
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValidationError, match="placeholder"):
            Settings()  # type: ignore[call-arg]

    def test_plaintext_password_rejected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("ADMIN_PASSWORD_HASH", "hunter2")
        monkeypatch.setenv("SESSION_SECRET", VALID_SECRET)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValidationError, match="bcrypt"):
            Settings()  # type: ignore[call-arg]

    def test_example_password_hash_rejected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(
            "ADMIN_PASSWORD_HASH",
            "$2b$12$EXAMPLE_REPLACE_WITH_REAL_BCRYPT_HASH",
        )
        monkeypatch.setenv("SESSION_SECRET", VALID_SECRET)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValidationError, match="placeholder"):
            Settings()  # type: ignore[call-arg]


class TestDefaults:
    """Default values should match what's documented in .env.example."""

    def test_default_values(self, minimal_env: None) -> None:
        s = Settings()  # type: ignore[call-arg]

        assert s.use_mock_hardware is True
        assert s.reader_type == "mock"
        assert s.relay_gpio_pin == 17
        assert s.door_switch_gpio_pin is None
        assert s.web_host == "0.0.0.0"
        assert s.web_port == 8000
        assert s.door_open_duration_seconds == 5.0
        assert s.fail_safe_mode is True
        assert s.rate_limit_failed_attempts == 5
        assert s.log_level == "INFO"

    def test_secrets_not_in_repr(self, minimal_env: None) -> None:
        """SecretStr fields must mask their values in repr()."""
        s = Settings()  # type: ignore[call-arg]
        r = repr(s)
        assert VALID_SECRET not in r
        assert VALID_BCRYPT not in r


class TestFieldValidation:
    def test_invalid_reader_type(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("ADMIN_PASSWORD_HASH", VALID_BCRYPT)
        monkeypatch.setenv("SESSION_SECRET", VALID_SECRET)
        monkeypatch.setenv("READER_TYPE", "wifi")  # not a valid option
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]

    def test_invalid_gpio_pin(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("ADMIN_PASSWORD_HASH", VALID_BCRYPT)
        monkeypatch.setenv("SESSION_SECRET", VALID_SECRET)
        monkeypatch.setenv("RELAY_GPIO_PIN", "99")
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]

    def test_invalid_web_port(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("ADMIN_PASSWORD_HASH", VALID_BCRYPT)
        monkeypatch.setenv("SESSION_SECRET", VALID_SECRET)
        monkeypatch.setenv("WEB_PORT", "70000")
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]


class TestEnsureDirectories:
    def test_creates_missing_directories(
        self, minimal_env: None, tmp_path: Path
    ) -> None:
        s = Settings()  # type: ignore[call-arg]

        # Parents should not exist yet (database_path is tmp_path/access.db,
        # so its parent IS tmp_path which exists, but logs/ doesn't).
        log_dir = tmp_path / "logs"
        backup_dir = tmp_path / "backups"
        assert not log_dir.exists()
        assert not backup_dir.exists()

        s.ensure_directories()

        assert log_dir.is_dir()
        assert backup_dir.is_dir()

    def test_idempotent(self, minimal_env: None) -> None:
        s = Settings()  # type: ignore[call-arg]
        s.ensure_directories()
        s.ensure_directories()  # Must not raise


class TestGetSettingsCache:
    def test_returns_same_instance(self, minimal_env: None) -> None:
        a = get_settings()
        b = get_settings()
        assert a is b

"""
Application configuration loaded from environment variables.

All settings are loaded from a `.env` file (or real environment variables)
using `pydantic-settings`. Required fields have no default and will cause
startup to fail if missing — this is deliberate, since a misconfigured
access control system is more dangerous than one that refuses to start.

Usage:
    from src.config import Settings, get_settings

    settings = get_settings()
    print(settings.web_port)
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

ReaderType = Literal["mock", "mfrc522", "pn532", "rs232"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """
    Application settings loaded from `.env` and the process environment.

    Environment variables take precedence over `.env` file values. Field
    names are case-insensitive (`READER_TYPE` and `reader_type` are
    equivalent), matching pydantic-settings' default behavior.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Hardware --------------------------------------------------------
    use_mock_hardware: bool = True
    reader_type: ReaderType = "mock"
    relay_gpio_pin: int = Field(default=17, ge=0, le=27)
    door_switch_gpio_pin: Optional[int] = Field(default=None, ge=0, le=27)
    rs232_port: str = "/dev/ttyUSB0"
    rs232_baudrate: int = Field(default=9600, gt=0)

    # -- Database --------------------------------------------------------
    database_path: Path = Path("./data/access.db")
    backup_interval_hours: int = Field(default=24, gt=0)
    backup_path: Path = Path("./backups/")

    # -- Web server ------------------------------------------------------
    web_host: str = "0.0.0.0"
    web_port: int = Field(default=8000, gt=0, le=65535)
    admin_username: str = "admin"
    admin_password_hash: SecretStr
    session_secret: SecretStr

    # -- Access control behavior ----------------------------------------
    door_open_duration_seconds: float = Field(default=5.0, gt=0)
    fail_safe_mode: bool = True
    rate_limit_failed_attempts: int = Field(default=5, gt=0)
    rate_limit_window_seconds: int = Field(default=60, gt=0)

    # -- Logging ---------------------------------------------------------
    log_level: LogLevel = "INFO"
    log_file: Path = Path("./logs/access.log")
    log_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    log_backup_count: int = Field(default=5, ge=0)

    @field_validator("session_secret")
    @classmethod
    def _session_secret_must_be_long(cls, v: SecretStr) -> SecretStr:
        """Reject obviously weak session secrets.

        A short or default-looking session secret defeats CSRF and
        session-tampering protection, so the system refuses to start
        rather than run insecurely.
        """
        raw = v.get_secret_value()
        if len(raw) < 32:
            raise ValueError(
                "SESSION_SECRET must be at least 32 characters. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        if "change_this" in raw.lower() or raw.lower() == "secret":
            raise ValueError(
                "SESSION_SECRET appears to be a placeholder. Set a real random value."
            )
        return v

    @field_validator("admin_password_hash")
    @classmethod
    def _password_hash_must_be_bcrypt(cls, v: SecretStr) -> SecretStr:
        """Ensure the admin password is bcrypt-hashed, not plaintext."""
        raw = v.get_secret_value()
        if not raw.startswith(("$2a$", "$2b$", "$2y$")):
            raise ValueError(
                "ADMIN_PASSWORD_HASH must be a bcrypt hash (starts with $2a$/$2b$/$2y$). "
                "Generate one with: python scripts/hash_password.py"
            )
        if "EXAMPLE" in raw or "REPLACE" in raw:
            raise ValueError(
                "ADMIN_PASSWORD_HASH still contains placeholder text. Set a real bcrypt hash."
            )
        return v

    def ensure_directories(self) -> None:
        """Create parent directories for database, backups, and log file.

        Called once at startup so that the application does not crash on
        first write to a path that doesn't yet exist.
        """
        for path in (
            self.database_path.parent,
            self.backup_path,
            self.log_file.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)
            logger.debug("Ensured directory exists: %s", path)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached process-wide Settings instance.

    Using `lru_cache` makes this safe to call from anywhere without
    re-reading the `.env` file. For tests, call `get_settings.cache_clear()`
    after monkeypatching environment variables.
    """
    return Settings()  # type: ignore[call-arg]

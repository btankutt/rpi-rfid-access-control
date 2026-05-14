"""
Application configuration loaded from environment variables.

All settings are read from a `.env` file (or the process environment)
via `pydantic-settings`. Every field has a sensible default so the
system can boot in mock mode without any explicit configuration.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

ReaderType = Literal["mock", "mfrc522", "pn532", "rs232"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Application settings loaded from `.env` and the process environment.

    Field names are case-insensitive (`READER_TYPE` and `reader_type`
    are equivalent). Unknown keys are silently ignored so the same
    `.env` can be shared across related repositories.
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
    relay_gpio_pin: int = 17
    door_switch_gpio_pin: Optional[int] = None
    rs232_port: str = "/dev/ttyUSB0"
    rs232_baudrate: int = 9600

    # -- Database --------------------------------------------------------
    database_path: str = "./data/access.db"

    # -- Door behavior --------------------------------------------------
    door_open_duration_seconds: float = 5.0
    fail_safe_mode: bool = True

    # -- Logging ---------------------------------------------------------
    log_level: LogLevel = "INFO"
    log_file: str = "./logs/access.log"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached process-wide Settings instance.

    Tests that mutate the environment should call
    `get_settings.cache_clear()` after patching env vars.
    """
    return Settings()

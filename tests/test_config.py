"""Tests for the Settings configuration module."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Keep tests from picking up the developer's real .env."""
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestDefaults:
    """With no env vars set, the documented defaults must apply."""

    def test_default_values(self):
        s = Settings()
        assert s.use_mock_hardware is True
        assert s.reader_type == "mock"
        assert s.relay_gpio_pin == 17
        assert s.door_switch_gpio_pin is None
        assert s.rs232_port == "/dev/ttyUSB0"
        assert s.rs232_baudrate == 9600
        assert s.database_path == "./data/access.db"
        assert s.door_open_duration_seconds == 5.0
        assert s.fail_safe_mode is True
        assert s.log_level == "INFO"
        assert s.log_file == "./logs/access.log"


class TestEnvOverrides:
    def test_reader_type_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("READER_TYPE", "mfrc522")
        assert Settings().reader_type == "mfrc522"

    def test_use_mock_hardware_false(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("USE_MOCK_HARDWARE", "false")
        assert Settings().use_mock_hardware is False

    def test_relay_pin_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("RELAY_GPIO_PIN", "22")
        assert Settings().relay_gpio_pin == 22

    def test_door_switch_pin_optional(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DOOR_SWITCH_GPIO_PIN", "18")
        assert Settings().door_switch_gpio_pin == 18

    def test_case_insensitive(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("log_level", "DEBUG")
        assert Settings().log_level == "DEBUG"


class TestValidation:
    def test_invalid_reader_type_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("READER_TYPE", "wifi")
        with pytest.raises(ValidationError):
            Settings()

    def test_invalid_log_level_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
        with pytest.raises(ValidationError):
            Settings()


class TestGetSettingsCache:
    def test_returns_same_instance(self):
        a = get_settings()
        b = get_settings()
        assert a is b

    def test_cache_clear_refreshes(self, monkeypatch: pytest.MonkeyPatch):
        a = get_settings()
        monkeypatch.setenv("READER_TYPE", "pn532")
        get_settings.cache_clear()
        b = get_settings()
        assert a is not b
        assert b.reader_type == "pn532"

"""Tests for the main entry-point helpers (no full asyncio.run)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from src.access_manager import AccessManager
from src.audit_logger import AuditLogger
from src.config import Settings, get_settings
from src.door_controller import MockDoorController
from src.main import build_state, reader_loop, setup_logging
from src.rate_limiter import RateLimiter
from src.readers import MockRFIDReader
from src.web.auth import hash_password


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.setenv("ADMIN_USERNAME", "rootadmin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("pw1234"))
    monkeypatch.setenv("SESSION_SECRET", "x" * 48)
    monkeypatch.setenv("USE_MOCK_HARDWARE", "true")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "m.db"))
    monkeypatch.setenv("BACKUP_PATH", str(tmp_path / "backups"))
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "logs" / "m.log"))
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    return Settings()  # type: ignore[call-arg]


class TestSetupLogging:
    def test_creates_log_directory_and_handlers(
        self, settings: Settings, tmp_path: Path
    ):
        setup_logging(settings)
        assert settings.log_file.parent.is_dir()
        root = logging.getLogger()
        # Console + rotating-file = 2 handlers
        assert len(root.handlers) == 2

    def test_idempotent(self, settings: Settings):
        setup_logging(settings)
        setup_logging(settings)  # second call must not pile up handlers
        assert len(logging.getLogger().handlers) == 2


class TestBuildState:
    def test_mock_mode_builds_mock_components(self, settings: Settings):
        state = build_state(settings)
        assert isinstance(state.reader, MockRFIDReader)
        assert isinstance(state.door, MockDoorController)
        assert isinstance(state.audit, AuditLogger)
        assert isinstance(state.rate_limiter, RateLimiter)
        assert isinstance(state.access_manager, AccessManager)
        assert state.settings is settings


class TestReaderLoop:
    @pytest.mark.asyncio
    async def test_processes_card_then_stops(self, settings: Settings):
        state = build_state(settings)
        await state.database.init_schema()

        stop = asyncio.Event()
        task = asyncio.create_task(reader_loop(state, stop, poll_timeout=0.05))

        # Trigger a card read; AccessManager will deny (UNKNOWN_CARD)
        # but the audit log should record it.
        state.reader.trigger_read("LOOPCARD")
        await asyncio.sleep(0.1)

        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

        events = await state.audit.recent_events()
        assert any(e.card_uid == "LOOPCARD" for e in events)
        await state.database.close()

    @pytest.mark.asyncio
    async def test_exception_does_not_kill_loop(self, settings: Settings):
        """Even if the reader raises, the loop logs and continues."""
        state = build_state(settings)
        await state.database.init_schema()

        original_read = state.reader.read_card
        call_count = {"n": 0}

        async def flaky_read(timeout: float = 1.0):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated reader fault")
            return await original_read(timeout=timeout)

        state.reader.read_card = flaky_read  # type: ignore[method-assign]

        stop = asyncio.Event()
        task = asyncio.create_task(reader_loop(state, stop, poll_timeout=0.05))

        # Give it enough time to retry past the back-off (1s) at least once,
        # plus a normal read after.
        await asyncio.sleep(1.3)
        state.reader.trigger_read = original_read.__self__.trigger_read  # noqa: SLF001
        original_read.__self__.trigger_read("OK")
        await asyncio.sleep(0.2)

        stop.set()
        await asyncio.wait_for(task, timeout=3.0)

        # The reader was called more than once -> loop survived the error.
        assert call_count["n"] >= 2
        await state.database.close()

    @pytest.mark.asyncio
    async def test_stop_event_terminates_loop(self, settings: Settings):
        state = build_state(settings)
        await state.database.init_schema()

        stop = asyncio.Event()
        stop.set()  # already stopped
        # With timeout very short, loop should observe stop and exit immediately.
        await asyncio.wait_for(
            reader_loop(state, stop, poll_timeout=0.05), timeout=1.0
        )
        await state.database.close()

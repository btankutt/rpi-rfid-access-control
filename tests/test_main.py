"""Tests for the main entry point."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from src.config import Settings, get_settings
from src.database import add_user, close_db, init_db, init_engine
from src.door_controller import MockDoorController
from src.main import (
    _build_arg_parser,
    main,
    process_card,
    reader_loop,
    setup_logging,
)
from src.readers import CardRead, MockRFIDReader


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "main.db"))
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "logs" / "main.log"))
    monkeypatch.setenv("USE_MOCK_HARDWARE", "true")
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture(autouse=True)
def _close_db_between_tests():
    """Each test should start without a leaked engine from a prior test."""
    yield
    asyncio.get_event_loop().run_until_complete(close_db()) if False else None
    # Use a fresh loop-safe close:
    try:
        asyncio.run(close_db())
    except RuntimeError:
        # If there's an active loop, skip — fixtures around it handle it.
        pass


class TestSetupLogging:
    def test_creates_log_dir_and_handlers(self, settings: Settings):
        setup_logging(settings)
        assert Path(settings.log_file).parent.is_dir()
        root = logging.getLogger()
        assert len(root.handlers) == 2

    def test_idempotent(self, settings: Settings):
        setup_logging(settings)
        setup_logging(settings)
        assert len(logging.getLogger().handlers) == 2


class TestArgParser:
    def test_no_args_simulate_is_none(self):
        ns = _build_arg_parser().parse_args([])
        assert ns.simulate_card is None

    def test_simulate_captured(self):
        ns = _build_arg_parser().parse_args(["--simulate-card", "AAAA"])
        assert ns.simulate_card == "AAAA"


class TestProcessCard:
    @pytest.mark.asyncio
    async def test_unknown_card_denied(self, settings: Settings):
        init_engine(settings.database_path)
        await init_db()
        door = MockDoorController(default_duration_seconds=0.01)
        try:
            card = CardRead(
                uid="NOPE",
                timestamp=__import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
                reader_type="mock",
            )
            decision = await process_card(card, door)
            assert decision == {
                "granted": False,
                "reason": "UNKNOWN_CARD",
                "user_id": None,
            }
            assert door.open_events == []
        finally:
            await close_db()

    @pytest.mark.asyncio
    async def test_known_active_user_granted(self, settings: Settings):
        init_engine(settings.database_path)
        await init_db()
        door = MockDoorController(default_duration_seconds=0.01)
        try:
            user = await add_user(card_uid="OK", name="Sim")
            card = CardRead(
                uid="OK",
                timestamp=__import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
                reader_type="mock",
            )
            decision = await process_card(card, door)
            assert decision["granted"] is True
            assert decision["user_id"] == user.id
            # Door open is fire-and-forget; let it run.
            await asyncio.sleep(0.05)
            assert len(door.open_events) == 1
        finally:
            await close_db()

    @pytest.mark.asyncio
    async def test_inactive_user_denied(self, settings: Settings):
        init_engine(settings.database_path)
        await init_db()
        door = MockDoorController(default_duration_seconds=0.01)
        try:
            user = await add_user(card_uid="OFF", name="Off", is_active=False)
            card = CardRead(
                uid="OFF",
                timestamp=__import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
                reader_type="mock",
            )
            decision = await process_card(card, door)
            assert decision == {
                "granted": False,
                "reason": "USER_INACTIVE",
                "user_id": user.id,
            }
            assert door.open_events == []
        finally:
            await close_db()


class TestReaderLoop:
    @pytest.mark.asyncio
    async def test_dispatches_card_to_process(self, settings: Settings):
        init_engine(settings.database_path)
        await init_db()
        reader = MockRFIDReader()
        await reader.initialize()
        door = MockDoorController(default_duration_seconds=0.01)
        try:
            stop = asyncio.Event()
            task = asyncio.create_task(
                reader_loop(reader, door, stop, poll_timeout=0.02)
            )

            reader.trigger_read("LOOPCARD")
            await asyncio.sleep(0.1)

            stop.set()
            await asyncio.wait_for(task, timeout=1.0)

            from src.database import get_recent_logs

            rows = await get_recent_logs(limit=5)
            assert any(r.card_uid == "LOOPCARD" for r in rows)
        finally:
            await reader.shutdown()
            await close_db()

    @pytest.mark.asyncio
    async def test_stop_event_terminates_immediately(self, settings: Settings):
        reader = MockRFIDReader()
        door = MockDoorController()
        stop = asyncio.Event()
        stop.set()
        await asyncio.wait_for(
            reader_loop(reader, door, stop, poll_timeout=0.05), timeout=1.0
        )


class TestEntryPointSimulate:
    def test_simulate_unknown_exits_1(
        self,
        settings: Settings,
        capsys: pytest.CaptureFixture[str],
    ):
        with pytest.raises(SystemExit) as exc:
            main(["--simulate-card", "NEVERSEEN"])
        assert exc.value.code == 1

        out = capsys.readouterr().out
        result = json.loads(out)
        assert result["granted"] is False
        assert result["reason"] == "UNKNOWN_CARD"

"""Tests for the audit logger."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from src.audit_logger import AccessEvent, AuditLogger
from src.database import Database, build_sqlite_url


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(build_sqlite_url(str(tmp_path / "audit.db")))
    await database.init_schema()
    try:
        yield database
    finally:
        await database.close()


@pytest_asyncio.fixture
async def audit(db: Database):
    return AuditLogger(db)


class TestPersistence:
    @pytest.mark.asyncio
    async def test_log_writes_row(self, audit: AuditLogger):
        event = await audit.log(
            card_uid="AAAA",
            decision="GRANTED",
            reason="OK",
            reader_type="mock",
        )
        assert event.card_uid == "AAAA"
        assert event.decision == "GRANTED"

        recent = await audit.recent_events()
        assert len(recent) == 1
        assert recent[0].card_uid == "AAAA"

    @pytest.mark.asyncio
    async def test_metadata_persisted_as_json(self, audit: AuditLogger):
        await audit.log(
            card_uid="META",
            decision="DENIED",
            reason="rate_limited",
            reader_type="mock",
            metadata={"attempts": 5, "window_s": 60},
        )
        rows = await audit.events_for_uid("META")
        assert len(rows) == 1
        assert '"attempts": 5' in (rows[0].metadata_json or "")

    @pytest.mark.asyncio
    async def test_recent_events_ordered_newest_first(self, audit: AuditLogger):
        for i in range(5):
            await audit.log(
                card_uid=f"CARD{i}",
                decision="GRANTED",
                reason="ok",
                reader_type="mock",
            )
            # Small spacing so timestamps are distinct.
            await asyncio.sleep(0.001)

        recent = await audit.recent_events(limit=3)
        assert [r.card_uid for r in recent] == ["CARD4", "CARD3", "CARD2"]

    @pytest.mark.asyncio
    async def test_events_for_uid_filters(self, audit: AuditLogger):
        for uid in ["X", "Y", "X", "Z", "X"]:
            await audit.log(
                card_uid=uid,
                decision="GRANTED",
                reason="ok",
                reader_type="mock",
            )
        rows = await audit.events_for_uid("X")
        assert len(rows) == 3
        assert all(r.card_uid == "X" for r in rows)


class TestSubscribers:
    @pytest.mark.asyncio
    async def test_subscriber_called(self, audit: AuditLogger):
        received: list[AccessEvent] = []

        async def cb(event: AccessEvent) -> None:
            received.append(event)

        audit.subscribe(cb)
        await audit.log(
            card_uid="SUB",
            decision="GRANTED",
            reason="ok",
            reader_type="mock",
        )
        assert len(received) == 1
        assert received[0].card_uid == "SUB"

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, audit: AuditLogger):
        a_calls, b_calls = [], []

        async def a(event: AccessEvent) -> None:
            a_calls.append(event)

        async def b(event: AccessEvent) -> None:
            b_calls.append(event)

        audit.subscribe(a)
        audit.subscribe(b)
        await audit.log(
            card_uid="BOTH",
            decision="DENIED",
            reason="x",
            reader_type="mock",
        )
        assert len(a_calls) == 1
        assert len(b_calls) == 1

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(self, audit: AuditLogger):
        calls = []

        async def cb(event: AccessEvent) -> None:
            calls.append(event)

        audit.subscribe(cb)
        audit.unsubscribe(cb)
        await audit.log(
            card_uid="X",
            decision="GRANTED",
            reason="ok",
            reader_type="mock",
        )
        assert calls == []

    @pytest.mark.asyncio
    async def test_subscriber_exception_does_not_break_others(
        self, audit: AuditLogger
    ):
        good_calls = []

        async def bad(event: AccessEvent) -> None:
            raise RuntimeError("simulated subscriber bug")

        async def good(event: AccessEvent) -> None:
            good_calls.append(event)

        audit.subscribe(bad)
        audit.subscribe(good)
        # Logging should not raise even though `bad` blows up.
        await audit.log(
            card_uid="X",
            decision="GRANTED",
            reason="ok",
            reader_type="mock",
        )
        assert len(good_calls) == 1


class TestAccessEvent:
    def test_to_dict_serializable(self):
        from datetime import datetime, timezone

        event = AccessEvent(
            timestamp=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            card_uid="DEAD",
            decision="GRANTED",
            reason="ok",
            reader_type="mock",
            user_id=42,
            metadata={"k": "v"},
        )
        d = event.to_dict()
        assert d["card_uid"] == "DEAD"
        assert d["user_id"] == 42
        assert d["metadata"] == {"k": "v"}
        assert d["timestamp"] == "2026-01-01T12:00:00+00:00"


class TestFailureResilience:
    @pytest.mark.asyncio
    async def test_persistence_failure_does_not_raise(self, audit: AuditLogger):
        """Closing the underlying DB simulates a write failure.

        The logger must swallow the exception and still broadcast the
        event to subscribers — the access decision (which already happened)
        must not be affected by audit failures.
        """
        received: list[AccessEvent] = []

        async def cb(event: AccessEvent) -> None:
            received.append(event)

        audit.subscribe(cb)
        await audit._db.close()  # simulate DB down

        event = await audit.log(
            card_uid="DOWN",
            decision="GRANTED",
            reason="ok",
            reader_type="mock",
        )
        assert event.card_uid == "DOWN"
        # Subscribers still fired even though persistence failed:
        assert len(received) == 1

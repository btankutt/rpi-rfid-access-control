"""Tests for the async SQLAlchemy database layer."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.database import (
    AuditLog,
    Card,
    Database,
    User,
    build_sqlite_url,
)


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    """Fresh on-disk SQLite DB per test with schema applied."""
    db_path = tmp_path / "test.db"
    database = Database(build_sqlite_url(str(db_path)))
    await database.init_schema()
    try:
        yield database
    finally:
        await database.close()


class TestUrlBuilder:
    def test_file_url(self):
        url = build_sqlite_url("/var/data/access.db")
        assert url == "sqlite+aiosqlite:////var/data/access.db"

    def test_memory_url(self):
        assert build_sqlite_url(":memory:") == "sqlite+aiosqlite:///:memory:"


class TestSchema:
    @pytest.mark.asyncio
    async def test_init_schema_idempotent(self, tmp_path: Path):
        """Calling init_schema twice must not raise."""
        db = Database(build_sqlite_url(str(tmp_path / "x.db")))
        await db.init_schema()
        await db.init_schema()
        await db.close()


class TestUserAndCardCRUD:
    @pytest.mark.asyncio
    async def test_insert_and_query_user(self, db: Database):
        async with db.session() as session:
            user = User(full_name="Alice Doe", role="user")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            assert user.id is not None
            assert user.created_at is not None
            assert user.active is True

        async with db.session() as session:
            result = await session.execute(
                select(User).where(User.full_name == "Alice Doe")
            )
            fetched = result.scalar_one()
            assert fetched.role == "user"

    @pytest.mark.asyncio
    async def test_card_uid_is_unique(self, db: Database):
        async with db.session() as session:
            user = User(full_name="Bob")
            session.add(user)
            await session.flush()

            session.add(Card(uid="ABCD1234", user_id=user.id))
            session.add(Card(uid="ABCD1234", user_id=user.id))
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_cascade_delete_user_removes_cards(self, db: Database):
        async with db.session() as session:
            user = User(full_name="Carol")
            session.add(user)
            await session.flush()
            session.add(Card(uid="C0001", user_id=user.id))
            session.add(Card(uid="C0002", user_id=user.id))
            await session.commit()
            user_id = user.id

        async with db.session() as session:
            user = await session.get(User, user_id)
            assert user is not None
            await session.delete(user)
            await session.commit()

        async with db.session() as session:
            result = await session.execute(
                select(Card).where(Card.user_id == user_id)
            )
            assert result.scalars().all() == []

    @pytest.mark.asyncio
    async def test_user_with_time_window(self, db: Database):
        async with db.session() as session:
            user = User(
                full_name="Dave",
                allowed_hours_start=_dt.time(9, 0),
                allowed_hours_end=_dt.time(18, 0),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            assert user.allowed_hours_start == _dt.time(9, 0)
            assert user.allowed_hours_end == _dt.time(18, 0)


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_insert_audit_event(self, db: Database):
        async with db.session() as session:
            event = AuditLog(
                card_uid="DEADBEEF",
                decision="GRANTED",
                reason="OK",
                reader_type="mock",
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)
            assert event.id is not None
            assert event.timestamp is not None

    @pytest.mark.asyncio
    async def test_audit_indexed_by_card_uid(self, db: Database):
        """Inserts many events; lookup by UID returns the right ones."""
        async with db.session() as session:
            for i in range(10):
                session.add(
                    AuditLog(
                        card_uid="AAAA" if i % 2 == 0 else "BBBB",
                        decision="GRANTED",
                        reason="ok",
                        reader_type="mock",
                    )
                )
            await session.commit()

        async with db.session() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.card_uid == "AAAA")
            )
            rows = result.scalars().all()
            assert len(rows) == 5

    @pytest.mark.asyncio
    async def test_audit_user_id_set_null_on_user_delete(self, db: Database):
        """When a user is deleted, the audit row's user_id becomes NULL.

        This preserves the audit trail even after the user is removed —
        critical for forensics.
        """
        async with db.session() as session:
            user = User(full_name="Eve")
            session.add(user)
            await session.flush()
            session.add(
                AuditLog(
                    card_uid="EVEE",
                    user_id=user.id,
                    decision="GRANTED",
                    reason="ok",
                    reader_type="mock",
                )
            )
            await session.commit()
            user_id = user.id
            await session.delete(user)
            await session.commit()

        async with db.session() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.card_uid == "EVEE")
            )
            row = result.scalar_one()
            assert row.user_id is None
            # The user is gone:
            assert await session.get(User, user_id) is None

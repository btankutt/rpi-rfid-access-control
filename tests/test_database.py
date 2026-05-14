"""Tests for the async database layer."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.database import (
    AccessLog,
    User,
    add_user,
    close_db,
    get_recent_logs,
    get_session,
    get_user_by_uid,
    init_db,
    init_engine,
    log_access_attempt,
)


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    """Fresh on-disk SQLite DB per test."""
    path = tmp_path / "test.db"
    init_engine(str(path))
    await init_db()
    yield
    await close_db()


class TestEngineLifecycle:
    @pytest.mark.asyncio
    async def test_get_session_without_init_raises(self):
        # Make sure we start from a clean state
        await close_db()
        with pytest.raises(RuntimeError, match="init_engine"):
            async with get_session():
                pass

    @pytest.mark.asyncio
    async def test_init_db_without_engine_raises(self):
        await close_db()
        with pytest.raises(RuntimeError, match="init_engine"):
            await init_db()

    @pytest.mark.asyncio
    async def test_close_db_idempotent(self, db):
        await close_db()
        await close_db()  # second close must not raise


class TestUserCRUD:
    @pytest.mark.asyncio
    async def test_add_and_query_user(self, db):
        user = await add_user(card_uid="AAAA", name="Alice")
        assert user.id is not None
        assert user.created_at is not None
        assert user.is_active is True

        fetched = await get_user_by_uid("AAAA")
        assert fetched is not None
        assert fetched.name == "Alice"
        assert fetched.role == "user"

    @pytest.mark.asyncio
    async def test_get_user_missing(self, db):
        assert await get_user_by_uid("NOPE") is None

    @pytest.mark.asyncio
    async def test_card_uid_unique(self, db):
        await add_user(card_uid="DUP", name="A")
        with pytest.raises(IntegrityError):
            await add_user(card_uid="DUP", name="B")

    @pytest.mark.asyncio
    async def test_role_persisted(self, db):
        await add_user(card_uid="ADM", name="Admin", role="admin")
        user = await get_user_by_uid("ADM")
        assert user is not None
        assert user.role == "admin"

    @pytest.mark.asyncio
    async def test_inactive_user(self, db):
        await add_user(card_uid="OFF", name="X", is_active=False)
        user = await get_user_by_uid("OFF")
        assert user is not None
        assert user.is_active is False


class TestAccessLog:
    @pytest.mark.asyncio
    async def test_log_granted(self, db):
        user = await add_user(card_uid="A", name="A")
        row = await log_access_attempt(
            card_uid="A", decision="GRANTED", reason="OK", user_id=user.id
        )
        assert row.id is not None
        assert row.timestamp is not None
        assert row.user_id == user.id

    @pytest.mark.asyncio
    async def test_log_denied_no_user(self, db):
        row = await log_access_attempt(
            card_uid="X", decision="DENIED", reason="UNKNOWN_CARD"
        )
        assert row.user_id is None

    @pytest.mark.asyncio
    async def test_get_recent_logs_descending(self, db):
        import asyncio

        for i in range(5):
            await log_access_attempt(
                card_uid=f"L{i}", decision="GRANTED", reason="ok"
            )
            await asyncio.sleep(0.001)
        rows = await get_recent_logs(limit=3)
        assert [r.card_uid for r in rows] == ["L4", "L3", "L2"]

    @pytest.mark.asyncio
    async def test_log_indexed_by_card_uid(self, db):
        for uid in ["X", "Y", "X", "Z", "X"]:
            await log_access_attempt(
                card_uid=uid, decision="GRANTED", reason="ok"
            )
        # Filter via the session directly to confirm the index is queryable.
        async with get_session() as session:
            result = await session.execute(
                select(AccessLog).where(AccessLog.card_uid == "X")
            )
            assert len(result.scalars().all()) == 3

    @pytest.mark.asyncio
    async def test_fk_set_null_on_user_delete(self, db):
        """When a user is deleted, the log's user_id becomes NULL.

        Preserves the audit trail after the user record is gone.
        """
        user = await add_user(card_uid="GONE", name="Eve")
        await log_access_attempt(
            card_uid="GONE", decision="GRANTED", reason="ok", user_id=user.id
        )
        async with get_session() as session:
            await session.delete(await session.get(User, user.id))
            await session.commit()
        async with get_session() as session:
            row = (
                await session.execute(
                    select(AccessLog).where(AccessLog.card_uid == "GONE")
                )
            ).scalar_one()
            assert row.user_id is None

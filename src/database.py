"""
Database layer: async SQLAlchemy 2.0 + aiosqlite.

The MVP keeps the schema deliberately compact:
- `User`: one row per cardholder, with `card_uid` directly on the row
  (a user has exactly one card in this design).
- `AccessLog`: append-only record of every access decision.

Engine and sessionmaker live as module-level singletons — call
`init_engine(path)` once at startup before issuing queries.
"""

from __future__ import annotations

import datetime as _dt
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    desc,
    event,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger(__name__)

Role = Literal["admin", "operator", "user"]
Decision = Literal["GRANTED", "DENIED"]


class Base(DeclarativeBase):
    """Common declarative base for all ORM models."""


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


class User(Base):
    """A cardholder. One card per user in the MVP schema."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_uid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"User(id={self.id}, name={self.name!r}, role={self.role})"


class AccessLog(Base):
    """Append-only record of every access decision."""

    __tablename__ = "access_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_uid: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[_dt.datetime] = mapped_column(
        DateTime, default=_utcnow, index=True
    )
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(120))
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    def __repr__(self) -> str:
        return (
            f"AccessLog(ts={self.timestamp.isoformat()}, "
            f"uid={self.card_uid}, decision={self.decision})"
        )


# =============================================================================
# Engine / session management
# =============================================================================

_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def _build_url(path: str) -> str:
    if path == ":memory:":
        return "sqlite+aiosqlite:///:memory:"
    return f"sqlite+aiosqlite:///{path}"


def init_engine(path: str) -> None:
    """Create the global engine and session factory.

    Idempotent across re-calls with the same path is NOT guaranteed —
    callers should call `close_db()` before re-initializing against a
    different database file.
    """
    global _engine, _sessionmaker
    _engine = create_async_engine(_build_url(path), echo=False, future=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)

    # SQLite ignores foreign-key constraints unless explicitly enabled
    # on each connection. Without this, ON DELETE SET NULL is a no-op.
    @event.listens_for(_engine.sync_engine, "connect")
    def _enable_sqlite_fk(dbapi_conn, _):  # noqa: ARG001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    logger.info("Database engine initialized for %s", path)


async def init_db() -> None:
    """Create all tables. Safe to call on every startup."""
    if _engine is None:
        raise RuntimeError("init_engine() must be called before init_db()")
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema initialized")


async def close_db() -> None:
    """Dispose the engine. Idempotent — calling on a closed engine is fine."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
        logger.info("Database engine closed")


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a short-lived AsyncSession."""
    if _sessionmaker is None:
        raise RuntimeError("init_engine() must be called before get_session()")
    async with _sessionmaker() as session:
        yield session


# =============================================================================
# CRUD operations
# =============================================================================


async def add_user(
    card_uid: str,
    name: str,
    role: Role = "user",
    is_active: bool = True,
) -> User:
    """Create a User row and return it with its assigned id."""
    async with get_session() as session:
        user = User(card_uid=card_uid, name=name, role=role, is_active=is_active)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def get_user_by_uid(card_uid: str) -> Optional[User]:
    """Look up a user by RFID UID, or None if not found.

    Returns the row regardless of `is_active` — the caller is responsible
    for the policy check.
    """
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.card_uid == card_uid)
        )
        return result.scalar_one_or_none()


async def log_access_attempt(
    card_uid: str,
    decision: Decision,
    reason: str,
    user_id: Optional[int] = None,
) -> AccessLog:
    """Append a row to the access log."""
    async with get_session() as session:
        row = AccessLog(
            card_uid=card_uid,
            decision=decision,
            reason=reason,
            user_id=user_id,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def get_recent_logs(limit: int = 100) -> list[AccessLog]:
    """Return the most recent N log rows, newest first."""
    async with get_session() as session:
        result = await session.execute(
            select(AccessLog).order_by(desc(AccessLog.timestamp)).limit(limit)
        )
        return list(result.scalars().all())

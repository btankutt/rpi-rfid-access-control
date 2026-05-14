"""
Database layer: async SQLAlchemy 2.0 + aiosqlite.

Models:
- User: a cardholder (the physical person who carries an RFID card).
- Card: an RFID card associated with a User. A user may own multiple
  cards (e.g., a primary plus a backup).
- AuditLog: append-only record of every access decision.

The `Database` class encapsulates the engine + session factory so the
rest of the application doesn't need to know about SQLAlchemy internals.
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
    Time,
    event,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

logger = logging.getLogger(__name__)

Role = Literal["admin", "operator", "user"]
Decision = Literal["GRANTED", "DENIED"]


class Base(DeclarativeBase):
    """Common declarative base for all ORM models."""


def _utcnow() -> _dt.datetime:
    """Timezone-aware UTC now; used as a column default."""
    return _dt.datetime.now(_dt.timezone.utc)


class User(Base):
    """A cardholder.

    A `User` represents the physical person whose access is being
    controlled — separate from the web-admin login (which is configured
    via environment variables, not stored here).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="user")
    email: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Time-window restriction. Both NULL = no restriction.
    allowed_hours_start: Mapped[Optional[_dt.time]] = mapped_column(
        Time, default=None
    )
    allowed_hours_end: Mapped[Optional[_dt.time]] = mapped_column(
        Time, default=None
    )

    # Optional expiry — useful for contractors, visitors, etc.
    expires_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime, default=None
    )
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_utcnow)
    notes: Mapped[Optional[str]] = mapped_column(String(500), default=None)

    cards: Mapped[list["Card"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"User(id={self.id}, name={self.full_name!r}, role={self.role})"


class Card(Base):
    """An RFID card belonging to a User."""

    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    label: Mapped[Optional[str]] = mapped_column(String(60), default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="cards", lazy="joined")

    def __repr__(self) -> str:
        return f"Card(uid={self.uid}, user_id={self.user_id}, active={self.active})"


class AuditLog(Base):
    """Append-only record of every access decision.

    Rows in this table are never updated or deleted by the application —
    only inserted. The web UI exposes read-only access. This guarantee
    is essential for security audits.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[_dt.datetime] = mapped_column(
        DateTime, default=_utcnow, index=True
    )
    card_uid: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(120))
    reader_type: Mapped[str] = mapped_column(String(20))
    metadata_json: Mapped[Optional[str]] = mapped_column(
        String(2000), default=None
    )

    def __repr__(self) -> str:
        return (
            f"AuditLog(ts={self.timestamp.isoformat()}, "
            f"uid={self.card_uid}, decision={self.decision})"
        )


# =============================================================================
# Engine / session management
# =============================================================================


def build_sqlite_url(path: str) -> str:
    """Build an aiosqlite URL from a filesystem path.

    Use the literal string ``:memory:`` for an in-memory database (mainly
    useful for tests that don't need persistence across sessions).
    """
    if path == ":memory:":
        return "sqlite+aiosqlite:///:memory:"
    return f"sqlite+aiosqlite:///{path}"


class Database:
    """Owns the AsyncEngine and produces short-lived AsyncSession objects.

    The application creates exactly one `Database` instance at startup
    and calls `init_schema()` once before serving traffic. Components
    that need DB access receive the `Database` and use `session()` as
    an async context manager.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._engine: AsyncEngine = create_async_engine(
            url, echo=False, future=True
        )
        self._sessionmaker = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )

        # SQLite does not enforce foreign-key constraints unless the
        # `foreign_keys` pragma is enabled on every connection. Without
        # this, ON DELETE CASCADE / SET NULL clauses are silently ignored.
        if url.startswith("sqlite"):

            @event.listens_for(self._engine.sync_engine, "connect")
            def _enable_sqlite_fk(dbapi_conn, _):  # noqa: ARG001
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        logger.info("Database engine created for %s", url)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def url(self) -> str:
        return self._url

    async def init_schema(self) -> None:
        """Create all tables if they don't already exist.

        Idempotent — safe to call on every startup. For schema changes
        in production deployments, use Alembic or a migration script;
        this method only handles the initial create.
        """
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database schema initialized")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a short-lived AsyncSession; commit on success."""
        async with self._sessionmaker() as session:
            yield session

    async def close(self) -> None:
        """Dispose the engine — call on shutdown."""
        await self._engine.dispose()
        logger.info("Database engine disposed")

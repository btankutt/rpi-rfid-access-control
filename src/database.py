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
    desc,
    event,
    select,
)
from sqlalchemy.orm import selectinload
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
from typing import Any

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


# =============================================================================
# Module-level CRUD helpers
# =============================================================================
# These are thin async wrappers over the ORM so scripts and one-off
# admin tasks don't need to construct sessions or write SELECTs. The
# business-logic layer (AccessManager, AuditLogger, web routes) talks
# to the ORM directly — use the helpers below from places where the
# extra ceremony would be noise.


async def add_cardholder(
    db: Database,
    *,
    full_name: str,
    role: str = "user",
    email: Optional[str] = None,
    active: bool = True,
    expires_at: Optional[_dt.datetime] = None,
    notes: Optional[str] = None,
) -> User:
    """Create a User row and return it with its assigned id."""
    async with db.session() as session:
        user = User(
            full_name=full_name,
            role=role,
            email=email,
            active=active,
            expires_at=expires_at,
            notes=notes,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def assign_card(
    db: Database,
    *,
    user_id: int,
    uid: str,
    label: Optional[str] = None,
    active: bool = True,
) -> Card:
    """Attach an RFID card to an existing User."""
    async with db.session() as session:
        card = Card(user_id=user_id, uid=uid, label=label, active=active)
        session.add(card)
        await session.commit()
        await session.refresh(card)
        return card


async def get_user_by_card_uid(db: Database, uid: str) -> Optional[User]:
    """Look up the cardholder for an RFID UID, or None if not found.

    Returns the `User` even if the card or user is deactivated — the
    caller is responsible for policy checks. This mirrors how
    `AccessManager` separates "lookup" from "authorize".
    """
    async with db.session() as session:
        result = await session.execute(
            select(Card)
            .where(Card.uid == uid)
            .options(selectinload(Card.user))
        )
        card = result.scalar_one_or_none()
        return card.user if card is not None else None


async def recent_access_logs(
    db: Database, limit: int = 100
) -> list[AuditLog]:
    """Return the most recent `limit` audit-log rows, newest first."""
    async with db.session() as session:
        result = await session.execute(
            select(AuditLog).order_by(desc(AuditLog.timestamp)).limit(limit)
        )
        return list(result.scalars().all())


async def log_access_attempt(
    db: Database,
    *,
    card_uid: str,
    decision: Decision,
    reason: str,
    reader_type: str = "unknown",
    user_id: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> AuditLog:
    """Append an AuditLog row directly, without firing subscribers.

    Used by maintenance scripts and recovery tooling. Production code
    paths should write through `AuditLogger.log()` instead so live
    dashboards see the event.
    """
    import json as _json

    async with db.session() as session:
        row = AuditLog(
            card_uid=card_uid,
            decision=decision,
            reason=reason,
            reader_type=reader_type,
            user_id=user_id,
            metadata_json=_json.dumps(metadata) if metadata else None,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row

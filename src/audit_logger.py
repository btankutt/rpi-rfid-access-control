"""
Audit logger — appends every access decision to the database.

The logger MUST NOT block an access decision. If the database is
temporarily unavailable, the door still opens (or stays closed)
based on the AccessManager's verdict, and the audit write is logged
to stderr instead. The trade-off favors physical safety over forensic
completeness; in practice DB writes to a local SQLite file should
never fail except in catastrophic conditions.

The logger also dispatches events to any subscribers — typically a
WebSocket broadcaster that forwards real-time events to admin dashboards.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import desc, select

from src.database import AuditLog, Database, Decision

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccessEvent:
    """Immutable representation of an access decision.

    Exposed to subscribers so they don't need to import ORM models.
    """

    timestamp: datetime
    card_uid: str
    decision: Decision
    reason: str
    reader_type: str
    user_id: Optional[int] = None
    metadata: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form for WebSocket broadcast."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "card_uid": self.card_uid,
            "decision": self.decision,
            "reason": self.reason,
            "reader_type": self.reader_type,
            "user_id": self.user_id,
            "metadata": self.metadata,
        }


Subscriber = Callable[[AccessEvent], Awaitable[None]]


class AuditLogger:
    """Persistent, append-only event log with pub/sub for real-time UIs."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._subscribers: list[Subscriber] = []

    def subscribe(self, callback: Subscriber) -> None:
        """Register an async callback invoked for every logged event.

        Used by the WebSocket layer to push events to connected admins.
        Subscriber exceptions are caught and logged — they never affect
        other subscribers or the access decision.
        """
        self._subscribers.append(callback)
        logger.debug("Subscriber registered; total=%d", len(self._subscribers))

    def unsubscribe(self, callback: Subscriber) -> None:
        try:
            self._subscribers.remove(callback)
        except ValueError:
            logger.warning("Tried to unsubscribe a callback that wasn't registered")

    async def log(
        self,
        *,
        card_uid: str,
        decision: Decision,
        reason: str,
        reader_type: str,
        user_id: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> AccessEvent:
        """Persist an event and broadcast it to subscribers.

        Returns the AccessEvent so callers can correlate with downstream
        actions. A persistence failure does not raise; the event is still
        broadcast (so admins see the attempt in real time) and the error
        is logged with stack trace.
        """
        event = AccessEvent(
            timestamp=datetime.now(timezone.utc),
            card_uid=card_uid,
            decision=decision,
            reason=reason,
            reader_type=reader_type,
            user_id=user_id,
            metadata=metadata,
        )

        await self._persist(event)
        await self._broadcast(event)
        return event

    async def _persist(self, event: AccessEvent) -> None:
        try:
            async with self._db.session() as session:
                session.add(
                    AuditLog(
                        timestamp=event.timestamp,
                        card_uid=event.card_uid,
                        user_id=event.user_id,
                        decision=event.decision,
                        reason=event.reason,
                        reader_type=event.reader_type,
                        metadata_json=(
                            json.dumps(event.metadata) if event.metadata else None
                        ),
                    )
                )
                await session.commit()
        except Exception:
            logger.exception(
                "Failed to persist audit event (uid=%s decision=%s) — "
                "physical access decision NOT affected",
                event.card_uid,
                event.decision,
            )

    async def _broadcast(self, event: AccessEvent) -> None:
        if not self._subscribers:
            return
        # Run subscribers concurrently; capture exceptions per-subscriber.
        results = await asyncio.gather(
            *(self._safe_call(s, event) for s in self._subscribers),
            return_exceptions=False,
        )
        # `_safe_call` already swallows exceptions, but be defensive:
        del results

    async def _safe_call(self, callback: Subscriber, event: AccessEvent) -> None:
        try:
            await callback(event)
        except Exception:
            logger.exception("Audit subscriber raised — event delivery skipped")

    async def recent_events(self, limit: int = 100) -> list[AuditLog]:
        """Return the most recent N events, newest first."""
        async with self._db.session() as session:
            result = await session.execute(
                select(AuditLog).order_by(desc(AuditLog.timestamp)).limit(limit)
            )
            return list(result.scalars().all())

    async def events_for_uid(self, card_uid: str, limit: int = 100) -> list[AuditLog]:
        """Return recent events for a specific card UID, newest first."""
        async with self._db.session() as session:
            result = await session.execute(
                select(AuditLog)
                .where(AuditLog.card_uid == card_uid)
                .order_by(desc(AuditLog.timestamp))
                .limit(limit)
            )
            return list(result.scalars().all())

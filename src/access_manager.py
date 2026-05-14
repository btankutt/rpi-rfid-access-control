"""
Access manager — the authorization engine.

Receives `CardRead` events from the reader, queries the database for the
matching card and its owner, applies all policy checks, then either
triggers the door controller or records a denial. Every decision is
written to the audit log.

This is intentionally the only place that ties hardware (door) to the
domain (users/cards) — keeping the interaction surface narrow makes
reasoning about correctness much easier than spreading the checks
across multiple modules.
"""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.audit_logger import AuditLogger
from src.database import Card, Database, User
from src.door_controller import DoorController
from src.rate_limiter import RateLimiter
from src.readers import CardRead

logger = logging.getLogger(__name__)


# =============================================================================
# Decision codes
# =============================================================================
# Stable, machine-readable strings so the audit log is greppable and
# reports can group by reason without parsing free text.

REASON_GRANTED = "OK"
REASON_UNKNOWN_CARD = "UNKNOWN_CARD"
REASON_CARD_DISABLED = "CARD_DISABLED"
REASON_USER_INACTIVE = "USER_INACTIVE"
REASON_EXPIRED = "EXPIRED"
REASON_OUT_OF_HOURS = "OUT_OF_HOURS"
REASON_RATE_LIMITED = "RATE_LIMITED"


@dataclass(frozen=True)
class AccessDecision:
    """Result of an authorization check.

    The `reason` field uses one of the REASON_* constants above so
    consumers can branch on a stable code rather than parsing a message.
    """

    granted: bool
    reason: str
    user_id: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# AccessManager
# =============================================================================


class AccessManager:
    """Authorizes card reads and orchestrates door + audit + rate limiting.

    Args:
        database: For looking up cards/users.
        door: The hardware (or mock) door controller.
        audit: Logger for the audit trail and event broadcast.
        rate_limiter: Tracks consecutive failed reads per UID.
        door_open_duration: Seconds the door stays unlocked on a grant.
        time_source: Returns the current local time as a `datetime`. Override
            in tests to make time-window checks deterministic.
    """

    def __init__(
        self,
        database: Database,
        door: DoorController,
        audit: AuditLogger,
        rate_limiter: RateLimiter,
        door_open_duration: float = 5.0,
        time_source=lambda: _dt.datetime.now(_dt.timezone.utc),
    ) -> None:
        self._db = database
        self._door = door
        self._audit = audit
        self._rate_limiter = rate_limiter
        self._door_open_duration = door_open_duration
        self._now = time_source

    async def handle_card_read(self, card_read: CardRead) -> AccessDecision:
        """Run all policy checks and act on the decision.

        Side effects:
        - On grant: opens the door (fires-and-returns; the door manages
          its own pulse duration internally).
        - Always: records the decision in the audit log.
        - On deny: increments the rate-limiter counter for this UID.
        - On grant: clears the rate-limiter counter.
        """
        uid = card_read.uid

        # --- Rate limit ---------------------------------------------------
        if await self._rate_limiter.is_locked_out(uid):
            remaining = await self._rate_limiter.time_until_unlock(uid)
            decision = AccessDecision(
                granted=False,
                reason=REASON_RATE_LIMITED,
                metadata={"unlock_in_seconds": remaining},
            )
            await self._record(card_read, decision)
            return decision

        # --- Database lookup ----------------------------------------------
        card = await self._lookup_card(uid)
        if card is None:
            decision = AccessDecision(granted=False, reason=REASON_UNKNOWN_CARD)
            await self._rate_limiter.record_failure(uid)
            await self._record(card_read, decision)
            return decision

        # --- Card / user state checks ------------------------------------
        if not card.active:
            decision = AccessDecision(
                granted=False, reason=REASON_CARD_DISABLED, user_id=card.user_id
            )
            await self._record(card_read, decision)
            return decision

        user = card.user
        if not user.active:
            decision = AccessDecision(
                granted=False, reason=REASON_USER_INACTIVE, user_id=user.id
            )
            await self._record(card_read, decision)
            return decision

        if self._is_expired(user):
            decision = AccessDecision(
                granted=False, reason=REASON_EXPIRED, user_id=user.id
            )
            await self._record(card_read, decision)
            return decision

        if not self._within_allowed_hours(user):
            decision = AccessDecision(
                granted=False,
                reason=REASON_OUT_OF_HOURS,
                user_id=user.id,
                metadata={
                    "allowed_start": (
                        user.allowed_hours_start.isoformat()
                        if user.allowed_hours_start
                        else None
                    ),
                    "allowed_end": (
                        user.allowed_hours_end.isoformat()
                        if user.allowed_hours_end
                        else None
                    ),
                },
            )
            await self._record(card_read, decision)
            return decision

        # --- Granted ------------------------------------------------------
        decision = AccessDecision(
            granted=True,
            reason=REASON_GRANTED,
            user_id=user.id,
            metadata={"role": user.role},
        )
        await self._rate_limiter.record_success(uid)
        await self._record(card_read, decision)

        # Open the door last, so the audit log is durably written first.
        # `open()` blocks for the pulse duration; we await it so the
        # caller's reader loop pauses while the door is unlocked.
        try:
            await self._door.open(self._door_open_duration)
        except Exception:
            logger.exception("Door open failed after authorization granted")

        return decision

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _lookup_card(self, uid: str) -> Optional[Card]:
        async with self._db.session() as session:
            result = await session.execute(
                select(Card)
                .where(Card.uid == uid)
                .options(selectinload(Card.user))
            )
            return result.scalar_one_or_none()

    def _is_expired(self, user: User) -> bool:
        if user.expires_at is None:
            return False
        now = self._now()
        # Coerce to aware for comparison if needed
        expires_at = user.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=_dt.timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=_dt.timezone.utc)
        return now >= expires_at

    def _within_allowed_hours(self, user: User) -> bool:
        """True if no window is set, or if `now` falls inside it.

        Handles wrap-around windows (e.g. 22:00 → 06:00) by treating them
        as "either side of midnight".
        """
        start = user.allowed_hours_start
        end = user.allowed_hours_end
        if start is None and end is None:
            return True
        if start is None or end is None:
            # Half-configured window is treated as "no restriction" but
            # logged so an admin can correct the record.
            logger.warning(
                "User %d has half-configured allowed hours (start=%s end=%s); "
                "treating as unrestricted",
                user.id,
                start,
                end,
            )
            return True

        now_t = self._now().time()
        if start <= end:
            return start <= now_t < end
        # Wrap-around: e.g. 22:00 → 06:00
        return now_t >= start or now_t < end

    async def _record(self, card_read: CardRead, decision: AccessDecision) -> None:
        decision_code = "GRANTED" if decision.granted else "DENIED"
        await self._audit.log(
            card_uid=card_read.uid,
            decision=decision_code,
            reason=decision.reason,
            reader_type=card_read.reader_type,
            user_id=decision.user_id,
            metadata=decision.metadata or None,
        )

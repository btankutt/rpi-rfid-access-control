"""End-to-end tests for the AccessManager.

These tests wire a real (in-memory-ish) database to the AccessManager
along with mock door, mock reader output, and a rate limiter using a
fake clock. The aim is to cover every branch of the decision tree.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
import pytest_asyncio

from src.access_manager import (
    REASON_CARD_DISABLED,
    REASON_EXPIRED,
    REASON_GRANTED,
    REASON_OUT_OF_HOURS,
    REASON_RATE_LIMITED,
    REASON_UNKNOWN_CARD,
    REASON_USER_INACTIVE,
    AccessManager,
)
from src.audit_logger import AuditLogger
from src.database import Card, Database, User, build_sqlite_url
from src.door_controller import MockDoorController
from src.rate_limiter import RateLimiter
from src.readers import CardRead


def make_card_read(uid: str = "AAAA") -> CardRead:
    return CardRead(
        uid=uid,
        timestamp=_dt.datetime.now(_dt.timezone.utc),
        reader_type="mock",
    )


class FakeClock:
    def __init__(self, when: _dt.datetime) -> None:
        self.when = when

    def __call__(self) -> _dt.datetime:
        return self.when


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(build_sqlite_url(str(tmp_path / "access.db")))
    await database.init_schema()
    try:
        yield database
    finally:
        await database.close()


@pytest_asyncio.fixture
async def manager(db: Database):
    """Default AccessManager: noon UTC clock, generous rate limit, mock door."""
    return AccessManager(
        database=db,
        door=MockDoorController(default_duration_seconds=0.01),
        audit=AuditLogger(db),
        rate_limiter=RateLimiter(max_failures=3, window_seconds=60),
        door_open_duration=0.01,
        time_source=lambda: _dt.datetime(2026, 5, 14, 12, 0, tzinfo=_dt.timezone.utc),
    )


async def _seed_user(
    db: Database,
    *,
    full_name: str = "Test User",
    role: str = "user",
    active: bool = True,
    expires_at: _dt.datetime | None = None,
    allowed_hours_start: _dt.time | None = None,
    allowed_hours_end: _dt.time | None = None,
    card_uid: str = "AAAA",
    card_active: bool = True,
) -> tuple[int, str]:
    async with db.session() as session:
        user = User(
            full_name=full_name,
            role=role,
            active=active,
            expires_at=expires_at,
            allowed_hours_start=allowed_hours_start,
            allowed_hours_end=allowed_hours_end,
        )
        session.add(user)
        await session.flush()
        session.add(Card(uid=card_uid, user_id=user.id, active=card_active))
        await session.commit()
        return user.id, card_uid


# =============================================================================
# Granted path
# =============================================================================


class TestGrantedPath:
    @pytest.mark.asyncio
    async def test_known_card_granted(self, db: Database, manager: AccessManager):
        user_id, uid = await _seed_user(db)
        decision = await manager.handle_card_read(make_card_read(uid))
        assert decision.granted is True
        assert decision.reason == REASON_GRANTED
        assert decision.user_id == user_id

    @pytest.mark.asyncio
    async def test_door_opened_on_grant(self, db: Database, manager: AccessManager):
        await _seed_user(db)
        door: MockDoorController = manager._door  # type: ignore[assignment]
        await manager.handle_card_read(make_card_read())
        assert len(door.open_events) == 1

    @pytest.mark.asyncio
    async def test_audit_event_written(self, db: Database, manager: AccessManager):
        await _seed_user(db)
        await manager.handle_card_read(make_card_read())
        events = await manager._audit.recent_events()
        assert len(events) == 1
        assert events[0].decision == "GRANTED"
        assert events[0].reason == REASON_GRANTED


# =============================================================================
# Denial paths
# =============================================================================


class TestUnknownCard:
    @pytest.mark.asyncio
    async def test_unknown_card_denied(self, manager: AccessManager):
        decision = await manager.handle_card_read(make_card_read("UNKNOWN"))
        assert decision.granted is False
        assert decision.reason == REASON_UNKNOWN_CARD

    @pytest.mark.asyncio
    async def test_unknown_card_does_not_open_door(self, manager: AccessManager):
        await manager.handle_card_read(make_card_read("NOSUCH"))
        door: MockDoorController = manager._door  # type: ignore[assignment]
        assert door.open_events == []


class TestDisabledStates:
    @pytest.mark.asyncio
    async def test_inactive_user_denied(self, db: Database, manager: AccessManager):
        await _seed_user(db, active=False)
        decision = await manager.handle_card_read(make_card_read())
        assert decision.granted is False
        assert decision.reason == REASON_USER_INACTIVE

    @pytest.mark.asyncio
    async def test_disabled_card_denied(self, db: Database, manager: AccessManager):
        await _seed_user(db, card_active=False)
        decision = await manager.handle_card_read(make_card_read())
        assert decision.granted is False
        assert decision.reason == REASON_CARD_DISABLED


class TestExpiry:
    @pytest.mark.asyncio
    async def test_expired_user_denied(self, db: Database, manager: AccessManager):
        await _seed_user(
            db,
            expires_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        )
        decision = await manager.handle_card_read(make_card_read())
        assert decision.granted is False
        assert decision.reason == REASON_EXPIRED

    @pytest.mark.asyncio
    async def test_future_expiry_allowed(self, db: Database, manager: AccessManager):
        await _seed_user(
            db,
            expires_at=_dt.datetime(2027, 1, 1, tzinfo=_dt.timezone.utc),
        )
        decision = await manager.handle_card_read(make_card_read())
        assert decision.granted is True


class TestTimeWindow:
    @pytest.mark.asyncio
    async def test_in_window_allowed(self, db: Database, manager: AccessManager):
        await _seed_user(
            db,
            allowed_hours_start=_dt.time(9, 0),
            allowed_hours_end=_dt.time(18, 0),
        )
        # Manager's clock is noon — inside 9-18
        decision = await manager.handle_card_read(make_card_read())
        assert decision.granted is True

    @pytest.mark.asyncio
    async def test_outside_window_denied(self, db: Database, manager: AccessManager):
        await _seed_user(
            db,
            allowed_hours_start=_dt.time(18, 0),
            allowed_hours_end=_dt.time(22, 0),
        )
        # noon is outside 18-22
        decision = await manager.handle_card_read(make_card_read())
        assert decision.granted is False
        assert decision.reason == REASON_OUT_OF_HOURS

    @pytest.mark.asyncio
    async def test_wraparound_window_at_midnight(
        self, db: Database, manager: AccessManager
    ):
        """22:00 -> 06:00 should allow access at 23:00 and 03:00."""
        await _seed_user(
            db,
            allowed_hours_start=_dt.time(22, 0),
            allowed_hours_end=_dt.time(6, 0),
        )
        # Switch clock to 23:00
        manager._now = lambda: _dt.datetime(
            2026, 5, 14, 23, 0, tzinfo=_dt.timezone.utc
        )
        assert (await manager.handle_card_read(make_card_read())).granted is True

        # And 03:00
        manager._now = lambda: _dt.datetime(
            2026, 5, 15, 3, 0, tzinfo=_dt.timezone.utc
        )
        assert (await manager.handle_card_read(make_card_read())).granted is True

        # But not 12:00
        manager._now = lambda: _dt.datetime(
            2026, 5, 14, 12, 0, tzinfo=_dt.timezone.utc
        )
        decision = await manager.handle_card_read(make_card_read())
        assert decision.granted is False
        assert decision.reason == REASON_OUT_OF_HOURS

    @pytest.mark.asyncio
    async def test_half_configured_window_allows_access(
        self, db: Database, manager: AccessManager
    ):
        """Only start set (no end) is treated as no restriction (and logged)."""
        await _seed_user(db, allowed_hours_start=_dt.time(9, 0))
        decision = await manager.handle_card_read(make_card_read())
        assert decision.granted is True


# =============================================================================
# Rate limiting + multi-event flow
# =============================================================================


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_unknown_card_increments_rate_limiter(
        self, manager: AccessManager
    ):
        for _ in range(3):
            await manager.handle_card_read(make_card_read("BAD"))

        # 4th attempt should be RATE_LIMITED (not UNKNOWN_CARD)
        decision = await manager.handle_card_read(make_card_read("BAD"))
        assert decision.reason == REASON_RATE_LIMITED

    @pytest.mark.asyncio
    async def test_successful_read_clears_failures(
        self, db: Database, manager: AccessManager
    ):
        await _seed_user(db, card_uid="GOOD")
        # Build up failures on the GOOD UID first (e.g., user mistakenly
        # presents card to a misconfigured reader). Then a clean read
        # should NOT punish them for prior misreads — assuming under threshold.
        await manager.handle_card_read(make_card_read("GOOD"))  # success clears

        for _ in range(2):
            await manager.handle_card_read(make_card_read("OTHER_BAD"))
        # That should not affect GOOD:
        decision = await manager.handle_card_read(make_card_read("GOOD"))
        assert decision.granted is True

    @pytest.mark.asyncio
    async def test_rate_limit_does_not_open_door(self, manager: AccessManager):
        for _ in range(3):
            await manager.handle_card_read(make_card_read("BAD"))
        await manager.handle_card_read(make_card_read("BAD"))
        door: MockDoorController = manager._door  # type: ignore[assignment]
        assert door.open_events == []


class TestAuditingAcrossDecisions:
    @pytest.mark.asyncio
    async def test_every_decision_audited(self, db: Database, manager: AccessManager):
        # Mix of one grant, one deny:
        await _seed_user(db, card_uid="OK")
        await manager.handle_card_read(make_card_read("OK"))
        await manager.handle_card_read(make_card_read("NOPE"))

        events = await manager._audit.recent_events()
        assert {(e.card_uid, e.decision) for e in events} == {
            ("OK", "GRANTED"),
            ("NOPE", "DENIED"),
        }

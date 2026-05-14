"""Tests for the sliding-window rate limiter."""

from __future__ import annotations

import asyncio

import pytest

from src.rate_limiter import RateLimiter


class FakeClock:
    """Manually advanced monotonic clock for deterministic tests."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def limiter(clock: FakeClock) -> RateLimiter:
    return RateLimiter(max_failures=3, window_seconds=60, time_source=clock)


class TestConstructor:
    def test_zero_failures_rejected(self):
        with pytest.raises(ValueError):
            RateLimiter(max_failures=0, window_seconds=60)

    def test_zero_window_rejected(self):
        with pytest.raises(ValueError):
            RateLimiter(max_failures=3, window_seconds=0)

    def test_negative_window_rejected(self):
        with pytest.raises(ValueError):
            RateLimiter(max_failures=3, window_seconds=-1)


class TestLockout:
    @pytest.mark.asyncio
    async def test_unlocked_by_default(self, limiter: RateLimiter):
        assert await limiter.is_locked_out("AAAA") is False

    @pytest.mark.asyncio
    async def test_locks_after_threshold(self, limiter: RateLimiter):
        for _ in range(2):
            await limiter.record_failure("UID")
            assert await limiter.is_locked_out("UID") is False

        await limiter.record_failure("UID")  # third failure -> locked
        assert await limiter.is_locked_out("UID") is True

    @pytest.mark.asyncio
    async def test_lockout_is_per_uid(self, limiter: RateLimiter):
        for _ in range(3):
            await limiter.record_failure("A")
        assert await limiter.is_locked_out("A") is True
        assert await limiter.is_locked_out("B") is False

    @pytest.mark.asyncio
    async def test_window_expires(self, limiter: RateLimiter, clock: FakeClock):
        for _ in range(3):
            await limiter.record_failure("U")
        assert await limiter.is_locked_out("U") is True

        clock.advance(61)  # one second past window
        assert await limiter.is_locked_out("U") is False

    @pytest.mark.asyncio
    async def test_partial_window_decay(
        self, limiter: RateLimiter, clock: FakeClock
    ):
        """Failures spread across the window edge stay locked until the
        oldest one ages out."""
        await limiter.record_failure("U")
        clock.advance(30)
        await limiter.record_failure("U")
        await limiter.record_failure("U")
        assert await limiter.is_locked_out("U") is True

        clock.advance(31)  # oldest is now 61s old -> drops out
        # Two failures remain within window -> below threshold
        assert await limiter.is_locked_out("U") is False


class TestTimeUntilUnlock:
    @pytest.mark.asyncio
    async def test_none_when_not_locked(self, limiter: RateLimiter):
        assert await limiter.time_until_unlock("X") is None
        await limiter.record_failure("X")
        assert await limiter.time_until_unlock("X") is None

    @pytest.mark.asyncio
    async def test_countdown(self, limiter: RateLimiter, clock: FakeClock):
        await limiter.record_failure("X")  # t=0
        clock.advance(10)
        await limiter.record_failure("X")  # t=10
        clock.advance(10)
        await limiter.record_failure("X")  # t=20, now locked
        # Oldest failure was at t=0; unlock at t=60. Currently t=20.
        remaining = await limiter.time_until_unlock("X")
        assert remaining == pytest.approx(40.0)

        clock.advance(40)  # at t=60, oldest expires this exact moment
        assert await limiter.is_locked_out("X") is False


class TestRecordSuccess:
    @pytest.mark.asyncio
    async def test_success_clears_history(self, limiter: RateLimiter):
        for _ in range(2):
            await limiter.record_failure("U")
        await limiter.record_success("U")

        # We should now tolerate the full threshold again
        for _ in range(2):
            await limiter.record_failure("U")
            assert await limiter.is_locked_out("U") is False

    @pytest.mark.asyncio
    async def test_success_on_unknown_uid_is_noop(self, limiter: RateLimiter):
        # Should not raise even if there's no history
        await limiter.record_success("NEVER_SEEN")


class TestReset:
    @pytest.mark.asyncio
    async def test_reset_clears_all_uids(self, limiter: RateLimiter):
        for uid in ["A", "B", "C"]:
            for _ in range(3):
                await limiter.record_failure(uid)
        for uid in ["A", "B", "C"]:
            assert await limiter.is_locked_out(uid) is True

        await limiter.reset()

        for uid in ["A", "B", "C"]:
            assert await limiter.is_locked_out(uid) is False


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_failures_counted_correctly(
        self, limiter: RateLimiter
    ):
        """Many coroutines hammering the same UID must agree on the count."""
        await asyncio.gather(*(limiter.record_failure("U") for _ in range(50)))
        # After 50 failures (way over threshold of 3), must be locked
        assert await limiter.is_locked_out("U") is True

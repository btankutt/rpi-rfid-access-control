"""
Rate limiter — brute-force protection for card readers.

After N failed attempts within M seconds for the same card UID, that
UID is locked out for the remainder of the window. A successful read
clears the failure history.

State is held in-memory (a single Raspberry Pi handles one door, so
no cross-process state is needed). For multi-door fleet deployments,
a shared Redis-backed limiter would replace this; the interface
remains the same.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """Sliding-window rate limiter keyed by card UID.

    Args:
        max_failures: Number of failed reads tolerated within `window_seconds`.
        window_seconds: Length of the sliding window in seconds.
        time_source: Monotonic clock callable; override in tests for a fake
            clock. Must be monotonic (never decreasing) — wall-clock time is
            not appropriate because NTP jumps can break the window.
    """

    def __init__(
        self,
        max_failures: int,
        window_seconds: float,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_failures <= 0:
            raise ValueError("max_failures must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")

        self._max_failures = max_failures
        self._window = window_seconds
        self._now = time_source
        self._failures: Dict[str, Deque[float]] = {}
        self._lock = asyncio.Lock()

    @property
    def max_failures(self) -> int:
        return self._max_failures

    @property
    def window_seconds(self) -> float:
        return self._window

    async def record_failure(self, uid: str) -> None:
        """Record a failed read for this UID."""
        now = self._now()
        async with self._lock:
            history = self._failures.setdefault(uid, deque())
            history.append(now)
            self._prune(history, now)
            logger.debug(
                "Recorded failure for %s; %d in window (limit=%d)",
                uid,
                len(history),
                self._max_failures,
            )

    async def record_success(self, uid: str) -> None:
        """Clear the failure history for this UID.

        Called after a successful authorized read — represents the user
        proving they have a legitimate card, so we forgive any prior
        misreads (which are common with worn or partially-presented cards).
        """
        async with self._lock:
            if uid in self._failures:
                del self._failures[uid]
                logger.debug("Cleared failure history for %s", uid)

    async def is_locked_out(self, uid: str) -> bool:
        """Return True iff this UID has hit the failure threshold."""
        now = self._now()
        async with self._lock:
            history = self._failures.get(uid)
            if history is None:
                return False
            self._prune(history, now)
            return len(history) >= self._max_failures

    async def time_until_unlock(self, uid: str) -> Optional[float]:
        """Seconds until this UID is no longer locked out.

        Returns None if the UID is not currently locked out. If the UID is
        locked out, returns the time until the oldest in-window failure
        expires — at which point the count drops below the threshold.
        """
        now = self._now()
        async with self._lock:
            history = self._failures.get(uid)
            if history is None:
                return None
            self._prune(history, now)
            if len(history) < self._max_failures:
                return None
            oldest = history[0]
            unlock_at = oldest + self._window
            return max(0.0, unlock_at - now)

    async def reset(self) -> None:
        """Clear all state — useful for tests and admin overrides."""
        async with self._lock:
            self._failures.clear()

    def _prune(self, history: Deque[float], now: float) -> None:
        """Drop entries older than the window from the left.

        Uses `<=` so that an entry exactly `window` seconds old is
        considered expired. This makes the contract of `time_until_unlock`
        precise: at the moment it counts down to zero, the caller is
        guaranteed to be unlocked.
        """
        cutoff = now - self._window
        while history and history[0] <= cutoff:
            history.popleft()

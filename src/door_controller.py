"""
Door controller — drives the relay that actuates the electromagnetic lock.

Follows the same abstraction pattern as `src.readers`: an `ABC` plus a
`MockDoorController` (for tests and dev), a `GPIODoorController` (for
real Pi hardware), and a `create_door_controller()` factory.

The `fail_safe_mode` parameter controls the relay polarity:
- True  → lock is engaged while powered; power loss unlocks the door
  (mandated for life-safety egress in most jurisdictions).
- False → lock is engaged only while energized; power loss leaves the
  door locked.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class DoorController(ABC):
    """Abstract door controller — implementations drive a relay GPIO pin."""

    @abstractmethod
    async def open(self, duration_seconds: Optional[float] = None) -> None:
        """Unlock the door for `duration_seconds`, then re-lock.

        Args:
            duration_seconds: Override for the configured default. None
                means "use the configured default".
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Re-lock the door immediately, interrupting any in-flight pulse.

        Calling `close()` while the door is already locked is a safe no-op.
        """
        raise NotImplementedError

    @abstractmethod
    def get_status(self) -> bool:
        """Return True if the door is currently unlocked, False otherwise."""
        raise NotImplementedError

    async def initialize(self) -> None:
        """Optional setup hook — e.g., configure GPIO."""

    async def shutdown(self) -> None:
        """Optional teardown hook — release GPIO, re-lock door."""


# =============================================================================
# Mock implementation
# =============================================================================


class MockDoorController(DoorController):
    """Software-only door controller for dev and tests.

    Records every open event so tests can assert the door was opened
    the right number of times for the right duration.
    """

    def __init__(self, default_duration_seconds: float = 5.0) -> None:
        self._default = default_duration_seconds
        self._is_open = False
        self._close_event = asyncio.Event()
        self.open_events: list[tuple[datetime, float]] = []

    def get_status(self) -> bool:
        return self._is_open

    async def open(self, duration_seconds: Optional[float] = None) -> None:
        duration = duration_seconds if duration_seconds is not None else self._default
        if duration <= 0:
            raise ValueError("duration_seconds must be positive")

        timestamp = datetime.now(timezone.utc)
        self.open_events.append((timestamp, duration))
        self._close_event.clear()
        self._is_open = True
        logger.info("MockDoor opened for %.2fs", duration)

        try:
            # Sleep up to `duration`, but wake immediately if close()
            # was called externally.
            await asyncio.wait_for(self._close_event.wait(), timeout=duration)
            logger.info("MockDoor re-locked early via close()")
        except asyncio.TimeoutError:
            logger.info("MockDoor re-locked after full duration")
        finally:
            self._is_open = False

    async def close(self) -> None:
        self._close_event.set()


# =============================================================================
# GPIO implementation — real Raspberry Pi
# =============================================================================


class GPIODoorController(DoorController):
    """Drives a relay on a single GPIO pin via `RPi.GPIO`.

    Args:
        pin: BCM pin number for the relay coil.
        default_duration_seconds: Pulse length on `open()` when not
            overridden by the caller.
        fail_safe_mode: True keeps the relay coil energized while idle
            (so power loss unlocks the door). False leaves it
            de-energized while idle.
    """

    def __init__(
        self,
        pin: int,
        default_duration_seconds: float = 5.0,
        fail_safe_mode: bool = True,
    ) -> None:
        if pin < 0 or pin > 27:
            raise ValueError(f"GPIO pin out of range: {pin}")
        self._pin = pin
        self._default = default_duration_seconds
        self._fail_safe_mode = fail_safe_mode
        self._is_open = False
        self._gpio = None  # populated in initialize()
        self._lock = asyncio.Lock()
        self._close_event = asyncio.Event()

    def get_status(self) -> bool:
        return self._is_open

    async def initialize(self) -> None:
        try:
            import RPi.GPIO as GPIO  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "RPi.GPIO not installed. Install with: pip install RPi.GPIO "
                "(Raspberry Pi only)"
            ) from e

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self._pin, GPIO.OUT)
        GPIO.output(self._pin, self._idle_level(GPIO))
        self._gpio = GPIO
        logger.info(
            "GPIODoorController initialized: pin=%d fail_safe_mode=%s",
            self._pin,
            self._fail_safe_mode,
        )

    def _idle_level(self, gpio):
        """Locked-state output level."""
        # Fail-safe: idle state energizes the lock (HIGH).
        # Fail-secure: idle state de-energizes the lock (LOW).
        return gpio.HIGH if self._fail_safe_mode else gpio.LOW

    def _active_level(self, gpio):
        """Unlocked-state output level."""
        return gpio.LOW if self._fail_safe_mode else gpio.HIGH

    async def open(self, duration_seconds: Optional[float] = None) -> None:
        if self._gpio is None:
            raise RuntimeError("DoorController not initialized — call initialize()")

        duration = duration_seconds if duration_seconds is not None else self._default
        if duration <= 0:
            raise ValueError("duration_seconds must be positive")

        # Serialize concurrent open() calls — overlapping pulses would
        # leave the relay in an indeterminate state.
        async with self._lock:
            self._close_event.clear()
            self._gpio.output(self._pin, self._active_level(self._gpio))
            self._is_open = True
            logger.info("Door opened for %.2fs", duration)
            try:
                await asyncio.wait_for(
                    self._close_event.wait(), timeout=duration
                )
                logger.info("Door re-locked early via close()")
            except asyncio.TimeoutError:
                logger.info("Door re-locked after full duration")
            finally:
                self._gpio.output(self._pin, self._idle_level(self._gpio))
                self._is_open = False

    async def close(self) -> None:
        self._close_event.set()

    async def shutdown(self) -> None:
        if self._gpio is not None:
            # Leave the pin in the idle (locked) state on shutdown.
            self._gpio.output(self._pin, self._idle_level(self._gpio))
            self._gpio.cleanup(self._pin)
            self._gpio = None
            logger.info("GPIODoorController shut down")


# =============================================================================
# Factory
# =============================================================================


def create_door_controller(use_mock: bool, **kwargs) -> DoorController:
    """Build a DoorController.

    Args:
        use_mock: If True, returns a `MockDoorController`. If False,
            returns a `GPIODoorController` configured from kwargs.
        **kwargs: Forwarded to the chosen implementation. For
            `GPIODoorController` you must supply at least `pin`.
    """
    if use_mock:
        # Mock controller only accepts default_duration_seconds.
        mock_kwargs = {
            k: kwargs[k] for k in ("default_duration_seconds",) if k in kwargs
        }
        logger.info("Creating MockDoorController")
        return MockDoorController(**mock_kwargs)

    if "pin" not in kwargs:
        raise ValueError("GPIODoorController requires a 'pin' kwarg")
    logger.info("Creating GPIODoorController on pin %d", kwargs["pin"])
    return GPIODoorController(**kwargs)

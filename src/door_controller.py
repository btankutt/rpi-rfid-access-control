"""
Door controller — drives the relay that actuates the electromagnetic lock.

Follows the same abstraction pattern as `src.readers`: an `ABC` plus a
`MockDoorController` (for tests and dev), a `GPIODoorController` (for
real Pi hardware), and a `create_door_controller()` factory.

Fail-safe vs fail-secure
------------------------
The fail-mode is a building-code question, not a software preference:

- **Fail-safe (recommended for egress doors)**: the lock is engaged
  only while powered. Power loss = door unlocks. Configure the
  hardware so the relay's idle state HOLDS the magnet energized, and
  ``open()`` de-energizes it briefly. This module's
  ``GPIODoorController(fail_safe=True)`` produces that behavior.

- **Fail-secure (entry-only doors)**: the lock requires power to
  unlock. Power loss = door stays locked. Configure the hardware so
  the relay's idle state has the strike de-energized; ``open()``
  energizes it briefly.

If unsure which is required, consult local building codes — fail-safe
is mandated for life-safety egress in most jurisdictions.
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

    controller_type: str = "abstract"

    @abstractmethod
    async def open(self, duration_seconds: Optional[float] = None) -> None:
        """Unlock the door for `duration_seconds`, then re-lock.

        Args:
            duration_seconds: Override for the configured default. None
                means "use the configured default".
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """True while the door is currently in the unlocked state."""
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

    Records every open event with timestamps so tests can assert the
    door was opened the right number of times for the right duration.
    """

    controller_type = "mock"

    def __init__(self, default_duration_seconds: float = 5.0) -> None:
        self._default = default_duration_seconds
        self._is_open = False
        self.open_events: list[tuple[datetime, float]] = []

    @property
    def is_open(self) -> bool:
        return self._is_open

    async def open(self, duration_seconds: Optional[float] = None) -> None:
        duration = duration_seconds if duration_seconds is not None else self._default
        if duration <= 0:
            raise ValueError("duration_seconds must be positive")

        timestamp = datetime.now(timezone.utc)
        self.open_events.append((timestamp, duration))
        self._is_open = True
        logger.info("MockDoor opened for %.2fs", duration)

        try:
            await asyncio.sleep(duration)
        finally:
            self._is_open = False
            logger.info("MockDoor re-locked")


# =============================================================================
# GPIO implementation — real Raspberry Pi
# =============================================================================


class GPIODoorController(DoorController):
    """Drives a relay on a single GPIO pin.

    The relay's polarity is configured via two parameters:

    - ``fail_safe``: True means the lock is engaged while powered
      (default state HIGH, ``open()`` pulses LOW). False means the lock
      is engaged only while energized (default state LOW, ``open()``
      pulses HIGH).
    - ``active_high``: If your relay board inverts the signal
      (common on cheap modules), set this to False to flip every
      output. Default True covers most boards.
    """

    controller_type = "gpio"

    def __init__(
        self,
        pin: int,
        default_duration_seconds: float = 5.0,
        fail_safe: bool = True,
        active_high: bool = True,
    ) -> None:
        if pin < 0 or pin > 27:
            raise ValueError(f"GPIO pin out of range: {pin}")
        self._pin = pin
        self._default = default_duration_seconds
        self._fail_safe = fail_safe
        self._active_high = active_high
        self._is_open = False
        self._gpio = None  # populated in initialize()
        self._lock = asyncio.Lock()

    @property
    def is_open(self) -> bool:
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
            "GPIODoorController initialized: pin=%d fail_safe=%s active_high=%s",
            self._pin,
            self._fail_safe,
            self._active_high,
        )

    def _idle_level(self, gpio):
        """Idle = door-locked state."""
        # In fail-safe mode the lock is energized while idle; in
        # fail-secure mode it's de-energized while idle.
        energized_at_idle = self._fail_safe
        return self._level_for(energized_at_idle, gpio)

    def _active_level(self, gpio):
        """Active = door-unlocked state."""
        energized_when_open = not self._fail_safe
        return self._level_for(energized_when_open, gpio)

    def _level_for(self, energize: bool, gpio):
        """Translate "energize/de-energize" into HIGH/LOW given relay polarity."""
        if energize:
            return gpio.HIGH if self._active_high else gpio.LOW
        return gpio.LOW if self._active_high else gpio.HIGH

    async def open(self, duration_seconds: Optional[float] = None) -> None:
        if self._gpio is None:
            raise RuntimeError("DoorController not initialized — call initialize()")

        duration = duration_seconds if duration_seconds is not None else self._default
        if duration <= 0:
            raise ValueError("duration_seconds must be positive")

        # Serialize concurrent open() calls — overlapping pulses would
        # leave the relay in an indeterminate state.
        async with self._lock:
            self._gpio.output(self._pin, self._active_level(self._gpio))
            self._is_open = True
            logger.info("Door opened for %.2fs", duration)
            try:
                await asyncio.sleep(duration)
            finally:
                self._gpio.output(self._pin, self._idle_level(self._gpio))
                self._is_open = False
                logger.info("Door re-locked")

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


def create_door_controller(controller_type: str, **kwargs) -> DoorController:
    """Build a DoorController from a config string.

    Args:
        controller_type: 'mock' or 'gpio'.
        **kwargs: Forwarded to the implementation constructor.

    Raises:
        ValueError: If `controller_type` is not recognized.
    """
    controllers = {
        "mock": MockDoorController,
        "gpio": GPIODoorController,
    }
    cls = controllers.get(controller_type.lower())
    if cls is None:
        raise ValueError(
            f"Unknown door controller type: {controller_type}. "
            f"Valid options: {', '.join(controllers.keys())}"
        )
    logger.info("Creating %s door controller", controller_type)
    return cls(**kwargs) if kwargs else cls()

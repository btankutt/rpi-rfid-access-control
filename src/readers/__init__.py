"""
RFID Reader abstraction layer.

Provides a unified interface for multiple RFID reader types:
- MFRC522 (SPI hobby module)
- PN532 (NFC-capable)
- RS-232 industrial readers
- Mock reader (for development without hardware)

The factory function `create_reader()` selects the appropriate
implementation based on configuration.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Data models
# =============================================================================

@dataclass(frozen=True)
class CardRead:
    """Represents a single RFID card read event."""

    uid: str
    timestamp: datetime
    reader_type: str
    signal_strength: Optional[int] = None  # for industrial readers

    def __str__(self) -> str:
        return f"CardRead(uid={self.uid}, reader={self.reader_type})"


# =============================================================================
# Abstract base
# =============================================================================

class RFIDReader(ABC):
    """
    Abstract base class for all RFID reader implementations.

    Subclasses must implement `read_card()` as an async method that
    returns a `CardRead` object or None if no card was detected
    within the timeout period.
    """

    reader_type: str = "abstract"

    @abstractmethod
    async def read_card(self, timeout: float = 1.0) -> Optional[CardRead]:
        """
        Wait for a card to be presented to the reader.

        Args:
            timeout: Maximum seconds to wait for a card.

        Returns:
            A CardRead instance on success, or None on timeout.
        """
        raise NotImplementedError

    async def initialize(self) -> None:
        """Optional initialization hook (e.g., open serial port)."""
        pass

    async def shutdown(self) -> None:
        """Optional shutdown hook (e.g., close serial port)."""
        pass


# =============================================================================
# Mock implementation — for development/testing without hardware
# =============================================================================

class MockRFIDReader(RFIDReader):
    """
    Mock RFID reader for development and testing.

    Card reads can be triggered programmatically via `trigger_read(uid)`,
    making this useful both for automated tests and the web simulator.
    """

    reader_type = "mock"

    def __init__(self) -> None:
        self._pending_uid: Optional[str] = None
        self._read_event = asyncio.Event()

    def trigger_read(self, uid: str) -> None:
        """
        Manually trigger a card read event.

        This is used by:
        - Unit tests to simulate card presentations
        - The web UI's "Simulate Card Read" button
        """
        self._pending_uid = uid
        self._read_event.set()
        logger.debug("Mock card read triggered: %s", uid)

    async def read_card(self, timeout: float = 1.0) -> Optional[CardRead]:
        try:
            await asyncio.wait_for(self._read_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

        uid = self._pending_uid
        self._pending_uid = None
        self._read_event.clear()

        if uid is None:
            return None

        return CardRead(
            uid=uid,
            timestamp=datetime.utcnow(),
            reader_type=self.reader_type,
        )


# =============================================================================
# MFRC522 implementation — SPI hobby module
# =============================================================================

class MFRC522Reader(RFIDReader):
    """
    MFRC522 RFID reader via SPI.

    Requires the `mfrc522` Python package and SPI to be enabled on the Pi.
    Tested on Raspberry Pi 3, 4, and Zero 2 W with Raspberry Pi OS.

    Hardware notes:
    - SPI traces should be < 30 cm to avoid signal degradation.
    - Power the module from 3.3V, NOT 5V (will damage the IC).
    - Connect IRQ pin to GPIO for interrupt-driven reads (optional).
    """

    reader_type = "mfrc522"

    def __init__(self) -> None:
        self._reader = None  # Lazy import — only available on Pi

    async def initialize(self) -> None:
        try:
            from mfrc522 import SimpleMFRC522  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "mfrc522 package not installed. "
                "Install with: pip install mfrc522 (Raspberry Pi only)"
            ) from e

        self._reader = SimpleMFRC522()
        logger.info("MFRC522 reader initialized")

    async def read_card(self, timeout: float = 1.0) -> Optional[CardRead]:
        if self._reader is None:
            raise RuntimeError("Reader not initialized — call initialize() first")

        # mfrc522 library is synchronous; run in thread pool
        loop = asyncio.get_event_loop()
        try:
            uid_int = await asyncio.wait_for(
                loop.run_in_executor(None, self._reader.read_id),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return None

        return CardRead(
            uid=format(uid_int, "X"),  # hex string
            timestamp=datetime.utcnow(),
            reader_type=self.reader_type,
        )


# =============================================================================
# PN532 implementation — NFC reader with cryptographic capabilities
# =============================================================================

class PN532Reader(RFIDReader):
    """
    PN532 NFC reader.

    Supports both UID reading and authenticated reads (MIFARE Classic,
    DESFire EV1+). For high-security applications where UID spoofing
    is a concern, prefer this over MFRC522.

    Connection options:
    - I2C (recommended)
    - SPI
    - UART
    """

    reader_type = "pn532"

    def __init__(self, interface: str = "i2c") -> None:
        self._interface = interface
        self._pn532 = None

    async def initialize(self) -> None:
        try:
            # Placeholder — actual implementation uses adafruit-circuitpython-pn532
            # import busio
            # from adafruit_pn532.i2c import PN532_I2C
            pass
        except ImportError as e:
            raise RuntimeError(
                "adafruit-circuitpython-pn532 not installed"
            ) from e
        logger.info("PN532 reader initialized (%s)", self._interface)

    async def read_card(self, timeout: float = 1.0) -> Optional[CardRead]:
        # TODO: implement actual PN532 read once hardware is available
        # For now, this is a stub for the showcase repo
        await asyncio.sleep(timeout)
        return None


# =============================================================================
# RS-232 industrial reader
# =============================================================================

class RS232Reader(RFIDReader):
    """
    Generic RS-232 RFID reader (Wiegand-to-serial bridge).

    Compatible with industrial readers from HID, Suprema, ZKTeco, etc.
    that output card data over a serial connection.

    Configurable parameters:
    - Serial port (e.g., /dev/ttyUSB0, COM3)
    - Baud rate (typically 9600 or 19200)
    - Data format (Wiegand 26-bit, 34-bit, or custom)
    """

    reader_type = "rs232"

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 9600) -> None:
        self._port = port
        self._baudrate = baudrate
        self._serial = None

    async def initialize(self) -> None:
        try:
            import serial  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "pyserial not installed. Install with: pip install pyserial"
            ) from e

        self._serial = serial.Serial(
            self._port,
            baudrate=self._baudrate,
            timeout=1.0,
        )
        logger.info("RS-232 reader initialized on %s @ %d", self._port, self._baudrate)

    async def shutdown(self) -> None:
        if self._serial is not None:
            self._serial.close()
            logger.info("RS-232 reader closed")

    async def read_card(self, timeout: float = 1.0) -> Optional[CardRead]:
        if self._serial is None:
            raise RuntimeError("Reader not initialized")

        # Run blocking serial read in executor
        loop = asyncio.get_event_loop()
        try:
            raw = await asyncio.wait_for(
                loop.run_in_executor(None, self._serial.readline),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return None

        if not raw:
            return None

        # Parse Wiegand format — vendor-specific
        # Example: "0x00A1B2C3\r\n" → "A1B2C3"
        uid = raw.decode("ascii", errors="ignore").strip()
        uid = uid.replace("0x", "").upper()

        if not uid:
            return None

        return CardRead(
            uid=uid,
            timestamp=datetime.utcnow(),
            reader_type=self.reader_type,
        )


# =============================================================================
# Factory
# =============================================================================

def create_reader(reader_type: str, **kwargs) -> RFIDReader:
    """
    Factory function to create an RFID reader instance.

    Args:
        reader_type: One of 'mock', 'mfrc522', 'pn532', 'rs232'.
        **kwargs: Reader-specific configuration.

    Returns:
        An RFIDReader instance.

    Raises:
        ValueError: If reader_type is not recognized.
    """
    readers = {
        "mock": MockRFIDReader,
        "mfrc522": MFRC522Reader,
        "pn532": PN532Reader,
        "rs232": RS232Reader,
    }

    reader_class = readers.get(reader_type.lower())
    if reader_class is None:
        raise ValueError(
            f"Unknown reader type: {reader_type}. "
            f"Valid options: {', '.join(readers.keys())}"
        )

    logger.info("Creating %s reader", reader_type)
    return reader_class(**kwargs) if kwargs else reader_class()

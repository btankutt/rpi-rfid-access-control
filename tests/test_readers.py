"""
Tests for RFID reader abstraction layer.

These tests use the MockRFIDReader and run without any physical hardware,
making them suitable for CI/CD pipelines.
"""

import asyncio

import pytest

from src.readers import (
    CardRead,
    MockRFIDReader,
    RFIDReader,
    create_reader,
)


# =============================================================================
# MockRFIDReader tests
# =============================================================================

class TestMockRFIDReader:
    """Unit tests for the mock reader."""

    @pytest.mark.asyncio
    async def test_triggered_read_returns_card(self):
        reader = MockRFIDReader()
        reader.trigger_read("A1B2C3D4")

        result = await reader.read_card(timeout=1.0)

        assert result is not None
        assert result.uid == "A1B2C3D4"
        assert result.reader_type == "mock"

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        reader = MockRFIDReader()

        result = await reader.read_card(timeout=0.1)

        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_reads_sequential(self):
        reader = MockRFIDReader()
        uids = ["AAAA", "BBBB", "CCCC"]

        results = []
        for uid in uids:
            reader.trigger_read(uid)
            card = await reader.read_card(timeout=1.0)
            results.append(card)

        assert all(r is not None for r in results)
        assert [r.uid for r in results] == uids

    @pytest.mark.asyncio
    async def test_event_clears_after_read(self):
        """After reading, the next read should timeout if no new trigger."""
        reader = MockRFIDReader()
        reader.trigger_read("AAAA")

        first = await reader.read_card(timeout=1.0)
        assert first is not None

        # No new trigger — should timeout
        second = await reader.read_card(timeout=0.1)
        assert second is None


# =============================================================================
# Factory tests
# =============================================================================

class TestReaderFactory:
    """Tests for the create_reader factory function."""

    def test_create_mock(self):
        reader = create_reader("mock")
        assert isinstance(reader, MockRFIDReader)
        assert reader.reader_type == "mock"

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown reader type"):
            create_reader("invalid_type")

    def test_case_insensitive(self):
        reader = create_reader("MOCK")
        assert isinstance(reader, MockRFIDReader)


# =============================================================================
# CardRead dataclass tests
# =============================================================================

class TestCardRead:
    """Tests for the CardRead data model."""

    def test_str_representation(self):
        from datetime import datetime

        card = CardRead(
            uid="DEADBEEF",
            timestamp=datetime(2026, 1, 1, 12, 0, 0),
            reader_type="mock",
        )

        assert "DEADBEEF" in str(card)
        assert "mock" in str(card)

    def test_immutable(self):
        from datetime import datetime

        card = CardRead(
            uid="DEADBEEF",
            timestamp=datetime.utcnow(),
            reader_type="mock",
        )

        # Frozen dataclass — should raise on mutation
        with pytest.raises(Exception):
            card.uid = "OTHER"  # type: ignore

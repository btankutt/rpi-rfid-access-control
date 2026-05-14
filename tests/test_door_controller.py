"""Tests for the door controller abstraction."""

from __future__ import annotations

import asyncio

import pytest

from src.door_controller import (
    DoorController,
    GPIODoorController,
    MockDoorController,
    create_door_controller,
)


class TestMockDoorController:
    @pytest.mark.asyncio
    async def test_open_sets_state_then_relocks(self):
        door = MockDoorController(default_duration_seconds=0.05)
        assert door.get_status() is False

        task = asyncio.create_task(door.open())
        await asyncio.sleep(0.01)
        assert door.get_status() is True

        await task
        assert door.get_status() is False

    @pytest.mark.asyncio
    async def test_records_open_events(self):
        door = MockDoorController(default_duration_seconds=0.01)
        await door.open()
        await door.open(duration_seconds=0.02)
        assert len(door.open_events) == 2
        assert door.open_events[0][1] == pytest.approx(0.01)
        assert door.open_events[1][1] == pytest.approx(0.02)

    @pytest.mark.asyncio
    async def test_zero_duration_rejected(self):
        door = MockDoorController()
        with pytest.raises(ValueError):
            await door.open(duration_seconds=0)

    @pytest.mark.asyncio
    async def test_close_relocks_early(self):
        door = MockDoorController(default_duration_seconds=10.0)
        task = asyncio.create_task(door.open())
        await asyncio.sleep(0.01)
        assert door.get_status() is True

        await door.close()
        await asyncio.wait_for(task, timeout=0.5)
        assert door.get_status() is False

    @pytest.mark.asyncio
    async def test_close_on_locked_door_is_safe(self):
        door = MockDoorController(default_duration_seconds=0.01)
        await door.close()
        assert door.get_status() is False
        await door.open()
        assert door.get_status() is False
        assert len(door.open_events) == 1

    @pytest.mark.asyncio
    async def test_concurrent_opens(self):
        """Calling open() multiple times in parallel records each event."""
        door = MockDoorController(default_duration_seconds=0.01)
        await asyncio.gather(door.open(), door.open(), door.open())
        assert len(door.open_events) == 3
        assert door.get_status() is False


class TestGPIODoorController:
    def test_pin_range_validation(self):
        with pytest.raises(ValueError):
            GPIODoorController(pin=99)
        with pytest.raises(ValueError):
            GPIODoorController(pin=-1)

    @pytest.mark.asyncio
    async def test_open_without_initialize_raises(self):
        controller = GPIODoorController(pin=17)
        with pytest.raises(RuntimeError, match="not initialized"):
            await controller.open()

    @pytest.mark.asyncio
    async def test_initialize_without_rpi_gpio_raises(self):
        """RPi.GPIO isn't installed on dev/CI; the error should be actionable."""
        controller = GPIODoorController(pin=17)
        with pytest.raises(RuntimeError, match="RPi.GPIO"):
            await controller.initialize()

    @pytest.mark.asyncio
    async def test_fail_safe_polarity(self):
        """fail_safe_mode=True: idle HIGH (lock energized), pulse LOW."""

        class FakeGPIO:
            BCM = "BCM"
            OUT = "OUT"
            HIGH = 1
            LOW = 0

            def __init__(self):
                self.outputs: list[tuple[int, int]] = []

            def setwarnings(self, _):
                pass

            def setmode(self, _):
                pass

            def setup(self, *_):
                pass

            def output(self, pin, level):
                self.outputs.append((pin, level))

            def cleanup(self, _):
                pass

        controller = GPIODoorController(
            pin=17, default_duration_seconds=0.01, fail_safe_mode=True
        )
        controller._gpio = FakeGPIO()
        await controller.open()
        levels = [lvl for _, lvl in controller._gpio.outputs]
        # Pulse: LOW (unlock by de-energize), then HIGH (idle locked)
        assert levels == [0, 1]

    @pytest.mark.asyncio
    async def test_fail_secure_polarity(self):
        """fail_safe_mode=False: idle LOW, pulse HIGH."""

        class FakeGPIO:
            BCM = "BCM"
            OUT = "OUT"
            HIGH = 1
            LOW = 0

            def __init__(self):
                self.outputs: list[tuple[int, int]] = []

            def setwarnings(self, _):
                pass

            def setmode(self, _):
                pass

            def setup(self, *_):
                pass

            def output(self, pin, level):
                self.outputs.append((pin, level))

            def cleanup(self, _):
                pass

        controller = GPIODoorController(
            pin=17, default_duration_seconds=0.01, fail_safe_mode=False
        )
        controller._gpio = FakeGPIO()
        await controller.open()
        levels = [lvl for _, lvl in controller._gpio.outputs]
        assert levels == [1, 0]


class TestFactory:
    def test_create_mock(self):
        door = create_door_controller(use_mock=True)
        assert isinstance(door, MockDoorController)

    def test_create_mock_with_kwargs(self):
        door = create_door_controller(
            use_mock=True, default_duration_seconds=10.0
        )
        assert isinstance(door, MockDoorController)
        assert door._default == 10.0

    def test_create_gpio_requires_pin(self):
        with pytest.raises(ValueError, match="pin"):
            create_door_controller(use_mock=False)

    def test_create_gpio(self):
        door = create_door_controller(use_mock=False, pin=22, fail_safe_mode=False)
        assert isinstance(door, GPIODoorController)
        assert door._pin == 22
        assert door._fail_safe_mode is False


class TestABCContract:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            DoorController()  # type: ignore[abstract]

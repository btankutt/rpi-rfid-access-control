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
        assert door.is_open is False

        task = asyncio.create_task(door.open())
        # Yield once so the task starts; it should now report open.
        await asyncio.sleep(0)
        # Use a very small wait to confirm it's actually open mid-pulse:
        await asyncio.sleep(0.01)
        assert door.is_open is True

        await task
        assert door.is_open is False

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
    async def test_default_duration_applied(self):
        door = MockDoorController(default_duration_seconds=0.03)
        await door.open()
        assert door.open_events[0][1] == pytest.approx(0.03)


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
        """RPi.GPIO isn't installed on dev/CI — initialize() must report
        a clear, actionable error rather than a confusing ImportError."""
        controller = GPIODoorController(pin=17)
        with pytest.raises(RuntimeError, match="RPi.GPIO"):
            await controller.initialize()

    @pytest.mark.asyncio
    async def test_polarity_logic_with_fake_gpio(self):
        """Use a fake GPIO module to verify the HIGH/LOW pulses come out
        right for the four combinations of fail_safe + active_high."""

        class FakeGPIO:
            BCM = "BCM"
            OUT = "OUT"
            HIGH = 1
            LOW = 0

            def __init__(self):
                self.calls: list[tuple[str, tuple]] = []
                self.pin_state: dict[int, int] = {}

            def setwarnings(self, _): self.calls.append(("setwarnings", ()))
            def setmode(self, _): self.calls.append(("setmode", ()))
            def setup(self, pin, mode): self.calls.append(("setup", (pin, mode)))
            def output(self, pin, level):
                self.calls.append(("output", (pin, level)))
                self.pin_state[pin] = level
            def cleanup(self, pin): self.calls.append(("cleanup", (pin,)))

        scenarios = [
            # (fail_safe, active_high, expected_idle, expected_active)
            (True, True, 1, 0),    # idle HIGH (energized), pulse LOW (de-energize)
            (True, False, 0, 1),   # inverted board
            (False, True, 0, 1),   # idle LOW (de-energized), pulse HIGH (energize)
            (False, False, 1, 0),  # inverted fail-secure
        ]
        for fail_safe, active_high, idle, active in scenarios:
            controller = GPIODoorController(
                pin=17,
                default_duration_seconds=0.01,
                fail_safe=fail_safe,
                active_high=active_high,
            )
            controller._gpio = FakeGPIO()
            # Skip initialize() since we're injecting the fake directly
            outputs_before = [
                level for op, (_, level) in controller._gpio.calls if op == "output"
            ]
            assert outputs_before == []

            await controller.open()
            outputs = [
                level for op, (_, level) in controller._gpio.calls if op == "output"
            ]
            # Pulse should be: active, then back to idle.
            assert outputs == [active, idle], (
                f"fail_safe={fail_safe} active_high={active_high} "
                f"expected [active={active}, idle={idle}] got {outputs}"
            )

    @pytest.mark.asyncio
    async def test_concurrent_opens_serialized(self):
        """Overlapping open() calls must be serialized via the internal lock."""

        class CountingGPIO:
            BCM = "BCM"
            OUT = "OUT"
            HIGH = 1
            LOW = 0

            def __init__(self):
                self.active_count = 0
                self.max_active = 0

            def setwarnings(self, _):
                pass

            def setmode(self, _):
                pass

            def setup(self, *_):
                pass

            def output(self, _, level):
                if level == 1:
                    self.active_count += 1
                else:
                    self.active_count -= 1
                self.max_active = max(self.max_active, self.active_count)

            def cleanup(self, _):
                pass

        controller = GPIODoorController(
            pin=17, default_duration_seconds=0.02, fail_safe=False
        )
        controller._gpio = CountingGPIO()

        await asyncio.gather(controller.open(), controller.open(), controller.open())
        # Three sequential active->idle pulses, never more than 1 active at a time
        assert controller._gpio.max_active == 1


class TestFactory:
    def test_create_mock(self):
        d = create_door_controller("mock")
        assert isinstance(d, MockDoorController)
        assert d.controller_type == "mock"

    def test_create_mock_with_kwargs(self):
        d = create_door_controller("mock", default_duration_seconds=10.0)
        assert isinstance(d, MockDoorController)
        assert d._default == 10.0

    def test_create_gpio(self):
        d = create_door_controller("gpio", pin=22)
        assert isinstance(d, GPIODoorController)

    def test_case_insensitive(self):
        d = create_door_controller("MOCK")
        assert isinstance(d, MockDoorController)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown door controller"):
            create_door_controller("magnet")


class TestABCContract:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            DoorController()  # type: ignore[abstract]

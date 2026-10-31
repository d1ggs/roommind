"""TRV setpoints in the valve's frame: the learned sensor offset moves what is sent.

A TRV compares the proportional setpoint with its own sensor, which sits on the
radiator and can read several degrees off the room. Shifting the setpoint by the
learned offset makes the valve see the push RoomMind meant, in the room's frame.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.roommind.const import MODE_HEATING, TargetTemps
from custom_components.roommind.control.mpc_controller import MPCController, clear_command_cache
from custom_components.roommind.control.thermal_model import RoomModelManager
from custom_components.roommind.managers.head_offset_tracker import HeadOffsetTracker
from custom_components.roommind.managers.heat_source_orchestrator import DeviceCommand, HeatSourcePlan

from .conftest import build_hass, make_room

TRV = "climate.living_trv"
TARGETS = TargetTemps(heat=21.0, cool=None)


def _tracker(**offsets: float) -> HeadOffsetTracker:
    tracker = HeadOffsetTracker()
    tracker.load_state({TRV: {state: [value, 50] for state, value in offsets.items()}})
    return tracker


def _trv_state():
    state = MagicMock()
    state.state = "heat"
    state.attributes = {
        "hvac_modes": ["heat", "off"],
        "min_temp": 5.0,
        "max_temp": 25.0,
        "target_temp_step": 0.5,
        "temperature": None,
    }
    return state


async def _apply(tracker, *, room=None, has_external_sensor=True, **kwargs):
    clear_command_cache()
    hass = build_hass()
    hass.states.get = MagicMock(return_value=_trv_state())
    ctrl = MPCController(
        hass,
        room or make_room(thermostats=[TRV]),
        model_manager=RoomModelManager(),
        outdoor_temp=5.0,
        settings={},
        has_external_sensor=has_external_sensor,
        head_offsets=tracker,
    )
    await ctrl.async_apply(MODE_HEATING, TARGETS, **kwargs)
    return [c[0][2]["temperature"] for c in hass.services.async_call.call_args_list if c[0][1] == "set_temperature"]


@pytest.mark.asyncio
async def test_trv_setpoint_moves_by_the_heating_offset():
    """A valve reading 3 K cold while heating gets the proportional setpoint 3 K lower."""
    kwargs = {"power_fraction": 0.3, "current_temp": 20.0}
    plain = await _apply(None, **kwargs)
    shifted = await _apply(_tracker(heat=-3.0, idle=-3.5), **kwargs)
    assert len(plain) == len(shifted) == 1
    assert shifted[0] == pytest.approx(plain[0] - 3.0)


@pytest.mark.asyncio
async def test_trv_setpoint_below_the_room_target_when_the_valve_reads_cold():
    """The floor at the room target holds in the room's frame, not in the valve's."""
    sent = await _apply(_tracker(heat=-3.0), power_fraction=0.0, current_temp=20.9)
    assert sent == [pytest.approx(18.0)]


@pytest.mark.asyncio
async def test_orchestrated_trv_setpoint_moves_by_the_heating_offset():
    plan = HeatSourcePlan(
        commands=[DeviceCommand(TRV, "primary", "thermostat", True, 0.3, "primary heating")],
        active_sources="primary",
        reason="test",
    )
    kwargs = {"power_fraction": 0.3, "current_temp": 20.0, "heat_source_plan": plan}
    plain = await _apply(None, **kwargs)
    shifted = await _apply(_tracker(heat=-3.0), **kwargs)
    assert len(plain) == len(shifted) == 1
    assert shifted[0] == pytest.approx(plain[0] - 3.0)


@pytest.mark.asyncio
async def test_trv_warm_offset_stays_under_the_device_ceiling():
    """A valve reading warm at full power cannot be pushed over its max_temp."""
    sent = await _apply(_tracker(heat=2.0), power_fraction=1.0, current_temp=18.0)
    assert sent == [24.5]


@pytest.mark.asyncio
async def test_direct_trv_keeps_its_own_offset():
    room = make_room(thermostats=[TRV])
    room["devices"][0].update(setpoint_mode="direct", setpoint_offset=1.0)
    kwargs = {"power_fraction": 0.3, "current_temp": 20.0, "room": room}
    plain = await _apply(None, **kwargs)
    shifted = await _apply(_tracker(heat=-3.0), **kwargs)
    assert shifted == plain == [22.0]


@pytest.mark.asyncio
async def test_trv_without_room_sensor_ignores_the_offset():
    kwargs = {"power_fraction": 0.3, "current_temp": 20.0, "has_external_sensor": False}
    plain = await _apply(None, **kwargs)
    assert await _apply(_tracker(heat=-3.0), **kwargs) == plain

"""The model does not learn while a climate device of the room is unreachable.

An unavailable device can neither be commanded nor observed: RoomMind does not
know whether it heats. Training went on with the commanded power, so hours of
"heating at 100 %" that never reached a TRV taught the model a radiator without
effect, and the pre-heat then started as early as the horizon allows.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from .conftest import _create_coordinator, _make_store_mock, make_mock_states_get
from .test_ghost_heating_guard import _room_for_guard, _run_and_capture_train_kwargs

TRV = "climate.living_room"
TRV_2 = "climate.living_room_2"
AC = "climate.living_room_ac"


def _device(entity_id: str, device_type: str = "trv") -> dict:
    return {
        "entity_id": entity_id,
        "type": device_type,
        "role": "auto",
        "heating_system_type": "radiator" if device_type == "trv" else "",
        "idle_action": "off",
        "idle_fan_mode": "low",
        "setpoint_mode": "direct",
    }


def _heating(action: str = "heating") -> tuple[str, dict]:
    return ("heat", {"hvac_action": action, "current_temperature": 19.5, "temperature": 21.0})


def _states(extra: dict, *, temp: str = "19.5", missing: tuple[str, ...] = ()):
    base = make_mock_states_get(temp=temp, humidity="55.0", schedule_state="on", outdoor_temp="5.0", extra=extra)

    def states_get(entity_id: str):
        return None if entity_id in missing else base(entity_id)

    return states_get


class TestUnreachableDeviceTraining:
    @pytest.mark.asyncio
    async def test_reachable_device_trains(self, hass, mock_config_entry):
        kwargs = await _run_and_capture_train_kwargs(hass, mock_config_entry, _states({TRV: _heating()}))
        assert kwargs is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["unavailable", "unknown"])
    async def test_unreachable_sole_device_skips_training(self, hass, mock_config_entry, state):
        """Commanded heating that cannot reach the only TRV is not a heating sample."""
        kwargs = await _run_and_capture_train_kwargs(hass, mock_config_entry, _states({TRV: (state, {})}))
        assert kwargs is None

    @pytest.mark.asyncio
    async def test_entity_without_state_still_trains(self, hass, mock_config_entry):
        """A removed or renamed entity is no device of the room, the other one is observed."""
        room = _room_for_guard(devices=[_device(TRV), _device(TRV_2)])
        room["thermostats"] = [TRV, TRV_2]
        states_get = _states({TRV: _heating()}, missing=(TRV_2,))
        kwargs = await _run_and_capture_train_kwargs(hass, mock_config_entry, states_get, room=room)
        assert kwargs is not None

    @pytest.mark.asyncio
    async def test_one_of_two_devices_unreachable_skips_training(self, hass, mock_config_entry):
        """The reachable TRV alone would label the room as heating at full power."""
        room = _room_for_guard(devices=[_device(TRV), _device(TRV_2)])
        room["thermostats"] = [TRV, TRV_2]
        states_get = _states({TRV: _heating(), TRV_2: ("unavailable", {})})
        kwargs = await _run_and_capture_train_kwargs(hass, mock_config_entry, states_get, room=room)
        assert kwargs is None

    @pytest.mark.asyncio
    async def test_unreachable_device_skips_training_while_idle(self, hass, mock_config_entry):
        """The off command did not arrive either: the valve may still be heating."""
        states_get = _states({TRV: ("unavailable", {})}, temp="23.0")
        kwargs = await _run_and_capture_train_kwargs(hass, mock_config_entry, states_get)
        assert kwargs is None

    @pytest.mark.asyncio
    async def test_unreachable_device_resyncs_the_training_state(self, hass, mock_config_entry):
        """The gap is not learned as one long step once the device is back."""
        room = _room_for_guard()
        hass.data = {"roommind": {"store": _make_store_mock({room["area_id"]: room})}}
        hass.states.get = MagicMock(side_effect=_states({TRV: ("unavailable", {})}))
        hass.services.async_call = AsyncMock()
        coordinator = _create_coordinator(hass, mock_config_entry)
        coordinator._ekf_training.process = MagicMock()
        coordinator._ekf_training.clear = MagicMock()

        await coordinator._async_update_data()

        coordinator._ekf_training.process.assert_not_called()
        coordinator._ekf_training.clear.assert_called_once_with(room["area_id"], resync=True)

    @pytest.mark.asyncio
    async def test_unreachable_ac_sitting_heating_out_still_trains(self, hass, mock_config_entry):
        """An AC that takes no part in heating (unplugged over the winter) does not stop the learning."""
        room = _room_for_guard(devices=[_device(TRV), _device(AC, "ac")])
        room["acs"] = [AC]
        room["climate_mode"] = "heat_only"
        states_get = _states({TRV: _heating(), AC: ("unavailable", {})})
        kwargs = await _run_and_capture_train_kwargs(hass, mock_config_entry, states_get, room=room)
        assert kwargs is not None

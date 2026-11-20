"""Coordinator wiring of the temperature-drop window detection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roommind.const import MODE_COOLING, MODE_IDLE

from .conftest import (
    MANAGED_ROOM,
    SAMPLE_ROOM,
    _create_coordinator,
    _make_store_mock,
    make_mock_states_get,
)

AREA = "living_room_abc12345"
DETECTOR_TIME = "custom_components.roommind.managers.window_drop_detector.time"
WINDOW = "binary_sensor.living_room_window"
DETECTING_ROOM = {**SAMPLE_ROOM, "window_detection_enabled": True}
AC = "climate.living_room"
COOLING_ROOM = {
    **SAMPLE_ROOM,
    "climate_mode": "cool_only",
    "devices": [
        {
            "entity_id": AC,
            "type": "ac",
            "role": "auto",
            "heating_system_type": "",
            "idle_action": "off",
            "idle_fan_mode": "low",
            "setpoint_mode": "direct",
        }
    ],
    "thermostats": [],
    "acs": [AC],
}


def _ac_device(temp: float) -> dict:
    attrs = {
        "hvac_action": "cooling",
        "current_temperature": temp,
        "temperature": 24.0,
        "hvac_modes": ["off", "cool", "heat", "auto"],
    }
    return {AC: ("cool", attrs)}


def _setup(hass, mock_config_entry, room):
    store = _make_store_mock({AREA: room})
    hass.data = {"roommind": {"store": store}}
    hass.services.async_call = AsyncMock()
    return _create_coordinator(hass, mock_config_entry)


async def _cycle(coordinator, hass, mock_time, now, *, temp, window=None, extra=None, outdoor_temp="5.0"):
    """Run one coordinator cycle at detector time *now*; return the room's live state."""
    mock_time.time.return_value = now
    hass.states.get = MagicMock(
        side_effect=make_mock_states_get(
            temp=temp,
            window_sensors={WINDOW: window} if window is not None else None,
            extra=extra,
            outdoor_temp=outdoor_temp,
        )
    )
    data = await coordinator._async_update_data()
    return data["rooms"][AREA]


class TestWindowDropDetection:
    @pytest.mark.asyncio
    async def test_fast_fall_idles_room(self, hass, mock_config_entry):
        coordinator = _setup(hass, mock_config_entry, DETECTING_ROOM)
        with patch(DETECTOR_TIME) as mock_time:
            state = await _cycle(coordinator, hass, mock_time, 1000.0, temp="20.0")
            assert state["mode"] == "heating"
            assert state["window_detected"] is False
            await _cycle(coordinator, hass, mock_time, 1060.0, temp="19.8")
            state = await _cycle(coordinator, hass, mock_time, 1120.0, temp="19.6")

        assert state["mode"] == MODE_IDLE
        assert state["window_open"] is True
        assert state["window_detected"] is True

    @pytest.mark.asyncio
    async def test_option_off_does_not_detect(self, hass, mock_config_entry):
        coordinator = _setup(hass, mock_config_entry, SAMPLE_ROOM)
        with patch(DETECTOR_TIME) as mock_time:
            await _cycle(coordinator, hass, mock_time, 1000.0, temp="20.0")
            await _cycle(coordinator, hass, mock_time, 1060.0, temp="19.8")
            state = await _cycle(coordinator, hass, mock_time, 1120.0, temp="19.6")

        assert state["mode"] == "heating"
        assert state["window_open"] is False
        assert state["window_detected"] is False

    @pytest.mark.asyncio
    async def test_no_external_sensor_never_detects(self, hass, mock_config_entry):
        room = {**MANAGED_ROOM, "window_detection_enabled": True}
        coordinator = _setup(hass, mock_config_entry, room)

        def device(temp):
            attrs = {
                "hvac_modes": ["off", "heat"],
                "current_temperature": temp,
                "temperature": 21.0,
                "min_temp": 5,
                "max_temp": 30,
            }
            return {"climate.living_room": ("heat", attrs)}

        with patch(DETECTOR_TIME) as mock_time:
            await _cycle(coordinator, hass, mock_time, 1000.0, temp=None, extra=device(20.0))
            await _cycle(coordinator, hass, mock_time, 1060.0, temp=None, extra=device(19.6))
            state = await _cycle(coordinator, hass, mock_time, 1120.0, temp=None, extra=device(19.0))
            assert coordinator._window_drop_detector.is_active(AREA) is False

        assert state["current_temp"] == 19.0
        assert state["window_open"] is False
        assert state["window_detected"] is False

    @pytest.mark.asyncio
    async def test_contact_and_detection_combine(self, hass, mock_config_entry):
        room = {**DETECTING_ROOM, "window_sensors": [WINDOW]}
        coordinator = _setup(hass, mock_config_entry, room)
        with patch(DETECTOR_TIME) as mock_time:
            await _cycle(coordinator, hass, mock_time, 1000.0, temp="20.0", window="on")
            state = await _cycle(coordinator, hass, mock_time, 1060.0, temp="19.6", window="on")
            assert state["mode"] == MODE_IDLE
            assert state["window_open"] is True
            assert state["window_detected"] is True

            # Contact closes while the detection pause (60 min default cap) still runs
            state = await _cycle(coordinator, hass, mock_time, 1120.0, temp="19.6", window="off")
            assert state["mode"] == MODE_IDLE
            assert state["window_open"] is True
            assert state["window_detected"] is True

            # Pause over and contact closed: control resumes
            state = await _cycle(coordinator, hass, mock_time, 1060.0 + 60 * 60, temp="19.6", window="off")

        assert state["window_open"] is False
        assert state["window_detected"] is False
        assert state["mode"] == "heating"

    @pytest.mark.asyncio
    async def test_recovery_ends_detection_before_the_cap(self, hass, mock_config_entry):
        """The room warming back up by the drop threshold ends the pause early."""
        coordinator = _setup(hass, mock_config_entry, DETECTING_ROOM)
        with patch(DETECTOR_TIME) as mock_time:
            await _cycle(coordinator, hass, mock_time, 1000.0, temp="20.0")
            state = await _cycle(coordinator, hass, mock_time, 1060.0, temp="19.6")
            assert state["window_detected"] is True
            state = await _cycle(coordinator, hass, mock_time, 1120.0, temp="19.2")
            assert state["window_detected"] is True
            state = await _cycle(coordinator, hass, mock_time, 1180.0, temp="19.5")

        assert state["window_detected"] is False
        assert state["window_open"] is False

    @pytest.mark.asyncio
    async def test_detection_skips_model_training(self, hass, mock_config_entry):
        coordinator = _setup(hass, mock_config_entry, DETECTING_ROOM)
        with patch(DETECTOR_TIME) as mock_time:
            await _cycle(coordinator, hass, mock_time, 1000.0, temp="20.0")
            with patch.object(coordinator._ekf_training, "process") as process:
                state = await _cycle(coordinator, hass, mock_time, 1060.0, temp="19.6")

        assert state["window_detected"] is True
        assert process.call_args_list
        for c in process.call_args_list:
            assert c.kwargs["raw_open"] is True
            assert c.kwargs["window_open"] is True

    @pytest.mark.asyncio
    async def test_custom_options_are_used(self, hass, mock_config_entry):
        """A larger drop threshold ignores a fall the default would catch."""
        room = {**DETECTING_ROOM, "window_detection_drop": 1.0}
        coordinator = _setup(hass, mock_config_entry, room)
        with patch(DETECTOR_TIME) as mock_time:
            await _cycle(coordinator, hass, mock_time, 1000.0, temp="20.0")
            state = await _cycle(coordinator, hass, mock_time, 1060.0, temp="19.6")
            assert state["window_detected"] is False
            state = await _cycle(coordinator, hass, mock_time, 1120.0, temp="19.0")

        assert state["window_detected"] is True

    @pytest.mark.asyncio
    async def test_sensor_removed_during_pause_ends_it(self, hass, mock_config_entry):
        """A room that loses its temperature sensor must not keep a stale detection."""
        room = dict(DETECTING_ROOM)
        coordinator = _setup(hass, mock_config_entry, room)
        with patch(DETECTOR_TIME) as mock_time:
            await _cycle(coordinator, hass, mock_time, 1000.0, temp="20.0")
            state = await _cycle(coordinator, hass, mock_time, 1060.0, temp="19.6")
            assert state["window_detected"] is True

            room["temperature_sensor"] = ""
            state = await _cycle(coordinator, hass, mock_time, 1120.0, temp=None)
            assert coordinator._window_drop_detector.is_active(AREA) is False

        assert state["window_detected"] is False

    @pytest.mark.asyncio
    async def test_room_removed_cleans_up_detector(self, hass, mock_config_entry):
        coordinator = _setup(hass, mock_config_entry, DETECTING_ROOM)
        coordinator.async_request_refresh = AsyncMock()
        with patch(DETECTOR_TIME) as mock_time:
            await _cycle(coordinator, hass, mock_time, 1000.0, temp="20.0")
            await _cycle(coordinator, hass, mock_time, 1060.0, temp="19.6")
            assert coordinator._window_drop_detector.is_active(AREA) is True

            mock_registry = MagicMock()
            mock_registry.entities.values.return_value = []
            with patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_registry):
                await coordinator.async_room_removed(AREA)

            assert coordinator._window_drop_detector.is_active(AREA) is False

    @pytest.mark.asyncio
    async def test_cooling_room_does_not_detect(self, hass, mock_config_entry):
        """An AC pulling the room down fast is not an open window."""
        room = {**COOLING_ROOM, "window_detection_enabled": True}
        coordinator = _setup(hass, mock_config_entry, room)
        with patch(DETECTOR_TIME) as mock_time:
            for now, temp in ((1000.0, "28.0"), (1060.0, "27.8"), (1120.0, "27.6")):
                state = await _cycle(
                    coordinator, hass, mock_time, now, temp=temp, extra=_ac_device(float(temp)), outdoor_temp="30.0"
                )
                assert state["mode"] == MODE_COOLING
                assert state["window_detected"] is False
            assert coordinator._window_drop_detector.is_active(AREA) is False

        assert state["window_open"] is False

    @pytest.mark.asyncio
    async def test_cooling_keeps_running_pause(self, hass, mock_config_entry):
        """A pause that is already running is not ended by the room turning to cooling."""
        coordinator = _setup(hass, mock_config_entry, DETECTING_ROOM)
        with patch(DETECTOR_TIME) as mock_time:
            await _cycle(coordinator, hass, mock_time, 1000.0, temp="20.0")
            state = await _cycle(coordinator, hass, mock_time, 1060.0, temp="19.6")
            assert state["window_detected"] is True

            coordinator._previous_modes[AREA] = MODE_COOLING
            state = await _cycle(coordinator, hass, mock_time, 1120.0, temp="19.6")
            assert state["window_detected"] is True
            assert coordinator._window_drop_detector.is_active(AREA) is True

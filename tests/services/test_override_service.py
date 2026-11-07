"""Tests for the roommind.set_override action."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.roommind import async_setup
from custom_components.roommind.const import DOMAIN
from custom_components.roommind.services.override_service import async_register_services

# 2099-01-15 10:00 in Rome (UTC+1 in winter) is 09:00 UTC.
ROME_10AM_AS_UTC = datetime(2099, 1, 15, 9, 0, tzinfo=UTC)


@pytest.fixture
async def service_hass(hass, store):
    """Return a hass with a loaded store and two rooms."""
    await store.async_load()
    await store.async_save_room("studio", {"climate_mode": "heat_only"})
    await store.async_save_room("lounge", {"climate_mode": "auto", "comfort_cool": 24.0})
    await store.async_save_room("attic", {"climate_mode": "cool_only"})
    hass.data[DOMAIN] = {"store": store}
    async_register_services(hass)
    return hass


async def call_action(hass, **data):
    """Run the registered action as HA does: its schema first, then its handler."""
    args = hass.services.async_register.call_args
    assert args.args[:2] == (DOMAIN, "set_override")
    await args.args[2](SimpleNamespace(data=args.kwargs["schema"](data)))


@pytest.mark.parametrize(
    "until",
    [
        pytest.param("2099-01-15T10:00:00", id="naive string"),
        pytest.param(datetime(2099, 1, 15, 10, 0), id="naive datetime"),
        pytest.param("2099-01-15T09:00:00+00:00", id="aware string"),
    ],
)
async def test_until_ends_override_at_that_moment_in_local_time(service_hass, store, until):
    """A time without zone means HA's local time, so a script can pass '10:00' as is."""
    dt_util.set_default_time_zone(ZoneInfo("Europe/Rome"))

    await call_action(service_hass, area_id="studio", temperature=21, until=until)

    room = store.get_room("studio")
    assert room["override_until"] == ROME_10AM_AS_UTC.timestamp()
    assert room["override_heat"] == 21.0
    assert room["override_type"] == "custom"


async def test_duration_ends_override_that_many_hours_from_now(service_hass, store):
    """Duration is in hours and may be longer than the panel's buttons."""
    before = time.time()

    await call_action(service_hass, area_id="studio", temperature=21, duration=9.5)

    assert before + 34200 <= store.get_room("studio")["override_until"] <= time.time() + 34200


@pytest.mark.parametrize(
    ("area_id", "heat", "cool"),
    [
        pytest.param("studio", 21.0, None, id="heat_only has no cooling side"),
        pytest.param("attic", None, 21.0, id="cool_only has no heating side"),
        pytest.param("lounge", 21.0, 24.0, id="auto keeps cooling at comfort"),
    ],
)
async def test_temperature_follows_the_rooms_climate_mode(service_hass, store, area_id, heat, cool):
    """A single temperature must not collapse an auto room's band to one point."""
    await call_action(service_hass, area_id=area_id, temperature=21, duration=1)

    room = store.get_room(area_id)
    assert (room["override_heat"], room["override_cool"]) == (heat, cool)


async def test_auto_room_above_comfort_cooling_keeps_a_valid_band(service_hass, store):
    """The cooling side never falls below the requested temperature."""
    await call_action(service_hass, area_id="lounge", temperature=26, duration=1)

    room = store.get_room("lounge")
    assert (room["override_heat"], room["override_cool"]) == (26.0, 26.0)


async def test_end_in_the_past_is_rejected_and_keeps_the_room_unchanged(service_hass, store):
    """A wrongly resolved date must fail visibly instead of doing nothing silently."""
    past = (dt_util.utcnow() - timedelta(minutes=1)).isoformat()

    with pytest.raises(ServiceValidationError):
        await call_action(service_hass, area_id="studio", temperature=21, until=past)

    assert store.get_room("studio").get("override_heat") is None


async def test_unknown_room_is_rejected(service_hass):
    """A mistyped area id must fail visibly."""
    with pytest.raises(ServiceValidationError):
        await call_action(service_hass, area_id="nowhere", temperature=21, duration=1)


async def test_action_before_setup_finished_is_rejected(hass):
    """Without a loaded store the action fails cleanly instead of raising KeyError."""
    hass.data[DOMAIN] = {}
    async_register_services(hass)

    with pytest.raises(ServiceValidationError):
        await call_action(hass, area_id="studio", temperature=21, duration=1)


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"area_id": "studio", "temperature": 21}, id="no end"),
        pytest.param(
            {"area_id": "studio", "temperature": 21, "duration": 1, "until": "2099-01-15T10:00:00"},
            id="two ends",
        ),
        pytest.param({"area_id": "studio", "temperature": 21, "duration": 0}, id="zero duration"),
        pytest.param({"area_id": "studio", "temperature": 4.5, "duration": 1}, id="too cold"),
        pytest.param({"area_id": "studio", "temperature": 35.5, "duration": 1}, id="too hot"),
        pytest.param({"area_id": "studio", "duration": 1}, id="no temperature"),
    ],
)
async def test_invalid_calls_are_rejected_and_keep_the_room_unchanged(service_hass, store, data):
    """Every override needs one temperature in the panel's range and exactly one end."""
    with pytest.raises(vol.Invalid):
        await call_action(service_hass, **data)

    assert store.get_room("studio").get("override_heat") is None


async def test_new_override_takes_effect_on_the_coordinator(service_hass):
    """The room reacts now, not at the next control cycle, and any seed is dropped."""
    coordinator = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    service_hass.data[DOMAIN]["coordinator"] = coordinator

    await call_action(service_hass, area_id="studio", temperature=21, duration=1)

    coordinator.clear_override_seed.assert_called_once_with("studio")
    coordinator.async_request_refresh.assert_awaited_once()


async def test_integration_setup_registers_the_action(hass):
    """The action exists from HA start, so scripts can call it."""
    await async_setup(hass, {})

    assert hass.services.async_register.call_args.args[:2] == (DOMAIN, "set_override")

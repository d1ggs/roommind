"""The roommind.set_override action: a custom override with a chosen end."""

from __future__ import annotations

import time

import voluptuous as vol
from homeassistant.components.climate import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from ..climate import _band_from_temperature_call
from ..const import DOMAIN, OVERRIDE_CUSTOM

SERVICE_SET_OVERRIDE = "set_override"

SET_OVERRIDE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required("area_id"): cv.string,
            vol.Required(ATTR_TEMPERATURE): vol.All(vol.Coerce(float), vol.Range(min=5, max=35)),
            vol.Exclusive("until", "end"): cv.datetime,
            vol.Exclusive("duration", "end"): vol.All(vol.Coerce(float), vol.Range(min=0, min_included=False)),
        }
    ),
    cv.has_at_least_one_key("until", "duration"),
)


def async_register_services(hass: HomeAssistant) -> None:
    """Register RoomMind's actions."""

    async def _handle_set_override(call: ServiceCall) -> None:
        area_id = call.data["area_id"]
        store = hass.data.get(DOMAIN, {}).get("store")
        room = store.get_room(area_id) if store else None
        if room is None:
            raise ServiceValidationError(f"RoomMind has no room '{area_id}'")

        if "until" in call.data:
            # as_utc reads a time without zone as HA's local time
            override_until = dt_util.as_utc(call.data["until"]).timestamp()
            if override_until <= time.time():
                raise ServiceValidationError(f"Override end {call.data['until']} is in the past")
        else:
            override_until = time.time() + call.data["duration"] * 3600

        band = _band_from_temperature_call(
            {ATTR_TEMPERATURE: call.data[ATTR_TEMPERATURE]},
            room.get("climate_mode", "auto"),
            lambda: (None, None),  # only read for a low/high range, which this action does not take
            room,
        )
        assert band is not None  # temperature is required by the schema
        await store.async_update_room(
            area_id,
            {
                "override_heat": band[0],
                "override_cool": band[1],
                "override_until": override_until,
                "override_type": OVERRIDE_CUSTOM,
            },
        )

        coordinator = hass.data[DOMAIN].get("coordinator")
        if coordinator:
            coordinator.clear_override_seed(area_id)
            await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_SET_OVERRIDE, _handle_set_override, schema=SET_OVERRIDE_SCHEMA)

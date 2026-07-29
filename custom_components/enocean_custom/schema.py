"""Shared configuration validators for EnOcean identifiers."""

from __future__ import annotations

import math
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .const import UI_DEVICE_PLATFORMS


def exact_finite_int(value: object) -> int:
    """Coerce exact finite integers without bools or lossy truncation."""
    if isinstance(value, bool):
        raise vol.Invalid("expected integer, not boolean")
    try:
        integer = int(value)
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as err:
        raise vol.Invalid("expected finite integer") from err
    if not math.isfinite(numeric):
        raise vol.Invalid("expected finite integer")
    if numeric != integer:
        raise vol.Invalid("integer value must not require truncation")
    return integer


def strict_int(value: object) -> int:
    """Require a real Python integer, excluding booleans and all floats."""
    if type(value) is not int:
        raise vol.Invalid("expected strict integer")
    return value


BYTE = vol.All(strict_int, vol.Range(min=0, max=255))
ENOCEAN_ID = vol.All(cv.ensure_list, [BYTE], vol.Length(min=4, max=4))


def optional_enocean_id(value: object) -> list[int]:
    """Accept an omitted receive ID or validate one complete EnOcean ID."""
    values = cv.ensure_list(value)
    if not values:
        return []
    return ENOCEAN_ID(values)


# Config entry options: devices added and removed entirely from the UI.
CONF_UI_DEVICES = "ui_devices"


def _light_requires_sender_id(device: dict) -> dict:
    """A UI light without a sender id would crash on its first command."""
    if device["platform"] == "light" and not device.get("sender_id"):
        raise vol.Invalid("sender_id is required for light devices")
    return device


UI_DEVICE_SCHEMA = vol.Schema(
    vol.All(
        {
            vol.Required("id"): ENOCEAN_ID,
            vol.Required("platform"): vol.In(UI_DEVICE_PLATFORMS),
            vol.Required("name"): cv.string,
            vol.Optional("device_class", default=None): vol.Any(None, cv.string),
            vol.Optional("channel", default=0): vol.All(
                exact_finite_int, vol.Range(min=0, max=255)
            ),
            vol.Optional("switch_type", default=None): vol.Any(
                None, vol.In(("default", "RPS"))
            ),
            vol.Optional("sender_id", default=None): vol.Any(None, ENOCEAN_ID),
        },
        _light_requires_sender_id,
    )
)

UI_DEVICES_SCHEMA = vol.All(cv.ensure_list, [UI_DEVICE_SCHEMA])


def valid_ui_devices(value: Any) -> list[dict]:
    """Return only the well-formed UI devices from a raw options value.

    Config entry options can be hand-edited in .storage; platform setup must
    never crash on a malformed entry, it skips the bad rows instead.
    """
    if not isinstance(value, list):
        return []
    valid: list[dict] = []
    for device in value:
        try:
            valid.append(UI_DEVICE_SCHEMA(device))
        except vol.Invalid:
            continue
    return valid

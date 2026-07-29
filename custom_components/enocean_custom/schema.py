"""Shared configuration validators for EnOcean identifiers."""

from __future__ import annotations

import math
from datetime import timedelta
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


def _ensure_finite(value: float) -> float:
    """Reject NaN and infinities in persisted UI options."""
    if not math.isfinite(value):
        raise vol.Invalid("expected finite number")
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


def _validate_platform_fields(device: dict) -> dict:
    """Validate and populate platform-specific UI fields."""
    if device["platform"] == "light" and not device.get("sender_id"):
        raise vol.Invalid("sender_id is required for light devices")
    if device["platform"] == "climate":
        if not device.get("id_switch"):
            raise vol.Invalid("id_switch is required for climate devices")
        if not device.get("device_type"):
            raise vol.Invalid("device_type is required for climate devices")
        if not device.get("sensor_entity_id"):
            raise vol.Invalid("sensor_entity_id is required for climate devices")
        if cv.positive_time_period(device["command_frequency"]) < timedelta(seconds=1):
            raise vol.Invalid("command_frequency must be at least one second")
    if device["platform"] == "sensor":
        if device["device_class"] is None:
            device["device_class"] = "powersensor"
        if device["device_class"] not in (
            "humidity",
            "powersensor",
            "temperature",
            "windowhandle",
            "shuttercontact",
        ):
            raise vol.Invalid("unsupported sensor device_class")
        if (
            device["device_class"] == "temperature"
            and device["min_temp"] >= device["max_temp"]
        ):
            raise vol.Invalid("min_temp must be lower than max_temp")
        if (
            device["device_class"] == "temperature"
            and device["range_from"] == device["range_to"]
        ):
            raise vol.Invalid("range_from and range_to must differ")
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
            vol.Optional("id_switch", default=None): vol.Any(None, ENOCEAN_ID),
            vol.Optional("device_type", default=None): vol.Any(
                None, vol.In(("SRC-D08",))
            ),
            vol.Optional("sensor_entity_id", default=None): vol.Any(None, cv.entity_id),
            vol.Optional("temperature_frost_protection", default=8.0): vol.All(
                vol.Coerce(float), _ensure_finite, vol.Range(min=0, max=20)
            ),
            vol.Optional("sensor_target_temperature_range", default=5): vol.All(
                exact_finite_int, vol.Range(min=0, max=20)
            ),
            vol.Optional(
                "sensor_target_temperature_update_tolerance", default=0.5
            ): vol.All(vol.Coerce(float), _ensure_finite, vol.Range(min=0, max=10)),
            vol.Optional("target_temperature_base_value", default=21.0): vol.All(
                vol.Coerce(float), _ensure_finite, vol.Range(min=5, max=35)
            ),
            vol.Optional("target_temperature_reduction_night", default=4.0): vol.All(
                vol.Coerce(float), _ensure_finite, vol.Range(min=0, max=20)
            ),
            vol.Optional("command_frequency", default="00:17:00"): cv.string,
            vol.Optional("pi_control_Kp", default=5.0): vol.All(
                vol.Coerce(float), _ensure_finite, vol.Range(min=0.001, max=100)
            ),
            vol.Optional("pi_control_Tn", default=240.0): vol.All(
                vol.Coerce(float), _ensure_finite, vol.Range(min=0.001, max=1440)
            ),
            vol.Optional("max_temp", default=40): vol.All(
                exact_finite_int, vol.Range(min=-100, max=100)
            ),
            vol.Optional("min_temp", default=0): vol.All(
                exact_finite_int, vol.Range(min=-100, max=100)
            ),
            vol.Optional("range_from", default=255): vol.All(
                exact_finite_int, vol.Range(min=0, max=255)
            ),
            vol.Optional("range_to", default=0): vol.All(
                exact_finite_int, vol.Range(min=0, max=255)
            ),
        },
        _validate_platform_fields,
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

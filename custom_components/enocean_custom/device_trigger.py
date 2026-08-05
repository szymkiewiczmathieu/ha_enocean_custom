"""Device triggers for EnOcean RPS rocker events."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import voluptuous as vol
from homeassistant.components.device_automation import (
    DEVICE_TRIGGER_BASE_SCHEMA,
    InvalidDeviceAutomationConfig,
)
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .binary_sensor import A514_CONTACT_UNIQUE_ID_SUFFIX, EVENT_BUTTON_PRESSED
from .const import DOMAIN

# Every trigger type maps to the exact subset of button_pressed event data it
# must match. The delegated event trigger compares those pairs for equality, so
# a telegram that is neither a press nor a release -- binary_sensor emits
# ``pushed: None`` for those -- can never satisfy any entry below.
TRIGGER_TYPES: dict[str, dict[str, Any]] = {
    "button_pressed": {"pushed": 1},
    "button_released": {"pushed": 0},
    "button_pressed_channel_0": {"pushed": 1, "which": 0},
    "button_pressed_channel_1": {"pushed": 1, "which": 1},
}

CONF_WHICH = "which"
CONF_ONOFF = "onoff"
WHICH_OPTIONS = (0, 1, 10)
ONOFF_OPTIONS = (0, 1)


def _select_int(options: tuple[int, ...]) -> Callable[[Any], int]:
    """Accept an integer or the numeric string emitted by HA SelectSelector."""
    string_options = {str(value): value for value in options}

    def validate(value: Any) -> int:
        if type(value) is int and value in options:
            return value
        if type(value) is str and value in string_options:
            return string_options[value]
        raise vol.Invalid(f"value must be one of {options}")

    return validate


_OPTIONAL_FILTERS: dict[str, Callable[[Any], int]] = {
    CONF_WHICH: _select_int(WHICH_OPTIONS),
    CONF_ONOFF: _select_int(ONOFF_OPTIONS),
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(sorted(TRIGGER_TYPES)),
        vol.Optional(CONF_WHICH): _OPTIONAL_FILTERS[CONF_WHICH],
        vol.Optional(CONF_ONOFF): _OPTIONAL_FILTERS[CONF_ONOFF],
    }
)

# Device identifiers are minted as four uppercase hex bytes by EnOceanEntity.
# int(..., 16) alone would accept signs and whitespace, so the shape is checked
# before parsing rather than after.
_SENDER_PATTERN = re.compile(r"\A[0-9A-Fa-f]{8}\Z")


def _radio_bytes(device: dr.DeviceEntry | None) -> list[int] | None:
    """Return the four radio bytes of an EnOcean device, or None if it has none."""
    if device is None:
        return None
    for domain, identifier in device.identifiers:
        if domain != DOMAIN:
            continue
        if not _SENDER_PATTERN.match(identifier):
            return None
        return [int(identifier[index : index + 2], 16) for index in range(0, 8, 2)]
    return None


def _radio_id(hass: HomeAssistant, device_id: str) -> list[int]:
    """Resolve an EnOcean device registry entry to its four radio bytes."""
    radio_id = _radio_bytes(dr.async_get(hass).async_get(device_id))
    if radio_id is None:
        raise InvalidDeviceAutomationConfig(
            f"Device ID {device_id} is not a valid {DOMAIN} device"
        )
    return radio_id


def _has_rocker(hass: HomeAssistant, device_id: str) -> bool:
    """Report whether the device owns an enabled rocker binary sensor.

    A5-14-01 contacts share the platform but decode 4BS telegrams and never emit
    button_pressed, so they are matched on their own unique_id rather than on
    device_class, which stays free-form user input for rockers.
    """
    return any(
        entry.domain == "binary_sensor"
        and entry.platform == DOMAIN
        and not (entry.unique_id or "").endswith(A514_CONTACT_UNIQUE_ID_SUFFIX)
        for entry in er.async_entries_for_device(er.async_get(hass), device_id)
    )


async def async_validate_trigger_config(
    hass: HomeAssistant, config: ConfigType
) -> ConfigType:
    """Validate an EnOcean device trigger configuration."""
    config = TRIGGER_SCHEMA(config)
    fixed_which = TRIGGER_TYPES[config[CONF_TYPE]].get(CONF_WHICH)
    if (
        fixed_which is not None
        and CONF_WHICH in config
        and config[CONF_WHICH] != fixed_which
    ):
        raise vol.Invalid(
            f"Trigger type {config[CONF_TYPE]} requires which={fixed_which}"
        )
    _radio_id(hass, config[CONF_DEVICE_ID])
    return config


async def async_get_trigger_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    """Return optional event-data filters for an EnOcean device trigger."""
    return {
        "extra_fields": vol.Schema(
            {
                vol.Optional(CONF_WHICH): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[str(value) for value in WHICH_OPTIONS],
                        custom_value=False,
                    )
                ),
                vol.Optional(CONF_ONOFF): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[str(value) for value in ONOFF_OPTIONS],
                        custom_value=False,
                    )
                ),
            }
        )
    }


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach an event-backed EnOcean rocker trigger."""
    event_data: dict[str, Any] = {
        "id": _radio_id(hass, config[CONF_DEVICE_ID]),
        **TRIGGER_TYPES[config[CONF_TYPE]],
    }
    # Re-validated defensively: HA always normalizes via async_validate_trigger_config
    # before attaching (setup, reload, and the live "test trigger" websocket preview
    # all call it), but a raw string from a SelectSelector would silently never match
    # the int the event carries, so this is cheap insurance against a caller that
    # skips that step.
    for optional_key, validate in _OPTIONAL_FILTERS.items():
        if optional_key in config:
            event_data[optional_key] = validate(config[optional_key])
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_BUTTON_PRESSED,
            event_trigger.CONF_EVENT_DATA: event_data,
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """Return native triggers for a device containing an RPS rocker entity."""
    if _radio_bytes(dr.async_get(hass).async_get(device_id)) is None:
        return []
    if not _has_rocker(hass, device_id):
        return []

    return [
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in sorted(TRIGGER_TYPES)
    ]

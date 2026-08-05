"""Ephemeral inventory of legacy YAML devices offered for UI migration."""

from __future__ import annotations

from threading import Lock
from typing import Any

import voluptuous as vol
from homeassistant.const import CONF_DEVICE_CLASS, CONF_ID, CONF_NAME
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .schema import UI_DEVICE_SCHEMA

DATA_YAML_DEVICE_CONFIGS = "yaml_device_configs"
DATA_YAML_NON_IMPORTABLE = "yaml_non_importable"
DATA_YAML_INVALID = "yaml_invalid"
IMPORTABLE_PLATFORMS = ("binary_sensor", "switch")

# binary_sensor and sensor expose a synchronous setup_platform, which Home
# Assistant runs in its executor: several YAML blocks are tracked concurrently
# and off the event loop, so every read-modify-write here is serialized.
_INVENTORY_LOCK = Lock()


def _domain_data(hass: HomeAssistant) -> dict[str, Any]:
    """Return integration runtime data without persisting it."""
    return hass.data.setdefault(DOMAIN, {})


def _stored_device_class(value: Any) -> str | None:
    """Render device_class exactly as the YAML entity embeds it.

    DEVICE_CLASSES_SCHEMA yields a BinarySensorDeviceClass member, which the
    legacy entity formats into f"{combine_hex(id)}-{device_class}". Formatting
    it the same way keeps the imported identity byte-identical while persisting
    a plain string, and None stays None so an undeclared device class keeps
    producing the mandatory "-None" suffix.
    """
    return None if value is None else f"{value}"


def track_yaml_device(
    hass: HomeAssistant, platform: str, config: dict[str, Any]
) -> None:
    """Record one normalized YAML device, or only its failure/platform count."""
    if platform not in IMPORTABLE_PLATFORMS:
        with _INVENTORY_LOCK:
            counts = _domain_data(hass).setdefault(DATA_YAML_NON_IMPORTABLE, {})
            counts[platform] = counts.get(platform, 0) + 1
        return

    try:
        # Validate once, here, and keep the schema output: the options flow
        # then persists an already-valid row and can never fail mid-import.
        row = UI_DEVICE_SCHEMA(
            {
                CONF_ID: list(config[CONF_ID]),
                "platform": platform,
                CONF_NAME: config[CONF_NAME],
                CONF_DEVICE_CLASS: _stored_device_class(
                    config.get(CONF_DEVICE_CLASS)
                ),
                "channel": config.get("channel", 0),
                "switch_type": config.get("switch_type", "default"),
            }
        )
    except (KeyError, TypeError, vol.Invalid):
        with _INVENTORY_LOCK:
            data = _domain_data(hass)
            data[DATA_YAML_INVALID] = data.get(DATA_YAML_INVALID, 0) + 1
        return
    with _INVENTORY_LOCK:
        _domain_data(hass).setdefault(DATA_YAML_DEVICE_CONFIGS, []).append(row)


def yaml_inventory(
    hass: HomeAssistant,
) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    """Return copies of importable rows, non-importable counts and failures."""
    data = hass.data.get(DOMAIN, {})
    with _INVENTORY_LOCK:
        # Copy every row: what the options flow persists must never alias the
        # in-memory inventory, which is discarded on unload.
        return (
            [dict(row) for row in data.get(DATA_YAML_DEVICE_CONFIGS, [])],
            dict(data.get(DATA_YAML_NON_IMPORTABLE, {})),
            int(data.get(DATA_YAML_INVALID, 0)),
        )


def purge_yaml_inventory(hass: HomeAssistant) -> None:
    """Forget all YAML migration data when the integration unloads."""
    data = hass.data.get(DOMAIN, {})
    with _INVENTORY_LOCK:
        data.pop(DATA_YAML_DEVICE_CONFIGS, None)
        data.pop(DATA_YAML_NON_IMPORTABLE, None)
        data.pop(DATA_YAML_INVALID, None)

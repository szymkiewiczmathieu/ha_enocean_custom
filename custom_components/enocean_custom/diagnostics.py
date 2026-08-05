"""Diagnostics support for the EnOcean config entry."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant

from .dongle import EnOceanDongle
from .inbox import get_device_inbox

TO_REDACT = {
    CONF_DEVICE,
    "id",
    "sender_id",
    "product_id",
    "product_reference",
    "manufacturer_id",
    "radio_metadata",
    "ui_devices",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[EnOceanDongle]
) -> dict[str, Any]:
    """Return config and serial-worker health without exposing the full path."""
    inbox = get_device_inbox(hass)
    return {
        "config_entry": async_redact_data(dict(entry.data), TO_REDACT),
        "dongle": entry.runtime_data.diagnostics(),
        "inbox": {
            "observed_senders_count": inbox.observed_senders_count if inbox else 0
        },
    }

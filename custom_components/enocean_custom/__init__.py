"""Set up the EnOcean Custom integration."""

from __future__ import annotations

import serial
import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    DATA_DEVICE_INBOX,
    DATA_ENOCEAN,
    DOMAIN,
    ENOCEAN_DONGLE,
    UI_DEVICE_PLATFORMS,
)
from .dongle import EnOceanDongle
from .enocean_library.utils import combine_hex
from .inbox import DeviceInbox
from .learn import async_register_learn_service, get_known_ids
from .schema import CONF_UI_DEVICES, valid_ui_devices

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Required(CONF_DEVICE): cv.string})},
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Import a legacy YAML gateway when no config entry exists."""
    if DOMAIN not in config or hass.config_entries.async_entries(DOMAIN):
        return True
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data=config[DOMAIN],
        )
    )
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[EnOceanDongle],
) -> bool:
    """Start the serial owner and forward all UI-capable platforms."""
    integration_data = hass.data.setdefault(DATA_ENOCEAN, {})
    try:
        gateway = await hass.async_add_executor_job(
            EnOceanDongle,
            hass,
            entry.data[CONF_DEVICE],
        )
    except serial.SerialException:
        raise ConfigEntryNotReady(
            "Unable to open the configured EnOcean dongle"
        ) from None

    integration_data[ENOCEAN_DONGLE] = gateway

    def _configured_ids() -> set[int]:
        configured = set(get_known_ids(hass))
        for row in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, [])):
            configured.add(combine_hex(row["id"]))
        return configured

    inbox = DeviceInbox(_configured_ids)
    integration_data[DATA_DEVICE_INBOX] = inbox
    gateway.set_inbox(inbox)
    entry.runtime_data = gateway
    await gateway.async_setup()
    async_register_learn_service(hass)

    try:
        await hass.config_entries.async_forward_entry_setups(
            entry,
            UI_DEVICE_PLATFORMS,
        )
    except Exception:
        gateway.set_inbox(None)
        await gateway.async_unload()
        integration_data.pop(ENOCEAN_DONGLE, None)
        integration_data.pop(DATA_DEVICE_INBOX, None)
        inbox.clear()
        entry.runtime_data = None
        raise

    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options_update))
    return True


async def _async_reload_on_options_update(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Reload entities after the options flow changes its device list."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[EnOceanDongle],
) -> bool:
    """Stop platforms and the serial thread before permitting a reload."""
    platforms_unloaded = await hass.config_entries.async_unload_platforms(
        entry,
        UI_DEVICE_PLATFORMS,
    )
    if not platforms_unloaded:
        return False

    # A teach-in window is intentionally not entry-scoped. It remains active
    # across an options-triggered reload and closes on its own bounded timeout.
    gateway = entry.runtime_data
    inbox = hass.data.get(DATA_ENOCEAN, {}).get(DATA_DEVICE_INBOX)
    gateway.set_inbox(None)
    if not await gateway.async_unload():
        gateway.set_inbox(inbox)
        return False

    hass.data.get(DATA_ENOCEAN, {}).pop(ENOCEAN_DONGLE, None)
    if inbox is not None:
        inbox.clear()
    hass.data.get(DATA_ENOCEAN, {}).pop(DATA_DEVICE_INBOX, None)
    return True

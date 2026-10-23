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
    CONFIG_ENTRY_MINOR_VERSION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    LOGGER,
    UI_DEVICE_PLATFORMS,
)
from .dongle import EnOceanDongle
from .enocean_library.utils import combine_hex
from .inbox import DeviceInbox
from .learn import async_register_learn_service, get_known_ids
from .migration import explicit_rps_actuator_profiles
from .schema import CONF_UI_DEVICES, valid_ui_devices
from .yaml_import import purge_yaml_inventory

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Required(CONF_DEVICE): cv.string})},
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration-wide action and import a legacy YAML gateway.

    ``enocean_custom.learn`` does not depend on a loaded entry, so it is
    registered here rather than in ``async_setup_entry`` (Quality Scale
    ``action-setup``): a call made while the entry is unloaded fails with a
    clear error instead of "service not found".
    """
    async_register_learn_service(hass)
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


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Rewrite options stored by an older release; entry data never changes.

    Minor version 2 makes the former runtime inference of the Ubiwizz
    UBID1507C profile explicit in the affected ``RPS`` switch rows (see
    ``migration.py``). A newer minor version (downgrade scenario) is left
    alone; a newer major version is refused.
    """
    if entry.version > CONFIG_ENTRY_VERSION:
        return False
    if entry.minor_version < 2:
        rows, changed = explicit_rps_actuator_profiles(
            entry.options.get(CONF_UI_DEVICES, [])
        )
        options = dict(entry.options)
        if changed:
            options[CONF_UI_DEVICES] = rows
        hass.config_entries.async_update_entry(
            entry, options=options, minor_version=CONFIG_ENTRY_MINOR_VERSION
        )
        LOGGER.info(
            "Migrated EnOcean options to minor version %d: %d RPS switch rows "
            "now carry the explicit ubiwizz_ubid1507c profile",
            CONFIG_ENTRY_MINOR_VERSION,
            changed,
        )
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[EnOceanDongle],
) -> bool:
    """Start the serial owner and forward all UI-capable platforms.

    The dongle and its passive inbox live on ``entry.runtime_data`` only;
    platforms and flows resolve them through ``runtime.get_gateway``.
    """
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

    def _configured_ids() -> set[int]:
        configured = set(get_known_ids(hass))
        for row in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, [])):
            configured.add(combine_hex(row["id"]))
        return configured

    inbox = DeviceInbox(_configured_ids)
    gateway.set_inbox(inbox)
    entry.runtime_data = gateway
    await gateway.async_setup()

    try:
        await hass.config_entries.async_forward_entry_setups(
            entry,
            UI_DEVICE_PLATFORMS,
        )
    except Exception:
        gateway.set_inbox(None)
        await gateway.async_unload()
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
    inbox = gateway.inbox
    gateway.set_inbox(None)
    if not await gateway.async_unload():
        gateway.set_inbox(inbox)
        return False

    if inbox is not None:
        inbox.clear()
    purge_yaml_inventory(hass)
    return True

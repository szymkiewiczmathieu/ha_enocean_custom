"""Support for EnOcean devices."""

from __future__ import annotations

import homeassistant.helpers.config_validation as cv
import serial
import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType

from .const import DATA_ENOCEAN, DOMAIN, ENOCEAN_DONGLE, UI_DEVICE_PLATFORMS
from .dongle import EnOceanDongle
from .learn import async_register_learn_service

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Required(CONF_DEVICE): cv.string})}, extra=vol.ALLOW_EXTRA
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the EnOcean component."""
    # support for text-based configuration (legacy)
    if DOMAIN not in config:
        return True

    if hass.config_entries.async_entries(DOMAIN):
        # We can only have one dongle. If there is already one in the config,
        # there is no need to import the yaml based config.
        return True

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=config[DOMAIN]
        )
    )

    return True


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry[EnOceanDongle]
) -> bool:
    """Set up an EnOcean dongle for the given entry."""
    enocean_data = hass.data.setdefault(DATA_ENOCEAN, {})
    try:
        usb_dongle = await hass.async_add_executor_job(
            EnOceanDongle, hass, config_entry.data[CONF_DEVICE]
        )
    except serial.SerialException:
        raise ConfigEntryNotReady(
            "Unable to open the configured EnOcean dongle"
        ) from None
    enocean_data[ENOCEAN_DONGLE] = usb_dongle
    config_entry.runtime_data = usb_dongle
    await usb_dongle.async_setup()

    async_register_learn_service(hass)
    try:
        await hass.config_entries.async_forward_entry_setups(
            config_entry, UI_DEVICE_PLATFORMS
        )
    except Exception:
        # Never leave a live serial reader behind when platform setup fails:
        # a retry must find the port free, not busy on a zombie owner. Drop
        # the reference as well so a retry builds a fresh dongle instead of
        # reusing the unloaded one (review finding: rollback residue).
        await usb_dongle.async_unload()
        enocean_data.pop(ENOCEAN_DONGLE, None)
        config_entry.runtime_data = None
        raise
    config_entry.async_on_unload(
        config_entry.add_update_listener(_async_reload_on_options_update)
    )

    return True


async def _async_reload_on_options_update(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> None:
    """Reload the entry so added/removed UI devices take effect immediately."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, config_entry: ConfigEntry[EnOceanDongle]
) -> bool:
    """Unload ENOcean config entry."""

    if not await hass.config_entries.async_unload_platforms(
        config_entry, UI_DEVICE_PLATFORMS
    ):
        return False

    # Deliberately do NOT stop an open teach-in window here: the dispatcher
    # registry lives in hass (not in the entry), so a learn window survives
    # an options-save reload — killing it would abort the waiting UI flow
    # with a misleading timeout. An orphan window self-closes on its own
    # bounded timeout (<= 300 s) even if the entry is removed for good.
    enocean_dongle = config_entry.runtime_data
    if not await enocean_dongle.async_unload():
        # Do not allow Home Assistant to create a second serial reader while
        # the old one is still alive.
        return False

    # Only the dongle is entry-scoped. known_ids/learn_manager stay in
    # hass.data across reloads: YAML setup_platform runs once at startup and
    # would not re-seed known_ids on a later entry reload.
    hass.data.get(DATA_ENOCEAN, {}).pop(ENOCEAN_DONGLE, None)

    return True

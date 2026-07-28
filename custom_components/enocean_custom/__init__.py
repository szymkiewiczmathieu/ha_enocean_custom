"""Support for EnOcean devices."""
import homeassistant.helpers.config_validation as cv
import serial
import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType

from .const import DATA_ENOCEAN, DOMAIN, ENOCEAN_DONGLE
from .dongle import EnOceanDongle

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


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up an EnOcean dongle for the given entry."""
    enocean_data = hass.data.setdefault(DATA_ENOCEAN, {})
    try:
        usb_dongle = await hass.async_add_executor_job(
            EnOceanDongle, hass, config_entry.data[CONF_DEVICE]
        )
    except serial.SerialException as err:
        raise ConfigEntryNotReady(
            f"Unable to open EnOcean serial port {config_entry.data[CONF_DEVICE]}"
        ) from err
    await usb_dongle.async_setup()
    enocean_data[ENOCEAN_DONGLE] = usb_dongle

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload ENOcean config entry."""

    enocean_dongle = hass.data[DATA_ENOCEAN][ENOCEAN_DONGLE]
    if not await enocean_dongle.async_unload():
        # Do not allow Home Assistant to create a second serial reader while
        # the old one is still alive.
        return False

    hass.data.pop(DATA_ENOCEAN)

    return True

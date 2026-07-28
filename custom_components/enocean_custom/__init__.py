"""Support for EnOcean devices."""
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import dispatcher_send
from homeassistant.helpers.typing import ConfigType

from .const import DATA_ENOCEAN, DOMAIN, ENOCEAN_DONGLE, LOGGER, SIGNAL_SEND_MESSAGE
from .dongle import EnOceanDongle
from .enocean_library.protocol.packet import Packet

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Required(CONF_DEVICE): cv.string})}, extra=vol.ALLOW_EXTRA
)

BYTE = vol.All(vol.Coerce(int), vol.Range(min=0, max=255))
BYTE_LIST = vol.All(cv.ensure_list, [BYTE])
SEND_PACKET_SCHEMA = vol.Schema(
    {
        vol.Optional("packet_type", default=0x01): BYTE,
        vol.Optional("optional", default=[]): BYTE_LIST,
        vol.Optional("data", default=[0xD5, 0x00]): BYTE_LIST,
        vol.Optional("status", default=[0x00]): vol.All(
            BYTE_LIST, vol.Length(min=1, max=1)
        ),
        vol.Optional("sender_id", default=[0xFF, 0xFF, 0xFF, 0xFF]): vol.All(
            BYTE_LIST, vol.Length(min=4, max=4)
        ),
    }
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
    usb_dongle = await hass.async_add_executor_job(
        EnOceanDongle, hass, config_entry.data[CONF_DEVICE]
    )
    await usb_dongle.async_setup()
    enocean_data[ENOCEAN_DONGLE] = usb_dongle

    def send_packet(call):
        """service call"""
        LOGGER.debug("Service called with data %s", call.data)

        packet_type = call.data["packet_type"]
        optional = call.data["optional"]
        data = call.data["data"]
        status = call.data["status"]
        sender_id = call.data["sender_id"]

        packet_data = [*data, *sender_id, *status]
        packet = Packet(packet_type, packet_data, optional)
        dispatcher_send(hass, SIGNAL_SEND_MESSAGE, packet)

    hass.services.async_register(
        DOMAIN, "send_packet", send_packet, schema=SEND_PACKET_SCHEMA
    )

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload ENOcean config entry."""

    enocean_dongle = hass.data[DATA_ENOCEAN][ENOCEAN_DONGLE]
    if not await enocean_dongle.async_unload():
        # Do not allow Home Assistant to create a second serial reader while
        # the old one is still alive.
        return False

    hass.services.async_remove(DOMAIN, "send_packet")
    hass.data.pop(DATA_ENOCEAN)

    return True

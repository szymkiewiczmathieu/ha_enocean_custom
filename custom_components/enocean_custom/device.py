"""Representation of an EnOcean device."""

from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import (
    DATA_ENOCEAN,
    ENOCEAN_DONGLE,
    LOGGER,
    SIGNAL_DONGLE_STATUS,
    SIGNAL_RECEIVE_MESSAGE,
)
from .enocean_library.protocol.packet import Packet
from .enocean_library.utils import combine_hex


def build_radio_optional(destination: list[int] | None = None) -> list[int]:
    """Build the seven ESP3 optional bytes required for outbound ERP1 radio."""
    return [0x03, *(destination or [0xFF] * 4), 0xFF, 0x00]


class EnOceanEntity(Entity):
    """Parent class for all entities associated with the EnOcean component."""

    _attr_should_poll = False

    def __init__(self, dev_id, dev_name="EnOcean device"):
        """Initialize the device."""
        self.dev_id = dev_id
        self.dev_name = dev_name
        self._attr_available = False

    async def async_added_to_hass(self):
        """Register packet and serial-health callbacks."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_RECEIVE_MESSAGE, self._message_received_callback
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_DONGLE_STATUS, self._dongle_status_changed
            )
        )
        enocean_data = self.hass.data.get(DATA_ENOCEAN, {})
        dongle = enocean_data.get(ENOCEAN_DONGLE)
        self._attr_available = bool(dongle and dongle.available)

    def _dongle_status_changed(self, available: bool) -> None:
        """Mirror serial-worker availability on every EnOcean entity."""
        self._attr_available = available
        self.async_write_ha_state()

    def _message_received_callback(self, packet):
        """Handle incoming packets."""

        if self.dev_id and packet.sender_int == combine_hex(self.dev_id):
            try:
                self.value_changed(packet)
            except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError):
                LOGGER.warning(
                    "Ignoring malformed EnOcean packet for %s",
                    self.dev_name,
                    exc_info=True,
                )

    def value_changed(self, packet):
        """Update the internal state of the device when a packet arrives."""

    def send_command(self, data, optional, packet_type, response_callback=None) -> bool:
        """Send a command via the EnOcean dongle."""
        packet = Packet(packet_type, data=data, optional=optional)
        enocean_data = self.hass.data.get(DATA_ENOCEAN, {})
        if (dongle := enocean_data.get(ENOCEAN_DONGLE)) is None:
            self._attr_available = False
            return False
        if dongle.send(packet, response_callback=response_callback):
            return True
        self._attr_available = False
        self.schedule_update_ha_state()
        LOGGER.warning("EnOcean command for %s was rejected", self.dev_name)
        return False

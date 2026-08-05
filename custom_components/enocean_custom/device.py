"""Shared entity support for the vendored ESP3 transport."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import (
    DATA_ENOCEAN,
    DOMAIN,
    ENOCEAN_DONGLE,
    LOGGER,
    SIGNAL_DONGLE_STATUS,
    SIGNAL_RECEIVE_MESSAGE,
)
from .device_intelligence import resolve_product
from .enocean_library.protocol.packet import Packet as ESP3Packet
from .enocean_library.utils import combine_hex as enocean_id_as_int


def build_radio_optional(destination: list[int] | None = None) -> list[int]:
    """Return the seven optional bytes required by an ERP1 radio frame."""
    radio_destination = destination if destination is not None else [0xFF] * 4
    return [0x03, *radio_destination, 0xFF, 0x00]


class EnOceanEntity(Entity):
    """Base entity bound to one four-byte EnOcean sender identity."""

    _attr_should_poll = False

    def __init__(
        self,
        dev_id: list[int],
        dev_name: str = "EnOcean device",
    ) -> None:
        """Store identity and initialize transport-derived availability."""
        self.dev_id = dev_id
        self._device_identifier_id = dev_id
        self.dev_name = dev_name
        self._attr_available = False
        self._d2_status = None
        self._last_status: str | None = None
        self._radio_metadata: dict[str, Any] = {}

    def set_radio_metadata(self, metadata: object) -> EnOceanEntity:
        """Attach already schema-validated safe metadata and return this entity."""
        self._radio_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        return self

    @property
    def device_info(self) -> DeviceInfo | None:
        """Group every entity of one sender without inventing product identity.

        A declared EEP is a data profile, not a model, so it never becomes a
        DeviceInfo model string. Manufacturer/model are resolved at runtime from
        the exact Product ID catalog and are absent for everything else, which
        keeps YAML devices grouped but unnamed rather than mislabeled.
        """
        identifier = self._device_identifier_id
        if not isinstance(identifier, (list, tuple)) or len(identifier) != 4:
            return None
        sender = "".join(f"{byte:02X}" for byte in identifier)
        info: dict[str, Any] = {
            "identifiers": {(DOMAIN, sender)},
            "name": self.dev_name,
        }
        if (product := resolve_product(self._radio_metadata)) is not None:
            info["manufacturer"] = product.manufacturer
            info["model"] = product.model
            info["model_id"] = product.model_id
        return DeviceInfo(**info)

    @property
    def d2_status_attributes(self) -> dict[str, Any]:
        """Describe the last valid D2-01 status response."""
        status = self._d2_status
        if status is None:
            return {}
        return {
            "d2_channel": status.channel,
            "d2_output_value": status.output_value,
            "d2_power_failure_detection_enabled": (
                status.power_failure_detection_enabled
            ),
            "d2_power_failure_detected": status.power_failure_detected,
            "last_status": self._last_status,
        }

    def record_d2_status(self, status: Any) -> None:
        """Save feedback metadata on Home Assistant's event-loop thread."""
        self._d2_status = status
        self._last_status = datetime.now(UTC).isoformat()

    async def async_added_to_hass(self) -> None:
        """Connect radio and serial-health dispatcher callbacks."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_RECEIVE_MESSAGE,
                self._message_received_callback,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_DONGLE_STATUS,
                self._dongle_status_changed,
            )
        )
        integration_data = self.hass.data.get(DATA_ENOCEAN, {})
        gateway = integration_data.get(ENOCEAN_DONGLE)
        self._attr_available = bool(gateway and gateway.available)

    @callback
    def _dongle_status_changed(self, available: bool) -> None:
        """Mirror serial health and write state on the event loop."""
        self._attr_available = available
        self.async_write_ha_state()

    @callback
    def _message_received_callback(self, packet: Any) -> None:
        """Route telegrams from this entity's sender only."""
        if not self.dev_id or packet.sender_int != enocean_id_as_int(self.dev_id):
            return
        try:
            self.value_changed(packet)
        except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError):
            LOGGER.warning(
                "Ignoring malformed EnOcean packet for %s",
                self.dev_name,
                exc_info=True,
            )

    def value_changed(self, packet: Any) -> None:
        """Handle one matching inbound telegram."""

    def send_command(
        self,
        data: list[int],
        optional: list[int],
        packet_type: int,
        response_callback=None,
    ) -> bool:
        """Queue an ESP3 packet through the entry-owned dongle."""
        packet = ESP3Packet(packet_type, data=data, optional=optional)
        integration_data = self.hass.data.get(DATA_ENOCEAN, {})
        gateway = integration_data.get(ENOCEAN_DONGLE)
        if gateway is None:
            self._attr_available = False
            return False
        if gateway.send(packet, response_callback=response_callback):
            return True
        self._attr_available = False
        self.schedule_update_ha_state()
        LOGGER.warning("EnOcean command for %s was rejected", self.dev_name)
        return False

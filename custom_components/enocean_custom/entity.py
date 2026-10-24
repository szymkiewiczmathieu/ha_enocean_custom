"""Shared entity support for the vendored ESP3 transport."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity, EntityCategory
from homeassistant.helpers.event import async_call_later

from .const import (
    DOMAIN,
    ISSUE_SILENT_DEVICE,
    LOGGER,
    SIGNAL_DONGLE_STATUS,
    SIGNAL_GATEWAY_UPDATE,
    SIGNAL_RECEIVE_MESSAGE,
)
from .device_intelligence import resolve_product
from .enocean_library.protocol.packet import Packet as ESP3Packet
from .enocean_library.utils import combine_hex as enocean_id_as_int
from .identity import gateway_device_identifier, gateway_unique_id
from .runtime import get_gateway


def build_radio_optional(destination: list[int] | None = None) -> list[int]:
    """Return the seven optional bytes required by an ERP1 radio frame."""
    radio_destination = destination if destination is not None else [0xFF] * 4
    return [0x03, *radio_destination, 0xFF, 0x00]


def get_gateway_base_id(gateway: object) -> list[int] | None:
    """Return a valid resolved dongle Base ID, never a guessed sender."""
    base_id = getattr(gateway, "base_id", None)
    if (
        not isinstance(base_id, (list, tuple))
        or len(base_id) != 4
        or any(isinstance(byte, bool) or not isinstance(byte, int) for byte in base_id)
        or any(not 0 <= byte <= 0xFF for byte in base_id)
    ):
        # The start-up read may have been refused or lost. Let the gateway
        # re-issue it within its own bounds so a later command can succeed
        # without a reload; this command is still skipped.
        retry = getattr(gateway, "retry_base_id", None)
        if callable(retry):
            retry()
        return None
    return list(base_id)


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
        # Silence detection (opt-in, see availability.py): the entity turns
        # unavailable when its sender has not been heard for the timeout.
        self._silence_timeout: timedelta | None = None
        self._silence_repair = False
        self._silent = False
        self._silence_unsub: CALLBACK_TYPE | None = None
        self._last_telegram_at: datetime | None = None

    def set_radio_metadata(self, metadata: object) -> EnOceanEntity:
        """Attach already schema-validated safe metadata and return this entity."""
        self._radio_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        return self

    def configure_silence(
        self, timeout: timedelta | None, *, repair: bool = False
    ) -> EnOceanEntity:
        """Arm silence detection with a timeout (None disables it)."""
        self._silence_timeout = timeout
        self._silence_repair = bool(repair) and timeout is not None
        return self

    @property
    def available(self) -> bool:
        """Mirror the dongle, and the sender's own silence when configured."""
        return bool(self._attr_available) and not self._silent

    @property
    def silent(self) -> bool:
        """Return whether silence detection currently marks this sender silent."""
        return self._silent

    @property
    def last_telegram_at(self) -> datetime | None:
        """Return when this entity last received a telegram from its sender."""
        return self._last_telegram_at

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
        # Every radio device is reached through the dongle of the config entry.
        gateway = get_gateway(getattr(self, "hass", None))
        entry_id = getattr(gateway, "entry_id", None)
        if entry_id:
            info["via_device"] = (DOMAIN, gateway_device_identifier(entry_id))
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
            # CMD 0x4 offset 16 (EEP 2.6.7 p.136): the one local setting the
            # actuator reports back; read-only, see d2_01.py on CMD 0x2.
            "d2_local_control_enabled": status.local_control_enabled,
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
        gateway = get_gateway(self.hass)
        self._attr_available = bool(gateway and gateway.available)
        if self._silence_timeout is not None:
            # The grace period starts now: a device that stays silent for a
            # whole timeout after start-up is flagged, nothing is assumed.
            self.async_on_remove(self._cancel_silence_timer)
            self._arm_silence_timer()

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
        self._note_telegram()
        try:
            self.value_changed(packet)
        except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError):
            LOGGER.warning(
                "Ignoring malformed EnOcean packet for %s",
                self.dev_name,
                exc_info=True,
            )

    @callback
    def _note_telegram(self) -> None:
        """Record a telegram from this sender and lift any silence."""
        self._last_telegram_at = datetime.now(UTC)
        if self._silence_timeout is None:
            return
        was_silent = self._silent
        self._silent = False
        self._arm_silence_timer()
        if was_silent:
            LOGGER.info("%s is transmitting again", self.dev_name)
            if self._silence_repair:
                ir.async_delete_issue(self.hass, DOMAIN, self._silence_issue_id())
            self.async_write_ha_state()

    def _silence_issue_id(self) -> str:
        return f"{ISSUE_SILENT_DEVICE}_{''.join(f'{byte:02X}' for byte in self.dev_id)}"

    @callback
    def _arm_silence_timer(self) -> None:
        self._cancel_silence_timer()
        if self._silence_timeout is None or getattr(self, "hass", None) is None:
            return
        self._silence_unsub = async_call_later(
            self.hass, self._silence_timeout, self._silence_expired
        )

    @callback
    def _cancel_silence_timer(self) -> None:
        if self._silence_unsub is not None:
            self._silence_unsub()
            self._silence_unsub = None

    @callback
    def _silence_expired(self, _now: Any = None) -> None:
        """Mark the sender silent once the timeout elapsed without a telegram."""
        self._silence_unsub = None
        if self._silent or self._silence_timeout is None:
            return
        self._silent = True
        minutes = int(self._silence_timeout.total_seconds() // 60)
        LOGGER.warning(
            "%s has not transmitted for %d minutes; marking it unavailable",
            self.dev_name,
            minutes,
        )
        if self._silence_repair:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._silence_issue_id(),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_SILENT_DEVICE,
                translation_placeholders={
                    "name": self.dev_name,
                    "minutes": str(minutes),
                },
            )
        self.async_write_ha_state()

    def value_changed(self, packet: Any) -> None:
        """Handle one matching inbound telegram."""

    def send_command(
        self,
        data: list[int],
        optional: list[int],
        packet_type: int,
        response_callback=None,
        written_callback=None,
    ) -> bool:
        """Queue an ESP3 packet through the entry-owned dongle."""
        packet = ESP3Packet(packet_type, data=data, optional=optional)
        gateway = get_gateway(self.hass)
        if gateway is None:
            self._attr_available = False
            return False
        if written_callback is None:
            accepted = gateway.send(packet, response_callback=response_callback)
        else:
            accepted = gateway.send(
                packet,
                response_callback=response_callback,
                written_callback=written_callback,
            )
        if accepted:
            return True
        self._attr_available = False
        self.schedule_update_ha_state()
        LOGGER.warning("EnOcean command for %s was rejected", self.dev_name)
        return False


class EnOceanGatewayEntity(Entity):
    """Diagnostic entity describing the dongle itself, not a radio device.

    Gateway entities stay available while the serial worker is down: that is
    precisely when a connectivity sensor or a last-telegram timestamp is worth
    reading. They refresh on the gateway/dongle signals and, for the telegram
    counters, on every dispatched radio telegram.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Subclasses that count telegrams opt in; the others ignore radio traffic.
    _tracks_telegrams = False

    def __init__(self, gateway: Any, entry_id: str, key: str) -> None:
        """Bind to the dongle of one config entry."""
        self._gateway = gateway
        self._entry_id = entry_id
        self._attr_unique_id = gateway_unique_id(entry_id, key)
        self._attr_translation_key = key

    @property
    def available(self) -> bool:
        """Gateway facts remain readable while the dongle is down."""
        return True

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the gateway device created by the config entry setup."""
        return DeviceInfo(
            identifiers={(DOMAIN, gateway_device_identifier(self._entry_id))}
        )

    async def async_added_to_hass(self) -> None:
        """Refresh on gateway facts, serial health and (optionally) telegrams."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_GATEWAY_UPDATE, self._gateway_updated
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_DONGLE_STATUS, self._dongle_status_changed
            )
        )
        if self._tracks_telegrams:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass, SIGNAL_RECEIVE_MESSAGE, self._telegram_received
                )
            )

    @callback
    def _gateway_updated(self) -> None:
        self.async_write_ha_state()

    @callback
    def _dongle_status_changed(self, _available: bool) -> None:
        self.async_write_ha_state()

    @callback
    def _telegram_received(self, _packet: Any) -> None:
        self.async_write_ha_state()

"""Switch platform for EnOcean actuators."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, override

import voluptuous as vol
from homeassistant.components.switch import (
    PLATFORM_SCHEMA as SWITCH_PLATFORM_SCHEMA,
)
from homeassistant.components.switch import (
    SwitchEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ID, CONF_NAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import (
    DATA_ENOCEAN,
    DOMAIN,
    ENOCEAN_DONGLE,
    LOGGER,
    SERVICE_UTE_COMMISSION,
)
from .device import EnOceanEntity, build_radio_optional, get_gateway_base_id
from .enocean_library.protocol.constants import RORG
from .enocean_library.protocol.d2 import parse_d2_01_actuator_status
from .enocean_library.utils import combine_hex
from .learn import register_known_id
from .schema import CONF_UI_DEVICES, ENOCEAN_ID, exact_finite_int, valid_ui_devices
from .ubiwizz import (
    UBID1507C,
    UBIWIZZ_ACTUATOR_PROFILES,
    valid_ubiwizz_actuator_channel,
)
from .ute import build_ute_teach_in_response, parse_ute_teach_in_request
from .yaml_import import track_yaml_device

CONF_ACTUATOR_PROFILE = "actuator_profile"
CONF_CHANNEL, CONF_SWITCH_TYPE = "channel", "switch_type"
CONF_EEP = "eep"
DEFAULT_NAME = "EnOcean Switch"
D2_SINGLE_CHANNEL_EEPS = frozenset(("D2-01-0A",))
# Operator-facing period to complete the local PRESS sequence. This is separate
# from UTE's responder deadline, which starts only when D4/A0 is received.
UTE_ASSOCIATION_WINDOW_SECONDS = 30.0

SWITCH_TYPES = ("default", "RPS")


def _validate_switch_config(config: ConfigType) -> ConfigType:
    """Reject channels outside the selected switch/profile contract."""
    channel = config[CONF_CHANNEL]
    if config[CONF_SWITCH_TYPE] == "RPS" and channel not in (0, 1):
        raise vol.Invalid("RPS channel must be 0 or 1")
    if config.get(CONF_EEP) in D2_SINGLE_CHANNEL_EEPS and channel != 0:
        raise vol.Invalid("D2-01-0A supports actuator channel 0 only")
    profile = config.get(CONF_ACTUATOR_PROFILE)
    if profile is not None and not valid_ubiwizz_actuator_channel(profile, channel):
        raise vol.Invalid(f"{profile} supports actuator channels 0 and 1 only")
    return config


PLATFORM_SCHEMA = vol.All(
    SWITCH_PLATFORM_SCHEMA.extend(
        {
            vol.Required(CONF_ID): ENOCEAN_ID,
            vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
            vol.Optional(CONF_CHANNEL, default=0): vol.All(
                exact_finite_int, vol.Range(min=0, max=31)
            ),
            vol.Optional(CONF_SWITCH_TYPE, default="default"): vol.In(SWITCH_TYPES),
            vol.Optional(CONF_EEP): vol.Match(r"^[0-9A-F]{2}-[0-9A-F]{2}-[0-9A-F]{2}$"),
            vol.Optional(CONF_ACTUATOR_PROFILE): vol.In(UBIWIZZ_ACTUATOR_PROFILES),
        }
    ),
    _validate_switch_config,
)


def generate_unique_id(dev_id: list[int], channel: int) -> str:
    """Build the channel-aware identity used since the core integration."""
    return f"{combine_hex(dev_id)}-{channel}"


def _migrate_to_new_unique_id(
    hass: HomeAssistant, dev_id: list[int], channel: int
) -> None:
    """Move the historical channel-less registry row when it is unambiguous."""
    registry = er.async_get(hass)
    old_identity = str(combine_hex(dev_id))
    entity_id = registry.async_get_entity_id(Platform.SWITCH, DOMAIN, old_identity)
    if entity_id is None:
        return

    try:
        registry.async_update_entity(
            entity_id,
            new_unique_id=generate_unique_id(dev_id, channel),
        )
    except ValueError:
        LOGGER.warning(
            "Skipped EnOcean switch identity migration because the target "
            "identity already exists"
        )
    else:
        LOGGER.debug("Migrated EnOcean switch to a channel-aware identity")


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Create a YAML-configured EnOcean switch."""
    track_yaml_device(hass, "switch", config)
    device_id: list[int] = config[CONF_ID]
    channel: int = config[CONF_CHANNEL]
    _migrate_to_new_unique_id(hass, device_id, channel)
    register_known_id(hass, device_id)
    async_add_entities(
        [
            EnOceanSwitch(
                device_id,
                config[CONF_NAME],
                channel,
                config[CONF_SWITCH_TYPE],
                config.get(CONF_EEP),
                config.get(CONF_ACTUATOR_PROFILE),
            )
        ]
    )
    _async_register_ute_commission_service()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create switches stored by the options flow."""
    entities = [
        EnOceanSwitch(
            row["id"],
            row["name"],
            row["channel"],
            row.get("switch_type") or "default",
            (row.get("radio_metadata") or {}).get("eep"),
            row.get(CONF_ACTUATOR_PROFILE)
            or ("ubiwizz_ubid1507c" if row.get("switch_type") == "RPS" else None),
        ).set_radio_metadata(row.get("radio_metadata"))
        for row in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, []))
        if row["platform"] == "switch"
    ]
    async_add_entities(entities)
    _async_register_ute_commission_service()


def _async_register_ute_commission_service() -> None:
    """Expose an entity-scoped, operator-triggered UTE commissioning action."""
    try:
        platform = entity_platform.async_get_current_platform()
    except RuntimeError:
        return
    platform.async_register_entity_service(
        SERVICE_UTE_COMMISSION,
        {},
        "async_start_ute_commissioning",
    )


class EnOceanSwitch(EnOceanEntity, SwitchEntity):
    """Represent a D2 actuator or an F6 rocker-paired relay."""

    _attr_assumed_state = True

    def __init__(
        self,
        dev_id: list[int],
        dev_name: str,
        channel: int,
        switch_type: str,
        eep: str | None = None,
        actuator_profile: str | None = None,
    ) -> None:
        """Initialize an actuator without claiming an unconfirmed state."""
        if eep in D2_SINGLE_CHANNEL_EEPS and channel != 0:
            raise ValueError(f"{eep} supports actuator channel 0 only")
        if actuator_profile is not None and not valid_ubiwizz_actuator_channel(
            actuator_profile, channel
        ):
            raise ValueError(
                f"{actuator_profile} supports actuator channels 0 and 1 only"
            )
        super().__init__(dev_id, dev_name)
        self._attr_name = dev_name
        self._attr_unique_id = generate_unique_id(dev_id, channel)
        self._attr_is_on = None
        self.channel = channel
        self._last_command_esp3_accepted: bool | None = None
        self._command_sequence = 0
        self._profile = switch_type
        self._actuator_profile = actuator_profile
        self._ute_commission_sequence = 0
        self._ute_commission_state: str | None = None
        self._ute_commission_last_esp3_ack: bool | None = None
        self._ute_commission_response_code: int | None = None
        self._ute_commission_transmissions = 0
        self._ute_commission_sender_id: list[int] | None = None
        self._ute_commission_timeout: asyncio.TimerHandle | None = None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose transport and feedback metadata without claiming actuation."""
        attributes = dict(self.d2_status_attributes)
        if self._last_command_esp3_accepted is not None:
            attributes["last_command_esp3_accepted"] = self._last_command_esp3_accepted
        if self._actuator_profile is not None:
            attributes["configured_actuator_profile"] = self._actuator_profile
        if self._actuator_profile == "ubiwizz_ubid1507c":
            attributes.update(
                {
                    "ute_commissioning_state": self._ute_commission_state,
                    "ute_commissioning_last_esp3_ack": self._ute_commission_last_esp3_ack,
                    "ute_commissioning_response_code": self._ute_commission_response_code,
                    "ute_commissioning_transmissions": self._ute_commission_transmissions,
                }
            )
        return attributes

    @override
    def turn_on(self, **kwargs: Any) -> None:
        """Request the ON state."""
        self._queue_state_change(True)

    @override
    def turn_off(self, **kwargs: Any) -> None:
        """Request the OFF state."""
        self._queue_state_change(False)

    async def async_start_ute_commissioning(self) -> None:
        """Open a directed UTE listener for this explicit Ubiwizz entity.

        The operator starts this listener before using the local PRESS sequence.
        A matching module broadcasts D4/A0, and this entity immediately queues
        the required directed D4/91 response. No UTE or D2 telegram is emitted
        merely by opening the listener.
        """
        if self._actuator_profile != UBID1507C.profile_id:
            raise HomeAssistantError(
                "UTE commissioning is available only for an explicitly configured "
                "Ubiwizz UBID1507C entity"
            )
        gateway = self.hass.data.get(DATA_ENOCEAN, {}).get(ENOCEAN_DONGLE)
        sender_id = get_gateway_base_id(gateway)
        self._ute_commission_sequence += 1
        sequence = self._ute_commission_sequence
        self._cancel_ute_commission_timeout()
        self._ute_commission_last_esp3_ack = None
        self._ute_commission_response_code = None
        self._ute_commission_sender_id = sender_id

        if sender_id is None:
            self._ute_commission_state = "base_id_unavailable"
            LOGGER.warning("UTE commissioning skipped: dongle Base ID unavailable")
            self.async_write_ha_state()
            return

        self._ute_commission_state = "awaiting_ute_request"
        self._ute_commission_timeout = self.hass.loop.call_later(
            UTE_ASSOCIATION_WINDOW_SECONDS,
            self._ute_commission_timed_out,
            sequence,
        )
        LOGGER.info("Opened directed UTE listener for %s", self.dev_name)
        self.async_write_ha_state()

    @callback
    def _record_ute_transport_result(self, sequence: int, accepted: bool) -> None:
        """Record only the dongle result for this entity's D4/91 response."""
        if sequence != self._ute_commission_sequence:
            return
        self._ute_commission_last_esp3_ack = accepted
        if accepted:
            self._ute_commission_state = "response_sent"
            LOGGER.info(
                "Directed UTE response accepted by the dongle for %s", self.dev_name
            )
        else:
            self._ute_commission_state = "transport_rejected"
            LOGGER.warning("Directed UTE response was rejected by the dongle")
        self.async_write_ha_state()

    @callback
    def _handle_ute_teach_in_request(self, packet: Any) -> bool:
        """Answer the active target's exact D4/A0 request with directed D4/91."""
        sequence = self._ute_commission_sequence
        sender_id = self._ute_commission_sender_id
        if self._ute_commission_state != "awaiting_ute_request" or sender_id is None:
            return False
        if (
            parse_ute_teach_in_request(
                packet,
                target_id=self.dev_id,
                eep=UBID1507C.eep,
                number_of_channels=UBID1507C.ute_number_of_channels,
                manufacturer_id=UBID1507C.ute_manufacturer_id,
            )
            is None
        ):
            return False
        try:
            response = build_ute_teach_in_response(
                sender_id=sender_id,
                target_id=self.dev_id,
                eep=UBID1507C.eep,
                number_of_channels=UBID1507C.ute_number_of_channels,
                manufacturer_id=UBID1507C.ute_manufacturer_id,
            )
        except ValueError:
            self._cancel_ute_commission_timeout()
            self._ute_commission_state = "invalid_request"
            LOGGER.warning("UTE response skipped: invalid configured identity")
            self.async_write_ha_state()
            return True

        self._cancel_ute_commission_timeout()
        self._ute_commission_response_code = 0x01
        self._ute_commission_state = "response_queued"
        self._ute_commission_transmissions += 1
        queued = self.send_command(
            response.data,
            response.optional,
            response.packet_type,
            response_callback=lambda accepted: self._record_ute_transport_result(
                sequence, accepted
            ),
        )
        if not queued:
            self._ute_commission_last_esp3_ack = False
            self._ute_commission_state = "transport_rejected"
        else:
            LOGGER.info("Queued directed UTE response for %s", self.dev_name)
        self.async_write_ha_state()
        return True

    @callback
    def _ute_commission_timed_out(self, sequence: int) -> None:
        """Close the operator learn window without emitting a radio telegram."""
        if sequence != self._ute_commission_sequence:
            return
        self._ute_commission_timeout = None
        if self._ute_commission_state == "awaiting_ute_request":
            self._ute_commission_state = "request_timeout"
        self.async_write_ha_state()

    @callback
    def _cancel_ute_commission_timeout(self) -> None:
        """Cancel the active operator UTE window, if this entity owns one."""
        if self._ute_commission_timeout is not None:
            self._ute_commission_timeout.cancel()
            self._ute_commission_timeout = None

    async def async_will_remove_from_hass(self) -> None:
        """Prevent a late commissioning callback after this entity is removed."""
        self._ute_commission_sequence += 1
        self._cancel_ute_commission_timeout()
        await super().async_will_remove_from_hass()

    def _queue_state_change(self, target: bool) -> None:
        """Encode the configured profile and wait for all ESP3 acknowledgements."""
        if self._profile == "RPS":
            pressed = {
                (0, True): 0x50,
                (1, True): 0x10,
                (0, False): 0x70,
                (1, False): 0x30,
            }[(self.channel, target)]
            frames = (
                ([RORG.RPS, pressed, *self.dev_id, 0x30], build_radio_optional()),
                ([RORG.RPS, 0x00, *self.dev_id, 0x20], build_radio_optional()),
            )
        else:
            hass = getattr(self, "hass", None)
            gateway = (
                hass.data.get(DATA_ENOCEAN, {}).get(ENOCEAN_DONGLE)
                if hass is not None
                else None
            )
            sender_id = get_gateway_base_id(gateway)
            if sender_id is None:
                LOGGER.warning(
                    "Skipping D2 command because the dongle Base ID is unavailable"
                )
                return
            output = 100 if target else 0
            frames = (
                (
                    [
                        RORG.VLD,
                        0x01,
                        self.channel,
                        output,
                        *sender_id,
                        0x00,
                    ],
                    build_radio_optional(self.dev_id),
                ),
            )
        command_sequence = self._begin_command_transport()
        self._send_state_packets(
            list(frames),
            completed=lambda accepted: self._record_command_transport_result(
                command_sequence, accepted
            ),
        )

    def _begin_command_transport(self) -> int:
        """Start a transport result transaction without changing relay state."""
        self._command_sequence += 1
        self._last_command_esp3_accepted = None
        return self._command_sequence

    def _record_command_transport_result(
        self, command_sequence: int, accepted: bool
    ) -> None:
        """Record only the newest completed command's transport result."""
        if command_sequence != self._command_sequence:
            return
        self._last_command_esp3_accepted = accepted
        if not accepted:
            LOGGER.warning(
                "EnOcean command for %s was not accepted by the dongle; "
                "HA state remains based on actuator feedback",
                self.dev_name,
            )
        self.async_write_ha_state()

    def _send_state_packets(
        self,
        packets: list[tuple[list[int], list[int]]],
        *,
        completed: Callable[[bool], None] | None = None,
    ) -> None:
        """Queue packets and report aggregate ESP3 delivery, not relay state.

        The entity state is deliberately changed only by ``value_changed`` after
        a matching D2-01 CMD 0x4 status telegram.  ESP3 acceptance proves only
        transport delivery to the dongle, not physical relay actuation.
        """
        outstanding = len(packets)
        all_accepted = True

        def response_received(accepted: bool) -> None:
            nonlocal all_accepted, outstanding
            all_accepted = all_accepted and accepted
            outstanding -= 1
            if outstanding == 0 and completed is not None:
                completed(all_accepted)

        for data, optional in packets:
            queued = self.send_command(
                data,
                optional,
                0x01,
                response_callback=response_received,
            )
            if not queued:
                response_received(False)

    @override
    def value_changed(self, packet) -> None:
        """Apply UTE requests, power-meter, or D2 feedback."""
        if self._handle_ute_teach_in_request(packet):
            return
        if not packet.data:
            return
        try:
            if packet.data[0] == RORG.BS4:
                packet.parse_eep(0x12, 0x01)
                values = packet.parsed
                if values["DT"]["raw_value"] != 1:
                    return
                measured = values["MR"]["raw_value"]
                scale = values["DIV"]["raw_value"]
                self._attr_is_on = measured / (10**scale) > 1
                self.schedule_update_ha_state()
                return

            if packet.data[0] != RORG.VLD:
                return
            status = parse_d2_01_actuator_status(packet.data)
            if status is None or status.channel != self.channel:
                return
            self.record_d2_status(status)
            self._attr_is_on = status.output_value > 0
            self.async_write_ha_state()
        except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError):
            LOGGER.debug("Ignoring malformed EnOcean switch packet", exc_info=True)

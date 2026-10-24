"""Switch platform for EnOcean actuators."""

from __future__ import annotations

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
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN, LOGGER
from .device_rows import (
    add_entities_for_group,
    async_connect_subentry_setup,
    rows_by_subentry,
)
from .enocean_library.protocol.constants import RORG
from .enocean_library.protocol.d2 import parse_d2_01_actuator_status
from .enocean_library.utils import combine_hex, to_hex_string
from .entity import EnOceanEntity, build_radio_optional, get_gateway_base_id
from .identity import device_names_by_sender, row_names_the_device, switch_unique_id
from .learn import register_known_id
from .runtime import get_gateway
from .schema import ENOCEAN_ID, exact_finite_int
from .ubiwizz import UBIWIZZ_ACTUATOR_PROFILES, valid_ubiwizz_actuator_channel
from .yaml_import import track_yaml_device

# Every state change is pushed by the serial worker; nothing here polls.
PARALLEL_UPDATES = 0

CONF_ACTUATOR_PROFILE = "actuator_profile"
CONF_CHANNEL, CONF_SWITCH_TYPE = "channel", "switch_type"
CONF_EEP = "eep"
DEFAULT_NAME = "EnOcean Switch"
D2_SINGLE_CHANNEL_EEPS = frozenset(("D2-01-0A",))

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
    return switch_unique_id(dev_id, channel)


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


def _switch_entities(rows: list[dict]) -> list[EnOceanSwitch]:
    """Build the switches of one group of rows (legacy list or one subentry).

    The actuator profile is read from the row only. It is never inferred from
    ``switch_type``: rows that relied on the pre-2.10 RPS inference were
    migrated once to carry the profile explicitly (``migration.py``).
    """
    names_by_sender = device_names_by_sender(rows)
    entities = []
    for row in rows:
        if row["platform"] != "switch":
            continue
        entity = EnOceanSwitch(
            row["id"],
            row["name"],
            row["channel"],
            row.get("switch_type") or "default",
            (row.get("radio_metadata") or {}).get("eep"),
            row.get(CONF_ACTUATOR_PROFILE),
        ).set_radio_metadata(row.get("radio_metadata"))
        if row_names_the_device(row, names_by_sender):
            # Bronze has-entity-name: Home Assistant then renders this
            # entity's name as the device's own name, the exact string
            # already shown today for a sender with a single row.
            entity._attr_has_entity_name = True
            entity._attr_name = None
        entities.append(entity)
    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create switches stored by the options flow, one batch per device subentry."""
    for subentry_id, rows in rows_by_subentry(entry):
        add_entities_for_group(async_add_entities, _switch_entities(rows), subentry_id)
    async_connect_subentry_setup(hass, entry, async_add_entities, _switch_entities)


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

    @property
    def extra_state_attributes(self) -> dict:
        """Expose transport and feedback metadata without claiming actuation."""
        attributes = dict(self.d2_status_attributes)
        if self._last_command_esp3_accepted is not None:
            attributes["last_command_esp3_accepted"] = self._last_command_esp3_accepted
        if self._actuator_profile is not None:
            attributes["configured_actuator_profile"] = self._actuator_profile
        if self._profile == "RPS":
            # The dongle's own Base ID is what a "default" (D2) row transmits
            # as instead; that one already has its own diagnostic sensor.
            attributes["sender_id"] = to_hex_string(self.dev_id)
        return attributes

    @override
    def turn_on(self, **kwargs: Any) -> None:
        """Request the ON state."""
        self._queue_state_change(True)

    @override
    def turn_off(self, **kwargs: Any) -> None:
        """Request the OFF state."""
        self._queue_state_change(False)

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
            gateway = get_gateway(getattr(self, "hass", None))
            sender_id = get_gateway_base_id(gateway)
            if sender_id is None:
                LOGGER.warning(
                    "Skipping D2 command because the dongle Base ID is unavailable"
                )
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="base_id_unavailable",
                    translation_placeholders={"name": self.dev_name},
                )
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
        queued = self._send_state_packets(
            list(frames),
            completed=lambda accepted: self._record_command_transport_result(
                command_sequence, accepted
            ),
        )
        if not queued:
            # The transport result (False) is already recorded and scheduled
            # for writing; the caller still deserves to know why nothing left.
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_rejected",
                translation_placeholders={"name": self.dev_name},
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
        # ``turn_on``/``turn_off`` run in Home Assistant's executor, and a
        # command the dongle refuses to queue completes synchronously on that
        # thread, while ESP3 results arrive later on the event loop. Scheduling
        # the write is legal from both; a direct ``async_write_ha_state`` is
        # not and raises off the loop.
        self.schedule_update_ha_state()

    def _send_state_packets(
        self,
        packets: list[tuple[list[int], list[int]]],
        *,
        completed: Callable[[bool], None] | None = None,
    ) -> int:
        """Queue packets and report aggregate ESP3 delivery, not relay state.

        The entity state is deliberately changed only by ``value_changed`` after
        a matching D2-01 CMD 0x4 status telegram.  ESP3 acceptance proves only
        transport delivery to the dongle, not physical relay actuation. Returns
        how many packets the dongle accepted into its queue.
        """
        outstanding = len(packets)
        all_accepted = True
        queued_count = 0

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
            if queued:
                queued_count += 1
            else:
                response_received(False)
        return queued_count

    @override
    def value_changed(self, packet) -> None:
        """Apply power-meter or D2 feedback."""
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

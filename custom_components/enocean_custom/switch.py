"""Switch platform for EnOcean actuators."""

from __future__ import annotations

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
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DATA_ENOCEAN, DOMAIN, ENOCEAN_DONGLE, LOGGER
from .device import EnOceanEntity, build_radio_optional, get_gateway_base_id
from .enocean_library.protocol.constants import RORG
from .enocean_library.protocol.d2 import parse_d2_01_actuator_status
from .enocean_library.utils import combine_hex
from .learn import register_known_id
from .schema import CONF_UI_DEVICES, ENOCEAN_ID, exact_finite_int, valid_ui_devices
from .ubiwizz import UBIWIZZ_ACTUATOR_PROFILES, valid_ubiwizz_actuator_channel
from .yaml_import import track_yaml_device

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
    # Register before adding entities: HA's current platform context is only
    # guaranteed during platform setup, not after entity creation.
    _register_repeater_service()
    async_add_entities(entities)


def _register_repeater_service() -> None:
    """Expose the documented D2-01-12 MSC repeater command in HA."""
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "configure_repeater",
        {
            vol.Required("level"): vol.All(vol.Coerce(int), vol.In((0, 1, 2))),
        },
        "configure_repeater",
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
        self._profile = switch_type
        self._actuator_profile = actuator_profile

    @property
    def extra_state_attributes(self) -> dict:
        """Expose configured profile and feedback metadata when available."""
        attributes = dict(self.d2_status_attributes)
        if self._actuator_profile is not None:
            attributes["configured_actuator_profile"] = self._actuator_profile
        return attributes

    @override
    def turn_on(self, **kwargs: Any) -> None:
        """Request the ON state."""
        self._queue_state_change(True)

    @override
    def turn_off(self, **kwargs: Any) -> None:
        """Request the OFF state."""
        self._queue_state_change(False)

    def configure_repeater(self, level: int) -> None:
        """Send the D2-01-12 MSC repeater command from the entity.

        The payload is published by openHAB for NodOn D2-01-12 actuators and
        matches the Ubiwizz two-channel profile: 00 46 08 01 01/02, with
        00 46 08 00 00 disabling the repeater. This is a configuration write,
        not a relay command; no load is switched.
        """
        if self._actuator_profile != "ubiwizz_ubid1507c":
            LOGGER.warning(
                "Repeater configuration refused for non-Ubiwizz D2 entity %s",
                self.dev_name,
            )
            return
        gateway = self.hass.data.get(DATA_ENOCEAN, {}).get(ENOCEAN_DONGLE)
        sender_id = get_gateway_base_id(gateway)
        if sender_id is None:
            LOGGER.warning("Repeater configuration skipped: dongle Base ID unavailable")
            return
        mode = 0 if level == 0 else 1
        selected_level = 0 if level == 0 else level
        data = [RORG.MSC, 0x00, 0x46, 0x08, mode, selected_level, *sender_id, 0x00]
        optional = build_radio_optional(self.dev_id)
        LOGGER.info(
            "Sending Ubiwizz repeater level=%s to %s channel=%s MSC=%s",
            level,
            self.dev_name,
            self.channel,
            bytes(data).hex(" "),
        )
        self._send_state_packets([(data, optional)])

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
        self._send_state_packets(list(frames))

    def _send_state_packets(
        self,
        packets: list[tuple[list[int], list[int]]],
    ) -> None:
        """Queue a command without treating ESP3 ACK as actuator feedback.

        The entity state is deliberately changed only by ``value_changed`` after
        a matching D2-01 CMD 0x4 status telegram.  ESP3 acceptance proves only
        transport delivery to the dongle, not physical relay actuation.
        """
        outstanding = len(packets)

        def response_received(accepted: bool) -> None:
            nonlocal outstanding
            outstanding -= 1
            if outstanding == 0 and not accepted:
                LOGGER.warning(
                    "D2 command for %s was not accepted by the dongle; "
                    "waiting for actuator feedback",
                    self.dev_name,
                )

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
        """Apply power-meter or D2 actuator feedback."""
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

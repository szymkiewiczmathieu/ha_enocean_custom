"""Support for EnOcean switches."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.switch import PLATFORM_SCHEMA, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ID, CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN, LOGGER
from .device import EnOceanEntity, build_radio_optional
from .enocean_library.protocol.d2 import parse_d2_01_actuator_status
from .enocean_library.utils import combine_hex
from .learn import register_known_id
from .schema import CONF_UI_DEVICES, ENOCEAN_ID, exact_finite_int, valid_ui_devices

CONF_CHANNEL = "channel"
DEFAULT_NAME = "EnOcean Switch"
CONF_SWITCH_TYPE = "switch_type"

SWITCH_TYPES = ("default", "RPS")


def _validate_switch_config(config: ConfigType) -> ConfigType:
    """Validate channel semantics for the selected switch profile."""
    if config[CONF_SWITCH_TYPE] == "RPS" and config[CONF_CHANNEL] not in (0, 1):
        raise vol.Invalid("RPS channel must be 0 or 1")
    return config


PLATFORM_SCHEMA = vol.All(
    PLATFORM_SCHEMA.extend(
        {
            vol.Required(CONF_ID): ENOCEAN_ID,
            vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
            vol.Optional(CONF_CHANNEL, default=0): vol.All(
                exact_finite_int, vol.Range(min=0, max=255)
            ),
            vol.Optional(CONF_SWITCH_TYPE, default="default"): vol.In(SWITCH_TYPES),
        }
    ),
    _validate_switch_config,
)


def generate_unique_id(dev_id: list[int], channel: int) -> str:
    """Generate a valid unique id."""
    return f"{combine_hex(dev_id)}-{channel}"


def _migrate_to_new_unique_id(hass: HomeAssistant, dev_id, channel) -> None:
    """Migrate old unique ids to new unique ids."""
    old_unique_id = f"{combine_hex(dev_id)}"

    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id(Platform.SWITCH, DOMAIN, old_unique_id)

    if entity_id is not None:
        new_unique_id = generate_unique_id(dev_id, channel)
        try:
            ent_reg.async_update_entity(entity_id, new_unique_id=new_unique_id)
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
    """Set up the EnOcean switch platform."""
    channel = config.get(CONF_CHANNEL)
    dev_id = config.get(CONF_ID)
    dev_name = config.get(CONF_NAME)
    switch_type = config.get(CONF_SWITCH_TYPE)

    _migrate_to_new_unique_id(hass, dev_id, channel)
    register_known_id(hass, dev_id)
    async_add_entities([EnOceanSwitch(dev_id, dev_name, channel, switch_type)])


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EnOcean switches configured entirely from the UI."""
    entities = []
    for device in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, [])):
        if device["platform"] != "switch":
            continue
        dev_id = device["id"]
        entities.append(
            EnOceanSwitch(
                dev_id,
                device["name"],
                device["channel"],
                device.get("switch_type") or "default",
            )
        )
    async_add_entities(entities)


class EnOceanSwitch(EnOceanEntity, SwitchEntity):
    """Representation of an EnOcean switch device."""

    _attr_assumed_state = True

    def __init__(self, dev_id, dev_name, channel, switch_type):
        """Initialize the EnOcean switch device."""
        super().__init__(dev_id, dev_name)
        self._light = None
        self._on_state = None
        self._on_state2 = False
        self.channel = channel
        self.switch_type = switch_type
        self._attr_unique_id = generate_unique_id(dev_id, channel)

    @property
    def is_on(self):
        """Return whether the switch is on or off."""
        return self._on_state

    @property
    def name(self):
        """Return the device name."""
        return self.dev_name

    @property
    def extra_state_attributes(self):
        """Return D2-01 feedback metadata after the first status."""
        return self.d2_status_attributes

    def turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        if self.switch_type == "RPS":
            val_pushed = 0x10 if self.channel == 1 else 0x50
            packets = [
                ([0xF6, val_pushed, *self.dev_id, 0x30], build_radio_optional()),
                ([0xF6, 0x00, *self.dev_id, 0x20], build_radio_optional()),
            ]
        else:
            packets = [
                (
                    [
                        0xD2,
                        0x01,
                        self.channel & 0xFF,
                        0x64,
                        0x00,
                        0x00,
                        0x00,
                        0x00,
                        0x00,
                    ],
                    [0x03, *self.dev_id, 0xFF, 0x00],
                )
            ]
        self._send_state_packets(packets, True)

    def turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        if self.switch_type == "RPS":
            val_pushed = 0x30 if self.channel == 1 else 0x70
            packets = [
                ([0xF6, val_pushed, *self.dev_id, 0x30], build_radio_optional()),
                ([0xF6, 0x00, *self.dev_id, 0x20], build_radio_optional()),
            ]
        else:
            packets = [
                (
                    [
                        0xD2,
                        0x01,
                        self.channel & 0xFF,
                        0x00,
                        0x00,
                        0x00,
                        0x00,
                        0x00,
                        0x00,
                    ],
                    [0x03, *self.dev_id, 0xFF, 0x00],
                )
            ]
        self._send_state_packets(packets, False)

    def _send_state_packets(
        self, packets: list[tuple[list[int], list[int]]], target_state: bool
    ) -> None:
        """Apply state only when every packet in the command sequence succeeds."""
        remaining = len(packets)
        all_accepted = True

        def _response(accepted: bool) -> None:
            nonlocal remaining, all_accepted
            all_accepted = all_accepted and accepted
            remaining -= 1
            if remaining == 0 and all_accepted:
                self._on_state = target_state
                self.async_write_ha_state()

        for data, optional in packets:
            if not self.send_command(data, optional, 0x01, response_callback=_response):
                _response(False)

    def value_changed(self, packet):
        """Update the internal state of the switch."""
        if not packet.data:
            return
        try:
            if packet.data[0] == 0xA5:
                # Power meter telegram, turn on if > 1 watt.
                packet.parse_eep(0x12, 0x01)
                if packet.parsed["DT"]["raw_value"] == 1:
                    raw_val = packet.parsed["MR"]["raw_value"]
                    divisor = packet.parsed["DIV"]["raw_value"]
                    watts = raw_val / (10**divisor)
                    self._on_state = watts > 1
                    self.schedule_update_ha_state()
            elif packet.data[0] == 0xD2:
                # EEP 2.6.7, §D2-01 CMD 0x4, pp. 135-136 and TYPE 0x12,
                # pp. 145-147: TYPE 0x12 has two channels; OV 0 is OFF and
                # OV 1..100 is ON. RPS entities consume it too: their module
                # reports the real state after a physical wall-switch toggle
                # (the exact sync the feedback exists for). Modules without
                # D2 simply never send it, leaving RPS behavior unchanged.
                status = parse_d2_01_actuator_status(packet.data)
                if status is not None and status.channel == self.channel:
                    self.record_d2_status(status)
                    self._on_state = status.output_value > 0
                    self.async_write_ha_state()
        except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError):
            LOGGER.debug("Ignoring malformed EnOcean switch packet", exc_info=True)

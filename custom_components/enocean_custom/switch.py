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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN, LOGGER
from .device import EnOceanEntity, build_radio_optional
from .enocean_library.protocol.constants import RORG
from .enocean_library.protocol.d2 import parse_d2_01_actuator_status
from .enocean_library.utils import combine_hex
from .learn import register_known_id
from .schema import CONF_UI_DEVICES, ENOCEAN_ID, exact_finite_int, valid_ui_devices

CONF_CHANNEL, CONF_SWITCH_TYPE = "channel", "switch_type"
DEFAULT_NAME = "EnOcean Switch"

SWITCH_TYPES = ("default", "RPS")


def _validate_switch_config(config: ConfigType) -> ConfigType:
    """Restrict rocker simulation to the two buttons defined by F6-02-01."""
    channel = config[CONF_CHANNEL]
    if config[CONF_SWITCH_TYPE] == "RPS" and channel not in (0, 1):
        raise vol.Invalid("RPS channel must be 0 or 1")
    return config


PLATFORM_SCHEMA = vol.All(
    SWITCH_PLATFORM_SCHEMA.extend(
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
        )
        for row in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, []))
        if row["platform"] == "switch"
    ]
    async_add_entities(entities)


class EnOceanSwitch(EnOceanEntity, SwitchEntity):
    """Represent a D2 actuator or an F6 rocker-paired relay."""

    _attr_assumed_state = True

    def __init__(
        self,
        dev_id: list[int],
        dev_name: str,
        channel: int,
        switch_type: str,
    ) -> None:
        """Initialize an actuator without claiming an unconfirmed state."""
        super().__init__(dev_id, dev_name)
        self._attr_name = dev_name
        self._attr_unique_id = generate_unique_id(dev_id, channel)
        self._attr_is_on = None
        self.channel = channel
        self._profile = switch_type

    @property
    def extra_state_attributes(self) -> dict:
        """Expose feedback metadata once the actuator reports it."""
        return self.d2_status_attributes

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
            output = 100 if target else 0
            frames = (
                (
                    [
                        RORG.VLD,
                        0x01,
                        self.channel,
                        output,
                        0x00,
                        0x00,
                        0x00,
                        0x00,
                        0x00,
                    ],
                    build_radio_optional(self.dev_id),
                ),
            )
        self._send_state_packets(list(frames), target)

    def _send_state_packets(
        self,
        packets: list[tuple[list[int], list[int]]],
        target_state: bool,
    ) -> None:
        """Commit the requested state only when the complete sequence is accepted."""
        outstanding = len(packets)
        successful = True

        def response_received(accepted: bool) -> None:
            nonlocal outstanding, successful
            successful &= accepted
            outstanding -= 1
            if outstanding == 0 and successful:
                self._attr_is_on = target_state
                self.async_write_ha_state()

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

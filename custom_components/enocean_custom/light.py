"""Light platform for EnOcean gateway dimmer commands."""

from __future__ import annotations

from typing import Any, ClassVar, override

import voluptuous as vol
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.components.light import (
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.components.light import (
    PLATFORM_SCHEMA as LIGHT_PLATFORM_SCHEMA,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ID, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN, SERVICE_SEND_TEACH_IN
from .device import EnOceanEntity, build_radio_optional
from .enocean_library.protocol.constants import RORG
from .enocean_library.protocol.d2 import parse_d2_01_actuator_status
from .enocean_library.utils import combine_hex
from .learn import register_known_id
from .schema import (
    CONF_UI_DEVICES,
    ENOCEAN_ID,
    exact_finite_int,
    optional_enocean_id,
    valid_ui_devices,
)

CONF_CHANNEL = "channel"
CONF_SENDER_ID = "sender_id"
DEFAULT_NAME = "EnOcean Light"

# A5-38-08, 4BS teach-in variation 2, Eltako manufacturer identifier 0x00D.
_A5_38_08_ELTAKO_TEACH_IN = (0xE0, 0x40, 0x0D, 0x80)

PLATFORM_SCHEMA = LIGHT_PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_ID, default=[]): optional_enocean_id,
        vol.Required(CONF_SENDER_ID): ENOCEAN_ID,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_CHANNEL, default=0): vol.All(
            exact_finite_int, vol.Range(min=0, max=31)
        ),
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Create one YAML-configured gateway dimmer."""
    sender_id: list[int] = config[CONF_SENDER_ID]
    device_id: list[int] = config[CONF_ID]
    entity = EnOceanLight(
        sender_id,
        device_id,
        config[CONF_NAME],
        config.get(CONF_CHANNEL, 0),
    )

    if not device_id:
        registry = er.async_get(hass)
        old_entity_id = registry.async_get_entity_id(LIGHT_DOMAIN, DOMAIN, "0")
        new_entity_id = registry.async_get_entity_id(
            LIGHT_DOMAIN,
            DOMAIN,
            entity.unique_id,
        )
        if old_entity_id is not None and new_entity_id is None:
            registry.async_update_entity(
                old_entity_id,
                new_unique_id=entity.unique_id,
            )

    register_known_id(hass, device_id or sender_id)
    async_add_entities([entity])
    _async_register_send_teach_in_service()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create dimmers stored in the entry options."""
    entities = [
        EnOceanLight(
            row["sender_id"],
            row["id"],
            row["name"],
            row.get("channel", 0),
        ).set_radio_metadata(row.get("radio_metadata"))
        for row in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, []))
        if row["platform"] == "light"
    ]
    async_add_entities(entities)
    _async_register_send_teach_in_service()


def _async_register_send_teach_in_service() -> None:
    """Publish the entity-scoped A5-38-08 teach-in service."""
    try:
        platform = entity_platform.async_get_current_platform()
    except RuntimeError:
        return
    platform.async_register_entity_service(
        SERVICE_SEND_TEACH_IN,
        {},
        "send_teach_in",
    )


class EnOceanLight(EnOceanEntity, LightEntity):
    """Represent an A5-38-08 dimmer command endpoint."""

    _attr_assumed_state = True
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.BRIGHTNESS}

    def __init__(
        self,
        sender_id: list[int] | None,
        dev_id: list[int],
        dev_name: str,
        channel: int = 0,
    ) -> None:
        """Initialize a dimmer with unknown actuator state."""
        super().__init__(dev_id, dev_name)
        self._device_identifier_id = dev_id or sender_id or []
        self._attr_brightness = 50
        self._attr_is_on = None
        self._attr_name = dev_name
        self._attr_unique_id = str(combine_hex(dev_id or sender_id or []))
        self._channel = channel
        self._sender_id = sender_id

    @property
    def extra_state_attributes(self) -> dict:
        """Expose D2 feedback metadata after a status telegram."""
        return self.d2_status_attributes

    @override
    def turn_on(self, **kwargs: Any) -> None:
        """Send an A5-38-08 ON/dim command."""
        requested = kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness)
        # Keep the v1.5.0 byte-exact formula (review finding P2-01):
        # round, not floor — brightness=254 must stay 100 %, not 99 %.
        percentage = max(1, min(100, round(requested / 255 * 100)))
        self._send_dimmer_command(percentage, True, requested)

    @override
    def turn_off(self, **kwargs: Any) -> None:
        """Send an A5-38-08 OFF command."""
        self._send_dimmer_command(0, False, self._attr_brightness)

    def _send_dimmer_command(
        self,
        percentage: int,
        target_state: bool,
        requested_brightness: int,
    ) -> None:
        """Queue a gateway command and stage its state until RET_OK."""
        if self._sender_id is None:
            return
        data = [
            RORG.BS4,
            0x02,
            percentage,
            0x01,
            0x09,
            *self._sender_id,
            0x00,
        ]
        self.send_command(
            data,
            build_radio_optional(self.dev_id or None),
            0x01,
            response_callback=lambda accepted: self._command_response(
                accepted,
                target_state,
                requested_brightness,
            ),
        )

    def send_teach_in(self) -> None:
        """Transmit the A5-38-08 teach-in telegram for this sender identity."""
        if self._sender_id is None:
            raise HomeAssistantError(
                "This light has no sender_id to teach in; set one first."
            )
        data = [
            RORG.BS4,
            *_A5_38_08_ELTAKO_TEACH_IN,
            *self._sender_id,
            0x00,
        ]
        if not self.send_command(data, build_radio_optional(), 0x01):
            raise HomeAssistantError(
                f"EnOcean teach-in for {self.name} was rejected by the dongle"
            )

    def _command_response(
        self,
        accepted: bool,
        on_state: bool,
        brightness: int,
    ) -> None:
        """Commit a command result after the dongle acknowledges it."""
        if not accepted:
            return
        self._attr_is_on = on_state
        self._attr_brightness = brightness
        self.async_write_ha_state()

    @override
    def value_changed(self, packet) -> None:
        """Decode A5 dimmer feedback or channel-filtered D2 status."""
        if packet.rorg == RORG.BS4:
            if len(packet.data) < 3 or packet.data[1] != 0x02:
                return
            percentage = min(packet.data[2], 100)
            self._attr_brightness = round(percentage / 100 * 255)
            self._attr_is_on = percentage != 0
            self.schedule_update_ha_state()
            return

        if packet.rorg != RORG.VLD:
            return
        status = parse_d2_01_actuator_status(packet.data)
        if status is None or status.channel != self._channel:
            return
        self.record_d2_status(status)
        self._attr_brightness = round(status.output_value / 100 * 255)
        self._attr_is_on = status.output_value > 0
        self.async_write_ha_state()

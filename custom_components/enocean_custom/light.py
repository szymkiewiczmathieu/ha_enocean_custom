"""Support for EnOcean light sources."""

from __future__ import annotations

from typing import Any, ClassVar

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    PLATFORM_SCHEMA,
    ColorMode,
    LightEntity,
)
from homeassistant.components.light import (
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ID, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN, SERVICE_SEND_TEACH_IN
from .device import EnOceanEntity, build_radio_optional
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

CONF_SENDER_ID = "sender_id"
CONF_CHANNEL = "channel"

DEFAULT_NAME = "EnOcean Light"
_A5_38_08_ELTAKO_TEACH_IN = [0xE0, 0x40, 0x0D, 0x80]

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_ID, default=[]): optional_enocean_id,
        vol.Required(CONF_SENDER_ID): ENOCEAN_ID,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        # D2-01-12 actuators report per-channel status (I/O is 5 bits); the
        # channel selects which one drives this entity's state.
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
    """Set up an EnOcean light device."""
    sender_id = config.get(CONF_SENDER_ID)
    dev_id = config.get(CONF_ID)
    dev_name = config.get(CONF_NAME)
    channel = config.get(CONF_CHANNEL)
    entity = EnOceanLight(sender_id, dev_id, dev_name, channel)
    if not dev_id:
        registry = er.async_get(hass)
        legacy_entity_id = registry.async_get_entity_id(LIGHT_DOMAIN, DOMAIN, "0")
        current_entity_id = registry.async_get_entity_id(
            LIGHT_DOMAIN, DOMAIN, entity.unique_id
        )
        if legacy_entity_id is not None and current_entity_id is None:
            # Migrate the one historical send-only entity immediately. Updating
            # its registry unique_id preserves entity_id/automations while the
            # next configured light remains free to use its own sender identity.
            registry.async_update_entity(
                legacy_entity_id,
                new_unique_id=entity.unique_id,
            )
    register_known_id(hass, dev_id or sender_id)
    async_add_entities([entity])
    _async_register_send_teach_in_service()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EnOcean lights configured entirely from the UI."""
    entities = []
    for device in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, [])):
        if device["platform"] != "light":
            continue
        dev_id = device["id"]
        entities.append(
            EnOceanLight(
                device["sender_id"], dev_id, device["name"], device.get("channel", 0)
            )
        )
    async_add_entities(entities)
    _async_register_send_teach_in_service()


def _async_register_send_teach_in_service() -> None:
    """Register the light-targeted teach-in entity service."""
    try:
        platform = entity_platform.async_get_current_platform()
    except RuntimeError:
        # Direct platform unit tests do not install an entity-platform context.
        return
    platform.async_register_entity_service(
        SERVICE_SEND_TEACH_IN,
        {},
        "send_teach_in",
    )


class EnOceanLight(EnOceanEntity, LightEntity):
    """Representation of an EnOcean light source."""

    _attr_assumed_state = True
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.BRIGHTNESS}

    def __init__(self, sender_id, dev_id, dev_name, channel=0):
        """Initialize the EnOcean light."""
        super().__init__(dev_id, dev_name)
        self._on_state = None
        self._brightness = 50
        self._sender_id = sender_id
        self._channel = channel
        self._attr_unique_id = f"{combine_hex(dev_id or sender_id)}"

    @property
    def name(self):
        """Return the name of the device if any."""
        return self.dev_name

    @property
    def brightness(self):
        """Brightness of the light.

        This method is optional. Removing it indicates to Home Assistant
        that brightness is not supported for this light.
        """
        return self._brightness

    @property
    def is_on(self):
        """If light is on."""
        return self._on_state

    @property
    def extra_state_attributes(self):
        """Return D2-01 feedback metadata after the first status."""
        return self.d2_status_attributes

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the light source on or sets a specific dimmer value."""
        brightness = kwargs.get(ATTR_BRIGHTNESS, self._brightness)
        bval = round(brightness / 255.0 * 100.0)
        if bval == 0:
            bval = 1
        command = [0xA5, 0x02, bval, 0x01, 0x09]
        command.extend(self._sender_id)
        command.extend([0x00])
        self.send_command(
            command,
            build_radio_optional(self.dev_id or None),
            0x01,
            response_callback=lambda accepted: self._command_response(
                accepted, True, brightness
            ),
        )

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the light source off."""
        command = [0xA5, 0x02, 0x00, 0x01, 0x09]
        command.extend(self._sender_id)
        command.extend([0x00])
        self.send_command(
            command,
            build_radio_optional(self.dev_id or None),
            0x01,
            response_callback=lambda accepted: self._command_response(
                accepted, False, self._brightness
            ),
        )

    def send_teach_in(self) -> None:
        """Teach an Eltako 4BS dimmer the entity's outbound sender ID.

        EEP 2.6.7 A5-38-08 and Appendix 3.3 define the Gateway profile and
        4BS teach-in variation 2 bit layout. Eltako ManID 0x00D encodes the
        resulting DB3..DB0 bytes as E0 40 0D 80.
        """
        if self._sender_id is None:
            raise HomeAssistantError(
                "This light has no sender_id to teach in; set one first."
            )
        command = [
            0xA5,
            *_A5_38_08_ELTAKO_TEACH_IN,
            *self._sender_id,
            0x00,
        ]
        self.send_command(command, build_radio_optional(), 0x01)

    def _command_response(
        self, accepted: bool, on_state: bool, brightness: int
    ) -> None:
        """Apply optimistic state only after the dongle accepts the command."""
        if not accepted:
            return
        self._on_state = on_state
        self._brightness = brightness
        self.async_write_ha_state()

    def value_changed(self, packet):
        """Update the internal state of this device.

        Dimmer devices like Eltako FUD61 send 4BS telegrams. D2-01-12
        actuators additionally send their actual local-control state as VLD.
        """
        if packet.rorg == 0xA5 and len(packet.data) >= 3 and packet.data[1] == 0x02:
            val = packet.data[2]
            self._brightness = round(min(val, 100) / 100.0 * 255.0)
            self._on_state = bool(val != 0)
            self.schedule_update_ha_state()
        elif packet.rorg == 0xD2:
            # EEP 2.6.7, §D2-01 CMD 0x4, p. 136, offsets 17..23: OV is an
            # absolute 0..100 %, mapped to HA's 0..255 brightness. Multi-gang
            # actuators report every channel: only the configured one drives
            # this entity (review finding: last-packet-wins was wrong).
            status = parse_d2_01_actuator_status(packet.data)
            if status is not None and status.channel == self._channel:
                self.record_d2_status(status)
                self._brightness = round(status.output_value / 100.0 * 255.0)
                self._on_state = status.output_value > 0
                self.async_write_ha_state()

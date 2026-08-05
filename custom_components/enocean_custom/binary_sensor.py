"""Binary sensor platform for EnOcean rocker telegrams."""

from __future__ import annotations

from typing import override

import voluptuous as vol
from homeassistant.components.binary_sensor import (
    DEVICE_CLASSES_SCHEMA,
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.binary_sensor import (
    PLATFORM_SCHEMA as BINARY_SENSOR_PLATFORM_SCHEMA,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE_CLASS, CONF_ID, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .device import EnOceanEntity
from .enocean_library.protocol.constants import RORG
from .enocean_library.utils import combine_hex
from .learn import register_known_id
from .schema import CONF_UI_DEVICES, ENOCEAN_ID, valid_ui_devices
from .yaml_import import track_yaml_device

DEFAULT_NAME = "EnOcean binary sensor"
EVENT_BUTTON_PRESSED = "button_pressed"

ATTR_ONOFF, ATTR_WHICH, ATTR_REPEATED_TELEGRAM = (
    "OnOff",
    "Which",
    "Repeated telegram",
)

PLATFORM_SCHEMA = BINARY_SENSOR_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_ID): ENOCEAN_ID,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_DEVICE_CLASS): DEVICE_CLASSES_SCHEMA,
    }
)


def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Create a YAML-configured rocker sensor."""
    track_yaml_device(hass, "binary_sensor", config)
    device_id: list[int] = config[CONF_ID]
    register_known_id(hass, device_id)
    add_entities(
        [
            EnOceanBinarySensor(
                device_id,
                config[CONF_NAME],
                config.get(CONF_DEVICE_CLASS),
            )
        ]
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create rocker sensors stored in the entry options."""
    configured = (
        (
            EnOceanA514Contact(row["id"], row["name"])
            if (row.get("radio_metadata") or {}).get("eep") == "A5-14-01"
            else EnOceanBinarySensor(row["id"], row["name"], row["device_class"])
        ).set_radio_metadata(row.get("radio_metadata"))
        for row in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, []))
        if row["platform"] == "binary_sensor"
    )
    async_add_entities(list(configured))


class EnOceanBinarySensor(EnOceanEntity, BinarySensorEntity):
    """Represent an F6-02-01/F6-02-02 energy-bow rocker."""

    def __init__(
        self,
        dev_id: list[int],
        dev_name: str,
        device_class: BinarySensorDeviceClass | None,
    ) -> None:
        """Initialize a rocker entity with its legacy identity."""
        super().__init__(dev_id, dev_name)
        self._attr_device_class = device_class
        self._attr_name = dev_name
        self._attr_unique_id = f"{combine_hex(dev_id)}-{device_class}"
        self.onoff = -1
        self.which = -1
        self.repeated_telegram: int = -1

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        """Expose the decoded rocker direction and repeater count."""
        return dict(
            zip(
                (ATTR_ONOFF, ATTR_WHICH, ATTR_REPEATED_TELEGRAM),
                (self.onoff, self.which, self.repeated_telegram),
                strict=True,
            )
        )

    @override
    def value_changed(self, packet) -> None:
        """Decode one complete RPS rocker telegram and emit its legacy event."""
        if packet.rorg != RORG.RPS or len(packet.data) < 7:
            return

        status = packet.status
        message_kind = status >> 4
        pushed = 1 if message_kind == 3 else 0 if message_kind == 2 else None
        self.repeated_telegram = status & 0x0F

        if pushed is not None:
            self._attr_is_on = bool(pushed)
        self.schedule_update_ha_state()

        action_map = {
            0x70: (0, 0),
            0x50: (0, 1),
            0x30: (1, 0),
            0x10: (1, 1),
            0x37: (10, 0),
            0x15: (10, 1),
        }
        if (decoded := action_map.get(packet.data[1])) is not None:
            self.which, self.onoff = decoded

        event_data = {
            "name": str(self.dev_name),
            "id": list(self.dev_id),
            "pushed": pushed,
            "which": int(self.which),
            "onoff": int(self.onoff),
            "repeated_telegram": int(self.repeated_telegram),
        }
        self.hass.bus.fire(EVENT_BUTTON_PRESSED, event_data)


class EnOceanA514Contact(EnOceanEntity, BinarySensorEntity):
    """Expose the contact bit of an A5-14-01 window/door sender."""

    _attr_device_class = BinarySensorDeviceClass.DOOR

    def __init__(self, dev_id: list[int], dev_name: str) -> None:
        """Initialize an automatically mapped contact."""
        super().__init__(dev_id, dev_name)
        self._attr_name = dev_name
        self._attr_unique_id = f"{combine_hex(dev_id)}-a5-14-01-contact"

    @override
    def value_changed(self, packet) -> None:
        """Decode CT at EEP bit offset 31 (DB0 bit 0)."""
        if packet.rorg != RORG.BS4 or len(packet.data) < 5:
            return
        # DB0 bit 3 is the 4BS LRN bit (RadioPacket.learn is its inverse). A
        # teach-in telegram carries FUNC/TYPE/manufacturer in DB3..DB1 and no
        # contact state at all, so it must never move the entity.
        if not packet.data[4] & 0x08:
            return
        self._attr_is_on = bool(packet.data[4] & 0x01)
        self.schedule_update_ha_state()

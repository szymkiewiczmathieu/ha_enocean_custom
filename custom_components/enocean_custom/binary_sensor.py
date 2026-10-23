"""Binary sensor platform for EnOcean rocker telegrams."""

from __future__ import annotations

from dataclasses import dataclass
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
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .availability import REPAIR_CATEGORIES, availability_settings, silence_category
from .eep_catalog import EepField, EepProfile, generic_profile
from .enocean_library.protocol.constants import RORG
from .entity import EnOceanEntity, EnOceanGatewayEntity
from .identity import (
    A514_EEP,
    GATEWAY_KEY_CONNECTED,
    GENERIC_SENSOR_DEVICE_CLASS,
    SMOKE_EEP,
    a514_contact_unique_id,
    binary_sensor_unique_id,
    generic_field_key,
    sensor_unique_id,
    smoke_battery_low_unique_id,
)
from .learn import register_known_id
from .schema import CONF_UI_DEVICES, ENOCEAN_ID, valid_ui_devices
from .yaml_import import track_yaml_device

# Every state change is pushed by the serial worker; nothing here polls.
PARALLEL_UPDATES = 0

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
    settings = availability_settings(getattr(entry, "options", {}))
    configured = []
    for row in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, [])):
        if row["platform"] == "sensor":
            entities = _generic_binary_entities(row)
        elif row["platform"] == "binary_sensor":
            entities = _entities_for_row(row)
        else:
            continue
        category = silence_category(row)
        for entity in entities:
            entity.set_radio_metadata(row.get("radio_metadata")).configure_silence(
                settings.timeout_for(category), repair=category in REPAIR_CATEGORIES
            )
            configured.append(entity)
    gateway = getattr(entry, "runtime_data", None)
    entry_id = getattr(entry, "entry_id", None)
    if gateway is not None and entry_id:
        configured.append(EnOceanGatewayConnectivity(gateway, entry_id))
    async_add_entities(configured)


def _generic_binary_entities(row: dict) -> list[EnOceanEntity]:
    """Return the binary companions of a generic ``eep`` sensor row (PIR, contact...)."""
    if row.get("device_class") != GENERIC_SENSOR_DEVICE_CLASS:
        return []
    profile = generic_profile((row.get("radio_metadata") or {}).get("eep"))
    if profile is None:
        return []
    return [
        EnOceanEepBinarySensor(row["id"], row["name"], profile, field)
        for field in profile.exposed_fields
        if not field.is_value
    ]


class EnOceanEepBinarySensor(EnOceanEntity, BinarySensorEntity):
    """One enumerated field of a generic EEP.xml profile mapped to a binary sensor."""

    _attr_has_entity_name = True

    def __init__(
        self, dev_id: list[int], dev_name: str, profile: EepProfile, field: EepField
    ) -> None:
        """Bind one enum field (PIR, contact, rain) to one sender."""
        super().__init__(dev_id, dev_name)
        self._profile = profile
        self._field = field
        semantics = field.semantics
        self._on_values = semantics.on_values if semantics else frozenset()
        self._attr_unique_id = sensor_unique_id(dev_id, generic_field_key(field.slug))
        if semantics is not None:
            self._attr_translation_key = semantics.translation_key
            if semantics.device_class is not None:
                self._attr_device_class = BinarySensorDeviceClass(
                    semantics.device_class
                )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Name the profile and field so the source of a state is auditable."""
        return {"eep": self._profile.eep, "eep_field": self._field.shortcut}

    @override
    def value_changed(self, packet) -> None:
        """Decode a data telegram of the declared profile."""
        decoded = self._profile.decode(getattr(packet, "data", None))
        if decoded is None or self._field.slug not in decoded:
            return
        self._attr_is_on = decoded[self._field.slug] in self._on_values
        self.schedule_update_ha_state()


def _entities_for_row(row: dict) -> list[EnOceanEntity]:
    """Choose the entity classes a binary_sensor row creates.

    The declared EEP decides, never the telegram: a row without a declared
    profile stays the historical rocker/contact decoder, whatever its device
    class says, so existing installations keep their behaviour.
    """
    eep = (row.get("radio_metadata") or {}).get("eep")
    if eep == A514_EEP:
        return [EnOceanA514Contact(row["id"], row["name"])]
    if eep == SMOKE_EEP:
        return [
            EnOceanSmokeDetector(row["id"], row["name"], row["device_class"]),
            EnOceanSmokeBatteryLow(row["id"], row["name"]),
        ]
    return [EnOceanBinarySensor(row["id"], row["name"], row["device_class"])]


@dataclass(frozen=True, slots=True)
class SmokeStatus:
    """Decoded F6-05-02 DB0: alarm state (None when the frame says nothing about it)."""

    alarm: bool | None
    battery_low: bool


def decode_f6_05_02(packet) -> SmokeStatus | None:
    """Decode the three documented F6-05-02 values, ignoring everything else.

    EEP F6-05-02 (vendored EEP.xml): DB0 ``0x00`` smoke alarm off, ``0x10``
    smoke alarm on, ``0x30`` energy low. An energy-low frame says nothing
    about the alarm, so the alarm state is left untouched by it.
    """
    if getattr(packet, "rorg", None) != RORG.RPS or len(packet.data) < 2:
        return None
    value = packet.data[1]
    if value == 0x00:
        return SmokeStatus(alarm=False, battery_low=False)
    if value == 0x10:
        return SmokeStatus(alarm=True, battery_low=False)
    if value == 0x30:
        return SmokeStatus(alarm=None, battery_low=True)
    return None


class EnOceanSmokeDetector(EnOceanEntity, BinarySensorEntity):
    """F6-05-02 smoke alarm, created only for a row that declares the profile."""

    _attr_device_class = BinarySensorDeviceClass.SMOKE

    def __init__(
        self,
        dev_id: list[int],
        dev_name: str,
        device_class: BinarySensorDeviceClass | str | None,
    ) -> None:
        """Keep the row's historical identity so an upgraded row keeps its entity."""
        super().__init__(dev_id, dev_name)
        self._attr_name = dev_name
        self._attr_unique_id = binary_sensor_unique_id(dev_id, device_class)

    @override
    def value_changed(self, packet) -> None:
        """Apply the alarm state; an energy-low frame leaves it alone."""
        status = decode_f6_05_02(packet)
        if status is None or status.alarm is None:
            return
        self._attr_is_on = status.alarm
        self.schedule_update_ha_state()


class EnOceanSmokeBatteryLow(EnOceanEntity, BinarySensorEntity):
    """F6-05-02 energy-low companion (on = battery low)."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "smoke_battery_low"

    def __init__(self, dev_id: list[int], dev_name: str) -> None:
        """Bind the companion to the detector's sender."""
        super().__init__(dev_id, dev_name)
        self._attr_unique_id = smoke_battery_low_unique_id(dev_id)

    @override
    def value_changed(self, packet) -> None:
        """Energy low on 0x30; an alarm-on/off frame means the battery is fine."""
        status = decode_f6_05_02(packet)
        if status is None:
            return
        self._attr_is_on = status.battery_low
        self.schedule_update_ha_state()


class EnOceanGatewayConnectivity(EnOceanGatewayEntity, BinarySensorEntity):
    """Report whether the serial worker currently owns a live dongle."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, gateway, entry_id: str) -> None:
        """Bind the connectivity sensor to the dongle."""
        super().__init__(gateway, entry_id, GATEWAY_KEY_CONNECTED)

    @property
    def is_on(self) -> bool:
        """Mirror the dongle availability without becoming unavailable itself."""
        return bool(self._gateway.available)


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
        self._attr_unique_id = binary_sensor_unique_id(dev_id, device_class)
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
        """Decode supported RPS rocker and D5-00-01 contact telegrams."""
        if packet.rorg == RORG.BS1:
            if len(packet.data) < 2 or packet.data[1] not in (0x08, 0x09):
                return
            # D5-00-01: DB0 bit 0 is 1 when the contact is closed.
            # Home Assistant's door binary sensor is on when open.
            self._attr_is_on = not bool(packet.data[1] & 0x01)
            self.schedule_update_ha_state()
            return

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
        self._attr_unique_id = a514_contact_unique_id(dev_id)

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

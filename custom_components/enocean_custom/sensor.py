"""Sensor platform for EnOcean measurements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

import voluptuous as vol
from homeassistant.components.sensor import (
    PLATFORM_SCHEMA as SENSOR_PLATFORM_SCHEMA,
)
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_ID,
    CONF_NAME,
    PERCENTAGE,
    STATE_CLOSED,
    STATE_OPEN,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN, LOGGER
from .device import EnOceanEntity
from .enocean_library.protocol.constants import RORG
from .enocean_library.utils import combine_hex
from .learn import register_known_id
from .schema import CONF_UI_DEVICES, ENOCEAN_ID, exact_finite_int, valid_ui_devices

CONF_MAX_TEMP = "max_temp"
CONF_MIN_TEMP = "min_temp"
CONF_RANGE_FROM = "range_from"
CONF_RANGE_TO = "range_to"

DEFAULT_NAME = "EnOcean sensor"

SENSOR_TYPE_HUMIDITY = "humidity"
SENSOR_TYPE_POWER = "powersensor"
SENSOR_TYPE_TEMPERATURE = "temperature"
SENSOR_TYPE_WINDOWHANDLE = "windowhandle"
SENSOR_TYPE_SHUTTERCONTACT: str = "shuttercontact"

ATTR_SETPOINT, ATTR_SLIDESWITCH = "SetPoint", "SlideSwitch"


@dataclass(frozen=True, kw_only=True)
class EnOceanSensorEntityDescription(SensorEntityDescription):
    """Describe one measurement exposed by an EnOcean sender."""


SENSOR_DESC_TEMPERATURE = EnOceanSensorEntityDescription(
    key=SENSOR_TYPE_TEMPERATURE,
    name="Temperature",
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
)
SENSOR_DESC_HUMIDITY = EnOceanSensorEntityDescription(
    key=SENSOR_TYPE_HUMIDITY,
    name="Humidity",
    native_unit_of_measurement=PERCENTAGE,
    device_class=SensorDeviceClass.HUMIDITY,
    state_class=SensorStateClass.MEASUREMENT,
)
SENSOR_DESC_POWER = EnOceanSensorEntityDescription(
    key=SENSOR_TYPE_POWER,
    name="Power",
    native_unit_of_measurement=UnitOfPower.WATT,
    device_class=SensorDeviceClass.POWER,
    state_class=SensorStateClass.MEASUREMENT,
)
SENSOR_DESC_ENERGY = EnOceanSensorEntityDescription(
    key="energy",
    name="Energy",
    native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
)
SENSOR_DESC_WINDOWHANDLE = EnOceanSensorEntityDescription(
    key=SENSOR_TYPE_WINDOWHANDLE,
    name="WindowHandle",
    translation_key="window_handle",
)
SENSOR_DESC_SHUTTERCONTACT: EnOceanSensorEntityDescription = (
    EnOceanSensorEntityDescription(
        key=str(SENSOR_TYPE_SHUTTERCONTACT),
        name=("Shutter" + "Contact"),
        icon=("mdi:" + "window-open-variant"),
    )
)

SENSOR_TYPES = (
    SENSOR_TYPE_HUMIDITY,
    SENSOR_TYPE_POWER,
    SENSOR_TYPE_TEMPERATURE,
    SENSOR_TYPE_WINDOWHANDLE,
    SENSOR_TYPE_SHUTTERCONTACT,
)


def _validate_sensor_config(config: ConfigType) -> ConfigType:
    """Reject impossible scaling values for temperature profiles."""
    if config[CONF_DEVICE_CLASS] != SENSOR_TYPE_TEMPERATURE:
        return config
    if config[CONF_MIN_TEMP] >= config[CONF_MAX_TEMP]:
        raise vol.Invalid("min_temp must be lower than max_temp")
    if config[CONF_RANGE_FROM] == config[CONF_RANGE_TO]:
        raise vol.Invalid("range_from and range_to must differ")
    return config


PLATFORM_SCHEMA = vol.All(
    SENSOR_PLATFORM_SCHEMA.extend(
        {
            vol.Required(CONF_ID): ENOCEAN_ID,
            vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
            vol.Optional(CONF_DEVICE_CLASS, default=SENSOR_TYPE_POWER): vol.In(
                SENSOR_TYPES
            ),
            vol.Optional(CONF_MAX_TEMP, default=40): vol.All(
                exact_finite_int,
                vol.Range(min=-100, max=100),
            ),
            vol.Optional(CONF_MIN_TEMP, default=0): vol.All(
                exact_finite_int,
                vol.Range(min=-100, max=100),
            ),
            vol.Optional(CONF_RANGE_FROM, default=255): vol.All(
                exact_finite_int,
                vol.Range(min=0, max=255),
            ),
            vol.Optional(CONF_RANGE_TO, default=0): vol.All(
                exact_finite_int,
                vol.Range(min=0, max=255),
            ),
        }
    ),
    _validate_sensor_config,
)


def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Create YAML-configured measurement entities."""
    register_known_id(hass, config[CONF_ID])
    add_entities(_entities_from_config(config))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create measurement entities stored in the entry options."""
    entities: list[EnOceanSensor] = []
    for row in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, [])):
        if row["platform"] != "sensor":
            continue
        config = PLATFORM_SCHEMA(
            {
                "platform": DOMAIN,
                CONF_ID: row["id"],
                CONF_NAME: row["name"],
                CONF_DEVICE_CLASS: row[CONF_DEVICE_CLASS],
                CONF_MAX_TEMP: row[CONF_MAX_TEMP],
                CONF_MIN_TEMP: row[CONF_MIN_TEMP],
                CONF_RANGE_FROM: row[CONF_RANGE_FROM],
                CONF_RANGE_TO: row[CONF_RANGE_TO],
            }
        )
        entities.extend(
            entity.set_radio_metadata(row.get("radio_metadata"))
            for entity in _entities_from_config(config)
        )
    async_add_entities(entities)


def _entities_from_config(config: ConfigType) -> list[EnOceanSensor]:
    """Instantiate the entity set selected by a validated sensor profile."""
    device_id = config[CONF_ID]
    name = config[CONF_NAME]
    sensor_type = config[CONF_DEVICE_CLASS]

    if sensor_type == SENSOR_TYPE_TEMPERATURE:
        return [
            EnOceanTemperatureSensor(
                device_id,
                name,
                SENSOR_DESC_TEMPERATURE,
                scale_min=config[CONF_MIN_TEMP],
                scale_max=config[CONF_MAX_TEMP],
                range_from=config[CONF_RANGE_FROM],
                range_to=config[CONF_RANGE_TO],
            )
        ]
    if sensor_type == SENSOR_TYPE_HUMIDITY:
        return [EnOceanHumiditySensor(device_id, name, SENSOR_DESC_HUMIDITY)]
    if sensor_type == SENSOR_TYPE_POWER:
        # D2-01-0B reports instantaneous power and accumulated energy with
        # distinct unit codes, so both measurements need stable entities.
        return [
            EnOceanPowerSensor(device_id, name, SENSOR_DESC_POWER),
            EnOceanEnergySensor(device_id, name, SENSOR_DESC_ENERGY),
        ]
    if sensor_type == SENSOR_TYPE_WINDOWHANDLE:
        return [EnOceanWindowHandle(device_id, name, SENSOR_DESC_WINDOWHANDLE)]
    return [EnOceanShutterContact(device_id, name, SENSOR_DESC_SHUTTERCONTACT)]


class EnOceanSensor(EnOceanEntity, RestoreSensor):
    """Base entity for an EnOcean measurement."""

    def __init__(
        self,
        dev_id: list[int],
        dev_name: str,
        description: EnOceanSensorEntityDescription,
    ) -> None:
        """Initialize a sensor with a device-class-aware identity."""
        super().__init__(dev_id, dev_name)
        self.entity_description = description
        self._attr_name = f"{description.name} {dev_name}"
        self._attr_unique_id = f"{combine_hex(dev_id)}-{description.key}"

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the latest numeric or textual state."""
        await super().async_added_to_hass()
        if self._attr_native_value is not None:
            return
        if (sensor_data := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = sensor_data.native_value


def _decode_d2_measurement(packet) -> tuple[str, float] | None:
    """Decode D2-01 CMD 0x7 into canonical W or Wh units."""
    data = packet.data
    if len(data) < 12 or data[0] != RORG.VLD or data[1] & 0x0F != 0x07:
        return None
    unit = (data[2] >> 5) & 0x07
    raw_value = int.from_bytes(data[3:7], byteorder="big")
    conversions = {
        0x00: ("energy", raw_value / 3600),
        0x01: ("energy", raw_value),
        0x02: ("energy", raw_value * 1000),
        0x03: ("power", raw_value),
        0x04: ("power", raw_value * 1000),
    }
    return conversions.get(unit)


class EnOceanPowerSensor(EnOceanSensor):
    """Expose A5-12-01 or D2-01-0B instantaneous power."""

    @override
    def value_changed(self, packet) -> None:
        """Decode an instantaneous power report."""
        if not packet.data:
            return
        if packet.data[0] == RORG.VLD:
            decoded = _decode_d2_measurement(packet)
            if decoded is None or decoded[0] != "power":
                return
            self._attr_native_value = decoded[1]
            self.schedule_update_ha_state()
            return
        if packet.data[0] != RORG.BS4 or len(packet.data) < 5:
            return
        packet.parse_eep(0x12, 0x01)
        if packet.parsed["DT"]["raw_value"] != 1:
            return
        reading = packet.parsed["MR"]["raw_value"]
        divisor = packet.parsed["DIV"]["raw_value"]
        self._attr_native_value = reading / (10**divisor)
        self.schedule_update_ha_state()


class EnOceanEnergySensor(EnOceanSensor):
    """Expose A5-12-01 or D2-01-0B accumulated energy."""

    @override
    def value_changed(self, packet) -> None:
        """Decode an accumulated energy report in watt-hours."""
        if not packet.data:
            return
        if packet.data[0] == RORG.VLD:
            decoded = _decode_d2_measurement(packet)
            if decoded is None or decoded[0] != "energy":
                return
            self._attr_native_value = decoded[1]
            self.schedule_update_ha_state()
            return
        if packet.data[0] != RORG.BS4 or len(packet.data) < 5:
            return
        packet.parse_eep(0x12, 0x01)
        if packet.parsed["DT"]["raw_value"] != 0:
            return
        reading = packet.parsed["MR"]["raw_value"]
        divisor = packet.parsed["DIV"]["raw_value"]
        self._attr_native_value = reading / (10**divisor)
        self.schedule_update_ha_state()


class EnOceanTemperatureSensor(EnOceanSensor):
    """Expose an 8-bit A5 temperature profile and panel controls."""

    def __init__(
        self,
        dev_id: list[int],
        dev_name: str,
        description: EnOceanSensorEntityDescription,
        *,
        scale_min: int,
        scale_max: int,
        range_from: int,
        range_to: int,
    ) -> None:
        """Store the configured linear raw-to-temperature scale."""
        super().__init__(dev_id, dev_name, description)
        self._scale_min = scale_min
        self._scale_max = scale_max
        self.range_from = range_from
        self.range_to = range_to
        self.setpoint: int | None = None
        self.slideswitch: int | None = None

    @override
    async def async_added_to_hass(self) -> None:
        """Restore panel setpoint and day/night attributes."""
        await super().async_added_to_hass()
        if (old_state := await self.async_get_last_state()) is None:
            return
        if self.setpoint is None:
            restored_setpoint = old_state.attributes.get(ATTR_SETPOINT)
            self.setpoint = restored_setpoint
        if self.slideswitch is None:
            restored_switch = old_state.attributes.get(ATTR_SLIDESWITCH)
            self.slideswitch = restored_switch

    @property
    def extra_state_attributes(self) -> dict[str, int | None]:
        """Expose the room-panel controls consumed by the climate platform."""
        return {
            ATTR_SETPOINT: getattr(self, "setpoint", None),
            ATTR_SLIDESWITCH: getattr(self, "slideswitch", None),
        }

    @override
    def value_changed(self, packet) -> None:
        """Apply the configured linear scale to a complete A5 telegram."""
        if packet.rorg != RORG.BS4 or len(packet.data) < 5:
            return
        raw_span = self.range_to - self.range_from
        temperature_span = self._scale_max - self._scale_min
        raw_temperature = packet.data[3]
        temperature = (
            temperature_span / raw_span * (raw_temperature - self.range_from)
            + self._scale_min
        )
        self._attr_native_value = round(temperature, 1)
        panel_setpoint = packet.data[2]
        self.setpoint = panel_setpoint
        self.slideswitch = packet.data[4] & 1
        self.schedule_update_ha_state()


class EnOceanHumiditySensor(EnOceanSensor):
    """Expose relative humidity from compatible A5 room sensors."""

    @override
    def value_changed(self, packet) -> None:
        """Map the EEP 0..250 humidity field to percent."""
        if packet.rorg != RORG.BS4 or len(packet.data) < 3:
            return
        self._attr_native_value = round(packet.data[2] * 100 / 250, 1)
        self.schedule_update_ha_state()


class EnOceanWindowHandle(EnOceanSensor):
    """Expose the position of an F6-10-00 mechanical handle."""

    @override
    def value_changed(self, packet) -> None:
        """Decode closed, open, and tilt positions."""
        if packet.rorg != RORG.RPS or len(packet.data) < 2:
            return
        action = (packet.data[1] & 0x70) >> 4
        positions = {
            0x07: STATE_CLOSED,
            0x06: STATE_OPEN,
            0x05: "tilt",
            0x04: STATE_OPEN,
        }
        if action not in positions:
            return
        self._attr_native_value = positions[action]
        self.schedule_update_ha_state()


class EnOceanShutterContact(EnOceanSensor):  # D5-00-01
    """Expose a D5-00-01 single-input contact."""

    @override
    def value_changed(self, packet) -> None:
        """Decode the contact bit from a complete 1BS telegram."""
        if packet.rorg != RORG.BS1 or len(packet.data) < 2:
            return
        contact_state = {0x09: STATE_CLOSED, 0x08: STATE_OPEN}.get(packet.data[1])
        if contact_state is None:
            LOGGER.debug("Ignoring unsupported D5 contact value")
            return
        self._attr_native_value = contact_state
        self.schedule_update_ha_state()

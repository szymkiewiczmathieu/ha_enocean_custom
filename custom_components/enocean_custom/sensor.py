"""Support for EnOcean sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.sensor import (
    PLATFORM_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
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
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN
from .device import EnOceanEntity
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
SENSOR_TYPE_SHUTTERCONTACT = "shuttercontact"

ATTR_SETPOINT = "SetPoint"
ATTR_SLIDESWITCH = "SlideSwitch"


@dataclass
class EnOceanSensorEntityDescriptionMixin:
    """Mixin for required keys."""

    unique_id: Callable[[list[int]], str | None]


@dataclass
class EnOceanSensorEntityDescription(
    SensorEntityDescription, EnOceanSensorEntityDescriptionMixin
):
    """Describes EnOcean sensor entity."""


SENSOR_DESC_TEMPERATURE = EnOceanSensorEntityDescription(
    key=SENSOR_TYPE_TEMPERATURE,
    name="Temperature",
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    icon="mdi:thermometer",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    unique_id=lambda dev_id: f"{combine_hex(dev_id)}-{SENSOR_TYPE_TEMPERATURE}",
)

SENSOR_DESC_HUMIDITY = EnOceanSensorEntityDescription(
    key=SENSOR_TYPE_HUMIDITY,
    name="Humidity",
    native_unit_of_measurement=PERCENTAGE,
    icon="mdi:water-percent",
    device_class=SensorDeviceClass.HUMIDITY,
    state_class=SensorStateClass.MEASUREMENT,
    unique_id=lambda dev_id: f"{combine_hex(dev_id)}-{SENSOR_TYPE_HUMIDITY}",
)

SENSOR_DESC_POWER = EnOceanSensorEntityDescription(
    key=SENSOR_TYPE_POWER,
    name="Power",
    native_unit_of_measurement=UnitOfPower.WATT,
    icon="mdi:power-plug",
    device_class=SensorDeviceClass.POWER,
    state_class=SensorStateClass.MEASUREMENT,
    unique_id=lambda dev_id: f"{combine_hex(dev_id)}-{SENSOR_TYPE_POWER}",
)

SENSOR_DESC_WINDOWHANDLE = EnOceanSensorEntityDescription(
    key=SENSOR_TYPE_WINDOWHANDLE,
    name="WindowHandle",
    icon="mdi:window-open-variant",
    unique_id=lambda dev_id: f"{combine_hex(dev_id)}-{SENSOR_TYPE_WINDOWHANDLE}",
)

SENSOR_DESC_SHUTTERCONTACT = EnOceanSensorEntityDescription(
    key=SENSOR_TYPE_SHUTTERCONTACT,
    name="ShutterContact",
    icon="mdi:window-open-variant",
    unique_id=lambda dev_id: f"{combine_hex(dev_id)}-{SENSOR_TYPE_SHUTTERCONTACT}",
)


SENSOR_TYPES = (
    SENSOR_TYPE_HUMIDITY,
    SENSOR_TYPE_POWER,
    SENSOR_TYPE_TEMPERATURE,
    SENSOR_TYPE_WINDOWHANDLE,
    SENSOR_TYPE_SHUTTERCONTACT,
)


def _validate_sensor_config(config: ConfigType) -> ConfigType:
    """Reject impossible temperature scaling before entity creation."""
    if config[CONF_DEVICE_CLASS] == SENSOR_TYPE_TEMPERATURE:
        if config[CONF_MIN_TEMP] >= config[CONF_MAX_TEMP]:
            raise vol.Invalid("min_temp must be lower than max_temp")
        if config[CONF_RANGE_FROM] == config[CONF_RANGE_TO]:
            raise vol.Invalid("range_from and range_to must differ")
    return config


PLATFORM_SCHEMA = vol.All(
    PLATFORM_SCHEMA.extend(
        {
            vol.Required(CONF_ID): ENOCEAN_ID,
            vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
            vol.Optional(CONF_DEVICE_CLASS, default=SENSOR_TYPE_POWER): vol.In(
                SENSOR_TYPES
            ),
            vol.Optional(CONF_MAX_TEMP, default=40): vol.All(
                exact_finite_int, vol.Range(min=-100, max=100)
            ),
            vol.Optional(CONF_MIN_TEMP, default=0): vol.All(
                exact_finite_int, vol.Range(min=-100, max=100)
            ),
            vol.Optional(CONF_RANGE_FROM, default=255): vol.All(
                exact_finite_int, vol.Range(min=0, max=255)
            ),
            vol.Optional(CONF_RANGE_TO, default=0): vol.All(
                exact_finite_int, vol.Range(min=0, max=255)
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
    """Set up an EnOcean sensor device."""
    register_known_id(hass, config[CONF_ID])
    _create_sensor_entities(config, add_entities)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EnOcean sensors configured entirely from the UI."""
    for device in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, [])):
        if device["platform"] != "sensor":
            continue
        config = PLATFORM_SCHEMA(
            {
                "platform": DOMAIN,
                CONF_ID: device["id"],
                CONF_NAME: device["name"],
                CONF_DEVICE_CLASS: device[CONF_DEVICE_CLASS],
                CONF_MAX_TEMP: device[CONF_MAX_TEMP],
                CONF_MIN_TEMP: device[CONF_MIN_TEMP],
                CONF_RANGE_FROM: device[CONF_RANGE_FROM],
                CONF_RANGE_TO: device[CONF_RANGE_TO],
            }
        )
        entities: list[EnOceanSensor] = []
        _create_sensor_entities(config, entities.extend)
        async_add_entities(entities)


def _create_sensor_entities(
    config: ConfigType, add_entities: AddEntitiesCallback
) -> None:
    """Create sensor entities from already validated config."""
    dev_id = config[CONF_ID]
    dev_name = config[CONF_NAME]
    sensor_type = config[CONF_DEVICE_CLASS]
    entities: list[EnOceanSensor] = []
    if sensor_type == SENSOR_TYPE_TEMPERATURE:
        entities = [
            EnOceanTemperatureSensor(
                dev_id,
                dev_name,
                SENSOR_DESC_TEMPERATURE,
                scale_min=config[CONF_MIN_TEMP],
                scale_max=config[CONF_MAX_TEMP],
                range_from=config[CONF_RANGE_FROM],
                range_to=config[CONF_RANGE_TO],
            )
        ]
    elif sensor_type == SENSOR_TYPE_HUMIDITY:
        entities = [EnOceanHumiditySensor(dev_id, dev_name, SENSOR_DESC_HUMIDITY)]
    elif sensor_type == SENSOR_TYPE_POWER:
        entities = [EnOceanPowerSensor(dev_id, dev_name, SENSOR_DESC_POWER)]
    elif sensor_type == SENSOR_TYPE_WINDOWHANDLE:
        entities = [EnOceanWindowHandle(dev_id, dev_name, SENSOR_DESC_WINDOWHANDLE)]
    elif sensor_type == SENSOR_TYPE_SHUTTERCONTACT:
        entities = [EnOceanShutterContact(dev_id, dev_name, SENSOR_DESC_SHUTTERCONTACT)]
    add_entities(entities)


class EnOceanSensor(EnOceanEntity, RestoreEntity, SensorEntity):
    """Representation of an  EnOcean sensor device such as a power meter."""

    def __init__(
        self, dev_id, dev_name, description: EnOceanSensorEntityDescription
    ) -> None:
        """Initialize the EnOcean sensor device."""
        super().__init__(dev_id, dev_name)
        self.entity_description = description
        self._attr_name = f"{description.name} {dev_name}"
        self._attr_unique_id = description.unique_id(dev_id)

    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        # If not None, we got an initial value.
        await super().async_added_to_hass()
        if self._attr_native_value is not None:
            return

        if (state := await self.async_get_last_state()) is not None:
            if self.entity_description.state_class is None:
                self._attr_native_value = state.state
                return
            try:
                self._attr_native_value = float(state.state)
            except (TypeError, ValueError):
                return

    def value_changed(self, packet):
        """Update the internal state of the sensor."""


class EnOceanPowerSensor(EnOceanSensor):
    """Representation of an EnOcean power sensor.

    EEPs (EnOcean Equipment Profiles):
    - A5-12-01 (Automated Meter Reading, Electricity)
    """

    def value_changed(self, packet):
        """Update the internal state of the sensor."""
        if packet.rorg != 0xA5 or len(packet.data) < 5:
            return
        packet.parse_eep(0x12, 0x01)
        if packet.parsed["DT"]["raw_value"] == 1:
            # this packet reports the current value
            raw_val = packet.parsed["MR"]["raw_value"]
            divisor = packet.parsed["DIV"]["raw_value"]
            self._attr_native_value = raw_val / (10**divisor)
            self.schedule_update_ha_state()


class EnOceanTemperatureSensor(EnOceanSensor):
    """Representation of an EnOcean temperature sensor device.

    EEPs (EnOcean Equipment Profiles):
    - A5-02-01 to A5-02-1B All 8 Bit Temperature Sensors of A5-02
    - A5-10-01 to A5-10-14 (Room Operating Panels)
    - A5-04-01 (Temp. and Humidity Sensor, Range 0°C to +40°C and 0% to 100%)
    - A5-04-02 (Temp. and Humidity Sensor, Range -20°C to +60°C and 0% to 100%)
    - A5-10-10 (Temp. and Humidity Sensor and Set Point)
    - A5-10-12 (Temp. and Humidity Sensor, Set Point and Occupancy Control)
    - 10 Bit Temp. Sensors are not supported (A5-02-20, A5-02-30)

    For the following EEPs the scales must be set to "0 to 250":
    - A5-04-01
    - A5-04-02
    - A5-10-10 to A5-10-14
    """

    def __init__(
        self,
        dev_id,
        dev_name,
        description: EnOceanSensorEntityDescription,
        *,
        scale_min,
        scale_max,
        range_from,
        range_to,
    ) -> None:
        """Initialize the EnOcean temperature sensor device."""
        super().__init__(dev_id, dev_name, description)
        self._scale_min = scale_min
        self._scale_max = scale_max
        self.range_from = range_from
        self.range_to = range_to
        self.setpoint = None
        self.slideswitch = None

    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        # If not None, we got an initial value.
        await super().async_added_to_hass()

        if (old_state := await self.async_get_last_state()) is not None:
            # state is restored in EnOceanSensor class
            # restore attributes
            if self.setpoint is None:
                self.setpoint = old_state.attributes.get(ATTR_SETPOINT)
            if self.slideswitch is None:
                self.slideswitch = old_state.attributes.get(ATTR_SLIDESWITCH)

    @property
    def extra_state_attributes(self):
        """Return entity specific state attributes."""
        self._attrs = {
            ATTR_SETPOINT: self.setpoint,
            ATTR_SLIDESWITCH: self.slideswitch,
        }
        return self._attrs

    def value_changed(self, packet):
        """Update the internal state of the sensor."""
        if packet.rorg != 0xA5 or len(packet.data) < 5:
            return
        temp_scale = self._scale_max - self._scale_min
        temp_range = self.range_to - self.range_from
        raw_val = packet.data[3]
        temperature = temp_scale / temp_range * (raw_val - self.range_from)
        temperature += self._scale_min
        self._attr_native_value = round(temperature, 1)
        self.setpoint = packet.data[2]
        self.slideswitch = packet.data[4] & 1
        self.schedule_update_ha_state()


class EnOceanHumiditySensor(EnOceanSensor):
    """Representation of an EnOcean humidity sensor device.

    EEPs (EnOcean Equipment Profiles):
    - A5-04-01 (Temp. and Humidity Sensor, Range 0°C to +40°C and 0% to 100%)
    - A5-04-02 (Temp. and Humidity Sensor, Range -20°C to +60°C and 0% to 100%)
    - A5-10-10 to A5-10-14 (Room Operating Panels)
    """

    def value_changed(self, packet):
        """Update the internal state of the sensor."""
        if packet.rorg != 0xA5 or len(packet.data) < 3:
            return
        humidity = packet.data[2] * 100 / 250
        self._attr_native_value = round(humidity, 1)
        self.schedule_update_ha_state()


class EnOceanWindowHandle(EnOceanSensor):
    """Representation of an EnOcean window handle device.

    EEPs (EnOcean Equipment Profiles):
    - F6-10-00 (Mechanical handle / Hoppe AG)
    """

    def value_changed(self, packet):
        """Update the internal state of the sensor."""
        if packet.rorg != 0xF6 or len(packet.data) < 2:
            return
        action = (packet.data[1] & 0x70) >> 4

        if action == 0x07:
            self._attr_native_value = STATE_CLOSED
        if action in (0x04, 0x06):
            self._attr_native_value = STATE_OPEN
        if action == 0x05:
            self._attr_native_value = "tilt"

        self.schedule_update_ha_state()


class EnOceanShutterContact(EnOceanSensor):
    """Representation of an EnOcean shutter contact device.

    EEPs (EnOcean Equipment Profiles):
    - D5-00-01 (Shutter contact)
    """

    def value_changed(self, packet):
        """Update the internal state of the sensor."""
        if packet.rorg != 0xD5 or len(packet.data) < 2:
            return
        if packet.data[1] == 0x09:
            self._attr_native_value = STATE_CLOSED
        if packet.data[1] == 0x08:
            self._attr_native_value = STATE_OPEN

        self.schedule_update_ha_state()

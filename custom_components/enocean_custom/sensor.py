"""Sensor platform for EnOcean measurements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, override

import voluptuous as vol
from homeassistant.components.sensor import (
    PLATFORM_SCHEMA as SENSOR_PLATFORM_SCHEMA,
)
from homeassistant.components.sensor import (
    RestoreSensor,
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
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    STATE_CLOSED,
    STATE_OPEN,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .availability import REPAIR_CATEGORIES, availability_settings, silence_category
from .const import DOMAIN, LOGGER
from .d2_14 import (
    AIR_QUALITY_STATES,
    COMFORT_STATES,
    ENERGY_STORAGE_STATES,
    decode_d2_14_30,
)
from .eep_catalog import EepField, EepProfile, generic_profile
from .enocean_library.protocol.constants import RORG
from .enocean_library.utils import combine_hex, to_hex_string
from .entity import EnOceanEntity, EnOceanGatewayEntity
from .identity import (
    A514_EEP,
    D2_14_30_EEP,
    GATEWAY_KEY_BASE_ID,
    GATEWAY_KEY_LAST_TELEGRAM,
    GATEWAY_KEY_RECEIVED_TELEGRAMS,
    GENERIC_SENSOR_DEVICE_CLASS,
    SENSOR_KEY_A514_SUPPLY_VOLTAGE,
    SENSOR_KEY_CONTACT,
    SENSOR_KEY_ENERGY,
    SENSOR_KEY_HUMIDITY,
    SENSOR_KEY_POWER,
    SENSOR_KEY_RADIO_LAST_SEEN,
    SENSOR_KEY_RADIO_REPEATER_COUNT,
    SENSOR_KEY_RADIO_RSSI,
    SENSOR_KEY_SHUTTERCONTACT,
    SENSOR_KEY_SIGNAL_ENERGY,
    SENSOR_KEY_TEMPERATURE,
    SENSOR_KEY_WINDOWHANDLE,
    device_names_by_sender,
    generic_field_key,
    sensor_unique_id,
)
from .inbox import PacketObservation, packet_observation
from .learn import register_known_id
from .schema import CONF_UI_DEVICES, ENOCEAN_ID, exact_finite_int, valid_ui_devices
from .yaml_import import track_yaml_device

# Every state change is pushed by the serial worker; nothing here polls.
PARALLEL_UPDATES = 0

CONF_MAX_TEMP = "max_temp"
CONF_MIN_TEMP = "min_temp"
CONF_RANGE_FROM = "range_from"
CONF_RANGE_TO = "range_to"

DEFAULT_NAME = "EnOcean sensor"

SENSOR_TYPE_HUMIDITY = "humidity"
SENSOR_TYPE_POWER = "powersensor"
SENSOR_TYPE_TEMPERATURE = "temperature"
SENSOR_TYPE_WINDOWHANDLE = "windowhandle"
SENSOR_TYPE_CONTACT = "contact"
# Retain the historical YAML/UI key as an alias for D5-00-01 contacts.
SENSOR_TYPE_SHUTTERCONTACT: str = "shuttercontact"

ATTR_SETPOINT, ATTR_SLIDESWITCH = "SetPoint", "SlideSwitch"


@dataclass(frozen=True, kw_only=True)
class EnOceanSensorEntityDescription(SensorEntityDescription):
    """Describe one measurement exposed by an EnOcean sender."""


SENSOR_DESC_TEMPERATURE = EnOceanSensorEntityDescription(
    key=SENSOR_KEY_TEMPERATURE,
    name="Temperature",
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
)
SENSOR_DESC_HUMIDITY = EnOceanSensorEntityDescription(
    key=SENSOR_KEY_HUMIDITY,
    name="Humidity",
    native_unit_of_measurement=PERCENTAGE,
    device_class=SensorDeviceClass.HUMIDITY,
    state_class=SensorStateClass.MEASUREMENT,
)
SENSOR_DESC_POWER = EnOceanSensorEntityDescription(
    key=SENSOR_KEY_POWER,
    name="Power",
    native_unit_of_measurement=UnitOfPower.WATT,
    device_class=SensorDeviceClass.POWER,
    state_class=SensorStateClass.MEASUREMENT,
)
SENSOR_DESC_ENERGY = EnOceanSensorEntityDescription(
    key=SENSOR_KEY_ENERGY,
    name="Energy",
    native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
)
SENSOR_DESC_SUPPLY_VOLTAGE = EnOceanSensorEntityDescription(
    key=SENSOR_KEY_A514_SUPPLY_VOLTAGE,
    name="Supply voltage",
    native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    device_class=SensorDeviceClass.VOLTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
)
# State values are frozen: automations in the field match on them. The ENUM
# device class only adds the option list, per-state icons (icons.json) and
# translated state labels (strings.json ``entity.sensor``); an unknown
# position is exposed as ``None`` so Home Assistant renders ``unknown`` as
# before without violating the option list.
HANDLE_STATE_TILT = "tilt"
WINDOW_HANDLE_OPTIONS = [STATE_OPEN, HANDLE_STATE_TILT, STATE_CLOSED]
CONTACT_OPTIONS = [STATE_OPEN, STATE_CLOSED]

SENSOR_DESC_WINDOWHANDLE = EnOceanSensorEntityDescription(
    key=SENSOR_KEY_WINDOWHANDLE,
    name="WindowHandle",
    translation_key="window_handle",
    device_class=SensorDeviceClass.ENUM,
    options=WINDOW_HANDLE_OPTIONS,
)
SENSOR_DESC_CONTACT = EnOceanSensorEntityDescription(
    key=SENSOR_KEY_CONTACT,
    name="Contact",
    translation_key="d5_contact",
    device_class=SensorDeviceClass.ENUM,
    options=CONTACT_OPTIONS,
)
SENSOR_DESC_SHUTTERCONTACT: EnOceanSensorEntityDescription = (
    EnOceanSensorEntityDescription(
        key=SENSOR_KEY_SHUTTERCONTACT,
        name=("Shutter" + "Contact"),
        translation_key="d5_contact",
        device_class=SensorDeviceClass.ENUM,
        options=CONTACT_OPTIONS,
    )
)

SENSOR_TYPES = (
    SENSOR_TYPE_HUMIDITY,
    SENSOR_TYPE_POWER,
    SENSOR_TYPE_TEMPERATURE,
    SENSOR_TYPE_WINDOWHANDLE,
    SENSOR_TYPE_CONTACT,
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
    track_yaml_device(hass, "sensor", config)
    register_known_id(hass, config[CONF_ID])
    add_entities(_entities_from_config(config))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create measurement entities stored in the entry options.

    Row entities come first; the device-level radio diagnostics and the
    gateway's own sensors are appended after them.
    """
    entities: list[SensorEntity] = []
    settings = availability_settings(getattr(entry, "options", {}))
    rows = valid_ui_devices(entry.options.get(CONF_UI_DEVICES, []))
    for row in rows:
        category = silence_category(row)
        silence = (settings.timeout_for(category), category in REPAIR_CATEGORIES)
        if (
            row["platform"] == "binary_sensor"
            and (row.get("radio_metadata") or {}).get("eep") == A514_EEP
        ):
            entities.append(
                EnOceanA514Voltage(row["id"], row["name"], SENSOR_DESC_SUPPLY_VOLTAGE)
                .set_radio_metadata(row.get("radio_metadata"))
                .configure_silence(silence[0], repair=silence[1])
            )
            continue
        if (
            row["platform"] == "binary_sensor"
            and (row.get("radio_metadata") or {}).get("eep") == D2_14_30_EEP
        ):
            entities.extend(
                EnOceanD21430Sensor(row["id"], row["name"], description)
                .set_radio_metadata(row.get("radio_metadata"))
                .configure_silence(silence[0], repair=silence[1])
                for description in D2_14_30_SENSOR_DESCRIPTIONS
            )
            continue
        if row["platform"] != "sensor":
            continue
        if row[CONF_DEVICE_CLASS] == GENERIC_SENSOR_DEVICE_CLASS:
            # The row carries a generic EEP (validated by the schema); every
            # numeric field of the profile becomes one sensor.
            profile = generic_profile((row.get("radio_metadata") or {}).get("eep"))
            if profile is None:
                continue
            entities.extend(
                EnOceanEepValueSensor(row["id"], row["name"], profile, field)
                .set_radio_metadata(row.get("radio_metadata"))
                .configure_silence(silence[0], repair=silence[1])
                for field in profile.exposed_fields
                if field.is_value
            )
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
            entity.set_radio_metadata(row.get("radio_metadata")).configure_silence(
                silence[0], repair=silence[1]
            )
            for entity in _entities_from_config(config)
        )
    entities.extend(_radio_diagnostic_entities(rows))
    entities.extend(_gateway_entities(entry))
    async_add_entities(entities)


def _gateway_entities(entry: Any) -> list[SensorEntity]:
    """Return the dongle's own diagnostic sensors for a real config entry."""
    gateway = getattr(entry, "runtime_data", None)
    entry_id = getattr(entry, "entry_id", None)
    if gateway is None or not entry_id:
        return []
    return [
        EnOceanGatewayTelegramCounter(gateway, entry_id),
        EnOceanGatewayLastTelegram(gateway, entry_id),
        EnOceanGatewayBaseId(gateway, entry_id),
    ]


class EnOceanGatewayTelegramCounter(EnOceanGatewayEntity, SensorEntity):
    """Count the valid radio telegrams the serial worker dispatched."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _tracks_telegrams = True

    def __init__(self, gateway: Any, entry_id: str) -> None:
        """Bind the counter to the dongle."""
        super().__init__(gateway, entry_id, GATEWAY_KEY_RECEIVED_TELEGRAMS)

    @property
    def native_value(self) -> int:
        """Return the worker's lifetime telegram count (resets on reload)."""
        return int(self._gateway.received_radio_packets)


class EnOceanGatewayLastTelegram(EnOceanGatewayEntity, SensorEntity):
    """Expose when the dongle last delivered a valid radio telegram."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _tracks_telegrams = True

    def __init__(self, gateway: Any, entry_id: str) -> None:
        """Bind the timestamp to the dongle."""
        super().__init__(gateway, entry_id, GATEWAY_KEY_LAST_TELEGRAM)

    @property
    def native_value(self) -> datetime | None:
        """Return the UTC time of the last dispatched telegram."""
        return self._gateway.last_packet_at


class EnOceanGatewayBaseId(EnOceanGatewayEntity, SensorEntity):
    """Expose the dongle's resolved Base ID, the sender of every command.

    Disabled by default: it is an identifier, useful when pairing a receiver
    (A5-38-08 dimmers, climate actuators) that must learn the sender address.
    """

    _attr_entity_registry_enabled_default = False
    # The Base ID resolves asynchronously and may be re-requested later; any
    # telegram or gateway update is an occasion to refresh the value.
    _tracks_telegrams = True

    def __init__(self, gateway: Any, entry_id: str) -> None:
        """Bind the identifier to the dongle."""
        super().__init__(gateway, entry_id, GATEWAY_KEY_BASE_ID)

    @property
    def native_value(self) -> str | None:
        """Return the Base ID as AA:BB:CC:DD once the dongle reported it."""
        base_id = self._gateway.base_id
        if not isinstance(base_id, (list, tuple)) or len(base_id) != 4:
            return None
        return to_hex_string(list(base_id))


@dataclass(frozen=True, kw_only=True)
class D21430SensorDescription(SensorEntityDescription):
    """One measurement or status of a D2-14-30 detector."""

    attribute: str = ""


D2_14_30_SENSOR_DESCRIPTIONS: tuple[D21430SensorDescription, ...] = (
    D21430SensorDescription(
        key="d2_14_30_temperature",
        translation_key="d2_14_30_temperature",
        attribute="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    D21430SensorDescription(
        key="d2_14_30_humidity",
        translation_key="d2_14_30_humidity",
        attribute="humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    D21430SensorDescription(
        key="d2_14_30_energy_storage",
        translation_key="d2_14_30_energy_storage",
        attribute="energy_storage",
        device_class=SensorDeviceClass.ENUM,
        options=list(ENERGY_STORAGE_STATES),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    D21430SensorDescription(
        key="d2_14_30_comfort",
        translation_key="d2_14_30_comfort",
        attribute="comfort",
        device_class=SensorDeviceClass.ENUM,
        options=list(COMFORT_STATES),
    ),
    D21430SensorDescription(
        key="d2_14_30_air_quality",
        translation_key="d2_14_30_air_quality",
        attribute="air_quality",
        device_class=SensorDeviceClass.ENUM,
        options=list(AIR_QUALITY_STATES),
    ),
    D21430SensorDescription(
        key="d2_14_30_maintenance_weeks",
        translation_key="d2_14_30_maintenance_weeks",
        attribute="maintenance_weeks",
        native_unit_of_measurement=UnitOfTime.WEEKS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    D21430SensorDescription(
        key="d2_14_30_remaining_life",
        translation_key="d2_14_30_remaining_life",
        attribute="remaining_life_months",
        native_unit_of_measurement=UnitOfTime.MONTHS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


class EnOceanD21430Sensor(EnOceanEntity, RestoreSensor):
    """One measurement or status of a D2-14-30 multi-sensor smoke detector."""

    _attr_has_entity_name = True

    def __init__(
        self, dev_id: list[int], dev_name: str, description: D21430SensorDescription
    ) -> None:
        """Bind one decoded attribute to the detector's sender."""
        super().__init__(dev_id, dev_name)
        self.entity_description = description
        self._attr_unique_id = sensor_unique_id(dev_id, description.key)

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the last value, dropping anything outside an option list."""
        await super().async_added_to_hass()
        if self._attr_native_value is not None:
            return
        if (sensor_data := await self.async_get_last_sensor_data()) is not None:
            restored = sensor_data.native_value
            options = self.options
            if options is not None and restored not in options:
                restored = None
            self._attr_native_value = restored

    @override
    def value_changed(self, packet) -> None:
        """Apply this entity's attribute; invalid readings keep the last value."""
        status = decode_d2_14_30(getattr(packet, "data", None))
        if status is None:
            return
        value = getattr(status, self.entity_description.attribute)
        if value is None:
            return
        self._attr_native_value = value
        self.schedule_update_ha_state()


class EnOceanEepValueSensor(EnOceanEntity, RestoreSensor):
    """One numeric field of a generic EEP.xml profile.

    Decoding uses the bit offsets, ranges and scales of the vendored EEP.xml;
    the shortcut of the specification selects the device class, unit and
    translated name. Out-of-range raw values (documented as invalid or
    reserved) are skipped rather than published.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        dev_id: list[int],
        dev_name: str,
        profile: EepProfile,
        field: EepField,
    ) -> None:
        """Bind one profile field to one sender."""
        super().__init__(dev_id, dev_name)
        self._profile = profile
        self._field = field
        self._attr_unique_id = sensor_unique_id(dev_id, generic_field_key(field.slug))
        self._attr_native_unit_of_measurement = field.normalized_unit
        semantics = field.semantics
        if semantics is None:
            # Unmapped shortcut: the XML description is the only name we have.
            self._attr_name = field.description
            return
        self._attr_translation_key = semantics.translation_key
        if semantics.device_class is not None:
            self._attr_device_class = SensorDeviceClass(semantics.device_class)
        if semantics.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = semantics.enabled_default

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Name the profile and field so the source of a value is auditable."""
        return {"eep": self._profile.eep, "eep_field": self._field.shortcut}

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the last decoded value."""
        await super().async_added_to_hass()
        if self._attr_native_value is not None:
            return
        if (sensor_data := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = sensor_data.native_value

    @override
    def value_changed(self, packet) -> None:
        """Decode a data telegram of the declared profile."""
        decoded = self._profile.decode(getattr(packet, "data", None))
        if decoded is None or self._field.slug not in decoded:
            return
        self._attr_native_value = decoded[self._field.slug]
        self.schedule_update_ha_state()


def _radio_diagnostic_entities(rows: list[dict]) -> list[EnOceanRadioDiagnosticSensor]:
    """Return one RSSI / last-seen / repeater set per configured sender.

    The set belongs to the device, so a sender that owns several rows (two
    relay channels, a rocker that is also a switch) gets exactly one set, named
    after the device.
    """
    names = device_names_by_sender(rows)
    entities: list[EnOceanRadioDiagnosticSensor] = []
    seen: set[int] = set()
    for row in rows:
        sender = combine_hex(row["id"])
        if sender in seen:
            continue
        seen.add(sender)
        for sensor_class in (
            EnOceanRssiSensor,
            EnOceanLastSeenSensor,
            EnOceanRepeaterCountSensor,
            EnOceanSignalEnergySensor,
        ):
            entities.append(
                sensor_class(row["id"], names[sender]).set_radio_metadata(
                    row.get("radio_metadata")
                )
            )
    return entities


class EnOceanRadioDiagnosticSensor(EnOceanEntity, RestoreSensor):
    """Radio facts about one sender, fed by every telegram it transmits.

    Disabled by default (Quality Scale ``entity-disabled-by-default``): they
    are a radio-coverage dashboard for people who want one, not noise for
    everyone. Values are restored across restarts so a device that transmits
    rarely does not look blank after a reboot.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_has_entity_name = True
    _key = ""

    def __init__(self, dev_id: list[int], dev_name: str) -> None:
        """Bind the diagnostic to one sender identity."""
        super().__init__(dev_id, dev_name)
        self._attr_unique_id = sensor_unique_id(dev_id, self._key)
        self._attr_translation_key = self._key

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the last known radio fact."""
        await super().async_added_to_hass()
        if self._attr_native_value is not None:
            return
        if (sensor_data := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = sensor_data.native_value

    @override
    def value_changed(self, packet) -> None:
        """Update from the radio envelope of any telegram of this sender."""
        observation = packet_observation(packet)
        if observation is None:
            return
        self._apply(observation)
        self.schedule_update_ha_state()

    def _apply(self, observation: PacketObservation) -> None:
        """Store the fact this sensor exposes."""


class EnOceanRssiSensor(EnOceanRadioDiagnosticSensor):
    """Signal strength of the last telegram, as reported by the dongle."""

    _key = SENSOR_KEY_RADIO_RSSI
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    @override
    def _apply(self, observation: PacketObservation) -> None:
        if observation.rssi_dbm is not None:
            self._attr_native_value = observation.rssi_dbm


class EnOceanLastSeenSensor(EnOceanRadioDiagnosticSensor):
    """UTC time of the last telegram received from this sender."""

    _key = SENSOR_KEY_RADIO_LAST_SEEN
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @override
    def _apply(self, observation: PacketObservation) -> None:
        self._attr_native_value = datetime.now(UTC)


class EnOceanRepeaterCountSensor(EnOceanRadioDiagnosticSensor):
    """Repeater hops of the last RPS/1BS/4BS telegram (ERP1 status bits 0..3)."""

    _key = SENSOR_KEY_RADIO_REPEATER_COUNT
    _attr_state_class = SensorStateClass.MEASUREMENT

    @override
    def _apply(self, observation: PacketObservation) -> None:
        if observation.repeater_count is not None:
            self._attr_native_value = observation.repeater_count


class EnOceanSignalEnergySensor(EnOceanRadioDiagnosticSensor):
    """Remaining energy self-reported by a SIGNAL telegram (RORG 0xD0, MID 0x06).

    Only a device that implements the EnOcean "Signal Telegram" specification
    sends this, entirely on its own initiative; this integration never
    requests or triggers it (see ``signal_telegram.py``).
    """

    _key = SENSOR_KEY_SIGNAL_ENERGY
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @override
    def _apply(self, observation: PacketObservation) -> None:
        if (
            observation.signal is not None
            and observation.signal.energy_status is not None
        ):
            self._attr_native_value = observation.signal.energy_status.percent


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
    if sensor_type == SENSOR_TYPE_CONTACT:
        return [EnOceanD50001Contact(device_id, name, SENSOR_DESC_CONTACT)]
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
        self._attr_unique_id = sensor_unique_id(dev_id, description.key)

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the latest numeric or textual state."""
        await super().async_added_to_hass()
        if self._attr_native_value is not None:
            return
        if (sensor_data := await self.async_get_last_sensor_data()) is not None:
            restored = sensor_data.native_value
            # Releases before 2.10 persisted the literal "unknown" for handles
            # and contacts; an ENUM sensor may only expose an option or None.
            options = self.options
            if options is not None and restored not in options:
                restored = None
            self._attr_native_value = restored


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
    """Expose an F6-10-00 HOPPE/Ubiwizz window-handle position.

    EEP F6-10-00 defines the final handle direction in the high nibble of RPS
    DB0. The low nibble is marked don't-care by the profile and is ignored.
    """

    @override
    def value_changed(self, packet) -> None:
        """Decode the documented destination position, or expose unknown (None)."""
        if packet.rorg != RORG.RPS or len(packet.data) < 2:
            return
        position = {
            0xC0: STATE_OPEN,
            0xD0: HANDLE_STATE_TILT,
            0xE0: STATE_OPEN,
            0xF0: STATE_CLOSED,
        }.get(packet.data[1] & 0xF0)
        self._attr_native_value = position
        self.schedule_update_ha_state()


class EnOceanD50001Contact(EnOceanSensor):
    """Expose a D5-00-01 / 1BS contact as open or closed."""

    @override
    def value_changed(self, packet) -> None:
        """Decode data telegrams; ignore 1BS teach-in and unsupported values."""
        if packet.rorg != RORG.BS1 or len(packet.data) < 2:
            return
        contact_state = {0x09: STATE_CLOSED, 0x08: STATE_OPEN}.get(packet.data[1])
        if contact_state is None:
            LOGGER.debug("Ignoring unsupported D5-00-01 contact value")
            return
        self._attr_native_value = contact_state
        self.schedule_update_ha_state()


class EnOceanShutterContact(EnOceanD50001Contact):
    """Legacy ``shuttercontact`` alias for a D5-00-01 contact."""


class EnOceanA514Voltage(EnOceanSensor):
    """Expose the optional diagnostic supply voltage of A5-14-01."""

    @override
    def value_changed(self, packet) -> None:
        """Decode SVC=0..250 linearly as 0..5 V; reject reserved errors."""
        if packet.rorg != RORG.BS4 or len(packet.data) < 5:
            return
        # DB0 bit 3 is the 4BS LRN bit: on a teach-in telegram DB3 holds
        # FUNC/TYPE, not a supply voltage.
        if not packet.data[4] & 0x08:
            return
        raw_voltage = packet.data[1]
        if not 0 <= raw_voltage <= 250:
            return
        self._attr_native_value = round(raw_voltage / 50, 2)
        self.schedule_update_ha_state()

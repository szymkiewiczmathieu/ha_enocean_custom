"""Entity identity formulas shared by the platforms and the options flow.

Every ``unique_id`` a platform mints for one device row is computed here, so
the options flow can find, de-duplicate and delete exactly the registry rows
that row created. The formulas are frozen: existing installations depend on
them. This module imports nothing from Home Assistant on purpose, so every
platform and the flow can use it without import cycles.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final

from .enocean_library.utils import combine_hex

# Sensor entity description keys; each is the suffix of that entity's unique_id.
SENSOR_KEY_TEMPERATURE: Final = "temperature"
SENSOR_KEY_HUMIDITY: Final = "humidity"
SENSOR_KEY_POWER: Final = "powersensor"
SENSOR_KEY_ENERGY: Final = "energy"
SENSOR_KEY_WINDOWHANDLE: Final = "windowhandle"
SENSOR_KEY_CONTACT: Final = "d5_00_01_contact"
SENSOR_KEY_SHUTTERCONTACT: Final = "shuttercontact"
SENSOR_KEY_A514_SUPPLY_VOLTAGE: Final = "a5_14_01_supply_voltage"

# The sensor platform's default device_class, applied by both the YAML schema
# and the UI schema when the class is omitted.
SENSOR_DEFAULT_DEVICE_CLASS: Final = SENSOR_KEY_POWER

# Entity description keys the sensor platform instantiates for one
# device_class, in creation order. ``powersensor`` deliberately creates two
# entities (instantaneous power and accumulated energy).
SENSOR_KEYS_BY_DEVICE_CLASS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "temperature": (SENSOR_KEY_TEMPERATURE,),
        "humidity": (SENSOR_KEY_HUMIDITY,),
        "powersensor": (SENSOR_KEY_POWER, SENSOR_KEY_ENERGY),
        "windowhandle": (SENSOR_KEY_WINDOWHANDLE,),
        "contact": (SENSOR_KEY_CONTACT,),
        "shuttercontact": (SENSOR_KEY_SHUTTERCONTACT,),
    }
)

# A binary_sensor row whose radio-declared profile is A5-14-01 is created as a
# contact on the binary_sensor platform plus a diagnostic supply-voltage
# sensor on the sensor platform.
A514_EEP: Final = "A5-14-01"
# Suffix minted by EnOceanA514Contact. It is the only exact discriminator
# between the two binary sensor classes, since device_class is free-form user
# input for rockers.
A514_CONTACT_UNIQUE_ID_SUFFIX: Final = "-a5-14-01-contact"


# F6-05-02 smoke detectors (2.10.0). The profile is declared explicitly (never
# inferred from an RPS telegram). A binary_sensor row that declares it keeps
# its historical primary identity, so a detector added before 2.10 as a plain
# smoke binary sensor keeps its entity; the low-battery companion is new.
SMOKE_EEP: Final = "F6-05-02"
SMOKE_BATTERY_LOW_UNIQUE_ID_SUFFIX: Final = "-f6-05-02-battery-low"

# D2-14-30 multi-sensor smoke detectors (2.10.0). The binary_sensor row keeps
# its historical primary identity (smoke alarm); the companions are new.
D2_14_30_EEP: Final = "D2-14-30"
D2_14_30_BINARY_SUFFIXES: Final = ("-d2-14-30-sensor-fault", "-d2-14-30-maintenance")
D2_14_30_SENSOR_KEYS: Final = (
    "d2_14_30_temperature",
    "d2_14_30_humidity",
    "d2_14_30_energy_storage",
    "d2_14_30_comfort",
    "d2_14_30_air_quality",
    "d2_14_30_maintenance_weeks",
    "d2_14_30_remaining_life",
)

# Generic EEP.xml-driven sensor rows (2.10.0): ``device_class: eep`` plus a
# generic profile in ``radio_metadata.eep``. One entity per exposed field,
# keyed by the specification shortcut (``<id>-eep_tmp``, ``<id>-eep_hum``).
GENERIC_SENSOR_DEVICE_CLASS: Final = "eep"
GENERIC_KEY_PREFIX: Final = "eep_"

# Per-sender radio diagnostics, added in 2.10.0: one set per device rather than
# per row, disabled by default. They are not part of
# ``ui_device_entity_identities`` (which stays frozen for parity with existing
# installations); the options flow removes them with the last row of a sender.
SENSOR_KEY_RADIO_RSSI: Final = "radio_rssi"
SENSOR_KEY_RADIO_LAST_SEEN: Final = "radio_last_seen"
SENSOR_KEY_RADIO_REPEATER_COUNT: Final = "radio_repeater_count"
# MID 0x06 of a RORG 0xD0 SIGNAL telegram the sender transmits on its own
# initiative (signal_telegram.py); never requested by this integration.
SENSOR_KEY_SIGNAL_ENERGY: Final = "signal_energy"
RADIO_DIAGNOSTIC_KEYS: Final = (
    SENSOR_KEY_RADIO_RSSI,
    SENSOR_KEY_RADIO_LAST_SEEN,
    SENSOR_KEY_RADIO_REPEATER_COUNT,
    SENSOR_KEY_SIGNAL_ENERGY,
)

# Gateway (dongle) entities, added in 2.10.0. They hang off the config entry,
# not off a radio sender, so their identities are keyed by the entry id.
GATEWAY_KEY_RECEIVED_TELEGRAMS: Final = "gateway_received_telegrams"
GATEWAY_KEY_LAST_TELEGRAM: Final = "gateway_last_telegram"
GATEWAY_KEY_BASE_ID: Final = "gateway_base_id"
GATEWAY_KEY_CONNECTED: Final = "gateway_connected"
GATEWAY_KEY_REPEATER: Final = "gateway_repeater"


def gateway_device_identifier(entry_id: str) -> str:
    """Return the device registry identifier of the dongle of one config entry."""
    return f"gateway:{entry_id}"


def gateway_unique_id(entry_id: str, key: str) -> str:
    """Return the identity of one dongle diagnostic entity."""
    return f"gateway:{entry_id}:{key}"


def switch_unique_id(dev_id: list[int], channel: int) -> str:
    """Return the channel-aware switch identity used since the core integration."""
    return f"{combine_hex(dev_id)}-{channel}"


def climate_unique_id(dev_id: list[int], channel: int = 0) -> str:
    """Return the legacy-compatible climate identity (always channel 0)."""
    return f"{combine_hex(dev_id)}-{channel}"


def binary_sensor_unique_id(dev_id: list[int], device_class: object) -> str:
    """Return the rocker/contact identity.

    ``device_class`` is formatted as-is: a ``BinarySensorDeviceClass`` member
    from YAML and its plain string value from the UI render identically, and an
    absent class keeps the historical ``-None`` suffix.
    """
    return f"{combine_hex(dev_id)}-{device_class}"


def a514_contact_unique_id(dev_id: list[int]) -> str:
    """Return the identity of the automatically mapped A5-14-01 contact."""
    return f"{combine_hex(dev_id)}{A514_CONTACT_UNIQUE_ID_SUFFIX}"


def smoke_battery_low_unique_id(dev_id: list[int]) -> str:
    """Return the identity of the F6-05-02 low-battery companion."""
    return f"{combine_hex(dev_id)}{SMOKE_BATTERY_LOW_UNIQUE_ID_SUFFIX}"


def d2_14_30_identities(
    dev_id: list[int], device_class: object
) -> tuple[tuple[str, str], ...]:
    """Return the primary smoke alarm plus every D2-14-30 companion identity."""
    return (
        ("binary_sensor", binary_sensor_unique_id(dev_id, device_class)),
        *(
            ("binary_sensor", f"{combine_hex(dev_id)}{suffix}")
            for suffix in D2_14_30_BINARY_SUFFIXES
        ),
        *(("sensor", sensor_unique_id(dev_id, key)) for key in D2_14_30_SENSOR_KEYS),
    )


def light_unique_id(dev_id: list[int] | None, sender_id: list[int] | None) -> str:
    """Return the dimmer identity: the receive ID, else the sender ID."""
    return str(combine_hex(dev_id or sender_id or []))


def sensor_unique_id(dev_id: list[int], key: str) -> str:
    """Return the identity of one measurement entity (description key)."""
    return f"{combine_hex(dev_id)}-{key}"


def sender_diagnostic_identities(dev_id: list[int]) -> tuple[tuple[str, str], ...]:
    """Return every ``(platform, unique_id)`` of one sender's radio diagnostics."""
    return tuple(
        ("sensor", sensor_unique_id(dev_id, key)) for key in RADIO_DIAGNOSTIC_KEYS
    )


def rocker_event_unique_id(dev_id: list[int]) -> str:
    """Return the identity of the rocker's companion ``event`` entity (2.10.0)."""
    return f"{combine_hex(dev_id)}-rocker-event"


def d2_01_refresh_unique_id(dev_id: list[int], channel: int) -> str:
    """Return the identity of a D2-01 channel's "refresh state" button (2.11.0).

    A companion of a ``switch`` row with ``switch_type: default``, keyed by
    sender and channel like the switch itself; not part of
    ``ui_device_entity_identities`` (frozen), removed with its row.
    """
    return f"{combine_hex(dev_id)}-{channel}-d2-01-refresh"


def device_names_by_sender(rows: Any) -> dict[int, str]:
    """Return the device name of each sender: the name of its first valid row.

    Several rows may share one sender (two relay channels, a rocker that is
    also a switch). Entities that belong to the device rather than to a row
    use this stable choice instead of whichever row happens to load last.
    """
    names: dict[int, str] = {}
    for row in rows:
        names.setdefault(combine_hex(row["id"]), row["name"])
    return names


def row_names_the_device(
    row: Mapping[str, Any], names_by_sender: dict[int, str]
) -> bool:
    """Return whether this row's own name is also its sender's device name.

    Used to decide ``has_entity_name``/``name=None`` (2.10.0, Bronze
    ``has-entity-name``) without ever changing a displayed name: true for a
    row that is alone on its sender, or that happens to be the first one,
    in which case Home Assistant renders the device's own name for this
    entity -- the exact string already shown today. False for a later row
    sharing a sender with a differently named earlier one (e.g. a second
    relay channel): that row keeps its own explicit name untouched, because
    collapsing it to the shared device name would make two different rows
    show the same text and erase the distinction between them.
    """
    return names_by_sender.get(combine_hex(row["id"])) == row.get("name")


def generic_field_key(slug: str) -> str:
    """Return the description key of one generic field (``eep_<shortcut slug>``)."""
    return f"{GENERIC_KEY_PREFIX}{slug}"


def generic_eep_identities(dev_id: list[int], eep: Any) -> tuple[tuple[str, str], ...]:
    """Return every ``(platform, unique_id)`` a generic sensor row creates.

    Numeric fields become sensors, mapped enums binary sensors, in XML order.
    The catalog is imported lazily: identity formulas must stay cheap to import
    and this is the only one that needs the vendored EEP description.
    """
    from .eep_catalog import generic_profile

    profile = generic_profile(eep)
    if profile is None:
        return ((("sensor"), sensor_unique_id(dev_id, GENERIC_SENSOR_DEVICE_CLASS)),)
    return tuple(
        (
            "sensor" if field.is_value else "binary_sensor",
            sensor_unique_id(dev_id, generic_field_key(field.slug)),
        )
        for field in profile.exposed_fields
    )


def sensor_keys_for_device_class(device_class: object) -> tuple[str, ...]:
    """Return the description keys the sensor platform creates for a class."""
    if isinstance(device_class, str) and device_class in SENSOR_KEYS_BY_DEVICE_CLASS:
        return SENSOR_KEYS_BY_DEVICE_CLASS[device_class]
    return SENSOR_KEYS_BY_DEVICE_CLASS[SENSOR_DEFAULT_DEVICE_CLASS]


def ui_device_entity_identities(
    device: Mapping[str, Any],
) -> tuple[tuple[str, str], ...]:
    """Return every ``(platform, unique_id)`` the platforms create for one row.

    The first pair is the entity on the row's own platform; later pairs are
    companion entities created on other platforms (A5-14-01 supply voltage,
    the energy counter of a power sensor).
    """
    dev_id = device["id"]
    platform = device["platform"]
    if platform == "switch":
        return ((platform, switch_unique_id(dev_id, device["channel"])),)
    if platform == "climate":
        return ((platform, climate_unique_id(dev_id)),)
    if platform == "light":
        return ((platform, light_unique_id(dev_id, device.get("sender_id"))),)
    if platform == "binary_sensor":
        metadata = device.get("radio_metadata") or {}
        if metadata.get("eep") == A514_EEP:
            return (
                (platform, a514_contact_unique_id(dev_id)),
                ("sensor", sensor_unique_id(dev_id, SENSOR_KEY_A514_SUPPLY_VOLTAGE)),
            )
        if metadata.get("eep") == D2_14_30_EEP:
            return d2_14_30_identities(dev_id, device["device_class"])
        primary = (platform, binary_sensor_unique_id(dev_id, device["device_class"]))
        if metadata.get("eep") == SMOKE_EEP:
            return (primary, (platform, smoke_battery_low_unique_id(dev_id)))
        return (primary,)
    if platform == "sensor":
        if device.get("device_class") == GENERIC_SENSOR_DEVICE_CLASS:
            metadata = device.get("radio_metadata") or {}
            return generic_eep_identities(dev_id, metadata.get("eep"))
        return tuple(
            (platform, sensor_unique_id(dev_id, key))
            for key in sensor_keys_for_device_class(device.get("device_class"))
        )
    return ((platform, str(combine_hex(dev_id))),)

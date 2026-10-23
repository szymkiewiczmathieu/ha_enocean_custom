"""Generic sensor profiles derived from the vendored ``EEP.xml``.

The XML already describes the bit layout, range and scale of every field of
the 4BS (A5) sensor profiles and of D2-14-41. This module turns those
descriptions into immutable Python objects once, and decodes telegrams with
plain integer arithmetic: no field semantics are invented, only the shortcut
names of the specification are mapped to Home Assistant device classes.

Boundaries, on purpose:

* profiles that already have a dedicated decoder (A5-12-01 metering, A5-14-01
  contact), bidirectional actuators (A5-20-01) and command telegrams
  (A5-38-08) are excluded;
* a command-based profile (A5-13-01) exposes its first command only, and a
  telegram is decoded only when its command field matches;
* the ``EEP.xml`` dates from EEP 2.6.x; every profile stays "generic" until a
  real frame of the owner's product has been checked against it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Final

from .enocean_library.protocol.constants import RORG
from .enocean_library.protocol.packet import Packet

# Profiles described in EEP.xml that must never be decoded generically.
EXCLUDED_PROFILES: Final = frozenset({"A5-12-01", "A5-14-01", "A5-20-01", "A5-38-08"})
GENERIC_RORGS: Final = (int(RORG.BS4), int(RORG.VLD))
# VLD profiles are actuators except the multi-function sensors of FUNC 0x14.
GENERIC_VLD_FUNCS: Final = frozenset({0x14})

# The sensor row device_class that selects the generic decoder.
GENERIC_SENSOR_DEVICE_CLASS: Final = "eep"


@dataclass(frozen=True, slots=True)
class FieldSemantics:
    """How one XML shortcut maps to a Home Assistant entity."""

    translation_key: str
    device_class: str | None = None
    unit: str | None = None
    diagnostic: bool = False
    enabled_default: bool = True
    # Binary fields: the raw values that mean "on".
    on_values: frozenset[int] = frozenset()


# Numeric <value> fields. Units are the Home Assistant constant strings.
VALUE_SEMANTICS: Final[Mapping[str, FieldSemantics]] = MappingProxyType(
    {
        "TMP": FieldSemantics("eep_temperature", "temperature", "°C"),
        "ASP": FieldSemantics("eep_actual_setpoint", "temperature", "°C"),
        "HUM": FieldSemantics("eep_humidity", "humidity", "%"),
        "ILL": FieldSemantics("eep_illuminance", "illuminance", "lx"),
        "ILL1": FieldSemantics("eep_illuminance_range_1", "illuminance", "lx"),
        "ILL2": FieldSemantics("eep_illuminance_range_2", "illuminance", "lx"),
        "DWS": FieldSemantics("eep_dawn", "illuminance", "lx"),
        "SVC": FieldSemantics(
            "eep_supply_voltage", "voltage", "V", diagnostic=True, enabled_default=False
        ),
        "WND": FieldSemantics("eep_wind_speed", "wind_speed", "m/s"),
        "SP": FieldSemantics("eep_setpoint", None, None),
        "CVAR": FieldSemantics("eep_controller_output", None, "%"),
        "BSP": FieldSemantics("eep_blind_position", None, "%"),
        "AN": FieldSemantics("eep_blind_angle", None, "°"),
        "ACX": FieldSemantics("eep_acceleration_x", None, "g", enabled_default=False),
        "ACY": FieldSemantics("eep_acceleration_y", None, "g", enabled_default=False),
        "ACZ": FieldSemantics("eep_acceleration_z", None, "g", enabled_default=False),
    }
)

# Concentration depends on the unit the profile declares.
_CONCENTRATION_SEMANTICS: Final[Mapping[str, FieldSemantics]] = MappingProxyType(
    {
        "ppm": FieldSemantics("eep_co2", "carbon_dioxide", "ppm"),
        "ppb": FieldSemantics("eep_voc", "volatile_organic_compounds_parts", "ppb"),
    }
)

# <enum> fields exposed as binary sensors. Everything else is left alone:
# momentary buttons and configuration flags are not states worth an entity.
BINARY_SEMANTICS: Final[Mapping[str, FieldSemantics]] = MappingProxyType(
    {
        "PIR": FieldSemantics("eep_occupancy", "occupancy", on_values=frozenset({1})),
        "PIRS": FieldSemantics("eep_occupancy", "occupancy", on_values=frozenset({0})),
        "CO": FieldSemantics("eep_contact", "opening", on_values=frozenset({0})),
        "RAN": FieldSemantics("eep_rain", "moisture", on_values=frozenset({1})),
    }
)

_UNIT_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {"C": "°C", "degrees": "°", "klx": "klx"}
)


@dataclass(frozen=True, slots=True)
class EepField:
    """One <value> or <enum> element of a profile."""

    shortcut: str
    slug: str
    description: str
    kind: str
    offset: int
    size: int
    unit: str
    range_min: float = 0.0
    range_max: float = 0.0
    scale_min: float = 0.0
    scale_max: float = 0.0

    @property
    def is_value(self) -> bool:
        """Return whether the field is numeric."""
        return self.kind == "value"

    @property
    def semantics(self) -> FieldSemantics | None:
        """Return the Home Assistant mapping, or None for an unexposed enum."""
        if self.is_value:
            if self.shortcut == "Conc":
                return _CONCENTRATION_SEMANTICS.get(self.unit)
            return VALUE_SEMANTICS.get(self.shortcut)
        return BINARY_SEMANTICS.get(self.shortcut)

    @property
    def exposed(self) -> bool:
        """Return whether an entity is created for this field."""
        return self.is_value or self.semantics is not None

    @property
    def normalized_unit(self) -> str | None:
        """Return the unit Home Assistant should display, if any.

        A mapped shortcut uses the unit of its semantics (a raw 0..255 set
        point has none, whatever the XML says); an unmapped numeric field keeps
        the XML unit, normalized for the few spellings the file uses.
        """
        semantics = self.semantics
        if semantics is not None:
            return semantics.unit
        if not self.unit:
            return None
        return _UNIT_ALIASES.get(self.unit, self.unit)

    def raw(self, payload_bits: int, payload: int, payload_size: int) -> int:
        """Extract the raw field value from the payload (MSB first)."""
        shift = payload_size - self.offset - self.size
        return (payload >> shift) & ((1 << self.size) - 1)

    def scaled(self, raw: int) -> float | None:
        """Scale a raw value with the XML range/scale, or None when out of range."""
        low, high = sorted((self.range_min, self.range_max))
        if not low <= raw <= high:
            return None
        if self.range_max == self.range_min:
            return self.scale_min
        value = (self.scale_max - self.scale_min) / (
            self.range_max - self.range_min
        ) * (raw - self.range_min) + self.scale_min
        return round(value, 3)


@dataclass(frozen=True, slots=True)
class EepProfile:
    """One decodable generic profile."""

    eep: str
    rorg: int
    func: int
    type: int
    description: str
    fields: tuple[EepField, ...]
    command: int | None = None
    command_offset: int = 0
    command_size: int = 0

    @property
    def exposed_fields(self) -> tuple[EepField, ...]:
        """Return the fields that become entities, in XML order."""
        return tuple(field for field in self.fields if field.exposed)

    @property
    def payload_bits(self) -> int:
        """Return how many payload bits the fields and the command need."""
        bits = max((field.offset + field.size for field in self.fields), default=0)
        if self.command is not None:
            bits = max(bits, self.command_offset + self.command_size)
        return bits

    def decode(self, data: Any) -> dict[str, int | float] | None:
        """Decode the ERP1 data bytes into ``{slug: value}``.

        Numeric fields outside their documented range are omitted; the whole
        telegram is rejected when it is too short or carries another command.
        """
        if not isinstance(data, (list, tuple)) or len(data) < 6 or data[0] != self.rorg:
            return None
        if any(
            isinstance(byte, bool) or not isinstance(byte, int) or not 0 <= byte <= 0xFF
            for byte in data
        ):
            return None
        payload = bytes(data[1:-5])
        payload_size = len(payload) * 8
        if payload_size < self.payload_bits:
            return None
        # 4BS DB0 bit 3 is the LRN bit: clear on a teach-in telegram, whose
        # data bytes carry FUNC/TYPE/manufacturer rather than a measurement.
        if self.rorg == int(RORG.BS4) and not payload[-1] & 0x08:
            return None
        bits = int.from_bytes(payload, "big")
        if self.command is not None:
            shift = payload_size - self.command_offset - self.command_size
            if (bits >> shift) & ((1 << self.command_size) - 1) != self.command:
                return None
        decoded: dict[str, int | float] = {}
        for field in self.fields:
            if not field.exposed:
                continue
            raw = field.raw(self.payload_bits, bits, payload_size)
            if field.is_value:
                scaled = field.scaled(raw)
                if scaled is not None:
                    decoded[field.slug] = scaled
            else:
                decoded[field.slug] = raw
        return decoded


def slugify_shortcut(shortcut: str) -> str:
    """Return the entity key suffix of a shortcut (``D/N`` -> ``d_n``)."""
    return "".join(char.lower() if char.isalnum() else "_" for char in shortcut)


def _float(node: Any) -> float | None:
    try:
        return float(node.text.strip())
    except (AttributeError, TypeError, ValueError):
        return None


def _field(element: Any) -> EepField | None:
    """Translate one <value>/<enum> element, or None when it is unusable."""
    shortcut = element.get("shortcut")
    if not isinstance(shortcut, str) or not shortcut:
        return None
    try:
        offset = int(element["offset"])
        size = int(element["size"])
    except (KeyError, TypeError, ValueError):
        return None
    if offset < 0 or size <= 0:
        return None
    description = element.get("description") or shortcut
    unit = element.get("unit") or ""
    slug = slugify_shortcut(shortcut)
    if element.name == "enum":
        return EepField(shortcut, slug, description, "enum", offset, size, unit)
    rng = element.find("range")
    scl = element.find("scale")
    values = [
        _float(rng.find("min")) if rng else None,
        _float(rng.find("max")) if rng else None,
        _float(scl.find("min")) if scl else None,
        _float(scl.find("max")) if scl else None,
    ]
    if any(value is None for value in values):
        return None
    range_min, range_max, scale_min, scale_max = values  # type: ignore[misc]
    return EepField(
        shortcut,
        slug,
        description,
        "value",
        offset,
        size,
        unit,
        range_min,
        range_max,
        scale_min,
        scale_max,
    )


def _profile(rorg: int, func: int, type_: int, node: Any) -> EepProfile | None:
    """Build a generic profile from its XML node, or None when excluded."""
    eep = f"{rorg:02X}-{func:02X}-{type_:02X}"
    if eep in EXCLUDED_PROFILES:
        return None
    if rorg == int(RORG.VLD) and func not in GENERIC_VLD_FUNCS:
        return None
    datas = node.find_all("data", recursive=False)
    if not datas or any(data.get("direction") for data in datas):
        return None
    command = None
    command_offset = command_size = 0
    data = datas[0]
    command_node = node.find("command", recursive=False)
    if command_node is not None:
        try:
            command_offset = int(command_node["offset"])
            command_size = int(command_node["size"])
        except (KeyError, TypeError, ValueError):
            return None
        first = command_node.find("item")
        try:
            command = int(first["value"]) if first is not None else None
        except (KeyError, TypeError, ValueError):
            return None
        if command is None:
            return None
        data = next(
            (item for item in datas if item.get("command") == str(command)), None
        )
        if data is None:
            return None
    fields = tuple(
        field
        for element in data.children
        if getattr(element, "name", None) in ("value", "enum")
        and (field := _field(element)) is not None
    )
    profile = EepProfile(
        eep,
        rorg,
        func,
        type_,
        node.get("description") or eep,
        fields,
        command,
        command_offset,
        command_size,
    )
    if not profile.exposed_fields:
        return None
    return profile


@lru_cache(maxsize=1)
def generic_profiles() -> Mapping[str, EepProfile]:
    """Return every generic profile, keyed by canonical ``XX-XX-XX``."""
    telegrams = Packet.eep.telegrams
    profiles: dict[str, EepProfile] = {}
    for rorg in GENERIC_RORGS:
        for func, types in sorted(telegrams.get(rorg, {}).items()):
            for type_, node in sorted(types.items()):
                profile = _profile(rorg, func, type_, node)
                if profile is not None:
                    profiles[profile.eep] = profile
    return MappingProxyType(profiles)


def generic_profile(eep: Any) -> EepProfile | None:
    """Return the generic profile for an EEP string, if it has one."""
    if not isinstance(eep, str):
        return None
    return generic_profiles().get(eep.upper())


def is_generic_supported(eep: Any) -> bool:
    """Return whether the generic decoder covers this EEP."""
    return generic_profile(eep) is not None

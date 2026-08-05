"""Local, privacy-preserving EnOcean device intelligence.

This module deliberately contains only immutable models and pure functions.
It never performs network lookups and never retains teach-in or QR payloads.

Three axes are kept strictly separate and must never be conflated:

* evidence -- how a profile assertion was obtained (:class:`Evidence`);
* configuration mode -- what a human still has to decide (:class:`ConfigMode`);
* support -- what this repository can actually do (:class:`SupportVerdict`).

An EURID identifies a radio sender, not a model. An EEP identifies a data
profile, not a product, and an EEP known to the EnOcean Alliance catalog is not
necessarily decodable here. Only an exactly cataloged Product ID may yield a
manufacturer/model string, and no entry in this module certifies hardware.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from .enocean_library.protocol.constants import RORG


class Evidence(StrEnum):
    """How an identity/profile assertion was obtained."""

    EXACT = "exact"
    ASSISTED = "assisted"
    MANUAL = "manual"
    PROFILE_UNKNOWN = "profile_unknown"


class ConfigMode(StrEnum):
    """What the operator still has to decide for an implemented profile."""

    AUTOMATIC = "automatic"
    ASSISTED = "assisted"
    MANUAL = "manual"
    YAML_ONLY = "yaml_only"


class SupportVerdict(StrEnum):
    """Implementation/configuration status, independent of catalog knowledge."""

    SUPPORTED = "supported"
    MANUAL = "manual"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RadioIdentity:
    """Safe identity extracted from one radio telegram."""

    sender_id: tuple[int, int, int, int]
    eep: str | None = None
    manufacturer_id: int | None = None
    channel: int | None = None
    evidence: Evidence = Evidence.PROFILE_UNKNOWN
    eep_source: str | None = None


@dataclass(frozen=True, slots=True)
class CommissioningIdentity:
    """Safe identity extracted from an Alliance commissioning label."""

    sender_id: tuple[int, int, int, int]
    product_id: str | None = None
    manufacturer_id: int | None = None
    product_reference: int | None = None
    evidence: Evidence = Evidence.ASSISTED


@dataclass(frozen=True, slots=True)
class EEPImplementation:
    """One EEP for which this repository ships code.

    ``platforms`` lists the Home Assistant platforms the repository can build
    from this profile. ``mode`` states what the operator must still decide; the
    recommendation and the support verdict are derived from it so the published
    matrix and the runtime behaviour cannot drift apart.
    """

    platforms: tuple[str, ...]
    mode: ConfigMode

    @property
    def recommended_platform(self) -> str | None:
        """Return a platform only when the mapping leaves no room for choice."""
        if self.mode in (ConfigMode.MANUAL, ConfigMode.YAML_ONLY):
            return None
        return self.platforms[0] if len(self.platforms) == 1 else None

    @property
    def support(self) -> SupportVerdict:
        """Report implementation status, never hardware certification."""
        if self.mode in (ConfigMode.MANUAL, ConfigMode.YAML_ONLY):
            return SupportVerdict.MANUAL
        return SupportVerdict.SUPPORTED


@dataclass(frozen=True, slots=True)
class ProductDefinition:
    """Product identity confirmed by an official EnOcean Alliance DDF."""

    manufacturer: str
    model: str
    model_id: str
    eep: str | None
    source: str


# Auditable implementation matrix. Every entry maps to code that exists in this
# repository; it describes neither EnOcean Alliance certification nor every EEP
# known to the standard. Keep this table and docs/device-intelligence.md in sync.
EEP_IMPLEMENTATIONS: Mapping[str, EEPImplementation] = MappingProxyType(
    {
        # binary_sensor.EnOceanBinarySensor decodes the rocker; switch.py also
        # simulates one from the same profile, so the platform is ambiguous.
        "F6-02-01": EEPImplementation(("binary_sensor", "switch"), ConfigMode.MANUAL),
        "F6-02-02": EEPImplementation(("binary_sensor", "switch"), ConfigMode.MANUAL),
        # sensor.EnOceanWindowHandle; only the sensor device_class must be set.
        "F6-10-00": EEPImplementation(("sensor",), ConfigMode.ASSISTED),
        # sensor.EnOceanShutterContact.
        "D5-00-01": EEPImplementation(("sensor",), ConfigMode.ASSISTED),
        # sensor.EnOceanTemperatureSensor is a generic linear 8-bit A5 decoder
        # whose scale and raw range must be supplied, so it is never automatic.
        "A5-10-06": EEPImplementation(("sensor",), ConfigMode.MANUAL),
        # sensor.EnOceanPowerSensor/EnOceanEnergySensor via parse_eep(0x12, 0x01).
        "A5-12-01": EEPImplementation(("sensor",), ConfigMode.ASSISTED),
        # climate.EnOceanClimate; the options flow only persists SRC-D08 today.
        "A5-20-04": EEPImplementation(("climate",), ConfigMode.YAML_ONLY),
        # light.EnOceanLight transmits only; a sender identity must be chosen.
        "A5-38-08": EEPImplementation(("light",), ConfigMode.ASSISTED),
        # sensor._decode_d2_measurement handles D2-01 CMD 0x7 reports.
        "D2-01-0B": EEPImplementation(("sensor",), ConfigMode.ASSISTED),
        # D2-01-12 actuators are valid as either a light or a switch.
        "D2-01-12": EEPImplementation(("light", "switch"), ConfigMode.MANUAL),
    }
)

# Provenance of every PRODUCT_CATALOG entry below. The official repository is a
# validation source, not a world catalog: the 2026-08-05 audit found seven XML
# files in total, so anything absent here must stay unknown.
DDF_SOURCE = "EnOcean-Alliance/enocean-alliance-ddf (audited 2026-08-05)"

# Product IDs confirmed by an official DDF. Entries identify hardware; they do
# not promise that this repository can decode it -- see support_for().
PRODUCT_CATALOG: Mapping[str, ProductDefinition] = MappingProxyType(
    {
        "002D00000004": ProductDefinition(
            "Afriso", "Cositherm 2-Channel", "002D00000004", "D2-34-10", DDF_SOURCE
        ),
        "002D0000000A": ProductDefinition(
            "Afriso", "Cositherm 2-Channel", "002D0000000A", "D2-34-10", DDF_SOURCE
        ),
        "002D00000005": ProductDefinition(
            "Afriso", "Cositherm 6-Channel", "002D00000005", "D2-34-10", DDF_SOURCE
        ),
        "002D0000000B": ProductDefinition(
            "Afriso", "Cositherm 6-Channel", "002D0000000B", "D2-34-10", DDF_SOURCE
        ),
        "001600013045": ProductDefinition(
            "BSC Computer",
            "eTronic window/door contact",
            "001600013045",
            "A5-14-01",
            DDF_SOURCE,
        ),
    }
)

# Fields safe to persist in the config entry options and to publish in events.
# Manufacturer/model strings are deliberately absent: they are resolved from
# PRODUCT_CATALOG at runtime so a hand-edited .storage cannot forge them.
PERSISTED_METADATA_FIELDS = frozenset(
    {
        "sender_id",
        "eep",
        "eep_source",
        "manufacturer_id",
        "product_id",
        "product_reference",
        "evidence",
        "support",
        "manufacturer_conflict",
    }
)

# The manufacturer field of a Product ID is 11 bits wide inside the first two
# bytes. The remaining bits are reserved and must never be masked away.
MAX_MANUFACTURER_ID = 0x7FF

UNKNOWN_PLACEHOLDER = "—"

_EVIDENCE_TOKENS = frozenset(member.value for member in Evidence)
_SUPPORT_TOKENS = frozenset(member.value for member in SupportVerdict)

_TYPED_ID = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){3}$")
_HEX_12 = re.compile(r"^[0-9A-Fa-f]{12}$")
_LABEL = re.compile(r"^[0-9A-Za-z+]+$")
_EEP = re.compile(r"^[0-9A-Fa-f]{2}-[0-9A-Fa-f]{2}-[0-9A-Fa-f]{2}$")

# Sources that constitute a profile *proof* rather than an operator claim. Any
# other source, including a missing one on a hand-edited row, is treated as
# declared too: refusing to overwrite is always the conservative direction.
MANUAL_EEP_SOURCE = "manual"


def format_eep(rorg: Any, func: Any, type_: Any) -> str | None:
    """Return canonical ``XX-XX-XX`` or None for invalid components."""
    values: list[int] = []
    for value in (rorg, func, type_):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= 255
        ):
            return None
        values.append(value)
    return "-".join(f"{value:02X}" for value in values)


def parse_manual_eep(value: Any) -> str | None:
    """Canonicalize an operator-typed EEP, or return None when it is not one.

    Only the strict ``XX-XX-XX`` hexadecimal form is accepted. Lowercase input
    is normalized to uppercase so a manual assertion is indistinguishable in
    shape from a declared one; everything else -- booleans, non-strings, short
    or over-long forms -- is rejected instead of being coerced.
    """
    if isinstance(value, bool) or not isinstance(value, str):
        return None
    candidate = value.strip()
    if not _EEP.fullmatch(candidate):
        return None
    return candidate.upper()


def metadata_declares_eep(metadata: Mapping[str, Any] | None) -> bool:
    """Return whether metadata already carries a radio- or product-declared EEP.

    Anything but an explicit ``manual`` source counts as declared, so neither a
    corrupt persisted row nor a forged submission can turn proof back into a
    claim the operator is then invited to overwrite.
    """
    if not isinstance(metadata, Mapping):
        return False
    eep = metadata.get("eep")
    if not isinstance(eep, str) or not eep:
        return False
    return metadata.get("eep_source") != MANUAL_EEP_SOURCE


def apply_manual_eep(
    metadata: Mapping[str, Any] | None, manual_eep: Any
) -> dict[str, Any]:
    """Return safe metadata with an operator EEP assertion applied.

    The input is filtered down to :data:`PERSISTED_METADATA_FIELDS`, so every
    safe QR/radio field survives while no raw payload or security container can
    re-enter. The assertion is refused outright when the metadata already
    declares a profile, and it never upgrades a manufacturer conflict: an
    ambiguous identity stays ``unknown``.
    """
    result = {
        key: value
        for key, value in (metadata.items() if isinstance(metadata, Mapping) else ())
        if key in PERSISTED_METADATA_FIELDS
    }
    eep = parse_manual_eep(manual_eep)
    if eep is None or metadata_declares_eep(result):
        return result
    result["eep"] = eep
    result["eep_source"] = MANUAL_EEP_SOURCE
    result["evidence"] = Evidence.MANUAL.value
    result["support"] = (
        SupportVerdict.UNKNOWN.value
        if result.get("manufacturer_conflict")
        else support_for(eep, manual=True).value
    )
    return result


def _sender_tuple(sender: Any) -> tuple[int, int, int, int] | None:
    if (
        isinstance(sender, bool)
        or not isinstance(sender, int)
        or not 0 <= sender <= 0xFFFFFFFF
    ):
        return None
    return tuple((sender >> shift) & 0xFF for shift in (24, 16, 8, 0))  # type: ignore[return-value]


def _bounded_int(value: Any, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= maximum else None


def _token(value: Any, allowed: frozenset[str], fallback: StrEnum) -> str:
    """Return a known token, falling back for any corrupt persisted value."""
    return value if isinstance(value, str) and value in allowed else fallback.value


def _safe_attr(value: Any, name: str, default: Any = None) -> Any:
    """Read an attribute from an untrusted packet without propagating errors."""
    try:
        return getattr(value, name, default)
    except Exception:  # noqa: BLE001 - packet properties are an untrusted boundary
        return default


def extract_radio_identity(packet: Any) -> RadioIdentity | None:
    """Extract safe radio identity without raising on malformed packets."""
    sender = _sender_tuple(_safe_attr(packet, "sender_int"))
    if sender is None:
        return None

    rorg = _safe_attr(packet, "rorg")
    eep = None
    manufacturer = None
    channel = None
    # UTE exposes rorg_of_eep; enriched 4BS exposes contains_eep and rorg.
    # Ordinary RPS F6 and 1BS D5 telegrams carry no profile and stay unknown.
    ute_rorg = _safe_attr(packet, "rorg_of_eep")
    if isinstance(ute_rorg, int) and ute_rorg not in (0, int(RORG.UNDEFINED)):
        eep = format_eep(
            ute_rorg,
            _safe_attr(packet, "rorg_func", _safe_attr(packet, "func")),
            _safe_attr(packet, "rorg_type", _safe_attr(packet, "type")),
        )
        channel = _safe_attr(packet, "channel")
    elif rorg == RORG.BS4 and _safe_attr(packet, "contains_eep", False) is True:
        eep = format_eep(
            rorg, _safe_attr(packet, "rorg_func"), _safe_attr(packet, "rorg_type")
        )
    if eep is not None:
        manufacturer = _bounded_int(
            _safe_attr(packet, "rorg_manufacturer", _safe_attr(packet, "manufacturer")),
            MAX_MANUFACTURER_ID,
        )
        channel = _bounded_int(channel, 0xFF)
    return RadioIdentity(
        sender_id=sender,
        eep=eep,
        manufacturer_id=manufacturer,
        channel=channel,
        evidence=Evidence.EXACT if eep else Evidence.PROFILE_UNKNOWN,
        eep_source="radio_declared" if eep else None,
    )


def parse_commissioning_identity(value: Any) -> CommissioningIdentity | None:
    """Parse strict typed EURID or Alliance 30S+1P identity, discarding containers.

    Security containers (10Z/11Z/13Z) are dropped without ever being copied into
    the returned model. A genuine 48-bit EURID and a Product ID whose reserved
    manufacturer bits are set are rejected rather than truncated or masked.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if _TYPED_ID.fullmatch(value):
        return CommissioningIdentity(tuple(int(part, 16) for part in value.split(":")))  # type: ignore[arg-type]
    if not _LABEL.fullmatch(value):
        return None
    containers = value.split("+")
    eurids = [item[3:] for item in containers if item.startswith("30S")]
    products = [item[2:] for item in containers if item.startswith("1P")]
    if (
        len(eurids) != 1
        or len(products) != 1
        or not _HEX_12.fullmatch(eurids[0])
        or not _HEX_12.fullmatch(products[0])
        or eurids[0][:4] != "0000"
    ):
        return None
    eurid = bytes.fromhex(eurids[0][4:])
    product = bytes.fromhex(products[0])
    manufacturer = int.from_bytes(product[:2], "big")
    if manufacturer > MAX_MANUFACTURER_ID:
        return None
    return CommissioningIdentity(
        sender_id=tuple(eurid),  # type: ignore[arg-type]
        product_id=product.hex().upper(),
        manufacturer_id=manufacturer,
        product_reference=int.from_bytes(product[2:], "big"),
    )


def implementation_for(eep: Any) -> EEPImplementation | None:
    """Return the repository implementation for an EEP, if any."""
    if not isinstance(eep, str):
        return None
    return EEP_IMPLEMENTATIONS.get(eep)


def support_for(eep: str | None, *, manual: bool = False) -> SupportVerdict:
    """Return support status without conflating catalog knowledge and decoding."""
    implementation = implementation_for(eep)
    if implementation is None:
        return SupportVerdict.UNSUPPORTED if eep else SupportVerdict.UNKNOWN
    return SupportVerdict.MANUAL if manual else implementation.support


def resolve_product(metadata: Mapping[str, Any] | None) -> ProductDefinition | None:
    """Resolve the official product from a Product ID, never from persisted text.

    A manufacturer conflict between the label and the radio suppresses the
    lookup: an ambiguous identity must not produce a model claim.
    """
    if not isinstance(metadata, Mapping) or metadata.get("manufacturer_conflict"):
        return None
    product_id = metadata.get("product_id")
    if not isinstance(product_id, str):
        return None
    return PRODUCT_CATALOG.get(product_id.upper())


def safe_metadata(
    radio: RadioIdentity | None = None,
    commissioning: CommissioningIdentity | None = None,
    *,
    manual_eep: str | None = None,
) -> dict[str, Any]:
    """Build the bounded persistence/event representation; no raw input survives."""
    sender = (
        commissioning.sender_id if commissioning else radio.sender_id if radio else None
    )
    product_id = commissioning.product_id if commissioning else None
    product = PRODUCT_CATALOG.get(product_id or "")
    eep = (
        manual_eep
        or (radio.eep if radio else None)
        or (product.eep if product else None)
    )
    evidence = (
        Evidence.MANUAL
        if manual_eep
        else commissioning.evidence
        if commissioning
        else radio.evidence
        if radio
        else Evidence.PROFILE_UNKNOWN
    )
    manufacturer = (
        commissioning.manufacturer_id
        if commissioning
        else radio.manufacturer_id
        if radio
        else None
    )
    conflict = bool(
        commissioning
        and radio
        and commissioning.manufacturer_id is not None
        and radio.manufacturer_id is not None
        and commissioning.manufacturer_id != radio.manufacturer_id
    )
    result: dict[str, Any] = {
        "evidence": evidence.value,
        "support": support_for(eep, manual=manual_eep is not None).value,
    }
    if sender is not None:
        result["sender_id"] = list(sender)
    if eep:
        result["eep"] = eep
        result["eep_source"] = (
            "manual"
            if manual_eep
            else radio.eep_source
            if radio and radio.eep
            else "product_declared"
        )
    if manufacturer is not None:
        result["manufacturer_id"] = manufacturer
    if product_id:
        result["product_id"] = product_id
        result["product_reference"] = commissioning.product_reference
    if conflict:
        result["manufacturer_conflict"] = True
        result["support"] = SupportVerdict.UNKNOWN.value
    return result


def recommended_platform(metadata: Mapping[str, Any] | None) -> str | None:
    """Return only an unambiguous implementation recommendation.

    An operator assertion is a claim, never a proof, so a manually entered EEP
    preselects nothing: the platform stays an explicit human decision.
    """
    if not isinstance(metadata, Mapping) or metadata.get("manufacturer_conflict"):
        return None
    if (
        metadata.get("eep_source") == MANUAL_EEP_SOURCE
        or metadata.get("evidence") == Evidence.MANUAL.value
    ):
        return None
    implementation = implementation_for(metadata.get("eep"))
    return implementation.recommended_platform if implementation else None


def reconcile_commissioning_metadata(
    commissioning: CommissioningIdentity,
    radio_metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Combine matching safe QR/radio evidence and flag manufacturer conflict."""
    result = safe_metadata(commissioning=commissioning)
    if not isinstance(radio_metadata, Mapping) or radio_metadata.get(
        "sender_id"
    ) != list(commissioning.sender_id):
        return result
    eep = radio_metadata.get("eep")
    if isinstance(eep, str):
        result["eep"] = eep
        result["eep_source"] = radio_metadata.get("eep_source", "radio_declared")
        result["support"] = support_for(eep).value
        result["evidence"] = Evidence.EXACT.value
    radio_manufacturer = radio_metadata.get("manufacturer_id")
    if (
        isinstance(radio_manufacturer, int)
        and not isinstance(radio_manufacturer, bool)
        and commissioning.manufacturer_id is not None
        and radio_manufacturer != commissioning.manufacturer_id
    ):
        result["manufacturer_conflict"] = True
        result["support"] = SupportVerdict.UNKNOWN.value
    return result


def flow_placeholders(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    """Return language-neutral placeholders for translated flow descriptions.

    Every value is a hex identifier, a proper noun or a stable machine token, so
    the same values render correctly under any translation; the surrounding
    labels live in strings.json and translations/*.json.
    """
    data = metadata if isinstance(metadata, Mapping) else {}
    product = resolve_product(data)
    manufacturer_id = _bounded_int(data.get("manufacturer_id"), MAX_MANUFACTURER_ID)
    eep = data.get("eep")
    evidence = data.get("evidence")
    support = data.get("support")
    return {
        "radio_eep": eep if isinstance(eep, str) else UNKNOWN_PLACEHOLDER,
        "radio_manufacturer": product.manufacturer if product else UNKNOWN_PLACEHOLDER,
        "radio_manufacturer_id": (
            f"{manufacturer_id:03X}"
            if manufacturer_id is not None
            else UNKNOWN_PLACEHOLDER
        ),
        "radio_model": product.model if product else UNKNOWN_PLACEHOLDER,
        "radio_evidence": _token(evidence, _EVIDENCE_TOKENS, Evidence.PROFILE_UNKNOWN),
        "radio_support": _token(support, _SUPPORT_TOKENS, SupportVerdict.UNKNOWN),
        "radio_conflict": (
            "manufacturer_conflict"
            if data.get("manufacturer_conflict")
            else UNKNOWN_PLACEHOLDER
        ),
    }

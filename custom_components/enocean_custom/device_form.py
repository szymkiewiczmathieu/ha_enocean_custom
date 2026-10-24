"""Device forms shared by the options flow and the device subentry flow.

The guided options flow (learn, QR code, pairing...) and the *Add device*
subentry flow both end on the same two questions: which platform and name,
then the platform-specific details. The schema builders and the validation of
those details live here, with no flow state of their own, so both flows
produce byte-identical rows and refuse exactly the same inputs. Every
function is pure: it reads what it is given and never touches Home Assistant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from math import isfinite
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import CONF_DEVICE_CLASS, CONF_NAME, CONF_PLATFORM
from homeassistant.helpers import selector

from .climate import (
    CONF_COMMAND_FREQUENCY,
    CONF_DEVICE_TYPE,
    CONF_PI_CONTROL_KP,
    CONF_PI_CONTROL_TN,
    CONF_SENDER_ID_SWITCH,
    CONF_SENSOR_ENTITY_ID,
    CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION,
    CONF_SENSOR_TARGET_TEMP_RANGE,
    CONF_SENSOR_TARGET_TEMP_TOLERANCE,
    CONF_TARGET_TEMP_BASE,
    CONF_TARGET_TEMP_NIGHT_REDUCTION,
)
from .const import DOMAIN, UI_DEVICE_PLATFORMS
from .device_intelligence import (
    apply_manual_eep,
    metadata_declares_eep,
    parse_commissioning_identity,
    parse_manual_eep,
    recommended_platform,
)
from .eep_catalog import generic_profiles, is_generic_supported
from .identity import GENERIC_SENSOR_DEVICE_CLASS, ui_device_entity_identities
from .light import CONF_SENDER_ID
from .schema import CONF_RADIO_METADATA, UI_CLIMATE_DEVICE_TYPES, UI_DEVICE_SCHEMA
from .sensor import (
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_RANGE_FROM,
    CONF_RANGE_TO,
    SENSOR_TYPES,
)
from .switch import CONF_CHANNEL, CONF_SWITCH_TYPE, SWITCH_TYPES

CONF_MANUAL_EEP = "manual_eep"
CONF_GENERIC_EEP = "eep"
CONF_QR_CODE = "qr_code"

_HEX_BYTE = re.compile(r"^[0-9A-Fa-f]{2}$")


def parse_sender_id(value: Any) -> list[int] | None:
    """Parse a sender id typed as 'AA:BB:CC:DD' (or space separated) hex bytes."""
    if not isinstance(value, str):
        return None
    parts = re.split(r"[: ]+", value.strip())
    if len(parts) != 4 or not all(_HEX_BYTE.match(part) for part in parts):
        return None
    return [int(part, 16) for part in parts]


def exact_int(value: Any) -> int | None:
    """Return value as an exact integer, or None when it is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def finite_float(value: Any) -> float | None:
    """Return a finite float, or None when conversion is unsafe."""
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


def parse_commissioning_code(value: Any) -> list[int] | None:
    """Extract a 32-bit EURID from an Alliance product label or typed ID.

    Product ID and Standardized Labeling Specification 1.8, sections 4.1 and
    4.3, defines an ANSI MH10.8.2 label with mandatory 30S (12-hex EURID) and
    1P (12-hex Product ID) containers. This integration supports 32-bit radio
    IDs only, so a genuine 48-bit EURID is rejected instead of truncated.
    """
    parsed = parse_commissioning_identity(value)
    if parsed:
        return list(parsed.sender_id)

    # Ubiwizz labels may expose the compact 14-hex QR payload
    # ``04B382DAED6780`` rather than an ANSI MH10.8.2 container.  The first
    # eight hex digits are the EnOcean EURID; retain the suffix only as label
    # data outside this parser and never send it over radio.
    compact = str(value or "").strip()
    if re.fullmatch(r"[0-9A-Fa-f]{14}", compact):
        return [int(compact[index : index + 2], 16) for index in range(0, 8, 2)]
    return None


def declared_generic_eep(radio_metadata: dict[str, Any] | None) -> str | None:
    """Return the declared EEP when the generic sensor decoder covers it."""
    if not metadata_declares_eep(radio_metadata):
        return None
    eep = (radio_metadata or {}).get("eep")
    return eep if is_generic_supported(eep) else None


def manual_eep_offered(radio_metadata: dict[str, Any] | None) -> bool:
    """Return whether the operator may still assert a profile by hand.

    The field only appears when no radio telegram and no Product ID has
    declared one; a proven profile is never presented as editable.
    """
    return not metadata_declares_eep(radio_metadata)


def device_form_schema(
    radio_metadata: dict[str, Any] | None,
    *,
    pending_platform: str | None = None,
    pending_name: str | None = None,
    pending_manual_eep: str = "",
) -> vol.Schema:
    """Build the platform/name form with websocket-serializable selectors.

    Values typed during a rejected submit are restored as defaults, and the
    optional manual EEP field is appended only when it is offered at all.
    """
    if pending_platform is not None:
        schema: dict[Any, Any] = {
            vol.Required(CONF_PLATFORM, default=pending_platform): (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(options=list(UI_DEVICE_PLATFORMS))
                )
            ),
            vol.Required(CONF_NAME, default=pending_name): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
        }
    else:
        recommendation = recommended_platform(radio_metadata)
        schema = {
            vol.Required(
                CONF_PLATFORM,
                **({"default": recommendation} if recommendation else {}),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=list(UI_DEVICE_PLATFORMS))
            ),
            vol.Required(CONF_NAME): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
        }
    if manual_eep_offered(radio_metadata):
        schema[vol.Optional(CONF_MANUAL_EEP, default=pending_manual_eep)] = (
            selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            )
        )
    return vol.Schema(schema)


def device_details_schema(
    platform: str, *, suggested_sender_id: str, declared_eep: str | None
) -> vol.Schema:
    """Build the platform form with websocket-serializable selectors only.

    Custom voluptuous functions crash the WS/REST flow handler with a
    ValueError (HTTP 500); native selectors are always serializable.
    """
    schema_dict: dict[Any, Any] = {}
    if platform == "binary_sensor":
        schema_dict[vol.Optional(CONF_DEVICE_CLASS)] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[item.value for item in BinarySensorDeviceClass]
            )
        )
    elif platform == "switch":
        schema_dict[vol.Optional(CONF_CHANNEL, default=0)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=31, mode=selector.NumberSelectorMode.BOX
            )
        )
        schema_dict[vol.Required(CONF_SWITCH_TYPE, default="default")] = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(options=list(SWITCH_TYPES))
            )
        )
    elif platform == "light":
        schema_dict[vol.Required(CONF_SENDER_ID, default=suggested_sender_id)] = (
            selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            )
        )
        # D2-01-12 multi-gang actuators report every channel; pick which
        # one drives this light (I/O channel is 5 bits wide).
        schema_dict[vol.Optional("channel", default=0)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=31,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
            )
        )
    elif platform == "sensor":
        # A profile declared by a 4BS teach-in (or a label) and covered by
        # the generic EEP.xml decoder pre-selects the ``eep`` class.
        schema_dict[
            vol.Optional(
                CONF_DEVICE_CLASS,
                default=GENERIC_SENSOR_DEVICE_CLASS if declared_eep else "powersensor",
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[*SENSOR_TYPES, GENERIC_SENSOR_DEVICE_CLASS]
            )
        )
        eep_options = [
            {"value": eep, "label": f"{eep} · {profile.description}"}
            for eep, profile in generic_profiles().items()
        ]
        eep_key = (
            vol.Optional(CONF_GENERIC_EEP, default=declared_eep)
            if declared_eep
            else vol.Optional(CONF_GENERIC_EEP)
        )
        schema_dict[eep_key] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=eep_options, mode=selector.SelectSelectorMode.DROPDOWN
            )
        )
        for field_name, default, minimum, maximum in (
            (CONF_MAX_TEMP, 40, -100, 100),
            (CONF_MIN_TEMP, 0, -100, 100),
            (CONF_RANGE_FROM, 255, 0, 255),
            (CONF_RANGE_TO, 0, 0, 255),
        ):
            schema_dict[vol.Optional(field_name, default=default)] = (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=minimum,
                        max=maximum,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                )
            )
    elif platform == "climate":
        schema_dict[
            vol.Required(CONF_SENDER_ID_SWITCH, default=suggested_sender_id)
        ] = selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        )
        schema_dict[vol.Required(CONF_DEVICE_TYPE)] = selector.SelectSelector(
            # Review finding P2-02: only offer what UI_DEVICE_SCHEMA can
            # actually persist (A5-20-04 stays YAML-only for now).
            selector.SelectSelectorConfig(options=list(UI_CLIMATE_DEVICE_TYPES))
        )
        schema_dict[vol.Required(CONF_SENSOR_ENTITY_ID)] = selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        )
        for field_name, default, minimum, maximum, step in (
            (CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION, 8.0, 0, 20, 0.1),
            (CONF_SENSOR_TARGET_TEMP_RANGE, 5, 0, 20, 1),
            (CONF_SENSOR_TARGET_TEMP_TOLERANCE, 0.5, 0, 10, 0.1),
            (CONF_TARGET_TEMP_BASE, 21.0, 5, 35, 0.1),
            (CONF_TARGET_TEMP_NIGHT_REDUCTION, 4.0, 0, 20, 0.1),
            (CONF_PI_CONTROL_KP, 5.0, 0.001, 100, 0.001),
            (CONF_PI_CONTROL_TN, 240.0, 0.001, 1440, 0.001),
        ):
            schema_dict[vol.Optional(field_name, default=default)] = (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=minimum,
                        max=maximum,
                        step=step,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                )
            )
        schema_dict[vol.Optional(CONF_COMMAND_FREQUENCY, default="00:17:00")] = (
            selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            )
        )
    return vol.Schema(schema_dict)


@dataclass(slots=True)
class DeviceDetailsOutcome:
    """What validating a details submission produced.

    Exactly one of ``row`` and ``errors`` is set. ``radio_metadata`` is the
    metadata to keep on the flow: it gains a manual generic-EEP assertion
    when the operator picked one for a ``sensor`` row.
    """

    row: dict[str, Any] | None = None
    errors: dict[str, str] = field(default_factory=dict)
    radio_metadata: dict[str, Any] | None = None


def validate_device_details(
    platform: str,
    user_input: dict[str, Any],
    *,
    captured_id: list[int],
    name: str,
    radio_metadata: dict[str, Any] | None,
    used_sender_ids: list[list[int]],
) -> DeviceDetailsOutcome:
    """Validate a details submission and build the validated row.

    The checks and their error keys are the ones the options flow has always
    applied; the first failing check wins. Nothing here consults a registry:
    identity collisions are the caller's business.
    """
    outcome = DeviceDetailsOutcome(radio_metadata=radio_metadata)
    values = dict(user_input)

    if platform == "switch":
        channel = exact_int(values.get(CONF_CHANNEL, 0))
        if channel is None or not 0 <= channel <= 31:
            outcome.errors = {CONF_CHANNEL: "invalid_channel"}
            return outcome
        if values.get(CONF_SWITCH_TYPE) == "RPS" and channel not in (0, 1):
            outcome.errors = {CONF_CHANNEL: "invalid_channel_rps"}
            return outcome
    elif platform == "light":
        # D2-01-12 I/O channel is 5 bits wide (0-31).
        channel = exact_int(values.get(CONF_CHANNEL, 0))
        if channel is None or not 0 <= channel <= 31:
            outcome.errors = {CONF_CHANNEL: "invalid_channel"}
            return outcome
    else:
        channel = 0

    sender_id: list[int] | None = None
    id_switch: list[int] | None = None
    if platform == "light":
        sender_id = parse_sender_id(values.get(CONF_SENDER_ID))
        if sender_id is None:
            outcome.errors = {CONF_SENDER_ID: "invalid_sender_id"}
            return outcome
        if sender_id in used_sender_ids:
            outcome.errors = {CONF_SENDER_ID: "sender_id_collision"}
            return outcome
    elif platform == "climate":
        id_switch = parse_sender_id(values.get(CONF_SENDER_ID_SWITCH))
        if id_switch is None:
            outcome.errors = {CONF_SENDER_ID_SWITCH: "invalid_sender_id"}
            return outcome
        if id_switch in used_sender_ids:
            outcome.errors = {CONF_SENDER_ID_SWITCH: "sender_id_collision"}
            return outcome

    if platform == "sensor":
        if values.get(CONF_DEVICE_CLASS) == GENERIC_SENSOR_DEVICE_CLASS:
            # A declared profile wins; otherwise the chosen one is recorded
            # as a manual assertion. Either way it must have a decoder.
            declared = declared_generic_eep(radio_metadata)
            chosen = declared or parse_manual_eep(values.get(CONF_GENERIC_EEP))
            if not is_generic_supported(chosen):
                outcome.errors = {CONF_GENERIC_EEP: "eep_unsupported"}
                return outcome
            if declared is None:
                outcome.radio_metadata = apply_manual_eep(radio_metadata, chosen)
        for field_name, minimum, maximum in (
            (CONF_MAX_TEMP, -100, 100),
            (CONF_MIN_TEMP, -100, 100),
            (CONF_RANGE_FROM, 0, 255),
            (CONF_RANGE_TO, 0, 255),
        ):
            value = exact_int(values.get(field_name))
            if value is None or not minimum <= value <= maximum:
                outcome.errors = {field_name: "invalid_exact_integer"}
                return outcome
            values[field_name] = value
        if (
            values.get(CONF_DEVICE_CLASS) == "temperature"
            and values[CONF_MIN_TEMP] >= values[CONF_MAX_TEMP]
        ):
            outcome.errors = {"base": "invalid_temperature_range"}
            return outcome
        if (
            values.get(CONF_DEVICE_CLASS) == "temperature"
            and values[CONF_RANGE_FROM] == values[CONF_RANGE_TO]
        ):
            outcome.errors = {"base": "invalid_raw_range"}
            return outcome

    if platform == "climate":
        for field_name, minimum, maximum in (
            (CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION, 0, 20),
            (CONF_SENSOR_TARGET_TEMP_TOLERANCE, 0, 10),
            (CONF_TARGET_TEMP_BASE, 5, 35),
            (CONF_TARGET_TEMP_NIGHT_REDUCTION, 0, 20),
            (CONF_PI_CONTROL_KP, 0.001, 100),
            (CONF_PI_CONTROL_TN, 0.001, 1440),
        ):
            value = finite_float(values.get(field_name))
            if value is None or not minimum <= value <= maximum:
                outcome.errors = {field_name: "invalid_number"}
                return outcome
            values[field_name] = value
        target_range = exact_int(values.get(CONF_SENSOR_TARGET_TEMP_RANGE))
        if target_range is None or not 0 <= target_range <= 20:
            outcome.errors = {CONF_SENSOR_TARGET_TEMP_RANGE: "invalid_exact_integer"}
            return outcome
        values[CONF_SENSOR_TARGET_TEMP_RANGE] = target_range
        try:
            command_frequency = cv.positive_time_period(
                values.get(CONF_COMMAND_FREQUENCY)
            )
        except vol.Invalid:
            command_frequency = timedelta(0)
        if command_frequency < timedelta(seconds=1):
            outcome.errors = {CONF_COMMAND_FREQUENCY: "invalid_command_frequency"}
            return outcome

    device = {
        "id": captured_id,
        "platform": platform,
        "name": name,
        CONF_RADIO_METADATA: outcome.radio_metadata,
        "device_class": values.get(CONF_DEVICE_CLASS),
        "channel": channel,
        "switch_type": values.get(CONF_SWITCH_TYPE),
        "sender_id": sender_id,
        "id_switch": id_switch,
        "device_type": values.get(CONF_DEVICE_TYPE),
        "sensor_entity_id": values.get(CONF_SENSOR_ENTITY_ID),
        "temperature_frost_protection": values.get(
            CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION, 8.0
        ),
        "sensor_target_temperature_range": values.get(CONF_SENSOR_TARGET_TEMP_RANGE, 5),
        "sensor_target_temperature_update_tolerance": values.get(
            CONF_SENSOR_TARGET_TEMP_TOLERANCE, 0.5
        ),
        "target_temperature_base_value": values.get(CONF_TARGET_TEMP_BASE, 21.0),
        "target_temperature_reduction_night": values.get(
            CONF_TARGET_TEMP_NIGHT_REDUCTION, 4.0
        ),
        "command_frequency": values.get(CONF_COMMAND_FREQUENCY, "00:17:00"),
        "pi_control_Kp": values.get(CONF_PI_CONTROL_KP, 5.0),
        "pi_control_Tn": values.get(CONF_PI_CONTROL_TN, 240.0),
        "max_temp": values.get(CONF_MAX_TEMP, 40),
        "min_temp": values.get(CONF_MIN_TEMP, 0),
        "range_from": values.get(CONF_RANGE_FROM, 255),
        "range_to": values.get(CONF_RANGE_TO, 0),
    }
    try:
        outcome.row = UI_DEVICE_SCHEMA(device)
    except vol.Invalid:
        outcome.errors = {"base": "invalid_device_details"}
    return outcome


def identity_collisions(
    row: dict[str, Any],
    other_rows: list[dict[str, Any]],
    registry_lookup: Any,
) -> bool:
    """Return whether any entity the row would create already exists.

    Every entity the row would create is checked against the registry and
    against every entity the other rows create, so a companion entity (energy
    counter, A5-14-01 supply voltage) can neither be duplicated nor silently
    merged.
    """
    stored_identities = {
        identity
        for existing in other_rows
        for identity in ui_device_entity_identities(existing)
    }
    return any(
        (platform, unique_id) in stored_identities
        or registry_lookup(platform, DOMAIN, unique_id) is not None
        for platform, unique_id in ui_device_entity_identities(row)
    )


def declare_profile_on_row(
    raw_row: dict[str, Any],
    row: dict[str, Any],
    eep: str,
    *,
    other_rows: list[dict[str, Any]],
    registry_lookup: Any,
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Record a manual EEP on a row, identity preserved; return (raw row, errors).

    The row is re-created as the same entity class or not at all: a profile
    that would change the primary identity is a new device, not a declaration
    on this one. New companion identities must be free in the registry and
    among the other rows.
    """
    metadata = apply_manual_eep(row.get(CONF_RADIO_METADATA), eep)
    metadata.setdefault("sender_id", list(row["id"]))
    updated_raw = {**raw_row, CONF_RADIO_METADATA: metadata}
    try:
        updated = UI_DEVICE_SCHEMA(updated_raw)
    except vol.Invalid:
        return None, {"base": "invalid_device_details"}
    before = ui_device_entity_identities(row)
    after = ui_device_entity_identities(updated)
    if after[0] != before[0]:
        return None, {CONF_MANUAL_EEP: "profile_changes_identity"}
    others = {
        identity
        for existing in other_rows
        for identity in ui_device_entity_identities(existing)
    }
    for platform, unique_id in after:
        if (platform, unique_id) in before:
            continue
        if (platform, unique_id) in others or registry_lookup(
            platform, DOMAIN, unique_id
        ) is not None:
            return None, {"base": "unique_id_exists"}
    return updated_raw, {}

"""User-supplied generic profile definitions (A6), loaded from a JSON file.

The vendored ``EEP.xml`` drives the generic sensor decoder (``eep_catalog``).
A profile it lacks, or a vendor-specific layout, can be described by the
operator in ``<config>/enocean_custom/profiles.json``:

.. code-block:: json

    {
      "version": 1,
      "profiles": [
        {
          "eep": "A5-02-30",
          "description": "Temperature sensor, -40..62.5 degrees",
          "fields": [
            {
              "shortcut": "TMP",
              "description": "Temperature",
              "kind": "value",
              "offset": 16,
              "size": 10,
              "unit": "C",
              "range": {"min": 1023, "max": 0},
              "scale": {"min": -40, "max": 62.5}
            }
          ]
        }
      ]
    }

The file is plain data. It is parsed with ``json.loads``, validated against a
strict voluptuous schema (unknown keys refused, every number bounded), and
turned into the same frozen ``EepProfile``/``EepField`` objects the XML
produces; nothing in it is ever executed. A profile the vendored catalog
already describes, or one of the families with a dedicated decoder (D2-01
actuators, D2-14-30, A5-12-01, A5-14-01, A5-20-xx, A5-38-08, RPS, 1BS), is
refused rather than overridden. An invalid file disables every user profile
and raises a Repair issue that quotes the validation error; a valid file
whose refused profiles are listed in a second Repair issue still loads the
rest. Rows that declare a user profile become plain skipped rows while the
file is invalid or absent: nothing crashes, the entities simply do not exist
until the definition is back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, LOGGER
from .eep_catalog import (
    EXCLUDED_PROFILES,
    EepField,
    EepProfile,
    built_in_profiles,
    set_user_profiles,
    slugify_shortcut,
)
from .enocean_library.protocol.constants import RORG

USER_PROFILES_RELATIVE_PATH: Final = ("enocean_custom", "profiles.json")
MAX_FILE_BYTES: Final = 256 * 1024
MAX_PROFILES: Final = 64
MAX_FIELDS: Final = 32

ISSUE_USER_PROFILES_INVALID: Final = "user_profiles_invalid"
ISSUE_USER_PROFILES_SKIPPED: Final = "user_profiles_skipped"

# Payload width per RORG: 4BS carries four data bytes, VLD up to fourteen.
_PAYLOAD_BITS: Final = {int(RORG.BS4): 32, int(RORG.VLD): 14 * 8}
# EEP families with a dedicated decoder in this repository: never overridden.
_RESERVED_PREFIXES: Final = ("D2-01-", "D2-14-30", "A5-20-", "F6-", "D5-")

_EEP = vol.Match(r"^[0-9A-Fa-f]{2}-[0-9A-Fa-f]{2}-[0-9A-Fa-f]{2}$")
_SHORTCUT = vol.All(str, vol.Length(min=1, max=16), vol.Match(r"^[A-Za-z0-9_/+\-]+$"))
_TEXT = vol.All(str, vol.Length(min=1, max=80))
_BIT = vol.All(int, vol.Range(min=0, max=127))
_NUMBER = vol.All(vol.Any(int, float), vol.Range(min=-1_000_000, max=1_000_000))

_BOUNDS_SCHEMA = vol.Schema(
    {vol.Required("min"): _NUMBER, vol.Required("max"): _NUMBER},
    extra=vol.PREVENT_EXTRA,
)

_FIELD_SCHEMA = vol.Schema(
    {
        vol.Required("shortcut"): _SHORTCUT,
        vol.Optional("description"): _TEXT,
        vol.Required("kind"): vol.In(("value", "enum")),
        vol.Required("offset"): _BIT,
        vol.Required("size"): vol.All(int, vol.Range(min=1, max=32)),
        vol.Optional("unit"): vol.All(str, vol.Length(max=16)),
        vol.Optional("range"): _BOUNDS_SCHEMA,
        vol.Optional("scale"): _BOUNDS_SCHEMA,
    },
    extra=vol.PREVENT_EXTRA,
)

_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required("offset"): _BIT,
        vol.Required("size"): vol.All(int, vol.Range(min=1, max=8)),
        vol.Required("value"): vol.All(int, vol.Range(min=0, max=255)),
    },
    extra=vol.PREVENT_EXTRA,
)

_PROFILE_SCHEMA = vol.Schema(
    {
        vol.Required("eep"): _EEP,
        vol.Required("description"): _TEXT,
        vol.Optional("command"): _COMMAND_SCHEMA,
        vol.Required("fields"): vol.All(
            [_FIELD_SCHEMA], vol.Length(min=1, max=MAX_FIELDS)
        ),
    },
    extra=vol.PREVENT_EXTRA,
)

USER_PROFILES_SCHEMA = vol.Schema(
    {
        vol.Required("version"): 1,
        vol.Required("profiles"): vol.All(
            [_PROFILE_SCHEMA], vol.Length(max=MAX_PROFILES)
        ),
    },
    extra=vol.PREVENT_EXTRA,
)


@dataclass(slots=True)
class UserProfilesReport:
    """What loading the file produced; shown in logs and Repair issues."""

    path: str
    present: bool = False
    error: str | None = None
    loaded: tuple[str, ...] = ()
    refused: dict[str, str] = field(default_factory=dict)


def user_profiles_path(hass: HomeAssistant) -> Path:
    """Return the location of the operator's profile definitions."""
    return Path(hass.config.path(*USER_PROFILES_RELATIVE_PATH))


def _reserved_reason(eep: str) -> str | None:
    """Return why a profile may not be defined by the operator, or None."""
    if eep in built_in_profiles():
        return "already described by the vendored EEP.xml"
    if eep in EXCLUDED_PROFILES:
        return "has a dedicated decoder in this integration"
    if eep.startswith(_RESERVED_PREFIXES):
        return "belongs to a family with a dedicated decoder in this integration"
    return None


def _build_field(spec: dict[str, Any]) -> EepField:
    shortcut = spec["shortcut"]
    kind = spec["kind"]
    description = spec.get("description") or shortcut
    unit = spec.get("unit") or ""
    if kind == "enum":
        return EepField(
            shortcut,
            slugify_shortcut(shortcut),
            description,
            "enum",
            spec["offset"],
            spec["size"],
            unit,
        )
    if "range" not in spec or "scale" not in spec:
        raise vol.Invalid(f"field {shortcut}: a value field needs range and scale")
    return EepField(
        shortcut,
        slugify_shortcut(shortcut),
        description,
        "value",
        spec["offset"],
        spec["size"],
        unit,
        float(spec["range"]["min"]),
        float(spec["range"]["max"]),
        float(spec["scale"]["min"]),
        float(spec["scale"]["max"]),
    )


def build_profile(spec: dict[str, Any]) -> EepProfile:
    """Turn one validated profile definition into a decodable profile.

    Raises ``vol.Invalid`` for a layout the decoder cannot honour: a RORG
    other than 4BS/VLD, a field or command beyond the telegram's payload, two
    fields with the same shortcut, or a profile without a single exposed
    field (an enum needs a known mapping, see ``eep_catalog.BINARY_SEMANTICS``).
    """
    eep = spec["eep"].upper()
    rorg, func, type_ = (int(part, 16) for part in eep.split("-"))
    payload_bits = _PAYLOAD_BITS.get(rorg)
    if payload_bits is None:
        raise vol.Invalid(f"{eep}: only 4BS (A5) and VLD (D2) profiles can be defined")
    fields = tuple(_build_field(item) for item in spec["fields"])
    shortcuts = [item.shortcut for item in fields]
    if len(set(shortcuts)) != len(shortcuts):
        raise vol.Invalid(f"{eep}: field shortcuts must be unique")
    for item in fields:
        if item.offset + item.size > payload_bits:
            raise vol.Invalid(
                f"{eep}: field {item.shortcut} ends beyond the {payload_bits}-bit payload"
            )
    command = spec.get("command")
    command_value = command_offset = command_size = None
    if command is not None:
        command_value, command_offset, command_size = (
            command["value"],
            command["offset"],
            command["size"],
        )
        if command_offset + command_size > payload_bits:
            raise vol.Invalid(f"{eep}: command field ends beyond the payload")
        if command_value >= 1 << command_size:
            raise vol.Invalid(f"{eep}: command value does not fit its size")
    profile = EepProfile(
        eep,
        rorg,
        func,
        type_,
        spec["description"],
        fields,
        command_value,
        command_offset or 0,
        command_size or 0,
    )
    if not profile.exposed_fields:
        raise vol.Invalid(
            f"{eep}: no field would become an entity (value fields always do; an enum "
            "needs a mapped shortcut such as PIR, PIRS, CO or RAN)"
        )
    return profile


def parse_user_profiles(text: str) -> tuple[dict[str, EepProfile], dict[str, str]]:
    """Validate the file content; return ``(accepted, refused)``.

    Raises ``vol.Invalid`` (or ``ValueError`` for malformed JSON) when the
    file as a whole is unusable; refused profiles (reserved or unbuildable)
    are reported by EEP with the reason and never override anything.
    """
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise vol.Invalid(f"file larger than {MAX_FILE_BYTES} bytes")
    document = USER_PROFILES_SCHEMA(json.loads(text))
    accepted: dict[str, EepProfile] = {}
    refused: dict[str, str] = {}
    for spec in document["profiles"]:
        eep = spec["eep"].upper()
        if eep in accepted:
            refused[eep] = "defined twice in the file"
            continue
        reason = _reserved_reason(eep)
        if reason is not None:
            refused[eep] = reason
            continue
        try:
            accepted[eep] = build_profile(spec)
        except vol.Invalid as err:
            refused[eep] = str(err)
    return accepted, refused


def _read_file(path: Path) -> str | None:
    """Return the file content, or None when there is no file (executor)."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


async def async_load_user_profiles(hass: HomeAssistant) -> UserProfilesReport:
    """Load the operator's profiles, publish them to the catalog, raise Repairs.

    Called at every entry set-up, so editing the file and reloading the
    integration is enough. A missing file is the normal case and clears every
    previous user profile and issue.
    """
    path = user_profiles_path(hass)
    report = UserProfilesReport(path=str(path))
    text = await hass.async_add_executor_job(_read_file, path)
    ir.async_delete_issue(hass, DOMAIN, ISSUE_USER_PROFILES_INVALID)
    ir.async_delete_issue(hass, DOMAIN, ISSUE_USER_PROFILES_SKIPPED)
    if text is None:
        set_user_profiles({})
        return report
    report.present = True
    try:
        accepted, refused = parse_user_profiles(text)
    except (vol.Invalid, ValueError, TypeError) as err:
        set_user_profiles({})
        report.error = str(err)
        LOGGER.warning(
            "User EEP profile definitions ignored (%s is invalid): %s", path, err
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_USER_PROFILES_INVALID,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_USER_PROFILES_INVALID,
            translation_placeholders={"path": str(path), "reason": str(err)[:300]},
        )
        return report
    set_user_profiles(accepted)
    report.loaded = tuple(accepted)
    report.refused = refused
    if refused:
        LOGGER.warning(
            "User EEP profile definitions skipped: %s",
            "; ".join(f"{eep}: {reason}" for eep, reason in refused.items()),
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_USER_PROFILES_SKIPPED,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_USER_PROFILES_SKIPPED,
            translation_placeholders={
                "path": str(path),
                "eeps": ", ".join(
                    f"{eep} ({reason})" for eep, reason in refused.items()
                )[:300],
            },
        )
    if accepted:
        LOGGER.info(
            "Loaded %d user EEP profile definition(s): %s",
            len(accepted),
            ", ".join(accepted),
        )
    return report

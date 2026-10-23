"""Silence detection: availability derived from the last received telegram.

EnOcean devices harvest their energy and transmit when they feel like it, so
"no telegram" is only meaningful for profiles that report periodically or
that matter when they fall silent. Detection is therefore opt-in, bounded per
profile category and configurable from the options flow:

* ``sensor``  - A5 measurements and D2-01 power reports (periodic);
* ``contact`` - D5-00-01 and A5-14-01 window/door contacts (heartbeats on
  most products, not all);
* ``smoke``   - F6-05-02 smoke detectors;
* ``handle``  - F6-10-00 handles (event-only on HOPPE products).

Rockers, actuators and climate controllers never time out: a rocker that is
not pressed is not broken. A category with ``0`` minutes is never timed out.
Smoke detectors and handles default to ``0`` because the real emission
intervals of the maintainer's products have not been measured yet; enabling
them is a deliberate choice with a measured interval.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from typing import Any, Final

import voluptuous as vol

from .schema import exact_finite_int

CONF_AVAILABILITY: Final = "availability"
CONF_ENABLED: Final = "enabled"

CATEGORY_SENSOR: Final = "sensor"
CATEGORY_CONTACT: Final = "contact"
CATEGORY_SMOKE: Final = "smoke"
CATEGORY_HANDLE: Final = "handle"
CATEGORIES: Final = (CATEGORY_SENSOR, CATEGORY_CONTACT, CATEGORY_SMOKE, CATEGORY_HANDLE)

# Categories whose silence also raises a Repair issue: a dead smoke detector,
# contact or handle must be seen, a stale temperature only becomes unavailable.
REPAIR_CATEGORIES: Final = frozenset(
    {CATEGORY_CONTACT, CATEGORY_SMOKE, CATEGORY_HANDLE}
)

MAX_MINUTES: Final = 7 * 24 * 60
DEFAULT_MINUTES: Final[Mapping[str, int]] = MappingProxyType(
    {
        CATEGORY_SENSOR: 180,
        CATEGORY_CONTACT: 24 * 60,
        CATEGORY_SMOKE: 0,
        CATEGORY_HANDLE: 0,
    }
)


def minutes_key(category: str) -> str:
    """Return the option key holding the timeout of one category."""
    return f"{category}_minutes"


AVAILABILITY_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ENABLED, default=False): bool,
        **{
            vol.Optional(minutes_key(category), default=default): vol.All(
                exact_finite_int, vol.Range(min=0, max=MAX_MINUTES)
            )
            for category, default in DEFAULT_MINUTES.items()
        },
    },
    extra=vol.REMOVE_EXTRA,
)


@dataclass(frozen=True, slots=True)
class AvailabilitySettings:
    """Validated silence-detection settings of the config entry."""

    enabled: bool
    minutes: Mapping[str, int]

    def timeout_for(self, category: str | None) -> timedelta | None:
        """Return the silence timeout of a category, or None when it never expires."""
        if not self.enabled or category is None:
            return None
        minutes = self.minutes.get(category, 0)
        return timedelta(minutes=minutes) if minutes > 0 else None

    def as_options(self) -> dict[str, Any]:
        """Return the persisted representation."""
        return {
            CONF_ENABLED: self.enabled,
            **{
                minutes_key(category): self.minutes[category] for category in CATEGORIES
            },
        }


DEFAULT_SETTINGS = AvailabilitySettings(enabled=False, minutes=DEFAULT_MINUTES)


def availability_settings(options: Any) -> AvailabilitySettings:
    """Read the settings from entry options; anything malformed means defaults.

    Options are hand-editable in ``.storage``: a bad value must never prevent
    the entry from loading, it only disables detection for that entry.
    """
    raw = options.get(CONF_AVAILABILITY) if isinstance(options, Mapping) else None
    if not isinstance(raw, Mapping):
        return DEFAULT_SETTINGS
    try:
        validated = AVAILABILITY_SCHEMA(dict(raw))
    except vol.Invalid:
        return DEFAULT_SETTINGS
    return AvailabilitySettings(
        enabled=bool(validated[CONF_ENABLED]),
        minutes=MappingProxyType(
            {category: validated[minutes_key(category)] for category in CATEGORIES}
        ),
    )


def silence_category(row: Mapping[str, Any]) -> str | None:
    """Classify a UI row for silence detection, or None for event-only devices.

    The classification uses what the row already states (platform, device
    class, declared EEP). It never infers a profile from a telegram.
    """
    platform = row.get("platform")
    device_class = row.get("device_class")
    eep = str((row.get("radio_metadata") or {}).get("eep") or "").upper()
    if platform == "binary_sensor":
        if device_class == "smoke" or eep in ("F6-05-02", "D2-14-30"):
            return CATEGORY_SMOKE
        if eep in ("A5-14-01", "D5-00-01"):
            return CATEGORY_CONTACT
        return None
    if platform == "sensor":
        if device_class == "windowhandle" or eep == "F6-10-00":
            return CATEGORY_HANDLE
        if device_class in ("contact", "shuttercontact") or eep == "D5-00-01":
            return CATEGORY_CONTACT
        if device_class in ("temperature", "humidity", "powersensor") or eep.startswith(
            "A5-"
        ):
            return CATEGORY_SENSOR
    return None

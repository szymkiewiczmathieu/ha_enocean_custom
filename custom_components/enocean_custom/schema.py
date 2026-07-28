"""Shared configuration validators for EnOcean identifiers."""

from __future__ import annotations

import math

import homeassistant.helpers.config_validation as cv
import voluptuous as vol


def exact_finite_int(value: object) -> int:
    """Coerce exact finite integers without bools or lossy truncation."""
    if isinstance(value, bool):
        raise vol.Invalid("expected integer, not boolean")
    try:
        integer = int(value)
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as err:
        raise vol.Invalid("expected finite integer") from err
    if not math.isfinite(numeric):
        raise vol.Invalid("expected finite integer")
    if numeric != integer:
        raise vol.Invalid("integer value must not require truncation")
    return integer


def strict_int(value: object) -> int:
    """Require a real Python integer, excluding booleans and all floats."""
    if type(value) is not int:
        raise vol.Invalid("expected strict integer")
    return value


BYTE = vol.All(strict_int, vol.Range(min=0, max=255))
ENOCEAN_ID = vol.All(cv.ensure_list, [BYTE], vol.Length(min=4, max=4))


def optional_enocean_id(value: object) -> list[int]:
    """Accept an omitted receive ID or validate one complete EnOcean ID."""
    values = cv.ensure_list(value)
    if not values:
        return []
    return ENOCEAN_ID(values)

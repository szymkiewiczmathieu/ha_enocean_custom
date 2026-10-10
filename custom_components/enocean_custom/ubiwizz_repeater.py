"""Bounded Ubiwizz repeater capability contract.

Ubiwizz documentation reports a repeater function disabled by default with
levels 1 and 2 on some D2-01-01 and D2-01-12 modules. The repository has no
model-specific, captured radio command or read-back telegram for that function.

This module intentionally contains no ESP3 encoder, transport dependency, or
state mutation. It is the single policy boundary used by diagnostics and the
options-flow information screen until a hardware-validated protocol contract is
available.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

UBIWIZZ_REPEATER_CANDIDATE_EEPS: Final = ("D2-01-01", "D2-01-12")
UBIWIZZ_REPEATER_LEVELS: Final = (1, 2)
UBIWIZZ_REPEATER_DOCUMENTED_DEFAULT: Final = "disabled"
UBIWIZZ_REPEATER_RUNTIME_STATE: Final = "unknown"
UBIWIZZ_REPEATER_RADIO_COMMAND: Final = "not_implemented"


@dataclass(frozen=True, slots=True)
class UbiwizzRepeaterDiagnosticRequest:
    """A bounded operator request, not a detected device state or model claim."""

    eep: str
    requested_level: int


def parse_ubiwizz_repeater_request(
    eep: Any, requested_level: Any
) -> UbiwizzRepeaterDiagnosticRequest | None:
    """Validate the diagnostic-only profile/level choice without coercion.

    The UI selector posts level values as strings; direct callers may use an
    integer. Floats, booleans, whitespace, unsupported profiles, and an
    implicit "off" command are rejected. There is no radio command for any
    accepted request.
    """
    if not isinstance(eep, str) or eep not in UBIWIZZ_REPEATER_CANDIDATE_EEPS:
        return None
    if isinstance(requested_level, bool):
        return None
    if isinstance(requested_level, str):
        if requested_level not in ("1", "2"):
            return None
        requested_level = int(requested_level)
    if (
        type(requested_level) is not int
        or requested_level not in UBIWIZZ_REPEATER_LEVELS
    ):
        return None
    return UbiwizzRepeaterDiagnosticRequest(eep, requested_level)


def ubiwizz_repeater_diagnostics() -> dict[str, object]:
    """Return only policy facts; live repeater state has no supported read-back."""
    return {
        "candidate_eep_profiles": list(UBIWIZZ_REPEATER_CANDIDATE_EEPS),
        "documented_default": UBIWIZZ_REPEATER_DOCUMENTED_DEFAULT,
        "requested_levels": list(UBIWIZZ_REPEATER_LEVELS),
        "runtime_state": UBIWIZZ_REPEATER_RUNTIME_STATE,
        "radio_command": UBIWIZZ_REPEATER_RADIO_COMMAND,
    }

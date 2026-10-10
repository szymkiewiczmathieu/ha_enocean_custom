"""Ubiwizz repeater diagnostic boundary.

The supplied UBID1507C manual and product page document a two-channel
D2-01-12 actuator and local association steps. They do not define a remote
repeater read/write protocol, a default state, or a packet sequence. The
existing diagnostic selector is therefore kept only as a bounded operator note:
it sends no radio and makes no state/model claim.

The module repeater must not be confused with the USB300 gateway's ESP3
CO_WR_REPEATER command: that command configures the gateway, not a remote
Ubiwizz device. Remote repeater writes remain blocked until a Ubiwizz-specific
command and read-back are captured and validated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

UBIWIZZ_REPEATER_CANDIDATE_EEPS: Final = ("D2-01-01", "D2-01-12")
UBIWIZZ_REPEATER_LEVELS: Final = (1, 2)
UBIWIZZ_REPEATER_DOCUMENTED_DEFAULT: Final = "not_documented"
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

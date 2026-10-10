"""Ubiwizz repeater capability contract and validated model metadata.

Internet documentation identifies the user's pictured two-channel module as
Ubiwizz/Decelect UBID1507C, EEP D2-01-12. Its public manual confirms the two
channels, PRESS commissioning sequence, and compatibility with F6-10-00
window handles and D5-00-01 contacts. The manual confirms a repeater function
in the product datasheet, but does not publish a radio write/read-back command.

The module repeater must therefore not be confused with the USB300 gateway's
ESP3 CO_WR_REPEATER command: that command configures the gateway, not a remote
UBID1507C. We keep remote repeater writes blocked until a Ubiwizz-specific
command or a captured Flexom transaction is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

UBIWIZZ_REPEATER_CANDIDATE_EEPS: Final = ("D2-01-01", "D2-01-12")
UBIWIZZ_VALIDATED_MODELS: Final = {"D2-01-12": ("UBID1507C", 2)}
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

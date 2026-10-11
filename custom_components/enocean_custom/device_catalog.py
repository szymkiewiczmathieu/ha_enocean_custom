"""Evidence-backed product commissioning catalog.

The catalog deliberately distinguishes a product recipe from a bare EEP.  A
recipe is selectable only when its evidence is explicit; unknown products stay
in the expert/manual path rather than being guessed from an EURID.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class CommissioningProfile:
    """A product recipe used by the guided commissioning flow."""

    profile_id: str
    display_name: str
    eep: str
    channels: tuple[int, ...]
    manufacturer_id: int
    ute_number_of_channels: int
    press_count: int
    evidence: tuple[str, ...]


UBIWIZZ_LIGHT_D2_01_08: Final = CommissioningProfile(
    profile_id="ubiwizz_light_d2_01_08",
    display_name="Ubiwizz lighting module (field-verified)",
    eep="D2-01-08",
    channels=(0, 1),
    manufacturer_id=0x046,
    ute_number_of_channels=2,
    press_count=3,
    evidence=(
        "DolphinView capture supplied by operator",
        "field pairing procedure supplied by operator",
    ),
)

COMMISSIONING_PROFILES: Final = (UBIWIZZ_LIGHT_D2_01_08,)


def get_commissioning_profile(profile_id: object) -> CommissioningProfile | None:
    """Return a named recipe; never infer a product from an EEP or EURID."""
    if not isinstance(profile_id, str):
        return None
    return next((p for p in COMMISSIONING_PROFILES if p.profile_id == profile_id), None)

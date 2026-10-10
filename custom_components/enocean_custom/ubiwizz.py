"""Source-backed metadata for documented Ubiwizz installation profiles.

An EEP is not a product identity.  This module defines an explicit operator
profile for the Ubiwizz UBID1507C and must never be selected from an observed
D2 telegram alone.  It contains no radio encoder, pairing sequence, repeater
command, or hardware discovery.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

UBIWIZZ_UBID1507C_PROFILE: Final = "ubiwizz_ubid1507c"
UBIWIZZ_UBID1507C_EEP: Final = "D2-01-12"
UBIWIZZ_UBID1507C_MANUAL_URL: Final = (
    "https://ubiwizz.com/index.php?controller=attachment&id_attachment=927"
)
UBIWIZZ_UBID1507C_PRODUCT_URL: Final = (
    "https://ubiwizz.com/l-offre-produits-ubiwizz/11905-"
    "micromodule-radio-enocean-2-canaux-2x5a.html"
)


@dataclass(frozen=True, slots=True)
class UbiwizzActuatorProfile:
    """An explicit installation profile from an official Ubiwizz source."""

    profile_id: str
    model: str
    eep: str
    output_channels: tuple[int, ...]
    rated_output_current_a: int
    manual_url: str
    product_url: str


UBID1507C: Final = UbiwizzActuatorProfile(
    profile_id=UBIWIZZ_UBID1507C_PROFILE,
    model="UBID1507C",
    eep=UBIWIZZ_UBID1507C_EEP,
    output_channels=(0, 1),
    rated_output_current_a=5,
    manual_url=UBIWIZZ_UBID1507C_MANUAL_URL,
    product_url=UBIWIZZ_UBID1507C_PRODUCT_URL,
)

UBIWIZZ_ACTUATOR_PROFILES: Final[Mapping[str, UbiwizzActuatorProfile]] = (
    MappingProxyType({UBID1507C.profile_id: UBID1507C})
)


def ubiwizz_actuator_profile(value: object) -> UbiwizzActuatorProfile | None:
    """Return an explicitly named profile, never infer one from radio traffic."""
    if not isinstance(value, str):
        return None
    return UBIWIZZ_ACTUATOR_PROFILES.get(value)


def valid_ubiwizz_actuator_channel(profile_id: object, channel: object) -> bool:
    """Return whether one exact channel belongs to an explicit profile."""
    profile = ubiwizz_actuator_profile(profile_id)
    return (
        profile is not None
        and type(channel) is int
        and channel in profile.output_channels
    )

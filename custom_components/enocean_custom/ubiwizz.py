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
    ute_number_of_channels: int
    ute_manufacturer_id: int
    manual_url: str
    product_url: str


UBID1507C: Final = UbiwizzActuatorProfile(
    profile_id=UBIWIZZ_UBID1507C_PROFILE,
    model="UBID1507C",
    eep=UBIWIZZ_UBID1507C_EEP,
    output_channels=(0, 1),
    rated_output_current_a=5,
    # Field-verified UTE request values, distinct from output channel 0 or 1.
    ute_number_of_channels=2,
    ute_manufacturer_id=0x046,
    manual_url=UBIWIZZ_UBID1507C_MANUAL_URL,
    product_url=UBIWIZZ_UBID1507C_PRODUCT_URL,
)

UBIWIZZ_ACTUATOR_PROFILES: Final[Mapping[str, UbiwizzActuatorProfile]] = (
    MappingProxyType({UBID1507C.profile_id: UBID1507C})
)

# EEP 2.6.7, UTE DB5: "0x00..0xFE number of individual channel to be taught
# in, 0xFF teach-in of all channels supported by the device".
UTE_ALL_CHANNELS: Final = 0xFF


def accepted_ute_request_channels(
    profile: UbiwizzActuatorProfile, channel: int
) -> frozenset[int]:
    """Return the UTE CMD0 DB5 values that may address one selected output.

    The specification values are the selected output itself and ``0xFF`` (all
    channels). The field capture documented for this profile showed the module
    requesting with DB5 ``0x02``, its number of outputs, so that documented
    value is accepted as well. Every other DB5 value is rejected; the stored
    row always uses the output the operator selected, never DB5.
    """
    return frozenset({channel, UTE_ALL_CHANNELS, profile.ute_number_of_channels})


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

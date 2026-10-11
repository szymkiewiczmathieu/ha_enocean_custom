"""Directed Universal Teach-in (UTE) packet construction.

The Ubiwizz guided association is controller-initiated: once the operator has
entered the module's local association mode and explicitly confirms it in the
options flow, Home Assistant sends one directed standard D4/91 UTE telegram.
The exact target EURID and already-resolved dongle Base ID are mandatory. This
module has no broadcast encoder, inbound-request parser, or relay-command path.
"""

from __future__ import annotations

from typing import Final

from .enocean_library.protocol.constants import PACKET, RORG
from .enocean_library.protocol.packet import Packet as ESP3Packet

UTE_ASSOCIATION_FLAGS_BIDIRECTIONAL_ACCEPTED: Final = 0x91


def _exact_octets(value: object, *, count: int) -> list[int] | None:
    """Return exactly ``count`` byte values, never coercing input."""
    if (
        not isinstance(value, (list, tuple))
        or len(value) != count
        or any(type(byte) is not int or not 0 <= byte <= 0xFF for byte in value)
    ):
        return None
    return list(value)


def _parse_eep(value: object) -> tuple[int, int, int] | None:
    """Parse one exact EEP identifier without accepting a partial profile."""
    if not isinstance(value, str):
        return None
    parts = value.split("-")
    if len(parts) != 3 or any(len(part) != 2 for part in parts):
        return None
    try:
        parsed = tuple(int(part, 16) for part in parts)
    except ValueError:
        return None
    if any(not 0 <= byte <= 0xFF for byte in parsed):
        return None
    return parsed[0], parsed[1], parsed[2]


def build_ute_association_packet(
    *,
    sender_id: object,
    target_id: object,
    eep: object,
    number_of_channels: object,
    manufacturer_id: object,
) -> ESP3Packet:
    """Build one exact directed standard D4/91 UTE association telegram.

    ``sender_id`` is the resolved USB-dongle Base ID and ``target_id`` is the
    physical module EURID. For the Ubiwizz D2-01-12 profile this makes ERP1 data
    ``D4 91 02 46 00 12 01 D2 <Base ID> 00`` and optional data
    ``03 <EURID> FF 00``. The packet is targeted; it cannot broadcast or encode
    an actuator D2 relay command.
    """
    sender = _exact_octets(sender_id, count=4)
    target = _exact_octets(target_id, count=4)
    profile = _parse_eep(eep)
    if sender is None:
        raise ValueError("UTE requires a resolved four-byte sender Base ID")
    if target is None:
        raise ValueError("UTE requires an exact four-byte target EURID")
    if profile is None:
        raise ValueError("UTE requires an exact RORG-FUNC-TYPE EEP")
    if type(number_of_channels) is not int or not 0 <= number_of_channels <= 0xFF:
        raise ValueError("UTE number of channels must be an integer from 0 through 255")
    if type(manufacturer_id) is not int or not 0 <= manufacturer_id <= 0x7FF:
        raise ValueError("UTE manufacturer ID must be an 11-bit integer")

    rorg, func, eep_type = profile
    return ESP3Packet(
        PACKET.RADIO_ERP1,
        data=[
            RORG.UTE,
            UTE_ASSOCIATION_FLAGS_BIDIRECTIONAL_ACCEPTED,
            number_of_channels,
            manufacturer_id & 0xFF,
            (manufacturer_id >> 8) & 0x07,
            eep_type,
            func,
            rorg,
            *sender,
            0x00,
        ],
        optional=[0x03, *target, 0xFF, 0x00],
    )

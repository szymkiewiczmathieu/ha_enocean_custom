"""Directed Universal Teach-in (UTE) helpers for explicit commissioning.

For UBID1507C association the module broadcasts the bidirectional D4/A0 UTE
request after its physical PRESS sequence. The controller listening for that
exact module answers with a directed D4/91 response, retaining the matching
UTE fields. These helpers deliberately expose neither a broadcast query nor a
CMD-1 response parser: the active commissioning path is responder-only.
"""

from __future__ import annotations

from typing import Final

from .enocean_library.protocol.constants import PACKET, RORG
from .enocean_library.protocol.packet import Packet as ESP3Packet
from .enocean_library.protocol.packet import UTETeachInPacket

# A UTE-capable actuator requests an answer with D4 DB6 = A0. The accepted
# bidirectional response retains DB5..DB0 and changes DB6 to 91.
UTE_REQUEST_FLAGS_BIDIRECTIONAL_RESPONSE_EXPECTED: Final = 0xA0
UTE_RESPONSE_FLAGS_BIDIRECTIONAL_ACCEPTED: Final = 0x91


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


def build_ute_teach_in_response(
    *,
    sender_id: object,
    target_id: object,
    eep: object,
    number_of_channels: object,
    manufacturer_id: object,
) -> ESP3Packet:
    """Build the directed accepted D4/91 response to an exact UTE request.

    ``sender_id`` is the dongle Base ID and ``target_id`` is the EURID printed
    on the physical module. The caller must first validate an inbound D4/A0
    request; this helper does not broadcast and cannot discover a target.
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
            UTE_RESPONSE_FLAGS_BIDIRECTIONAL_ACCEPTED,
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


def parse_ute_teach_in_request(
    packet: object,
    *,
    target_id: object,
    eep: object,
    number_of_channels: object,
    manufacturer_id: object,
) -> dict[str, object] | None:
    """Return one exact broadcast D4/A0 request, otherwise ``None``.

    The physical label/QR EURID is part of the safety boundary: a request from
    another device, a directed packet, or different UTE fields is ignored.
    ``number_of_channels`` is the UTE field and is deliberately distinct from
    a configured relay output channel.
    """
    target = _exact_octets(target_id, count=4)
    profile = _parse_eep(eep)
    if (
        target is None
        or profile is None
        or type(number_of_channels) is not int
        or not 0 <= number_of_channels <= 0xFF
        or type(manufacturer_id) is not int
        or not 0 <= manufacturer_id <= 0x7FF
        or not isinstance(packet, UTETeachInPacket)
    ):
        return None

    data = packet.data
    sender = _exact_octets(packet.sender, count=4)
    destination = _exact_octets(packet.destination, count=4)
    rorg, func, eep_type = profile
    if (
        len(data) != 13
        or data[0] != RORG.UTE
        or data[1] != UTE_REQUEST_FLAGS_BIDIRECTIONAL_RESPONSE_EXPECTED
        or data[2] != number_of_channels
        or data[3] != manufacturer_id & 0xFF
        or data[4] != (manufacturer_id >> 8) & 0x07
        or data[5:8] != [eep_type, func, rorg]
        or data[12] != 0x00
        or sender != target
        or destination != [0xFF, 0xFF, 0xFF, 0xFF]
    ):
        return None
    return {
        "sender_id": sender,
        "destination": destination,
        "number_of_channels": data[2],
        "manufacturer_id": manufacturer_id,
        "eep": f"{rorg:02X}-{func:02X}-{eep_type:02X}",
    }

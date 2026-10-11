"""Standard Universal Teach-in (UTE) packets for explicit commissioning.

EEP 2.6.7 appendix 3.6 defines a UTE query as a broadcast D4 telegram with
CMD 0x0. The device that wants to be taught into a receiver sends that query;
the receiver in learn mode replies with addressed D4 CMD 0x1, echoing EEP and
channel. A responder has at most 500 ms after reception; the query sender waits
at most 700 ms after transmission. These helpers contain no relay-control
payload.
"""

from __future__ import annotations

from typing import Final

from .enocean_library.protocol.constants import PACKET, RORG
from .enocean_library.protocol.packet import Packet as ESP3Packet
from .enocean_library.protocol.packet import UTETeachInPacket

UTE_MANUFACTURER_ID_UNSPECIFIED: Final = 0x000
UTE_QUERY_FLAGS_BIDIRECTIONAL_RESPONSE_EXPECTED: Final = 0x80
UTE_RESPONSE_FLAGS_BIDIRECTIONAL_ACCEPTED: Final = 0x91
UTE_ALL_CHANNELS: Final = 0xFF
UTE_RESPONDER_MAX_RESPONSE_SECONDS: Final = 0.5
UTE_QUERY_MAX_RESPONSE_SECONDS: Final = 0.7


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


def build_ute_teach_in_query(
    *,
    sender_id: object,
    eep: object,
    channel: object,
    manufacturer_id: int = UTE_MANUFACTURER_ID_UNSPECIFIED,
) -> ESP3Packet:
    """Build one standards-defined broadcast UTE teach-in query.

    The query is intentionally broadcast as required by EEP appendix 3.6.  The
    selected entity's EURID is not used as a radio destination; it is used only
    to match the addressed response from the module that was placed in learn
    mode.  ``sender_id`` is the resolved dongle Base ID and is therefore the
    identity the receiver learns.
    """
    sender = _exact_octets(sender_id, count=4)
    profile = _parse_eep(eep)
    if sender is None:
        raise ValueError("UTE requires a resolved four-byte sender Base ID")
    if profile is None:
        raise ValueError("UTE requires an exact RORG-FUNC-TYPE EEP")
    if type(channel) is not int or not 0 <= channel <= UTE_ALL_CHANNELS:
        raise ValueError("UTE channel must be an integer from 0 through 255")
    if type(manufacturer_id) is not int or not 0 <= manufacturer_id <= 0x7FF:
        raise ValueError("UTE manufacturer ID must be an 11-bit integer")

    rorg, func, eep_type = profile
    data = [
        RORG.UTE,
        UTE_QUERY_FLAGS_BIDIRECTIONAL_RESPONSE_EXPECTED,
        channel,
        manufacturer_id & 0xFF,
        (manufacturer_id >> 8) & 0x07,
        eep_type,
        func,
        rorg,
        *sender,
        0x00,
    ]
    optional = [0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00]
    return ESP3Packet(PACKET.RADIO_ERP1, data=data, optional=optional)


def parse_ute_teach_in_response(
    packet: object,
    *,
    requester_id: object | None = None,
    eep: object | None = None,
    channel: object | None = None,
    manufacturer_id: int | None = None,
) -> dict[str, object] | None:
    """Parse one CMD 1 response, optionally binding it to a sent CMD 0 query.

    A discovery flow does not know the receiver EURID in advance, but it does
    know every field it broadcast: its own Base ID, requested EEP, channel and
    manufacturer ID. Binding all of those fields prevents an unrelated UTE
    exchange on the same radio from being mistaken for this one.
    """
    requested_sender = (
        _exact_octets(requester_id, count=4) if requester_id is not None else None
    )
    profile = _parse_eep(eep) if eep is not None else None
    if requester_id is not None and requested_sender is None:
        return None
    if eep is not None and profile is None:
        return None
    if channel is not None and (
        type(channel) is not int or not 0 <= channel <= UTE_ALL_CHANNELS
    ):
        return None
    if manufacturer_id is not None and (
        type(manufacturer_id) is not int or not 0 <= manufacturer_id <= 0x7FF
    ):
        return None
    if not isinstance(packet, UTETeachInPacket):
        return None

    data = packet.data
    sender = _exact_octets(packet.sender, count=4)
    destination = _exact_octets(packet.destination, count=4)
    if (
        len(data) != 13
        or data[0] != RORG.UTE
        or data[1] & 0xCF != 0x81
        or data[12] != 0x00
        or sender is None
        or destination is None
        or (requested_sender is not None and destination != requested_sender)
        or (channel is not None and data[2] != channel)
        or (
            manufacturer_id is not None
            and ((data[4] & 0x07) << 8) | data[3] != manufacturer_id
        )
        or (profile is not None and data[5:8] != [profile[2], profile[1], profile[0]])
    ):
        return None
    return {
        "sender_id": sender,
        "destination": destination,
        "accepted": ((data[1] >> 4) & 0x03) == 0x01,
        "response_code": (data[1] >> 4) & 0x03,
        "channel": data[2],
        "manufacturer_id": ((data[4] & 0x07) << 8) | data[3],
        "eep": f"{data[7]:02X}-{data[6]:02X}-{data[5]:02X}",
    }


def ute_teach_in_response_code(
    packet: object,
    *,
    target_id: object,
    sender_id: object,
    eep: object,
    channel: object,
) -> int | None:
    """Return a matching standard UTE response code, or ``None`` if unrelated."""
    target = _exact_octets(target_id, count=4)
    sender = _exact_octets(sender_id, count=4)
    profile = _parse_eep(eep)
    if (
        not isinstance(packet, UTETeachInPacket)
        or target is None
        or sender is None
        or profile is None
        or type(channel) is not int
        or not 0 <= channel <= UTE_ALL_CHANNELS
    ):
        return None

    response = parse_ute_teach_in_response(
        packet,
        requester_id=sender,
        eep=eep,
        channel=channel,
        manufacturer_id=UTE_MANUFACTURER_ID_UNSPECIFIED,
    )
    if response is None or response["sender_id"] != target:
        return None
    return (packet.data[1] >> 4) & 0x03


def is_accepted_ute_teach_in_response(
    packet: object,
    *,
    target_id: object,
    sender_id: object,
    eep: object,
    channel: object,
) -> bool:
    """Return whether a packet is the exact accepted response to one query."""
    return (
        ute_teach_in_response_code(
            packet,
            target_id=target_id,
            sender_id=sender_id,
            eep=eep,
            channel=channel,
        )
        == 0x01
    )

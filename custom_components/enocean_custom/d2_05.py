"""D2-05-00 blinds control commands and reply decoding.

Source: EnOcean Equipment Profiles 2.6.7, D2-05-00, pp. 157-159.
Only TYPE 0x00 (one output channel) is implemented. CMD 0x5 Set Parameters is
intentionally absent: motor timing and alarm behaviour are installation-specific
and are not inferred by this integration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from .enocean_library.protocol.constants import PACKET, RORG
from .enocean_library.protocol.packet import Packet as ESP3Packet
from .entity import build_radio_optional

CMD_GO_TO: Final = 0x01
CMD_STOP: Final = 0x02
CMD_QUERY: Final = 0x03
CMD_REPLY: Final = 0x04
UNCHANGED: Final = 0x7F


def _check_channel(channel: int) -> None:
    # EEP 2.6.7 p. 157 command overview: TYPE 0x00 has one output channel;
    # p. 158 CHN=0 means channel 1. Multi-channel TYPE 0x01 is not this profile.
    if isinstance(channel, bool) or not isinstance(channel, int) or channel != 0:
        raise ValueError("D2-05-00 supports only channel 0")


def _check_ha_position(position: int) -> None:
    if (
        isinstance(position, bool)
        or not isinstance(position, int)
        or not 0 <= position <= 100
    ):
        raise ValueError("cover position must be an integer from 0 to 100")


def build_position_command(
    sender_id: list[int], destination: list[int], channel: int, ha_position: int
) -> ESP3Packet:
    """Build CMD 0x1 Go to Position and Angle (EEP 2.6.7 p. 158).

    POS counts 0 % as up/open and 100 % as down/closed; Home Assistant uses
    the opposite convention, hence ``100 - ha_position``. ANG=0x7F means
    "Do not change" (not 0 degrees). REPO=0 goes directly to POS/ANG and
    LOCK=0 leaves the locking mode unchanged. CMD is the low nibble of byte 3.
    """
    _check_channel(channel)
    _check_ha_position(ha_position)
    spec_position = 100 - ha_position
    return ESP3Packet(
        PACKET.RADIO_ERP1,
        data=[
            RORG.VLD,
            spec_position,
            UNCHANGED,
            0x00,
            (channel << 4) | CMD_GO_TO,
            *sender_id,
            0x00,
        ],
        optional=build_radio_optional(destination),
    )


def build_stop_command(
    sender_id: list[int], destination: list[int], channel: int
) -> ESP3Packet:
    """Build CMD 0x2 Stop (EEP 2.6.7 p. 158)."""
    _check_channel(channel)
    return ESP3Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.VLD, (channel << 4) | CMD_STOP, *sender_id, 0x00],
        optional=build_radio_optional(destination),
    )


def build_query_command(
    sender_id: list[int], destination: list[int], channel: int
) -> ESP3Packet:
    """Build CMD 0x3 Query Position and Angle (EEP 2.6.7 pp. 158-159)."""
    _check_channel(channel)
    return ESP3Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.VLD, (channel << 4) | CMD_QUERY, *sender_id, 0x00],
        optional=build_radio_optional(destination),
    )


@dataclass(frozen=True, slots=True)
class D205Status:
    """One valid CMD 0x4 reply; positions use Home Assistant's convention."""

    position: int | None
    angle: int | None
    locking_mode: int
    channel: int


def parse_d2_05_00_status(data: Any) -> D205Status | None:
    """Decode CMD 0x4 Reply Position and Angle (EEP 2.6.7 p. 159).

    POS and ANG are seven-bit values. 0x7F means unknown; other values above
    100 are reserved. POS is inverted for Home Assistant. LOCK is kept as the
    documented raw value (0 normal, 1 blockage, 2 alarm); no angle-to-HA tilt
    conversion is invented because this entity does not claim tilt support.
    """
    if not isinstance(data, (list, tuple)) or len(data) != 10 or data[0] != RORG.VLD:
        return None
    if any(
        isinstance(byte, bool) or not isinstance(byte, int) or not 0 <= byte <= 0xFF
        for byte in data
    ):
        return None
    position_raw = data[1] & 0x7F
    angle_raw = data[2] & 0x7F
    locking_mode = data[3] & 0x07
    channel = (data[4] >> 4) & 0x0F
    if data[4] & 0x0F != CMD_REPLY or channel != 0:
        return None
    if position_raw > 100 and position_raw != UNCHANGED:
        return None
    return D205Status(
        position=(None if position_raw == UNCHANGED else 100 - position_raw),
        angle=angle_raw if angle_raw <= 100 else None,
        locking_mode=locking_mode,
        channel=channel,
    )

"""RORG 0xD0 SIGNAL telegrams: passive-only diagnostics.

Source: the EnOcean Alliance "Signal Telegram" system specification, V3.6
(https://www.enocean-alliance.org/st/). A SIGNAL telegram reports a generic
system condition (remaining energy, firmware/hardware revision, a periodic
"still alive" heartbeat, debug receive-quality data, or a manufacturer
Product ID) independently of the sender's main EEP::

    RORG (0xD0) | MID (1 byte) | data for this MID (0..13 bytes) | sender ID | status

A device that implements the feature sends these telegrams on its own
initiative. This module only ever decodes what arrives on the air; nothing
in this integration requests or triggers a SIGNAL telegram. In particular,
MID 0x0A (RX-channel quality) is normally only produced once a transmitter's
debug "RSSI test mode" has been turned on with a dedicated ESP3 write
command (``CO_WR_RSSITESTMODE`` on EnOcean TCM 515/615 modules); the TCM 515
user manual explicitly warns that this "significantly increases the radio
traffic" and "shall not be used in production devices". This integration
never sends that command, so a MID 0x0A telegram is only decoded if some
other party already has that debug mode enabled.

Decoded facts are display-only: they are never written back into a row's
declared ``radio_metadata`` (profile identity stays an explicit, user-
confirmed fact, never something inferred from a passively received frame).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from .enocean_library.protocol.constants import RORG

MID_ENERGY_STATUS: Final = 0x06
MID_REVISION: Final = 0x07
MID_HEARTBEAT: Final = 0x08
MID_RX_CHANNEL_QUALITY: Final = 0x0A
MID_PRODUCT_ID: Final = 0x12


def _valid_data(data: Any, length: int, mid: int) -> bool:
    """Check the generic SIGNAL envelope: RORG, MID and exact byte count."""
    if (
        not isinstance(data, (list, tuple))
        or len(data) != length
        or data[0] != RORG.SIGNAL
        or data[1] != mid
    ):
        return False
    return all(
        isinstance(byte, int) and not isinstance(byte, bool) and 0 <= byte <= 0xFF
        for byte in data
    )


@dataclass(frozen=True, slots=True)
class EnergyStatus:
    """MID 0x06: remaining energy, 1..100 %, or 0 for "gasp of death"."""

    percent: int


def decode_energy_status(data: Any) -> EnergyStatus | None:
    """Decode MID 0x06 (Energy status of the device).

    0 means "last message due to power loss" (critically depleted); 101..255
    are reserved and decode to None rather than being guessed at.
    """
    if not _valid_data(data, 8, MID_ENERGY_STATUS):
        return None
    raw = data[2]
    if raw > 100:
        return None
    return EnergyStatus(raw)


@dataclass(frozen=True, slots=True)
class Revision:
    """MID 0x07: SW/HW version, each formatted ``A.B.C.D`` (0xAABBCCDD)."""

    sw_version: str
    hw_version: str


def _format_version(data: Any, offset: int) -> str:
    return ".".join(str(byte) for byte in data[offset : offset + 4])


def decode_revision(data: Any) -> Revision | None:
    """Decode MID 0x07 (Revision of the device: SW version then HW version)."""
    if not _valid_data(data, 15, MID_REVISION):
        return None
    return Revision(_format_version(data, 2), _format_version(data, 6))


def is_heartbeat(data: Any) -> bool:
    """Return whether ``data`` is a MID 0x08 heartbeat ("still alive") telegram."""
    return _valid_data(data, 7, MID_HEARTBEAT)


@dataclass(frozen=True, slots=True)
class RxChannelQuality:
    """MID 0x0A: debug link quality, normally only seen in RSSI test mode.

    ``reported_sender_id`` is the EURID of the telegram whose quality is
    being reported, which is generally *not* the sender of this SIGNAL
    telegram itself.
    """

    reported_sender_id: int
    dbm_worst: int | None
    dbm_best: int | None
    subtelegram_count: int | None
    max_repeater_level: int | None


def decode_rx_channel_quality(data: Any) -> RxChannelQuality | None:
    """Decode MID 0x0A (RX-channel quality)."""
    if not _valid_data(data, 14, MID_RX_CHANNEL_QUALITY):
        return None
    reported_id = (data[2] << 24) | (data[3] << 16) | (data[4] << 8) | data[5]
    dbm_worst = 127 - data[6] if data[6] != 0xFF else None
    dbm_best = 127 - data[7] if data[7] != 0xFF else None
    subtelegram_raw = data[8] >> 4
    repeater_raw = data[8] & 0x0F
    return RxChannelQuality(
        reported_sender_id=reported_id,
        dbm_worst=dbm_worst,
        dbm_best=dbm_best,
        subtelegram_count=subtelegram_raw or None,
        max_repeater_level=repeater_raw if repeater_raw <= 2 else None,
    )


@dataclass(frozen=True, slots=True)
class ProductId:
    """MID 0x12: EnOcean Alliance manufacturer ID and product reference."""

    manufacturer_id: int
    product_reference: int


def decode_product_id(data: Any) -> ProductId | None:
    """Decode MID 0x12 (Product ID: 0xMMMM_PPPPPPPP)."""
    if not _valid_data(data, 13, MID_PRODUCT_ID):
        return None
    manufacturer_id = (data[2] << 8) | data[3]
    product_reference = (data[4] << 24) | (data[5] << 16) | (data[6] << 8) | data[7]
    return ProductId(manufacturer_id, product_reference)


@dataclass(frozen=True, slots=True)
class SignalSnapshot:
    """One decoded SIGNAL telegram, tagged by which MID it carried."""

    mid: int
    energy_status: EnergyStatus | None = None
    revision: Revision | None = None
    heartbeat: bool = False
    rx_channel_quality: RxChannelQuality | None = None
    product_id: ProductId | None = None


def decode_signal_telegram(data: Any) -> SignalSnapshot | None:
    """Decode any of the five supported SIGNAL (RORG 0xD0) MIDs, or None.

    Any other MID, or malformed data, decodes to None rather than being
    guessed at; the five handled here are exactly the ones in the roadmap
    (energy, revision, heartbeat, RX quality, Product ID).
    """
    if not isinstance(data, (list, tuple)) or len(data) < 2 or data[0] != RORG.SIGNAL:
        return None
    mid = data[1]
    if (energy := decode_energy_status(data)) is not None:
        return SignalSnapshot(mid, energy_status=energy)
    if (revision := decode_revision(data)) is not None:
        return SignalSnapshot(mid, revision=revision)
    if is_heartbeat(data):
        return SignalSnapshot(mid, heartbeat=True)
    if (quality := decode_rx_channel_quality(data)) is not None:
        return SignalSnapshot(mid, rx_channel_quality=quality)
    if (product := decode_product_id(data)) is not None:
        return SignalSnapshot(mid, product_id=product)
    return None

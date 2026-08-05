"""Bounded, passive, in-memory observations of received EnOcean senders."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .const import DATA_DEVICE_INBOX, DATA_ENOCEAN
from .enocean_library.protocol.constants import RORG

MAX_UNCONFIGURED_SENDERS = 64
_REPEATER_RORGS = frozenset((int(RORG.RPS), int(RORG.BS1), int(RORG.BS4)))


@dataclass(frozen=True, slots=True)
class PacketObservation:
    """Minimal immutable packet snapshot transferred to the HA loop."""

    sender_id: int
    rorg: int
    rssi_dbm: int | None
    repeater_count: int | None


@dataclass(slots=True)
class InboxEntry:
    """Latest radio facts for one sender; never persisted or logged."""

    last_seen: datetime
    packet_count: int = 1
    last_rssi_dbm: int | None = None
    last_repeater_count: int | None = None
    rorgs: set[int] = field(default_factory=set)


def packet_observation(packet: Any) -> PacketObservation | None:
    """Copy bounded radio facts without retaining the mutable packet."""
    try:
        sender_id = packet.sender_int
        rorg = int(packet.rorg)
        rssi = packet.dBm
        status = packet.status
    except (AttributeError, TypeError, ValueError):
        return None
    if (
        isinstance(sender_id, bool)
        or not isinstance(sender_id, int)
        or not 0 <= sender_id <= 0xFFFFFFFF
        or not 0 <= rorg <= 0xFF
    ):
        return None
    rssi_dbm = rssi if isinstance(rssi, int) and not isinstance(rssi, bool) else None
    repeater_count = None
    if (
        rorg in _REPEATER_RORGS
        and isinstance(status, int)
        and not isinstance(status, bool)
    ):
        # ERP1 status bits 0..3 are the repeater counter for RPS/1BS/4BS.
        repeater_count = status & 0x0F
    return PacketObservation(sender_id, rorg, rssi_dbm, repeater_count)


class DeviceInbox:
    """LRU registry whose unknown-sender portion is strictly bounded."""

    def __init__(self, configured_ids) -> None:
        """Accept a live callable so UI/YAML changes take effect immediately."""
        self._configured_ids = configured_ids
        self._entries: OrderedDict[int, InboxEntry] = OrderedDict()

    def observe(self, observation: PacketObservation) -> None:
        """Update one observation on Home Assistant's event-loop thread."""
        now = datetime.now(UTC)
        entry = self._entries.pop(observation.sender_id, None)
        if entry is None:
            entry = InboxEntry(now)
        else:
            entry.last_seen = now
            entry.packet_count += 1
        entry.last_rssi_dbm = observation.rssi_dbm
        entry.last_repeater_count = observation.repeater_count
        entry.rorgs.add(observation.rorg)
        self._entries[observation.sender_id] = entry
        self._evict_unknown_lru()

    def _evict_unknown_lru(self) -> None:
        # Resolving the configured set re-validates every UI row, so it must not
        # run for each received telegram. While the whole registry fits inside
        # the cap the unknown subset trivially does too, and nothing can be due
        # for eviction.
        if len(self._entries) <= MAX_UNCONFIGURED_SENDERS:
            return
        configured = set(self._configured_ids())
        unknown = [sender for sender in self._entries if sender not in configured]
        for sender in unknown[:-MAX_UNCONFIGURED_SENDERS]:
            self._entries.pop(sender, None)

    def get(self, sender_id: int) -> InboxEntry | None:
        """Return current facts without changing LRU recency."""
        return self._entries.get(sender_id)

    @property
    def observed_senders_count(self) -> int:
        """Return the sole privacy-safe diagnostic view."""
        return len(self._entries)

    def clear(self) -> None:
        """Drop every sender observation on unload/reload."""
        self._entries.clear()


def get_device_inbox(hass) -> DeviceInbox | None:
    """Return the entry-owned inbox when the integration is loaded."""
    return getattr(hass, "data", {}).get(DATA_ENOCEAN, {}).get(DATA_DEVICE_INBOX)

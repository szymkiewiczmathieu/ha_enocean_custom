"""Sender ID pool: Base ID + offset, computed in software, never CO_WR_IDBASE.

An EnOcean dongle owns 128 consecutive IDs: its Base ID (offset 0, already
used by ``switch.py`` for direct D2 addressing) plus 127 more
(offset 1..127). A5-38-08 dimmer teach-in and the SRC-D08 climate panel
emulation each need a *distinct, stable* virtual transmitter identity in
that range, separate from each other and from the dongle itself, so the
actuator they teach-in to can tell them apart.

This module only ever computes an offset inside the dongle's own,
already-read Base ID (read once at startup with the local, radio-silent
``CO_RD_IDBASE`` command); it never writes ``CO_WR_IDBASE`` and never
proposes an ID the dongle has not already claimed for itself. Per FHEM's own
warning, a physical EnOcean address cannot be emulated; this module never
invents an ID outside the dongle's own allocated range.

Allocation is a one-time *suggestion* offered when a new row is created,
pre-filling a field the operator can still override; nothing here ever
reassigns an existing row's sender_id, because the receiving actuator's
teach-in binding depends on it staying exactly what it was taught.
"""

from __future__ import annotations

from typing import Any, Final

from .enocean_library.utils import combine_hex

MIN_OFFSET: Final = 1
MAX_OFFSET: Final = 127


def _valid_base_id(base_id: Any) -> bool:
    return (
        isinstance(base_id, (list, tuple))
        and len(base_id) == 4
        and all(
            isinstance(byte, int) and not isinstance(byte, bool) and 0 <= byte <= 0xFF
            for byte in base_id
        )
    )


def offset_sender_id(base_id: Any, offset: int) -> list[int] | None:
    """Return ``base_id + offset`` as four bytes, or None if either is invalid."""
    if (
        not _valid_base_id(base_id)
        or isinstance(offset, bool)
        or not isinstance(offset, int)
        or not MIN_OFFSET <= offset <= MAX_OFFSET
    ):
        return None
    value = combine_hex(list(base_id)) + offset
    if value > 0xFFFFFFFF:
        return None
    return [
        (value >> 24) & 0xFF,
        (value >> 16) & 0xFF,
        (value >> 8) & 0xFF,
        value & 0xFF,
    ]


def offset_of(base_id: Any, sender_id: Any) -> int | None:
    """Return the pool offset of ``sender_id``, or None if outside 1..127."""
    if not _valid_base_id(base_id) or not _valid_base_id(sender_id):
        return None
    diff = combine_hex(list(sender_id)) - combine_hex(list(base_id))
    return diff if MIN_OFFSET <= diff <= MAX_OFFSET else None


def allocate_sender_id(base_id: Any, used: Any) -> list[int] | None:
    """Return the lowest free ``base_id + offset`` not already used, or None.

    ``used`` is every sender_id already relied upon by an existing row,
    regardless of platform or how it was obtained: a collision would be
    just as wrong whether the clashing row is a light, a climate actor, or
    an RPS-emulated switch. None is returned both when ``base_id`` is not
    yet resolved and when the (vanishingly unlikely) 127-entry pool is full;
    either way the caller falls back to asking the operator to type one.
    """
    if not isinstance(used, (list, tuple, set, frozenset)):
        return None
    used_ints = {
        combine_hex(list(candidate)) for candidate in used if _valid_base_id(candidate)
    }
    for offset in range(MIN_OFFSET, MAX_OFFSET + 1):
        candidate = offset_sender_id(base_id, offset)
        if candidate is None:
            return None
        if combine_hex(candidate) not in used_ints:
            return candidate
    return None

"""Diagnostics exports, pinned against committed JSON snapshots (M9).

Complements tests/test_device_diagnostics.py (stdlib unittest, asserts
specific keys and redaction). This file instead freezes the *whole*
exported shape for one representative config entry and device, so any
accidental new field, renamed key or redaction regression shows up as a
snapshot diff even if no individual assertion was written for it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_custom.const import DOMAIN
from custom_components.enocean_custom.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)
from custom_components.enocean_custom.enocean_library.protocol.constants import (
    PACKET,
    RORG,
)
from custom_components.enocean_custom.enocean_library.protocol.packet import Packet
from custom_components.enocean_custom.identity import gateway_device_identifier
from custom_components.enocean_custom.inbox import DeviceInbox, packet_observation
from custom_components.enocean_custom.schema import CONF_UI_DEVICES

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def _assert_matches_snapshot(name: str, export: object) -> None:
    """Compare an export with ``snapshots/<name>.json``.

    Plain JSON instead of syrupy: syrupy failed to locate a committed
    ``.ambr`` file on GitHub runners only (same versions, same file), so the
    pinned shape now lives in a reviewable JSON file. Regenerate with
    ``ENOCEAN_UPDATE_SNAPSHOTS=1 python -m pytest``.
    """
    received = json.loads(json.dumps(export, sort_keys=True, default=str))
    path = SNAPSHOT_DIR / f"{name}.json"
    if os.environ.get("ENOCEAN_UPDATE_SNAPSHOTS") == "1":
        path.write_text(
            json.dumps(received, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    assert path.is_file(), f"missing snapshot {path}"
    assert received == json.loads(path.read_text(encoding="utf-8"))


SENDER = [0x05, 0x8F, 0x22, 0x33]
NAME = "Living room rocker"

# The inbox's last_seen is wall-clock time; freeze it so the snapshot does
# not go stale (and fail) the next time this suite runs.
pytestmark = pytest.mark.freeze_time("2026-01-01T00:00:00+00:00")


def _parsed_vld(sender):
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.VLD, 0x04, 0x00, 0x64, *sender, 0x00],
        optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 61, 0x00],
    ).build()
    return Packet.parse_msg(bytearray(wire))[2]


async def _entry_with_one_sender(hass) -> tuple[MockConfigEntry, object]:
    rows = [
        {
            "id": SENDER,
            "platform": "switch",
            "name": NAME,
            "channel": 0,
            "switch_type": "RPS",
        }
    ]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"device": "/dev/serial/by-id/private-for-the-snapshot"},
        options={CONF_UI_DEVICES: rows, "availability": {"enabled": True}},
        minor_version=2,
    )
    entry.add_to_hass(hass)
    inbox = DeviceInbox(set)
    inbox.observe(packet_observation(_parsed_vld(SENDER)))
    entry.runtime_data = SimpleNamespace(
        entry_id=entry.entry_id,
        inbox=inbox,
        diagnostics=lambda: {
            "health": "ok",
            "base_id_resolved": True,
            "network_url": False,
        },
    )
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "058F2233")},
        name=NAME,
    )
    return entry, device


async def test_config_entry_diagnostics_snapshot(hass) -> None:
    entry, _device = await _entry_with_one_sender(hass)
    export = await async_get_config_entry_diagnostics(hass, entry)
    _assert_matches_snapshot("config_entry_diagnostics_snapshot", export)


async def test_device_diagnostics_snapshot(hass) -> None:
    entry, device = await _entry_with_one_sender(hass)
    export = await async_get_device_diagnostics(hass, entry, device)
    _assert_matches_snapshot("device_diagnostics_snapshot", export)


async def test_gateway_device_diagnostics_snapshot(hass) -> None:
    entry, _sender_device = await _entry_with_one_sender(hass)
    device_registry = dr.async_get(hass)
    gateway_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, gateway_device_identifier(entry.entry_id))},
    )
    export = await async_get_device_diagnostics(hass, entry, gateway_device)
    _assert_matches_snapshot("gateway_device_diagnostics_snapshot", export)

"""No blocking call on the event loop, through the real Home Assistant loader.

Every test of this directory already runs under the autouse guard of
``conftest.py`` (cold profile catalog, blocking primitives wrapped). The tests
here drive the paths that used to be reachable before the catalog was warm:
an entry whose dongle cannot be opened, and the options migration.
"""

from __future__ import annotations

from unittest.mock import patch

import serial
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_subentries import FakeDongle

import custom_components.enocean_custom as integration
from custom_components.enocean_custom import eep_catalog
from custom_components.enocean_custom.const import DOMAIN
from custom_components.enocean_custom.device_rows import subentries_by_sender
from custom_components.enocean_custom.panel import build_panel_snapshot
from custom_components.enocean_custom.schema import CONF_UI_DEVICES

SENDER = [0x01, 0x02, 0x03, 0x04]


def _generic_row() -> dict:
    """A sensor row whose validation needs the generic profile catalog."""
    return {
        "id": SENDER,
        "platform": "sensor",
        "name": "Room",
        "device_class": "eep",
        "radio_metadata": {"eep": "A5-04-01"},
    }


class MissingDongle:
    """A serial owner whose port cannot be opened (unplugged at start-up)."""

    def __init__(self, hass, serial_path, entry_id=None) -> None:
        raise serial.SerialException("could not open port")


def _entry(hass: HomeAssistant, *, minor_version: int) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"device": "/dev/serial/by-id/fake"},
        options={CONF_UI_DEVICES: [_generic_row()]},
        version=1,
        minor_version=minor_version,
    )
    entry.add_to_hass(hass)
    return entry


async def test_options_flow_after_a_failed_dongle_open_reads_nothing_on_the_loop(
    hass: HomeAssistant, event_loop_guard
) -> None:
    """Dongle unplugged at start-up, then *Configure*: the catalog is warm."""
    entry = _entry(hass, minor_version=3)
    with patch.object(integration, "EnOceanDongle", MissingDongle):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY

        # The menu step validates every stored row, on the event loop.
        result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert event_loop_guard.violations == []
    assert eep_catalog.built_in_profiles.cache_info().currsize == 1


async def test_migrating_a_generic_row_reads_nothing_on_the_loop(
    hass: HomeAssistant, event_loop_guard
) -> None:
    """The minor-3 migration validates rows before the entry is set up."""
    entry = _entry(hass, minor_version=2)
    with patch.object(integration, "EnOceanDongle", FakeDongle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.minor_version == 3
    assert event_loop_guard.violations == []
    # The generic row was valid during the migration: it owns a subentry.
    assert 0x01020304 in subentries_by_sender(entry)


async def test_loaded_entry_flows_and_panel_read_nothing_on_the_loop(
    hass: HomeAssistant, event_loop_guard
) -> None:
    """Set-up, the options menu, the panel snapshot and unload, from cold."""
    entry = _entry(hass, minor_version=3)
    with patch.object(integration, "EnOceanDongle", FakeDongle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is config_entries.ConfigEntryState.LOADED

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.MENU
        hass.config_entries.options.async_abort(result["flow_id"])

        gateway = entry.runtime_data
        gateway.telegram_monitor = None
        snapshot = build_panel_snapshot(hass, entry, gateway, anonymize=True)
        assert [device["eep"] for device in snapshot["devices"]] == ["A5-04-01"]

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert event_loop_guard.violations == []

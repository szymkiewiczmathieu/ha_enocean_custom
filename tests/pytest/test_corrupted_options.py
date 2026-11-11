"""A corrupted ``ui_devices`` option must never keep the entry from loading.

Options are hand-editable in ``.storage``. The row validators already skip a
malformed row and treat a non-list as "no rows"; the entry set-up, the device
page and the options flow must do the same instead of failing on ``null``, a
number or a boolean.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_subentries import FakeDongle

import custom_components.enocean_custom as integration
from custom_components.enocean_custom.const import DOMAIN
from custom_components.enocean_custom.schema import CONF_UI_DEVICES

CORRUPTED = [None, 5, True, "text", {"a": 1}, [None, 3, "x", {"id": "nope"}]]


async def _load(hass: HomeAssistant, stored: object) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"device": "/dev/serial/by-id/fake"},
        options={CONF_UI_DEVICES: stored},
        version=1,
        minor_version=3,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.parametrize("stored", CORRUPTED, ids=repr)
async def test_entry_loads_whatever_ui_devices_holds(
    hass: HomeAssistant, stored: object
) -> None:
    with patch.object(integration, "EnOceanDongle", FakeDongle):
        entry = await _load(hass, stored)
        assert entry.state is config_entries.ConfigEntryState.LOADED
        # Nothing is repaired or dropped behind the operator's back.
        assert entry.options[CONF_UI_DEVICES] == stored
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("stored", [None, 5, True], ids=repr)
async def test_options_flow_survives_a_corrupted_list(
    hass: HomeAssistant, stored: object
) -> None:
    with patch.object(integration, "EnOceanDongle", FakeDongle):
        entry = await _load(hass, stored)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.MENU
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "manage"}
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "no_ui_devices"

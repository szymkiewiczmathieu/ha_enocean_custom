"""Device subentries through the real loader, flow manager and platforms (M9/A1).

Where ``tests/test_subentry_lifecycle.py`` stands the platforms in, this file
lets Home Assistant load the integration for real (dongle replaced by a fake
serial owner): the minor-3 migration runs inside ``async_setup``, the
platforms create entities under their subentry, the *Add device* subentry
flow and a reconfigure run through ``hass.config_entries.subentries``, and
adding or removing a device is proven to leave ``async_setup_entry`` at one
call -- no reload.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.enocean_custom as integration
from custom_components.enocean_custom.const import DOMAIN, SUBENTRY_TYPE_DEVICE
from custom_components.enocean_custom.device_rows import (
    device_subentries,
    device_subentry_id,
    subentries_by_sender,
    subentry_rows,
)
from custom_components.enocean_custom.schema import CONF_UI_DEVICES

SENDER = [0x05, 0x8F, 0x22, 0x33]
OTHER = [0x05, 0x8F, 0x22, 0x34]


class FakeDongle:
    """A serial owner that never opens a port and accepts every command."""

    def __init__(self, hass, serial_path, entry_id=None) -> None:
        self.hass = hass
        self.serial_path = serial_path
        self.entry_id = entry_id
        self.available = True
        self.base_id = [0xFF, 0x87, 0xCA, 0x00]
        self.gateway_info = None
        self.repeater = None
        self.received_radio_packets = 0
        self.last_packet_at = None
        self._inbox = None
        self.sent = []

    def set_inbox(self, inbox):
        self._inbox = inbox

    @property
    def inbox(self):
        return self._inbox

    async def async_setup(self):
        return None

    async def async_unload(self):
        return True

    def send(self, packet, response_callback=None, written_callback=None):
        self.sent.append(packet)
        return True

    def retry_base_id(self):
        return False

    def diagnostics(self):
        return {"base_id_resolved": True}


def _row(sender, channel=0, name="Relay"):
    return {
        "id": sender,
        "platform": "switch",
        "name": name,
        "channel": channel,
        "switch_type": "default",
    }


@pytest.fixture
def fake_dongle():
    with patch.object(integration, "EnOceanDongle", FakeDongle):
        yield


@pytest.fixture
def setup_calls():
    """Count ``async_setup_entry`` calls: one means no reload happened."""
    calls: list[str] = []
    original = integration.async_setup_entry

    async def counting(hass, entry):
        calls.append(entry.entry_id)
        return await original(hass, entry)

    with patch.object(integration, "async_setup_entry", counting):
        yield calls


async def _load(hass: HomeAssistant, rows, *, minor_version=2) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"device": "/dev/serial/by-id/fake"},
        options={CONF_UI_DEVICES: rows},
        version=1,
        minor_version=minor_version,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.LOADED
    return entry


async def test_migration_then_setup_puts_every_radio_entity_in_its_subentry(
    hass: HomeAssistant, fake_dongle, setup_calls
) -> None:
    rows = [_row(SENDER, 0, "Stairs"), _row(SENDER, 1, "Stairs bottom"), _row(OTHER)]
    entry = await _load(hass, rows, minor_version=2)

    assert entry.minor_version == 3
    subentries = device_subentries(entry)
    assert len(subentries) == 2
    by_sender = subentries_by_sender(entry)
    assert subentry_rows(by_sender[0x058F2233])[0]["name"] == "Stairs"
    assert len(subentry_rows(by_sender[0x058F2233])) == 2
    # The mirror is untouched by the migration.
    assert entry.options[CONF_UI_DEVICES] == rows

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    entities = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    switches = [e for e in entities if e.domain == "switch"]
    assert {e.unique_id for e in switches} == {"93266483-0", "93266483-1", "93266484-0"}
    for entity in entities:
        device = device_registry.async_get(entity.device_id)
        sender = next(
            (
                identifier
                for domain, identifier in device.identifiers
                if domain == DOMAIN
            ),
        )
        if sender.startswith("gateway:"):
            assert entity.config_subentry_id is None
            assert device_subentry_id(device, entry.entry_id) is None
        else:
            expected = by_sender[int(sender, 16)].subentry_id
            assert entity.config_subentry_id == expected, entity.entity_id
            assert device_subentry_id(device, entry.entry_id) == expected
    assert hass.states.get("switch.stairs") is not None
    assert setup_calls == [entry.entry_id]


async def test_add_device_subentry_flow_creates_entities_without_a_reload(
    hass: HomeAssistant, fake_dongle, setup_calls
) -> None:
    entry = await _load(hass, [_row(SENDER, 0, "Stairs")], minor_version=3)
    assert entry.supported_subentry_types == {
        SUBENTRY_TYPE_DEVICE: {"supports_reconfigure": True}
    }

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DEVICE),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    # A malformed ID is refused on the form, nothing is created.
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Kitchen", "qr_code": "not-an-id", "platform": "binary_sensor"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"qr_code": "invalid_qr_code"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Kitchen", "qr_code": "05:8F:22:34", "platform": "binary_sensor"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "details"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"device_class": "door"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    subentry = subentries_by_sender(entry)[0x058F2234]
    assert subentry.title == "Kitchen"
    assert subentry.unique_id == "058F2234"
    rows = subentry_rows(subentry)
    assert len(rows) == 1
    assert rows[0]["platform"] == "binary_sensor"
    assert rows[0]["device_class"] == "door"
    # Mirror: the options flow and a 2.10.x downgrade both see the row.
    assert [row["name"] for row in entry.options[CONF_UI_DEVICES]] == [
        "Stairs",
        "Kitchen",
    ]
    # The entities exist, attached to the subentry, and the entry was never
    # reloaded: async_setup_entry ran exactly once.
    entity_registry = er.async_get(hass)
    door = entity_registry.async_get_entity_id("binary_sensor", DOMAIN, "93266484-door")
    assert door is not None
    assert entity_registry.async_get(door).config_subentry_id == subentry.subentry_id
    assert hass.states.get(door) is not None
    event = entity_registry.async_get_entity_id(
        "event", DOMAIN, "93266484-rocker-event"
    )
    assert event is not None
    assert setup_calls == [entry.entry_id]

    # The same ID a second time is refused as already configured.
    again = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DEVICE),
        context={"source": config_entries.SOURCE_USER},
    )
    again = await hass.config_entries.subentries.async_configure(
        again["flow_id"],
        {"name": "Twice", "qr_code": "05:8F:22:34", "platform": "binary_sensor"},
    )
    assert again["type"] is FlowResultType.ABORT
    assert again["reason"] == "already_configured"


async def test_reconfigure_renames_the_device_and_recreates_its_entities(
    hass: HomeAssistant, fake_dongle, setup_calls
) -> None:
    entry = await _load(hass, [_row(SENDER, 0, "Stairs")], minor_version=3)
    subentry = subentries_by_sender(entry)[0x058F2233]
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    switch_id = entity_registry.async_get_entity_id("switch", DOMAIN, "93266483-0")
    assert switch_id == "switch.stairs"
    device = device_registry.async_get(entity_registry.async_get(switch_id).device_id)
    assert device.name == "Stairs"

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DEVICE),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry.subentry_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Staircase", "row": "0", "manual_eep": ""}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    assert subentry.title == "Staircase"
    assert subentry_rows(subentry)[0]["name"] == "Staircase"
    assert entry.options[CONF_UI_DEVICES][0]["name"] == "Staircase"
    # Same entity, same device: identity is never touched by a rename.
    assert (
        entity_registry.async_get_entity_id("switch", DOMAIN, "93266483-0") == switch_id
    )
    assert entity_registry.async_get(switch_id).device_id == device.id
    assert device_registry.async_get(device.id).name == "Staircase"
    assert hass.states.get(switch_id) is not None
    assert setup_calls == [entry.entry_id]


async def test_removing_a_device_from_its_page_removes_its_subentry_only(
    hass: HomeAssistant, fake_dongle, setup_calls
) -> None:
    entry = await _load(
        hass, [_row(SENDER, 0, "Stairs"), _row(OTHER, 0, "Hall")], minor_version=3
    )
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    hall_switch = entity_registry.async_get("switch.hall")
    hall_device = device_registry.async_get(hall_switch.device_id)

    assert await integration.async_remove_config_entry_device(hass, entry, hall_device)
    await hass.async_block_till_done()

    assert device_registry.async_get(hall_device.id) is None
    assert entity_registry.async_get("switch.hall") is None
    assert hass.states.get("switch.hall") is None
    assert list(subentries_by_sender(entry)) == [0x058F2233]
    assert [row["name"] for row in entry.options[CONF_UI_DEVICES]] == ["Stairs"]
    # The other device is untouched and the entry was not reloaded.
    assert hass.states.get("switch.stairs") is not None
    assert setup_calls == [entry.entry_id]


async def test_options_flow_style_write_adds_a_device_without_a_reload(
    hass: HomeAssistant, fake_dongle, setup_calls
) -> None:
    entry = await _load(hass, [_row(SENDER, 0, "Stairs")], minor_version=3)
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_UI_DEVICES: [*entry.options[CONF_UI_DEVICES], _row(OTHER, 0, "Hall")],
        },
    )
    await hass.async_block_till_done()
    assert 0x058F2234 in subentries_by_sender(entry)
    assert hass.states.get("switch.hall") is not None
    hall = er.async_get(hass).async_get("switch.hall")
    assert (
        hall.config_subentry_id == subentries_by_sender(entry)[0x058F2234].subentry_id
    )
    assert setup_calls == [entry.entry_id]
    # Unloading tears everything down cleanly, subentry entities included.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.NOT_LOADED

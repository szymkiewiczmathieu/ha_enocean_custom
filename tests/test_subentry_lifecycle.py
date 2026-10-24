"""Device subentries at runtime: the listener keeps rows, subentries and entities in step.

``async_setup_entry`` installs one update listener. These tests drive it the
way Home Assistant does -- through real ``ConfigEntries`` writes on a real
``HomeAssistant`` instance -- with the serial dongle and the platform
forwarding replaced by stand-ins, and observe the three things the listener
owns: the ``device`` subentries, the ``ui_devices`` mirror and the
``SIGNAL_SUBENTRY_SETUP`` dispatch that makes the platforms create a device's
entities without an entry reload.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant, callback
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    import custom_components.enocean_custom as integration
    from custom_components.enocean_custom.const import (
        DOMAIN,
        SIGNAL_SUBENTRY_SETUP,
        SUBENTRY_TYPE_DEVICE,
    )
    from custom_components.enocean_custom.device_rows import (
        build_device_subentry,
        device_subentries,
        legacy_rows,
        subentries_by_sender,
        subentry_rows,
    )
    from custom_components.enocean_custom.schema import (
        CONF_UI_DEVICES,
        UI_DEVICE_SCHEMA,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [0x05, 0x8F, 0x22, 0x33]
OTHER = [0x05, 0x8F, 0x22, 0x34]


def _row(sender, channel=0, name="Relay", platform="switch", **extra):
    base = {"id": sender, "platform": platform, "name": name, "channel": channel}
    if platform == "switch":
        base["switch_type"] = "default"
    return UI_DEVICE_SCHEMA({**base, **extra})


class FakeDongle:
    """What ``async_setup_entry`` needs from the serial owner, and nothing else."""

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

    def diagnostics(self):
        return {}


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class SubentryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.forwarded = AsyncMock(return_value=True)
        self.hass.config_entries.async_forward_entry_setups = self.forwarded
        self.reloads = AsyncMock(return_value=True)
        self.hass.config_entries.async_reload = self.reloads
        self.setups: list[tuple[str, str]] = []

        @callback
        def _record(entry_id, subentry_id):
            self.setups.append((entry_id, subentry_id))

        async_dispatcher_connect(self.hass, SIGNAL_SUBENTRY_SETUP, _record)
        self._dongle_patch = patch.object(integration, "EnOceanDongle", FakeDongle)
        self._dongle_patch.start()

    async def asyncTearDown(self):
        self._dongle_patch.stop()
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _entry(self, rows=(), *, minor_version=3, subentries=()):
        entry = ConfigEntry(
            data={"device": "/dev/test-lifecycle"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=minor_version,
            options={CONF_UI_DEVICES: list(rows)},
            source="user",
            subentries_data=[
                {
                    "data": dict(subentry.data),
                    "subentry_id": subentry.subentry_id,
                    "subentry_type": subentry.subentry_type,
                    "title": subentry.title,
                    "unique_id": subentry.unique_id,
                }
                for subentry in subentries
            ],
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        self.hass.config_entries._entries[entry.entry_id] = entry
        return entry

    async def _setup(self, entry):
        self.assertTrue(await integration.async_setup_entry(self.hass, entry))
        await self.hass.async_block_till_done()
        self.forwarded.assert_awaited_once()
        self.assertEqual(len(entry.update_listeners), 1)
        return entry

    async def test_setup_adopts_rows_left_in_the_options_without_a_subentry(self):
        # A 2.10.x release ran during a downgrade and wrote a new row.
        row = _row(SENDER)
        entry = self._entry([row])
        await self._setup(entry)
        subentries = device_subentries(entry)
        self.assertEqual(len(subentries), 1)
        subentry = next(iter(subentries.values()))
        self.assertEqual(subentry.unique_id, "058F2233")
        self.assertEqual(subentry_rows(subentry), [row])
        self.assertEqual(legacy_rows(entry), [])
        # The platforms read the subentry at setup; no runtime dispatch and no reload.
        self.assertEqual(self.setups, [])
        self.reloads.assert_not_awaited()
        self.assertEqual(entry.options[CONF_UI_DEVICES], [row])

    async def test_options_flow_write_creates_the_subentry_and_sets_it_up(self):
        entry = await self._setup(self._entry([]))
        row = _row(SENDER, name="New relay")
        # Exactly what every guided options flow does on success.
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_UI_DEVICES: [row]}
        )
        await self.hass.async_block_till_done()

        subentries = device_subentries(entry)
        self.assertEqual(len(subentries), 1)
        subentry = next(iter(subentries.values()))
        self.assertEqual(subentry.title, "New relay")
        self.assertEqual(subentry_rows(subentry), [row])
        self.assertEqual(self.setups, [(entry.entry_id, subentry.subentry_id)])
        self.reloads.assert_not_awaited()
        self.assertEqual(entry.options[CONF_UI_DEVICES], [row])

    async def test_second_channel_of_a_sender_rewrites_its_subentry(self):
        first = _row(SENDER, 0, "Stairs")
        entry = await self._setup(self._entry([first]))
        subentry_id = next(iter(device_subentries(entry)))
        second = _row(SENDER, 1, "Stairs bottom")
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_UI_DEVICES: [first, second]}
        )
        await self.hass.async_block_till_done()
        self.assertEqual(list(device_subentries(entry)), [subentry_id])
        self.assertEqual(subentry_rows(entry.subentries[subentry_id]), [first, second])
        self.assertEqual(entry.subentries[subentry_id].title, "Stairs")
        self.assertEqual(self.setups, [(entry.entry_id, subentry_id)])
        self.reloads.assert_not_awaited()

    async def test_removing_the_last_row_removes_the_subentry_and_its_device(self):
        row = _row(SENDER)
        entry = await self._setup(self._entry([row]))
        subentry_id = next(iter(device_subentries(entry)))
        registry = dr.async_get(self.hass)
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=subentry_id,
            identifiers={(DOMAIN, "058F2233")},
            name="Relay",
        )
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_UI_DEVICES: []}
        )
        await self.hass.async_block_till_done()
        self.assertEqual(device_subentries(entry), {})
        self.assertIsNone(registry.async_get(device.id))
        self.assertEqual(entry.options[CONF_UI_DEVICES], [])
        self.assertEqual(self.setups, [])
        self.reloads.assert_not_awaited()

    async def test_subentry_created_by_a_flow_is_mirrored_and_set_up(self):
        entry = await self._setup(self._entry([]))
        row = _row(OTHER, name="From the subentry flow")
        subentry = build_device_subentry([row])
        self.hass.config_entries.async_add_subentry(entry, subentry)
        await self.hass.async_block_till_done()
        self.assertEqual(entry.options[CONF_UI_DEVICES], [row])
        self.assertEqual(self.setups, [(entry.entry_id, subentry.subentry_id)])
        self.reloads.assert_not_awaited()

    async def test_subentry_removed_from_the_integration_page_shrinks_the_mirror(self):
        rows = [_row(SENDER, name="Keep"), _row(OTHER, name="Drop")]
        entry = await self._setup(self._entry(rows))
        dropped = subentries_by_sender(entry)[0x058F2234]
        self.hass.config_entries.async_remove_subentry(entry, dropped.subentry_id)
        await self.hass.async_block_till_done()
        self.assertEqual(entry.options[CONF_UI_DEVICES], [rows[0]])
        self.assertEqual(len(device_subentries(entry)), 1)
        self.assertEqual(self.setups, [])

    async def test_renaming_a_subentry_renames_the_device_and_its_first_row(self):
        row = _row(SENDER, name="Old name")
        entry = await self._setup(self._entry([row]))
        subentry = next(iter(device_subentries(entry).values()))
        registry = dr.async_get(self.hass)
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=subentry.subentry_id,
            identifiers={(DOMAIN, "058F2233")},
            name="Old name",
        )
        # What the frontend's rename does (websocket config_subentry_update).
        self.hass.config_entries.async_update_subentry(
            entry, subentry, title="New name"
        )
        await self.hass.async_block_till_done()
        self.assertEqual(subentry_rows(subentry)[0]["name"], "New name")
        self.assertEqual(entry.options[CONF_UI_DEVICES][0]["name"], "New name")
        self.assertEqual(registry.async_get(device.id).name, "New name")
        self.assertEqual(self.setups, [(entry.entry_id, subentry.subentry_id)])
        self.reloads.assert_not_awaited()

    async def test_other_settings_still_reload_the_entry(self):
        entry = await self._setup(self._entry([_row(SENDER)]))
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, "availability": {"enabled": True}}
        )
        await self.hass.async_block_till_done()
        self.reloads.assert_awaited_once_with(entry.entry_id)
        self.assertEqual(self.setups, [])

    async def test_removing_a_device_from_its_page_removes_its_subentry(self):
        row = _row(SENDER)
        entry = await self._setup(self._entry([row]))
        subentry_id = next(iter(device_subentries(entry)))
        registry = dr.async_get(self.hass)
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=subentry_id,
            identifiers={(DOMAIN, "058F2233")},
            name="Relay",
        )
        self.assertTrue(
            await integration.async_remove_config_entry_device(self.hass, entry, device)
        )
        await self.hass.async_block_till_done()
        self.assertEqual(device_subentries(entry), {})
        self.assertIsNone(registry.async_get(device.id))
        self.assertEqual(entry.options[CONF_UI_DEVICES], [])
        # The gateway device is never removable.
        gateway = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"gateway:{entry.entry_id}")},
        )
        self.assertFalse(
            await integration.async_remove_config_entry_device(
                self.hass, entry, gateway
            )
        )

    async def test_hand_edited_subentry_rows_of_another_sender_are_ignored(self):
        row = _row(SENDER)
        subentry = build_device_subentry([row])
        poisoned = SimpleNamespace(
            data={"rows": [row, _row(OTHER)]},
            subentry_type=SUBENTRY_TYPE_DEVICE,
            unique_id=subentry.unique_id,
            title=subentry.title,
            subentry_id=subentry.subentry_id,
        )
        self.assertEqual(subentry_rows(poisoned), [row])


if __name__ == "__main__":
    unittest.main()

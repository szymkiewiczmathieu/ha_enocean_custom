"""Opt-in silence detection, Repair issues and device removal.

A device that stops transmitting must be visible when it matters (smoke
detectors, contacts, handles) without turning every rarely pressed rocker
unavailable. Detection is therefore off by default, bounded per category and
configurable; rockers, actuators and climate controllers never time out.
"""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant import loader
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import issue_registry as ir

    from custom_components.enocean_custom import (
        async_remove_config_entry_device,
        async_remove_stale_devices,
        binary_sensor,
        learn,
        sensor,
    )
    from custom_components.enocean_custom.availability import (
        CATEGORY_CONTACT,
        CATEGORY_HANDLE,
        CATEGORY_SENSOR,
        CATEGORY_SMOKE,
        DEFAULT_MINUTES,
        availability_settings,
        silence_category,
    )
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        RORG,
    )
    from custom_components.enocean_custom.identity import gateway_device_identifier
    from custom_components.enocean_custom.schema import CONF_UI_DEVICES

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class SettingsTests(unittest.TestCase):
    """Settings default to off and tolerate hand-edited garbage."""

    def test_defaults_are_off_with_documented_minutes(self):
        settings = availability_settings({})
        self.assertFalse(settings.enabled)
        self.assertEqual(dict(settings.minutes), dict(DEFAULT_MINUTES))
        self.assertEqual(DEFAULT_MINUTES[CATEGORY_SENSOR], 180)
        self.assertEqual(DEFAULT_MINUTES[CATEGORY_CONTACT], 24 * 60)
        self.assertEqual(DEFAULT_MINUTES[CATEGORY_SMOKE], 0)
        self.assertEqual(DEFAULT_MINUTES[CATEGORY_HANDLE], 0)
        for category in (CATEGORY_SENSOR, CATEGORY_CONTACT, None):
            self.assertIsNone(settings.timeout_for(category), "disabled never expires")

    def test_enabled_settings_time_out_only_non_zero_categories(self):
        settings = availability_settings(
            {"availability": {"enabled": True, "sensor_minutes": 30, "bogus": 1}}
        )
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.timeout_for(CATEGORY_SENSOR), timedelta(minutes=30))
        self.assertEqual(settings.timeout_for(CATEGORY_CONTACT), timedelta(hours=24))
        self.assertIsNone(settings.timeout_for(CATEGORY_SMOKE), "0 minutes = never")
        self.assertIsNone(settings.timeout_for(None))
        self.assertEqual(
            settings.as_options(),
            {
                "enabled": True,
                "sensor_minutes": 30,
                "contact_minutes": 1440,
                "smoke_minutes": 0,
                "handle_minutes": 0,
            },
        )

    def test_malformed_settings_fall_back_to_defaults(self):
        for raw in (
            {"availability": {"sensor_minutes": -1}},
            {"availability": {"enabled": "yes"}},
            {"availability": "on"},
            {"availability": {"sensor_minutes": 1.5}},
            None,
        ):
            with self.subTest(raw=raw):
                self.assertFalse(availability_settings(raw).enabled)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class CategoryTests(unittest.TestCase):
    """Rows are classified from what they state; nothing is inferred."""

    def test_categories(self):
        cases = [
            ({"platform": "binary_sensor", "device_class": "smoke"}, CATEGORY_SMOKE),
            (
                {"platform": "binary_sensor", "radio_metadata": {"eep": "F6-05-02"}},
                CATEGORY_SMOKE,
            ),
            (
                {"platform": "binary_sensor", "radio_metadata": {"eep": "A5-14-01"}},
                CATEGORY_CONTACT,
            ),
            ({"platform": "binary_sensor", "device_class": None}, None),
            ({"platform": "binary_sensor", "device_class": "door"}, None),
            ({"platform": "sensor", "device_class": "windowhandle"}, CATEGORY_HANDLE),
            ({"platform": "sensor", "device_class": "contact"}, CATEGORY_CONTACT),
            (
                {"platform": "sensor", "device_class": "shuttercontact"},
                CATEGORY_CONTACT,
            ),
            ({"platform": "sensor", "device_class": "temperature"}, CATEGORY_SENSOR),
            ({"platform": "sensor", "device_class": "powersensor"}, CATEGORY_SENSOR),
            ({"platform": "switch", "switch_type": "RPS"}, None),
            ({"platform": "light"}, None),
            ({"platform": "climate"}, None),
        ]
        for row, expected in cases:
            with self.subTest(row=row):
                self.assertEqual(silence_category(row), expected)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class EntitySilenceTests(unittest.IsolatedAsyncioTestCase):
    """The entity state machine: grace period, silence, repair, recovery."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        # Writing state for an entity without a platform makes Home Assistant
        # look up the integration's issue tracker; the loader must exist.
        loader.async_setup(self.hass)
        await ir.async_load(self.hass)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        entry = ConfigEntry(
            data={"device": "/dev/test"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=2,
            options={},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        entry.runtime_data = SimpleNamespace(available=True, entry_id=entry.entry_id)
        self.hass.config_entries._entries[entry.entry_id] = entry

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def _entity(self, timeout, *, repair):
        entity = binary_sensor.EnOceanBinarySensor([1, 2, 3, 4], "Pantry", "door")
        entity.hass = self.hass
        entity.entity_id = "binary_sensor.pantry"
        entity.configure_silence(timeout, repair=repair)
        await entity.async_added_to_hass()
        return entity

    def _issue(self):
        return ir.async_get(self.hass).async_get_issue(DOMAIN, "silent_device_01020304")

    async def test_silence_marks_unavailable_raises_repair_and_recovers(self):
        entity = await self._entity(timedelta(minutes=30), repair=True)
        self.assertTrue(entity.available)
        self.assertIsNotNone(entity._silence_unsub, "grace timer armed at start-up")
        self.assertIsNone(self._issue())

        entity._silence_expired()
        self.assertFalse(entity.available)
        self.assertTrue(entity.silent)
        issue = self._issue()
        self.assertIsNotNone(issue)
        self.assertEqual(issue.translation_key, "silent_device")
        self.assertEqual(
            issue.translation_placeholders, {"name": "Pantry", "minutes": "30"}
        )

        entity._message_received_callback(
            SimpleNamespace(sender_int=0x01020304, rorg=RORG.BS1, data=[RORG.BS1, 0x08])
        )
        self.assertTrue(entity.available)
        self.assertIs(entity.is_on, True, "the telegram was also decoded")
        self.assertIsNone(self._issue(), "the repair clears itself")
        self.assertIsNotNone(entity.last_telegram_at)
        self.assertIsNotNone(entity._silence_unsub, "timer re-armed")

        # Another sender neither lifts nor records anything.
        entity._silence_expired()
        entity._message_received_callback(
            SimpleNamespace(sender_int=0xAABBCCDD, rorg=RORG.BS1, data=[RORG.BS1, 0x09])
        )
        self.assertFalse(entity.available)

    async def test_sensor_category_goes_unavailable_without_a_repair(self):
        entity = await self._entity(timedelta(minutes=5), repair=False)
        entity._silence_expired()
        self.assertFalse(entity.available)
        self.assertIsNone(self._issue())

    async def test_without_a_timeout_nothing_ever_expires(self):
        entity = await self._entity(None, repair=True)
        self.assertIsNone(entity._silence_unsub)
        entity._silence_expired()
        self.assertTrue(entity.available)
        self.assertIsNone(self._issue())

    async def test_platforms_apply_the_entry_settings(self):
        rows = [
            {
                "id": [1, 2, 3, 4],
                "platform": "binary_sensor",
                "name": "Smoke",
                "device_class": "smoke",
            },
            {"id": [5, 6, 7, 8], "platform": "binary_sensor", "name": "Rocker"},
            {
                "id": [9, 9, 9, 9],
                "platform": "sensor",
                "name": "Room",
                "device_class": "temperature",
            },
            {
                "id": [8, 8, 8, 8],
                "platform": "sensor",
                "name": "Pantry",
                "device_class": "contact",
            },
        ]
        enabled = SimpleNamespace(
            options={
                CONF_UI_DEVICES: rows,
                "availability": {"enabled": True, "smoke_minutes": 60},
            }
        )
        binaries: list = []
        await binary_sensor.async_setup_entry(self.hass, enabled, binaries.extend)
        sensors: list = []
        await sensor.async_setup_entry(self.hass, enabled, sensors.extend)
        by_name = {
            entity.dev_name: entity
            for entity in (*binaries, *sensors)
            if not entity.unique_id.endswith(
                ("radio_rssi", "radio_last_seen", "radio_repeater_count")
            )
        }
        self.assertEqual(by_name["Smoke"]._silence_timeout, timedelta(minutes=60))
        self.assertTrue(by_name["Smoke"]._silence_repair)
        self.assertIsNone(by_name["Rocker"]._silence_timeout)
        self.assertEqual(by_name["Room"]._silence_timeout, timedelta(minutes=180))
        self.assertFalse(by_name["Room"]._silence_repair)
        self.assertEqual(by_name["Pantry"]._silence_timeout, timedelta(hours=24))
        self.assertTrue(by_name["Pantry"]._silence_repair)

        disabled = SimpleNamespace(options={CONF_UI_DEVICES: rows})
        created: list = []
        await binary_sensor.async_setup_entry(self.hass, disabled, created.extend)
        await sensor.async_setup_entry(self.hass, disabled, created.extend)
        self.assertTrue(all(e._silence_timeout is None for e in created))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DeviceRegistryTests(unittest.IsolatedAsyncioTestCase):
    """Stale devices are detached at start-up; device pages can delete rows."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.registry = dr.async_get(self.hass)
        rows = [
            {
                "id": [1, 2, 3, 4],
                "platform": "switch",
                "name": "A1",
                "channel": 0,
                "switch_type": "default",
            },
            {
                "id": [1, 2, 3, 4],
                "platform": "switch",
                "name": "A2",
                "channel": 1,
                "switch_type": "default",
            },
            {"id": [5, 6, 7, 8], "platform": "binary_sensor", "name": "B"},
        ]
        self.entry = ConfigEntry(
            data={"device": "/dev/test"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=2,
            options={CONF_UI_DEVICES: rows},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _device(self, identifier: str):
        return self.registry.async_get_or_create(
            config_entry_id=self.entry.entry_id, identifiers={(DOMAIN, identifier)}
        )

    async def test_stale_devices_are_detached_and_configured_ones_kept(self):
        gateway = self._device(gateway_device_identifier(self.entry.entry_id))
        sender_a = self._device("01020304")
        sender_b = self._device("05060708")
        orphan = self._device("AABBCCDD")

        self.assertEqual(async_remove_stale_devices(self.hass, self.entry), 1)

        for device in (gateway, sender_a, sender_b):
            self.assertIsNotNone(self.registry.async_get(device.id))
        self.assertIsNone(self.registry.async_get(orphan.id))
        self.assertEqual(async_remove_stale_devices(self.hass, self.entry), 0)

    async def test_device_page_deletion_removes_the_rows_of_that_sender(self):
        gateway = self._device(gateway_device_identifier(self.entry.entry_id))
        sender_a = self._device("01020304")
        sender_b = self._device("05060708")

        self.assertFalse(
            await async_remove_config_entry_device(self.hass, self.entry, gateway)
        )
        self.assertTrue(
            await async_remove_config_entry_device(self.hass, self.entry, sender_a)
        )
        remaining = self.entry.options[CONF_UI_DEVICES]
        self.assertEqual([row["name"] for row in remaining], ["B"])

        # A sender also configured in YAML stays out of reach of the UI.
        learn.register_known_id(self.hass, [5, 6, 7, 8])
        self.assertFalse(
            await async_remove_config_entry_device(self.hass, self.entry, sender_b)
        )
        self.assertEqual([row["name"] for row in remaining], ["B"])

        foreign = self.registry.async_get_or_create(
            config_entry_id=self.entry.entry_id, identifiers={("other", "x")}
        )
        self.assertFalse(
            await async_remove_config_entry_device(self.hass, self.entry, foreign)
        )


if __name__ == "__main__":
    unittest.main()

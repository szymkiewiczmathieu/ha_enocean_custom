"""Rocker ``event`` entity: a typed companion to the existing button_pressed bus event.

Decoding is never duplicated: the entity only ever reacts to the bus event
``binary_sensor.EnOceanBinarySensor`` already fires, so this suite drives it
through that same event rather than through synthetic radio telegrams.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components/enocean_custom"
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant import loader
    from homeassistant.components.event import EventDeviceClass
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.util import dt as dt_util

    from custom_components.enocean_custom import event, options_flow
    from custom_components.enocean_custom.binary_sensor import EVENT_BUTTON_PRESSED
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.identity import (
        rocker_event_unique_id,
        sender_diagnostic_identities,
        ui_device_entity_identities,
    )
    from custom_components.enocean_custom.schema import (
        CONF_UI_DEVICES,
        UI_DEVICE_SCHEMA,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [0x05, 0x8F, 0x22, 0x33]
DECIMAL = "93266483"
OTHER = [1, 2, 3, 4]


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class IdentityTests(unittest.TestCase):
    def test_unique_id_is_outside_the_frozen_formulas(self):
        self.assertEqual(rocker_event_unique_id(SENDER), f"{DECIMAL}-rocker-event")


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class TranslationTests(unittest.TestCase):
    def test_every_language_names_the_entity_and_every_event_type(self):
        for name in ("strings.json", "translations/en.json", "translations/fr.json"):
            rocker = json.loads((COMPONENT / name).read_text(encoding="utf-8"))[
                "entity"
            ]["event"]["rocker_event"]
            self.assertTrue(rocker["name"], name)
            self.assertEqual(set(rocker["state"]), set(event.EVENT_TYPES), name)
            for event_type in event.EVENT_TYPES:
                self.assertTrue(rocker["state"][event_type], f"{name}: {event_type}")


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class SetupTests(unittest.IsolatedAsyncioTestCase):
    """Which rows get the companion entity, and which do not."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def _setup(self, rows):
        entry = SimpleNamespace(options={CONF_UI_DEVICES: rows})
        created: list = []
        await event.async_setup_entry(self.hass, entry, created.extend)
        return created

    async def test_one_entity_per_rocker_sender_deduplicated(self):
        rows = [
            {"id": SENDER, "platform": "binary_sensor", "name": "Rocker A"},
            {"id": SENDER, "platform": "binary_sensor", "name": "Rocker A bis"},
            {"id": OTHER, "platform": "switch", "name": "Not a sensor row"},
        ]
        created = await self._setup(rows)
        self.assertEqual(len(created), 1)
        entity = created[0]
        self.assertEqual(entity.unique_id, rocker_event_unique_id(SENDER))
        self.assertEqual(entity.dev_name, "Rocker A")
        self.assertTrue(entity.has_entity_name)
        self.assertEqual(entity.translation_key, "rocker_event")
        self.assertEqual(entity.device_class, EventDeviceClass.BUTTON)
        self.assertEqual(
            set(entity.event_types),
            {
                "channel_0_pressed",
                "channel_0_released",
                "channel_0_long_pressed",
                "channel_1_pressed",
                "channel_1_released",
                "channel_1_long_pressed",
                "both_pressed",
                "both_released",
                "both_long_pressed",
            },
        )

    async def test_declared_contact_smoke_and_multisensor_rows_are_excluded(self):
        rows = [
            {
                "id": [1, 1, 1, 1],
                "platform": "binary_sensor",
                "name": "Window",
                "radio_metadata": {
                    "sender_id": [1, 1, 1, 1],
                    "eep": "A5-14-01",
                    "eep_source": "manual",
                    "evidence": "manual",
                },
            },
            {
                "id": [2, 2, 2, 2],
                "platform": "binary_sensor",
                "name": "Smoke",
                "device_class": "smoke",
                "radio_metadata": {
                    "sender_id": [2, 2, 2, 2],
                    "eep": "F6-05-02",
                    "eep_source": "manual",
                    "evidence": "manual",
                },
            },
            {
                "id": [3, 3, 3, 3],
                "platform": "binary_sensor",
                "name": "Multisensor",
                "device_class": "smoke",
                "radio_metadata": {
                    "sender_id": [3, 3, 3, 3],
                    "eep": "D2-14-30",
                    "eep_source": "manual",
                    "evidence": "manual",
                },
            },
            {"id": SENDER, "platform": "binary_sensor", "name": "Plain rocker"},
        ]
        created = await self._setup(rows)
        self.assertEqual([e.dev_name for e in created], ["Plain rocker"])


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DecodeTests(unittest.IsolatedAsyncioTestCase):
    """Driven through the real button_pressed bus event, end to end."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        # Writing state for an entity without a platform makes Home Assistant
        # look up the integration's issue tracker; the loader must exist.
        loader.async_setup(self.hass)
        self.entity = event.EnOceanRockerEvent(SENDER, "Rocker")
        self.entity.hass = self.hass
        self.entity.entity_id = "event.rocker"
        # No real EntityPlatform is attached, so the has_entity_name /
        # translation_key naming path has nothing to resolve against; only
        # production code (through a real platform) needs it to succeed.
        self.entity.async_write_ha_state = lambda: None
        await self.entity.async_added_to_hass()

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _fire(self, *, pushed, which, onoff, repeated=0, sender=SENDER):
        self.hass.bus.async_fire(
            EVENT_BUTTON_PRESSED,
            {
                "name": "Rocker",
                "id": sender,
                "pushed": pushed,
                "which": which,
                "onoff": onoff,
                "repeated_telegram": repeated,
            },
        )

    async def test_press_fires_immediately(self):
        self._fire(pushed=1, which=0, onoff=1)
        await self.hass.async_block_till_done()
        self.assertEqual(
            self.entity.state_attributes["event_type"], "channel_0_pressed"
        )
        self.assertEqual(self.entity.state_attributes["onoff"], 1)
        self.assertIsNotNone(self.entity.state)

    async def test_short_release_does_not_synthesize_a_long_press(self):
        self._fire(pushed=1, which=1, onoff=0)
        await self.hass.async_block_till_done()
        self._fire(pushed=0, which=1, onoff=0)
        await self.hass.async_block_till_done()
        self.assertEqual(
            self.entity.state_attributes["event_type"], "channel_1_released"
        )

    async def test_held_release_also_fires_a_long_press(self):
        self._fire(pushed=1, which=0, onoff=1)
        await self.hass.async_block_till_done()
        # Back-date the recorded press instead of sleeping for the real
        # one-second threshold.
        self.entity._pressed_at[0] = dt_util.utcnow() - timedelta(seconds=5)
        self._fire(pushed=0, which=0, onoff=1)
        await self.hass.async_block_till_done()
        self.assertEqual(
            self.entity.state_attributes["event_type"], "channel_0_long_pressed"
        )

    async def test_both_channels_bitfield_is_its_own_event_family(self):
        self._fire(pushed=1, which=10, onoff=0)
        await self.hass.async_block_till_done()
        self.assertEqual(self.entity.state_attributes["event_type"], "both_pressed")

    async def test_unrecognised_telegrams_are_ignored(self):
        self._fire(pushed=None, which=-1, onoff=-1)
        await self.hass.async_block_till_done()
        self.assertIsNone(self.entity.state)

    async def test_other_senders_are_filtered(self):
        self._fire(pushed=1, which=0, onoff=1, sender=OTHER)
        await self.hass.async_block_till_done()
        self.assertIsNone(self.entity.state)

    async def test_value_changed_is_a_no_op(self):
        self.entity.value_changed(SimpleNamespace())  # must not raise
        self.assertIsNone(self.entity.state)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DeletionTests(unittest.IsolatedAsyncioTestCase):
    """The companion entity goes with the last row of its sender."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.registry = er.async_get(self.hass)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def test_deleting_the_last_row_removes_the_event_entity(self):
        row = UI_DEVICE_SCHEMA(
            {"id": SENDER, "platform": "binary_sensor", "name": "Rocker"}
        )
        entry = ConfigEntry(
            data={"device": "/dev/test"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=2,
            options={CONF_UI_DEVICES: [row]},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        self.hass.config_entries._entries[entry.entry_id] = entry
        for platform, unique_id in (
            *ui_device_entity_identities(row),
            *sender_diagnostic_identities(SENDER),
            ("event", rocker_event_unique_id(SENDER)),
        ):
            self.registry.async_get_or_create(platform, DOMAIN, unique_id)
        self.assertIsNotNone(
            self.registry.async_get_entity_id(
                "event", DOMAIN, rocker_event_unique_id(SENDER)
            )
        )

        flow = options_flow.EnOceanOptionsFlow()
        flow.hass, flow.handler = self.hass, entry.entry_id
        await flow.async_step_manage({"device": "0"})
        result = await flow.async_step_delete_confirm({"confirm": True})
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        self.assertIsNone(
            self.registry.async_get_entity_id(
                "event", DOMAIN, rocker_event_unique_id(SENDER)
            )
        )


if __name__ == "__main__":
    unittest.main()

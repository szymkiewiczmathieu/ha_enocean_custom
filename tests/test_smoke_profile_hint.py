"""A smoke binary sensor without a declared profile must say so, not stay mute.

A row that only says ``device_class: smoke`` keeps the historical rocker
decoder (see ``test_smoke_detector``): its state follows the ERP1 status
bits of a rocker press/release. An F6-05-02 detector is not a rocker. EEP
2.6.8 has it repeat "Smoke Alarm OFF" (DB0 ``0x00``) every 20 minutes, and
the profile says nothing about the status bits: on the reference installation
66 such telegrams were received in 22 hours and the entity never left
``unknown``, so an alarm automation waiting for ``on`` had nothing to trigger
on.

Decoding stays what the row declares. What changes is that the operator is
told: a non-fixable Repair issue names the device and the profile to declare,
and clears itself once the row declares it or the device is removed.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant import loader
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import issue_registry as ir

    from custom_components.enocean_custom import binary_sensor, const
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RORG,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import Packet

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [1, 2, 3, 4]
ISSUE_ID = "smoke_profile_missing_01020304"
TRANSLATIONS = ROOT / "custom_components/enocean_custom"
# ERP1 status values whose T21/NU bits are not a rocker press (0x3x) or
# release (0x2x): what a non-rocker RPS transmitter may send.
NOT_A_ROCKER_STATUS = 0x00


def _rps(db0: int, status: int):
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.RPS, db0, *SENDER, status],
        optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
    ).build()
    return Packet.parse_msg(bytearray(wire))[2]


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class SmokeProfileHintTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
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

    async def _added(self, entity):
        entity.hass = self.hass
        entity.entity_id = "binary_sensor.hall_smoke"
        entity.schedule_update_ha_state = lambda *args: None
        await entity.async_added_to_hass()
        return entity

    async def _legacy(self, device_class="smoke"):
        return await self._added(
            binary_sensor.EnOceanBinarySensor(SENDER, "Hall smoke", device_class)
        )

    def _issue(self):
        return ir.async_get(self.hass).async_get_issue(DOMAIN, ISSUE_ID)

    async def test_an_f6_05_02_heartbeat_on_an_undeclared_smoke_row_raises_a_repair(
        self,
    ):
        entity = await self._legacy()
        self.assertIsNone(self._issue())

        entity.value_changed(_rps(0x00, NOT_A_ROCKER_STATUS))

        issue = self._issue()
        self.assertIsNotNone(issue)
        self.assertEqual(const.ISSUE_SMOKE_PROFILE_MISSING, "smoke_profile_missing")
        self.assertEqual(issue.translation_key, "smoke_profile_missing")
        self.assertEqual(
            issue.translation_placeholders, {"name": "Hall smoke", "eep": "F6-05-02"}
        )
        self.assertFalse(issue.is_fixable)
        self.assertFalse(issue.is_persistent)
        self.assertEqual(issue.severity, ir.IssueSeverity.WARNING)
        # The decoder itself is unchanged: the telegram still moves nothing.
        self.assertIsNone(entity.is_on)

    async def test_alarm_and_energy_low_values_raise_it_too(self):
        for db0 in (0x10, 0x30):
            with self.subTest(db0=hex(db0)):
                entity = await self._legacy()
                entity.value_changed(_rps(db0, NOT_A_ROCKER_STATUS))
                self.assertIsNotNone(self._issue())
                self.assertIsNone(entity.is_on)
                await entity.async_will_remove_from_hass()
                self.assertIsNone(self._issue())

    async def test_the_repair_is_raised_once_per_entity(self):
        entity = await self._legacy()
        entity.value_changed(_rps(0x00, NOT_A_ROCKER_STATUS))
        registry = ir.async_get(self.hass)
        registry.async_delete(DOMAIN, ISSUE_ID)

        entity.value_changed(_rps(0x00, NOT_A_ROCKER_STATUS))

        self.assertIsNone(self._issue(), "one hint per entity lifetime")

    async def test_rocker_shaped_telegrams_keep_working_without_a_repair(self):
        entity = await self._legacy()

        entity.value_changed(_rps(0x10, 0x30))
        self.assertTrue(entity.is_on)
        entity.value_changed(_rps(0x00, 0x20))
        self.assertFalse(entity.is_on)

        self.assertIsNone(self._issue())

    async def test_a_row_that_is_not_a_smoke_sensor_is_never_flagged(self):
        for device_class in (None, "door", "motion"):
            with self.subTest(device_class=device_class):
                entity = await self._legacy(device_class)
                entity.value_changed(_rps(0x00, NOT_A_ROCKER_STATUS))
                entity.value_changed(_rps(0x10, NOT_A_ROCKER_STATUS))
                self.assertIsNone(self._issue())

    async def test_values_outside_the_profile_are_not_flagged(self):
        entity = await self._legacy()
        for db0 in (0x50, 0x70, 0x11, 0xF0):
            entity.value_changed(_rps(db0, NOT_A_ROCKER_STATUS))
        self.assertIsNone(self._issue())

    async def test_removing_the_entity_clears_the_repair(self):
        entity = await self._legacy()
        entity.value_changed(_rps(0x00, NOT_A_ROCKER_STATUS))
        self.assertIsNotNone(self._issue())

        await entity.async_will_remove_from_hass()

        self.assertIsNone(self._issue())

    async def test_declaring_the_profile_clears_a_leftover_repair(self):
        legacy = await self._legacy()
        legacy.value_changed(_rps(0x00, NOT_A_ROCKER_STATUS))
        self.assertIsNotNone(self._issue())

        detector = await self._added(
            binary_sensor.EnOceanSmokeDetector(SENDER, "Hall smoke", "smoke")
        )

        self.assertIsNone(self._issue())
        detector.value_changed(_rps(0x00, NOT_A_ROCKER_STATUS))
        self.assertFalse(detector.is_on)
        detector.value_changed(_rps(0x10, NOT_A_ROCKER_STATUS))
        self.assertTrue(detector.is_on, "the declared profile ignores status bits")
        self.assertIsNone(self._issue())


class TranslationTests(unittest.TestCase):
    def test_the_repair_is_translated_with_its_placeholders_everywhere(self):
        for relative in (
            "strings.json",
            "translations/en.json",
            "translations/fr.json",
            "translations/de.json",
            "translations/nl.json",
        ):
            with self.subTest(file=relative):
                strings = json.loads(
                    (TRANSLATIONS / relative).read_text(encoding="utf-8")
                )
                issue = strings["issues"]["smoke_profile_missing"]
                self.assertIn("{name}", issue["title"])
                self.assertIn("{name}", issue["description"])
                self.assertIn("{eep}", issue["description"])


if __name__ == "__main__":
    unittest.main()

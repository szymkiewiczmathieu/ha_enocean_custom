"""Minor version 2: the runtime RPS -> UBID1507C inference becomes explicit.

v2.7.0 made the switch platform treat every UI ``RPS`` row as a Ubiwizz
UBID1507C at runtime. v2.10.0 removes that inference and writes the profile
into the affected rows exactly once, so existing installations keep the same
entities, identities and attributes while the stored configuration finally
says what the code does.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.enocean_custom import async_migrate_entry, switch
    from custom_components.enocean_custom.const import (
        CONFIG_ENTRY_MINOR_VERSION,
        CONFIG_ENTRY_VERSION,
        DOMAIN,
    )
    from custom_components.enocean_custom.migration import (
        explicit_rps_actuator_profiles,
    )
    from custom_components.enocean_custom.schema import CONF_UI_DEVICES

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

PROFILE = "ubiwizz_ubid1507c"


def _rps_row(channel: int = 0, name: str = "Stairs", **extra) -> dict:
    """Return a row shaped like the production .storage rows of 2.9.x."""
    return {
        "channel": channel,
        "device_class": None,
        "id": [5, 143, 34, 51],
        "name": name,
        "platform": "switch",
        "radio_metadata": None,
        "sender_id": None,
        "switch_type": "RPS",
        **extra,
    }


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ExplicitProfileRewriteTests(unittest.TestCase):
    """The pure rewrite touches exactly the rows that relied on the inference."""

    def test_legacy_rps_rows_receive_the_profile_once(self):
        explicit = _rps_row(1, "Explicit", actuator_profile=PROFILE)
        default = {**_rps_row(0, "Relay"), "switch_type": "default"}
        rocker = {**_rps_row(0, "Rocker"), "platform": "binary_sensor"}
        malformed = {"id": [1], "platform": "switch"}
        rows = [_rps_row(0), _rps_row(1, "Hall"), explicit, default, rocker, malformed]

        migrated, changed = explicit_rps_actuator_profiles(rows)

        self.assertEqual(changed, 2)
        self.assertEqual(migrated[0]["actuator_profile"], PROFILE)
        self.assertEqual(migrated[1]["actuator_profile"], PROFILE)
        # Everything else is the very same object, not a repaired copy.
        for index in (2, 3, 4, 5):
            self.assertIs(migrated[index], rows[index])
        # The two rewritten rows differ from their source by one key only.
        for index in (0, 1):
            self.assertEqual(
                {**rows[index], "actuator_profile": PROFILE}, migrated[index]
            )
            self.assertNotIn("actuator_profile", rows[index])

    def test_a_row_the_profile_would_invalidate_is_left_alone(self):
        # The explicit profile allows channels 0 and 1 only; such a row never
        # produced a working entity with the inference and is not rewritten.
        rows = [_rps_row(2, "Odd")]
        migrated, changed = explicit_rps_actuator_profiles(rows)
        self.assertEqual(changed, 0)
        self.assertIs(migrated[0], rows[0])

    def test_non_list_options_are_returned_unchanged(self):
        for value in (None, "rows", {"id": [1, 2, 3, 4]}):
            with self.subTest(value=value):
                migrated, changed = explicit_rps_actuator_profiles(value)
                self.assertIs(migrated, value)
                self.assertEqual(changed, 0)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class EntryMigrationTests(unittest.IsolatedAsyncioTestCase):
    """async_migrate_entry bumps the minor version exactly once, safely."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _entry(self, rows, *, version=1, minor_version=1) -> ConfigEntry:
        entry = ConfigEntry(
            data={"device": "/dev/test-migration"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=minor_version,
            options={CONF_UI_DEVICES: list(rows)},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=version,
        )
        self.hass.config_entries._entries[entry.entry_id] = entry
        return entry

    def test_flow_handler_declares_the_migrated_version(self):
        self.assertEqual((CONFIG_ENTRY_VERSION, CONFIG_ENTRY_MINOR_VERSION), (1, 2))
        try:
            # The flow imports Home Assistant's usb component, whose optional
            # dependencies are absent from the bare test environment.
            from custom_components.enocean_custom.config_flow import (
                EnOceanFlowHandler,
            )
        except ModuleNotFoundError as err:  # pragma: no cover - environment
            self.skipTest(f"config flow dependencies unavailable: {err}")
        self.assertEqual(EnOceanFlowHandler.VERSION, CONFIG_ENTRY_VERSION)
        self.assertEqual(EnOceanFlowHandler.MINOR_VERSION, CONFIG_ENTRY_MINOR_VERSION)

    async def test_minor_version_one_is_rewritten_once(self):
        explicit = _rps_row(1, "Explicit", actuator_profile=PROFILE)
        entry = self._entry([_rps_row(0), explicit])

        self.assertTrue(await async_migrate_entry(self.hass, entry))

        self.assertEqual(entry.version, 1)
        self.assertEqual(entry.minor_version, 2)
        self.assertEqual(entry.data, {"device": "/dev/test-migration"})
        rows = entry.options[CONF_UI_DEVICES]
        self.assertEqual(rows[0]["actuator_profile"], PROFILE)
        self.assertEqual(rows[1], explicit)

        # Idempotent: a second run (e.g. after a downgrade/upgrade cycle)
        # changes nothing.
        before = entry.options
        self.assertTrue(await async_migrate_entry(self.hass, entry))
        self.assertEqual(entry.options, before)
        self.assertEqual(entry.minor_version, 2)

    async def test_newer_minor_version_is_left_alone_and_newer_major_refused(self):
        future_minor = self._entry([_rps_row(0)], minor_version=3)
        self.assertTrue(await async_migrate_entry(self.hass, future_minor))
        self.assertNotIn("actuator_profile", future_minor.options[CONF_UI_DEVICES][0])
        self.assertEqual(future_minor.minor_version, 3)

        future_major = self._entry([_rps_row(0)], version=2)
        self.assertFalse(await async_migrate_entry(self.hass, future_major))

    async def test_switch_platform_no_longer_infers_but_honours_the_migrated_row(
        self,
    ):
        legacy: list = []
        await switch.async_setup_entry(
            self.hass,
            SimpleNamespace(options={CONF_UI_DEVICES: [_rps_row(0)]}),
            legacy.extend,
        )
        self.assertEqual(len(legacy), 1)
        self.assertIsNone(legacy[0]._actuator_profile)
        self.assertNotIn(
            "configured_actuator_profile", legacy[0].extra_state_attributes
        )

        migrated_rows, _changed = explicit_rps_actuator_profiles([_rps_row(0)])
        migrated: list = []
        await switch.async_setup_entry(
            self.hass,
            SimpleNamespace(options={CONF_UI_DEVICES: migrated_rows}),
            migrated.extend,
        )
        self.assertEqual(
            migrated[0].extra_state_attributes["configured_actuator_profile"], PROFILE
        )
        # Identity is independent of the profile: the entity registry row of a
        # production switch is untouched by the migration.
        self.assertEqual(migrated[0].unique_id, legacy[0].unique_id)
        self.assertEqual(migrated[0].unique_id, "93266483-0")


if __name__ == "__main__":
    unittest.main()

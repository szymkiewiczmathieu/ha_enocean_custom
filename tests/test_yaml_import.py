"""Tests for the explicit, identity-preserving YAML to UI migration."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType

    from custom_components.enocean_custom import (
        async_unload_entry,
        binary_sensor,
        switch,
    )
    from custom_components.enocean_custom.binary_sensor import EnOceanBinarySensor
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.options_flow import (
        EnOceanOptionsFlow,
        _unique_id_for,
    )
    from custom_components.enocean_custom.schema import CONF_UI_DEVICES
    from custom_components.enocean_custom.switch import EnOceanSwitch
    from custom_components.enocean_custom.yaml_import import (
        DATA_YAML_DEVICE_CONFIGS,
        purge_yaml_inventory,
        track_yaml_device,
        yaml_inventory,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - bare unit-test environments
    HA_AVAILABLE = False


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class YamlImportTests(unittest.IsolatedAsyncioTestCase):
    """Exercise inventory lifetime, flow behavior, and identity parity."""

    async def asyncSetUp(self) -> None:
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.entry = ConfigEntry(
            data={"device": "/dev/test"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=1,
            options={},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry

    async def asyncTearDown(self) -> None:
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _flow(self) -> EnOceanOptionsFlow:
        flow = EnOceanOptionsFlow()
        flow.hass = self.hass
        flow.handler = self.entry.entry_id
        return flow

    def _track_defaults(self) -> None:
        track_yaml_device(
            self.hass,
            "binary_sensor",
            {"id": [1, 2, 3, 4], "name": "Rocker"},
        )
        track_yaml_device(
            self.hass,
            "switch",
            {"id": [5, 6, 7, 8], "name": "Relay"},
        )

    def test_normalization_and_unique_id_parity_for_both_platforms(self) -> None:
        """Imported rows reproduce YAML IDs, including absent device_class."""
        self._track_defaults()
        rows, _unsupported, _invalid = yaml_inventory(self.hass)
        binary_row, switch_row = rows
        self.assertIsNone(binary_row["device_class"])
        self.assertEqual(binary_row["channel"], 0)
        self.assertEqual(binary_row["switch_type"], "default")
        self.assertIsNone(binary_row["radio_metadata"])
        binary = EnOceanBinarySensor(binary_row["id"], binary_row["name"], None)
        switch = EnOceanSwitch(
            switch_row["id"],
            switch_row["name"],
            switch_row["channel"],
            switch_row["switch_type"],
        )
        self.assertEqual(binary.unique_id, _unique_id_for(binary_row))
        self.assertEqual(binary.unique_id, "16909060-None")
        self.assertEqual(switch.unique_id, _unique_id_for(switch_row))

    async def test_yaml_platform_setups_track_normalized_configs(self) -> None:
        """Both supported legacy setup hooks populate the ephemeral inventory."""
        binary_config = binary_sensor.PLATFORM_SCHEMA(
            {"platform": DOMAIN, "id": [1, 2, 3, 4], "name": "Rocker"}
        )
        switch_config = switch.PLATFORM_SCHEMA(
            {"platform": DOMAIN, "id": [5, 6, 7, 8], "name": "Relay"}
        )
        with (
            patch.object(binary_sensor, "register_known_id"),
            patch.object(switch, "register_known_id"),
            patch.object(switch, "_migrate_to_new_unique_id"),
        ):
            binary_sensor.setup_platform(self.hass, binary_config, lambda _rows: None)
            await switch.async_setup_platform(
                self.hass, switch_config, lambda _rows: None
            )
        rows, _unsupported, invalid = yaml_inventory(self.hass)
        self.assertEqual([row["platform"] for row in rows], ["binary_sensor", "switch"])
        self.assertEqual(invalid, 0)

    async def test_declared_device_class_and_channel_keep_yaml_identity(self) -> None:
        """A declared device_class or channel survives as its exact YAML text.

        DEVICE_CLASSES_SCHEMA yields a BinarySensorDeviceClass member, which
        the legacy entity formats into its unique_id. The inventory must store
        the same rendering as a plain string so the identity matches before and
        after the options are written to .storage as JSON.
        """
        binary_config = binary_sensor.PLATFORM_SCHEMA(
            {
                "platform": DOMAIN,
                "id": [0xAA, 0xBB, 0xCC, 0xDD],
                "name": "Front door",
                "device_class": "Door",
            }
        )
        switch_config = switch.PLATFORM_SCHEMA(
            {
                "platform": DOMAIN,
                "id": [5, 6, 7, 8],
                "name": "Rocker relay",
                "channel": 1,
                "switch_type": "RPS",
            }
        )
        with (
            patch.object(binary_sensor, "register_known_id"),
            patch.object(switch, "register_known_id"),
            patch.object(switch, "_migrate_to_new_unique_id"),
        ):
            binary_sensor.setup_platform(self.hass, binary_config, lambda _rows: None)
            await switch.async_setup_platform(
                self.hass, switch_config, lambda _rows: None
            )
        binary_row, switch_row = yaml_inventory(self.hass)[0]
        self.assertIs(type(binary_row["device_class"]), str)
        self.assertEqual(binary_row["device_class"], "door")
        self.assertEqual(switch_row["channel"], 1)
        self.assertEqual(switch_row["switch_type"], "RPS")
        yaml_binary = EnOceanBinarySensor(
            binary_config["id"],
            binary_config["name"],
            binary_config["device_class"],
        )
        yaml_switch = EnOceanSwitch(
            switch_config["id"],
            switch_config["name"],
            switch_config["channel"],
            switch_config["switch_type"],
        )
        self.assertEqual(_unique_id_for(binary_row), yaml_binary.unique_id)
        self.assertEqual(_unique_id_for(switch_row), yaml_switch.unique_id)

        await self._flow().async_step_import_yaml({"confirm": True})
        # Options are persisted to .storage: a stray enum or tuple would be
        # written as something the next start cannot compare identities on.
        stored = json.loads(json.dumps(self.entry.options[CONF_UI_DEVICES]))
        self.assertEqual(
            [_unique_id_for(row) for row in stored],
            [yaml_binary.unique_id, yaml_switch.unique_id],
        )
        ui_binary = EnOceanBinarySensor(
            stored[0]["id"], stored[0]["name"], stored[0]["device_class"]
        )
        ui_switch = EnOceanSwitch(
            stored[1]["id"],
            stored[1]["name"],
            stored[1]["channel"],
            stored[1]["switch_type"],
        )
        self.assertEqual(ui_binary.unique_id, yaml_binary.unique_id)
        self.assertEqual(ui_switch.unique_id, yaml_switch.unique_id)

    async def test_menu_absent_without_pending_yaml(self) -> None:
        flow = self._flow()
        menu = await flow.async_step_init()
        self.assertNotIn("import_yaml", menu["menu_options"])
        self._track_defaults()
        self.hass.config_entries.async_update_entry(
            self.entry, options={CONF_UI_DEVICES: yaml_inventory(self.hass)[0]}
        )
        menu = await flow.async_step_init()
        self.assertNotIn("import_yaml", menu["menu_options"])

    async def test_duplicate_yaml_identity_is_counted_as_skipped(self) -> None:
        row = {"id": [1, 2, 3, 4], "name": "Rocker"}
        track_yaml_device(self.hass, "binary_sensor", row)
        track_yaml_device(self.hass, "binary_sensor", row)
        result = await self._flow().async_step_import_yaml({"confirm": True})
        self.assertEqual(result["description_placeholders"]["imported"], "1")
        self.assertEqual(result["description_placeholders"]["skipped"], "1")
        self.assertEqual(len(self.entry.options[CONF_UI_DEVICES]), 1)

    async def test_confirmation_import_and_success_counts(self) -> None:
        self._track_defaults()
        track_yaml_device(self.hass, "sensor", {"id": [9, 9, 9, 9]})
        track_yaml_device(self.hass, "switch", {"id": [1], "name": "bad"})
        flow = self._flow()
        menu = await flow.async_step_init()
        self.assertIn("import_yaml", menu["menu_options"])
        confirm = await flow.async_step_import_yaml()
        self.assertEqual(confirm["type"], FlowResultType.FORM)
        self.assertEqual(
            confirm["description_placeholders"],
            {
                "binary_sensors": "1",
                "switches": "1",
                "non_importable": "1",
                "invalid": "1",
            },
        )
        result = await flow.async_step_import_yaml({"confirm": True})
        self.assertEqual(result["step_id"], "import_yaml_success")
        self.assertEqual(result["description_placeholders"]["imported"], "2")
        self.assertEqual(len(self.entry.options[CONF_UI_DEVICES]), 2)
        self.assertTrue(
            all(
                row["radio_metadata"] is None
                for row in self.entry.options[CONF_UI_DEVICES]
            )
        )

    async def test_cancel_and_restart_without_tracking(self) -> None:
        self._track_defaults()
        flow = self._flow()
        result = await flow.async_step_import_yaml({"confirm": False})
        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertNotIn(CONF_UI_DEVICES, self.entry.options)
        purge_yaml_inventory(self.hass)
        self.assertNotIn(DATA_YAML_DEVICE_CONFIGS, self.hass.data[DOMAIN])
        menu = await self._flow().async_step_init()
        self.assertNotIn("import_yaml", menu["menu_options"])

    async def test_entry_unload_purges_inventory(self) -> None:
        self._track_defaults()
        gateway = MagicMock()
        gateway.async_unload = AsyncMock(return_value=True)
        self.entry.runtime_data = gateway
        with patch.object(
            self.hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=True),
        ):
            self.assertTrue(await async_unload_entry(self.hass, self.entry))
        self.assertNotIn(DATA_YAML_DEVICE_CONFIGS, self.hass.data[DOMAIN])

    def test_translation_keys_match_and_are_present(self) -> None:
        documents = []
        for relative in (
            "custom_components/enocean_custom/strings.json",
            "custom_components/enocean_custom/translations/en.json",
            "custom_components/enocean_custom/translations/fr.json",
        ):
            documents.append(json.loads((ROOT / relative).read_text(encoding="utf-8")))
        reference = set(documents[0]["options"]["step"])
        for document in documents:
            self.assertEqual(set(document["options"]["step"]), reference)
            self.assertIn(
                "import_yaml",
                document["options"]["step"]["init"]["menu_options"],
            )
            self.assertIn("import_yaml_success", document["options"]["step"])


if __name__ == "__main__":
    unittest.main()

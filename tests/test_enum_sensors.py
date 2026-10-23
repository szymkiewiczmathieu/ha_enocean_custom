"""Handle and contact sensors are ENUM sensors with frozen state values.

The F6-10-00 handle keeps ``open`` / ``tilt`` / ``closed`` and the D5-00-01
contact keeps ``open`` / ``closed``: automations in the field match on those
strings. The ENUM device class only adds the option list, per-state icons and
translated labels, and it forces the pre-2.10 literal ``unknown`` out of the
restored state.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.components.sensor import SensorDeviceClass
    from homeassistant.core import HomeAssistant

    from custom_components.enocean_custom import sensor
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        RORG,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

COMPONENT = ROOT / "custom_components/enocean_custom"
EXPECTED_OPTIONS = {
    "window_handle": ["open", "tilt", "closed"],
    "d5_contact": ["open", "closed"],
}


class TranslationAndIconCatalogTests(unittest.TestCase):
    """Every option has an icon and a translated label in every language."""

    def test_icons_cover_exactly_the_options(self):
        icons = json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))
        sensors = icons["entity"]["sensor"]
        for key, options in EXPECTED_OPTIONS.items():
            with self.subTest(key=key):
                self.assertIn("default", sensors[key])
                self.assertEqual(set(sensors[key]["state"]), set(options))
                for icon in (sensors[key]["default"], *sensors[key]["state"].values()):
                    self.assertTrue(icon.startswith("mdi:"), icon)

    def test_state_labels_cover_exactly_the_options_in_every_language(self):
        for name in ("strings.json", "translations/en.json", "translations/fr.json"):
            data = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
            sensors = data["entity"]["sensor"]
            for key, options in EXPECTED_OPTIONS.items():
                with self.subTest(file=name, key=key):
                    self.assertTrue(sensors[key]["name"])
                    self.assertEqual(set(sensors[key]["state"]), set(options))

    def test_french_state_labels_are_translated(self):
        english = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
        french = json.loads(
            (COMPONENT / "translations/fr.json").read_text(encoding="utf-8")
        )
        for key in EXPECTED_OPTIONS:
            with self.subTest(key=key):
                self.assertNotEqual(
                    english["entity"]["sensor"][key]["state"],
                    french["entity"]["sensor"][key]["state"],
                )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class EnumDescriptionTests(unittest.TestCase):
    """Descriptions declare the ENUM class with the frozen option lists."""

    def test_descriptions(self):
        cases = (
            (sensor.SENSOR_DESC_WINDOWHANDLE, "window_handle", "windowhandle"),
            (sensor.SENSOR_DESC_CONTACT, "d5_contact", "d5_00_01_contact"),
            (sensor.SENSOR_DESC_SHUTTERCONTACT, "d5_contact", "shuttercontact"),
        )
        for description, translation_key, identity_key in cases:
            with self.subTest(key=identity_key):
                self.assertEqual(description.device_class, SensorDeviceClass.ENUM)
                self.assertEqual(description.translation_key, translation_key)
                self.assertEqual(description.options, EXPECTED_OPTIONS[translation_key])
                # The unique_id suffix is untouched by the ENUM change.
                self.assertEqual(description.key, identity_key)
                self.assertIsNone(description.icon)

    def test_handle_states_are_the_documented_strings_or_none(self):
        entity = sensor.EnOceanWindowHandle(
            [1, 2, 3, 4], "handle", sensor.SENSOR_DESC_WINDOWHANDLE
        )
        entity.schedule_update_ha_state = lambda: None
        for payload, expected in (
            (0xC0, "open"),
            (0xD0, "tilt"),
            (0xE0, "open"),
            (0xF0, "closed"),
            (0x00, None),
        ):
            entity.value_changed(
                SimpleNamespace(rorg=RORG.RPS, data=[RORG.RPS, payload])
            )
            self.assertEqual(entity.native_value, expected)
            # Every exposed value is a declared option: HA raises otherwise.
            self.assertTrue(expected is None or expected in entity.options)
        self.assertEqual(entity.unique_id, "16909060-windowhandle")

    def test_contact_states_are_the_documented_strings(self):
        for description in (
            sensor.SENSOR_DESC_CONTACT,
            sensor.SENSOR_DESC_SHUTTERCONTACT,
        ):
            entity = sensor.EnOceanD50001Contact([1, 2, 3, 4], "contact", description)
            entity.schedule_update_ha_state = lambda: None
            entity.value_changed(SimpleNamespace(rorg=RORG.BS1, data=[RORG.BS1, 0x08]))
            self.assertEqual(entity.native_value, "open")
            entity.value_changed(SimpleNamespace(rorg=RORG.BS1, data=[RORG.BS1, 0x09]))
            self.assertEqual(entity.native_value, "closed")
            self.assertIn(entity.native_value, entity.options)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class RestoredStateTests(unittest.IsolatedAsyncioTestCase):
    """A persisted pre-2.10 "unknown" never violates the option list."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def _restored(self, stored):
        entity = sensor.EnOceanWindowHandle(
            [1, 2, 3, 4], "handle", sensor.SENSOR_DESC_WINDOWHANDLE
        )
        entity.hass = self.hass
        with patch.object(
            entity,
            "async_get_last_sensor_data",
            AsyncMock(return_value=SimpleNamespace(native_value=stored)),
        ):
            await entity.async_added_to_hass()
        return entity.native_value

    async def test_unknown_literal_is_dropped_and_options_are_kept(self):
        self.assertIsNone(await self._restored("unknown"))
        self.assertIsNone(await self._restored("bogus"))
        self.assertEqual(await self._restored("tilt"), "tilt")
        self.assertEqual(await self._restored("closed"), "closed")


if __name__ == "__main__":
    unittest.main()

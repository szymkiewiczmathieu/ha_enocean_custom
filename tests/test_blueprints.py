"""The shipped automation blueprints load and validate on the pinned release.

Each blueprint is parsed with Home Assistant's own YAML loader (``!input``
tags), checked against the automation blueprint schema, instantiated with
sample inputs and the resulting automation is validated against the automation
platform schema. Device/entity existence is not checked here: that needs a
running registry.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.components.automation.config import (
        AUTOMATION_BLUEPRINT_SCHEMA,
        PLATFORM_SCHEMA,
    )
    from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
    from homeassistant.core import HomeAssistant
    from homeassistant.util.yaml import load_yaml_dict

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

BLUEPRINT_DIR = ROOT / "blueprints/automation/enocean_custom"
README = ROOT / "README.md"

# One plausible answer per required input, as the UI would submit it.
SAMPLE_INPUTS = {
    "rocker_to_light.yaml": {
        "rocker": "0123456789abcdef0123456789abcdef",
        "channel": "1",
        "light_target": {"entity_id": "light.kitchen"},
    },
    "handle_open_hvac_off.yaml": {
        "handle": "sensor.window_handle",
        "climate_target": {"entity_id": ["climate.living_room"]},
        "notify_service": "notify.mobile_app_phone",
    },
    "smoke_alarm_notify.yaml": {
        "smoke_sensors": ["binary_sensor.smoke_hall", "binary_sensor.smoke_floor"],
        "lights": {"area_id": "hall"},
    },
}


class BlueprintInventoryTests(unittest.TestCase):
    """Every shipped blueprint is known to this test and documented."""

    def test_inventory_and_readme_links(self):
        shipped = sorted(path.name for path in BLUEPRINT_DIR.glob("*.yaml"))
        self.assertEqual(shipped, sorted(SAMPLE_INPUTS))
        readme = README.read_text(encoding="utf-8")
        for name in shipped:
            with self.subTest(blueprint=name):
                self.assertIn(name, readme)
                self.assertIn(
                    "blueprint_import/?blueprint_url="
                    "https%3A%2F%2Fgithub.com%2Fszymkiewiczmathieu%2F"
                    "ha_enocean_custom%2Fblob%2Fmain%2Fblueprints%2Fautomation%2F"
                    f"enocean_custom%2F{name}",
                    readme,
                )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class BlueprintValidationTests(unittest.IsolatedAsyncioTestCase):
    """Blueprints are valid and produce valid automations once instantiated.

    Template validators insist on a Home Assistant instance bound to the
    running event loop, exactly as Home Assistant validates automations, hence
    the asynchronous test case with a real (idle) instance.
    """

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _blueprint(self, name: str) -> Blueprint:
        data = load_yaml_dict(BLUEPRINT_DIR / name)
        return Blueprint(
            data,
            path=name,
            expected_domain="automation",
            schema=AUTOMATION_BLUEPRINT_SCHEMA,
        )

    def test_metadata_declares_author_and_minimum_version(self):
        for name in SAMPLE_INPUTS:
            with self.subTest(blueprint=name):
                blueprint = self._blueprint(name)
                self.assertIsNone(blueprint.validate(), "min_version above runtime")
                self.assertEqual(blueprint.metadata["author"], "EnOcean Custom")
                self.assertEqual(
                    blueprint.metadata["homeassistant"]["min_version"], "2026.7.0"
                )
                for key, definition in blueprint.inputs.items():
                    self.assertIn("selector", definition, f"{name}: {key}")

    async def test_instantiated_automations_validate(self):
        for name, inputs in SAMPLE_INPUTS.items():
            with self.subTest(blueprint=name):
                blueprint = self._blueprint(name)
                blueprint_inputs = BlueprintInputs(
                    blueprint, {"use_blueprint": {"path": name, "input": inputs}}
                )
                blueprint_inputs.validate()
                config = blueprint_inputs.async_substitute()
                validated = PLATFORM_SCHEMA({"id": "test", "alias": name, **config})
                self.assertTrue(validated["triggers"])
                self.assertTrue(validated["actions"])

    def test_rocker_blueprint_uses_the_native_device_trigger(self):
        blueprint = self._blueprint("rocker_to_light.yaml")
        config = BlueprintInputs(
            blueprint,
            {
                "use_blueprint": {
                    "path": "rocker_to_light.yaml",
                    "input": SAMPLE_INPUTS["rocker_to_light.yaml"],
                }
            },
        ).async_substitute()
        (trigger,) = config["triggers"]
        self.assertEqual(trigger["domain"], "enocean_custom")
        self.assertEqual(trigger["type"], "button_pressed")
        self.assertEqual(trigger["which"], "1")
        self.assertEqual(trigger["device_id"], "0123456789abcdef0123456789abcdef")


if __name__ == "__main__":
    unittest.main()

"""has_entity_name migration (M8): never changes an already-shown name.

Quality Scale Bronze ``has-entity-name``. The policy: a row's primary
entity switches to ``has_entity_name=True`` / ``name=None`` only when its
own name already equals its sender's device name (the common case: the row
is alone on its sender, or happens to be the first one) -- Home Assistant
then renders the device's own name for that entity, the exact string
already shown today. A row sharing a sender with a differently named
earlier row (a second relay channel) is left exactly as it was: its own
explicit name, has_entity_name unset. Existing unique_ids and entity_ids
are untouched either way; only an already-displayed string can change, and
only for the rows this policy deliberately leaves alone.
"""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.typing import ConfigType

    from custom_components.enocean_custom import (
        binary_sensor,
        climate,
        light,
        sensor,
        switch,
    )
    from custom_components.enocean_custom.identity import row_names_the_device
    from custom_components.enocean_custom.schema import CONF_UI_DEVICES

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [0x05, 0x8F, 0x22, 0x33]
OTHER_SENDER = [0x01, 0x02, 0x03, 0x04]


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class RowNamesTheDeviceTests(unittest.TestCase):
    def test_sole_row_matches_its_own_device_name(self):
        names = {0x058F2233: "Stairs"}
        self.assertTrue(row_names_the_device({"id": SENDER, "name": "Stairs"}, names))

    def test_a_differently_named_later_row_does_not_match(self):
        names = {0x058F2233: "Stairs top"}
        self.assertFalse(
            row_names_the_device({"id": SENDER, "name": "Stairs bottom"}, names)
        )

    def test_unknown_sender_never_matches(self):
        self.assertFalse(row_names_the_device({"id": SENDER, "name": "X"}, {}))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class PlatformSetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def test_switch_single_row_gets_has_entity_name(self):
        rows = [
            {
                "id": SENDER,
                "platform": "switch",
                "name": "Stairs",
                "channel": 0,
                "switch_type": "default",
            }
        ]
        entry = SimpleNamespace(options={CONF_UI_DEVICES: rows})
        created: list = []
        await switch.async_setup_entry(self.hass, entry, created.extend)
        (entity,) = created
        self.assertTrue(entity.has_entity_name)
        self.assertIsNone(entity._attr_name)

    async def test_switch_two_channels_keep_their_own_distinct_names(self):
        rows = [
            {
                "id": SENDER,
                "platform": "switch",
                "name": "Left plug",
                "channel": 0,
                "switch_type": "default",
            },
            {
                "id": SENDER,
                "platform": "switch",
                "name": "Right plug",
                "channel": 1,
                "switch_type": "default",
            },
        ]
        entry = SimpleNamespace(options={CONF_UI_DEVICES: rows})
        created: list = []
        await switch.async_setup_entry(self.hass, entry, created.extend)
        by_channel = {e.channel: e for e in created}
        # The first row names the device: it may take has_entity_name.
        self.assertTrue(by_channel[0].has_entity_name)
        self.assertIsNone(by_channel[0]._attr_name)
        # The second keeps exactly its own name, untouched -- the display
        # string for this one is unchanged by this migration.
        self.assertFalse(by_channel[1].has_entity_name)
        self.assertEqual(by_channel[1]._attr_name, "Right plug")

    async def test_light_single_row_gets_has_entity_name(self):
        rows = [
            {
                "id": [0x09, 0x09, 0x09, 0x09],
                "platform": "light",
                "name": "Dimmer",
                "sender_id": [0x05, 0x00, 0x00, 0x01],
                "channel": 0,
            }
        ]
        entry = SimpleNamespace(options={CONF_UI_DEVICES: rows})
        created: list = []
        await light.async_setup_entry(self.hass, entry, created.extend)
        (entity,) = created
        self.assertTrue(entity.has_entity_name)
        self.assertIsNone(entity._attr_name)

    async def test_yaml_light_gets_has_entity_name(self):
        config: ConfigType = {
            "sender_id": [0x05, 0x00, 0x00, 0x01],
            "id": [],
            "name": "Dimmer",
            "channel": 0,
        }
        created: list = []
        await light.async_setup_platform(self.hass, config, created.extend)
        entity = created[0]
        self.assertTrue(entity.has_entity_name)
        self.assertIsNone(entity._attr_name)

    async def test_climate_row_gets_has_entity_name(self):
        rows = [
            {
                "id": SENDER,
                "platform": "climate",
                "name": "Thermostat",
                "id_switch": [0x05, 0x00, 0x00, 0x01],
                "device_type": "SRC-D08",
                "sensor_entity_id": "sensor.room",
            }
        ]
        entry = SimpleNamespace(options={CONF_UI_DEVICES: rows})
        created: list = []
        await climate.async_setup_entry(self.hass, entry, created.extend)
        (entity,) = created
        self.assertTrue(entity.has_entity_name)
        self.assertIsNone(entity._attr_name)

    async def test_yaml_climate_gets_has_entity_name(self):
        config = {
            "id": SENDER,
            "name": "Thermostat",
            "sender_id_switch": [0x05, 0x00, 0x00, 0x01],
            "device_type": "SRC-D08",
            "sensor_entity_id": "sensor.room",
            "temperature_frost_protection": 8.0,
            "sensor_target_temperature_range": 5,
            "sensor_target_temperature_update_tolerance": 0.5,
            "target_temperature_base_value": 21.0,
            "target_temperature_reduction_night": 4.0,
            "command_frequency": timedelta(minutes=17),
            "pi_control_Kp": 5.0,
            "pi_control_Tn": 240.0,
        }
        created: list = []
        await climate.async_setup_platform(self.hass, config, created.extend)
        entity = created[0]
        self.assertTrue(entity.has_entity_name)
        self.assertIsNone(entity._attr_name)

    async def test_binary_sensor_primary_gets_has_entity_name(self):
        rows = [{"id": SENDER, "platform": "binary_sensor", "name": "Rocker"}]
        entry = SimpleNamespace(options={CONF_UI_DEVICES: rows})
        created: list = []
        await binary_sensor.async_setup_entry(self.hass, entry, created.extend)
        (entity,) = [
            e for e in created if isinstance(e, binary_sensor.EnOceanBinarySensor)
        ]
        self.assertTrue(entity.has_entity_name)
        self.assertIsNone(entity._attr_name)

    async def test_smoke_companion_keeps_its_own_translation_key_untouched(self):
        rows = [
            {
                "id": SENDER,
                "platform": "binary_sensor",
                "name": "Detector",
                "device_class": "smoke",
                "radio_metadata": {
                    "sender_id": SENDER,
                    "eep": "F6-05-02",
                    "eep_source": "manual",
                    "evidence": "manual",
                },
            }
        ]
        entry = SimpleNamespace(options={CONF_UI_DEVICES: rows})
        created: list = []
        await binary_sensor.async_setup_entry(self.hass, entry, created.extend)
        primary = next(
            e for e in created if isinstance(e, binary_sensor.EnOceanSmokeDetector)
        )
        battery = next(
            e for e in created if isinstance(e, binary_sensor.EnOceanSmokeBatteryLow)
        )
        self.assertTrue(primary.has_entity_name)
        self.assertIsNone(primary._attr_name)
        # Untouched: it already had has_entity_name + translation_key.
        self.assertTrue(battery.has_entity_name)
        self.assertEqual(battery.translation_key, "smoke_battery_low")


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class LegacySensorNamingTests(unittest.TestCase):
    """EnOceanSensor and its subclasses now resolve their own name from
    entity_description instead of formatting ``"{description} {dev_name}"``
    by hand."""

    def test_has_entity_name_and_translation_key_are_set(self):
        entity = sensor.EnOceanSensor(SENDER, "Room", sensor.SENSOR_DESC_TEMPERATURE)
        self.assertTrue(entity.has_entity_name)
        self.assertEqual(entity.translation_key, "temperature")
        self.assertFalse(hasattr(entity, "_attr_name") and entity._attr_name)

    def test_every_legacy_description_has_a_translation_key(self):
        for description in (
            sensor.SENSOR_DESC_TEMPERATURE,
            sensor.SENSOR_DESC_HUMIDITY,
            sensor.SENSOR_DESC_POWER,
            sensor.SENSOR_DESC_ENERGY,
            sensor.SENSOR_DESC_SUPPLY_VOLTAGE,
            sensor.SENSOR_DESC_WINDOWHANDLE,
            sensor.SENSOR_DESC_CONTACT,
            sensor.SENSOR_DESC_SHUTTERCONTACT,
        ):
            self.assertTrue(description.translation_key, description.key)


if __name__ == "__main__":
    unittest.main()

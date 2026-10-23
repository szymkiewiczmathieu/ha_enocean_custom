"""The options flow computes unique_ids exactly as the platforms mint them.

Every formula is frozen: existing installations key their entity registry
rows on these strings, so the expected values below are spelled out literally
instead of being derived from the code under test.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from typing import Any

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from custom_components.enocean_custom import (
        binary_sensor,
        climate,
        light,
        options_flow,
        sensor,
        switch,
    )
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.identity import (
        SENSOR_KEYS_BY_DEVICE_CLASS,
        sensor_keys_for_device_class,
        sensor_unique_id,
        ui_device_entity_identities,
    )
    from custom_components.enocean_custom.schema import (
        CONF_UI_DEVICES,
        UI_DEVICE_SCHEMA,
        valid_ui_devices,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

DEVICE_ID = [0x01, 0x02, 0x03, 0x04]
DECIMAL_ID = "16909060"


def _row(platform: str, **fields: Any) -> dict[str, Any]:
    return UI_DEVICE_SCHEMA({"id": DEVICE_ID, "platform": platform, **fields})


def _rows() -> dict[str, dict[str, Any]]:
    """Return one validated row per platform shape the flow can persist."""
    return {
        "switch": _row("switch", name="relay", channel=2, switch_type="default"),
        "rps": _row("switch", name="rocker", channel=1, switch_type="RPS"),
        "climate": _row(
            "climate",
            name="heating",
            id_switch=[5, 6, 7, 8],
            device_type="SRC-D08",
            sensor_entity_id="sensor.room",
        ),
        "light": _row("light", name="dimmer", sender_id=[9, 10, 11, 12]),
        "binary_door": _row("binary_sensor", name="door", device_class="door"),
        "binary_none": _row("binary_sensor", name="rocker"),
        "binary_a514": _row(
            "binary_sensor",
            name="window",
            device_class="door",
            radio_metadata={
                "sender_id": DEVICE_ID,
                "eep": "A5-14-01",
                "eep_source": "radio_declared",
                "evidence": "exact",
            },
        ),
        "sensor_temperature": _row(
            "sensor", name="room", device_class="temperature", min_temp=0, max_temp=40
        ),
        "sensor_humidity": _row("sensor", name="room", device_class="humidity"),
        "sensor_power": _row("sensor", name="plug", device_class="powersensor"),
        "sensor_default": _row("sensor", name="plug"),
        "sensor_windowhandle": _row("sensor", name="handle", device_class="windowhandle"),
        "sensor_contact": _row("sensor", name="pantry", device_class="contact"),
        "sensor_shuttercontact": _row(
            "sensor", name="legacy", device_class="shuttercontact"
        ),
    }


EXPECTED_IDENTITIES = {
    "switch": (("switch", f"{DECIMAL_ID}-2"),),
    "rps": (("switch", f"{DECIMAL_ID}-1"),),
    "climate": (("climate", f"{DECIMAL_ID}-0"),),
    "light": (("light", DECIMAL_ID),),
    "binary_door": (("binary_sensor", f"{DECIMAL_ID}-door"),),
    "binary_none": (("binary_sensor", f"{DECIMAL_ID}-None"),),
    "binary_a514": (
        ("binary_sensor", f"{DECIMAL_ID}-a5-14-01-contact"),
        ("sensor", f"{DECIMAL_ID}-a5_14_01_supply_voltage"),
    ),
    "sensor_temperature": (("sensor", f"{DECIMAL_ID}-temperature"),),
    "sensor_humidity": (("sensor", f"{DECIMAL_ID}-humidity"),),
    "sensor_power": (
        ("sensor", f"{DECIMAL_ID}-powersensor"),
        ("sensor", f"{DECIMAL_ID}-energy"),
    ),
    "sensor_default": (
        ("sensor", f"{DECIMAL_ID}-powersensor"),
        ("sensor", f"{DECIMAL_ID}-energy"),
    ),
    "sensor_windowhandle": (("sensor", f"{DECIMAL_ID}-windowhandle"),),
    "sensor_contact": (("sensor", f"{DECIMAL_ID}-d5_00_01_contact"),),
    "sensor_shuttercontact": (("sensor", f"{DECIMAL_ID}-shuttercontact"),),
}


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class FrozenFormulaTests(unittest.TestCase):
    def test_every_row_shape_maps_to_its_literal_registry_identities(self):
        rows = _rows()
        self.assertEqual(set(rows), set(EXPECTED_IDENTITIES))
        for name, row in rows.items():
            with self.subTest(row=name):
                self.assertEqual(ui_device_entity_identities(row), EXPECTED_IDENTITIES[name])
                # The flow's primary identity is the row's own-platform entity.
                self.assertEqual(
                    options_flow._unique_id_for(row), EXPECTED_IDENTITIES[name][0][1]
                )

    def test_yaml_enum_device_class_renders_like_the_ui_string(self):
        yaml_entity = binary_sensor.EnOceanBinarySensor(
            DEVICE_ID, "door", BinarySensorDeviceClass.DOOR
        )
        ui_entity = binary_sensor.EnOceanBinarySensor(DEVICE_ID, "door", "door")
        self.assertEqual(yaml_entity.unique_id, f"{DECIMAL_ID}-door")
        self.assertEqual(ui_entity.unique_id, yaml_entity.unique_id)

    def test_sensor_platform_creates_exactly_the_shared_key_list(self):
        for device_class, keys in SENSOR_KEYS_BY_DEVICE_CLASS.items():
            with self.subTest(device_class=device_class):
                config = sensor.PLATFORM_SCHEMA(
                    {
                        "platform": DOMAIN,
                        "id": DEVICE_ID,
                        "name": "probe",
                        "device_class": device_class,
                    }
                )
                self.assertEqual(
                    [entity.unique_id for entity in sensor._entities_from_config(config)],
                    [sensor_unique_id(DEVICE_ID, key) for key in keys],
                )
        self.assertEqual(sensor_keys_for_device_class(None), ("powersensor", "energy"))
        self.assertEqual(sensor_keys_for_device_class("bogus"), ("powersensor", "energy"))
        self.assertEqual(set(SENSOR_KEYS_BY_DEVICE_CLASS), set(sensor.SENSOR_TYPES))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class PlatformParityAndRegistryTests(unittest.IsolatedAsyncioTestCase):
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

    async def _entities_created_by_platforms(self, row) -> list[tuple[str, str]]:
        """Run every platform's config-entry setup for one row."""
        entry = SimpleNamespace(options={CONF_UI_DEVICES: [row]})
        created: list[tuple[str, str]] = []
        for platform, module in (
            ("binary_sensor", binary_sensor),
            ("climate", climate),
            ("light", light),
            ("sensor", sensor),
            ("switch", switch),
        ):
            entities: list[Any] = []
            await module.async_setup_entry(self.hass, entry, entities.extend)
            created.extend((platform, entity.unique_id) for entity in entities)
        return created

    async def test_platform_entities_match_the_shared_identities_exactly(self):
        for name, row in _rows().items():
            with self.subTest(row=name):
                created = await self._entities_created_by_platforms(row)
                self.assertEqual(
                    sorted(created), sorted(ui_device_entity_identities(row))
                )
                self.assertEqual(len(created), len(set(created)))

    def _entry(self, rows):
        entry = ConfigEntry(
            data={"device": "/dev/test"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=1,
            options={CONF_UI_DEVICES: list(rows)},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        self.hass.config_entries._entries[entry.entry_id] = entry
        flow = options_flow.EnOceanOptionsFlow()
        flow.hass, flow.handler = self.hass, entry.entry_id
        return entry, flow

    async def test_deleting_a_row_removes_every_entity_it_created(self):
        rows = _rows()
        for name in ("sensor_power", "binary_a514", "sensor_contact"):
            with self.subTest(row=name):
                row = rows[name]
                _entry, flow = self._entry([row])
                for platform, unique_id in ui_device_entity_identities(row):
                    self.registry.async_get_or_create(platform, DOMAIN, unique_id)
                foreign = self.registry.async_get_or_create(
                    "sensor", "other_integration", f"{DECIMAL_ID}-energy"
                )

                await flow.async_step_manage({"device": "0"})
                result = await flow.async_step_delete_confirm({"confirm": True})

                self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
                self.assertEqual(result["data"][CONF_UI_DEVICES], [])
                for platform, unique_id in ui_device_entity_identities(row):
                    self.assertIsNone(
                        self.registry.async_get_entity_id(platform, DOMAIN, unique_id),
                        f"{platform} {unique_id} survived the deletion",
                    )
                self.assertEqual(
                    self.registry.async_get_entity_id(
                        "sensor", "other_integration", f"{DECIMAL_ID}-energy"
                    ),
                    foreign.entity_id,
                )
                self.registry.async_remove(foreign.entity_id)

    async def test_collision_is_detected_on_the_real_registry_identities(self):
        rows = _rows()
        cases = {
            # Only the companion energy counter exists: still a collision.
            "sensor_power": ("sensor", f"{DECIMAL_ID}-energy"),
            # The real contact identity, not the flow's former "-contact" guess.
            "sensor_contact": ("sensor", f"{DECIMAL_ID}-d5_00_01_contact"),
            # Only the diagnostic supply-voltage sensor of an A5-14-01 row.
            "binary_a514": ("sensor", f"{DECIMAL_ID}-a5_14_01_supply_voltage"),
        }
        for name, (platform, unique_id) in cases.items():
            with self.subTest(row=name):
                _entry, flow = self._entry([])
                entity = self.registry.async_get_or_create(platform, DOMAIN, unique_id)
                self.assertIsNone(flow._options_with_added_device(rows[name]))
                self.registry.async_remove(entity.entity_id)
                self.assertIsNotNone(flow._options_with_added_device(rows[name]))

    async def test_collision_is_detected_against_stored_companion_entities(self):
        rows = _rows()
        _entry, flow = self._entry([rows["binary_a514"]])
        # A plain sensor row that would mint the stored row's supply-voltage
        # identity cannot be known to the schema, so prove the check through
        # the stored A5-14-01 row itself and a duplicate power sensor.
        self.assertIsNone(flow._options_with_added_device(rows["binary_a514"]))
        _entry, flow = self._entry([rows["sensor_power"]])
        self.assertIsNone(flow._options_with_added_device(rows["sensor_default"]))
        self.assertIsNotNone(flow._options_with_added_device(rows["sensor_contact"]))

    async def test_stored_rows_are_validated_before_identities_are_computed(self):
        raw = [{"id": DEVICE_ID, "platform": "sensor"}]  # missing name: invalid
        self.assertEqual(valid_ui_devices(raw), [])
        _entry, flow = self._entry(raw)
        self.assertIsNotNone(flow._options_with_added_device(_rows()["sensor_power"]))


if __name__ == "__main__":
    unittest.main()

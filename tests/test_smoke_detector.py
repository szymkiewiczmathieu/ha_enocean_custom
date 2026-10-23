"""F6-05-02 smoke detectors: explicit profile, alarm plus low battery.

An RPS telegram never declares its EEP, so the profile is declared by the
operator (manual entry or the "declare profile" step) and never inferred. A
row that declares it keeps its historical ``<id>-<device_class>`` identity, so
the maintainer's detectors added as plain smoke binary sensors keep their
entities and gain a low-battery companion.
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
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.entity import EntityCategory

    from custom_components.enocean_custom import binary_sensor, options_flow
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.device_intelligence import (
        EEP_IMPLEMENTATIONS,
        ConfigMode,
    )
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RORG,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import Packet
    from custom_components.enocean_custom.identity import (
        ui_device_entity_identities,
    )
    from custom_components.enocean_custom.schema import CONF_UI_DEVICES

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

# The maintainer's rows, shaped exactly like the production .storage.
SENDER = [5, 140, 103, 208]
DECIMAL = "93087696"
LEGACY_SMOKE_ROW = {
    "channel": 0,
    "device_class": "smoke",
    "id": SENDER,
    "name": "Détecteur Fumée Entrée",
    "platform": "binary_sensor",
    "radio_metadata": None,
    "switch_type": "default",
}


def _rps(db0: int, status: int = 0x20):
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.RPS, db0, *SENDER, status],
        optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
    ).build()
    return Packet.parse_msg(bytearray(wire))[2]


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DecoderTests(unittest.TestCase):
    def test_documented_values(self):
        off = binary_sensor.decode_f6_05_02(_rps(0x00))
        self.assertEqual((off.alarm, off.battery_low), (False, False))
        on = binary_sensor.decode_f6_05_02(_rps(0x10, 0x30))
        self.assertEqual((on.alarm, on.battery_low), (True, False))
        low = binary_sensor.decode_f6_05_02(_rps(0x30))
        self.assertEqual((low.alarm, low.battery_low), (None, True))
        for other in (0x50, 0x70, 0xFF):
            self.assertIsNone(binary_sensor.decode_f6_05_02(_rps(other)))
        self.assertIsNone(
            binary_sensor.decode_f6_05_02(
                SimpleNamespace(rorg=RORG.BS1, data=[0xD5, 0x10])
            )
        )
        self.assertIsNone(
            binary_sensor.decode_f6_05_02(SimpleNamespace(rorg=RORG.RPS, data=[0xF6]))
        )

    def test_matrix_declares_the_profile_as_assisted_binary_sensor(self):
        implementation = EEP_IMPLEMENTATIONS["F6-05-02"]
        self.assertEqual(implementation.platforms, ("binary_sensor",))
        self.assertEqual(implementation.mode, ConfigMode.ASSISTED)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class IdentityAndPlatformTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _declared_row(self):
        return {
            **LEGACY_SMOKE_ROW,
            "radio_metadata": {
                "sender_id": SENDER,
                "eep": "F6-05-02",
                "eep_source": "manual",
                "evidence": "manual",
                "support": "manual",
            },
        }

    def test_legacy_identity_is_kept_and_a_companion_is_added(self):
        self.assertEqual(
            ui_device_entity_identities(LEGACY_SMOKE_ROW),
            (("binary_sensor", f"{DECIMAL}-smoke"),),
        )
        self.assertEqual(
            ui_device_entity_identities(self._declared_row()),
            (
                ("binary_sensor", f"{DECIMAL}-smoke"),
                ("binary_sensor", f"{DECIMAL}-f6-05-02-battery-low"),
            ),
        )

    async def _entities(self, row):
        created: list = []
        await binary_sensor.async_setup_entry(
            self.hass, SimpleNamespace(options={CONF_UI_DEVICES: [row]}), created.extend
        )
        for entity in created:
            entity.schedule_update_ha_state = lambda: None
        return created

    async def test_legacy_row_keeps_the_historical_rocker_decoder(self):
        (entity,) = await self._entities(LEGACY_SMOKE_ROW)
        self.assertIsInstance(entity, binary_sensor.EnOceanBinarySensor)
        self.assertNotIsInstance(entity, binary_sensor.EnOceanSmokeDetector)
        self.assertEqual(entity.unique_id, f"{DECIMAL}-smoke")
        self.assertEqual(entity.device_class, "smoke")

    async def test_declared_row_creates_detector_and_battery_companion(self):
        detector, battery = await self._entities(self._declared_row())
        self.assertIsInstance(detector, binary_sensor.EnOceanSmokeDetector)
        self.assertEqual(detector.unique_id, f"{DECIMAL}-smoke")
        self.assertEqual(detector.device_class, BinarySensorDeviceClass.SMOKE)
        self.assertEqual(battery.unique_id, f"{DECIMAL}-f6-05-02-battery-low")
        self.assertEqual(battery.device_class, BinarySensorDeviceClass.BATTERY)
        self.assertEqual(battery.entity_category, EntityCategory.DIAGNOSTIC)
        self.assertTrue(battery.has_entity_name)
        self.assertEqual(battery.translation_key, "smoke_battery_low")
        self.assertIsNone(detector.is_on)
        self.assertIsNone(battery.is_on)

        for entity in (detector, battery):
            entity._message_received_callback(_rps(0x10, 0x30))
        self.assertIs(detector.is_on, True)
        self.assertIs(battery.is_on, False)
        for entity in (detector, battery):
            entity._message_received_callback(_rps(0x30))
        self.assertIs(detector.is_on, True, "energy low says nothing about smoke")
        self.assertIs(battery.is_on, True)
        for entity in (detector, battery):
            entity._message_received_callback(_rps(0x00))
        self.assertIs(detector.is_on, False)
        self.assertIs(battery.is_on, False)
        # An undocumented value changes nothing.
        for entity in (detector, battery):
            entity._message_received_callback(_rps(0x50))
        self.assertIs(detector.is_on, False)
        self.assertIs(battery.is_on, False)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DeclareProfileFlowTests(unittest.IsolatedAsyncioTestCase):
    """The options flow enriches an existing row without changing its identity."""

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

    def _flow(self, rows):
        entry = ConfigEntry(
            data={"device": "/dev/test"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=2,
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
        return flow, entry

    async def test_declaring_f6_05_02_keeps_the_entity_and_adds_the_companion(self):
        declared = {
            "id": [1, 2, 3, 4],
            "platform": "binary_sensor",
            "name": "Window",
            "device_class": "door",
            "radio_metadata": {
                "sender_id": [1, 2, 3, 4],
                "eep": "A5-14-01",
                "eep_source": "radio_declared",
                "evidence": "exact",
            },
        }
        flow, _entry = self._flow([declared, LEGACY_SMOKE_ROW])
        existing = self.registry.async_get_or_create(
            "binary_sensor", DOMAIN, f"{DECIMAL}-smoke"
        )
        menu = await flow.async_step_init()
        self.assertIn("declare_profile", menu["menu_options"])

        form = await flow.async_step_declare_profile()
        self.assertEqual(form["type"], FlowResultType.FORM)
        options = form["data_schema"].schema
        (device_key,) = [key for key in options if str(key) == "device"]
        choices = [option["value"] for option in options[device_key].config["options"]]
        self.assertEqual(choices, ["1"], "only the undeclared row is offered")

        eep_form = await flow.async_step_declare_profile({"device": "1"})
        self.assertEqual(eep_form["step_id"], "declare_profile_eep")
        self.assertEqual(
            eep_form["description_placeholders"],
            {
                "name": "Détecteur Fumée Entrée",
                "platform": "binary_sensor",
                "captured_id": "05:8C:67:D0",
            },
        )
        invalid = await flow.async_step_declare_profile_eep({"manual_eep": "F6-5-2"})
        self.assertEqual(invalid["errors"], {"manual_eep": "invalid_eep"})

        result = await flow.async_step_declare_profile_eep({"manual_eep": "f6-05-02"})
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        rows = result["data"][CONF_UI_DEVICES]
        self.assertEqual(rows[0], declared, "the other row is untouched")
        metadata = rows[1]["radio_metadata"]
        self.assertEqual(metadata["eep"], "F6-05-02")
        self.assertEqual(metadata["eep_source"], "manual")
        self.assertEqual(metadata["evidence"], "manual")
        self.assertEqual(metadata["sender_id"], SENDER)
        self.assertEqual(
            ui_device_entity_identities(rows[1])[0],
            ("binary_sensor", f"{DECIMAL}-smoke"),
        )
        self.assertEqual(
            self.registry.async_get_entity_id(
                "binary_sensor", DOMAIN, f"{DECIMAL}-smoke"
            ),
            existing.entity_id,
        )

    async def test_a_profile_that_changes_the_identity_is_refused(self):
        flow, _entry = self._flow([LEGACY_SMOKE_ROW])
        await flow.async_step_declare_profile({"device": "0"})
        # A5-14-01 rows are created as EnOceanA514Contact with another unique_id.
        refused = await flow.async_step_declare_profile_eep({"manual_eep": "A5-14-01"})
        self.assertEqual(refused["type"], FlowResultType.FORM)
        self.assertEqual(refused["errors"], {"manual_eep": "profile_changes_identity"})

    async def test_companion_collision_is_refused(self):
        flow, _entry = self._flow([LEGACY_SMOKE_ROW])
        self.registry.async_get_or_create(
            "binary_sensor", DOMAIN, f"{DECIMAL}-f6-05-02-battery-low"
        )
        await flow.async_step_declare_profile({"device": "0"})
        refused = await flow.async_step_declare_profile_eep({"manual_eep": "F6-05-02"})
        self.assertEqual(refused["errors"], {"base": "unique_id_exists"})

    async def test_nothing_to_declare_aborts(self):
        flow, _entry = self._flow(
            [
                {
                    "id": [1, 2, 3, 4],
                    "platform": "binary_sensor",
                    "name": "Window",
                    "radio_metadata": {
                        "sender_id": [1, 2, 3, 4],
                        "eep": "A5-14-01",
                        "eep_source": "radio_declared",
                        "evidence": "exact",
                    },
                }
            ]
        )
        result = await flow.async_step_declare_profile()
        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(result["reason"], "no_undeclared_devices")


if __name__ == "__main__":
    unittest.main()

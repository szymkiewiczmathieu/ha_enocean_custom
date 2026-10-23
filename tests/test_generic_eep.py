"""Generic EEP.xml-driven sensors (device class ``eep``).

Bit offsets, ranges and scales come from the vendored EEP.xml; only shortcuts
are mapped to Home Assistant semantics. Frames are built through the real ESP3
wire format so the byte offsets are proven, not assumed.
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
    import voluptuous as vol
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass
    from homeassistant.components.sensor import SensorDeviceClass
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.entity import EntityCategory

    from custom_components.enocean_custom import binary_sensor, options_flow, sensor
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.device_intelligence import (
        SupportVerdict,
        implementation_for,
        recommended_platform,
        support_for,
    )
    from custom_components.enocean_custom.eep_catalog import (
        EXCLUDED_PROFILES,
        generic_profile,
        generic_profiles,
        is_generic_supported,
    )
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RORG,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import Packet
    from custom_components.enocean_custom.identity import ui_device_entity_identities
    from custom_components.enocean_custom.schema import (
        CONF_UI_DEVICES,
        UI_DEVICE_SCHEMA,
        valid_ui_devices,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [0x01, 0x02, 0x03, 0x04]
DECIMAL = "16909060"


def _parsed(data):
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=data,
        optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
    ).build()
    return Packet.parse_msg(bytearray(wire))[2]


def _4bs(db3, db2, db1, db0):
    return _parsed([RORG.BS4, db3, db2, db1, db0, *SENDER, 0x00])


def _bits_to_bytes(fields: list[tuple[int, int]]) -> list[int]:
    """Pack (size, value) pairs MSB-first into whole bytes."""
    bits = "".join(format(value, f"0{size}b") for size, value in fields)
    bits += "0" * (-len(bits) % 8)
    return [int(bits[i : i + 8], 2) for i in range(0, len(bits), 8)]


def _row(eep: str, **extra):
    return UI_DEVICE_SCHEMA(
        {
            "id": SENDER,
            "platform": "sensor",
            "name": "Probe",
            "device_class": "eep",
            "radio_metadata": {
                "sender_id": SENDER,
                "eep": eep,
                "eep_source": "radio_declared",
                "evidence": "exact",
            },
            **extra,
        }
    )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class CatalogTests(unittest.TestCase):
    def test_catalog_shape(self):
        profiles = generic_profiles()
        self.assertEqual(len(profiles), 43)
        for excluded in EXCLUDED_PROFILES:
            self.assertNotIn(excluded, profiles)
        self.assertNotIn("D2-01-12", profiles, "actuators are never generic")
        self.assertNotIn("D2-05-00", profiles)
        weather = profiles["A5-13-01"]
        self.assertEqual(weather.command, 1)
        self.assertEqual(
            [field.shortcut for field in weather.exposed_fields],
            ["DWS", "TMP", "WND", "RAN"],
        )
        indoor = profiles["D2-14-41"]
        self.assertEqual(indoor.rorg, RORG.VLD)
        self.assertEqual(indoor.payload_bits, 68)
        self.assertTrue(is_generic_supported("a5-02-05"))
        self.assertFalse(is_generic_supported("A5-12-01"))
        self.assertFalse(is_generic_supported(None))

    def test_semantics(self):
        co2 = generic_profile("A5-09-04")
        by_shortcut = {field.shortcut: field for field in co2.exposed_fields}
        self.assertEqual(by_shortcut["Conc"].semantics.device_class, "carbon_dioxide")
        self.assertEqual(by_shortcut["Conc"].normalized_unit, "ppm")
        voc = generic_profile("A5-09-05").exposed_fields[0]
        self.assertEqual(voc.semantics.device_class, "volatile_organic_compounds_parts")
        self.assertEqual(voc.normalized_unit, "ppb")
        setpoint = next(
            f for f in generic_profile("A5-10-03").fields if f.shortcut == "SP"
        )
        self.assertIsNone(setpoint.normalized_unit, "a raw set point has no unit")
        pirs = next(
            f for f in generic_profile("A5-08-01").fields if f.shortcut == "PIRS"
        )
        self.assertEqual(pirs.semantics.on_values, frozenset({0}))
        svc = next(f for f in generic_profile("A5-07-01").fields if f.shortcut == "SVC")
        self.assertTrue(svc.semantics.diagnostic)
        self.assertFalse(svc.semantics.enabled_default)

    def test_device_intelligence_reports_the_generic_implementation(self):
        self.assertEqual(implementation_for("A5-02-05").platforms, ("sensor",))
        self.assertEqual(support_for("A5-02-05"), SupportVerdict.SUPPORTED)
        self.assertEqual(support_for("A5-02-05", manual=True), SupportVerdict.MANUAL)
        self.assertEqual(
            recommended_platform({"eep": "A5-04-01", "eep_source": "radio_declared"}),
            "sensor",
        )
        self.assertIsNone(implementation_for("A5-10-FF"))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DecodingTests(unittest.TestCase):
    def test_a5_02_05_temperature_is_inverted_8_bit(self):
        profile = generic_profile("A5-02-05")
        # DB1 carries the temperature, DB0 bit 3 marks a data telegram.
        self.assertEqual(profile.decode(_4bs(0, 0, 255, 0x08).data), {"tmp": 0.0})
        self.assertEqual(profile.decode(_4bs(0, 0, 0, 0x08).data), {"tmp": 40.0})
        decoded = profile.decode(_4bs(0, 0, 128, 0x08).data)
        self.assertAlmostEqual(decoded["tmp"], 19.922, places=3)
        # A teach-in telegram (LRN bit clear) is never a measurement.
        self.assertIsNone(profile.decode(_4bs(0x10, 0x28, 0x00, 0x80).data))
        self.assertIsNone(profile.decode([RORG.RPS, 0x50, *SENDER, 0x30]))

    def test_a5_04_01_range_limits(self):
        profile = generic_profile("A5-04-01")
        self.assertEqual(
            profile.decode(_4bs(0, 125, 125, 0x08).data), {"hum": 50.0, "tmp": 20.0}
        )
        # 251..255 are outside the documented 0..250 range: field omitted.
        self.assertEqual(profile.decode(_4bs(0, 255, 125, 0x08).data), {"tmp": 20.0})

    def test_a5_09_04_co2(self):
        profile = generic_profile("A5-09-04")
        decoded = profile.decode(_4bs(100, 100, 51, 0x08).data)
        self.assertEqual(decoded, {"hum": 50.0, "conc": 1000.0, "tmp": 10.2})

    def test_a5_13_01_decodes_the_weather_command_only(self):
        profile = generic_profile("A5-13-01")
        # DB3 dawn 255 -> 999 lx, DB2 temp 128 -> -40 + 120 * 128 / 255 °C,
        # DB1 wind 0, DB0: command 1 in bits 24..27 (high nibble), LRN bit 3,
        # rain bit 1.
        decoded = profile.decode(_4bs(255, 128, 0, 0x1A).data)
        self.assertEqual(decoded["dws"], 999.0)
        self.assertAlmostEqual(decoded["tmp"], 20.235, places=3)
        self.assertEqual(decoded["wnd"], 0.0)
        self.assertEqual(decoded["ran"], 1)
        self.assertIsNone(profile.decode(_4bs(255, 128, 0, 0x28).data), "command 2")

    def test_d2_14_41_vld_layout(self):
        profile = generic_profile("D2-14-41")
        payload = _bits_to_bytes(
            [
                (10, 600),  # TMP 600/1000 -> 20 °C
                (8, 100),  # HUM 100/200 -> 50 %
                (17, 1500),  # ILL 1500 lx
                (2, 0),  # ACC periodic
                (10, 500),  # ACX -> 0 g
                (10, 500),
                (10, 500),
                (1, 0),  # CO open
            ]
        )
        packet = _parsed([RORG.VLD, *payload, *SENDER, 0x00])
        decoded = profile.decode(packet.data)
        self.assertEqual(decoded["tmp"], 20.0)
        self.assertEqual(decoded["hum"], 50.0)
        self.assertEqual(decoded["ill"], 1500.0)
        self.assertEqual(decoded["acx"], 0.0)
        self.assertEqual(decoded["co"], 0)
        self.assertIsNone(profile.decode([RORG.VLD, 1, 2, *SENDER, 0]), "too short")


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class RowAndEntityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def test_schema_requires_a_generic_profile_for_the_eep_class(self):
        self.assertEqual(_row("A5-04-01")["device_class"], "eep")
        for bad in ("A5-12-01", "D2-01-12", "A5-10-FF"):
            with self.subTest(eep=bad), self.assertRaises(vol.Invalid):
                _row(bad)
        with self.assertRaises(vol.Invalid):
            UI_DEVICE_SCHEMA(
                {"id": SENDER, "platform": "sensor", "name": "x", "device_class": "eep"}
            )
        self.assertEqual(
            valid_ui_devices(
                [
                    {
                        "id": SENDER,
                        "platform": "sensor",
                        "name": "x",
                        "device_class": "eep",
                    }
                ]
            ),
            [],
        )
        self.assertNotIn("eep", sensor.SENSOR_TYPES, "YAML schema is unchanged")

    def test_identities_follow_the_profile_fields(self):
        self.assertEqual(
            ui_device_entity_identities(_row("A5-07-01")),
            (
                ("sensor", f"{DECIMAL}-eep_svc"),
                ("binary_sensor", f"{DECIMAL}-eep_pir"),
            ),
        )
        self.assertEqual(
            ui_device_entity_identities(_row("A5-02-05")),
            (("sensor", f"{DECIMAL}-eep_tmp"),),
        )

    async def test_platforms_create_the_mapped_entities(self):
        row = _row("A5-08-01")
        entry = SimpleNamespace(options={CONF_UI_DEVICES: [row]})
        sensors: list = []
        await sensor.async_setup_entry(self.hass, entry, sensors.extend)
        binaries: list = []
        await binary_sensor.async_setup_entry(self.hass, entry, binaries.extend)
        generic = [e for e in sensors if isinstance(e, sensor.EnOceanEepValueSensor)]
        by_key = {e.unique_id: e for e in generic}
        self.assertEqual(
            set(by_key),
            {f"{DECIMAL}-eep_svc", f"{DECIMAL}-eep_ill", f"{DECIMAL}-eep_tmp"},
        )
        temperature = by_key[f"{DECIMAL}-eep_tmp"]
        self.assertEqual(temperature.device_class, SensorDeviceClass.TEMPERATURE)
        self.assertEqual(temperature.native_unit_of_measurement, "°C")
        self.assertEqual(temperature.translation_key, "eep_temperature")
        self.assertTrue(temperature.has_entity_name)
        voltage = by_key[f"{DECIMAL}-eep_svc"]
        self.assertEqual(voltage.entity_category, EntityCategory.DIAGNOSTIC)
        self.assertFalse(voltage.entity_registry_enabled_default)
        (occupancy,) = binaries
        self.assertIsInstance(occupancy, binary_sensor.EnOceanEepBinarySensor)
        self.assertEqual(occupancy.unique_id, f"{DECIMAL}-eep_pirs")
        self.assertEqual(occupancy.device_class, BinarySensorDeviceClass.OCCUPANCY)
        created = {("sensor", e.unique_id) for e in generic} | {
            ("binary_sensor", occupancy.unique_id)
        }
        self.assertEqual(created, set(ui_device_entity_identities(row)))

        for entity in (*generic, occupancy):
            entity.schedule_update_ha_state = lambda: None
            # SVC 125 -> 2.5 V, ILL 128 -> 256 lx, TMP 128 -> 25.6 °C,
            # DB0: LRN set, PIRS bit 1 clear (= PIR on), OCC bit 0 set.
            entity._message_received_callback(_4bs(125, 128, 128, 0x09))
        self.assertEqual(voltage.native_value, 2.5)
        self.assertEqual(by_key[f"{DECIMAL}-eep_ill"].native_value, 256.0)
        self.assertEqual(temperature.native_value, 25.6)
        self.assertIs(occupancy.is_on, True)
        self.assertEqual(
            temperature.extra_state_attributes, {"eep": "A5-08-01", "eep_field": "TMP"}
        )
        occupancy._message_received_callback(_4bs(125, 128, 128, 0x0B))
        self.assertIs(occupancy.is_on, False)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class OptionsFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
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
        self.hass.config_entries._entries[entry.entry_id] = entry
        self.flow = options_flow.EnOceanOptionsFlow()
        self.flow.hass, self.flow.handler = self.hass, entry.entry_id
        self.flow._captured_id = SENDER
        self.flow._pending_platform = "sensor"
        self.flow._pending_name = "Probe"

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _defaults(self, schema):
        return {
            str(key): key.default()
            for key in schema.schema
            if key.default is not vol.UNDEFINED
        }

    async def test_declared_profile_pre_selects_the_generic_class(self):
        self.flow._radio_metadata = {
            "sender_id": SENDER,
            "eep": "A5-04-01",
            "eep_source": "radio_declared",
            "evidence": "exact",
        }
        defaults = self._defaults(self.flow._device_details_schema("sensor"))
        self.assertEqual(defaults["device_class"], "eep")
        self.assertEqual(defaults["eep"], "A5-04-01")
        result = await self.flow.async_step_device_details(
            {
                "device_class": "eep",
                "max_temp": 40,
                "min_temp": 0,
                "range_from": 255,
                "range_to": 0,
            }
        )
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        row = result["data"][CONF_UI_DEVICES][0]
        self.assertEqual(row["device_class"], "eep")
        self.assertEqual(row["radio_metadata"]["eep"], "A5-04-01")
        self.assertEqual(row["radio_metadata"]["eep_source"], "radio_declared")

    async def test_unknown_profile_requires_a_choice_recorded_as_manual(self):
        self.flow._radio_metadata = {"sender_id": SENDER, "evidence": "profile_unknown"}
        defaults = self._defaults(self.flow._device_details_schema("sensor"))
        self.assertEqual(defaults["device_class"], "powersensor")
        self.assertNotIn("eep", defaults)
        base = {"max_temp": 40, "min_temp": 0, "range_from": 255, "range_to": 0}
        refused = await self.flow.async_step_device_details(
            {"device_class": "eep", **base}
        )
        self.assertEqual(refused["type"], FlowResultType.FORM)
        self.assertEqual(refused["errors"], {"eep": "eep_unsupported"})
        refused = await self.flow.async_step_device_details(
            {"device_class": "eep", "eep": "A5-12-01", **base}
        )
        self.assertEqual(refused["errors"], {"eep": "eep_unsupported"})
        result = await self.flow.async_step_device_details(
            {"device_class": "eep", "eep": "A5-02-05", **base}
        )
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        metadata = result["data"][CONF_UI_DEVICES][0]["radio_metadata"]
        self.assertEqual(metadata["eep"], "A5-02-05")
        self.assertEqual(metadata["eep_source"], "manual")
        self.assertEqual(metadata["evidence"], "manual")


if __name__ == "__main__":
    unittest.main()

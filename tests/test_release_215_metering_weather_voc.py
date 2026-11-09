"""Release 2.15 workstream 4: meters, weather commands and dual-unit VOC."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.enocean_custom import identity as identity_module
from custom_components.enocean_custom import sensor
from custom_components.enocean_custom.eep_catalog import generic_profile
from custom_components.enocean_custom.enocean_library.protocol.constants import (
    PACKET,
    RORG,
)
from custom_components.enocean_custom.enocean_library.protocol.packet import Packet
from custom_components.enocean_custom.identity import generic_eep_identities

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


def _entity(eep, slug):
    profile = generic_profile(eep)
    field = next(field for field in profile.exposed_fields if field.slug == slug)
    entity = sensor.EnOceanEepValueSensor(SENDER, eep, profile, field)
    entity.schedule_update_ha_state = lambda: None
    return entity


class MeterTests(unittest.TestCase):
    def test_gas_and_water_vectors_have_independent_entities_and_ti(self):
        for eep, device_class in (
            ("A5-12-02", SensorDeviceClass.GAS),
            ("A5-12-03", SensorDeviceClass.WATER),
        ):
            with self.subTest(eep=eep):
                profile = generic_profile(eep)
                self.assertEqual(
                    profile.decode(_4bs(0x00, 0x00, 0x7B, 0x0D).data),
                    {"flow": 12.3},
                )
                self.assertEqual(
                    profile.decode(_4bs(0x00, 0x30, 0x39, 0x0A).data),
                    {"cumul": 123.45},
                )
                volume = _entity(eep, "cumul")
                flow = _entity(eep, "flow")
                self.assertEqual(volume.unique_id, f"{DECIMAL}-eep_cumul")
                self.assertEqual(flow.unique_id, f"{DECIMAL}-eep_flow")
                self.assertEqual(volume.device_class, device_class)
                self.assertEqual(volume.native_unit_of_measurement, "m³")
                self.assertEqual(volume.state_class, SensorStateClass.TOTAL_INCREASING)
                self.assertEqual(flow.device_class, SensorDeviceClass.VOLUME_FLOW_RATE)
                self.assertEqual(flow.native_unit_of_measurement, "L/s")
                self.assertEqual(flow.state_class, SensorStateClass.MEASUREMENT)
                volume.value_changed(_4bs(0x00, 0x30, 0x39, 0x0A))
                flow.value_changed(_4bs(0x00, 0x30, 0x39, 0x0A))
                self.assertEqual(volume.native_value, 123.45)
                self.assertIsNone(flow.native_value)
                self.assertEqual(volume.extra_state_attributes["tariff_info"], 0)
                flow.value_changed(_4bs(0x00, 0x00, 0x7B, 0x0D))
                volume.value_changed(_4bs(0x00, 0x00, 0x7B, 0x0D))
                self.assertEqual(flow.native_value, 12.3)
                self.assertEqual(volume.native_value, 123.45)
                self.assertIsNone(generic_profile(eep).decode(_4bs(0, 0, 0, 0).data))


class WeatherTests(unittest.TestCase):
    def test_weather_and_sun_commands_keep_existing_values_and_add_lx_night(self):
        profile = generic_profile("A5-13-01")
        self.assertEqual(
            profile.decode(_4bs(0xFF, 0x80, 0x00, 0x28).data),
            {"snw": 150000, "sns": 75294, "sne": 0},
        )
        weather = profile.decode(_4bs(0xFF, 0x80, 0x00, 0x18).data)
        self.assertEqual(weather["dws"], 999.0)
        self.assertAlmostEqual(weather["tmp"], 20.235, places=3)
        self.assertEqual(weather["wnd"], 0.0)
        self.assertEqual(weather["ran"], 0)
        self.assertEqual(weather["d_n"], 0)
        self.assertEqual(profile.decode(_4bs(0xFF, 0x80, 0x00, 0x1C).data)["d_n"], 1)
        self.assertIsNone(profile.decode(_4bs(0xFF, 0x80, 0x00, 0x38).data))

        sun = _entity("A5-13-01", "sns")
        self.assertEqual(sun.device_class, SensorDeviceClass.ILLUMINANCE)
        self.assertEqual(sun.native_unit_of_measurement, "lx")
        sun.value_changed(_4bs(0xFF, 0x80, 0x00, 0x2C))
        self.assertEqual(sun.extra_state_attributes["hem"], 1)

    def test_existing_weather_identity_tuple_is_frozen_and_companions_are_additive(
        self,
    ):
        self.assertEqual(
            generic_eep_identities(SENDER, "A5-13-01"),
            (
                ("sensor", f"{DECIMAL}-eep_dws"),
                ("sensor", f"{DECIMAL}-eep_tmp"),
                ("sensor", f"{DECIMAL}-eep_wnd"),
                ("binary_sensor", f"{DECIMAL}-eep_ran"),
            ),
        )
        additive = identity_module.additive_generic_eep_identities(SENDER, "A5-13-01")
        self.assertEqual(
            set(additive),
            {
                ("binary_sensor", f"{DECIMAL}-eep_d_n"),
                ("sensor", f"{DECIMAL}-eep_snw"),
                ("sensor", f"{DECIMAL}-eep_sns"),
                ("sensor", f"{DECIMAL}-eep_sne"),
            },
        )


class DualUnitVocTests(unittest.TestCase):
    def test_unit_selects_one_entity_and_scm_scales_without_off_by_one(self):
        profile = generic_profile("A5-09-0C")
        self.assertEqual(
            profile.decode(_4bs(0x01, 0xF4, 0x00, 0x0A).data), {"conc": 500.0}
        )
        self.assertEqual(
            profile.decode(_4bs(0x01, 0xF4, 0x00, 0x0C).data), {"vocm": 5.0}
        )
        parts = _entity("A5-09-0C", "conc")
        mass = _entity("A5-09-0C", "vocm")
        self.assertEqual(
            parts.device_class, SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS
        )
        self.assertEqual(parts.native_unit_of_measurement, "ppb")
        self.assertEqual(
            mass.device_class, SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS
        )
        self.assertEqual(mass.native_unit_of_measurement, "µg/m³")
        parts.value_changed(_4bs(0x01, 0xF4, 0x00, 0x0A))
        mass.value_changed(_4bs(0x01, 0xF4, 0x00, 0x0A))
        self.assertEqual(parts.native_value, 500.0)
        self.assertIsNone(mass.native_value)
        mass.value_changed(_4bs(0x01, 0xF4, 0x00, 0x0C))
        parts.value_changed(_4bs(0x01, 0xF4, 0x00, 0x0C))
        self.assertEqual(mass.native_value, 5.0)
        self.assertEqual(parts.native_value, 500.0)
        self.assertEqual(mass.extra_state_attributes["scale_multiplier"], 0.01)
        self.assertEqual(mass.extra_state_attributes["voc_id"], 0)
        self.assertIsNone(profile.decode(_4bs(0x01, 0xF4, 0x00, 0x00).data))


if __name__ == "__main__":
    unittest.main()

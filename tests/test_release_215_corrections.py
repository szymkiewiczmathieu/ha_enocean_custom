"""Release 2.15 workstream 1: verified A5-09-05 and A5-12-01 vectors."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.enocean_custom import sensor
from custom_components.enocean_custom.availability import availability_settings
from custom_components.enocean_custom.const import DOMAIN
from custom_components.enocean_custom.eep_catalog import generic_profile
from custom_components.enocean_custom.enocean_library.protocol.constants import (
    PACKET,
    RORG,
)
from custom_components.enocean_custom.enocean_library.protocol.packet import Packet
from custom_components.enocean_custom.identity import ui_device_entity_identities
from custom_components.enocean_custom.schema import UI_DEVICE_SCHEMA

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


def _power_row(eep="A5-12-01"):
    return UI_DEVICE_SCHEMA(
        {
            "id": SENDER,
            "platform": "sensor",
            "name": "Meter",
            "device_class": "powersensor",
            "radio_metadata": {"eep": eep} if eep else {},
        }
    )


class A50905ScaleTests(unittest.TestCase):
    def test_scm_vectors_and_lrn_rejection(self):
        profile = generic_profile("A5-09-05")
        for db0, expected in ((0x08, 5.0), (0x09, 50.0), (0x0A, 500.0), (0x0B, 5000.0)):
            with self.subTest(db0=db0):
                self.assertEqual(
                    profile.decode(_4bs(0x01, 0xF4, 0x00, db0).data)["conc"], expected
                )
        self.assertIsNone(profile.decode(_4bs(0x01, 0xF4, 0x00, 0x00).data))

    def test_scale_multiplier_is_exposed_without_changing_identity(self):
        entity = sensor.EnOceanEepValueSensor(
            SENDER,
            "VOC",
            generic_profile("A5-09-05"),
            generic_profile("A5-09-05").exposed_fields[0],
        )
        entity.schedule_update_ha_state = lambda: None
        self.assertEqual(entity.unique_id, f"{DECIMAL}-eep_conc")
        entity.value_changed(_4bs(0x01, 0xF4, 0x00, 0x09))
        self.assertEqual(entity.native_value, 50.0)
        self.assertEqual(entity.extra_state_attributes["scale_multiplier"], 0.1)
        self.assertEqual(entity.extra_state_attributes["voc_id"], 0)


class A51201AdditiveEnergyTests(unittest.TestCase):
    def _entities(self, eep="A5-12-01", hass=None):
        entities = sensor._row_entities(
            [_power_row(eep)], availability_settings({}), hass
        )
        return {entity.unique_id: entity for entity in entities}

    def test_new_kwh_entity_and_historical_wh_entity_follow_their_own_contracts(self):
        entities = self._entities()
        corrected = entities[f"{DECIMAL}-a5_12_01_energy"]
        legacy = entities[f"{DECIMAL}-energy"]
        power = entities[f"{DECIMAL}-powersensor"]
        for entity in (corrected, legacy, power):
            entity.schedule_update_ha_state = lambda: None
        self.assertEqual(corrected.native_unit_of_measurement, "kWh")
        self.assertEqual(legacy.native_unit_of_measurement, "Wh")
        self.assertTrue(corrected.entity_registry_enabled_default)

        vectors = (
            ((0x00, 0x30, 0x39, 0x08), 12345.0),
            ((0x00, 0x30, 0x39, 0x09), 1234.5),
            ((0x00, 0x30, 0x39, 0x0A), 123.45),
            ((0x00, 0x30, 0x39, 0x0B), 12.345),
        )
        for data, expected in vectors:
            with self.subTest(data=data):
                packet = _4bs(*data)
                corrected.value_changed(packet)
                legacy.value_changed(packet)
                self.assertEqual(corrected.native_value, expected)
                # 2.14.1 behavior is deliberately preserved in 2.15.
                self.assertEqual(legacy.native_value, expected)

        packet = _4bs(0x00, 0x30, 0x39, 0x0C)
        corrected.value_changed(packet)
        legacy.value_changed(packet)
        power.value_changed(packet)
        self.assertEqual(corrected.native_value, 12.345)
        self.assertEqual(legacy.native_value, 12.345)
        self.assertEqual(power.native_value, 12345.0)
        power.value_changed(_4bs(0x00, 0x11, 0xD7, 0x0D))
        self.assertEqual(power.native_value, 456.7)

    def test_non_a5_row_gets_the_additive_entity_disabled_by_default(self):
        entities = self._entities("D2-01-0C")
        corrected = entities[f"{DECIMAL}-a5_12_01_energy"]
        corrected.schedule_update_ha_state = lambda: None
        self.assertFalse(corrected.entity_registry_enabled_default)
        corrected.value_changed(
            _parsed([RORG.VLD, 0x07, 0x20, 0, 0, 0, 100, *SENDER, 0])
        )
        self.assertIsNone(corrected.native_value)

    def test_d2_energy_stays_in_wh_and_frozen_identities_stay_frozen(self):
        entities = self._entities("D2-01-0C")
        legacy = entities[f"{DECIMAL}-energy"]
        legacy.schedule_update_ha_state = lambda: None
        legacy.value_changed(_parsed([RORG.VLD, 0x07, 0x20, 0, 0, 0, 100, *SENDER, 0]))
        self.assertEqual(legacy.native_value, 100)
        legacy.value_changed(_parsed([RORG.VLD, 0x07, 0x40, 0, 0, 0, 100, *SENDER, 0]))
        self.assertEqual(legacy.native_value, 100000)
        self.assertEqual(
            ui_device_entity_identities(_power_row()),
            (("sensor", f"{DECIMAL}-powersensor"), ("sensor", f"{DECIMAL}-energy")),
        )

    def test_repair_is_non_fixable_and_scoped_to_the_a5_row(self):
        with patch(
            "custom_components.enocean_custom.sensor.ir.async_create_issue"
        ) as create:
            self._entities(hass=SimpleNamespace())
        create.assert_called_once()
        args, kwargs = create.call_args
        self.assertEqual(args[:2], (ANY, DOMAIN))
        self.assertFalse(kwargs["is_fixable"])
        self.assertEqual(kwargs["translation_key"], "a5_12_01_energy_migration")


if __name__ == "__main__":
    unittest.main()

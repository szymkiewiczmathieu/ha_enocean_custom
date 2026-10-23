"""D2-14-30 multi-sensor smoke detectors (layout from openHAB and FHEM).

The payload is rebuilt bit by bit from the documented layout, pushed through
the real ESP3 wire format, and compared with the arithmetic both reference
implementations use. Nothing here proves the hardware; the tests prove that
the decoder matches those two implementations.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass
    from homeassistant.components.sensor import SensorDeviceClass
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import EntityCategory

    from custom_components.enocean_custom import binary_sensor, sensor
    from custom_components.enocean_custom.availability import silence_category
    from custom_components.enocean_custom.d2_14 import decode_d2_14_30
    from custom_components.enocean_custom.device_intelligence import EEP_IMPLEMENTATIONS
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RORG,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import Packet
    from custom_components.enocean_custom.identity import ui_device_entity_identities
    from custom_components.enocean_custom.schema import (
        CONF_UI_DEVICES,
        UI_DEVICE_SCHEMA,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [0x05, 0x01, 0x02, 0x03]
DECIMAL = str((5 << 24) | (1 << 16) | (2 << 8) | 3)


def _payload(
    *,
    smoke=0,
    fault=0,
    maintenance=0,
    humidity_fault=0,
    temperature_fault=0,
    weeks=0,
    energy=0,
    life=0,
    temperature_raw=0,
    humidity_raw=0,
    comfort=0,
    air=0,
):
    fields = [
        (1, smoke),
        (1, fault),
        (1, maintenance),
        (1, humidity_fault),
        (1, temperature_fault),
        (8, weeks),
        (2, energy),
        (8, life),
        (8, temperature_raw),
        (8, humidity_raw),
        (2, comfort),
        (3, air),
        (4, 0),
    ]
    bits = "".join(format(value, f"0{size}b") for size, value in fields)
    return [int(bits[i : i + 8], 2) for i in range(0, 48, 8)]


def _frame(**kwargs):
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.VLD, *_payload(**kwargs), *SENDER, 0x00],
        optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
    ).build()
    return Packet.parse_msg(bytearray(wire))[2]


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DecoderTests(unittest.TestCase):
    def test_layout_matches_both_reference_implementations(self):
        packet = _frame(
            smoke=1,
            fault=0,
            maintenance=1,
            humidity_fault=0,
            temperature_fault=1,
            weeks=37,
            energy=2,
            life=42,
            temperature_raw=105,
            humidity_raw=91,
            comfort=1,
            air=2,
        )
        status = decode_d2_14_30(packet.data)
        self.assertTrue(status.smoke_alarm)
        self.assertFalse(status.sensor_fault)
        self.assertTrue(status.maintenance_due)
        self.assertFalse(status.humidity_out_of_range)
        self.assertTrue(status.temperature_out_of_range)
        self.assertEqual(status.maintenance_weeks, 37)
        self.assertEqual(status.energy_storage, "low")
        self.assertEqual(status.remaining_life_months, 42)
        self.assertEqual(status.temperature, 21.0)
        self.assertEqual(status.humidity, 45.5)
        self.assertEqual(status.comfort, "medium")
        self.assertEqual(status.air_quality, "humidity_high")

        # The same bytes through the openHAB arithmetic (bytes[] = payload).
        b = packet.data[1:7]
        self.assertEqual(bool(b[0] & 0x80), status.smoke_alarm)
        self.assertEqual(((b[2] << 7) + (b[3] >> 1)) & 0xFF, 105)
        self.assertEqual(((b[3] << 7) + (b[4] >> 1)) & 0xFF, 91)
        self.assertEqual((((b[4] & 0b1) << 1) + (b[5] >> 7)), 1)
        self.assertEqual((b[5] >> 4) & 0b111, 2)
        self.assertEqual((b[1] & 0b110) >> 1, 2)
        # ... and the FHEM arithmetic (db[5] = first payload byte).
        db = list(reversed(b))
        self.assertEqual(((db[5] & 7) << 5 | db[4] >> 3), 37)
        self.assertEqual(((db[4] & 1) << 7 | db[3] >> 1), 42)
        self.assertEqual(((db[3] & 1) << 7 | db[2] >> 1) / 5, 21.0)
        self.assertEqual(((db[2] & 1) << 7 | db[1] >> 1) / 2, 45.5)

    def test_reserved_values_become_none(self):
        status = decode_d2_14_30(
            _frame(
                life=200, temperature_raw=251, humidity_raw=255, air=5, energy=0
            ).data
        )
        self.assertIsNone(status.remaining_life_months)
        self.assertIsNone(status.temperature)
        self.assertIsNone(status.humidity)
        self.assertIsNone(status.air_quality)
        self.assertEqual(status.energy_storage, "high")
        self.assertEqual(decode_d2_14_30(_frame(air=6).data).air_quality, "error")

    def test_malformed_data_is_rejected(self):
        self.assertIsNone(decode_d2_14_30([RORG.VLD, 1, 2, 3, *SENDER, 0]))
        self.assertIsNone(decode_d2_14_30([RORG.BS4, *_payload(), *SENDER, 0]))
        self.assertIsNone(decode_d2_14_30(None))

    def test_matrix_and_silence_category(self):
        self.assertEqual(EEP_IMPLEMENTATIONS["D2-14-30"].platforms, ("binary_sensor",))
        self.assertEqual(
            silence_category(
                {"platform": "binary_sensor", "radio_metadata": {"eep": "D2-14-30"}}
            ),
            "smoke",
        )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class EntityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        self.row = UI_DEVICE_SCHEMA(
            {
                "id": SENDER,
                "platform": "binary_sensor",
                "name": "Insafe",
                "device_class": "smoke",
                "radio_metadata": {
                    "sender_id": SENDER,
                    "eep": "D2-14-30",
                    "eep_source": "manual",
                    "evidence": "manual",
                },
            }
        )

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def test_identities(self):
        identities = ui_device_entity_identities(self.row)
        self.assertEqual(identities[0], ("binary_sensor", f"{DECIMAL}-smoke"))
        self.assertEqual(len(identities), 10)
        self.assertIn(("sensor", f"{DECIMAL}-d2_14_30_temperature"), identities)
        self.assertIn(("binary_sensor", f"{DECIMAL}-d2-14-30-sensor-fault"), identities)

    async def test_entities_decode_a_frame(self):
        entry = SimpleNamespace(options={CONF_UI_DEVICES: [self.row]})
        created: list = []
        await binary_sensor.async_setup_entry(self.hass, entry, created.extend)
        await sensor.async_setup_entry(self.hass, entry, created.extend)
        entities = {
            e.unique_id: e
            for e in created
            if isinstance(
                e,
                (
                    binary_sensor.EnOceanD21430SmokeAlarm,
                    binary_sensor.EnOceanD21430Problem,
                    sensor.EnOceanD21430Sensor,
                ),
            )
        }
        self.assertEqual(
            set(entities),
            {uid for _platform, uid in ui_device_entity_identities(self.row)},
        )
        for entity in entities.values():
            entity.schedule_update_ha_state = lambda: None
            entity._message_received_callback(
                _frame(
                    smoke=1,
                    fault=1,
                    weeks=12,
                    energy=3,
                    life=60,
                    temperature_raw=100,
                    humidity_raw=120,
                    comfort=0,
                    air=0,
                )
            )
        alarm = entities[f"{DECIMAL}-smoke"]
        self.assertEqual(alarm.device_class, BinarySensorDeviceClass.SMOKE)
        self.assertIs(alarm.is_on, True)
        fault = entities[f"{DECIMAL}-d2-14-30-sensor-fault"]
        self.assertEqual(fault.device_class, BinarySensorDeviceClass.PROBLEM)
        self.assertEqual(fault.entity_category, EntityCategory.DIAGNOSTIC)
        self.assertIs(fault.is_on, True)
        self.assertIs(entities[f"{DECIMAL}-d2-14-30-maintenance"].is_on, False)
        self.assertEqual(entities[f"{DECIMAL}-d2_14_30_temperature"].native_value, 20.0)
        self.assertEqual(entities[f"{DECIMAL}-d2_14_30_humidity"].native_value, 60.0)
        energy = entities[f"{DECIMAL}-d2_14_30_energy_storage"]
        self.assertEqual(energy.device_class, SensorDeviceClass.ENUM)
        self.assertEqual(energy.native_value, "critical")
        self.assertEqual(entities[f"{DECIMAL}-d2_14_30_comfort"].native_value, "good")
        self.assertEqual(
            entities[f"{DECIMAL}-d2_14_30_air_quality"].native_value, "optimal"
        )
        self.assertEqual(
            entities[f"{DECIMAL}-d2_14_30_maintenance_weeks"].native_value, 12
        )
        self.assertEqual(
            entities[f"{DECIMAL}-d2_14_30_remaining_life"].native_value, 60
        )
        # Reserved readings keep the previous value.
        temperature = entities[f"{DECIMAL}-d2_14_30_temperature"]
        temperature._message_received_callback(_frame(temperature_raw=255))
        self.assertEqual(temperature.native_value, 20.0)


if __name__ == "__main__":
    unittest.main()

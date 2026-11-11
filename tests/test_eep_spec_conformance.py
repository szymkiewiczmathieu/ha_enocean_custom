"""Decoders and encoders checked against the text of EEP 2.6.8.

Every case quotes the table it comes from (profile, field, valid range). The
expectations are the specification's, not another implementation's.
"""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.components.climate import HVACMode

    from custom_components.enocean_custom import climate, sensor
    from custom_components.enocean_custom.d2_14 import (
        AIR_QUALITY_STATES,
        decode_d2_14_30,
    )
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RORG,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import Packet
    from custom_components.enocean_custom.valve_a5_20 import (
        decode_a5_20_01,
        encode_a5_20_01_command,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [0x01, 0x02, 0x03, 0x04]
SENDER_INT = 0x01020304
CONTROLLER = [0xFF, 0x80, 0x00, 0x01]


def _d2_14_30(*, weeks=0, life=0, temperature_raw=0, humidity_raw=0, air=0):
    """Build a D2-14-30 payload from the bit offsets of EEP 2.6.8 (48 bits)."""
    fields = [
        (5, 0),  # offsets 0..4: alarm and condition flags
        (8, weeks),  # offset 5
        (2, 0),  # offset 13: energy storage
        (8, life),  # offset 15
        (8, temperature_raw),  # offset 23
        (8, humidity_raw),  # offset 31
        (2, 0),  # offset 39: comfort index
        (3, air),  # offset 41: IAQTH
        (4, 0),  # offset 44: not used
    ]
    bits = "".join(format(value, f"0{size}b") for size, value in fields)
    payload = [int(bits[i : i + 8], 2) for i in range(0, 48, 8)]
    return [RORG.VLD, *payload, *SENDER, 0x00]


def _bs4(db3: int, db2: int, db1: int, db0: int):
    """Return a 4BS telegram parsed from its real ESP3 frame."""
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.BS4, db3, db2, db1, db0, *SENDER, 0x00],
        optional=[0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
    ).build()
    return Packet.parse_msg(bytearray(wire))[2]


def _truncated_bs4():
    """An ERP1 frame that says 4BS but carries one data byte (7 bytes in all)."""
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.BS4, 0x08, *SENDER, 0x00],
        optional=[0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
    ).build()
    return Packet.parse_msg(bytearray(wire))[2]


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class D21430Tests(unittest.TestCase):
    """EEP 2.6.8, D2-14-30 (Nexelec), offsets 5, 31 and 41."""

    def test_humidity_above_200_is_reserved(self):
        # HUM, offset 31: 0...200 -> 0...100 %RH; 201...254 reserved; 255 error.
        self.assertEqual(decode_d2_14_30(_d2_14_30(humidity_raw=200)).humidity, 100.0)
        for reserved in (201, 250, 254, 255):
            with self.subTest(raw=reserved):
                self.assertIsNone(
                    decode_d2_14_30(_d2_14_30(humidity_raw=reserved)).humidity
                )

    def test_temperature_keeps_its_own_250_limit(self):
        # TMP8, offset 23: 0...250 -> 0...50 degC; 251...254 reserved; 255 error.
        self.assertEqual(
            decode_d2_14_30(_d2_14_30(temperature_raw=250)).temperature, 50.0
        )
        self.assertIsNone(decode_d2_14_30(_d2_14_30(temperature_raw=251)).temperature)

    def test_indoor_air_analysis_codes(self):
        # IAQTH, offset 41: 0 optimal, 1 dry, 2 high humidity, 3 high
        # temperature and humidity, 4 out of analysis range, 5...6 reserved,
        # 7 error.
        expected = {
            0: "optimal",
            1: "dry",
            2: "humidity_high",
            3: "temperature_humidity_high",
            4: "out_of_range",
            5: None,
            6: None,
            7: "error",
        }
        for code, state in expected.items():
            with self.subTest(code=code):
                self.assertEqual(
                    decode_d2_14_30(_d2_14_30(air=code)).air_quality, state
                )
        self.assertEqual(
            set(AIR_QUALITY_STATES), {s for s in expected.values() if s is not None}
        )

    def test_weeks_since_maintenance_above_250_are_reserved(self):
        # Offset 5: 0...250 weeks; 251...254 reserved; 255 error.
        self.assertEqual(decode_d2_14_30(_d2_14_30(weeks=250)).maintenance_weeks, 250)
        for reserved in (251, 254, 255):
            with self.subTest(raw=reserved):
                self.assertIsNone(
                    decode_d2_14_30(_d2_14_30(weeks=reserved)).maintenance_weeks
                )

    def test_every_air_quality_state_is_translated_and_has_an_icon(self):
        import json

        component = ROOT / "custom_components" / "enocean_custom"
        files = [component / "strings.json", component / "icons.json"]
        files += sorted((component / "translations").glob("*.json"))
        for path in files:
            with self.subTest(file=path.name):
                states = json.loads(path.read_text("utf-8"))["entity"]["sensor"][
                    "d2_14_30_air_quality"
                ]["state"]
                self.assertEqual(set(states), set(AIR_QUALITY_STATES))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class A52001Tests(unittest.TestCase):
    """EEP 2.6.8, A5-20-01 Battery Powered Actuator (BI-DIR)."""

    def test_battery_capacity_bit_is_zero_when_the_battery_must_be_changed(self):
        # Direction 1, DB2.4 BCAP "Battery capacity; change battery next
        # days": Enum 0 = true. Direction 2 repeats it: "need changing
        # batteries = 0".
        change_soon = decode_a5_20_01([RORG.BS4, 50, 0x00, 128, 0x08, *SENDER, 0])
        battery_fine = decode_a5_20_01([RORG.BS4, 50, 0x10, 128, 0x08, *SENDER, 0])
        self.assertTrue(change_soon.battery_capacity_low)
        self.assertFalse(battery_fine.battery_capacity_low)

    def test_the_other_status_bits_are_one_when_true(self):
        status = decode_a5_20_01([RORG.BS4, 50, 0xEF, 128, 0x08, *SENDER, 0])
        self.assertTrue(status.service_on)
        self.assertTrue(status.energy_input_enabled)
        self.assertTrue(status.energy_storage_charged)
        self.assertTrue(status.contact_cover_open)
        self.assertTrue(status.temperature_sensor_failure)
        self.assertTrue(status.window_open)
        self.assertTrue(status.obstructed)

    def test_room_temperature_sent_to_the_valve_uses_the_inverted_scale(self):
        # Direction 2, DB2 TMP "Temperature actual from RCU": valid range
        # 255...0, scale 0...+40 degC.
        self.assertEqual(encode_a5_20_01_command(40, 0.0), [40, 255, 0x00, 0x08])
        self.assertEqual(encode_a5_20_01_command(40, 40.0), [40, 0, 0x00, 0x08])
        self.assertEqual(encode_a5_20_01_command(40, 30.0)[1], 64)
        # Out-of-scale readings saturate instead of wrapping.
        self.assertEqual(encode_a5_20_01_command(40, -5.0)[1], 255)
        self.assertEqual(encode_a5_20_01_command(40, 55.0)[1], 0)

    def test_temperature_reported_by_the_valve_stays_direct(self):
        # Direction 1, DB1 TMP: 0...255 -> 0...+40 degC.
        status = decode_a5_20_01([RORG.BS4, 50, 0x10, 255, 0x08, *SENDER, 0])
        self.assertEqual(status.temperature, 40.0)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class FourBsTeachInTests(unittest.TestCase):
    """DB0.3 is the 4BS LRN bit: 0 = teach-in telegram, 1 = data telegram.

    A teach-in telegram carries FUNC/TYPE/manufacturer in DB3..DB1, never a
    measurement. A5-12-01 states it in its own table (offset 28, LRNB).
    """

    # A5-12-01 teach-in with EEP: FUNC 0x12, TYPE 0x01, manufacturer 0x00D.
    TEACH_IN = (0x48, 0x08, 0x0D, 0x80)

    def _entity(self, cls, description, **kwargs):
        entity = cls(SENDER, "Meter", description, **kwargs)
        entity.schedule_update_ha_state = mock.Mock()
        return entity

    def _assert_ignores(self, entity, packet):
        entity.value_changed(packet)
        self.assertIsNone(entity.native_value)
        entity.schedule_update_ha_state.assert_not_called()

    def test_energy_counters_ignore_a_teach_in_telegram(self):
        for cls, description in (
            (sensor.EnOceanEnergySensor, sensor.SENSOR_DESC_ENERGY),
            (sensor.EnOceanA51201EnergySensor, sensor.SENSOR_DESC_A5_12_01_ENERGY),
        ):
            with self.subTest(entity=cls.__name__):
                self._assert_ignores(
                    self._entity(cls, description), _bs4(*self.TEACH_IN)
                )

    def test_energy_counters_still_decode_a_data_telegram(self):
        # 0x000FA0 = 4000, DT=0 cumulative, DIV=1 -> 400.0
        entity = self._entity(
            sensor.EnOceanA51201EnergySensor, sensor.SENSOR_DESC_A5_12_01_ENERGY
        )
        entity.value_changed(_bs4(0x00, 0x0F, 0xA0, 0x09))
        self.assertEqual(entity.native_value, 400.0)

    def test_power_still_decodes_a_data_telegram(self):
        entity = self._entity(sensor.EnOceanPowerSensor, sensor.SENSOR_DESC_POWER)
        entity.value_changed(_bs4(0x00, 0x00, 0x64, 0x0C))  # DT=1, DIV=0
        self.assertEqual(entity.native_value, 100.0)

    def test_temperature_ignores_a_teach_in_telegram(self):
        entity = self._entity(
            sensor.EnOceanTemperatureSensor,
            sensor.SENSOR_DESC_TEMPERATURE,
            scale_min=0,
            scale_max=40,
            range_from=255,
            range_to=0,
        )
        self._assert_ignores(entity, _bs4(0x08, 0x28, 0x0D, 0x80))
        self.assertIsNone(entity.setpoint)
        entity.value_changed(_bs4(0x00, 0x7F, 0x80, 0x09))
        self.assertEqual(entity.native_value, 19.9)
        self.assertEqual(entity.setpoint, 0x7F)
        self.assertEqual(entity.slideswitch, 1)

    def test_humidity_ignores_a_teach_in_telegram(self):
        entity = self._entity(sensor.EnOceanHumiditySensor, sensor.SENSOR_DESC_HUMIDITY)
        self._assert_ignores(entity, _bs4(0x10, 0x08, 0x0D, 0x80))
        entity.value_changed(_bs4(0x00, 125, 100, 0x0A))
        self.assertEqual(entity.native_value, 50.0)

    def test_humidity_above_250_is_outside_the_valid_range(self):
        # A5-04-01, DB2 HUM: valid range 0...250 -> 0...100 %.
        entity = self._entity(sensor.EnOceanHumiditySensor, sensor.SENSOR_DESC_HUMIDITY)
        self._assert_ignores(entity, _bs4(0x00, 251, 100, 0x0A))
        entity.value_changed(_bs4(0x00, 250, 100, 0x0A))
        self.assertEqual(entity.native_value, 100.0)

    def test_a_truncated_4bs_frame_is_never_decoded(self):
        # RORG A5 with a single data byte: the sender bytes must not be read
        # as DB2..DB0.
        packet = _truncated_bs4()
        self.assertEqual(len(packet.data), 7)
        temperature = self._entity(
            sensor.EnOceanTemperatureSensor,
            sensor.SENSOR_DESC_TEMPERATURE,
            scale_min=0,
            scale_max=40,
            range_from=255,
            range_to=0,
        )
        self._assert_ignores(temperature, packet)
        humidity = self._entity(
            sensor.EnOceanHumiditySensor, sensor.SENSOR_DESC_HUMIDITY
        )
        self._assert_ignores(humidity, packet)
        energy = self._entity(sensor.EnOceanEnergySensor, sensor.SENSOR_DESC_ENERGY)
        self._assert_ignores(energy, packet)


def _valve(profile: str):
    entity = climate.EnOceanClimate(
        SENDER,
        "Radiator",
        CONTROLLER,
        "sensor.room",
        8.0,
        5,
        0.5,
        21.0,
        4.0,
        timedelta(minutes=17),
        5.0,
        240.0,
        device_type=profile,
    )
    entity.schedule_update_ha_state = mock.Mock()
    entity.sent = []

    def send_command(data, optional, packet_type, response_callback=None, **_):
        entity.sent.append((list(data), list(optional), packet_type))
        return True

    entity.send_command = send_command
    entity._attr_hvac_mode = HVACMode.HEAT
    entity._attr_target_temp = 21.0
    entity._attr_native_current_temperature = 19.0
    entity._attr_pi_control_output = 37.4
    return entity


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class A52004Tests(unittest.TestCase):
    """EEP 2.6.8, A5-20-04 radiator valve (BI-DIR, 4BS teach-in variation 3)."""

    def test_a_teach_in_telegram_is_not_valve_feedback(self):
        # Direction 1, DB0.3 LRNB: 0 = teach-in telegram.
        entity = _valve(climate.PROFILE_A5_20_04)
        entity.value_changed(_bs4(0x80, 0x20, 0x49, 0x80))
        self.assertIsNone(entity._attr_valve_position)
        self.assertEqual(entity._attr_native_current_temperature, 19.0)
        self.assertEqual(entity.sent, [], "a teach-in is never answered on its own")
        entity.schedule_update_ha_state.assert_not_called()

    def test_a_truncated_frame_is_not_valve_feedback(self):
        entity = _valve(climate.PROFILE_A5_20_04)
        entity.value_changed(_truncated_bs4())
        self.assertIsNone(entity._attr_valve_position)
        self.assertEqual(entity.sent, [])

    def test_a_status_telegram_is_answered_in_the_receive_window(self):
        # "A message in this direction has to be sent after receiving a
        # message from the actuator ... If the controller message is not
        # received by the actuator in a specific time after a direction 1
        # message, no information will be received by the actuator."
        entity = _valve(climate.PROFILE_A5_20_04)
        entity.value_changed(_bs4(42, 0x00, 128, 0x08))

        self.assertEqual(entity._attr_valve_position, 42)
        self.assertEqual(len(entity.sent), 1)
        data, optional, packet_type = entity.sent[0]
        self.assertEqual(packet_type, 0x01)
        # POS 37 %, TSP 21 degC -> (21-10)*255/20 = 140, WUC 33 (1020 s),
        # DB0 = 0x08 (data telegram, nothing else requested).
        self.assertEqual(data, [RORG.BS4, 37, 140, 33, 0x08, *CONTROLLER, 0x00])
        # Addressed to the valve, sent from the controller identity.
        self.assertEqual(optional[1:5], SENDER)

    def test_the_answer_closes_the_valve_when_heating_is_off(self):
        entity = _valve(climate.PROFILE_A5_20_04)
        entity._attr_hvac_mode = HVACMode.OFF
        entity.value_changed(_bs4(42, 0x00, 128, 0x08))
        data = entity.sent[0][0]
        # POS 0 %, TSP = frost protection 8 degC, clamped to the 10 degC floor.
        self.assertEqual(data[1:3], [0, 0])

    def test_no_answer_before_the_control_state_is_known(self):
        entity = _valve(climate.PROFILE_A5_20_04)
        entity._attr_hvac_mode = None
        entity._attr_target_temp = None
        entity.value_changed(_bs4(42, 0x00, 128, 0x08))
        self.assertEqual(entity._attr_valve_position, 42)
        self.assertEqual(entity.sent, [])

    def test_the_answer_does_not_advance_the_pi_controller(self):
        entity = _valve(climate.PROFILE_A5_20_04)
        before = (
            entity._attr_pi_control_output,
            entity._pi_control_integrator_state,
            entity._pi_control_update_time,
        )
        entity.value_changed(_bs4(42, 0x00, 128, 0x08))
        self.assertEqual(
            before,
            (
                entity._attr_pi_control_output,
                entity._pi_control_integrator_state,
                entity._pi_control_update_time,
            ),
        )

    def test_the_legacy_thermostat_profile_never_answers(self):
        entity = _valve(climate.PROFILE_SRC_D08)
        entity.value_changed(_bs4(42, 0x00, 128, 0x08))
        self.assertEqual(entity.sent, [])


if __name__ == "__main__":
    unittest.main()

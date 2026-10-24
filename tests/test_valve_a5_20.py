"""A5-20-01 / A5-20-06 valves (M2): EXPERIMENTAL, needs hardware validation.

Byte layouts are taken verbatim from the vendored EEP.xml (A5-20-01) and the
official EnOcean Alliance EEP proposal PDF (A5-20-06, EH4/Micropelt, V0.6,
2018-02-28). The one-second reply budget means the whole point of this
suite is proving the reply is queued synchronously, inside the dispatcher
callback, with nothing awaited in between -- not just that the bytes are
right.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    import voluptuous as vol
    from homeassistant.components.climate import HVACMode

    from custom_components.enocean_custom import climate
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RORG,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import Packet
    from custom_components.enocean_custom.valve_a5_20 import (
        bidirectional_teach_in_response,
        decode_a5_20_01,
        decode_a5_20_06,
        encode_a5_20_01_command,
        encode_a5_20_06_command,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [0x05, 0x8F, 0x22, 0x33]


def _hass_with_gateway(gateway) -> SimpleNamespace:
    entry = SimpleNamespace(runtime_data=gateway)
    return SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda domain: [entry]),
        data={},
    )


def _capture_into(storage: list) -> object:
    """Return a send_command fake that records every call's data and accepts it."""

    def send(data, *_args, **_kwargs):
        storage.append(data)
        return True

    return send


def _frame(db3, db2, db1, db0) -> object:
    """Build and parse a real ESP3 4BS frame carrying DB3..DB0."""
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.BS4, db3, db2, db1, db0, *SENDER, 0x00],
        optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
    ).build()
    return Packet.parse_msg(bytearray(wire))[2]


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class TeachInTests(unittest.TestCase):
    def test_reproduces_the_historical_a5_20_04_bytes_exactly(self):
        # climate.py's own, already field-verified A5-20-04 response.
        self.assertEqual(
            bidirectional_teach_in_response(0x20, 0x04), [0x80, 0x20, 0x00, 0xF0]
        )

    def test_a5_20_01_and_a5_20_06(self):
        self.assertEqual(
            bidirectional_teach_in_response(0x20, 0x01), [0x80, 0x08, 0x00, 0xF0]
        )
        self.assertEqual(
            bidirectional_teach_in_response(0x20, 0x06), [0x80, 0x30, 0x00, 0xF0]
        )

    def test_manufacturer_id_spans_db2_and_db1(self):
        # Manufacturer 0x046 (Ubiwizz, used elsewhere in this repository),
        # FUNC 0x20 TYPE 0x04: DB2 bits 2-0 carry MID bits 10-8.
        self.assertEqual(
            bidirectional_teach_in_response(0x20, 0x04, 0x046),
            [0x80, 0x20, 0x46, 0xF0],
        )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class A52001DecodeTests(unittest.TestCase):
    def test_typical_status(self):
        # CV=42%, SO+ES+DWO set, TMP raw 128 -> 20.1 degC.
        status = decode_a5_20_01(_frame(42, 0b10100010, 128, 0x08).data)
        self.assertEqual(status.valve_position, 42)
        self.assertTrue(status.service_on)
        self.assertFalse(status.energy_input_enabled)
        self.assertTrue(status.energy_storage_charged)
        self.assertFalse(status.battery_capacity_low)
        self.assertFalse(status.contact_cover_open)
        self.assertFalse(status.temperature_sensor_failure)
        self.assertTrue(status.window_open)
        self.assertFalse(status.obstructed)
        self.assertEqual(status.temperature, round(128 * 40 / 255, 1))

    def test_reserved_valve_position_is_none(self):
        status = decode_a5_20_01(_frame(150, 0, 0, 0x08).data)
        self.assertIsNone(status.valve_position)

    def test_teach_in_telegram_is_rejected(self):
        self.assertIsNone(decode_a5_20_01(_frame(42, 0, 0, 0x00).data))

    def test_wrong_rorg_or_length_is_rejected(self):
        self.assertIsNone(decode_a5_20_01([RORG.VLD, 1, 2, 3, 4, *SENDER, 0]))
        self.assertIsNone(decode_a5_20_01(None))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class A52001EncodeTests(unittest.TestCase):
    def test_typical_command(self):
        self.assertEqual(
            encode_a5_20_01_command(75, 21.0), [75, round(21 * 255 / 40), 0x00, 0x08]
        )

    def test_position_and_temperature_are_clipped(self):
        self.assertEqual(encode_a5_20_01_command(150, 60.0)[0], 100)
        self.assertEqual(encode_a5_20_01_command(-5, -10.0)[0], 0)
        self.assertEqual(encode_a5_20_01_command(0, 60.0)[1], 255)
        self.assertEqual(encode_a5_20_01_command(0, -10.0)[1], 0)

    def test_lrn_bit_marks_a_data_telegram(self):
        self.assertEqual(encode_a5_20_01_command(0, 0.0)[3], 0x08)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class A52006DecodeTests(unittest.TestCase):
    def test_typical_ambient_status(self):
        # CV=60%, LOM=0, LO=3, ambient TMP raw 60 -> 30 degC, ENIE+DWO set,
        # LRN bit (0x08) set: a data telegram, not a teach-in.
        status = decode_a5_20_06(_frame(60, 0b00000011, 60, 0b01011000).data)
        self.assertEqual(status.valve_position, 60)
        self.assertFalse(status.local_offset_mode_absolute)
        self.assertEqual(status.local_offset_raw, 3)
        self.assertEqual(status.temperature, 30.0)
        self.assertFalse(status.feed_temperature_selected)
        self.assertTrue(status.energy_input_enabled)
        self.assertFalse(status.energy_storage_charged)
        self.assertTrue(status.window_open)
        self.assertFalse(status.radio_communication_error)
        self.assertFalse(status.weak_radio_signal)
        self.assertFalse(status.obstructed)

    def test_feed_temperature_uses_the_wider_range(self):
        status = decode_a5_20_06(_frame(0, 0, 160, 0b10001000).data)
        self.assertTrue(status.feed_temperature_selected)
        self.assertEqual(status.temperature, 80.0)

    def test_ambient_reading_above_80_is_reserved(self):
        status = decode_a5_20_06(_frame(0, 0, 81, 0b00001000).data)
        self.assertIsNone(status.temperature)

    def test_sensor_failure_sentinel(self):
        status = decode_a5_20_06(_frame(0, 0, 0xFF, 0x08).data)
        self.assertIsNone(status.temperature)

    def test_reserved_valve_position_and_teach_in_rejection(self):
        self.assertIsNone(decode_a5_20_06(_frame(255, 0, 0, 0x08).data).valve_position)
        self.assertIsNone(decode_a5_20_06(_frame(50, 0, 0, 0x00).data))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class A52006EncodeTests(unittest.TestCase):
    def test_typical_command(self):
        self.assertEqual(encode_a5_20_06_command(80, 20.0), [80, 80, 0x00, 0x08])

    def test_temperature_sentinels_are_never_emitted_by_accident(self):
        self.assertEqual(encode_a5_20_06_command(0, 0.0)[1], 1)
        self.assertEqual(encode_a5_20_06_command(0, 45.0)[1], 160)

    def test_position_is_clipped(self):
        self.assertEqual(encode_a5_20_06_command(200, 20.0)[0], 100)


def _climate(device_type: str) -> object:
    """Build a bare valve climate entity, bypassing the YAML schema."""
    entity = climate.EnOceanClimate(
        SENDER,
        "Valve",
        [0x01, 0x02, 0x03, 0x04],
        "sensor.room",
        8.0,
        5,
        0.5,
        21.0,
        4.0,
        timedelta(minutes=17),
        5.0,
        240.0,
        device_type=device_type,
    )
    entity.async_write_ha_state = lambda: None
    entity.schedule_update_ha_state = lambda: None
    return entity


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ClimateIntegrationTests(unittest.TestCase):
    def test_status_telegram_is_answered_synchronously_no_await_needed(self):
        entity = _climate(climate.PROFILE_A5_20_01)
        entity._attr_hvac_mode = HVACMode.HEAT
        entity._attr_target_temp = 21.0
        entity._attr_current_temperature = 19.0
        sent: list[list[int]] = []
        entity.send_command = Mock(side_effect=_capture_into(sent))

        entity.value_changed(_frame(55, 0b10000000, 128, 0x08))

        # No await happened between the call above and this assertion: if
        # the reply were scheduled on the loop instead of sent inline, this
        # list would still be empty here.
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], RORG.BS4)
        self.assertEqual(entity._attr_valve_position, 55)
        self.assertTrue(entity._valve_diagnostics["service_on"])

    def test_a5_20_06_status_telegram_is_also_answered_synchronously(self):
        entity = _climate(climate.PROFILE_A5_20_06)
        entity._attr_hvac_mode = HVACMode.HEAT
        entity._attr_target_temp = 21.0
        entity._attr_current_temperature = 19.0
        sent: list[list[int]] = []
        entity.send_command = Mock(side_effect=_capture_into(sent))

        entity.value_changed(_frame(30, 0, 50, 0b00011000))

        self.assertEqual(len(sent), 1)
        self.assertEqual(entity._attr_valve_position, 30)
        self.assertTrue(entity._valve_diagnostics["window_open"])

    def test_hvac_off_requests_a_closed_valve(self):
        entity = _climate(climate.PROFILE_A5_20_01)
        entity._attr_hvac_mode = HVACMode.OFF
        entity._attr_current_temperature = 19.0
        sent: list[list[int]] = []
        entity.send_command = Mock(side_effect=_capture_into(sent))

        entity.value_changed(_frame(20, 0, 0, 0x08))

        self.assertEqual(sent[0][1], 0)  # DB3 (position) of the reply

    def test_no_reply_is_sent_without_a_usable_room_temperature(self):
        entity = _climate(climate.PROFILE_A5_20_01)
        entity._attr_hvac_mode = HVACMode.HEAT
        entity._attr_target_temp = 21.0
        entity._attr_current_temperature = None
        entity.send_command = Mock(return_value=True)

        entity.value_changed(_frame(20, 0, 0, 0x08))

        entity.send_command.assert_not_called()

    def test_other_profiles_and_rorgs_never_reach_the_valve_decoder(self):
        entity = _climate(climate.PROFILE_SRC_D08)
        entity.send_command = Mock(return_value=True)
        entity.value_changed(_frame(20, 0, 0, 0x08))
        entity.send_command.assert_not_called()

    def test_teach_in_actor_uses_the_generalized_response(self):
        for profile, type_ in (
            (climate.PROFILE_A5_20_01, 0x01),
            (climate.PROFILE_A5_20_06, 0x06),
        ):
            with self.subTest(profile=profile):
                entity = _climate(profile)
                captured: list[list[int]] = []
                entity.send_command = Mock(side_effect=_capture_into(captured))
                entity.teach_in_actor()
                payload = captured[0][1:5]
                self.assertEqual(payload, bidirectional_teach_in_response(0x20, type_))

    def test_setting_temperature_commits_locally_without_a_dongle(self):
        """No ESP3 command exists for a setpoint change: it must still stick."""
        entity = _climate(climate.PROFILE_A5_20_01)
        entity._attr_hvac_mode = HVACMode.HEAT
        entity.hass = _hass_with_gateway(None)
        asyncio.run(entity.async_set_temperature(temperature=23.5))
        self.assertEqual(entity._attr_target_temp, 23.5)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class SchemaGatingTests(unittest.TestCase):
    def _config(self, **overrides):
        config = {
            "platform": "enocean_custom",
            "id": SENDER,
            "id_switch": [0x01, 0x02, 0x03, 0x04],
            "device_type": climate.PROFILE_A5_20_01,
            "sensor_entity_id": "sensor.room",
        }
        config.update(overrides)
        return config

    def test_experimental_profile_without_ack_is_refused(self):
        with self.assertRaises(vol.Invalid):
            climate.PLATFORM_SCHEMA(self._config())

    def test_experimental_profile_with_ack_is_accepted(self):
        validated = climate.PLATFORM_SCHEMA(self._config(experimental_valve_ack=True))
        self.assertEqual(validated["device_type"], climate.PROFILE_A5_20_01)

    def test_a5_20_04_never_needs_the_ack(self):
        validated = climate.PLATFORM_SCHEMA(
            self._config(device_type=climate.PROFILE_A5_20_04)
        )
        self.assertEqual(validated["device_type"], climate.PROFILE_A5_20_04)

    def test_ui_climate_device_types_stays_src_d08_only(self):
        # A5-20-01/06 are YAML-only, same precedent as A5-20-04.
        from custom_components.enocean_custom.schema import UI_CLIMATE_DEVICE_TYPES

        self.assertEqual(UI_CLIMATE_DEVICE_TYPES, ("SRC-D08",))


if __name__ == "__main__":
    unittest.main()

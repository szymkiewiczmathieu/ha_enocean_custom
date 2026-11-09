"""Release 2.15 workstream 5: D2-01-0C pilot-wire control, user-action only."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)

from custom_components.enocean_custom import d2_01, select, sensor
from custom_components.enocean_custom import identity as identity_module
from custom_components.enocean_custom.const import DOMAIN, SIGNAL_RECEIVE_MESSAGE
from custom_components.enocean_custom.enocean_library.protocol.constants import RORG
from custom_components.enocean_custom.identity import (
    additive_ui_device_entity_identities,
    ui_device_entity_identities,
)
from custom_components.enocean_custom.schema import UI_DEVICE_SCHEMA

SENDER = [0x05, 0x8F, 0x22, 0x33]
BASE_ID = [0xFF, 0x87, 0xCA, 0x00]
DECIMAL = "93266483"


def _row(eep="D2-01-0C"):
    return UI_DEVICE_SCHEMA(
        {
            "id": SENDER,
            "platform": "switch",
            "name": "Heater",
            "channel": 0,
            "switch_type": "default",
            "radio_metadata": {"eep": eep},
        }
    )


def _response(mode):
    return SimpleNamespace(
        sender_int=0x058F2233,
        data=[RORG.VLD, 0x0A, mode, *SENDER, 0x00],
    )


class PilotWireFrameTests(unittest.TestCase):
    def test_encoding_and_decoder_accept_only_modes_zero_through_five(self):
        packet = d2_01.build_pilot_wire_set(BASE_ID, SENDER, 2)
        self.assertEqual(packet.data, [RORG.VLD, 0x08, 0x02, *BASE_ID, 0x00])
        self.assertEqual(packet.optional, [0x03, *SENDER, 0xFF, 0x00])
        query = d2_01.build_pilot_wire_query(BASE_ID, SENDER)
        self.assertEqual(query.data, [RORG.VLD, 0x09, *BASE_ID, 0x00])
        self.assertEqual(d2_01.decode_pilot_wire_mode(_response(2)), "eco")
        self.assertEqual(d2_01.decode_pilot_wire_mode(_response(5)), "comfort_2")
        self.assertIsNone(d2_01.decode_pilot_wire_mode(_response(6)))
        self.assertIsNone(
            d2_01.decode_pilot_wire_mode(SimpleNamespace(data=[RORG.VLD, 0x07, 2]))
        )
        for invalid in (-1, 6, 1.5, "2"):
            with (
                self.subTest(invalid=invalid),
                self.assertRaises((TypeError, ValueError)),
            ):
                d2_01.build_pilot_wire_set(BASE_ID, SENDER, invalid)

    def test_identity_is_additive_and_switch_identity_is_frozen(self):
        row = _row()
        self.assertEqual(
            identity_module.d2_01_0c_pilot_wire_unique_id(SENDER, 0),
            f"{DECIMAL}-0-d2-01-0c-pilot-wire",
        )
        self.assertEqual(
            ui_device_entity_identities(row), (("switch", f"{DECIMAL}-0"),)
        )
        self.assertEqual(
            additive_ui_device_entity_identities(row),
            (("select", f"{DECIMAL}-0-d2-01-0c-pilot-wire"),),
        )
        self.assertEqual(additive_ui_device_entity_identities(_row("D2-01-0B")), ())

    def test_cmd7_measurements_remain_wh_and_w(self):
        vectors = (
            ([0x07, 0x20, 0, 0, 0, 100], ("energy", 100)),
            ([0x07, 0x40, 0, 0, 0, 100], ("energy", 100000)),
            ([0x07, 0x60, 0, 0, 0, 100], ("power", 100)),
            ([0x07, 0x80, 0, 0, 0, 100], ("power", 100000)),
            ([0x07, 0x00, 0, 0, 0x0E, 0x10], ("energy", 1)),
        )
        for payload, expected in vectors:
            with self.subTest(payload=payload):
                packet = SimpleNamespace(data=[RORG.VLD, *payload, *SENDER, 0])
                self.assertEqual(sensor._decode_d2_measurement(packet), expected)
        self.assertIsNone(
            sensor._decode_d2_measurement(
                SimpleNamespace(data=[RORG.VLD, 0x0A, 0x02, 0, 0, 0, 0, *SENDER, 0])
            )
        )


class FakeDongle:
    def __init__(self, hass, *, answer=True):
        self.hass = hass
        self.available = True
        self.base_id = list(BASE_ID)
        self.entry_id = "entry"
        self.answer = answer
        self.sent = []

    def send(self, packet, response_callback=None, written_callback=None):
        self.sent.append(packet)
        loop = self.hass.loop
        if written_callback is not None:
            loop.call_soon(written_callback)
        if response_callback is not None:
            loop.call_soon(response_callback, True)
        if packet.data[1] == 0x09 and self.answer:
            loop.call_soon(
                async_dispatcher_send, self.hass, SIGNAL_RECEIVE_MESSAGE, _response(2)
            )
        return True


class PilotWireCycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        self.dongle = FakeDongle(self.hass)
        self.runner = d2_01.D201QueryRunner(self.hass, self.dongle)
        self.target = d2_01.D201Target(tuple(SENDER), 0, "Heater", "D2-01-0C")

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def test_full_cycle_is_exactly_set_query_response(self):
        with patch.object(d2_01, "MIN_SPACING_SECONDS", 0.0):
            outcome = await self.runner.async_pilot_wire_set(self.target, 2)
        self.assertEqual(outcome, "answered")
        self.assertEqual(
            [packet.data[1:-5] for packet in self.dongle.sent],
            [[0x08, 0x02], [0x09]],
        )
        self.assertTrue(
            all(packet.data[1] not in (0x02, 0x05) for packet in self.dongle.sent)
        )

        entity = select._pilot_wire_selects([_row()])[0]
        entity.hass = self.hass
        entity.schedule_update_ha_state = lambda: None
        entity.async_write_ha_state = lambda: None
        entry = SimpleNamespace(runtime_data=self.dongle)
        self.hass.config_entries = SimpleNamespace(async_entries=lambda domain: [entry])
        unsubscribe = async_dispatcher_connect(
            self.hass, SIGNAL_RECEIVE_MESSAGE, entity._message_received_callback
        )
        self.dongle.sent.clear()
        try:
            await entity.async_select_option("eco")
        finally:
            unsubscribe()
        self.assertEqual(entity.current_option, "eco")
        self.assertEqual(
            [packet.data[1:-5] for packet in self.dongle.sent],
            [[0x08, 0x02], [0x09]],
        )

    async def test_missing_cmd_a_is_an_explicit_failure(self):
        self.dongle.answer = False
        with (
            patch.object(d2_01, "MIN_SPACING_SECONDS", 0.0),
            patch.object(d2_01, "RESPONSE_WINDOW_SECONDS", 0.01),
        ):
            outcome = await self.runner.async_pilot_wire_set(self.target, 2)
        self.assertEqual(outcome, "unanswered")
        self.assertEqual([packet.data[1] for packet in self.dongle.sent], [0x08, 0x09])

    async def test_select_creates_only_for_0c_and_state_comes_only_from_cmd_a(self):
        self.assertEqual(len(select._pilot_wire_selects([_row()])), 1)
        self.assertEqual(select._pilot_wire_selects([_row("D2-01-0B")]), [])
        entity = select._pilot_wire_selects([_row()])[0]
        entity.schedule_update_ha_state = lambda: None
        self.assertIsNone(entity.current_option)
        entity.value_changed(_response(2))
        self.assertEqual(entity.current_option, "eco")
        entity.value_changed(_response(6))
        self.assertEqual(entity.current_option, "eco")

    async def test_select_timeout_keeps_old_state_and_raises_translated_error(self):
        entity = select._pilot_wire_selects([_row()])[0]
        entity.hass = self.hass
        entry = SimpleNamespace(runtime_data=self.dongle)
        self.hass.config_entries = SimpleNamespace(async_entries=lambda domain: [entry])
        entity._attr_current_option = "comfort"
        self.dongle.answer = False
        with (
            patch.object(d2_01, "MIN_SPACING_SECONDS", 0.0),
            patch.object(d2_01, "RESPONSE_WINDOW_SECONDS", 0.01),
            self.assertRaises(HomeAssistantError) as raised,
        ):
            await entity.async_select_option("eco")
        self.assertEqual(raised.exception.translation_domain, DOMAIN)
        self.assertEqual(
            raised.exception.translation_key, "pilot_wire_readback_timeout"
        )
        self.assertEqual(entity.current_option, "comfort")
        before = len(self.dongle.sent)
        with self.assertRaises((TypeError, ValueError)):
            await entity.async_select_option("invalid")
        self.assertEqual(len(self.dongle.sent), before)


if __name__ == "__main__":
    unittest.main()

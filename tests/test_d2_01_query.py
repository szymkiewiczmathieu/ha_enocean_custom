"""D2-01 status and measurement queries: frames, capability table, discipline.

Sources: EnOcean Equipment Profiles 2.6.7, D2-01 (pages 131-146): the TYPE
capability matrix (pp. 131-132), CMD 0x3 Actuator Status Query (p. 135, two
bytes, 300 ms response window), CMD 0x4 (pp. 135-136), CMD 0x6 Actuator
Measurement Query (p. 137, ``qu`` bit at offset 10) and CMD 0x7 (pp. 137-138).
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.core import HomeAssistant
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.enocean_custom import button, d2_01
    from custom_components.enocean_custom.const import (
        FORWARDED_PLATFORMS,
        SIGNAL_RECEIVE_MESSAGE,
        UI_DEVICE_PLATFORMS,
    )
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RORG,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import (
        Packet,
    )
    from custom_components.enocean_custom.identity import d2_01_refresh_unique_id
    from custom_components.enocean_custom.schema import (
        CONF_UI_DEVICES,
        UI_DEVICE_SCHEMA,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [0x05, 0x8F, 0x22, 0x33]
BASE_ID = [0xFF, 0x87, 0xCA, 0x00]


def _row(platform="switch", channel=0, switch_type="default", eep=None, **extra):
    row = {
        "id": SENDER,
        "platform": platform,
        "name": "ASP",
        "channel": channel,
        **extra,
    }
    if platform == "switch":
        row["switch_type"] = switch_type
    if eep is not None:
        row["radio_metadata"] = {"eep": eep}
    return UI_DEVICE_SCHEMA(row)


def _status_response(sender, channel, output=100):
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.VLD, 0x04, channel, output, *sender, 0x00],
        optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
    ).build()
    return Packet.parse_msg(bytearray(wire))[2]


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class CapabilityTableTests(unittest.TestCase):
    """EEP 2.6.7 pp. 131-132, transcribed, never inferred."""

    def test_status_query_applies_to_every_d2_01_type_only(self):
        for type_ in range(0x15):
            self.assertTrue(d2_01.supports_status_query(f"D2-01-{type_:02X}"))
        for eep in ("D2-01-15", "D2-05-00", "F6-02-01", "A5-12-01", None, 12):
            self.assertFalse(d2_01.supports_status_query(eep))

    def test_measurement_queries_follow_the_capability_matrix(self):
        # NodOn ASP-2-1-00 (D2-01-0A): no metering at all.
        self.assertEqual(d2_01.measurement_queries("D2-01-0A"), (False, False))
        # NodOn ASP-2-1-01 (D2-01-0B): energy and power, report on query.
        self.assertEqual(d2_01.measurement_queries("D2-01-0B"), (True, True))
        # Ubiwizz UBID1507C (D2-01-12) and D2-01-08 (energy only).
        self.assertEqual(d2_01.measurement_queries("D2-01-12"), (False, False))
        self.assertEqual(d2_01.measurement_queries("D2-01-08"), (True, True))
        self.assertEqual(d2_01.measurement_queries("D2-01-00"), (True, False))
        self.assertEqual(d2_01.measurement_queries("D2-01-10"), (True, False))
        self.assertEqual(d2_01.measurement_queries("D2-01-01"), (False, False))
        self.assertEqual(d2_01.measurement_queries(None), (False, False))
        self.assertEqual(d2_01.OUTPUT_CHANNELS_BY_TYPE[0x12], 2)
        self.assertEqual(d2_01.OUTPUT_CHANNELS_BY_TYPE[0x0A], 1)
        self.assertEqual(d2_01.POWER_FAILURE_DETECTION_TYPES, {0x0A, 0x0B})
        self.assertNotIn(0x06, d2_01.LOCAL_CONTROL_TYPES)
        self.assertIn(0x0A, d2_01.LOCAL_CONTROL_TYPES)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class FrameTests(unittest.TestCase):
    """Byte layouts of CMD 0x3 (p. 135) and CMD 0x6 (p. 137)."""

    def test_status_query_frame(self):
        packet = d2_01.build_status_query(BASE_ID, SENDER, 1)
        self.assertEqual(packet.packet_type, PACKET.RADIO_ERP1)
        self.assertEqual(packet.data, [0xD2, 0x03, 0x01, *BASE_ID, 0x00])
        # Directed to the actuator, sub-telegram count 3, dBm 0xFF, no security.
        self.assertEqual(packet.optional, [0x03, *SENDER, 0xFF, 0x00])
        packet.build()  # a well-formed ESP3 frame

    def test_measurement_query_frames(self):
        energy = d2_01.build_measurement_query(BASE_ID, SENDER, 0, power=False)
        power = d2_01.build_measurement_query(BASE_ID, SENDER, 0, power=True)
        self.assertEqual(energy.data, [0xD2, 0x06, 0x00, *BASE_ID, 0x00])
        self.assertEqual(power.data, [0xD2, 0x06, 0x20, *BASE_ID, 0x00])
        # The channel never leaks into the qu bit, and 0x1E/0x1F are legal.
        self.assertEqual(
            d2_01.build_measurement_query(BASE_ID, SENDER, 0x1E, power=True).data[2],
            0x3E,
        )

    def test_matching_responses(self):
        target = d2_01.D201Target(tuple(SENDER), 1, "ASP", "D2-01-0A")
        status = _status_response(SENDER, 1)
        self.assertTrue(d2_01.is_matching_response(status, target, 0x03))
        other_channel = _status_response(SENDER, 0)
        self.assertFalse(d2_01.is_matching_response(other_channel, target, 0x03))
        other_sender = _status_response([1, 2, 3, 4], 1)
        self.assertFalse(d2_01.is_matching_response(other_sender, target, 0x03))
        measurement = SimpleNamespace(
            sender_int=0x058F2233,
            data=[0xD2, 0x07, 0x61, 0, 0, 0, 0x10, *SENDER, 0],
        )
        self.assertTrue(d2_01.is_matching_response(measurement, target, 0x06))
        self.assertFalse(d2_01.is_matching_response(measurement, target, 0x03))
        self.assertFalse(d2_01.is_matching_response(SimpleNamespace(), target, 0x03))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class TargetSelectionTests(unittest.TestCase):
    def test_only_default_switch_rows_are_targets_and_metering_needs_a_sensor(self):
        rows = [
            _row(channel=0, eep="D2-01-0B"),
            _row(channel=0, eep="D2-01-0B"),  # duplicate channel: once
            _row(channel=1, eep="D2-01-0B"),
            _row(switch_type="RPS", actuator_profile="ubiwizz_ubid1507c"),
            _row(platform="binary_sensor", device_class="door"),
        ]
        targets = d2_01.d2_01_targets(rows)
        self.assertEqual(
            [(t.channel, t.query_energy, t.query_power) for t in targets],
            [
                (0, False, False),
                (1, False, False),
            ],
        )
        with_sensor = d2_01.d2_01_targets(
            [*rows, _row(platform="sensor", device_class="powersensor")]
        )
        self.assertEqual(
            [(t.channel, t.query_energy, t.query_power) for t in with_sensor],
            [(0, True, True), (1, True, True)],
        )
        # An undeclared profile is queried for status only, never for metering.
        plain = d2_01.d2_01_targets(
            [_row(), _row(platform="sensor", device_class="powersensor")]
        )
        self.assertEqual(
            [(t.query_energy, t.query_power) for t in plain], [(False, False)]
        )

    def test_button_platform_is_forwarded_and_not_a_row_platform(self):
        self.assertIn("button", FORWARDED_PLATFORMS)
        self.assertNotIn("button", UI_DEVICE_PLATFORMS)
        self.assertEqual(d2_01_refresh_unique_id(SENDER, 1), "93266483-1-d2-01-refresh")


class FakeDongle:
    """Deliver ESP3 results and the physical write the way EnOceanDongle does."""

    def __init__(self, hass, *, accept=True, base_id=BASE_ID):
        self.hass = hass
        self.available = True
        self.base_id = base_id
        self.entry_id = "entry"
        self.accept = accept
        self.sent = []
        self.written_at = []

    def send(self, packet, response_callback=None, written_callback=None):
        self.sent.append(packet)
        if not self.accept:
            return False
        loop = self.hass.loop
        if written_callback is not None:
            loop.call_soon(written_callback)
        if response_callback is not None:
            loop.call_soon(response_callback, True)
        self.written_at.append(loop.time())
        return True


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        self.dongle = FakeDongle(self.hass)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _target(self, channel=0, **flags):
        return d2_01.D201Target(tuple(SENDER), channel, "ASP", "D2-01-0B", **flags)

    async def test_one_query_per_channel_waits_for_the_answer_and_spaces_writes(self):
        runner = d2_01.D201QueryRunner(self.hass, self.dongle)
        targets = [self._target(0), self._target(1)]

        async def answer_channel_zero():
            # The actuator answers channel 0 right after the write, inside the
            # 300 ms window; channel 1 stays silent.
            while not self.dongle.sent:
                await asyncio.sleep(0)
            await asyncio.sleep(0.02)
            async_dispatcher_send(
                self.hass, SIGNAL_RECEIVE_MESSAGE, _status_response(SENDER, 0)
            )

        answering = self.hass.async_create_task(answer_channel_zero())
        report = await runner.async_run(targets, reason="test")
        await answering
        self.assertEqual(len(self.dongle.sent), 2)
        self.assertEqual(
            [packet.data[1:3] for packet in self.dongle.sent], [[3, 0], [3, 1]]
        )
        self.assertEqual((report.sent, report.answered, report.unanswered), (2, 1, 1))
        self.assertEqual(report.outcomes, ["answered", "unanswered"])
        # Second write at least 250 ms after the first (spec window 300 ms).
        self.assertGreaterEqual(
            self.dongle.written_at[1] - self.dongle.written_at[0],
            d2_01.MIN_SPACING_SECONDS,
        )

    async def test_measurement_queries_follow_the_target_flags_only(self):
        runner = d2_01.D201QueryRunner(self.hass, self.dongle)
        with (
            patch.object(d2_01, "RESPONSE_WINDOW_SECONDS", 0.01),
            patch.object(d2_01, "MIN_SPACING_SECONDS", 0.0),
        ):
            await runner.async_run(
                [self._target(0, query_energy=True, query_power=True)], reason="test"
            )
            self.assertEqual(
                [packet.data[1:3] for packet in self.dongle.sent],
                [[3, 0], [6, 0x00], [6, 0x20]],
            )
            self.dongle.sent.clear()
            await runner.async_run([self._target(0)], reason="test")
            self.assertEqual([p.data[1:3] for p in self.dongle.sent], [[3, 0]])

    async def test_refused_queue_is_reported_and_never_retried(self):
        dongle = FakeDongle(self.hass, accept=False)
        runner = d2_01.D201QueryRunner(self.hass, dongle)
        report = await runner.async_run([self._target(0)], reason="test")
        self.assertEqual((report.sent, report.rejected), (0, 1))
        self.assertEqual(len(dongle.sent), 1)

    async def test_missing_base_id_sends_nothing(self):
        dongle = FakeDongle(self.hass, base_id=None)
        runner = d2_01.D201QueryRunner(self.hass, dongle)
        report = await runner.async_run([self._target(0)], reason="test")
        self.assertEqual(dongle.sent, [])
        self.assertEqual(report.skipped_no_base_id, 1)

    async def test_startup_pass_is_skipped_without_targets_or_base_id(self):
        self.assertIsNone(
            await d2_01.async_startup_pass(self.hass, self.dongle, "entry", [])
        )
        dongle = FakeDongle(self.hass, base_id=None)
        with patch.object(d2_01, "BASE_ID_WAIT_SECONDS", 0.0):
            self.assertIsNone(
                await d2_01.async_startup_pass(
                    self.hass, dongle, "entry", [self._target(0)]
                )
            )
        self.assertEqual(dongle.sent, [])

    async def test_button_press_runs_one_pass_and_translates_refusals(self):
        entry = SimpleNamespace(runtime_data=self.dongle)
        self.hass.config_entries = SimpleNamespace(async_entries=lambda domain: [entry])
        rows = [_row(eep="D2-01-0A")]
        created: list = []
        await button.async_setup_entry(
            self.hass, SimpleNamespace(options={CONF_UI_DEVICES: rows}), created.extend
        )
        self.assertEqual(len(created), 1)
        pressed = created[0]
        self.assertEqual(pressed.unique_id, "93266483-0-d2-01-refresh")
        self.assertEqual(pressed.extra_state_attributes["queries"], ["status"])
        pressed.hass = self.hass
        with patch.object(d2_01, "RESPONSE_WINDOW_SECONDS", 0.01):
            await pressed.async_press()
        self.assertEqual([p.data[1:3] for p in self.dongle.sent], [[3, 0]])

        self.dongle.accept = False
        with self.assertRaises(HomeAssistantError) as refused:
            await pressed.async_press()
        self.assertEqual(refused.exception.translation_key, "command_rejected")
        self.dongle.accept = True
        self.dongle.base_id = None
        with self.assertRaises(HomeAssistantError) as no_base:
            await pressed.async_press()
        self.assertEqual(no_base.exception.translation_key, "base_id_unavailable")

    def test_no_button_for_rps_rows(self):
        rows = [_row(switch_type="RPS", actuator_profile="ubiwizz_ubid1507c")]
        self.assertEqual(button._refresh_buttons(rows), [])


if __name__ == "__main__":
    unittest.main()

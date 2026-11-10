"""ESP3 framing diagnostics: count what the serial link reports, warn only on faults.

The vendored parser logs ``Message incomplete, buffered_bytes=N`` at WARNING
whenever a serial read ends inside the six-byte ESP3 header. That is ordinary
read fragmentation -- the bytes stay buffered and the next read completes the
frame -- yet it filled the Home Assistant system log (120 entries in 22 hours
for 1601 telegrams on the reference installation). The integration now counts
it as a diagnostic and keeps it out of the warning log, while real framing
faults (CRC errors, impossible lengths, frames that never complete) stay
visible at their original level and are counted too.

The vendored library is not modified: the monitor is a logging filter owned by
the integration and attached to the library's own loggers.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.enocean_custom import dongle as dongle_module
from custom_components.enocean_custom import esp3_monitor
from custom_components.enocean_custom.dongle import EnOceanDongle
from custom_components.enocean_custom.enocean_library.communicators import (
    SerialCommunicator,
)
from custom_components.enocean_custom.enocean_library.protocol.constants import (
    PACKET,
    PARSE_RESULT,
    RORG,
)
from custom_components.enocean_custom.enocean_library.protocol.packet import Packet
from custom_components.enocean_custom.esp3_monitor import Esp3FramingMonitor

LIBRARY = ROOT / "custom_components/enocean_custom/enocean_library"
PACKET_LOGGER = Packet.logger.name
SERIAL_LOGGER = SerialCommunicator.logger.name
ZERO = {
    "partial_header_reads": 0,
    "header_crc_errors": 0,
    "data_crc_errors": 0,
    "invalid_frame_lengths": 0,
    "frame_timeouts": 0,
}


def _rocker_frame() -> list[int]:
    """Return the wire bytes of one valid ERP1 RPS telegram (21 bytes)."""
    return Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.RPS, 0x30, 0x00, 0x3A, 0xFD, 0xD0, 0x30],
        optional=[0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0x44, 0x00],
    ).build()


class _Attached(unittest.TestCase):
    """Attach a fresh monitor for the duration of one test."""

    def setUp(self) -> None:
        self.monitor = Esp3FramingMonitor()
        self.monitor.attach()
        self.addCleanup(self.monitor.detach)


class PartialHeaderReadTests(_Attached):
    def test_a_read_ending_inside_the_header_is_counted_not_warned(self):
        wire = _rocker_frame()
        with self.assertNoLogs(PACKET_LOGGER, level="WARNING"):
            status, buffered, packet = Packet.parse_msg(wire[:4])

        self.assertEqual(status, PARSE_RESULT.INCOMPLETE)
        self.assertEqual(buffered, wire[:4])
        self.assertIsNone(packet)
        self.assertEqual(self.monitor.snapshot(), {**ZERO, "partial_header_reads": 1})

    def test_the_fragmented_frame_is_decoded_once_the_rest_arrives(self):
        wire = _rocker_frame()
        _, buffered, _ = Packet.parse_msg(wire[:4])

        status, remaining, packet = Packet.parse_msg(buffered + wire[4:])

        self.assertEqual(status, PARSE_RESULT.OK)
        self.assertEqual(remaining, [])
        self.assertEqual(packet.sender, [0x00, 0x3A, 0xFD, 0xD0])
        self.assertEqual(self.monitor.snapshot(), {**ZERO, "partial_header_reads": 1})

    def test_the_event_remains_available_as_a_debug_line(self):
        with self.assertLogs(esp3_monitor.__name__, level="DEBUG") as captured:
            Packet.parse_msg(_rocker_frame()[:5])

        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].levelno, logging.DEBUG)
        self.assertIn("5", captured.records[0].getMessage())

    def test_without_the_monitor_the_library_still_warns(self):
        self.monitor.detach()
        with self.assertLogs(PACKET_LOGGER, level="WARNING") as captured:
            Packet.parse_msg(_rocker_frame()[:4])

        self.assertEqual(
            [record.getMessage() for record in captured.records],
            ["Message incomplete, buffered_bytes=4"],
        )
        self.assertEqual(self.monitor.snapshot(), ZERO)


class RealFaultTests(_Attached):
    def test_a_header_crc_error_stays_an_error_and_is_counted(self):
        wire = _rocker_frame()
        wire[5] ^= 0xFF
        with self.assertLogs(PACKET_LOGGER, level="ERROR") as captured:
            status, _, _ = Packet.parse_msg(wire)

        self.assertEqual(status, PARSE_RESULT.CRC_MISMATCH)
        self.assertEqual(captured.records[0].getMessage(), "Header CRC error!")
        self.assertEqual(self.monitor.snapshot(), {**ZERO, "header_crc_errors": 1})

    def test_a_data_crc_error_stays_an_error_and_is_counted(self):
        wire = _rocker_frame()
        wire[-1] ^= 0xFF
        with self.assertLogs(PACKET_LOGGER, level="ERROR") as captured:
            status, _, _ = Packet.parse_msg(wire)

        self.assertEqual(status, PARSE_RESULT.CRC_MISMATCH)
        self.assertEqual(captured.records[0].getMessage(), "Data CRC error!")
        self.assertEqual(self.monitor.snapshot(), {**ZERO, "data_crc_errors": 1})

    def test_an_impossible_erp1_length_stays_an_error_and_is_counted(self):
        oversized = Packet(PACKET.RADIO_ERP1, data=[RORG.RPS] * 30, optional=[0] * 7)
        with self.assertLogs(PACKET_LOGGER, level="ERROR"):
            status, _, _ = Packet.parse_msg(oversized.build())

        self.assertEqual(status, PARSE_RESULT.CRC_MISMATCH)
        self.assertEqual(self.monitor.snapshot(), {**ZERO, "invalid_frame_lengths": 1})

    def test_a_frame_that_never_completes_stays_an_error_and_is_counted(self):
        with self.assertLogs(SERIAL_LOGGER, level="ERROR"):
            SerialCommunicator.logger.error(
                "Incomplete ESP3 frame timed out; discarding one byte"
            )

        self.assertEqual(self.monitor.snapshot(), {**ZERO, "frame_timeouts": 1})

    def test_unrelated_library_records_pass_through_untouched(self):
        with self.assertLogs(SERIAL_LOGGER, level="WARNING") as captured:
            SerialCommunicator.logger.warning("Unable to close serial port")

        self.assertEqual(len(captured.records), 1)
        self.assertEqual(self.monitor.snapshot(), ZERO)


class VendoredTemplateTests(unittest.TestCase):
    """The monitor matches log templates; a library update must not drift silently."""

    def test_every_watched_template_exists_verbatim_in_the_vendored_sources(self):
        sources = "\n".join(
            (LIBRARY / relative).read_text(encoding="utf-8")
            for relative in (
                "protocol/packet.py",
                "communicators/serialcommunicator.py",
            )
        )
        for template in esp3_monitor.WATCHED_TEMPLATES:
            with self.subTest(template=template):
                self.assertIn(template, sources)

    def test_the_counters_cover_every_watched_template(self):
        self.assertEqual(
            set(esp3_monitor.WATCHED_TEMPLATES.values()), set(ZERO), ZERO.keys()
        )


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    def _dongle(self) -> EnOceanDongle:
        with patch.object(dongle_module, "SerialCommunicator") as constructor:
            constructor.return_value = Mock()
            dongle = EnOceanDongle(MagicMock(), "/dev/null", "entry")
        dongle.hass.async_add_executor_job.side_effect = lambda fn, *args: (
            asyncio.to_thread(fn, *args)
        )
        return dongle

    def _attached(self, dongle: EnOceanDongle) -> bool:
        monitor = dongle._framing_monitor
        return all(
            monitor in logger.filters
            for logger in (Packet.logger, SerialCommunicator.logger)
        )

    async def test_setup_attaches_and_a_clean_unload_detaches(self):
        dongle = self._dongle()
        self.assertFalse(self._attached(dongle))
        with (
            patch.object(dongle_module, "ir"),
            patch.object(dongle_module, "dispatcher_send"),
        ):
            await dongle.async_setup()
            self.addCleanup(dongle._framing_monitor.detach)
            self.assertTrue(self._attached(dongle))

            dongle._communicator.is_alive.return_value = False
            self.assertTrue(await dongle.async_unload())

        self.assertFalse(self._attached(dongle))
        self.assertNotIn(dongle._framing_monitor, Packet.logger.filters)

    async def test_a_refused_unload_keeps_the_monitor_of_the_living_worker(self):
        dongle = self._dongle()
        with (
            patch.object(dongle_module, "ir"),
            patch.object(dongle_module, "dispatcher_send"),
        ):
            await dongle.async_setup()
            self.addCleanup(dongle._framing_monitor.detach)
            dongle._communicator.is_alive.return_value = True

            self.assertFalse(await dongle.async_unload())

        self.assertTrue(self._attached(dongle))

    async def test_attaching_twice_installs_one_filter(self):
        monitor = Esp3FramingMonitor()
        self.addCleanup(monitor.detach)
        monitor.attach()
        monitor.attach()

        self.assertEqual(Packet.logger.filters.count(monitor), 1)
        Packet.parse_msg(_rocker_frame()[:3])
        self.assertEqual(monitor.snapshot()["partial_header_reads"], 1)

    async def test_diagnostics_export_the_counters_without_identifiers(self):
        dongle = self._dongle()
        dongle._communicator.name = "EnOceanSerialCommunicator"
        dongle._communicator.transmit = None
        dongle._communicator.current_base_id = None
        dongle._framing_monitor.attach()
        self.addCleanup(dongle._framing_monitor.detach)
        Packet.parse_msg(_rocker_frame()[:2])

        exported = dongle.diagnostics()["esp3_framing"]

        self.assertEqual(exported, {**ZERO, "partial_header_reads": 1})

    def test_a_dongle_built_without_init_reports_no_counters(self):
        dongle = object.__new__(EnOceanDongle)
        dongle.serial_path = "/dev/null"
        dongle.identifier = "configured EnOcean dongle"
        dongle._available = False
        dongle._stopping = False
        dongle._base_id_retry_count = 0
        dongle._reconnect_attempts = 0
        dongle._reconnect_unsub = None
        dongle._started_at = dongle._last_packet_at = dongle._last_error = None
        dongle._received_packets = dongle._transmit_attempts = 0
        dongle._transmit_queued = dongle._transmit_rejected = 0
        dongle._response_packets = dongle._response_errors = 0
        dongle._last_response_at = dongle._last_response_code = None
        dongle._response_lock = dongle_module.Lock()
        dongle._pending_response_callbacks = []
        dongle._communicator = Mock()
        dongle._communicator.name = "EnOceanSerialCommunicator"
        dongle._communicator.transmit = None
        dongle._communicator.current_base_id = None

        self.assertIsNone(dongle.diagnostics()["esp3_framing"])


if __name__ == "__main__":
    unittest.main()

"""Release 2.15 workstream 2: reconnection and passive diagnostics."""

from __future__ import annotations

import asyncio
import queue
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.enocean_custom import dongle as dongle_module
from custom_components.enocean_custom.dongle import EnOceanDongle
from custom_components.enocean_custom.enocean_library.protocol.constants import (
    RETURN_CODE,
)
from custom_components.enocean_custom.enocean_library.protocol.packet import (
    ResponsePacket,
)

BASE_ID = [0xFF, 0x87, 0xCA, 0x00]


def _response(data, optional=()):
    packet = object.__new__(ResponsePacket)
    packet.response = RETURN_CODE.OK
    packet.response_data = list(data)
    packet.optional = list(optional)
    return packet


class ReconnectProofTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_living_old_worker_is_joined_and_rearms_without_reopening(self):
        dongle = object.__new__(EnOceanDongle)
        dongle._stopping = False
        dongle._reconnect_unsub = Mock()
        old = Mock()
        old.is_alive.return_value = True
        dongle._communicator = old
        dongle._build_communicator = Mock()
        dongle._schedule_reconnect = Mock()
        dongle.hass = MagicMock()
        dongle.hass.async_add_executor_job.side_effect = lambda fn, *args: (
            asyncio.to_thread(fn, *args)
        )

        await dongle._attempt_reconnect(None)

        old.join.assert_called_once_with(1)
        dongle._build_communicator.assert_not_called()
        dongle._schedule_reconnect.assert_called_once_with()

    async def test_missing_local_path_never_calls_serial_for_url(self):
        dongle = object.__new__(EnOceanDongle)
        dongle.serial_path = "/dev/serial/by-id/not-present"
        with (
            patch.object(dongle_module.os.path, "exists", return_value=False),
            patch.object(dongle_module, "SerialCommunicator") as constructor,
            self.assertRaises(dongle_module.serial.SerialException),
        ):
            dongle._build_communicator()
        constructor.assert_not_called()

    def test_only_a_valid_version_response_resets_the_attempt_count(self):
        dongle = object.__new__(EnOceanDongle)
        dongle._reconnect_attempts = 4
        dongle._gateway_info = None
        dongle.hass = MagicMock()

        dongle._version_response(_response([0] * 31))
        self.assertEqual(dongle._reconnect_attempts, 4)
        dongle._version_response(_response(range(32)))
        self.assertEqual(dongle._reconnect_attempts, 0)


class PassiveDiagnosticsTests(unittest.TestCase):
    def test_base_id_optional_byte_cycles(self):
        parser = dongle_module.parse_base_id_write_cycles
        self.assertIsNone(parser(_response(BASE_ID), BASE_ID))
        self.assertEqual(parser(_response(BASE_ID, [2]), BASE_ID), 2)
        self.assertEqual(parser(_response(BASE_ID, [0xFF]), BASE_ID), "unlimited")
        self.assertIsNone(parser(_response([1, 2, 3, 4], [2]), BASE_ID))

        communicator = Mock()
        communicator.is_alive.return_value = True
        communicator.name = "EnOceanSerialCommunicator"
        communicator.transmit = None
        communicator.current_base_id = list(BASE_ID)
        with patch.object(
            dongle_module, "SerialCommunicator", return_value=communicator
        ):
            dongle = EnOceanDongle(MagicMock(), "/dev/test")
        for optional, expected in (([0xFF], "unlimited"), ([2], 2), ([], None)):
            with self.subTest(optional=optional):
                dongle.callback(_response(BASE_ID, optional))
                self.assertEqual(
                    dongle.diagnostics()["base_id_write_cycles_remaining"], expected
                )

    def test_repeated_ute_requests_hint_without_any_transmission(self):
        tracker = dongle_module.RepeatedTeachInTracker()
        for index in range(3):
            self.assertFalse(tracker.observe(0x01020304, 100.0 + index))
        self.assertTrue(tracker.observe(0x01020304, 103.0))
        self.assertEqual(tracker.hinted_senders(103.0), 1)
        self.assertEqual(tracker.hinted_senders(404.0), 0)

    def test_queue_oldest_age_is_measured_and_cleared_on_write(self):
        communicator = Mock()
        communicator.is_alive.return_value = True
        communicator.name = "EnOceanSerialCommunicator"
        communicator.send.return_value = True
        communicator.transmit = queue.Queue()
        communicator.current_base_id = None
        with patch.object(
            dongle_module, "SerialCommunicator", return_value=communicator
        ):
            dongle = EnOceanDongle(MagicMock(), "/dev/test")
        dongle._available = True
        command = Mock()
        command.build.return_value = [0]
        with patch.object(dongle_module, "monotonic", return_value=100.0):
            self.assertTrue(dongle.send(command))
        with patch.object(dongle_module, "monotonic", return_value=103.5):
            self.assertEqual(
                dongle.diagnostics()["transmit_queue_oldest_age_seconds"], 3.5
            )
        dongle._packet_written(command)
        self.assertIsNone(dongle.diagnostics()["transmit_queue_oldest_age_seconds"])

        failed = Mock()
        dongle._queued_response_callbacks[id(command)] = (failed, None)
        dongle._stopping = True
        dongle._notify_status = Mock()
        dongle.hass.loop.call_soon_threadsafe.side_effect = lambda callback, *args: (
            callback(*args)
        )
        dongle._communicator_stopped("read failed")
        dongle._communicator_stopped("read failed")
        failed.assert_called_once_with(False)


class FakeWorker(threading.Thread):
    """A real stoppable thread with a fresh transmit queue per reconnect."""

    open_descriptors = 0
    max_open_descriptors = 0

    def __init__(self, *args, **kwargs):
        super().__init__(name="EnOceanReconnectCycleProbe", daemon=True)
        self.transmit = queue.Queue()
        self.current_base_id = list(BASE_ID)
        self._stop_event = threading.Event()
        self.closed = False

    def run(self):
        type(self).open_descriptors += 1
        type(self).max_open_descriptors = max(
            type(self).max_open_descriptors, type(self).open_descriptors
        )
        self._stop_event.wait(2)
        self.close()

    def stop(self):
        self._stop_event.set()

    def close(self):
        if not self.closed:
            self.closed = True
            if self.ident is not None:
                type(self).open_descriptors -= 1

    def request_base_id(self):
        return True

    def request_common_command(self, _data, _handler):
        return True


class ReconnectCycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_hundred_stop_reconnect_cycles_leave_no_thread_or_replay(self):
        FakeWorker.open_descriptors = 0
        FakeWorker.max_open_descriptors = 0
        with patch.object(dongle_module, "SerialCommunicator", FakeWorker):
            dongle = EnOceanDongle(MagicMock(), "/dev/test")
        dongle.hass.async_add_executor_job.side_effect = lambda fn, *args: (
            asyncio.to_thread(fn, *args)
        )
        dongle._schedule_reconnect = Mock()
        dongle._notify_status = Mock()
        dongle._build_communicator = lambda: FakeWorker()
        dongle._stopping = False
        dongle._reconnect_attempts = 1
        dongle._reconnect_unsub = None
        dongle._communicator.start()

        for _cycle in range(100):
            dongle._communicator.stop()
            dongle._communicator.join(1)
            dongle._communicator.close()
            dongle._communicator.transmit.put(object())
            dongle._available = False
            await dongle._attempt_reconnect(None)
            self.assertTrue(dongle._communicator.transmit.empty())
            self.assertLessEqual(FakeWorker.open_descriptors, 1)

        self.assertTrue(await dongle.async_unload())
        self.assertFalse(dongle._communicator.is_alive())
        self.assertEqual(FakeWorker.open_descriptors, 0)
        self.assertEqual(FakeWorker.max_open_descriptors, 1)
        self.assertFalse(
            any(
                thread.name == "EnOceanReconnectCycleProbe"
                for thread in threading.enumerate()
            )
        )


if __name__ == "__main__":
    unittest.main()

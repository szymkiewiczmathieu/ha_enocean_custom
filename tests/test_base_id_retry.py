"""The dongle re-reads a refused or lost Base ID on demand, within bounds."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has neither Home Assistant nor pyserial.
    from custom_components.enocean_custom import dongle as dongle_module
    from custom_components.enocean_custom.dongle import EnOceanDongle
    from custom_components.enocean_custom.enocean_library.communicators.communicator import (
        Communicator,
    )
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RETURN_CODE,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import (
        ResponsePacket,
    )
    from custom_components.enocean_custom.entity import get_gateway_base_id

    RUNTIME_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    RUNTIME_AVAILABLE = False

BASE_ID = [0xFF, 0x87, 0xCA, 0x00]


if RUNTIME_AVAILABLE:

    class RecordingCommunicator(Communicator):
        """Real Base-ID bookkeeping with the serial port and thread stubbed out."""

        def __init__(
            self,
            port,
            callback=None,
            stopped_callback=None,
            written_callback=None,
            teach_in=False,
        ):
            super().__init__(callback, teach_in=teach_in)
            self.name = "EnOceanSerialCommunicator"
            self.started = False

        def start(self):
            self.started = True

        def is_alive(self):
            return self.started

        def close(self):
            return None


def _response(code, data=()):
    packet = object.__new__(ResponsePacket)
    packet.response = code
    packet.response_data = list(data)
    return packet


@unittest.skipUnless(RUNTIME_AVAILABLE, "EnOcean runtime dependencies not installed")
class BaseIdRetryTests(unittest.TestCase):
    def setUp(self):
        with patch.object(dongle_module, "SerialCommunicator", RecordingCommunicator):
            self.dongle = EnOceanDongle(MagicMock(), "/dev/test-base-id")
        self.communicator = self.dongle._communicator
        self.communicator.start()
        self.dongle._available = True
        self.clock = 1_000.0
        patcher = patch.object(dongle_module, "monotonic", lambda: self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The start-up read issued by async_setup.
        self.assertTrue(self.communicator.request_base_id())

    def _dongle_answers(self, code, data=()):
        request = self.communicator.transmit.get_nowait()
        self.assertEqual(request.packet_type, PACKET.COMMON_COMMAND)
        self.assertEqual(request.data, [0x08])
        self.communicator._packet_written(request)
        self.communicator._response_received(_response(code, data))

    def test_refused_startup_read_is_retried_on_demand_with_backoff_and_cap(self):
        self._dongle_answers(RETURN_CODE.ERROR)
        self.assertIsNone(self.dongle.base_id)
        self.assertTrue(self.communicator.transmit.empty(), "no retry on its own")

        # The first command that needs the Base ID is skipped, but queues a
        # new read; an immediate second command does not (backoff), and
        # neither does one that arrives while the read is still pending.
        self.assertIsNone(get_gateway_base_id(self.dongle))
        self.assertEqual(self.communicator.transmit.qsize(), 1)
        self.assertIsNone(get_gateway_base_id(self.dongle))
        self.assertEqual(self.communicator.transmit.qsize(), 1)
        self.clock += 1_000
        self.assertIsNone(get_gateway_base_id(self.dongle))
        self.assertEqual(self.communicator.transmit.qsize(), 1)

        self._dongle_answers(RETURN_CODE.ERROR)
        for _attempt in range(EnOceanDongle.BASE_ID_RETRY_LIMIT - 1):
            self.clock += 1_000
            self.assertIsNone(get_gateway_base_id(self.dongle))
            self.assertEqual(self.communicator.transmit.qsize(), 1)
            self._dongle_answers(RETURN_CODE.ERROR)

        # The budget is spent: later commands are skipped without any radio
        # or serial traffic, so a dead dongle cannot cause an endless loop.
        self.clock += 1_000_000
        for _command in range(3):
            self.assertIsNone(get_gateway_base_id(self.dongle))
        self.assertTrue(self.communicator.transmit.empty())
        self.assertEqual(
            self.dongle._base_id_retry_count, EnOceanDongle.BASE_ID_RETRY_LIMIT
        )
        self.assertEqual(
            self.dongle.diagnostics()["base_id_retries"],
            EnOceanDongle.BASE_ID_RETRY_LIMIT,
        )
        self.assertFalse(self.dongle.diagnostics()["base_id_resolved"])

    def test_backoff_grows_between_attempts(self):
        self._dongle_answers(RETURN_CODE.ERROR)
        deadlines = []
        for _attempt in range(EnOceanDongle.BASE_ID_RETRY_LIMIT):
            self.assertTrue(self.dongle.retry_base_id())
            deadlines.append(self.dongle._base_id_retry_not_before - self.clock)
            self._dongle_answers(RETURN_CODE.ERROR)
            self.clock += 1_000
        self.assertEqual(deadlines, list(EnOceanDongle.BASE_ID_RETRY_BACKOFF_SECONDS))
        self.assertFalse(self.dongle.retry_base_id())

    def test_retry_resolves_the_base_id_for_the_next_command(self):
        self._dongle_answers(RETURN_CODE.ERROR)
        self.assertIsNone(get_gateway_base_id(self.dongle))
        self._dongle_answers(RETURN_CODE.OK, BASE_ID)

        self.assertEqual(get_gateway_base_id(self.dongle), BASE_ID)
        self.assertTrue(self.communicator.transmit.empty())
        self.assertFalse(self.dongle.retry_base_id())
        self.assertTrue(self.dongle.diagnostics()["base_id_resolved"])
        self.assertEqual(self.dongle.diagnostics()["base_id_retries"], 1)

    def test_pending_startup_read_is_not_duplicated(self):
        # The start-up read has not been answered yet.
        self.assertEqual(self.communicator.transmit.qsize(), 1)
        self.assertIsNone(get_gateway_base_id(self.dongle))
        self.assertEqual(self.communicator.transmit.qsize(), 1)
        self.assertEqual(self.dongle._base_id_retry_count, 0)

    def test_unavailable_worker_never_queues_a_retry(self):
        self._dongle_answers(RETURN_CODE.ERROR)
        self.dongle._available = False
        self.assertIsNone(get_gateway_base_id(self.dongle))
        self.assertTrue(self.communicator.transmit.empty())
        self.assertEqual(self.dongle._base_id_retry_count, 0)

    def test_gateways_without_the_retry_hook_are_left_alone(self):
        self.assertIsNone(get_gateway_base_id(SimpleNamespace(base_id=None)))
        self.assertIsNone(get_gateway_base_id(None))
        self.assertEqual(
            get_gateway_base_id(SimpleNamespace(base_id=tuple(BASE_ID))), BASE_ID
        )


if __name__ == "__main__":
    unittest.main()

"""Regression tests: a UTE teach-in is never acknowledged on its own.

Answering a UTE telegram is an unsolicited radio transmission that pairs
whichever device asks. It must only ever happen inside a session an operator
opened explicitly, so the runtime worker is built with ``teach_in=False`` and
the vendored communicator defaults to the same.
"""

from __future__ import annotations

import ast
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

COMPONENT_ROOT = ROOT / "custom_components/enocean_custom"
DONGLE_PATH = COMPONENT_ROOT / "dongle.py"
COMMUNICATOR_PATH = COMPONENT_ROOT / "enocean_library/communicators/communicator.py"
SERIAL_PATH = COMPONENT_ROOT / "enocean_library/communicators/serialcommunicator.py"

try:  # The bare CI lifecycle environment has no pyserial or beautifulsoup4.
    from custom_components.enocean_custom.enocean_library.communicators import (
        serialcommunicator,
    )
    from custom_components.enocean_custom.enocean_library.communicators.communicator import (
        Communicator,
    )
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RETURN_CODE,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import (
        RadioPacket,
        ResponsePacket,
        UTETeachInPacket,
    )

    RADIO_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    RADIO_AVAILABLE = False

# One complete ESP3 frame carrying a bidirectional UTE teach-in request from
# sender 0x01 0x94 0xE3 0xB9. Answering it is precisely what must not happen
# outside an explicitly opened session.
UTE_FRAME = [
    0x55,
    0x00,
    0x0D,
    0x07,
    0x01,
    0xFD,
    0xD4,
    0xA0,
    0xFF,
    0x3E,
    0x00,
    0x01,
    0x01,
    0xD2,
    0x01,
    0x94,
    0xE3,
    0xB9,
    0x00,
    0x01,
    0xFF,
    0xFF,
    0xFF,
    0xFF,
    0x40,
    0x00,
    0xAB,
]
UTE_SENDER = [0x01, 0x94, 0xE3, 0xB9]
BASE_ID = [0xFF, 0x87, 0xCA, 0x00]


def _init_defaults(path: Path, class_name: str) -> dict[str, ast.expr]:
    """Return the ``__init__`` parameter defaults of a class, by name."""
    tree = ast.parse(path.read_text())
    class_node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    init = next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    positional = init.args.posonlyargs + init.args.args
    defaults = dict(
        zip(positional[len(positional) - len(init.args.defaults) :], init.args.defaults)
    )
    defaults.update(
        {
            argument.arg: default
            for argument, default in zip(init.args.kwonlyargs, init.args.kw_defaults)
            if default is not None
        }
    )
    return {
        argument.arg if isinstance(argument, ast.arg) else argument: default
        for argument, default in defaults.items()
    }


def _calls_to(path: Path, name: str) -> list[ast.Call]:
    """Return every call to a given callable name in a module."""
    return [
        node
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    ]


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    """Return the value node of a keyword argument, if the call passes it."""
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


class AutomaticTeachInIsNeverEnabledTests(unittest.TestCase):
    """Source-level guarantees, verifiable without any runtime dependency."""

    def test_vendored_communicator_defaults_to_no_acknowledgement(self):
        default = _init_defaults(COMMUNICATOR_PATH, "Communicator")["teach_in"]
        self.assertIsInstance(default, ast.Constant)
        self.assertIs(default.value, False)

    def test_serial_communicator_defaults_to_no_acknowledgement(self):
        default = _init_defaults(SERIAL_PATH, "SerialCommunicator")["teach_in"]
        self.assertIsInstance(default, ast.Constant)
        self.assertIs(default.value, False)

    def test_serial_communicator_forwards_the_session_flag(self):
        # The subclass must hand teach_in to the base class rather than let it
        # fall back to a default, so an explicit session is still expressible.
        source = SERIAL_PATH.read_text()
        self.assertIn("super().__init__(callback, teach_in=teach_in)", source)

    def test_runtime_dongle_builds_its_worker_without_teach_in(self):
        runtime_calls = [
            call
            for call in _calls_to(DONGLE_PATH, "SerialCommunicator")
            if _keyword(call, "callback") is not None
        ]
        self.assertEqual(len(runtime_calls), 1)
        teach_in = _keyword(runtime_calls[0], "teach_in")
        self.assertIsInstance(teach_in, ast.Constant)
        self.assertIs(teach_in.value, False)

    def test_no_integration_module_requests_automatic_teach_in(self):
        offenders = []
        for module_path in sorted(COMPONENT_ROOT.rglob("*.py")):
            for node in ast.walk(ast.parse(module_path.read_text())):
                if not isinstance(node, ast.Call):
                    continue
                value = _keyword(node, "teach_in")
                if isinstance(value, ast.Constant) and value.value is True:
                    offenders.append(str(module_path.relative_to(ROOT)))
        self.assertEqual(offenders, [])


@unittest.skipUnless(RADIO_AVAILABLE, "EnOcean radio dependencies not installed")
class UnsolicitedTeachInTests(unittest.TestCase):
    """No UTE received outside an explicit session may produce any packet."""

    def _parse_ute(self, communicator):
        received = []
        communicator._Communicator__callback = received.append
        communicator._buffer = list(UTE_FRAME)
        communicator.parse()
        return received

    def test_ute_with_unknown_base_id_transmits_nothing(self):
        communicator = Communicator()
        received = self._parse_ute(communicator)

        # The telegram is still parsed and dispatched: passive extraction of
        # the UTE identity is preserved, only the answer is withheld.
        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], UTETeachInPacket)
        self.assertEqual(received[0].sender, UTE_SENDER)
        self.assertEqual(communicator.transmit.qsize(), 0)
        self.assertEqual(len(communicator._pending_ute_packets), 0)
        # Not even the Base-ID lookup that answering would require is started.
        self.assertFalse(communicator._base_id_requested)
        self.assertEqual(communicator._buffer, [])

    def test_ute_with_known_base_id_transmits_nothing(self):
        communicator = Communicator()
        communicator.base_id = BASE_ID
        received = self._parse_ute(communicator)

        self.assertEqual(len(received), 1)
        self.assertEqual(communicator.transmit.qsize(), 0)
        self.assertEqual(len(communicator._pending_ute_packets), 0)

    def test_repeated_ute_telegrams_never_accumulate_or_answer(self):
        communicator = Communicator()
        for _ in range(5):
            self._parse_ute(communicator)
        self.assertEqual(communicator.transmit.qsize(), 0)
        self.assertEqual(len(communicator._pending_ute_packets), 0)

    def test_session_closing_mid_flight_drops_the_deferred_acknowledgement(self):
        communicator = Communicator(teach_in=True)
        self._parse_ute(communicator)
        base_id_request = communicator.transmit.get_nowait()
        communicator._packet_written(base_id_request)
        self.assertEqual(len(communicator._pending_ute_packets), 1)

        # The session closes while the Base-ID command is still in flight.
        communicator.teach_in = False
        response = object.__new__(ResponsePacket)
        response.response = RETURN_CODE.OK
        response.response_data = BASE_ID
        communicator._response_received(response)

        self.assertEqual(communicator._base_id, BASE_ID)
        self.assertEqual(communicator.transmit.qsize(), 0)
        self.assertEqual(len(communicator._pending_ute_packets), 0)

    def test_stop_discards_pending_teach_ins(self):
        communicator = Communicator(teach_in=True)
        self._parse_ute(communicator)
        self.assertEqual(len(communicator._pending_ute_packets), 1)
        communicator.stop()
        self.assertEqual(len(communicator._pending_ute_packets), 0)


@unittest.skipUnless(RADIO_AVAILABLE, "EnOcean radio dependencies not installed")
class ExplicitTeachInSessionTests(unittest.TestCase):
    """An explicitly opened session keeps answering, or refuses honestly."""

    def _parse_ute(self, communicator):
        received = []
        communicator._Communicator__callback = received.append
        communicator._buffer = list(UTE_FRAME)
        communicator.parse()
        return received

    def test_known_base_id_answers_the_teach_in(self):
        communicator = Communicator(teach_in=True)
        communicator.base_id = BASE_ID
        self._parse_ute(communicator)

        self.assertEqual(communicator.transmit.qsize(), 1)
        answer = communicator.transmit.get_nowait()
        self.assertIsInstance(answer, RadioPacket)
        # Accepted, addressed to the sender that asked, sent from the Base ID.
        self.assertEqual(answer.data[1] & 0b00110000, 0b00010000)
        self.assertEqual(answer.data[8:12], BASE_ID)
        self.assertEqual(answer.optional[1:5], UTE_SENDER)

    def test_unknown_base_id_defers_then_answers_once_resolved(self):
        communicator = Communicator(teach_in=True)
        self._parse_ute(communicator)

        request = communicator.transmit.get_nowait()
        self.assertEqual(request.packet_type, PACKET.COMMON_COMMAND)
        self.assertEqual(request.data[0], 0x08)
        self.assertEqual(len(communicator._pending_ute_packets), 1)

        communicator._packet_written(request)
        response = object.__new__(ResponsePacket)
        response.response = RETURN_CODE.OK
        response.response_data = BASE_ID
        communicator._response_received(response)

        self.assertEqual(communicator.transmit.qsize(), 1)
        answer = communicator.transmit.get_nowait()
        self.assertEqual(answer.optional[1:5], UTE_SENDER)
        self.assertEqual(len(communicator._pending_ute_packets), 0)

    def test_refused_base_id_gives_up_without_answering(self):
        communicator = Communicator(teach_in=True)
        self._parse_ute(communicator)

        refusal = object.__new__(ResponsePacket)
        refusal.response = RETURN_CODE.WRONG_PARAM
        refusal.response_data = []
        for _ in range(communicator.BASE_ID_MAX_AUTO_RETRIES + 1):
            request = communicator.transmit.get_nowait()
            self.assertEqual(request.packet_type, PACKET.COMMON_COMMAND)
            communicator._packet_written(request)
            communicator._response_received(refusal)

        # An unresolvable Base ID is an honest failure: the teach-in is simply
        # not answered, and the retries stay bounded.
        self.assertEqual(communicator.transmit.qsize(), 0)
        self.assertIsNone(communicator._base_id)


@unittest.skipUnless(RADIO_AVAILABLE, "EnOcean radio dependencies not installed")
class SerialWorkerTests(unittest.TestCase):
    """The worker the integration actually starts must stay radio-silent."""

    def test_running_worker_writes_nothing_for_an_unsolicited_ute(self):
        received = []
        delivered = threading.Event()
        writes = []

        class ReplayingSerial:
            def __init__(self, *_args, **_kwargs):
                self._frames = [bytes(UTE_FRAME)]
                self.closed = False

            def read(self, _size):
                return self._frames.pop() if self._frames else b""

            @staticmethod
            def write(data):
                writes.append(bytes(data))
                return len(data)

            @staticmethod
            def cancel_read():
                return None

            @staticmethod
            def cancel_write():
                return None

            def close(self):
                self.closed = True

        def receive(packet):
            received.append(packet)
            delivered.set()

        threads_before = threading.active_count()
        original_serial = serialcommunicator.serial.Serial
        serialcommunicator.serial.Serial = ReplayingSerial
        worker = None
        try:
            worker = serialcommunicator.SerialCommunicator("redacted", callback=receive)
            self.assertFalse(worker.teach_in)
            worker.start()
            self.assertTrue(delivered.wait(2), "UTE telegram was never dispatched")
            worker.stop()
            worker.join(2)
            self.assertFalse(worker.is_alive())
        finally:
            if worker is not None and worker.is_alive():  # pragma: no cover
                worker.stop()
                worker.join(2)
            serialcommunicator.serial.Serial = original_serial

        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], UTETeachInPacket)
        self.assertEqual(writes, [])
        self.assertEqual(threading.active_count(), threads_before)


if __name__ == "__main__":
    unittest.main()

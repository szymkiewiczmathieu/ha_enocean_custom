"""Regression tests for the EnOcean serial lifecycle using only stdlib."""

import ast
import asyncio
import json
import queue
import struct
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).parents[1]
DONGLE_PATH = ROOT / "custom_components/enocean_custom/dongle.py"
SERIAL_PATH = (
    ROOT
    / "custom_components/enocean_custom/enocean_library/communicators/serialcommunicator.py"
)
CLIMATE_PATH = ROOT / "custom_components/enocean_custom/climate.py"
INIT_PATH = ROOT / "custom_components/enocean_custom/__init__.py"
MANIFEST_PATH = ROOT / "custom_components/enocean_custom/manifest.json"
SERVICES_PATH = ROOT / "custom_components/enocean_custom/services.yaml"
STRINGS_PATH = ROOT / "custom_components/enocean_custom/strings.json"
EN_TRANSLATION_PATH = ROOT / "custom_components/enocean_custom/translations/en.json"
HACS_PATH = ROOT / "hacs.json"
BRAND_ICON_PATH = ROOT / "custom_components/enocean_custom/brand/icon.png"
VENDORED_LICENSE_PATH = (
    ROOT / "custom_components/enocean_custom/enocean_library/LICENSE"
)


def _load_dongle_members(*names):
    """Load selected dongle members without importing Home Assistant."""
    tree = ast.parse(DONGLE_PATH.read_text())
    dongle_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "EnOceanDongle"
    )
    class_members: list[ast.stmt] = []
    for node in dongle_class.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        ):
            class_members.append(node)
    top_level: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            top_level.append(node)
    module_body: list[ast.stmt] = [
        ast.ClassDef(
            name="EnOceanDongle",
            bases=[],
            keywords=[],
            body=class_members or [ast.Pass()],
            decorator_list=[],
        )
    ]
    module_body.extend(top_level)
    isolated = ast.Module(body=module_body, type_ignores=[])
    ast.fix_missing_locations(isolated)
    serial = Mock()
    serial.SerialException = OSError
    namespace = {
        "_LOGGER": Mock(),
        "Any": object,
        "SerialCommunicator": Mock(),
        "SIGNAL_DONGLE_STATUS": "dongle_status",
        "dispatcher_send": Mock(),
        "samefile": Mock(),
        "serial": serial,
    }
    exec(compile(isolated, str(DONGLE_PATH), "exec"), namespace)  # noqa: S102
    return namespace


def _load_serial_class(serial_port=None):
    """Load SerialCommunicator.run/close without pyserial or its base class."""
    tree = ast.parse(SERIAL_PATH.read_text())
    source_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SerialCommunicator"
    )
    members: list[ast.stmt] = []
    for node in source_class.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name)
            and target.id
            in {"RESPONSE_TIMEOUT_SECONDS", "FRAME_ASSEMBLY_TIMEOUT_SECONDS"}
            for target in node.targets
        ):
            members.append(node)
        if isinstance(node, ast.FunctionDef) and node.name in {
            "__init__",
            "_packet_written",
            "_response_received",
            "_stop_on_response_timeout",
            "run",
            "close",
            "send",
            "stop",
        }:
            members.append(node)
    isolated = ast.Module(
        body=[
            ast.ClassDef(
                name="SerialCommunicator",
                bases=[ast.Name(id="Communicator", ctx=ast.Load())],
                keywords=[],
                body=members,
                decorator_list=[],
            )
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(isolated)

    class Communicator(threading.Thread):
        def __init__(self, callback=None, teach_in=False):
            super().__init__()
            self._stop_flag = threading.Event()
            self.transmit = queue.Queue()
            self._buffer = bytearray()
            self.teach_in = teach_in

        def _get_from_send_queue(self):
            try:
                return self.transmit.get_nowait()
            except queue.Empty:
                return None

        def stop(self):
            self._stop_flag.set()

        def send(self, packet):
            if started := getattr(self, "_test_send_started", None):
                started.set()
                self._test_send_release.wait(1)  # type: ignore[attr-defined]
            self.transmit.put(packet)
            return True

        def parse(self):
            return None

        def _packet_written(self, packet):
            return None

        def _response_received(self, packet):
            return None

        def _response_timed_out(self, packet):
            return None

    serial = SimpleNamespace(
        Serial=lambda *args, **kwargs: serial_port or Mock(),
        SerialException=OSError,
    )
    namespace = {
        "Communicator": Communicator,
        "serial": serial,
        "threading": threading,
        "time": time,
    }
    exec(compile(isolated, str(SERIAL_PATH), "exec"), namespace)  # noqa: S102
    return namespace["SerialCommunicator"]


class DongleLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_unload_disconnects_and_stops_serial_thread(self):
        namespace = _load_dongle_members("async_unload")
        dongle = namespace["EnOceanDongle"]()
        dongle._stopping = False
        dongle._available = True
        dongle._communicator = Mock()
        dongle._communicator.is_alive.return_value = False
        dongle.hass = Mock()
        dongle.hass.async_add_executor_job.side_effect = lambda function, *args: (
            asyncio.to_thread(function, *args)
        )

        result = await dongle.async_unload()

        self.assertTrue(result)
        self.assertTrue(dongle._stopping)
        self.assertFalse(dongle._available)
        namespace["dispatcher_send"].assert_called_once_with(
            dongle.hass, "dongle_status", False
        )
        dongle._communicator.stop.assert_called_once_with()
        dongle._communicator.join.assert_called_once_with(1)
        namespace["_LOGGER"].warning.assert_not_called()

    async def test_unload_refuses_reload_if_thread_stays_alive(self):
        namespace = _load_dongle_members("async_unload")
        dongle = namespace["EnOceanDongle"]()
        dongle._stopping = False
        dongle._available = True
        dongle._notify_status = Mock()
        dongle._communicator = Mock()
        dongle._communicator.is_alive.return_value = True
        dongle.hass = Mock()
        dongle.hass.async_add_executor_job.side_effect = lambda function, *args: (
            asyncio.to_thread(function, *args)
        )

        result = await dongle.async_unload()

        self.assertFalse(result)
        self.assertFalse(dongle._stopping)
        dongle._notify_status.assert_called_once_with()
        namespace["_LOGGER"].warning.assert_called_once()

    def test_serial_error_redacts_device_path(self):
        namespace = _load_dongle_members("_communicator_stopped")
        dongle = namespace["EnOceanDongle"]()
        dongle.serial_path = "/dev/serial/by-id/private-serial"
        dongle._available = True
        dongle._stopping = False
        dongle.hass = Mock()
        dongle._notify_status = Mock()
        dongle._response_lock = threading.Lock()
        dongle._queued_response_callbacks = {}
        dongle._pending_response_callbacks = deque()

        dongle._communicator_stopped("read failed on /dev/serial/by-id/private-serial")

        self.assertNotIn("private-serial", dongle._last_error)
        self.assertIn("[REDACTED]", dongle._last_error)
        dongle.hass.loop.call_soon_threadsafe.assert_called_once_with(
            dongle._notify_status
        )

    def test_diagnostics_redact_path_from_thread_name(self):
        namespace = _load_dongle_members("available", "diagnostics")
        dongle = namespace["EnOceanDongle"]()
        dongle.serial_path = "/dev/serial/by-id/private-hardware-serial"
        dongle.identifier = "configured EnOcean dongle"
        dongle._available = True
        dongle._stopping = False
        dongle._started_at = None
        dongle._last_packet_at = None
        dongle._last_error = None
        dongle._received_packets = 0
        dongle._transmit_attempts = 0
        dongle._transmit_queued = 0
        dongle._transmit_rejected = 0
        dongle._response_packets = 0
        dongle._response_errors = 0
        dongle._last_response_at = None
        dongle._last_response_code = None
        dongle._response_lock = threading.Lock()
        dongle._pending_response_callbacks = deque()
        dongle._communicator = Mock()
        dongle._communicator.is_alive.return_value = True
        dongle._communicator.name = (
            "EnOceanSerialCommunicator[/dev/serial/by-id/private-hardware-serial]"
        )
        dongle._communicator.transmit = None

        diagnostics = dongle.diagnostics()

        self.assertNotIn("private-hardware-serial", str(diagnostics))
        self.assertIn("[REDACTED]", diagnostics["thread_name"])

    def test_validate_path_closes_probe_descriptor(self):
        namespace = _load_dongle_members("validate_path")
        communicator = namespace["SerialCommunicator"].return_value

        self.assertTrue(namespace["validate_path"]("/dev/test"))

        communicator.close.assert_called_once_with()

    def test_validate_path_does_not_log_private_device_path(self):
        namespace = _load_dongle_members("validate_path")
        private_path = "/dev/serial/by-id/private-hardware-serial"
        namespace["SerialCommunicator"].side_effect = OSError(private_path)

        self.assertFalse(namespace["validate_path"](private_path))

        log_call = namespace["_LOGGER"].warning.call_args
        self.assertIsNotNone(log_call)
        self.assertNotIn(private_path, str(log_call))

    def test_serial_thread_name_does_not_expose_device_path(self):
        private_path = "/dev/serial/by-id/private-hardware-serial"
        serial_class = _load_serial_class(Mock())

        communicator = serial_class(private_path)

        self.assertEqual(communicator.name, "EnOceanSerialCommunicator")
        self.assertNotIn(private_path, communicator.name)

    def test_serial_alias_validation_does_not_open_a_second_reader(self):
        namespace = _load_dongle_members("same_device")
        namespace["samefile"].return_value = True

        self.assertTrue(namespace["same_device"]("/dev/ttyUSB0", "/dev/by-id/x"))
        namespace["samefile"].assert_called_once_with("/dev/ttyUSB0", "/dev/by-id/x")

        namespace["samefile"].side_effect = OSError
        self.assertFalse(namespace["same_device"]("missing-a", "missing-b"))

    def test_serial_run_always_closes_descriptor(self):
        serial_class = _load_serial_class()
        communicator = serial_class()
        communicator._stop_flag = Mock()
        communicator._stop_flag.is_set.return_value = True
        communicator.logger = Mock()
        serial_port = Mock()
        communicator._SerialCommunicator__ser = serial_port

        communicator.run()

        serial_port.close.assert_called_once_with()
        communicator.logger.info.assert_any_call("SerialCommunicator stopped")

    def test_serial_stop_callback_runs_after_descriptor_close(self):
        serial_port = Mock()
        serial_port.read.side_effect = OSError("device disconnected")
        stopped_callback = Mock()
        serial_class = _load_serial_class(serial_port)
        communicator = serial_class("/dev/test", stopped_callback=stopped_callback)
        communicator.logger = Mock()

        communicator.run()

        serial_port.close.assert_called_once_with()
        stopped_callback.assert_called_once_with("OSError: serial read failed")

    def test_stop_interrupts_write_without_draining_queued_packets(self):
        class BlockingSerial:
            def __init__(self):
                self.write_started = threading.Event()
                self.first_write_cancelled = threading.Event()
                self.cancel_read_calls = 0
                self.cancel_write_calls = 0
                self.closed = False
                self.write_count = 0

            def write(self, data):
                self.write_count += 1
                if self.write_count == 1:
                    self.write_started.set()
                    self.first_write_cancelled.wait(5)
                    # pyserial may report a cancelled write as zero bytes.
                    return 0
                # pyserial cancellation is one-shot. A worker that drains the
                # remaining queue can spend one timeout per packet.
                time.sleep(0.5)
                return len(data)

            def read(self, size):
                return b""

            def cancel_read(self):
                self.cancel_read_calls += 1

            def cancel_write(self):
                self.cancel_write_calls += 1
                self.first_write_cancelled.set()

            def close(self):
                self.closed = True

        class Packet:
            def build(self):
                return [0x01]

        serial_port = BlockingSerial()
        serial_class = _load_serial_class(serial_port)
        communicator = serial_class("/dev/test")
        communicator.logger = Mock()
        for _ in range(5):
            communicator.transmit.put(Packet())
        communicator.start()
        self.assertTrue(serial_port.write_started.wait(1))

        try:
            communicator.stop()
            communicator.join(1)
            self.assertFalse(communicator.is_alive())
            self.assertEqual(serial_port.cancel_read_calls, 1)
            self.assertEqual(serial_port.cancel_write_calls, 1)
            self.assertEqual(serial_port.write_count, 1)
            self.assertIsNone(communicator._terminal_error)
            self.assertTrue(serial_port.closed)
        finally:
            serial_port.first_write_cancelled.set()
            communicator.join(1)

    def test_stop_wins_during_serial_write_method_lookup(self):
        """A packet may not enter write after shutdown has been linearized."""

        class LookupPausedSerial:
            def __init__(self):
                self.write_lookup = threading.Event()
                self.release_lookup = threading.Event()
                self.write_count = 0
                self.closed = False
                self.cancel_read_calls = 0
                self.cancel_write_calls = 0

            def __getattribute__(self, name):
                if name == "write":
                    object.__getattribute__(self, "write_lookup").set()
                    if not object.__getattribute__(self, "release_lookup").wait(2):
                        raise TimeoutError("write lookup was not released")
                    return object.__getattribute__(self, "_write")
                return object.__getattribute__(self, name)

            def _write(self, data):
                self.write_count += 1
                return len(data)

            def read(self, size):
                return b""

            def cancel_read(self):
                self.cancel_read_calls += 1

            def cancel_write(self):
                self.cancel_write_calls += 1

            def close(self):
                self.closed = True

        class Packet:
            def build(self):
                return [0x01]

        serial_port = LookupPausedSerial()
        serial_class = _load_serial_class(serial_port)
        communicator = serial_class("/dev/test")
        communicator.logger = Mock()
        communicator.transmit.put(Packet())
        communicator.start()
        self.assertTrue(serial_port.write_lookup.wait(1))

        communicator.stop()
        self.assertTrue(communicator._stop_flag.is_set())
        serial_port.release_lookup.set()
        communicator.join(1)

        self.assertEqual(serial_port.write_count, 0)
        self.assertEqual(serial_port.cancel_read_calls, 1)
        self.assertEqual(serial_port.cancel_write_calls, 1)
        self.assertTrue(serial_port.closed)
        self.assertFalse(communicator.is_alive())

    def test_incomplete_generic_frame_expires_after_inter_byte_timeout(self):
        """A generic truncated ESP3 frame may not poison the serial buffer forever."""

        class PartialSerial:
            def __init__(self):
                self.reads = 0
                self.closed = False

            def read(self, size):
                self.reads += 1
                if self.reads == 1:
                    return b"\x55\xff\xff\x00\x04\x00"
                return b""

            def cancel_read(self):
                return None

            def cancel_write(self):
                return None

            def close(self):
                self.closed = True

        serial_port = PartialSerial()
        serial_class = _load_serial_class(serial_port)
        communicator = serial_class("/dev/test")
        communicator.logger = Mock()
        communicator.FRAME_ASSEMBLY_TIMEOUT_SECONDS = 0.01
        communicator.start()

        deadline = time.monotonic() + 1
        while serial_port.reads == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        while (
            communicator._buffer
            and communicator._buffer[0] == 0x55
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)

        communicator.stop()
        communicator.join(1)

        self.assertFalse(communicator.is_alive())
        self.assertTrue(serial_port.closed)
        self.assertTrue(
            not communicator._buffer or communicator._buffer[0] != 0x55,
            "truncated generic ESP3 frame did not expire",
        )

    def test_serial_writes_only_one_command_until_its_response(self):
        class ControlledSerial:
            def __init__(self):
                self.writes = []
                self.first_written = threading.Event()
                self.second_written = threading.Event()

            def write(self, data):
                self.writes.append(bytes(data))
                event = (
                    self.first_written if len(self.writes) == 1 else self.second_written
                )
                event.set()
                return len(data)

            def read(self, size):
                return b""

            def cancel_read(self):
                return None

            def cancel_write(self):
                return None

            def close(self):
                return None

        class Packet:
            def __init__(self, value):
                self.value = value

            def build(self):
                return [self.value]

        serial_port = ControlledSerial()
        serial_class = _load_serial_class(serial_port)
        communicator = serial_class("/dev/test")
        communicator.logger = Mock()
        communicator.transmit.put(Packet(0xA1))
        communicator.transmit.put(Packet(0xB2))
        communicator.start()
        self.assertTrue(serial_port.first_written.wait(1))
        self.assertFalse(
            serial_port.second_written.wait(0.05),
            "second command was written before the first RESPONSE",
        )

        communicator._response_received(object())
        self.assertTrue(serial_port.second_written.wait(1))
        communicator.stop()
        communicator.join(1)

        self.assertEqual(serial_port.writes, [b"\xa1", b"\xb2"])
        self.assertFalse(communicator.is_alive())

    def test_missing_response_stops_transport_before_next_write(self):
        class NoResponseSerial:
            def __init__(self):
                self.writes = []
                self.first_written = threading.Event()

            def write(self, data):
                self.writes.append(bytes(data))
                self.first_written.set()
                return len(data)

            def read(self, size):
                return b""

            def cancel_read(self):
                return None

            def cancel_write(self):
                return None

            def close(self):
                return None

        class Packet:
            def __init__(self, value):
                self.value = value

            def build(self):
                return [self.value]

        stopped = threading.Event()
        stop_errors = []

        def stopped_callback(error):
            stop_errors.append(error)
            stopped.set()

        serial_port = NoResponseSerial()
        serial_class = _load_serial_class(serial_port)
        communicator = serial_class("/dev/test", stopped_callback=stopped_callback)
        communicator.RESPONSE_TIMEOUT_SECONDS = 0.05
        communicator.logger = Mock()
        communicator.transmit.put(Packet(0xA1))
        communicator.transmit.put(Packet(0xB2))
        communicator.start()

        self.assertTrue(serial_port.first_written.wait(1))
        self.assertTrue(stopped.wait(1), "missing RESPONSE did not stop transport")
        communicator.join(1)

        self.assertEqual(serial_port.writes, [b"\xa1"])
        self.assertEqual(stop_errors, ["ESP3 response timeout; transport stopped"])
        self.assertFalse(communicator.is_alive())

    def test_send_rejects_packets_after_stop(self):
        serial_class = _load_serial_class(Mock())
        communicator = serial_class("/dev/test")
        communicator.logger = Mock()
        communicator.stop()

        accepted = communicator.send(Mock())

        self.assertFalse(accepted)
        self.assertTrue(communicator.transmit.empty())

    def test_stop_is_serialized_with_an_inflight_send(self):
        serial_class = _load_serial_class(Mock())
        communicator = serial_class("/dev/test")
        communicator.logger = Mock()
        communicator._test_send_started = threading.Event()
        communicator._test_send_release = threading.Event()
        stop_done = threading.Event()
        send_result = []

        sender = threading.Thread(
            target=lambda: send_result.append(communicator.send(Mock()))
        )
        sender.start()
        self.assertTrue(communicator._test_send_started.wait(1))

        stopper = threading.Thread(
            target=lambda: (communicator.stop(), stop_done.set())
        )
        stopper.start()
        try:
            self.assertFalse(
                stop_done.wait(0.05), "stop overtook a send already in progress"
            )
        finally:
            communicator._test_send_release.set()
            sender.join(1)
            stopper.join(1)

        self.assertEqual(send_result, [True])
        self.assertTrue(stop_done.is_set())

    def test_invalid_packet_does_not_kill_serial_worker(self):
        class SerialPort:
            def __init__(self):
                self.closed = False

            def write(self, data):
                raise AssertionError("Invalid packet must not reach serial.write")

            def read(self, size):
                return b""

            def cancel_read(self):
                return None

            def cancel_write(self):
                return None

            def close(self):
                self.closed = True

        class InvalidPacket:
            def __init__(self):
                self.build_called = threading.Event()

            def build(self):
                self.build_called.set()
                return [256]

        serial_port = SerialPort()
        packet = InvalidPacket()
        serial_class = _load_serial_class(serial_port)
        communicator = serial_class("/dev/test")
        communicator.logger = Mock()
        communicator.transmit.put(packet)
        communicator.start()
        self.assertTrue(packet.build_called.wait(1))

        self.assertTrue(communicator.is_alive())
        communicator.stop()
        communicator.join(1)

        self.assertFalse(communicator.is_alive())
        self.assertTrue(serial_port.closed)
        communicator.logger.exception.assert_any_call(
            "Invalid EnOcean packet; dropping it"
        )

    def test_malformed_inbound_frame_cannot_poison_buffer_forever(self):
        serial_port = Mock()
        serial_port.read.return_value = b""
        serial_class = _load_serial_class(serial_port)
        communicator = serial_class("/dev/test")
        communicator.logger = Mock()
        communicator._buffer = bytearray([0x55, 0x01])

        def fail_once():
            communicator.stop()
            raise IndexError("truncated packet")

        communicator.parse = fail_once
        communicator.run()

        self.assertEqual(communicator._buffer, bytearray([0x01]))
        communicator.logger.exception.assert_called_with(
            "Malformed EnOcean frame; discarding one byte to resynchronize"
        )

    def test_unexpected_parser_failure_cannot_poison_buffer_forever(self):
        serial_port = Mock()
        serial_port.read.return_value = b""
        serial_class = _load_serial_class(serial_port)
        communicator = serial_class("/dev/test")
        communicator.logger = Mock()
        communicator._buffer = bytearray([0x55, 0x01])

        def fail_once():
            communicator.stop()
            raise RuntimeError("unexpected decoder failure")

        communicator.parse = fail_once
        communicator.run()

        self.assertEqual(communicator._buffer, bytearray([0x01]))
        communicator.logger.exception.assert_called_with(
            "Unexpected exception while parsing; resynchronizing"
        )


class ReleaseHardeningTests(unittest.TestCase):
    def test_english_runtime_translation_matches_source_strings(self):
        self.assertEqual(
            json.loads(EN_TRANSLATION_PATH.read_text()),
            json.loads(STRINGS_PATH.read_text()),
        )

    def test_runtime_dependencies_and_single_entry_are_declared(self):
        manifest = json.loads(MANIFEST_PATH.read_text())

        self.assertEqual(
            manifest["requirements"],
            ["beautifulsoup4==4.13.3", "lxml==6.1.1", "pyserial==3.5"],
        )
        self.assertIs(manifest["single_config_entry"], True)

    def test_unsafe_raw_packet_service_is_not_registered(self):
        self.assertNotIn("send_packet", INIT_PATH.read_text())
        self.assertNotIn("send_packet:", SERVICES_PATH.read_text())
        self.assertNotIn('"send_packet"', STRINGS_PATH.read_text())

    def test_climate_interval_is_cancelled_with_entity(self):
        tree = ast.parse(CLIMATE_PATH.read_text())
        climate_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "EnOceanClimate"
        )
        create_timer = next(
            node
            for node in climate_class.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_async_create_timer"
        )
        remove_callbacks = [
            node
            for node in ast.walk(create_timer)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "async_on_remove"
        ]

        self.assertEqual(len(remove_callbacks), 1)
        self.assertNotIn("random.seed(", CLIMATE_PATH.read_text())

    def test_hacs_metadata_has_only_supported_keys(self):
        self.assertEqual(
            set(json.loads(HACS_PATH.read_text())), {"name", "render_readme"}
        )

    def test_hacs_brand_icon_is_valid_square_png(self):
        """Ship the local brand icon required by current HACS."""
        payload = BRAND_ICON_PATH.read_bytes()
        self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", payload[16:24])
        self.assertEqual((width, height), (256, 256))

    def test_vendored_mit_notice_is_retained(self):
        license_text = VENDORED_LICENSE_PATH.read_text()

        self.assertIn("The MIT License", license_text)
        self.assertIn("Copyright (c) 2014-2016 Kimmo Huoman", license_text)

    def test_runtime_logs_do_not_dump_packet_or_serial_buffer_contents(self):
        runtime_sources = "\n".join(
            path.read_text()
            for path in (
                DONGLE_PATH,
                ROOT
                / "custom_components/enocean_custom/enocean_library/communicators/communicator.py",
                ROOT
                / "custom_components/enocean_custom/enocean_library/protocol/packet.py",
            )
        )

        self.assertNotIn("debug(packet)", runtime_sources)
        self.assertNotIn('"Received radio packet: %s", packet', runtime_sources)
        self.assertNotIn('"parse_msg called with buf=%s", buf', runtime_sources)
        self.assertNotIn('"Message incomplete, buf: %s", buf', runtime_sources)


if __name__ == "__main__":
    unittest.main()

"""Regression tests for the EnOcean serial lifecycle using only stdlib."""

import ast
import asyncio
import json
import queue
import threading
import unittest
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
HACS_PATH = ROOT / "hacs.json"
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
        "SerialCommunicator": Mock(),
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
        if isinstance(node, ast.FunctionDef) and node.name in {
            "__init__",
            "run",
            "close",
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
        def __init__(self, callback=None):
            super().__init__()
            self._stop_flag = threading.Event()
            self.transmit = queue.Queue()
            self._buffer = bytearray()

        def _get_from_send_queue(self):
            try:
                return self.transmit.get_nowait()
            except queue.Empty:
                return None

        def stop(self):
            self._stop_flag.set()

        def parse(self):
            return None

    serial = SimpleNamespace(
        Serial=lambda *args, **kwargs: serial_port or Mock(),
        SerialException=OSError,
    )
    namespace = {"Communicator": Communicator, "serial": serial, "time": Mock()}
    exec(compile(isolated, str(SERIAL_PATH), "exec"), namespace)  # noqa: S102
    return namespace["SerialCommunicator"]


class DongleLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_unload_disconnects_and_stops_serial_thread(self):
        namespace = _load_dongle_members("async_unload")
        dongle = namespace["EnOceanDongle"]()
        disconnect = Mock()
        dongle.dispatcher_disconnect_handle = disconnect
        dongle._communicator = Mock()
        dongle._communicator.is_alive.return_value = False
        dongle.hass = Mock()
        dongle.hass.async_add_executor_job.side_effect = (
            lambda function, *args: asyncio.to_thread(function, *args)
        )

        result = await dongle.async_unload()

        self.assertTrue(result)
        disconnect.assert_called_once_with()
        self.assertIsNone(dongle.dispatcher_disconnect_handle)
        dongle._communicator.stop.assert_called_once_with()
        dongle._communicator.join.assert_called_once_with(1)
        namespace["_LOGGER"].warning.assert_not_called()

    async def test_unload_refuses_reload_if_thread_stays_alive(self):
        namespace = _load_dongle_members("async_unload")
        dongle = namespace["EnOceanDongle"]()
        disconnect = Mock()
        dongle.dispatcher_disconnect_handle = disconnect
        dongle._communicator = Mock()
        dongle._communicator.is_alive.return_value = True
        dongle.hass = Mock()
        dongle.hass.async_add_executor_job.side_effect = (
            lambda function, *args: asyncio.to_thread(function, *args)
        )

        result = await dongle.async_unload()

        self.assertFalse(result)
        disconnect.assert_not_called()
        namespace["_LOGGER"].warning.assert_called_once()

    def test_validate_path_closes_probe_descriptor(self):
        namespace = _load_dongle_members("validate_path")
        communicator = namespace["SerialCommunicator"].return_value

        self.assertTrue(namespace["validate_path"]("/dev/test"))

        communicator.close.assert_called_once_with()

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

    def test_stop_interrupts_a_blocked_serial_write(self):
        class BlockingSerial:
            def __init__(self):
                self.write_started = threading.Event()
                self.write_cancelled = threading.Event()
                self.cancel_read_calls = 0
                self.cancel_write_calls = 0
                self.closed = False

            def write(self, data):
                self.write_started.set()
                self.write_cancelled.wait(5)
                return len(data)

            def read(self, size):
                return b""

            def cancel_read(self):
                self.cancel_read_calls += 1

            def cancel_write(self):
                self.cancel_write_calls += 1
                self.write_cancelled.set()

            def close(self):
                self.closed = True

        class Packet:
            def build(self):
                return [0x01]

        serial_port = BlockingSerial()
        serial_class = _load_serial_class(serial_port)
        communicator = serial_class("/dev/test")
        communicator.logger = Mock()
        communicator.transmit.put(Packet())
        communicator.start()
        self.assertTrue(serial_port.write_started.wait(1))

        try:
            communicator.stop()
            communicator.join(1)
            self.assertFalse(communicator.is_alive())
            self.assertEqual(serial_port.cancel_read_calls, 1)
            self.assertEqual(serial_port.cancel_write_calls, 1)
            self.assertTrue(serial_port.closed)
        finally:
            serial_port.write_cancelled.set()
            communicator.join(1)

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


class ReleaseHardeningTests(unittest.TestCase):
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

    def test_vendored_mit_notice_is_retained(self):
        license_text = VENDORED_LICENSE_PATH.read_text()

        self.assertIn("The MIT License", license_text)
        self.assertIn("Copyright (c) 2014-2016 Kimmo Huoman", license_text)


if __name__ == "__main__":
    unittest.main()

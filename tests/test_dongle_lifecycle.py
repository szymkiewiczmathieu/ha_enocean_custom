"""Regression tests for the EnOcean serial lifecycle using only stdlib."""

import ast
import asyncio
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).parents[1]
DONGLE_PATH = ROOT / "custom_components/enocean_custom/dongle.py"
SERIAL_PATH = (
    ROOT
    / "custom_components/enocean_custom/enocean_library/communicators/serialcommunicator.py"
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


def _load_serial_class():
    """Load SerialCommunicator.run/close without pyserial or its base class."""
    tree = ast.parse(SERIAL_PATH.read_text())
    source_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SerialCommunicator"
    )
    members: list[ast.stmt] = []
    for node in source_class.body:
        if isinstance(node, ast.FunctionDef) and node.name in {"run", "close"}:
            members.append(node)
    isolated = ast.Module(
        body=[
            ast.ClassDef(
                name="SerialCommunicator",
                bases=[],
                keywords=[],
                body=members,
                decorator_list=[],
            )
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(isolated)
    serial = Mock()
    serial.SerialException = OSError
    namespace = {"serial": serial, "time": Mock()}
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


if __name__ == "__main__":
    unittest.main()

"""Regression tests for safe EnOcean config-entry unload."""

import ast
from pathlib import Path
from unittest.mock import Mock


def load_unload_method():
    """Load only EnOceanDongle.unload without importing Home Assistant."""
    path = Path(__file__).parents[1] / "custom_components/enocean_custom/dongle.py"
    tree = ast.parse(path.read_text())
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "EnOceanDongle")
    method = next(node for node in klass.body if isinstance(node, ast.FunctionDef) and node.name == "unload")
    isolated = ast.Module(
        body=[ast.ClassDef(name="EnOceanDongle", bases=[], keywords=[], body=[method], decorator_list=[])],
        type_ignores=[],
    )
    ast.fix_missing_locations(isolated)
    namespace = {"_LOGGER": Mock()}
    exec(compile(isolated, str(path), "exec"), namespace)
    return namespace["EnOceanDongle"], namespace["_LOGGER"]


def test_unload_disconnects_and_stops_serial_thread():
    cls, logger = load_unload_method()
    dongle = cls()
    disconnect = Mock()
    dongle.dispatcher_disconnect_handle = disconnect
    dongle._communicator = Mock()
    dongle._communicator.is_alive.return_value = False

    dongle.unload()

    disconnect.assert_called_once_with()
    assert dongle.dispatcher_disconnect_handle is None
    dongle._communicator.stop.assert_called_once_with()
    dongle._communicator.join.assert_called_once_with(timeout=1)
    logger.warning.assert_not_called()


def test_unload_warns_if_serial_thread_does_not_stop():
    cls, logger = load_unload_method()
    dongle = cls()
    dongle.dispatcher_disconnect_handle = None
    dongle._communicator = Mock()
    dongle._communicator.is_alive.return_value = True

    dongle.unload()

    dongle._communicator.stop.assert_called_once_with()
    dongle._communicator.join.assert_called_once_with(timeout=1)
    logger.warning.assert_called_once()
"""Dependency-free D2-01-0A wire-contract tests."""

import importlib.util
import pathlib
import sys
import types
import unittest

_ROOT = (
    pathlib.Path(__file__).parents[1]
    / "custom_components/enocean_custom/enocean_library/protocol"
)
_pkg = "_pure_protocol"
sys.modules.setdefault(_pkg, types.ModuleType(_pkg))
_spec = importlib.util.spec_from_file_location(
    f"{_pkg}.constants", _ROOT / "constants.py"
)
_constants = importlib.util.module_from_spec(_spec)
sys.modules[f"{_pkg}.constants"] = _constants
_spec.loader.exec_module(_constants)
_spec = importlib.util.spec_from_file_location(f"{_pkg}.d2", _ROOT / "d2.py")
_d2 = importlib.util.module_from_spec(_spec)
sys.modules[f"{_pkg}.d2"] = _d2
_spec.loader.exec_module(_d2)
RORG = _constants.RORG
parse_d2_01_actuator_status = _d2.parse_d2_01_actuator_status
is_matching_d2_01_feedback = _d2.is_matching_d2_01_feedback


class D201PureTests(unittest.TestCase):
    def test_status_feedback_decodes_channel_zero_and_output(self):
        packet = [RORG.VLD, 0x84, 0x00, 100, 1, 2, 3, 4, 0]
        status = parse_d2_01_actuator_status(packet)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.channel, 0)
        self.assertEqual(status.output_value, 100)
        self.assertTrue(status.power_failure_detection_enabled)

    def test_ack_or_wrong_command_is_not_actuator_feedback(self):
        # ESP3 ACK is not a radio payload and must never be interpreted as state.
        self.assertIsNone(parse_d2_01_actuator_status([0x02, 0x00]))
        self.assertIsNone(
            parse_d2_01_actuator_status([RORG.VLD, 0x81, 0, 100, 1, 2, 3, 4, 0])
        )

    def test_malformed_runtime_data_is_rejected(self):
        self.assertFalse(is_matching_d2_01_feedback(None, 0))
        self.assertFalse(is_matching_d2_01_feedback([RORG.VLD, "bad"], 0))

    def test_feedback_boundary_requires_exact_channel_and_requested_output(self):
        packet = [RORG.VLD, 0x84, 0x00, 100, 1, 2, 3, 4, 0]
        self.assertTrue(is_matching_d2_01_feedback(packet, 0, output_value=100))
        self.assertFalse(is_matching_d2_01_feedback(packet, 1, output_value=100))
        off = [RORG.VLD, 0x84, 0x00, 0, 1, 2, 3, 4, 0]
        self.assertFalse(is_matching_d2_01_feedback(off, 0, output_value=100))

        self.assertIsNone(
            parse_d2_01_actuator_status([RORG.VLD, 0x84, 0, 127, 1, 2, 3, 4, 0])
        )
        status = parse_d2_01_actuator_status([RORG.VLD, 0x04, 0x9F, 0, 1, 2, 3, 4, 0])
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.channel, 31)


if __name__ == "__main__":
    unittest.main()

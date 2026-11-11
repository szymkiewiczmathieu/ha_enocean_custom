"""A simulated telegram must never make the radio transmit.

``enocean_custom.simulate`` injects a telegram through the real receive chain
and promises that nothing is sent by radio. Valve profiles answer the
telegrams of their actuator from the receive callback itself; a simulated
telegram carrying the valve's sender ID must update the entity like a real
one and stop there.
"""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.components.climate import HVACMode

    from custom_components.enocean_custom import climate
    from custom_components.enocean_custom.simulator import build_simulated_packet

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

VALVE = [0x01, 0x02, 0x03, 0x04]
CONTROLLER = [0xFF, 0x80, 0x00, 0x01]


def _valve(profile: str):
    entity = climate.EnOceanClimate(
        VALVE,
        "Radiator",
        CONTROLLER,
        "sensor.room",
        8.0,
        5,
        0.5,
        21.0,
        4.0,
        timedelta(minutes=17),
        5.0,
        240.0,
        device_type=profile,
    )
    entity.schedule_update_ha_state = mock.Mock()
    entity.sent = []

    def send_command(data, optional, packet_type, response_callback=None, **_):
        entity.sent.append(list(data))
        return True

    entity.send_command = send_command
    entity._attr_hvac_mode = HVACMode.HEAT
    entity._attr_target_temp = 21.0
    entity._attr_native_current_temperature = 19.0
    entity._attr_pi_control_output = 40.0
    return entity


def _real_twin(simulated):
    """The same telegram as received from the air (no ``simulated`` mark)."""
    twin = build_simulated_packet("temperature", VALVE)
    twin.simulated = False
    assert twin.data == simulated.data
    return twin


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class SimulatedTelegramTests(unittest.TestCase):
    def setUp(self):
        # The "temperature" preset is a plain 4BS data telegram: with a
        # valve's sender ID it is exactly what the valve's own status looks like.
        self.simulated = build_simulated_packet("temperature", VALVE)
        self.assertIs(self.simulated.simulated, True)

    def test_polled_valve_profiles_do_not_answer_a_simulated_telegram(self):
        for profile in climate.EXPERIMENTAL_VALVE_PROFILES:
            with self.subTest(profile=profile):
                entity = _valve(profile)
                entity.value_changed(self.simulated)
                self.assertEqual(entity.sent, [], "a simulation reached the radio")
                entity.schedule_update_ha_state.assert_called()

                entity.value_changed(_real_twin(self.simulated))
                self.assertEqual(len(entity.sent), 1, "a real poll is still answered")

    def test_a5_20_04_does_not_answer_a_simulated_telegram(self):
        entity = _valve(climate.PROFILE_A5_20_04)
        entity.value_changed(self.simulated)
        self.assertEqual(entity.sent, [], "a simulation reached the radio")
        self.assertEqual(entity._attr_valve_position, 0)
        entity.schedule_update_ha_state.assert_called()

        entity.value_changed(_real_twin(self.simulated))
        self.assertEqual(len(entity.sent), 1, "a real status is still answered")


if __name__ == "__main__":
    unittest.main()

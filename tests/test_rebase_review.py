"""Regression tests for the v2.0.0 rebase adversarial-review findings."""

from __future__ import annotations

import asyncio
import math
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare CI lifecycle environment has no Home Assistant installed.
    # Imported first: Home Assistant 2026.10 aliases ``voluptuous`` to probatio.
    import homeassistant  # noqa: F401
    import voluptuous as vol

    from custom_components.enocean_custom import climate, light
    from custom_components.enocean_custom.climate import (
        PLATFORM_SCHEMA as CLIMATE_PLATFORM_SCHEMA,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class WakeUpCycleCodeTests(unittest.TestCase):
    """Review P1-01: the WUC code must be the globally nearest legal one."""

    def test_official_table_corners(self):
        cases = {
            10: 0,
            60: 1,
            90: 2,
            120: 3,
            1500: 49,
            3 * 3600: 50,
            6 * 3600: 51,
            42 * 3600: 63,
        }
        for seconds, expected in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(
                    climate.EnOceanClimate._wake_up_cycle_code(
                        timedelta(seconds=seconds)
                    ),
                    expected,
                )

    def test_review_boundaries(self):
        # 61 s is nearer to 60 s (code 1) than to 90 s (code 2); 1501 s and
        # 26 minutes are nearer to 1500 s (code 49) than to 3 h (code 50).
        for seconds, expected in ((61, 1), (1501, 49), (26 * 60, 49)):
            with self.subTest(seconds=seconds):
                self.assertEqual(
                    climate.EnOceanClimate._wake_up_cycle_code(
                        timedelta(seconds=seconds)
                    ),
                    expected,
                )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ClimateYamlValidationTests(unittest.TestCase):
    """Review P1-H1: booleans and non-finite YAML values must be rejected."""

    _BASE: ClassVar[dict] = {
        "platform": "enocean_custom",
        "id": [1, 2, 3, 4],
        "id_switch": [5, 6, 7, 8],
        "device_type": "SRC-D08",
        "sensor_entity_id": "sensor.room",
    }

    def _assert_rejected(self, field: str, value):
        with (
            self.subTest(field=field, value=value),
            self.assertRaises(vol.Invalid),
        ):
            CLIMATE_PLATFORM_SCHEMA({**self._BASE, field: value})

    def test_booleans_rejected_on_numeric_fields(self):
        for field in (
            "temperature_frost_protection",
            "sensor_target_temperature_update_tolerance",
            "target_temperature_reduction_night",
            "pi_control_Kp",
            "pi_control_Tn",
            "command_frequency",
        ):
            self._assert_rejected(field, True)
            self._assert_rejected(field, False)

    def test_non_finite_rejected(self):
        for field in (
            "temperature_frost_protection",
            "target_temperature_base",
            "pi_control_Kp",
        ):
            for value in (math.inf, -math.inf, math.nan):
                self._assert_rejected(field, value)

    def test_command_frequency_rejects_garbage(self):
        for value in (math.inf, -1, "not-a-period", None):
            self._assert_rejected("command_frequency", value)

    def test_valid_config_still_accepted(self):
        config = CLIMATE_PLATFORM_SCHEMA(
            {
                **self._BASE,
                "temperature_frost_protection": 8.0,
                "command_frequency": "00:17:00",
                "pi_control_Kp": 5.0,
            }
        )
        self.assertEqual(config["temperature_frost_protection"], 8.0)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ClimateRuntimeInputValidationTests(unittest.TestCase):
    """Sensor attributes must not inject invalid values into climate control."""

    def _entity(self):
        entity = object.__new__(climate.EnOceanClimate)
        entity._attr_current_temperature = 19.0
        entity._profile = climate.PROFILE_SRC_D08
        entity._sensor_target_temp = None
        entity._sensor_target_temp_range = 5
        entity._target_temp_base = 21.0
        entity._target_temp_reduction_night = 4.0
        return entity

    def test_invalid_panel_attributes_are_rejected_without_target_change(self):
        for setpoint, slide_switch in (
            (True, 1),
            (math.nan, 1),
            (-1, 1),
            (256, 1),
            (128, True),
            (128, 2),
        ):
            with self.subTest(setpoint=setpoint, slide_switch=slide_switch):
                entity = self._entity()
                state = type(
                    "PanelState",
                    (),
                    {
                        "state": "20.0",
                        "attributes": {
                            "SetPoint": setpoint,
                            "SlideSwitch": slide_switch,
                        },
                    },
                )()
                self.assertFalse(asyncio.run(entity._async_get_sensor_update(state)))
                self.assertIsNone(entity._sensor_target_temp)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class LightPayloadRoundingTests(unittest.TestCase):
    """Review P2-01: brightness 254 must keep the v1.5.0 100 % byte."""

    def _dim_byte(self, brightness: int) -> int:
        entity = light.EnOceanLight(
            [0xFF, 0x9C, 0xD4, 0x38], [0x01, 0x02, 0x03, 0x04], "dimmer"
        )
        sent = []

        def _spy(*args, **kwargs):
            sent.append(args[0])
            return True

        entity.send_command = _spy
        entity.turn_on(brightness=brightness)
        return sent[0][2]

    def test_brightness_254_is_100_percent(self):
        self.assertEqual(self._dim_byte(254), 100)

    def test_brightness_extremes(self):
        self.assertEqual(self._dim_byte(255), 100)
        self.assertEqual(self._dim_byte(1), 1)
        self.assertEqual(self._dim_byte(128), 50)


if __name__ == "__main__":
    unittest.main()

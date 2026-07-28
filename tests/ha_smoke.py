"""Smoke-test the integration against the pinned Home Assistant runtime."""

from __future__ import annotations

import sys
from pathlib import Path

import voluptuous as vol
from homeassistant.const import CONF_ID, CONF_NAME, CONF_PLATFORM

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.enocean_custom import climate, dongle  # noqa: F401


def main() -> None:
    """Import the integration and verify dangerous climate inputs are rejected."""
    base = {
        CONF_PLATFORM: "enocean_custom",
        CONF_ID: [0x0F, 0x53, 0xD6, 0x83],
        CONF_NAME: "test",
        climate.CONF_SENDER_ID_SWITCH: [0x12, 0x34, 0x56, 0x78],
        climate.CONF_DEVICE_TYPE: "SRC-D08",
        climate.CONF_SENSOR_ENTITY_ID: "sensor.test_temperature",
    }
    validated = climate.PLATFORM_SCHEMA(base)
    if validated[CONF_ID] != base[CONF_ID]:
        raise AssertionError("Valid EnOcean ID was changed by schema validation")

    invalid_cases = [
        {**base, CONF_ID: [1, 2, 3]},
        {**base, climate.CONF_PI_CONTROL_TN: 0},
        {**base, climate.CONF_COMMAND_FREQUENCY: "00:00:00"},
    ]
    for config in invalid_cases:
        try:
            climate.PLATFORM_SCHEMA(config)
        except vol.Invalid:
            continue
        raise AssertionError(f"Unsafe climate config accepted: {config}")

    print("HA_2026_7_3_SMOKE_OK invalid_cases_rejected=3")


if __name__ == "__main__":
    main()

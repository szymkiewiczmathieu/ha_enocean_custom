"""Smoke-test the integration against the pinned Home Assistant runtime."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import voluptuous as vol
from homeassistant.components.climate.const import HVACMode
from homeassistant.const import CONF_ID, CONF_NAME, CONF_PLATFORM

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.enocean_custom import climate, dongle  # noqa: F401


async def _assert_off_without_sensor_sends_switch_off() -> None:
    """OFF must always reach the actor, even before a sensor value exists."""
    entity = object.__new__(climate.EnOceanClimate)
    entity.dev_name = "test"
    entity._attr_hvac_mode = HVACMode.OFF
    entity._attr_preset_mode = None
    entity._attr_target_temp = None
    entity._attr_current_temperature = None
    packets: list[list[int]] = []

    def record_packet(data: list[int]) -> None:
        packets.append(data)

    entity.sendPacket = record_packet  # type: ignore[method-assign]

    await entity._async_control_heating()

    if packets != [[0x00]]:
        raise AssertionError(f"OFF without sensor sent unexpected packets: {packets}")


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
    finite_fields = (
        climate.CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION,
        climate.CONF_SENSOR_TARGET_TEMP_TOLERANCE,
        climate.CONF_TARGET_TEMP_BASE,
        climate.CONF_TARGET_TEMP_NIGHT_REDUCTION,
        climate.CONF_PI_CONTROL_KP,
        climate.CONF_PI_CONTROL_TN,
    )
    invalid_cases.extend(
        {**base, field: value}
        for field in finite_fields
        for value in ("inf", "-inf", "nan")
    )
    out_of_range_values = {
        climate.CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION: 21,
        climate.CONF_SENSOR_TARGET_TEMP_RANGE: 21,
        climate.CONF_SENSOR_TARGET_TEMP_TOLERANCE: 11,
        climate.CONF_TARGET_TEMP_BASE: 36,
        climate.CONF_TARGET_TEMP_NIGHT_REDUCTION: 21,
        climate.CONF_PI_CONTROL_KP: 101,
        climate.CONF_PI_CONTROL_TN: 1441,
    }
    invalid_cases.extend(
        {**base, field: value} for field, value in out_of_range_values.items()
    )
    for config in invalid_cases:
        try:
            climate.PLATFORM_SCHEMA(config)
        except vol.Invalid:
            continue
        raise AssertionError(f"Unsafe climate config accepted: {config}")

    asyncio.run(_assert_off_without_sensor_sends_switch_off())
    print(
        "HA_2026_7_3_SMOKE_OK "
        f"invalid_cases_rejected={len(invalid_cases)} off_without_sensor=sent"
    )


if __name__ == "__main__":
    main()

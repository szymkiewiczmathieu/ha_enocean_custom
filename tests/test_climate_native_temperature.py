"""Climate temperature members follow the ``native_`` API of Home Assistant 2026.11.

Home Assistant 2026.11 renamed the temperature members a climate integration
provides (``temperature_unit``, ``current_temperature``, ``target_temperature``
and the high/low pair) with a ``native_`` prefix; the unprefixed names log a
deprecation warning and stop working in 2027.11. The oldest supported core
(2026.7) only reads the unprefixed names, so the entity has to serve both
generations without ever touching a deprecated member on a core that knows the
new ones.

The first test class restates the selection rule the 2026.11 base class applies
when a subclass is created (``migrate_deprecated_entity_members``): for every
renamed member, the most derived class providing either spelling must provide
the native one. It is a pure class inspection, so it holds on every core.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.components.climate import ClimateEntity, HVACMode
    from homeassistant.const import UnitOfTemperature
    from homeassistant.util.unit_system import METRIC_SYSTEM

    from custom_components.enocean_custom import climate
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RORG,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import Packet

    HA_AVAILABLE = True
    # True from Home Assistant 2026.11, whose base class knows the native names.
    NATIVE_API = hasattr(ClimateEntity, "native_temperature_unit")
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False
    NATIVE_API = False

SENDER = [0x05, 0x8F, 0x22, 0x33]
RENAMED_MEMBERS = (
    "temperature_unit",
    "current_temperature",
    "target_temperature",
    "target_temperature_high",
    "target_temperature_low",
)


def _deprecated_members_provided(cls: type) -> list[str]:
    """Return the deprecated names Home Assistant 2026.11 would warn about."""
    below_base = cls.__mro__[: cls.__mro__.index(ClimateEntity)]
    offenders = []
    for old in RENAMED_MEMBERS:
        new = f"native_{old}"
        for name, natives in (
            (old, {new, f"_attr_{new}"}),
            (f"_attr_{old}", {f"_attr_{new}"}),
        ):
            provider = next(
                (
                    klass
                    for klass in below_base
                    if name in vars(klass) or natives & vars(klass).keys()
                ),
                None,
            )
            if provider is not None and not natives & vars(provider).keys():
                offenders.append(name)
    return offenders


class _Recorder:
    """Data descriptor standing in for a deprecated ``_attr_`` shorthand."""

    def __init__(self, accesses: list[tuple[str, str]]) -> None:
        self._accesses = accesses
        self._name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def __get__(self, instance: object, owner: type | None = None) -> object:
        if instance is None:
            return self
        self._accesses.append(("read", self._name))
        return None

    def __set__(self, instance: object, value: object) -> None:
        self._accesses.append(("write", self._name))


def _build(entity_class: type, device_type: str) -> object:
    """Build a climate entity of ``entity_class``, bypassing the YAML schema."""
    entity = entity_class(
        SENDER,
        "Valve",
        [0x01, 0x02, 0x03, 0x04],
        "sensor.room",
        8.0,
        5,
        0.5,
        21.0,
        4.0,
        timedelta(minutes=17),
        5.0,
        240.0,
        device_type=device_type,
    )
    entity.async_write_ha_state = lambda: None
    entity.schedule_update_ha_state = lambda: None
    entity.hass = SimpleNamespace(config=SimpleNamespace(units=METRIC_SYSTEM))
    return entity


def _a5_20_04_status(db3: int, db2: int, db1: int, db0: int) -> object:
    """Build and parse a real ESP3 4BS frame carrying DB3..DB0."""
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.BS4, db3, db2, db1, db0, *SENDER, 0x00],
        optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
    ).build()
    return Packet.parse_msg(bytearray(wire))[2]


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class NativeMemberContractTests(unittest.TestCase):
    def test_no_deprecated_member_is_provided_without_its_native_name(self):
        self.assertEqual(_deprecated_members_provided(climate.EnOceanClimate), [])

    def test_native_unit_is_celsius(self):
        entity = _build(climate.EnOceanClimate, climate.PROFILE_A5_20_04)
        self.assertEqual(entity.native_temperature_unit, UnitOfTemperature.CELSIUS)
        self.assertEqual(entity._attr_native_temperature_unit, "°C")

    def _exercise(self, entity) -> None:
        """Run every integration path that reads or writes the room temperature.

        Valve feedback (room temperature 10 + 115*20/255), a linked-sensor
        update, a control cycle and a transaction snapshot/rollback. What the
        base class publishes from them is covered by the behaviour tests.
        Two frames leave: the answer to the valve feedback inside its receive
        window, then the control cycle's own command.
        """
        entity._attr_hvac_mode = HVACMode.HEAT
        entity._attr_target_temp = 21.0
        sent: list[list[int]] = []
        entity._send_a5_20_04 = lambda payload, response_callback=None: (
            sent.append(payload) or True
        )
        with patch.object(climate, "ir"):  # the valve-failure Repair is not under test
            entity.value_changed(_a5_20_04_status(40, 0, 115, 0x08))
        state = SimpleNamespace(state="19.5", attributes={})
        self.assertTrue(asyncio.run(entity._async_get_sensor_update(state)))
        asyncio.run(entity._async_control_heating(response_callback=lambda _ok: None))
        entity._restore_control_state(entity._control_state())
        self.assertEqual(len(sent), 2)

    @unittest.skipIf(NATIVE_API, "this core reports deprecated members itself")
    def test_instances_never_touch_a_deprecated_attr_shorthand(self):
        accesses: list[tuple[str, str]] = []
        # The unit shorthand is left out: a core without the native API reads
        # it legitimately, and the integration never does.
        probe = type(
            "ProbeClimate",
            (climate.EnOceanClimate,),
            {f"_attr_{name}": _Recorder(accesses) for name in RENAMED_MEMBERS[1:]},
        )

        self._exercise(_build(probe, climate.PROFILE_A5_20_04))

        self.assertEqual(accesses, [])

    @unittest.skipUnless(NATIVE_API, "needs the deprecation reporting of 2026.11")
    def test_the_core_reports_no_deprecated_member_usage(self):
        # The core reports each deprecated read/write once per entity class
        # and call site, so a fresh subclass makes this test order-independent.
        fresh = type("FreshClimate", (climate.EnOceanClimate,), {})
        with self.assertLogs(level="WARNING") as captured:
            logging.getLogger(__name__).warning("sentinel")
            entity = _build(fresh, climate.PROFILE_A5_20_04)
            self._exercise(entity)
            _ = entity.state_attributes
            _ = entity.capability_attributes

        deprecated = [line for line in captured.output if "deprecated" in line]
        self.assertEqual(deprecated, [])


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class TemperatureBehaviourTests(unittest.TestCase):
    """The values Home Assistant publishes are the same on every core."""

    def test_room_temperature_from_valve_feedback_reaches_the_state(self):
        entity = _build(climate.EnOceanClimate, climate.PROFILE_A5_20_04)
        entity._attr_hvac_mode = HVACMode.HEAT
        entity._attr_target_temp = 21.0

        with patch.object(climate, "ir"):
            entity.value_changed(_a5_20_04_status(40, 0, 115, 0x08))

        self.assertEqual(entity.native_current_temperature, 19.0)
        attributes = entity.state_attributes
        self.assertEqual(attributes["current_temperature"], 19.0)
        self.assertEqual(attributes["temperature"], 21.0)

    def test_linked_sensor_temperature_reaches_the_state(self):
        entity = _build(climate.EnOceanClimate, climate.PROFILE_A5_20_04)
        entity._attr_hvac_mode = HVACMode.HEAT
        entity._attr_target_temp = 20.5
        state = SimpleNamespace(state="18.5", attributes={})

        self.assertTrue(asyncio.run(entity._async_get_sensor_update(state)))

        self.assertEqual(entity.state_attributes["current_temperature"], 18.5)
        self.assertEqual(entity.native_target_temperature, 20.5)

    def test_frost_protection_is_the_target_while_off(self):
        entity = _build(climate.EnOceanClimate, climate.PROFILE_A5_20_04)
        entity._attr_hvac_mode = HVACMode.OFF
        entity._attr_target_temp = 21.0

        self.assertEqual(entity.native_target_temperature, 8.0)
        self.assertEqual(entity.state_attributes["temperature"], 8.0)

    def test_room_temperature_is_part_of_the_transaction_snapshot(self):
        entity = _build(climate.EnOceanClimate, climate.PROFILE_A5_20_04)
        entity._attr_native_current_temperature = 19.0
        snapshot = entity._control_state()

        entity._attr_native_current_temperature = 25.0
        entity._restore_control_state(snapshot)

        self.assertEqual(entity._attr_native_current_temperature, 19.0)
        self.assertEqual(entity.native_current_temperature, 19.0)


if __name__ == "__main__":
    unittest.main()

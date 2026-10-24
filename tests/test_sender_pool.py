"""Sender ID pool: Base ID + offset 1..127, never CO_WR_IDBASE.

Covers the pure arithmetic (``sender_pool.py``), the row scan that finds
what is already in use (``schema.used_sender_ids``), the auto-fill and
collision guard added to the "add device" step for lights and climate
actors, and the new "sender_id" diagnostic attribute on light, switch and
climate entities.
"""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from custom_components.enocean_custom import climate, light, options_flow, switch
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.schema import CONF_UI_DEVICES, used_sender_ids
    from custom_components.enocean_custom.sender_pool import (
        MAX_OFFSET,
        MIN_OFFSET,
        allocate_sender_id,
        offset_of,
        offset_sender_id,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

BASE_ID = [0x05, 0x00, 0x00, 0x00]


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ArithmeticTests(unittest.TestCase):
    def test_offsets_are_added_to_the_base_id(self):
        self.assertEqual(offset_sender_id(BASE_ID, 1), [0x05, 0x00, 0x00, 0x01])
        self.assertEqual(offset_sender_id(BASE_ID, 127), [0x05, 0x00, 0x00, 0x7F])

    def test_offset_zero_and_above_127_are_rejected(self):
        self.assertIsNone(offset_sender_id(BASE_ID, 0))
        self.assertIsNone(offset_sender_id(BASE_ID, 128))
        self.assertIsNone(offset_sender_id(BASE_ID, -1))
        self.assertIsNone(offset_sender_id(BASE_ID, True))  # bool is not an offset

    def test_invalid_base_id_is_rejected(self):
        self.assertIsNone(offset_sender_id(None, 1))
        self.assertIsNone(offset_sender_id([0x05, 0x00, 0x00], 1))
        self.assertIsNone(offset_sender_id([0x05, 0x00, 0x00, 256], 1))
        self.assertIsNone(offset_sender_id([0x05, 0x00, 0x00, True], 1))

    def test_carry_across_bytes(self):
        self.assertEqual(
            offset_sender_id([0x05, 0x00, 0x00, 0xFF], 1), [0x05, 0x00, 0x01, 0x00]
        )

    def test_offset_of_is_the_inverse_of_offset_sender_id(self):
        for offset in (MIN_OFFSET, 42, MAX_OFFSET):
            sender_id = offset_sender_id(BASE_ID, offset)
            self.assertEqual(offset_of(BASE_ID, sender_id), offset)

    def test_offset_of_rejects_ids_outside_the_pool(self):
        self.assertIsNone(offset_of(BASE_ID, BASE_ID))  # offset 0: the dongle itself
        self.assertIsNone(offset_of(BASE_ID, [0x05, 0x00, 0x00, 0x80]))  # offset 128
        self.assertIsNone(offset_of(BASE_ID, [0x01, 0x02, 0x03, 0x04]))  # unrelated
        self.assertIsNone(offset_of(None, BASE_ID))

    def test_allocate_picks_the_lowest_free_offset(self):
        used = [offset_sender_id(BASE_ID, 1), offset_sender_id(BASE_ID, 2)]
        self.assertEqual(
            allocate_sender_id(BASE_ID, used), offset_sender_id(BASE_ID, 3)
        )

    def test_allocate_ignores_unrelated_or_malformed_used_entries(self):
        used = [[9, 9, 9, 9], None, "not-an-id", [1, 2, 3]]
        self.assertEqual(
            allocate_sender_id(BASE_ID, used), offset_sender_id(BASE_ID, 1)
        )

    def test_allocate_returns_none_without_a_resolved_base_id(self):
        self.assertIsNone(allocate_sender_id(None, []))

    def test_allocate_returns_none_when_the_pool_is_exhausted(self):
        used = [
            offset_sender_id(BASE_ID, offset)
            for offset in range(MIN_OFFSET, MAX_OFFSET + 1)
        ]
        self.assertIsNone(allocate_sender_id(BASE_ID, used))

    def test_allocate_rejects_a_non_iterable_used_argument(self):
        self.assertIsNone(allocate_sender_id(BASE_ID, None))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class UsedSenderIdsTests(unittest.TestCase):
    def test_light_counts_its_sender_id_not_its_own_id(self):
        rows = [
            {
                "id": [9, 9, 9, 9],
                "platform": "light",
                "name": "Dimmer",
                "sender_id": [5, 0, 0, 1],
                "channel": 0,
            }
        ]
        self.assertEqual(used_sender_ids(rows), [[5, 0, 0, 1]])

    def test_climate_counts_id_switch(self):
        rows = [
            {
                "id": [1, 2, 3, 4],
                "platform": "climate",
                "name": "Valve",
                "id_switch": [5, 0, 0, 2],
                "device_type": "SRC-D08",
                "sensor_entity_id": "sensor.room",
            }
        ]
        self.assertEqual(used_sender_ids(rows), [[5, 0, 0, 2]])

    def test_rps_switch_counts_its_own_id_default_switch_does_not(self):
        rows = [
            {
                "id": [5, 0, 0, 3],
                "platform": "switch",
                "name": "Rocker-emulated",
                "channel": 0,
                "switch_type": "RPS",
            },
            {
                "id": [9, 9, 9, 9],
                "platform": "switch",
                "name": "D2 relay",
                "channel": 0,
                "switch_type": "default",
            },
        ]
        self.assertEqual(used_sender_ids(rows), [[5, 0, 0, 3]])

    def test_binary_sensor_and_sensor_rows_never_contribute(self):
        rows = [
            {"id": [1, 1, 1, 1], "platform": "binary_sensor", "name": "Rocker"},
            {
                "id": [2, 2, 2, 2],
                "platform": "sensor",
                "name": "Temp",
                "device_class": "temperature",
            },
        ]
        self.assertEqual(used_sender_ids(rows), [])

    def test_malformed_rows_are_skipped_not_crashed_on(self):
        self.assertEqual(used_sender_ids("not-a-list"), [])
        self.assertEqual(used_sender_ids([{"platform": "light"}]), [])


class _FakeGateway(SimpleNamespace):
    """Minimal stand-in exposing exactly what get_gateway_base_id reads."""


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DeviceDetailsFlowTests(unittest.IsolatedAsyncioTestCase):
    """Auto-fill and collision guard in the generic 'add device' step."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.hass.config_entries = ConfigEntries(self.hass, {})

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _flow(self, rows, *, base_id=BASE_ID):
        entry = ConfigEntry(
            data={"device": "/dev/test"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=2,
            options={CONF_UI_DEVICES: list(rows)},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        if base_id is not None:
            entry.runtime_data = _FakeGateway(base_id=base_id)
        self.hass.config_entries._entries[entry.entry_id] = entry
        flow = options_flow.EnOceanOptionsFlow()
        flow.hass, flow.handler = self.hass, entry.entry_id
        flow._captured_id = [9, 9, 9, 9]
        flow._pending_name = "New device"
        return flow

    def _default(self, schema, key):
        (marker,) = [k for k in schema.schema if str(k) == key]
        return marker.default()

    async def test_light_default_is_the_lowest_free_offset(self):
        flow = self._flow(
            [
                {
                    "id": [8, 8, 8, 8],
                    "platform": "light",
                    "name": "Existing",
                    "sender_id": [5, 0, 0, 1],
                    "channel": 0,
                }
            ]
        )
        flow._pending_platform = "light"
        default = self._default(flow._device_details_schema("light"), "sender_id")
        self.assertEqual(default, "05:00:00:02")

    async def test_climate_default_is_the_lowest_free_offset(self):
        flow = self._flow([])
        flow._pending_platform = "climate"
        default = self._default(flow._device_details_schema("climate"), "id_switch")
        self.assertEqual(default, "05:00:00:01")

    async def test_default_is_empty_without_a_resolved_base_id(self):
        flow = self._flow([], base_id=None)
        flow._pending_platform = "light"
        default = self._default(flow._device_details_schema("light"), "sender_id")
        self.assertEqual(default, "")

    async def test_creating_a_light_with_a_colliding_sender_id_is_refused(self):
        flow = self._flow(
            [
                {
                    "id": [8, 8, 8, 8],
                    "platform": "light",
                    "name": "Existing",
                    "sender_id": [5, 0, 0, 1],
                    "channel": 0,
                }
            ]
        )
        flow._pending_platform = "light"
        refused = await flow.async_step_device_details(
            {"sender_id": "05:00:00:01", "channel": 0}
        )
        self.assertEqual(refused["type"], FlowResultType.FORM)
        self.assertEqual(refused["errors"], {"sender_id": "sender_id_collision"})

    async def test_creating_a_climate_actor_with_a_colliding_id_switch_is_refused(self):
        flow = self._flow(
            [
                {
                    "id": [1, 1, 1, 1],
                    "platform": "climate",
                    "name": "Existing",
                    "id_switch": [5, 0, 0, 1],
                    "device_type": "SRC-D08",
                    "sensor_entity_id": "sensor.room",
                }
            ]
        )
        flow._pending_platform = "climate"
        # The collision guard runs before any other climate field is parsed,
        # so only id_switch needs to be supplied to exercise it.
        refused = await flow.async_step_device_details(
            {
                "id_switch": "05:00:00:01",
                "device_type": "SRC-D08",
                "sensor_entity_id": "sensor.room2",
            }
        )
        self.assertEqual(refused["type"], FlowResultType.FORM)
        self.assertEqual(refused["errors"], {"id_switch": "sender_id_collision"})

    async def test_the_suggested_light_sender_id_is_accepted_and_free_next_time(self):
        flow = self._flow([])
        flow._pending_platform = "light"
        suggested = self._default(flow._device_details_schema("light"), "sender_id")
        result = await flow.async_step_device_details(
            {"sender_id": suggested, "channel": 0}
        )
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        rows = result["data"][CONF_UI_DEVICES]
        self.assertEqual(used_sender_ids(rows), [[5, 0, 0, 1]])


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DiagnosticAttributeTests(unittest.TestCase):
    """The new 'sender used' attribute, independent of the allocator above."""

    def test_light_reports_its_configured_sender_id(self):
        entity = light.EnOceanLight([5, 0, 0, 1], [], "Dimmer")
        self.assertEqual(entity.extra_state_attributes["sender_id"], "05:00:00:01")

    def test_light_without_a_sender_id_reports_none(self):
        entity = light.EnOceanLight(None, [1, 2, 3, 4], "Dimmer")
        self.assertIsNone(entity.extra_state_attributes["sender_id"])

    def test_rps_switch_reports_its_own_id_default_switch_does_not(self):
        rps = switch.EnOceanSwitch([5, 0, 0, 3], "Rocker-emulated", 0, "RPS")
        self.assertEqual(rps.extra_state_attributes["sender_id"], "05:00:00:03")
        relay = switch.EnOceanSwitch([9, 9, 9, 9], "D2 relay", 0, "default")
        self.assertNotIn("sender_id", relay.extra_state_attributes)

    def test_climate_reports_its_control_sender_id(self):
        entity = climate.EnOceanClimate(
            [1, 2, 3, 4],
            "Valve",
            [5, 0, 0, 1],
            "sensor.room",
            8.0,
            5,
            0.5,
            21.0,
            4.0,
            timedelta(minutes=17),
            5.0,
            240.0,
        )
        self.assertEqual(entity.extra_state_attributes["sender_id"], "05:00:00:01")


if __name__ == "__main__":
    unittest.main()

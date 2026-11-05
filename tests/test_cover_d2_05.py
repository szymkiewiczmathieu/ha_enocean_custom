"""D2-05-00 cover protocol, entity, identity and UI tests."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

import voluptuous as vol
from homeassistant.components.cover import ATTR_CURRENT_POSITION, ATTR_POSITION
from homeassistant.core import HomeAssistant

try:
    from probatio.error import Invalid as ProbatioInvalid
except ModuleNotFoundError:  # HA 2026.7 uses voluptuous directly.
    ProbatioInvalid = vol.Invalid

from custom_components.enocean_custom import cover, d2_05
from custom_components.enocean_custom.const import (
    FORWARDED_PLATFORMS,
    UI_DEVICE_PLATFORMS,
)
from custom_components.enocean_custom.device_catalog import get_product_recipe
from custom_components.enocean_custom.device_form import (
    device_details_schema,
    validate_device_details,
)
from custom_components.enocean_custom.device_intelligence import (
    implementation_for,
    support_for,
)
from custom_components.enocean_custom.enocean_library.protocol.constants import RORG
from custom_components.enocean_custom.identity import (
    cover_unique_id,
    ui_device_entity_identities,
)
from custom_components.enocean_custom.schema import UI_DEVICE_SCHEMA

SENDER = [0x05, 0x8F, 0x22, 0x33]
BASE_ID = [0xFF, 0x87, 0xCA, 0x00]


def _row(**changes):
    row = {
        "id": SENDER,
        "platform": "cover",
        "name": "Bedroom shutter",
        "channel": 0,
        "radio_metadata": {"eep": "D2-05-00"},
    }
    row.update(changes)
    return row


class ProtocolTests(unittest.TestCase):
    """EEP 2.6.7 D2-05-00, pp. 158-159, exact command/reply layouts."""

    def test_position_commands_invert_ha_and_leave_other_fields_unchanged(self):
        packet = d2_05.build_position_command(BASE_ID, SENDER, 0, 25)
        self.assertEqual(
            packet.data,
            [RORG.VLD, 75, 0x7F, 0x00, 0x01, *BASE_ID, 0x00],
        )
        self.assertEqual(packet.optional, [0x03, *SENDER, 0xFF, 0x00])
        self.assertEqual(
            d2_05.build_position_command(BASE_ID, SENDER, 0, 100).data[1],
            0,
        )
        self.assertEqual(
            d2_05.build_position_command(BASE_ID, SENDER, 0, 0).data[1],
            100,
        )
        with self.assertRaises(ValueError):
            d2_05.build_position_command(BASE_ID, SENDER, 1, 25)
        with self.assertRaises(ValueError):
            d2_05.build_position_command(BASE_ID, SENDER, 0, 101)

    def test_stop_and_query_are_one_byte_channel_commands(self):
        self.assertEqual(
            d2_05.build_stop_command(BASE_ID, SENDER, 0).data,
            [RORG.VLD, 0x02, *BASE_ID, 0x00],
        )
        self.assertEqual(
            d2_05.build_query_command(BASE_ID, SENDER, 0).data,
            [RORG.VLD, 0x03, *BASE_ID, 0x00],
        )

    def test_reply_decodes_position_unknown_angle_lock_and_channel(self):
        status = d2_05.parse_d2_05_00_status(
            [RORG.VLD, 60, 40, 0x01, 0x04, *SENDER, 0x00]
        )
        self.assertIsNotNone(status)
        self.assertEqual(status.position, 40)  # spec 60 % closed -> HA 40 % open
        self.assertEqual(status.angle, 40)
        self.assertEqual(status.locking_mode, 1)
        self.assertEqual(status.channel, 0)

        unknown = d2_05.parse_d2_05_00_status(
            [RORG.VLD, 0x7F, 0x7F, 0x00, 0x04, *SENDER, 0x00]
        )
        self.assertIsNone(unknown.position)
        self.assertIsNone(unknown.angle)
        self.assertIsNone(
            d2_05.parse_d2_05_00_status([RORG.VLD, 60, 0x7F, 0x00, 0x14, *SENDER, 0x00])
        )
        self.assertIsNone(
            d2_05.parse_d2_05_00_status([RORG.VLD, 60, 0x7F, 0x00, 0x03, *SENDER, 0x00])
        )
        self.assertIsNone(
            d2_05.parse_d2_05_00_status(
                [RORG.VLD, 101, 0x7F, 0x00, 0x04, *SENDER, 0x00]
            )
        )


class FakeDongle:
    def __init__(self):
        self.available = True
        self.base_id = list(BASE_ID)
        self.entry_id = "entry"
        self.sent = []
        self.responses = []
        self.written = []

    def send(self, packet, response_callback=None, written_callback=None):
        self.sent.append(packet)
        self.responses.append(response_callback)
        self.written.append(written_callback)
        return True


def _hass_with_gateway(gateway):
    entry = SimpleNamespace(runtime_data=gateway)
    return SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda domain: [entry]),
        data={},
    )


class CoverEntityTests(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeDongle()
        self.entity = cover.EnOceanCover(SENDER, "Bedroom shutter", 0)
        self.entity.hass = _hass_with_gateway(self.gateway)
        self.entity.entity_id = "cover.bedroom_shutter"
        self.entity.schedule_update_ha_state = Mock()
        self.entity.async_write_ha_state = Mock()

    def test_commands_do_not_optimistically_change_position(self):
        asyncio.run(self.entity.async_open_cover())
        asyncio.run(self.entity.async_close_cover())
        asyncio.run(self.entity.async_set_cover_position(**{ATTR_POSITION: 25}))
        self.assertEqual(
            [packet.data[1:5] for packet in self.gateway.sent],
            [[0, 0x7F, 0, 1], [100, 0x7F, 0, 1], [75, 0x7F, 0, 1]],
        )
        self.assertIsNone(self.entity.current_cover_position)
        self.assertIsNone(self.entity.is_closed)
        self.assertEqual(self.entity.unique_id, "93266483-0-d2-05-00-cover")

    def test_feedback_updates_real_position_and_lock_without_angle_guessing(self):
        self.entity.value_changed(
            SimpleNamespace(
                rorg=RORG.VLD,
                data=[RORG.VLD, 60, 0x7F, 0x02, 0x04, *SENDER, 0],
            )
        )
        self.assertEqual(self.entity.current_cover_position, 40)
        self.assertIsNone(self.entity.extra_state_attributes["d2_angle"])
        self.assertEqual(self.entity.extra_state_attributes["d2_locking_mode"], 2)
        self.assertFalse(self.entity.is_closed)

        # Unknown POS (0x7F) does not erase a valid restored/reported position.
        self.entity.value_changed(
            SimpleNamespace(
                rorg=RORG.VLD,
                data=[RORG.VLD, 0x7F, 0x7F, 0x00, 0x04, *SENDER, 0],
            )
        )
        self.assertEqual(self.entity.current_cover_position, 40)
        self.entity.value_changed(
            SimpleNamespace(
                rorg=RORG.VLD,
                data=[RORG.VLD, 100, 0x7F, 0x00, 0x04, *SENDER, 0],
            )
        )
        self.assertTrue(self.entity.is_closed)

    def test_stop_schedules_one_query_after_the_physical_write(self):
        cancel = Mock()
        with unittest.mock.patch.object(
            cover, "async_call_later", return_value=cancel
        ) as later:
            asyncio.run(self.entity.async_stop_cover())
            self.assertEqual(self.gateway.sent[0].data[1], 0x02)
            later.assert_not_called()
            self.gateway.written[0]()
            later.assert_called_once()
            self.assertEqual(later.call_args.args[1], 2.0)
            later.call_args.args[2](None)
        self.assertEqual(self.gateway.sent[1].data[1], 0x03)


class CoverLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_restore_then_startup_query(self):
        config_dir = TemporaryDirectory()
        hass = HomeAssistant(config_dir.name)
        gateway = FakeDongle()
        entry = SimpleNamespace(runtime_data=gateway)
        hass.config_entries = SimpleNamespace(async_entries=lambda domain: [entry])
        entity = cover.EnOceanCover(SENDER, "Bedroom shutter", 0)
        entity.hass = hass
        entity.async_write_ha_state = Mock()
        entity.async_get_last_state = AsyncMock(
            return_value=SimpleNamespace(attributes={ATTR_CURRENT_POSITION: 37})
        )
        try:
            await entity.async_added_to_hass()
            for _ in range(100):
                if gateway.sent:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(entity.current_cover_position, 37)
            self.assertEqual(len(gateway.sent), 1)
            self.assertEqual(gateway.sent[0].data[1], 0x03)
            await entity.async_will_remove_from_hass()
        finally:
            await hass.async_stop(force=True)
            config_dir.cleanup()


class ConfigurationTests(unittest.TestCase):
    def test_cover_schema_identity_and_catalog(self):
        row = UI_DEVICE_SCHEMA(_row())
        self.assertEqual(row["platform"], "cover")
        self.assertEqual(cover_unique_id(SENDER, 0), "93266483-0-d2-05-00-cover")
        self.assertEqual(
            ui_device_entity_identities(row),
            (("cover", "93266483-0-d2-05-00-cover"),),
        )
        self.assertIn("cover", UI_DEVICE_PLATFORMS)
        self.assertIn("cover", FORWARDED_PLATFORMS)
        self.assertEqual(
            implementation_for("D2-05-00").recommended_platform,
            "cover",
        )
        self.assertEqual(support_for("D2-05-00").value, "supported")
        recipe = get_product_recipe("nodon_sin_2_rs_01")
        self.assertEqual(recipe.platforms, ("cover",))
        self.assertEqual(recipe.support, "manual")
        with self.assertRaises((vol.Invalid, ProbatioInvalid)):
            UI_DEVICE_SCHEMA(_row(channel=1))
        with self.assertRaises((vol.Invalid, ProbatioInvalid)):
            UI_DEVICE_SCHEMA(_row(radio_metadata={"eep": "D2-01-12"}))

    def test_cover_uses_the_shared_device_form(self):
        schema = device_details_schema(
            "cover", suggested_sender_id="", declared_eep=None
        )
        self.assertEqual(len(schema.schema), 1)
        outcome = validate_device_details(
            "cover",
            {"channel": 0},
            captured_id=SENDER,
            name="Bedroom shutter",
            radio_metadata={"eep": "D2-05-00"},
            used_sender_ids=[],
        )
        self.assertEqual(outcome.errors, {})
        self.assertEqual(outcome.row["platform"], "cover")
        rejected = validate_device_details(
            "cover",
            {"channel": 1},
            captured_id=SENDER,
            name="Bedroom shutter",
            radio_metadata={"eep": "D2-05-00"},
            used_sender_ids=[],
        )
        self.assertIsNone(rejected.row)
        self.assertEqual(rejected.errors, {"channel": "invalid_channel"})

    def test_config_flow_version_stays_one(self):
        try:
            from custom_components.enocean_custom.config_flow import EnOceanFlowHandler
        except ModuleNotFoundError as err:  # pragma: no cover - environment
            self.skipTest(f"config flow dependencies unavailable: {err}")
        self.assertEqual(EnOceanFlowHandler.VERSION, 1)


if __name__ == "__main__":
    unittest.main()

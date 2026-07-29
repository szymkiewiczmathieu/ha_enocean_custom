"""Unit tests for the v1.3.0 UI teach-in feature."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare CI lifecycle environment has no Home Assistant installed.
    import voluptuous as vol
    from homeassistant.core import HomeAssistant
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.enocean_custom import learn, light
    from custom_components.enocean_custom.const import (
        DOMAIN,
        EVENT_DEVICE_LEARNED,
        SERVICE_LEARN,
        SIGNAL_RECEIVE_MESSAGE,
    )
    from custom_components.enocean_custom.options_flow import (
        _parse_commissioning_code,
        _unique_id_for,
    )
    from custom_components.enocean_custom.schema import UI_DEVICE_SCHEMA

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

COMPONENT_ROOT = ROOT / "custom_components/enocean_custom"
STRINGS_PATH = COMPONENT_ROOT / "strings.json"
EN_TRANSLATION_PATH = COMPONENT_ROOT / "translations/en.json"
FR_TRANSLATION_PATH = COMPONENT_ROOT / "translations/fr.json"


def _key_paths(node: object, prefix: str = "") -> set[str]:
    """Return the set of dotted key paths in a nested translation tree."""
    if isinstance(node, dict):
        paths: set[str] = set()
        for key, value in node.items():
            paths |= _key_paths(value, f"{prefix}.{key}" if prefix else key)
        return paths
    return {prefix}


class TranslationSyncTests(unittest.TestCase):
    def test_english_translation_matches_strings_exactly(self):
        self.assertEqual(
            json.loads(EN_TRANSLATION_PATH.read_text()),
            json.loads(STRINGS_PATH.read_text()),
        )

    def test_french_translation_has_the_same_keys_as_strings(self):
        strings = json.loads(STRINGS_PATH.read_text())
        french = json.loads(FR_TRANSLATION_PATH.read_text())
        self.assertEqual(_key_paths(strings), _key_paths(french))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class UniqueIdFormulaTests(unittest.TestCase):
    def test_binary_sensor_unique_id_matches_yaml_formula(self):
        device = {
            "id": [1, 2, 3, 4],
            "platform": "binary_sensor",
            "device_class": "door",
        }
        identifier = (1 << 24) | (2 << 16) | (3 << 8) | 4
        self.assertEqual(_unique_id_for(device), f"{identifier}-door")

    def test_switch_unique_id_matches_yaml_formula(self):
        device = {"id": [1, 2, 3, 4], "platform": "switch", "channel": 2}
        identifier = (1 << 24) | (2 << 16) | (3 << 8) | 4
        self.assertEqual(_unique_id_for(device), f"{identifier}-2")

    def test_light_unique_id_matches_yaml_formula(self):
        device = {"id": [1, 2, 3, 4], "platform": "light"}
        identifier = (1 << 24) | (2 << 16) | (3 << 8) | 4
        self.assertEqual(_unique_id_for(device), f"{identifier}")

    def test_climate_unique_id_matches_yaml_formula(self):
        device = {"id": [1, 2, 3, 4], "platform": "climate"}
        identifier = (1 << 24) | (2 << 16) | (3 << 8) | 4
        self.assertEqual(_unique_id_for(device), f"{identifier}-0")

    def test_sensor_unique_id_matches_yaml_formula(self):
        device = {
            "id": [1, 2, 3, 4],
            "platform": "sensor",
            "device_class": "temperature",
        }
        identifier = (1 << 24) | (2 << 16) | (3 << 8) | 4
        self.assertEqual(_unique_id_for(device), f"{identifier}-temperature")


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class UiDeviceSchemaTests(unittest.TestCase):
    def test_schema_accepts_a_minimal_switch_device(self):
        validated = UI_DEVICE_SCHEMA(
            {"id": [1, 2, 3, 4], "platform": "switch", "name": "relay"}
        )
        self.assertEqual(validated["channel"], 0)
        self.assertIsNone(validated["device_class"])
        self.assertIsNone(validated["sender_id"])

    def test_schema_accepts_climate_and_sensor(self):
        climate_device = UI_DEVICE_SCHEMA(
            {
                "id": [1, 2, 3, 4],
                "platform": "climate",
                "name": "heating",
                "id_switch": [5, 6, 7, 8],
                "device_type": "SRC-D08",
                "sensor_entity_id": "sensor.room_temperature",
            }
        )
        self.assertEqual(climate_device["sensor_target_temperature_range"], 5)
        sensor_device = UI_DEVICE_SCHEMA(
            {"id": [1, 2, 3, 4], "platform": "sensor", "name": "power"}
        )
        self.assertEqual(sensor_device["device_class"], "powersensor")

    def test_schema_rejects_bool_and_out_of_range_channel(self):
        base = {"id": [1, 2, 3, 4], "platform": "switch", "name": "x"}
        for bad_channel in (True, False, 1.5, -1, 256):
            with self.assertRaises(vol.Invalid):
                UI_DEVICE_SCHEMA({**base, "channel": bad_channel})

    def test_schema_rejects_a_malformed_sender_id(self):
        base = {"id": [1, 2, 3, 4], "platform": "light", "name": "x"}
        UI_DEVICE_SCHEMA({**base, "sender_id": [1, 2, 3, 4]})
        with self.assertRaises(vol.Invalid):
            UI_DEVICE_SCHEMA({**base, "sender_id": [1, 2, 3]})


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class CommissioningQrTests(unittest.TestCase):
    def test_official_qr_extracts_32_bit_eurid(self):
        payload = "30S0000AABBCCDD+1P001B0000789A+10Z00+11Z12312312"
        self.assertEqual(_parse_commissioning_code(payload), [0xAA, 0xBB, 0xCC, 0xDD])

    def test_fallback_colon_id_is_accepted(self):
        self.assertEqual(
            _parse_commissioning_code("AA:BB:CC:DD"), [0xAA, 0xBB, 0xCC, 0xDD]
        )

    def test_invalid_or_48_bit_qr_is_rejected(self):
        for payload in (
            "not-a-code",
            "30S0000AABBCCDD",
            "30S1234AABBCCDD+1P001B0000789A",
            "30S0000AABBCCDG+1P001B0000789A",
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(_parse_commissioning_code(payload))

    def test_malformed_fallbacks_and_label_garbage_are_rejected(self):
        # Adversarial review vectors: the tolerant sender-id parser must not
        # leak into the commissioning path, and labels must be clean MH10.8.2.
        for payload in (
            "AA::BB:CC:DD",
            "AA  BB CC DD",
            "AA : BB : CC : DD",
            "aabbccdd",
            "30S0000A1B2C3D4+1P001B0000789A+99ZBAD\x00UNICODE☃",
            "30S0000A1B2C3D4 +1P001B0000789A",
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(_parse_commissioning_code(payload))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class SendTeachInTests(unittest.TestCase):
    def test_light_entity_service_is_registered(self):
        platform = Mock()
        with patch.object(
            light.entity_platform,
            "async_get_current_platform",
            return_value=platform,
        ):
            light._async_register_send_teach_in_service()
        platform.async_register_entity_service.assert_called_once_with(
            "send_teach_in", {}, "send_teach_in"
        )

    def test_packet_matches_a5_38_08_eltako_teach_in(self):
        entity = light.EnOceanLight(
            [0xFF, 0x9C, 0xD4, 0x38],
            [0x01, 0x02, 0x03, 0x04],
            "dimmer",
        )
        packets = []
        entity.send_command = (  # type: ignore[method-assign]
            lambda data, optional, packet_type: (
                packets.append((data, optional, packet_type)) or True
            )
        )

        entity.send_teach_in()

        self.assertEqual(
            packets,
            [
                (
                    [
                        0xA5,
                        0xE0,
                        0x40,
                        0x0D,
                        0x80,
                        0xFF,
                        0x9C,
                        0xD4,
                        0x38,
                        0x00,
                    ],
                    [0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00],
                    0x01,
                )
            ],
        )

    def test_send_teach_in_without_sender_id_fails_cleanly(self):
        # Review finding: a missing sender_id produced a raw TypeError.
        entity = light.EnOceanLight(None, [0x01, 0x02, 0x03, 0x04], "bad dimmer")
        with self.assertRaises(HomeAssistantError):
            entity.send_teach_in()

    def test_send_teach_in_rejected_by_dongle_raises(self):
        # Review finding P1-02: a locally rejected telegram is a service
        # error, never a silently "sent" teach-in.
        entity = light.EnOceanLight(
            [0xFF, 0x9C, 0xD4, 0x38],
            [0x01, 0x02, 0x03, 0x04],
            "dimmer",
        )
        with (
            patch.object(entity, "send_command", return_value=False),
            self.assertRaises(HomeAssistantError),
        ):
            entity.send_teach_in()


class FakeRadioPacket:
    def __init__(self, sender_int: int) -> None:
        self.sender_int = sender_int


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class LearnManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def test_start_marks_active_and_stop_clears_it(self):
        manager = learn.LearnManager(self.hass)
        self.assertFalse(manager.is_active)
        manager.start(timeout=15)
        self.assertTrue(manager.is_active)
        manager.stop()
        self.assertFalse(manager.is_active)

    async def test_capture_of_unknown_sender_stops_the_window_and_fires_event(self):
        manager = learn.LearnManager(self.hass)
        events = []
        self.hass.bus.async_listen(
            EVENT_DEVICE_LEARNED, lambda event: events.append(event.data)
        )
        manager.start(timeout=15)

        async_dispatcher_send(
            self.hass, SIGNAL_RECEIVE_MESSAGE, FakeRadioPacket(0x01020304)
        )
        await self.hass.async_block_till_done()

        self.assertEqual(manager.captured, [0x01, 0x02, 0x03, 0x04])
        self.assertFalse(manager.is_active)
        self.assertEqual(
            events, [{"id": [0x01, 0x02, 0x03, 0x04], "hex": "01:02:03:04"}]
        )

    async def test_known_sender_is_ignored(self):
        manager = learn.LearnManager(self.hass)
        learn.register_known_id(self.hass, [0x01, 0x02, 0x03, 0x04])
        manager.start(timeout=15)

        async_dispatcher_send(
            self.hass, SIGNAL_RECEIVE_MESSAGE, FakeRadioPacket(0x01020304)
        )
        await self.hass.async_block_till_done()

        self.assertIsNone(manager.captured)
        self.assertTrue(manager.is_active)
        manager.stop()

    async def test_timeout_closes_the_window_without_a_capture(self):
        manager = learn.LearnManager(self.hass)
        manager.start(timeout=0.01)
        await asyncio.sleep(0.05)
        self.assertFalse(manager.is_active)
        self.assertIsNone(manager.captured)

    async def test_stop_cancels_the_pending_timeout_handle(self):
        manager = learn.LearnManager(self.hass)
        manager.start(timeout=15)
        handle = manager._timeout_handle
        manager.stop()
        self.assertTrue(handle.cancelled())

    async def test_restart_while_active_is_refused_and_preserves_the_window(self):
        manager = learn.LearnManager(self.hass)
        self.assertTrue(manager.start(timeout=15))
        first_handle = manager._timeout_handle
        self.assertFalse(manager.start(timeout=15))
        self.assertFalse(first_handle.cancelled())
        self.assertTrue(manager.is_active)
        manager.stop()

    async def test_stop_with_foreign_owner_leaves_the_window_running(self):
        manager = learn.LearnManager(self.hass)
        self.assertTrue(manager.start(timeout=15, owner="service"))
        manager.stop(owner="options_flow")
        self.assertTrue(manager.is_active)
        manager.stop(owner="service")
        self.assertFalse(manager.is_active)

    async def test_register_known_id_ignores_empty_ids(self):
        learn.register_known_id(self.hass, [])
        learn.register_known_id(self.hass, None)
        self.assertEqual(learn.get_known_ids(self.hass), set())

    async def test_ui_known_ids_skips_hand_edited_malformed_rows(self):
        """N2: a malformed .storage row must not crash the learn machinery."""
        from types import SimpleNamespace

        from custom_components.enocean_custom.enocean_library.utils import combine_hex

        entry = SimpleNamespace(
            options={
                "ui_devices": [
                    {"id": "aabbccdd", "platform": "switch", "name": "bad"},
                    {"id": [1, 2, 3], "platform": "switch", "name": "short"},
                    {
                        "id": [0x0A, 0x0B, 0x0C, 0x0D],
                        "platform": "switch",
                        "name": "good",
                    },
                ]
            }
        )
        self.hass.config_entries = SimpleNamespace(async_entries=lambda domain: [entry])
        known = learn._ui_known_ids(self.hass)
        self.assertEqual(known, {combine_hex([0x0A, 0x0B, 0x0C, 0x0D])})

    def test_light_without_sender_id_is_rejected_by_the_schema(self):
        """N4: a light row without sender_id can never reach turn_on."""
        import voluptuous as vol

        from custom_components.enocean_custom.schema import (
            UI_DEVICE_SCHEMA,
            valid_ui_devices,
        )

        with self.assertRaises(vol.Invalid):
            UI_DEVICE_SCHEMA(
                {
                    "id": [1, 2, 3, 4],
                    "platform": "light",
                    "name": "bad light",
                    "sender_id": None,
                }
            )
        kept = valid_ui_devices(
            [
                {
                    "id": [1, 2, 3, 4],
                    "platform": "light",
                    "name": "bad light",
                    "sender_id": None,
                },
                {
                    "id": [1, 2, 3, 4],
                    "platform": "light",
                    "name": "good light",
                    "sender_id": [5, 6, 7, 8],
                },
            ]
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["name"], "good light")

    async def test_yaml_sensor_and_climate_register_their_sender_ids(self):
        """K3: a sender owned by a YAML sensor/climate is not teachable."""
        from unittest.mock import AsyncMock, patch

        from custom_components.enocean_custom import climate, sensor
        from custom_components.enocean_custom.const import (
            DATA_ENOCEAN,
            DATA_KNOWN_IDS,
        )
        from custom_components.enocean_custom.enocean_library.utils import combine_hex

        sensor_id = [0x01, 0x02, 0x03, 0x04]
        sensor.setup_platform(
            self.hass,
            {"id": sensor_id, "name": "t", "device_class": "power"},
            lambda entities: None,
        )
        known = self.hass.data[DATA_ENOCEAN][DATA_KNOWN_IDS]
        self.assertIn(combine_hex(sensor_id), known)

        climate_id = [0x05, 0x06, 0x07, 0x08]
        with (
            patch.object(climate, "async_setup_reload_service", new=AsyncMock()),
            patch.object(climate, "_migrate_to_new_unique_id"),
        ):
            await climate.async_setup_platform(
                self.hass,
                {
                    "id": climate_id,
                    "name": "rad",
                    "device_type": "SRC-D08",
                    "sender_id_switch": [0x09, 0x0A, 0x0B, 0x0C],
                    "sensor_entity_id": "sensor.fake_temp",
                    "sensor_target_temp_range": {"min": 5, "max": 30},
                    "sensor_target_temp_tolerance": 0.5,
                },
                lambda entities: None,
            )
        self.assertIn(combine_hex(climate_id), known)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ServiceRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def test_register_learn_service_is_idempotent(self):
        learn.async_register_learn_service(self.hass)
        learn.async_register_learn_service(self.hass)
        self.assertTrue(self.hass.services.has_service(DOMAIN, SERVICE_LEARN))

    async def test_learn_service_starts_a_window_with_the_requested_timeout(self):
        learn.async_register_learn_service(self.hass)
        await self.hass.services.async_call(
            DOMAIN, SERVICE_LEARN, {"timeout": 20}, blocking=True
        )
        manager = learn.get_learn_manager(self.hass)
        self.assertTrue(manager.is_active)
        manager.stop()

    async def test_learn_service_rejects_out_of_range_timeout(self):
        learn.async_register_learn_service(self.hass)
        with self.assertRaises(vol.Invalid):
            await self.hass.services.async_call(
                DOMAIN, SERVICE_LEARN, {"timeout": 1000}, blocking=True
            )


if __name__ == "__main__":
    unittest.main()

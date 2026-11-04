"""Device-page teach-in buttons delegate to the existing entity actions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.const import CONF_ENTITY_ID
    from homeassistant.core import HomeAssistant
    from homeassistant.exceptions import ServiceValidationError
    from homeassistant.helpers.entity import EntityCategory

    from custom_components.enocean_custom import button
    from custom_components.enocean_custom import event as event_platform
    from custom_components.enocean_custom.const import (
        DOMAIN,
        SERVICE_SEND_TEACH_IN,
        SIGNAL_DONGLE_STATUS,
        SIGNAL_RECEIVE_MESSAGE,
    )
    from custom_components.enocean_custom.schema import UI_DEVICE_SCHEMA

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [0x05, 0x8F, 0x22, 0x33]
SENDER_DECIMAL = "93266483"


def _rows() -> list[dict]:
    common = {"id": SENDER}
    return [
        UI_DEVICE_SCHEMA(
            {
                **common,
                "platform": "light",
                "name": "Dimmer",
                "sender_id": [0xFF, 0x87, 0xCA, 0x01],
            }
        ),
        UI_DEVICE_SCHEMA(
            {
                **common,
                "platform": "climate",
                "name": "Panel",
                "id_switch": [0xFF, 0x87, 0xCA, 0x02],
                "device_type": "SRC-D08",
                "sensor_entity_id": "sensor.room",
            }
        ),
        UI_DEVICE_SCHEMA(
            {
                **common,
                "platform": "switch",
                "name": "Relay",
                "switch_type": "default",
            }
        ),
    ]


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class TeachInButtonFactoryTests(unittest.TestCase):
    def test_light_and_climate_rows_create_only_their_three_actions(self):
        entities = button._teach_in_buttons(_rows())
        self.assertEqual(
            [entity.unique_id for entity in entities],
            [
                f"{SENDER_DECIMAL}-teach-in",
                f"{SENDER_DECIMAL}-0-teach-in",
                f"{SENDER_DECIMAL}-0-teach-in-rocker",
            ],
        )
        self.assertEqual(
            [entity.translation_key for entity in entities],
            [
                "light_teach_in",
                "climate_teach_in_actor",
                "climate_teach_in_actor_switch",
            ],
        )
        self.assertTrue(all(entity.has_entity_name for entity in entities))
        self.assertTrue(
            all(entity.entity_category is EntityCategory.CONFIG for entity in entities)
        )

    def test_a_switch_row_alone_creates_no_teach_in_button(self):
        self.assertEqual(button._teach_in_buttons([_rows()[2]]), [])


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class TeachInButtonPressTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_press_calls_the_existing_entity_service_exactly_once(self):
        registry = SimpleNamespace(
            async_get_entity_id=Mock(
                side_effect=[
                    "light.dimmer",
                    "climate.panel",
                    "climate.panel",
                ]
            )
        )
        services = SimpleNamespace(async_call=AsyncMock())
        hass = SimpleNamespace(services=services)
        entities = button._teach_in_buttons(_rows())
        with patch.object(button.er, "async_get", return_value=registry):
            for entity in entities:
                entity.hass = hass
                await entity.async_press()

        self.assertEqual(
            services.async_call.await_args_list,
            [
                call(
                    DOMAIN,
                    SERVICE_SEND_TEACH_IN,
                    {CONF_ENTITY_ID: "light.dimmer"},
                    blocking=True,
                ),
                call(
                    DOMAIN,
                    "climate_teach_in_actor",
                    {CONF_ENTITY_ID: "climate.panel"},
                    blocking=True,
                ),
                call(
                    DOMAIN,
                    "climate_teach_in_actor_switch",
                    {CONF_ENTITY_ID: "climate.panel"},
                    blocking=True,
                ),
            ],
        )

    async def test_a_missing_target_is_a_translated_validation_error(self):
        registry = SimpleNamespace(async_get_entity_id=Mock(return_value=None))
        services = SimpleNamespace(async_call=AsyncMock())
        entity = button._teach_in_buttons(_rows())[0]
        entity.hass = SimpleNamespace(services=services)
        with (
            patch.object(button.er, "async_get", return_value=registry),
            self.assertRaises(ServiceValidationError) as raised,
        ):
            await entity.async_press()
        self.assertEqual(
            raised.exception.translation_key, "teach_in_target_unavailable"
        )
        services.async_call.assert_not_awaited()


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ActionButtonListenerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        entry = SimpleNamespace(runtime_data=SimpleNamespace(available=True))
        self.hass.config_entries = SimpleNamespace(async_entries=lambda domain: [entry])

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def test_action_buttons_do_not_subscribe_to_radio_they_never_decode(self):
        entities = [
            *button._all_buttons(_rows()),
            event_platform.EnOceanRockerEvent(SENDER, "Rocker"),
        ]
        self.assertEqual(len(entities), 5)  # four actions + bus-fed rocker event
        with patch(
            "custom_components.enocean_custom.entity.async_dispatcher_connect",
            side_effect=lambda *args: lambda: None,
        ) as connect:
            for entity in entities:
                entity.hass = self.hass
                await entity.async_added_to_hass()
                self.assertTrue(entity.available)

        signals = [call.args[1] for call in connect.call_args_list]
        self.assertNotIn(SIGNAL_RECEIVE_MESSAGE, signals)
        self.assertEqual(signals.count(SIGNAL_DONGLE_STATUS), len(entities))


if __name__ == "__main__":
    unittest.main()

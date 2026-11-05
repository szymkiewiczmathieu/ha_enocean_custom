"""Opt-in D2-01 command-feedback verification and bounded retries."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.enocean_custom.const import DOMAIN, EVENT_NO_FEEDBACK
from custom_components.enocean_custom.d2_01 import (
    CONF_D2_01_FEEDBACK_CHECK,
    CONF_D2_01_QUERY_ON_START,
)
from custom_components.enocean_custom.enocean_library.protocol.constants import RORG
from custom_components.enocean_custom.options_flow import EnOceanOptionsFlow
from custom_components.enocean_custom.switch import EnOceanSwitch

SENDER = [0x11, 0x22, 0x33, 0x44]
BASE_ID = [0xA1, 0xB2, 0xC3, 0xD4]


class FakeDongle:
    def __init__(self):
        self.available = True
        self.base_id = list(BASE_ID)
        self.entry_id = "entry"
        self.sent = []
        self.callbacks = []

    def send(self, packet, response_callback=None, written_callback=None):
        self.sent.append(packet)
        self.callbacks.append(response_callback)
        return True


class FeedbackOptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_option_is_off_by_default_and_saved_without_touching_rows(self):
        flow = EnOceanOptionsFlow()
        entry = SimpleNamespace(options={"ui_devices": [{"sentinel": True}]})
        flow.hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_get_known_entry=lambda entry_id: entry)
        )
        flow.handler = "entry"
        result = await flow.async_step_actuators()
        fields = {
            str(key.schema): key.default() if callable(key.default) else key.default
            for key in result["data_schema"].schema
        }
        self.assertEqual(fields[CONF_D2_01_QUERY_ON_START], False)
        self.assertEqual(fields[CONF_D2_01_FEEDBACK_CHECK], False)

        saved = await flow.async_step_actuators(
            {
                CONF_D2_01_QUERY_ON_START: False,
                CONF_D2_01_FEEDBACK_CHECK: True,
            }
        )
        self.assertIs(saved["data"][CONF_D2_01_FEEDBACK_CHECK], True)
        self.assertEqual(saved["data"]["ui_devices"], [{"sentinel": True}])


class FeedbackCheckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        self.gateway = FakeDongle()
        self.hass.config_entries = ConfigEntries(self.hass, {})
        entry = ConfigEntry(
            data={"device": "/dev/test-feedback"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=3,
            options={},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        entry.runtime_data = self.gateway
        self.gateway.entry_id = entry.entry_id
        self.hass.config_entries._entries[entry.entry_id] = entry
        self.device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "11223344")},
        )
        self.events = []
        self.hass.bus.async_listen(EVENT_NO_FEEDBACK, self.events.append)
        self.entity = EnOceanSwitch(SENDER, "Relay", 0, "default", feedback_check=True)
        self.entity.hass = self.hass
        self.entity.entity_id = "switch.relay"
        self.entity.schedule_update_ha_state = Mock()
        self.entity.async_write_ha_state = Mock()

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _status(self, output):
        self.entity.value_changed(
            SimpleNamespace(
                data=[RORG.VLD, 0x04, 0, output, *SENDER, 0],
            )
        )

    async def test_silence_retries_twice_then_fires_once_without_state_change(self):
        with patch(
            "custom_components.enocean_custom.switch.FEEDBACK_TIMEOUT_SECONDS",
            0.01,
        ):
            self.entity.turn_on()
            await self.hass.async_block_till_done()

        self.assertEqual(len(self.gateway.sent), 3)
        self.assertIsNone(self.entity.is_on)
        self.assertEqual(len(self.events), 1)
        data = self.events[0].data
        self.assertEqual(data["entity_id"], "switch.relay")
        self.assertEqual(data["device_id"], self.device.id)
        self.assertEqual(data["command"], "turn_on")
        self.assertEqual(data["attempts"], 3)

    async def test_matching_new_state_stops_retries(self):
        with patch(
            "custom_components.enocean_custom.switch.FEEDBACK_TIMEOUT_SECONDS",
            0.05,
        ):
            self.entity.turn_on()
            await asyncio.sleep(0.005)
            self._status(100)
            await self.hass.async_block_till_done()

        self.assertEqual(len(self.gateway.sent), 1)
        self.assertIs(self.entity.is_on, True)
        self.assertEqual(self.events, [])

    async def test_opposite_feedback_does_not_confirm_requested_state(self):
        with patch(
            "custom_components.enocean_custom.switch.FEEDBACK_TIMEOUT_SECONDS",
            0.01,
        ):
            self.entity.turn_on()
            for _ in range(3):
                await asyncio.sleep(0.005)
                self._status(0)
            await self.hass.async_block_till_done()

        self.assertEqual(len(self.gateway.sent), 3)
        self.assertIs(self.entity.is_on, False)
        self.assertEqual(len(self.events), 1)

    async def test_disabled_check_never_retries_or_emits(self):
        self.entity._feedback_check = False
        self.entity.turn_on()
        await asyncio.sleep(0.03)
        await self.hass.async_block_till_done()
        self.assertEqual(len(self.gateway.sent), 1)
        self.assertEqual(self.events, [])

    async def test_rps_never_gets_d2_feedback_retries(self):
        self.entity._profile = "RPS"
        self.entity.turn_on()
        await asyncio.sleep(0.03)
        await self.hass.async_block_till_done()
        self.assertEqual(len(self.gateway.sent), 2)  # press + release only
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()

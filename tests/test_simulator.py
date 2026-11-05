"""Opt-in simulated telegram service and receive-chain tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from custom_components.enocean_custom import simulator
from custom_components.enocean_custom.binary_sensor import EnOceanBinarySensor
from custom_components.enocean_custom.const import (
    CONF_ALLOW_SIMULATION,
    DOMAIN,
    EVENT_SIMULATED_TELEGRAM,
    SERVICE_SIMULATE,
    SIGNAL_RECEIVE_MESSAGE,
)
from custom_components.enocean_custom.dongle import EnOceanDongle
from custom_components.enocean_custom.enocean_library.protocol.constants import RORG
from custom_components.enocean_custom.inbox import DeviceInbox

SENDER_TEXT = "05:8F:22:33"
SENDER = [0x05, 0x8F, 0x22, 0x33]


class PresetTests(unittest.TestCase):
    def test_presets_use_real_profile_bytes_and_cover_inversion(self):
        self.assertEqual(
            simulator.build_simulated_packet("window_open", SENDER).data[1],
            0x08,
        )
        self.assertEqual(
            simulator.build_simulated_packet("window_closed", SENDER).data[1],
            0x09,
        )
        temperature = simulator.build_simulated_packet("temperature", SENDER)
        self.assertEqual(temperature.data[1:5], [0, 0, 118, 0x08])
        button = simulator.build_simulated_packet("button", SENDER)
        self.assertEqual((button.data[1], button.status), (0x10, 0x30))
        smoke = simulator.build_simulated_packet("smoke_alarm", SENDER)
        self.assertEqual((smoke.data[1], smoke.status), (0x10, 0x30))
        cover = simulator.build_simulated_packet("cover_position", SENDER, 40)
        self.assertEqual(cover.data[1:5], [60, 0x7F, 0, 0x04])
        self.assertTrue(cover.simulated)
        with self.assertRaises(ValueError):
            simulator.build_simulated_packet("cover_position", SENDER, 101)
        with self.assertRaises(ValueError):
            simulator.build_simulated_packet("not-a-preset", SENDER)


class SimulatorServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        self.gateway = object.__new__(EnOceanDongle)
        self.gateway.hass = self.hass
        self.gateway._received_packets = 0
        self.gateway._last_packet_at = None
        self.gateway._inbox = DeviceInbox(lambda: set())
        self.entry = ConfigEntry(
            data={"device": "/dev/test-simulator"},
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
        self.gateway.entry_id = self.entry.entry_id
        self.entry.runtime_data = self.gateway
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry
        self.received = []
        self.simulated_events = []
        self._unsubscribe = async_dispatcher_connect(
            self.hass, SIGNAL_RECEIVE_MESSAGE, self.received.append
        )
        self.hass.bus.async_listen(
            EVENT_SIMULATED_TELEGRAM, self.simulated_events.append
        )
        simulator.async_register_simulate_service(self.hass)

    async def asyncTearDown(self):
        self._unsubscribe()
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def test_service_is_admin_only_and_disabled_by_default(self):
        self.hass.config_entries.async_update_entry(
            self.entry, options={CONF_ALLOW_SIMULATION: True}
        )
        self.hass.auth = SimpleNamespace(
            async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False))
        )
        with self.assertRaises(Unauthorized):
            await self.hass.services.async_call(
                DOMAIN,
                SERVICE_SIMULATE,
                {"preset": "smoke_alarm", "sender_id": SENDER_TEXT},
                blocking=True,
                context=Context(user_id="non-admin-user"),
            )
        self.assertEqual(self.received, [])

        self.hass.config_entries.async_update_entry(self.entry, options={})
        with (
            self.assertLogs("custom_components.enocean_custom", level="WARNING"),
            self.assertRaises(HomeAssistantError) as refused,
        ):
            await self.hass.services.async_call(
                DOMAIN,
                SERVICE_SIMULATE,
                {"preset": "smoke_alarm", "sender_id": SENDER_TEXT},
                blocking=True,
            )
        self.assertEqual(refused.exception.translation_key, "simulation_disabled")
        self.assertEqual(self.received, [])
        self.assertEqual(self.simulated_events, [])

    async def test_opt_in_injects_real_chain_with_markers_and_no_persistence(self):
        self.hass.config_entries.async_update_entry(
            self.entry, options={CONF_ALLOW_SIMULATION: True}
        )
        original_options = dict(self.entry.options)
        with self.assertLogs(
            "custom_components.enocean_custom", level="WARNING"
        ) as logs:
            await self.hass.services.async_call(
                DOMAIN,
                SERVICE_SIMULATE,
                {"preset": "smoke_alarm", "sender_id": SENDER_TEXT},
                blocking=True,
            )
        await self.hass.async_block_till_done()
        self.assertEqual(len(self.received), 1)
        packet = self.received[0]
        self.assertEqual(packet.rorg, RORG.RPS)
        self.assertEqual(packet.data[1], 0x10)
        self.assertEqual(packet.sender, SENDER)
        self.assertIs(packet.simulated, True)
        self.assertEqual(len(self.simulated_events), 1)
        self.assertIs(self.simulated_events[0].data["simulated"], True)
        self.assertEqual(self.simulated_events[0].data["preset"], "smoke_alarm")
        self.assertEqual(self.entry.options, original_options)
        self.assertIn("Simulated EnOcean telegram", "\n".join(logs.output))

    async def test_button_pressed_event_also_marks_simulation(self):
        self.hass.config_entries.async_update_entry(
            self.entry, options={CONF_ALLOW_SIMULATION: True}
        )
        button_events = []
        self.hass.bus.async_listen("button_pressed", button_events.append)
        entity = EnOceanBinarySensor(SENDER, "Rocker", None)
        entity.hass = self.hass
        entity.schedule_update_ha_state = Mock()
        self._unsubscribe()
        self._unsubscribe = async_dispatcher_connect(
            self.hass, SIGNAL_RECEIVE_MESSAGE, entity.value_changed
        )
        await self.hass.services.async_call(
            DOMAIN,
            SERVICE_SIMULATE,
            {"preset": "button", "sender_id": SENDER_TEXT},
            blocking=True,
        )
        await self.hass.async_block_till_done()
        self.assertEqual(len(button_events), 1)
        self.assertIs(button_events[0].data["simulated"], True)


if __name__ == "__main__":
    unittest.main()

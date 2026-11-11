"""A5-20-04: the answer leaves through the real serial chain, in time.

EEP 2.6.8 gives a valve taught in with the 4BS method 1100 ms to hear its
controller after each of its own status telegrams. Here the production
parser, serial worker, dongle and climate entity run against the ESP3
emulator on a pseudo-terminal: a status telegram goes in, and the frame the
emulator captures on its "radio" side is checked byte for byte and timed.

This proves the software path and its latency on the test host. It says
nothing about a real dongle, a real valve or radio propagation.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from homeassistant import loader
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from custom_components.enocean_custom import climate
from custom_components.enocean_custom.const import DOMAIN, SIGNAL_RECEIVE_MESSAGE
from custom_components.enocean_custom.dongle import EnOceanDongle
from custom_components.enocean_custom.enocean_library.protocol.constants import RORG
from scripts.fake_esp3_module import FakeESP3Module

BASE_ID = (0xFF, 0x80, 0x00, 0x00)
CONTROLLER = (0xFF, 0x80, 0x00, 0x01)
VALVE = (0x01, 0x02, 0x03, 0x04)
# EEP 2.6.8, A5-20-04: maximum response time with the 4BS teach-in method.
RECEIVE_WINDOW_SECONDS = 1.1


async def wait_for(predicate, timeout):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not met before the deadline")


class ReceiveWindowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        loader.async_setup(self.hass)
        await ir.async_load(self.hass)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.entry = ConfigEntry(
            data={"device": "/dev/test"},
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
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry
        self.emulator = FakeESP3Module(base_id=BASE_ID)
        self.gateway = EnOceanDongle(self.hass, self.emulator.port, self.entry.entry_id)
        self.entry.runtime_data = self.gateway
        await self.gateway.async_setup()
        await wait_for(lambda: self.gateway.base_id is not None, 4.0)

        self.entity = climate.EnOceanClimate(
            list(VALVE),
            "Radiator",
            list(CONTROLLER),
            "sensor.room",
            8.0,
            5,
            0.5,
            21.0,
            4.0,
            timedelta(minutes=17),
            5.0,
            240.0,
            device_type=climate.PROFILE_A5_20_04,
        )
        self.entity.hass = self.hass
        self.entity.schedule_update_ha_state = lambda *args, **kwargs: None
        self.entity._attr_hvac_mode = HVACMode.HEAT
        self.entity._attr_target_temp = 21.0
        self.entity._attr_pi_control_output = 40.0
        self._unsubscribe = async_dispatcher_connect(
            self.hass, SIGNAL_RECEIVE_MESSAGE, self.entity._message_received_callback
        )

    async def asyncTearDown(self):
        self._unsubscribe()
        if self.gateway._communicator.is_alive():
            await self.gateway.async_unload()
        self.emulator.close()
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def test_status_telegram_is_answered_inside_the_receive_window(self):
        loop = asyncio.get_running_loop()
        started = loop.time()
        # DB2 carries the set point shown on the valve: raw 140 = 21.0 degC.
        self.emulator.inject_a5_20_04(
            VALVE, position=42, temperature_raw=128, target_raw=140
        )
        await wait_for(
            lambda: len(self.emulator.sent_radio) == 1, RECEIVE_WINDOW_SECONDS
        )
        latency = loop.time() - started

        self.assertLess(latency, RECEIVE_WINDOW_SECONDS)
        outbound = self.emulator.sent_radio[0]
        self.assertEqual(outbound.rorg, RORG.BS4)
        # POS 40 %, TSP raw 140 (21.0 degC), WUC 33 (1020 s), data telegram.
        self.assertEqual(outbound.payload, (40, 140, 33, 0x08))
        self.assertEqual(outbound.sender, CONTROLLER)
        self.assertEqual(outbound.destination, VALVE)
        self.assertEqual(self.entity._attr_valve_position, 42)

        # One status telegram, one answer.
        await asyncio.sleep(0.2)
        self.assertEqual(len(self.emulator.sent_radio), 1)

    async def test_teach_in_telegram_is_never_answered(self):
        # 4BS teach-in with EEP A5-20-04 (LRN bit clear).
        self.emulator.inject_4bs([0x80, 0x20, 0x00, 0x80], VALVE)
        await asyncio.sleep(0.4)
        self.assertEqual(self.emulator.sent_radio, [])
        self.assertIsNone(self.entity._attr_valve_position)


if __name__ == "__main__":
    unittest.main()

"""Real dongle/communicator integration tests against the ESP3 emulator."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from custom_components.enocean_custom.const import SIGNAL_RECEIVE_MESSAGE
from custom_components.enocean_custom.d2_01 import build_status_query
from custom_components.enocean_custom.dongle import EnOceanDongle
from custom_components.enocean_custom.enocean_library.protocol.constants import RORG
from scripts.fake_esp3_module import FakeESP3Module, FakeESP3TCPServer

SENDER = (0x05, 0x8F, 0x22, 0x33)
BASE_ID = (0xFF, 0x87, 0xCA, 0x00)


async def wait_for(predicate, timeout=4.0):
    """Wait for a serial/HA condition without guessing at fixed scheduling delays."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before the serial test deadline")


class EmulatorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the production parser, worker and dongle, not a packet mock."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        self.received = []
        self._unsubscribe = async_dispatcher_connect(
            self.hass, SIGNAL_RECEIVE_MESSAGE, self.received.append
        )
        self.gateway = None
        self.emulator = None

    async def asyncTearDown(self):
        self._unsubscribe()
        if self.gateway is not None and self.gateway._communicator.is_alive():
            await self.gateway.async_unload()
        if self.emulator is not None:
            self.emulator.close()
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def test_pty_identify_receive_send_and_clean_unload(self):
        self.emulator = FakeESP3Module(base_id=BASE_ID)
        self.gateway = EnOceanDongle(self.hass, self.emulator.port, "pty-entry")
        await self.gateway.async_setup()
        await wait_for(
            lambda: (
                self.gateway.base_id is not None
                and self.gateway.gateway_info is not None
                and self.gateway.repeater is not None
            )
        )

        self.assertEqual(self.gateway.base_id, list(BASE_ID))
        self.assertEqual(self.gateway.gateway_info.app_version, "2.15.0.0")
        self.assertFalse(self.gateway.repeater.enabled)
        self.assertEqual(self.gateway.repeater.level, 1)
        self.assertTrue(self.gateway.available)
        self.assertFalse(self.gateway._communicator.teach_in)

        self.emulator.inject_rps(0x10, SENDER, status=0x30)
        self.emulator.inject_4bs([0x00, 0x00, 118, 0x08], SENDER)
        self.emulator.inject_d2_01_status(SENDER, channel=1, output=100)
        self.emulator.inject_a5_20_04(SENDER, position=42, temperature_raw=128)
        self.emulator.inject_d2_05_00_status(SENDER, position=60, channel=0)
        await wait_for(lambda: len(self.received) >= 5)

        self.assertEqual(
            [packet.rorg for packet in self.received[:5]],
            [RORG.RPS, RORG.BS4, RORG.VLD, RORG.BS4, RORG.VLD],
        )
        self.assertEqual(self.received[2].data[1:4], [0x04, 0x01, 100])
        self.assertEqual(self.received[4].data[1:5], [60, 0x7F, 0x00, 0x04])
        self.assertTrue(
            all(packet.sender_int == 0x058F2233 for packet in self.received)
        )

        accepted = []
        written = []
        query = build_status_query(self.gateway.base_id, list(SENDER), channel=1)
        self.assertTrue(
            self.gateway.send(
                query,
                response_callback=accepted.append,
                written_callback=lambda: written.append(True),
            )
        )
        await wait_for(lambda: accepted == [True] and written == [True])
        await wait_for(lambda: len(self.emulator.sent_radio) == 1)
        outbound = self.emulator.sent_radio[0]
        self.assertEqual(outbound.rorg, RORG.VLD)
        self.assertEqual(outbound.payload, (0x03, 0x01))
        self.assertEqual(outbound.sender, BASE_ID)
        self.assertEqual(outbound.destination, SENDER)

        self.assertTrue(await self.gateway.async_unload())
        self.assertFalse(self.gateway._communicator.is_alive())
        self.assertFalse(self.gateway.available)

    async def test_network_loss_reconnect_and_clean_unload(self):
        self.emulator = FakeESP3TCPServer(base_id=BASE_ID)
        self.gateway = EnOceanDongle(self.hass, self.emulator.url, "network-entry")
        self.gateway.RECONNECT_BACKOFF_SECONDS = (0.02, 0.02, 0.02, 0.02, 0.02)
        await self.gateway.async_setup()
        await wait_for(lambda: self.gateway.base_id is not None)
        self.assertTrue(self.gateway.available)

        self.emulator.drop_client()
        await wait_for(lambda: not self.gateway.available)
        await wait_for(
            lambda: (
                self.gateway.available
                and self.gateway._reconnect_attempts == 0
                and self.gateway.base_id is not None
            ),
            timeout=6.0,
        )
        self.assertGreaterEqual(self.emulator.connection_count, 2)

        self.assertTrue(await self.gateway.async_unload())
        self.assertFalse(self.gateway._communicator.is_alive())


if __name__ == "__main__":
    unittest.main()

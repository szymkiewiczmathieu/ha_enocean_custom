"""The dongle repeater select: write, then trust only the read-back.

CO_WR_REPEATER / CO_RD_REPEATER address the USB gateway over the serial link
(ESP3 2.5.11 and 2.5.12). The Ubiwizz module repeater remains blocked and is
not touched by anything here.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.core import HomeAssistant
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers.entity import EntityCategory

    from custom_components.enocean_custom import dongle as dongle_module
    from custom_components.enocean_custom import select
    from custom_components.enocean_custom.const import DOMAIN, FORWARDED_PLATFORMS
    from custom_components.enocean_custom.dongle import (
        CO_RD_REPEATER,
        CO_WR_REPEATER,
        EnOceanDongle,
        RepeaterState,
    )
    from custom_components.enocean_custom.enocean_library.communicators.communicator import (
        Communicator,
    )
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RETURN_CODE,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import (
        Packet,
    )
    from custom_components.enocean_custom.schema import CONF_UI_DEVICES

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False


def _response(code, data):
    wire = Packet(PACKET.RESPONSE, data=[int(code), *data]).build()
    return Packet.parse_msg(bytearray(wire))[2]


if HA_AVAILABLE:

    class RecordingCommunicator(Communicator):
        """Real bookkeeping with the serial port and the thread stubbed out."""

        def __init__(
            self,
            port,
            callback=None,
            stopped_callback=None,
            written_callback=None,
            teach_in=False,
        ):
            super().__init__(callback, teach_in=teach_in)
            self.name = "EnOceanSerialCommunicator"
            self.alive = True

        def start(self):
            return None

        def is_alive(self):
            return self.alive

        def close(self):
            return None


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class OptionMappingTests(unittest.TestCase):
    def test_read_back_to_option(self):
        self.assertIsNone(select.repeater_option(None))
        self.assertEqual(select.repeater_option(RepeaterState(False, 1, 0)), "off")
        self.assertEqual(select.repeater_option(RepeaterState(True, 1, 1)), "level_1")
        self.assertEqual(select.repeater_option(RepeaterState(True, 2, 1)), "level_2")
        # A selective repeater (ESP3 1.58 value 2) still shows its level.
        self.assertEqual(select.repeater_option(RepeaterState(True, 2, 2)), "level_2")

    def test_select_is_forwarded_but_never_offered_as_a_row_platform(self):
        from custom_components.enocean_custom.const import UI_DEVICE_PLATFORMS

        self.assertIn("select", FORWARDED_PLATFORMS)
        self.assertNotIn("select", UI_DEVICE_PLATFORMS)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class RepeaterWriteReadBackTests(unittest.IsolatedAsyncioTestCase):
    """The write is a serial command; the state comes from the read-back."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        with patch.object(dongle_module, "SerialCommunicator", RecordingCommunicator):
            self.dongle = EnOceanDongle(self.hass, "/dev/test-repeater", "entry-1")
        self.communicator = self.dongle._communicator
        self.dongle._available = True
        self.dongle.REPEATER_COMMAND_TIMEOUT_SECONDS = 0.2

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def _answer_next(self, code, data):
        """Let the stubbed worker write the next queued command and answer it.

        The dongle resolves its futures through ``call_soon_threadsafe``, so
        the next command appears after a few loop iterations, not instantly.
        """
        for _ in range(100):
            if not self.communicator.transmit.empty():
                break
            await asyncio.sleep(0.001)
        packet = self.communicator.transmit.get_nowait()
        self.communicator._packet_written(packet)
        self.communicator._response_received(_response(code, data))
        return packet

    async def test_write_then_read_back_updates_the_state(self):
        task = asyncio.ensure_future(self.dongle.async_set_repeater(True, 2))
        written = await self._answer_next(RETURN_CODE.OK, [])
        self.assertEqual(written.packet_type, PACKET.COMMON_COMMAND)
        self.assertEqual(written.data, [CO_WR_REPEATER, 1, 2])
        self.assertIsNone(self.dongle.repeater, "state must wait for the read-back")
        read = await self._answer_next(RETURN_CODE.OK, [1, 2])
        self.assertEqual(read.data, [CO_RD_REPEATER])
        state = await asyncio.wait_for(task, 1)
        self.assertEqual((state.enabled, state.level), (True, 2))
        self.assertEqual(self.dongle.repeater, state)

    async def test_switching_off_writes_level_one_and_disabled(self):
        task = asyncio.ensure_future(self.dongle.async_set_repeater(False, 2))
        written = await self._answer_next(RETURN_CODE.OK, [])
        self.assertEqual(written.data, [CO_WR_REPEATER, 0, 1])
        await self._answer_next(RETURN_CODE.OK, [0, 1])
        state = await asyncio.wait_for(task, 1)
        self.assertFalse(state.enabled)

    async def test_refused_write_is_a_translated_error_and_changes_nothing(self):
        self.dongle._repeater = RepeaterState(False, 1, 0)
        task = asyncio.ensure_future(self.dongle.async_set_repeater(True, 1))
        await self._answer_next(RETURN_CODE.NOT_SUPPORTED, [])
        with self.assertRaises(HomeAssistantError) as raised:
            await asyncio.wait_for(task, 1)
        self.assertEqual(raised.exception.translation_domain, DOMAIN)
        self.assertEqual(raised.exception.translation_key, "repeater_write_rejected")
        self.assertEqual(self.dongle.repeater, RepeaterState(False, 1, 0))
        self.assertTrue(
            self.communicator.transmit.empty(), "no read-back after a refusal"
        )

    async def test_missing_read_back_is_a_translated_timeout(self):
        task = asyncio.ensure_future(self.dongle.async_set_repeater(True, 1))
        await self._answer_next(RETURN_CODE.OK, [])
        with self.assertRaises(HomeAssistantError) as raised:
            await asyncio.wait_for(task, 2)
        self.assertEqual(raised.exception.translation_key, "repeater_readback_timeout")
        self.assertEqual(raised.exception.translation_placeholders, {"seconds": "0"})
        self.assertFalse(self.dongle._repeater_waiters)

    async def test_unavailable_dongle_refuses_before_queueing(self):
        self.dongle._available = False
        with self.assertRaises(HomeAssistantError) as raised:
            await self.dongle.async_set_repeater(True, 1)
        self.assertEqual(raised.exception.translation_key, "repeater_write_rejected")
        self.assertTrue(self.communicator.transmit.empty())

    async def test_invalid_level_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            await self.dongle.async_set_repeater(True, 3)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class SelectEntityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def test_entity_reads_the_gateway_and_writes_through_it(self):
        calls = []

        async def set_repeater(enabled, level):
            calls.append((enabled, level))
            gateway.repeater = RepeaterState(enabled, level, int(enabled))
            return gateway.repeater

        gateway = SimpleNamespace(
            entry_id="entry-1",
            repeater=None,
            available=True,
            async_set_repeater=set_repeater,
        )
        entry = SimpleNamespace(
            entry_id="entry-1", runtime_data=gateway, options={CONF_UI_DEVICES: []}
        )
        created: list = []
        await select.async_setup_entry(self.hass, entry, created.extend)
        (entity,) = created
        entity.hass = self.hass
        entity.async_write_ha_state = lambda: None
        self.assertEqual(entity.unique_id, "gateway:entry-1:gateway_repeater")
        self.assertEqual(entity.entity_category, EntityCategory.CONFIG)
        self.assertEqual(entity.options, ["off", "level_1", "level_2"])
        self.assertIsNone(entity.current_option)
        self.assertTrue(entity.available)

        await entity.async_select_option("level_2")
        self.assertEqual(calls, [(True, 2)])
        self.assertEqual(entity.current_option, "level_2")
        await entity.async_select_option("off")
        self.assertEqual(calls[-1], (False, 1))
        self.assertEqual(entity.current_option, "off")

    async def test_no_select_without_a_real_entry(self):
        created: list = []
        await select.async_setup_entry(
            self.hass, SimpleNamespace(options={}), created.extend
        )
        self.assertEqual(created, [])


if __name__ == "__main__":
    unittest.main()

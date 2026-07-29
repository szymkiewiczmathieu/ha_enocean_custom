"""End-to-end tests for D2-01-12 actuator status feedback."""

from __future__ import annotations

import asyncio
import unittest
from tempfile import TemporaryDirectory

try:  # The bare CI lifecycle environment has no Home Assistant installed.
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.enocean_custom.const import SIGNAL_RECEIVE_MESSAGE
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        PARSE_RESULT,
        RORG,
    )
    from custom_components.enocean_custom.enocean_library.protocol.d2 import (
        parse_d2_01_actuator_status,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import (
        Packet,
    )
    from custom_components.enocean_custom.light import EnOceanLight
    from custom_components.enocean_custom.switch import EnOceanSwitch

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False


def _parse_radio(data: list[int]):
    """Build and parse a real ESP3 RADIO_ERP1 frame."""
    wire_packet = Packet(
        PACKET.RADIO_ERP1,
        data=data,
        optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
    ).build()
    status, remaining, packet = Packet.parse_msg(bytearray(wire_packet))
    if status != PARSE_RESULT.OK or remaining:
        raise AssertionError(f"Could not parse synthetic radio packet: {status}")
    return wire_packet, packet


def _status_packet(
    sender: list[int],
    channel: int,
    output: int,
    *,
    flags: int = 0,
    channel_flags: int = 0,
    local_control: bool = False,
):
    """Create a parsed D2-01 Actuator Status Response."""
    # EEP 2.6.7, §D2-01 CMD 0x4, p. 136: CMD occupies the low nibble, I/O
    # the low five bits, and OV the low seven bits.
    return _parse_radio(
        [
            RORG.VLD,
            flags | 0x04,
            channel_flags | channel,
            (0x80 if local_control else 0) | output,
            *sender,
            0x00,
        ]
    )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class D201PacketTests(unittest.TestCase):
    """Exercise the vendored parser and D2-01-12 decoder together."""

    def test_variable_length_vld_status_is_accepted_and_decoded(self):
        wire_packet, packet = _status_packet(
            [0x01, 0x02, 0x03, 0x04],
            1,
            64,
            flags=0xC0,
            channel_flags=0xA0,
            local_control=True,
        )

        self.assertEqual(wire_packet[1:3], [0x00, 0x09])
        self.assertEqual(packet.rorg, RORG.VLD)
        status = parse_d2_01_actuator_status(packet.data)
        self.assertIsNotNone(status)
        self.assertEqual(status.channel, 1)
        self.assertEqual(status.output_value, 64)
        self.assertTrue(status.power_failure_detection_enabled)
        self.assertTrue(status.power_failure_detected)
        self.assertTrue(status.over_current_switch_off)
        self.assertEqual(status.error_level, 1)
        self.assertTrue(status.local_control_enabled)

        packet.parse_eep(0x01, 0x12, command=0x04)
        self.assertEqual(packet.parsed["CMD"]["raw_value"], 0x04)
        self.assertEqual(packet.parsed["IO"]["raw_value"], 1)
        self.assertEqual(packet.parsed["OV"]["raw_value"], 64)
        self.assertEqual(packet.parsed["PF"]["raw_value"], 1)
        self.assertEqual(packet.parsed["PFD"]["raw_value"], 1)

    def test_malformed_d2_packets_are_rejected_without_exception(self):
        _, truncated = _parse_radio(
            [RORG.VLD, 0x04, 0x00, 0x01, 0x02, 0x03, 0x04, 0x00]
        )
        _, invalid_output = _status_packet([0x01, 0x02, 0x03, 0x04], 0, 101)
        _, wrong_command = _parse_radio(
            [
                RORG.VLD,
                0x03,
                0x00,
                50,
                0x01,
                0x02,
                0x03,
                0x04,
                0x00,
            ]
        )

        self.assertIsNone(parse_d2_01_actuator_status(truncated.data))
        self.assertIsNone(parse_d2_01_actuator_status(invalid_output.data))
        self.assertIsNone(parse_d2_01_actuator_status(wrong_command.data))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class D201DispatcherTests(unittest.IsolatedAsyncioTestCase):
    """Pass parsed packets through HA's dispatcher to real entities."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        self.sender = [0x01, 0x02, 0x03, 0x04]
        self.loop_writes: list[asyncio.AbstractEventLoop] = []

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def _add(self, entity):
        entity.hass = self.hass
        entity.async_write_ha_state = lambda: self.loop_writes.append(
            asyncio.get_running_loop()
        )
        await entity.async_added_to_hass()
        return entity

    async def test_switch_channels_and_light_follow_real_d2_status(self):
        switch_0 = await self._add(EnOceanSwitch(self.sender, "relay 0", 0, "default"))
        switch_1 = await self._add(EnOceanSwitch(self.sender, "relay 1", 1, "default"))
        light = await self._add(
            EnOceanLight([0x05, 0x06, 0x07, 0x08], self.sender, "dimmer")
        )
        light_ch1 = await self._add(
            EnOceanLight([0x05, 0x06, 0x07, 0x08], self.sender, "dimmer ch1", 1)
        )

        _, channel_0 = _status_packet(self.sender, 0, 25)
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, channel_0)
        await self.hass.async_block_till_done()

        self.assertIs(switch_0.is_on, True)
        self.assertIsNone(switch_1.is_on)
        self.assertIs(light.is_on, True)
        self.assertEqual(light.brightness, 64)
        # Channel-1 light does not react to a channel-0 status.
        self.assertIsNone(light_ch1.is_on)
        self.assertEqual(switch_0.extra_state_attributes["d2_channel"], 0)
        self.assertEqual(
            switch_0.extra_state_attributes["d2_output_value"],
            25,
        )
        self.assertIn("last_status", light.extra_state_attributes)

        _, channel_1 = _status_packet(self.sender, 1, 0)
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, channel_1)
        await self.hass.async_block_till_done()

        self.assertIs(switch_0.is_on, True)
        self.assertIs(switch_1.is_on, False)
        # Regression guard (review finding): a channel-1 status must NOT
        # overwrite a light bound to channel 0.
        self.assertIs(light.is_on, True)
        self.assertEqual(light.brightness, 64)
        self.assertIs(light_ch1.is_on, False)
        self.assertEqual(light_ch1.brightness, 0)
        self.assertTrue(self.loop_writes)
        self.assertTrue(all(loop is self.hass.loop for loop in self.loop_writes))

    async def test_unknown_sender_and_malformed_d2_are_ignored(self):
        entity = await self._add(
            EnOceanSwitch(self.sender, "known relay", 0, "default")
        )
        _, unknown = _status_packet([0x10, 0x20, 0x30, 0x40], 0, 100)
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, unknown)

        _, malformed = _parse_radio([RORG.VLD, 0x04, 0x00, *self.sender, 0x00])
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, malformed)
        await self.hass.async_block_till_done()

        self.assertIsNone(entity.is_on)
        self.assertEqual(entity.extra_state_attributes, {})
        self.assertEqual(self.loop_writes, [])

    async def test_d2_feedback_syncs_rps_switch_state(self):
        # Deliberate contract: RPS entities consume D2 status so a physical
        # wall-switch toggle on a directly-paired module syncs HA's state.
        entity = await self._add(EnOceanSwitch(self.sender, "RPS", 0, "RPS"))
        _, status = _status_packet(self.sender, 0, 100)
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, status)
        await self.hass.async_block_till_done()

        self.assertIs(entity.is_on, True)
        self.assertEqual(entity.extra_state_attributes["d2_output_value"], 100)
        self.assertTrue(self.loop_writes)

        # A status for the OTHER channel still does not leak across.
        _, other = _status_packet(self.sender, 1, 0)
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, other)
        await self.hass.async_block_till_done()
        self.assertIs(entity.is_on, True)

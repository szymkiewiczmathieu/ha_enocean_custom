"""Command and feedback boundaries for EnOcean switches."""

from types import SimpleNamespace
from typing import ClassVar
from unittest import TestCase
from unittest.mock import Mock

from custom_components.enocean_custom.const import DATA_ENOCEAN, ENOCEAN_DONGLE
from custom_components.enocean_custom.enocean_library.protocol.constants import RORG
from custom_components.enocean_custom.switch import EnOceanSwitch


class FakeDongle:
    """Capture outbound packets and resolve ESP3 callbacks under test control."""

    def __init__(self, base_id: tuple[int, ...] | None) -> None:
        self.base_id = base_id
        self.sent = []
        self.callbacks = []

    def send(self, packet, response_callback=None) -> bool:
        self.sent.append(packet)
        self.callbacks.append(response_callback)
        return True


class SwitchCommandTests(TestCase):
    """Keep ESP3 transport confirmation separate from actuator feedback."""

    sender: ClassVar[tuple[int, ...]] = (0x11, 0x22, 0x33, 0x44)
    base_id: ClassVar[tuple[int, ...]] = (0xA1, 0xB2, 0xC3, 0xD4)

    def _entity(self, *, channel: int = 0, switch_type: str = "default"):
        dongle = FakeDongle(self.base_id)
        entity = EnOceanSwitch(list(self.sender), "relay", channel, switch_type)
        entity.hass = SimpleNamespace(data={DATA_ENOCEAN: {ENOCEAN_DONGLE: dongle}})
        entity.async_write_ha_state = Mock()
        return entity, dongle

    def _status(self, entity: EnOceanSwitch, output: int) -> None:
        entity.value_changed(
            SimpleNamespace(
                data=[RORG.VLD, 0x04, entity.channel, output, *self.sender, 0x00]
            )
        )

    def test_d2_on_off_packets_are_directed_and_ack_does_not_change_state(self) -> None:
        entity, dongle = self._entity(channel=1)

        entity.turn_on()

        self.assertEqual(
            dongle.sent[0].data,
            [RORG.VLD, 0x01, 0x01, 100, *self.base_id, 0x00],
        )
        self.assertEqual(
            dongle.sent[0].optional,
            [0x03, *self.sender, 0xFF, 0x00],
        )
        dongle.callbacks[0](True)
        self.assertIsNone(entity.is_on)
        self.assertTrue(entity.extra_state_attributes["last_command_esp3_accepted"])

        self._status(entity, 100)
        self.assertIs(entity.is_on, True)

        entity.turn_off()

        self.assertEqual(
            dongle.sent[1].data,
            [RORG.VLD, 0x01, 0x01, 0, *self.base_id, 0x00],
        )
        dongle.callbacks[1](False)
        self.assertIs(entity.is_on, True)
        self.assertFalse(entity.extra_state_attributes["last_command_esp3_accepted"])

        self._status(entity, 0)
        self.assertIs(entity.is_on, False)

    def test_newer_d2_command_ignores_stale_transport_result(self) -> None:
        entity, dongle = self._entity()

        entity.turn_on()
        entity.turn_off()
        dongle.callbacks[0](True)

        self.assertNotIn("last_command_esp3_accepted", entity.extra_state_attributes)
        self.assertIsNone(entity.is_on)

        dongle.callbacks[1](False)

        self.assertFalse(entity.extra_state_attributes["last_command_esp3_accepted"])
        self.assertIsNone(entity.is_on)

    def test_rps_requires_all_transport_responses_without_claiming_actuation(
        self,
    ) -> None:
        entity, dongle = self._entity(switch_type="RPS")

        entity.turn_on()

        self.assertEqual(
            [packet.data for packet in dongle.sent],
            [
                [RORG.RPS, 0x50, *self.sender, 0x30],
                [RORG.RPS, 0x00, *self.sender, 0x20],
            ],
        )
        dongle.callbacks[0](False)
        dongle.callbacks[1](True)

        self.assertFalse(entity.extra_state_attributes["last_command_esp3_accepted"])
        self.assertIsNone(entity.is_on)

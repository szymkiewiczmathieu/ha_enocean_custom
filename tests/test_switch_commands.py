"""Command and feedback boundaries for EnOcean switches."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from typing import ClassVar
from unittest import TestCase
from unittest.mock import Mock

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

from custom_components.enocean_custom.const import DOMAIN
from custom_components.enocean_custom.enocean_library.protocol.constants import RORG
from custom_components.enocean_custom.light import EnOceanLight
from custom_components.enocean_custom.switch import EnOceanSwitch


def _hass_with_gateway(gateway) -> SimpleNamespace:
    """Mimic the loaded config entry that owns the dongle (``runtime_data``)."""
    entry = SimpleNamespace(runtime_data=gateway)
    return SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda domain: [entry]),
        data={},
    )


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


class StoppedGateway:
    """A dongle whose serial worker stopped: every command is refused at once."""

    base_id: ClassVar[list[int]] = [0xAA, 0xBB, 0xCC, 0x01]
    available = False

    def send(self, *_args, **_kwargs) -> bool:
        return False


class SwitchCommandTests(TestCase):
    """Keep ESP3 transport confirmation separate from actuator feedback."""

    sender: ClassVar[tuple[int, ...]] = (0x11, 0x22, 0x33, 0x44)
    base_id: ClassVar[tuple[int, ...]] = (0xA1, 0xB2, 0xC3, 0xD4)

    def _entity(self, *, channel: int = 0, switch_type: str = "default"):
        dongle = FakeDongle(self.base_id)
        entity = EnOceanSwitch(list(self.sender), "relay", channel, switch_type)
        entity.hass = _hass_with_gateway(dongle)
        # Transport results may complete on the executor thread, so they must
        # schedule the write; only loop-side feedback writes state directly.
        entity.schedule_update_ha_state = Mock()
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
        entity.schedule_update_ha_state.assert_called_once_with()
        entity.async_write_ha_state.assert_not_called()

        self._status(entity, 100)
        self.assertIs(entity.is_on, True)
        entity.async_write_ha_state.assert_called_once_with()

        entity.turn_off()

        self.assertEqual(
            dongle.sent[1].data,
            [RORG.VLD, 0x01, 0x01, 0, *self.base_id, 0x00],
        )
        dongle.callbacks[1](False)
        self.assertIs(entity.is_on, True)
        self.assertFalse(entity.extra_state_attributes["last_command_esp3_accepted"])
        self.assertEqual(entity.schedule_update_ha_state.call_count, 2)

        self._status(entity, 0)
        self.assertIs(entity.is_on, False)

    def test_newer_d2_command_ignores_stale_transport_result(self) -> None:
        entity, dongle = self._entity()

        entity.turn_on()
        entity.turn_off()
        dongle.callbacks[0](True)

        self.assertNotIn("last_command_esp3_accepted", entity.extra_state_attributes)
        self.assertIsNone(entity.is_on)
        entity.schedule_update_ha_state.assert_not_called()

        dongle.callbacks[1](False)

        self.assertFalse(entity.extra_state_attributes["last_command_esp3_accepted"])
        self.assertIsNone(entity.is_on)
        entity.schedule_update_ha_state.assert_called_once_with()

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
        entity.schedule_update_ha_state.assert_not_called()
        dongle.callbacks[1](True)

        self.assertFalse(entity.extra_state_attributes["last_command_esp3_accepted"])
        self.assertIsNone(entity.is_on)
        entity.schedule_update_ha_state.assert_called_once_with()


class ExecutorCommandStateWriteTests(unittest.IsolatedAsyncioTestCase):
    """Commands run in the executor must never write state off the event loop.

    ``ToggleEntity.async_turn_on`` runs the synchronous ``turn_on`` in Home
    Assistant's executor. A command refused before queueing completes its
    transport result on that same thread, where ``async_write_ha_state``
    raises for a custom integration once the frame helper is active.
    """

    async def asyncSetUp(self) -> None:
        self._config_dir = TemporaryDirectory()
        config_dir = Path(self._config_dir.name)
        # The frame helper attributes a call to an integration it can load
        # from the config directory, exactly as a real install does.
        (config_dir / "custom_components").symlink_to(ROOT / "custom_components")
        self.hass = HomeAssistant(str(config_dir))
        loader.async_setup(self.hass)
        frame.async_setup(self.hass)
        await loader.async_get_integration(self.hass, DOMAIN)
        # The stopped dongle is reached the way production reaches it: as the
        # ``runtime_data`` of the single EnOcean config entry.
        self.hass.config_entries = ConfigEntries(self.hass, {})
        entry = ConfigEntry(
            data={"device": "/dev/test-executor"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=1,
            options={},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        entry.runtime_data = StoppedGateway()
        self.hass.config_entries._entries[entry.entry_id] = entry

    async def asyncTearDown(self) -> None:
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def _run_in_executor(self, entity, command):
        entity.hass = self.hass
        await self.hass.async_add_executor_job(command)
        await self.hass.async_block_till_done()
        return self.hass.states.get(entity.entity_id)

    async def test_rejected_switch_command_writes_state_from_the_executor(self):
        for switch_type in ("default", "RPS"):
            with self.subTest(switch_type=switch_type):
                entity = EnOceanSwitch([1, 2, 3, 4], "lamp", 0, switch_type)
                entity.entity_id = f"switch.lamp_{switch_type.lower()}"
                state = await self._run_in_executor(entity, entity.turn_on)
                self.assertIsNotNone(state, "rejected command wrote no state")
                self.assertIs(
                    entity.extra_state_attributes["last_command_esp3_accepted"],
                    False,
                )
                self.assertIsNone(entity.is_on, "rejection must not assume a state")

    async def test_rejected_light_command_writes_state_from_the_executor(self):
        entity = EnOceanLight([5, 6, 7, 8], [1, 2, 3, 4], "dimmer")
        entity.entity_id = "light.dimmer"
        state = await self._run_in_executor(
            entity, lambda: entity.turn_on(brightness=255)
        )
        self.assertIsNotNone(state, "rejected command wrote no state")
        self.assertIsNone(entity.is_on, "rejection must not assume a state")


if __name__ == "__main__":
    unittest.main()

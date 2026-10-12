"""Focused directed-UTE commissioning regression coverage."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from custom_components.enocean_custom import options_flow
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RORG,
    )
    from custom_components.enocean_custom.schema import (
        CONF_UI_DEVICES,
        UI_DEVICE_SCHEMA,
    )
    from custom_components.enocean_custom.ute import build_ute_association_packet

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - bare environment
    HA_AVAILABLE = False

TARGET_ID = [0x04, 0xB3, 0x82, 0xDA]
OTHER_ID = [0x04, 0xB3, 0x82, 0xDB]
BASE_ID = [0xA1, 0xB2, 0xC3, 0xD4]
UTE_ASSOCIATION_FIELDS = [0x91, 0x02, 0x46, 0x00, 0x08, 0x01, 0xD2]


class FakeDongle:
    """Capture queued UTE packets and manually drive ESP3 callbacks."""

    def __init__(self, base_id: list[int] | None = None) -> None:
        self.base_id = base_id
        self.sent = []
        self.response_callbacks = []
        self.written_callbacks = []
        self.queue_result = True

    def send(self, packet, response_callback=None, written_callback=None) -> bool:
        self.sent.append(packet)
        self.response_callbacks.append(response_callback)
        self.written_callbacks.append(written_callback)
        return self.queue_result


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class UtePacketTests(unittest.TestCase):
    """Prove exact D4/91 data and target validation."""

    def test_directed_association_packet_matches_field_procedure(self) -> None:
        packet = build_ute_association_packet(
            sender_id=BASE_ID,
            target_id=TARGET_ID,
            eep="D2-01-12",
            number_of_channels=2,
            manufacturer_id=0x046,
        )

        self.assertEqual(packet.packet_type, PACKET.RADIO_ERP1)
        self.assertEqual(
            packet.data,
            [RORG.UTE, 0x91, 0x02, 0x46, 0x00, 0x12, 0x01, 0xD2, *BASE_ID, 0x00],
        )
        self.assertEqual(packet.optional, [0x03, *TARGET_ID, 0xFF, 0x00])

    def test_d2_01_08_field_profile_is_encoded_without_relay_command(self):
        packet = build_ute_association_packet(
            sender_id=BASE_ID,
            target_id=TARGET_ID,
            eep="D2-01-08",
            number_of_channels=2,
            manufacturer_id=0x046,
        )
        self.assertEqual(
            packet.data[0:8], [0xD4, 0x91, 0x02, 0x46, 0x00, 0x08, 0x01, 0xD2]
        )
        self.assertEqual(packet.optional, [0x03, *TARGET_ID, 0xFF, 0x00])

    def test_association_packet_rejects_missing_or_invalid_ids(self) -> None:
        for sender, target in (
            (None, TARGET_ID),
            ([0xA1, 0xB2, 0xC3], TARGET_ID),
            (BASE_ID, [0x04, 0xB3, 0x82]),
            (BASE_ID, [0x04, 0xB3, 0x82, 0x100]),
        ):
            with (
                self.subTest(sender=sender, target=target),
                self.assertRaises(ValueError),
            ):
                build_ute_association_packet(
                    sender_id=sender,
                    target_id=target,
                    eep="D2-01-12",
                    number_of_channels=2,
                    manufacturer_id=0x046,
                )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class UteDiscoveryFlowTests(unittest.IsolatedAsyncioTestCase):
    """Exercise explicit-EURID directed association and ESP3 acknowledgement."""

    async def asyncSetUp(self) -> None:
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self._flows = []

    async def asyncTearDown(self) -> None:
        for flow in self._flows:
            flow.async_remove()
            task = flow._ute_discovery_task
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _entry(self, rows=(), base_id=BASE_ID):
        entry = ConfigEntry(
            data={"device": "/dev/test-ute-discovery"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=1,
            options={CONF_UI_DEVICES: list(rows)},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        entry.runtime_data = FakeDongle(base_id)
        self.hass.config_entries._entries[entry.entry_id] = entry
        return entry

    def _flow(self, entry):
        flow = options_flow.EnOceanOptionsFlow()
        flow.hass = self.hass
        flow.handler = entry.entry_id
        self._flows.append(flow)
        return flow

    async def _start(self, flow, *, name="New relay", channel=0, target=TARGET_ID):
        details = await flow.async_step_ute_discover(
            {
                "name": name,
                "qr_code": ":".join(f"{byte:02X}" for byte in target),
                "channel": channel,
            }
        )
        self.assertEqual(details["type"], FlowResultType.FORM)
        self.assertEqual(details["step_id"], "ute_discover_confirm")
        dongle = flow.config_entry.runtime_data
        self.assertEqual(dongle.sent, [])

        result = await flow.async_step_ute_discover_confirm({"ute_ready": True})
        self.assertEqual(result["type"], FlowResultType.SHOW_PROGRESS)
        self.assertEqual(result["step_id"], "ute_discover_progress")
        self.assertEqual(len(dongle.sent), 1)
        self.assertEqual(
            dongle.sent[0].data,
            [RORG.UTE, *UTE_ASSOCIATION_FIELDS, *BASE_ID, 0x00],
        )
        self.assertEqual(dongle.sent[0].optional, [0x03, *target, 0xFF, 0x00])
        self.assertFalse(any(packet.data[0] == RORG.VLD for packet in dongle.sent))
        return dongle

    async def _finished_result(self, flow):
        await self.hass.async_block_till_done()
        progressed = await flow.async_step_ute_discover_progress()
        self.assertEqual(progressed["type"], FlowResultType.SHOW_PROGRESS_DONE)
        return await flow.async_step_ute_discover_result()

    async def test_esp3_ok_waits_for_physical_write_before_creating_output(self):
        entry = self._entry()
        flow = self._flow(entry)
        dongle = await self._start(flow, name="Boiler output 2", channel=1)

        self.assertTrue(callable(dongle.written_callbacks[0]))
        dongle.response_callbacks[0](True)
        self.assertFalse(flow._ute_discovery_written)
        self.assertIsNone(flow._ute_discovery_outcome)

        dongle.written_callbacks[0]()
        result = await self._finished_result(flow)

        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(
            result["reason"], "ute_discovery_transport_confirmed_unverified"
        )
        self.assertEqual(entry.options[CONF_UI_DEVICES], [])

    async def test_same_module_can_add_its_other_output(self):
        existing = UI_DEVICE_SCHEMA(
            {
                "id": TARGET_ID,
                "platform": "switch",
                "name": "Output 1",
                "channel": 0,
                "switch_type": "default",
                "actuator_profile": "ubiwizz_ubid1507c",
                "radio_metadata": {
                    "sender_id": TARGET_ID,
                    "eep": "D2-01-12",
                    "manufacturer_id": 0x046,
                    "evidence": "manual",
                    "eep_source": "manual",
                },
            }
        )
        entry = self._entry([existing])
        flow = self._flow(entry)
        dongle = await self._start(flow, channel=1)
        dongle.written_callbacks[0]()
        dongle.response_callbacks[0](True)

        result = await self._finished_result(flow)
        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(
            result["reason"], "ute_discovery_transport_confirmed_unverified"
        )
        self.assertEqual(entry.options[CONF_UI_DEVICES], [existing])

    async def test_unconfirmed_invalid_collision_queue_rejection_and_timeout_do_not_save(
        self,
    ):
        entry = self._entry()
        flow = self._flow(entry)
        details = await flow.async_step_ute_discover(
            {"name": "Unconfirmed", "qr_code": "04:B3:82:DA", "channel": 0}
        )
        self.assertEqual(details["type"], FlowResultType.FORM)
        not_ready = await flow.async_step_ute_discover_confirm({"ute_ready": False})
        self.assertEqual(not_ready["type"], FlowResultType.FORM)
        self.assertEqual(not_ready["errors"], {"ute_ready": "ute_not_ready"})
        self.assertEqual(entry.runtime_data.sent, [])

        invalid_flow = self._flow(entry)
        invalid = await invalid_flow.async_step_ute_discover(
            {"name": "Invalid", "qr_code": "not-an-eurid", "channel": 0}
        )
        self.assertEqual(invalid["type"], FlowResultType.FORM)
        self.assertEqual(invalid["errors"], {"qr_code": "invalid_ute_target"})

        no_base_entry = self._entry(base_id=None)
        no_base_flow = self._flow(no_base_entry)
        details = await no_base_flow.async_step_ute_discover(
            {"name": "No base", "qr_code": "04:B3:82:DA", "channel": 0}
        )
        self.assertEqual(details["type"], FlowResultType.FORM)
        no_base = await no_base_flow.async_step_ute_discover_confirm(
            {"ute_ready": True}
        )
        self.assertEqual(no_base["type"], FlowResultType.ABORT)
        self.assertEqual(no_base["reason"], "ute_discovery_base_id_unavailable")

        rejected_entry = self._entry()
        rejected_entry.runtime_data.queue_result = False
        rejected_flow = self._flow(rejected_entry)
        rejected_dongle = await self._start(rejected_flow)
        self.assertEqual(len(rejected_dongle.sent), 1)
        rejected = await self._finished_result(rejected_flow)
        self.assertEqual(rejected["type"], FlowResultType.ABORT)
        self.assertEqual(rejected["reason"], "ute_discovery_queue_rejected")

        response_rejected_entry = self._entry()
        response_rejected_flow = self._flow(response_rejected_entry)
        response_rejected_dongle = await self._start(response_rejected_flow)
        response_rejected_dongle.written_callbacks[0]()
        response_rejected_dongle.response_callbacks[0](False)
        response_rejected = await self._finished_result(response_rejected_flow)
        self.assertEqual(response_rejected["type"], FlowResultType.ABORT)
        self.assertEqual(
            response_rejected["reason"], "ute_discovery_transport_rejected"
        )

        collision_entry = self._entry(
            [
                UI_DEVICE_SCHEMA(
                    {
                        "id": TARGET_ID,
                        "platform": "switch",
                        "name": "Already configured",
                        "channel": 0,
                        "switch_type": "default",
                        "actuator_profile": "ubiwizz_ubid1507c",
                        "radio_metadata": {"eep": "D2-01-12"},
                    }
                )
            ]
        )
        collision_flow = self._flow(collision_entry)
        details = await collision_flow.async_step_ute_discover(
            {"name": "Duplicate", "qr_code": "04:B3:82:DA", "channel": 0}
        )
        self.assertEqual(details["type"], FlowResultType.FORM)
        collision = await collision_flow.async_step_ute_discover_confirm(
            {"ute_ready": True}
        )
        self.assertEqual(collision["type"], FlowResultType.ABORT)
        self.assertEqual(collision["reason"], "ute_discovery_collision")
        self.assertEqual(collision_entry.runtime_data.sent, [])

        timeout_entry = self._entry()
        timeout_flow = self._flow(timeout_entry)
        with patch.object(options_flow, "UTE_DISCOVERY_TIMEOUT", 0.001):
            timeout_dongle = await self._start(timeout_flow, channel=0)
            timeout_dongle.response_callbacks[0](True)
            await asyncio.sleep(0.01)
        timeout = await self._finished_result(timeout_flow)
        self.assertEqual(timeout["type"], FlowResultType.ABORT)
        self.assertEqual(timeout["reason"], "ute_discovery_write_unconfirmed")

        response_timeout_entry = self._entry()
        response_timeout_flow = self._flow(response_timeout_entry)
        with patch.object(options_flow, "UTE_DISCOVERY_TIMEOUT", 0.001):
            response_timeout_dongle = await self._start(
                response_timeout_flow, channel=0
            )
            response_timeout_dongle.written_callbacks[0]()
            await asyncio.sleep(0.01)
        response_timeout = await self._finished_result(response_timeout_flow)
        self.assertEqual(response_timeout["type"], FlowResultType.ABORT)
        self.assertEqual(response_timeout["reason"], "ute_discovery_timeout")

    async def test_stale_esp3_callback_cannot_persist_after_removal(self):
        entry = self._entry()
        flow = self._flow(entry)
        dongle = await self._start(flow, channel=0)
        response_callback = dongle.response_callbacks[0]
        flow.async_remove()
        response_callback(True)
        await self.hass.async_block_till_done()

        self.assertEqual(entry.options[CONF_UI_DEVICES], [])
        self.assertIsNone(flow._ute_discovery_outcome)


if __name__ == "__main__":
    unittest.main()

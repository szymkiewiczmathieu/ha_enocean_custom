"""Regression coverage for request-driven, bounded UTE commissioning."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

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
    from custom_components.enocean_custom.enocean_library.protocol.constants import PACKET, RORG
    from custom_components.enocean_custom.enocean_library.protocol.packet import UTETeachInPacket
    from custom_components.enocean_custom.schema import CONF_UI_DEVICES, UI_DEVICE_SCHEMA

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    HA_AVAILABLE = False

TARGET_ID = [0x04, 0xB3, 0x82, 0xDA]
OTHER_ID = [0x04, 0xB3, 0x82, 0xDB]
BASE_ID = [0xA1, 0xB2, 0xC3, 0xD4]


class FakeDongle:
    def __init__(self, base_id=BASE_ID):
        self.base_id = base_id
        self.sent = []
        self.response_callbacks = []
        self.written_callbacks = []
        self.queue_result = True

    def send(self, packet, response_callback=None, written_callback=None):
        self.sent.append(packet)
        self.response_callbacks.append(response_callback)
        self.written_callbacks.append(written_callback)
        return self.queue_result


def ute_request(
    *, sender=TARGET_ID, eep_type=0x12, manufacturer=0x046, channels=0,
    flags=0x80, age_ms=0,
):
    """Build one inbound bidirectional UTE CMD0 as transmitted by an actuator."""
    packet = UTETeachInPacket(
        PACKET.RADIO_ERP1,
        data=[
            RORG.UTE, flags, channels, manufacturer & 0xFF,
            (manufacturer >> 8) & 0x07, eep_type, 0x01, 0xD2, *sender, 0,
        ],
        optional=[3, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0],
    )
    packet.received = datetime.now(UTC) - timedelta(milliseconds=age_ms)
    return packet


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class UteRequestFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory()
        self.hass = HomeAssistant(self.tmp.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.flows = []

    async def asyncTearDown(self):
        for flow in self.flows:
            flow.async_remove()
            if flow._ute_discovery_task:
                with contextlib.suppress(asyncio.CancelledError):
                    await flow._ute_discovery_task
        await self.hass.async_stop(force=True)
        self.tmp.cleanup()

    def new_flow(self, *, base_id=BASE_ID, devices=None):
        entry = ConfigEntry(
            data={"device": "/dev/test"}, discovery_keys=MappingProxyType({}),
            domain=DOMAIN, minor_version=1,
            options={CONF_UI_DEVICES: [] if devices is None else devices},
            source="user", subentries_data=None, title="EnOcean", unique_id=None, version=1,
        )
        entry.runtime_data = FakeDongle(base_id)
        self.hass.config_entries._entries[entry.entry_id] = entry
        flow = options_flow.EnOceanOptionsFlow()
        flow.hass, flow.handler = self.hass, entry.entry_id
        self.flows.append(flow)
        return flow, entry.runtime_data

    async def arm(self, flow, *, channel=0):
        result = await flow.async_step_ute_discover(
            {"name": "Dressing", "qr_code": "04:B3:82:DA", "channel": channel}
        )
        self.assertEqual(result["step_id"], "ute_discover_confirm")
        result = await flow.async_step_ute_discover_confirm({"ute_ready": True})
        self.assertEqual(result["type"], FlowResultType.SHOW_PROGRESS)
        return result

    async def finish(self, flow):
        await self.hass.async_block_till_done()
        result = await flow.async_step_ute_discover_progress()
        self.assertEqual(result["type"], FlowResultType.SHOW_PROGRESS_DONE)
        return await flow.async_step_ute_discover_result()

    async def test_arms_before_press_and_answers_matching_request_only(self):
        flow, dongle = self.new_flow()
        await self.arm(flow)
        self.assertEqual(dongle.sent, [], "arming never sends a blind UTE")
        flow._ute_request_received(ute_request())
        self.assertEqual(len(dongle.sent), 1)
        answer = dongle.sent[0]
        self.assertEqual(answer.data, [RORG.UTE, 0x91, 0, 0x46, 0, 0x12, 1, 0xD2, *BASE_ID, 0])
        self.assertEqual(answer.optional, [3, *TARGET_ID, 0xFF, 0])
        dongle.written_callbacks[0]()
        dongle.response_callbacks[0](True)
        result = await self.finish(flow)
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        row = result["data"][CONF_UI_DEVICES][0]
        self.assertEqual(row["radio_metadata"]["eep"], "D2-01-12")
        self.assertEqual(row["radio_metadata"]["evidence"], "exact")

    async def test_manual_placeholder_is_promoted_without_duplicate(self):
        placeholder = UI_DEVICE_SCHEMA(
            {
                "id": TARGET_ID,
                "platform": "switch",
                "name": "Dressing",
                "channel": 0,
                "switch_type": "default",
                "radio_metadata": {
                    "sender_id": TARGET_ID,
                    "eep": "D2-01-08",
                    "eep_source": "manual",
                    "evidence": "manual",
                    "manufacturer_id": 0x046,
                    "support": "manual",
                },
            }
        )
        flow, dongle = self.new_flow(devices=[placeholder])
        await self.arm(flow)
        flow._ute_request_received(ute_request(eep_type=0x12))
        self.assertEqual(len(dongle.sent), 1)
        dongle.written_callbacks[0]()
        dongle.response_callbacks[0](True)
        result = await self.finish(flow)
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        rows = result["data"][CONF_UI_DEVICES]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Dressing")
        self.assertEqual(rows[0]["radio_metadata"]["eep"], "D2-01-12")
        self.assertEqual(rows[0]["radio_metadata"]["evidence"], "exact")

    async def test_radio_declared_d2_01_08_is_echoed_not_replaced(self):
        flow, dongle = self.new_flow()
        await self.arm(flow)
        flow._ute_request_received(ute_request(eep_type=0x08))
        self.assertEqual(dongle.sent[0].data[5], 0x08)

    async def test_selected_individual_channel_and_all_channel_request_are_answered(self):
        flow, dongle = self.new_flow()
        await self.arm(flow, channel=1)
        flow._ute_request_received(ute_request(channels=1))
        self.assertEqual(dongle.sent[0].data[2], 1)

        flow, dongle = self.new_flow()
        await self.arm(flow, channel=1)
        flow._ute_request_received(ute_request(channels=0xFF))
        self.assertEqual(dongle.sent[0].data[2], 0xFF)

    async def test_wrong_sender_flags_manufacturer_and_individual_channel_are_silent(self):
        for packet in (
            ute_request(sender=OTHER_ID), ute_request(flags=0xA0),
            ute_request(manufacturer=0x047), ute_request(channels=1),
        ):
            flow, dongle = self.new_flow()
            await self.arm(flow)
            flow._ute_request_received(packet)
            self.assertEqual(dongle.sent, [])
            flow.async_remove()

    async def test_late_packet_is_not_answered(self):
        flow, dongle = self.new_flow()
        await self.arm(flow)
        flow._ute_request_received(ute_request(age_ms=501))
        self.assertEqual(dongle.sent, [])
        result = await self.finish(flow)
        self.assertEqual(result["reason"], "ute_discovery_deadline_missed")

    async def test_radio_probe_is_passive_and_classifies_target_ute(self):
        flow, dongle = self.new_flow()
        result = await flow.async_step_radio_probe({"qr_code": "04:B3:82:DA"})
        self.assertEqual(result["step_id"], "radio_probe_confirm")
        result = await flow.async_step_radio_probe_confirm({"ute_ready": True})
        self.assertEqual(result["type"], FlowResultType.SHOW_PROGRESS)
        flow._radio_probe_packet_received(ute_request())
        self.assertEqual(dongle.sent, [], "passive probe must never transmit")
        await self.hass.async_block_till_done()
        result = await flow.async_step_radio_probe_progress()
        self.assertEqual(result["type"], FlowResultType.SHOW_PROGRESS_DONE)
        result = await flow.async_step_radio_probe_result()
        self.assertEqual(result["reason"], "radio_probe_target_ute")

    async def test_radio_probe_ignores_another_sender(self):
        flow, dongle = self.new_flow()
        await flow.async_step_radio_probe({"qr_code": "04:B3:82:DA"})
        await flow.async_step_radio_probe_confirm({"ute_ready": True})
        flow._radio_probe_packet_received(ute_request(sender=OTHER_ID))
        self.assertIsNone(flow._radio_probe_result)
        self.assertEqual(dongle.sent, [])

    async def test_transport_failure_and_stale_callback_never_persist(self):
        flow, dongle = self.new_flow()
        await self.arm(flow)
        flow._ute_request_received(ute_request())
        response = dongle.response_callbacks[0]
        flow.async_remove()
        response(True)
        await self.hass.async_block_till_done()
        self.assertEqual(flow.config_entry.options[CONF_UI_DEVICES], [])


if __name__ == "__main__":
    unittest.main()

"""Regression coverage for request-driven, bounded UTE commissioning.

The fake dongle below delivers its callbacks the way ``EnOceanDongle`` does in
production: the serial worker reports the physical write and the ESP3 result
through ``loop.call_soon_threadsafe``, so both land on the event loop *after*
the flow has already closed its listener and queued the response. Tests that
need to control that ordering ask for a ``manual`` dongle instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import unittest
from datetime import UTC, datetime, timedelta
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
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.enocean_custom import options_flow
    from custom_components.enocean_custom.const import DOMAIN, SIGNAL_RECEIVE_MESSAGE
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RORG,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import (
        UTETeachInPacket,
    )
    from custom_components.enocean_custom.learn import get_learn_manager
    from custom_components.enocean_custom.schema import (
        CONF_UI_DEVICES,
        UI_DEVICE_SCHEMA,
    )
    from custom_components.enocean_custom.ubiwizz import UBID1507C

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    HA_AVAILABLE = False

TARGET_ID = [0x04, 0xB3, 0x82, 0xDA]
OTHER_ID = [0x04, 0xB3, 0x82, 0xDB]
BASE_ID = [0xA1, 0xB2, 0xC3, 0xD4]


class FakeDongle:
    """Capture outbound packets and deliver callbacks like ``EnOceanDongle``.

    ``written_callback`` and ``response_callback`` are scheduled on the event
    loop (``delay`` seconds apart, like the serial worker's later
    ``call_soon_threadsafe`` hand-offs) instead of being run inside ``send``.
    With ``manual=True`` nothing is delivered: the test fires the captured
    callbacks itself.
    """

    def __init__(self, base_id=BASE_ID, *, delay=0.0, manual=False):
        self.base_id = base_id
        self.sent = []
        self.response_callbacks = []
        self.written_callbacks = []
        self.queue_result = True
        self.transport_accepted = True
        self.delay = delay
        self.manual = manual

    def send(self, packet, response_callback=None, written_callback=None):
        self.sent.append(packet)
        self.response_callbacks.append(response_callback)
        self.written_callbacks.append(written_callback)
        if not self.queue_result:
            return False
        if not self.manual:
            loop = asyncio.get_running_loop()
            if written_callback is not None:
                loop.call_later(self.delay, written_callback)
            if response_callback is not None:
                loop.call_later(
                    self.delay * 2, response_callback, self.transport_accepted
                )
        return True


def ute_request(
    *,
    sender=TARGET_ID,
    eep_type=0x12,
    manufacturer=0x046,
    channels=0,
    flags=0x80,
    age_ms=0,
):
    """Build one inbound bidirectional UTE CMD0 as transmitted by an actuator."""
    packet = UTETeachInPacket(
        PACKET.RADIO_ERP1,
        data=[
            RORG.UTE,
            flags,
            channels,
            manufacturer & 0xFF,
            (manufacturer >> 8) & 0x07,
            eep_type,
            0x01,
            0xD2,
            *sender,
            0,
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

    def new_flow(self, *, base_id=BASE_ID, devices=None, delay=0.0, manual=False):
        entry = ConfigEntry(
            data={"device": "/dev/test"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=1,
            options={CONF_UI_DEVICES: [] if devices is None else devices},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        entry.runtime_data = FakeDongle(base_id, delay=delay, manual=manual)
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
        # Hand control to the loop: the progress task is now blocked on the
        # listener, exactly where it waits when the physical PRESS happens.
        await asyncio.sleep(0)
        return result

    def module_sends(self, packet):
        """Inject one telegram the way the serial worker dispatches it."""
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, packet)

    async def finish(self, flow):
        await self.hass.async_block_till_done()
        result = await flow.async_step_ute_discover_progress()
        self.assertEqual(result["type"], FlowResultType.SHOW_PROGRESS_DONE)
        return await flow.async_step_ute_discover_result()

    async def test_arms_before_press_and_answers_matching_request_only(self):
        flow, dongle = self.new_flow()
        await self.arm(flow)
        self.assertEqual(dongle.sent, [], "arming never sends a blind UTE")
        self.module_sends(ute_request())
        self.assertEqual(len(dongle.sent), 1)
        answer = dongle.sent[0]
        self.assertEqual(
            answer.data, [RORG.UTE, 0x91, 0, 0x46, 0, 0x12, 1, 0xD2, *BASE_ID, 0]
        )
        self.assertEqual(answer.optional, [3, *TARGET_ID, 0xFF, 0])
        self.assertIsNone(flow._ute_discovery_outcome, "no verdict before transport")
        result = await self.finish(flow)
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        row = result["data"][CONF_UI_DEVICES][0]
        self.assertEqual(row["radio_metadata"]["eep"], "D2-01-12")
        self.assertEqual(row["radio_metadata"]["evidence"], "exact")

    async def test_asynchronous_dongle_callbacks_do_not_end_as_no_request(self):
        """Regression: the closed listener wakes the waiter before the callbacks.

        The flow stops the listener, then queues the response; the dongle only
        confirms the write and the ESP3 result on later loop iterations. The
        waiter must keep waiting for that transport result instead of
        concluding ``no_request`` and cancelling the response token.
        """
        for delay in (0.0, 0.001, 0.02):
            with self.subTest(callback_delay=delay):
                flow, dongle = self.new_flow(delay=delay)
                await self.arm(flow)
                self.module_sends(ute_request())
                self.assertEqual(len(dongle.sent), 1)
                await flow._ute_discovery_task
                self.assertEqual(flow._ute_discovery_outcome, "success")
                result = await self.finish(flow)
                self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
                self.assertEqual(result["data"][CONF_UI_DEVICES][0]["id"], TARGET_ID)
                self.hass.config_entries.async_update_entry(
                    flow.config_entry, options={CONF_UI_DEVICES: []}
                )

    async def test_window_closing_without_a_request_is_no_request(self):
        flow, dongle = self.new_flow()
        await self.arm(flow)
        get_learn_manager(self.hass).stop()  # the listening window expired
        result = await self.finish(flow)
        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(result["reason"], "ute_discovery_no_request")
        self.assertEqual(dongle.sent, [])

    async def test_rejected_transport_result_is_reported_not_persisted(self):
        flow, dongle = self.new_flow()
        dongle.transport_accepted = False
        await self.arm(flow)
        self.module_sends(ute_request())
        result = await self.finish(flow)
        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(result["reason"], "ute_discovery_transport_rejected")
        self.assertEqual(flow.config_entry.options[CONF_UI_DEVICES], [])

    async def test_missing_transport_confirmation_times_out_without_persisting(self):
        for deliver_write, reason in (
            (True, "ute_discovery_timeout"),
            (False, "ute_discovery_write_unconfirmed"),
        ):
            with self.subTest(written=deliver_write):
                flow, dongle = self.new_flow(manual=True)
                with patch.object(options_flow, "UTE_DISCOVERY_TIMEOUT", 0.01):
                    await self.arm(flow)
                    self.module_sends(ute_request())
                    self.assertEqual(len(dongle.sent), 1)
                    if deliver_write:
                        dongle.written_callbacks[0]()
                    result = await self.finish(flow)
                self.assertEqual(result["type"], FlowResultType.ABORT)
                self.assertEqual(result["reason"], reason)
                self.assertEqual(flow.config_entry.options[CONF_UI_DEVICES], [])

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
        self.module_sends(ute_request(eep_type=0x12))
        self.assertEqual(len(dongle.sent), 1)
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
        self.module_sends(ute_request(eep_type=0x08))
        self.assertEqual(dongle.sent[0].data[5], 0x08)

    async def test_selected_individual_channel_and_all_channel_request_are_answered(
        self,
    ):
        flow, dongle = self.new_flow()
        await self.arm(flow, channel=1)
        self.module_sends(ute_request(channels=1))
        self.assertEqual(dongle.sent[0].data[2], 1)

        flow, dongle = self.new_flow()
        await self.arm(flow, channel=1)
        self.module_sends(ute_request(channels=0xFF))
        self.assertEqual(dongle.sent[0].data[2], 0xFF)

    async def test_documented_channel_count_request_is_answered_for_each_output(
        self,
    ):
        """The field capture carries DB5 = 0x02 (channel count), not an output."""
        self.assertEqual(UBID1507C.ute_number_of_channels, 2)
        for channel in UBID1507C.output_channels:
            with self.subTest(channel=channel):
                flow, dongle = self.new_flow()
                await self.arm(flow, channel=channel)
                self.module_sends(ute_request(channels=0x02))
                self.assertEqual(len(dongle.sent), 1)
                # The response echoes the request's DB5 and keeps the exact EEP.
                self.assertEqual(
                    dongle.sent[0].data,
                    [RORG.UTE, 0x91, 0x02, 0x46, 0, 0x12, 1, 0xD2, *BASE_ID, 0],
                )
                result = await self.finish(flow)
                self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
                row = result["data"][CONF_UI_DEVICES][-1]
                self.assertEqual(row["channel"], channel, "DB5 never picks the row")
                self.assertEqual(row["radio_metadata"]["eep"], "D2-01-12")
                self.hass.config_entries.async_update_entry(
                    flow.config_entry, options={CONF_UI_DEVICES: []}
                )

    async def test_undocumented_channel_values_are_rejected(self):
        for channels in (0x03, 0x10, 0xFE):
            with self.subTest(channels=channels):
                flow, dongle = self.new_flow()
                await self.arm(flow)
                self.module_sends(ute_request(channels=channels))
                self.assertEqual(dongle.sent, [])
                self.assertIsNone(flow._ute_discovery_outcome)
                flow.async_remove()

    async def test_wrong_sender_flags_manufacturer_and_individual_channel_are_silent(
        self,
    ):
        for packet in (
            ute_request(sender=OTHER_ID),
            ute_request(flags=0xA0),
            ute_request(manufacturer=0x047),
            ute_request(channels=1),
        ):
            flow, dongle = self.new_flow()
            await self.arm(flow)
            self.module_sends(packet)
            self.assertEqual(dongle.sent, [])
            flow.async_remove()

    async def test_late_packet_is_not_answered(self):
        flow, dongle = self.new_flow()
        await self.arm(flow)
        self.module_sends(ute_request(age_ms=501))
        self.assertEqual(dongle.sent, [])
        result = await self.finish(flow)
        self.assertEqual(result["reason"], "ute_discovery_deadline_missed")

    async def test_radio_probe_is_passive_and_classifies_target_ute(self):
        flow, dongle = self.new_flow()
        result = await flow.async_step_radio_probe({"qr_code": "04:B3:82:DA"})
        self.assertEqual(result["step_id"], "radio_probe_confirm")
        result = await flow.async_step_radio_probe_confirm({"ute_ready": True})
        self.assertEqual(result["type"], FlowResultType.SHOW_PROGRESS)
        self.module_sends(ute_request())
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
        self.module_sends(ute_request(sender=OTHER_ID))
        self.assertIsNone(flow._radio_probe_result)
        self.assertEqual(dongle.sent, [])

    async def test_transport_failure_and_stale_callback_never_persist(self):
        flow, dongle = self.new_flow(manual=True)
        await self.arm(flow)
        self.module_sends(ute_request())
        written, response = dongle.written_callbacks[0], dongle.response_callbacks[0]
        flow.async_remove()
        written()
        response(True)
        await self.hass.async_block_till_done()
        self.assertEqual(flow.config_entry.options[CONF_UI_DEVICES], [])


if __name__ == "__main__":
    unittest.main()

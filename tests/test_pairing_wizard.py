"""Safety regression tests for guided actuator commissioning."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # Bare unittest collection intentionally has no Home Assistant dependency.
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.const import CONF_ENTITY_ID
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.enocean_custom import options_flow
    from custom_components.enocean_custom.const import (
        DOMAIN,
        SERVICE_SEND_TEACH_IN,
        SIGNAL_RECEIVE_MESSAGE,
    )
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RORG,
    )
    from custom_components.enocean_custom.schema import (
        CONF_RADIO_METADATA,
        CONF_UI_DEVICES,
        UI_DEVICE_SCHEMA,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - bare CI environment
    HA_AVAILABLE = False


class FakeDongle:
    """Capture packets and expose response callbacks under test control."""

    def __init__(self) -> None:
        self.sent = []
        self.callbacks = []
        self.queue_result = True

    def send(self, packet, response_callback=None):
        self.sent.append(packet)
        self.callbacks.append(response_callback)
        return self.queue_result


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class PairingWizardTests(unittest.IsolatedAsyncioTestCase):
    """Prove the D2 flow's radio, causality, persistence, and cleanup gates."""

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
            task = flow._pairing_task
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _entry(self, rows=()):
        entry = ConfigEntry(
            data={"device": "/dev/test-pairing"},
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
        entry.runtime_data = FakeDongle()
        self.hass.config_entries._entries[entry.entry_id] = entry
        return entry

    def _flow(self, entry):
        flow = options_flow.EnOceanOptionsFlow()
        flow.hass = self.hass
        flow.handler = entry.entry_id
        self._flows.append(flow)
        return flow

    @staticmethod
    def _row(sender="11:22:33:44", channel=0, name="Relay"):
        sender_bytes = [int(part, 16) for part in sender.split(":")]
        return UI_DEVICE_SCHEMA(
            {
                "id": sender_bytes,
                "platform": "switch",
                "name": name,
                "channel": channel,
                "switch_type": "default",
                CONF_RADIO_METADATA: {
                    "sender_id": sender_bytes,
                    "eep": "D2-01-12",
                    "evidence": "exact",
                },
            }
        )

    @staticmethod
    def _status(sender="11:22:33:44", channel=0, output=50):
        sender_bytes = [int(part, 16) for part in sender.split(":")]
        return SimpleNamespace(
            sender_int=int(sender.replace(":", ""), 16),
            data=[RORG.VLD, 0x04, channel, output, *sender_bytes, 0],
        )

    async def _new_relay(self, channel=0):
        entry = self._entry()
        flow = self._flow(entry)
        await flow.async_step_pair_actuator({"qr_code": "11:22:33:44"})
        await flow.async_step_pair_actuator_type(
            {"name": "Relay", "actuator_type": "relay_d2"}
        )
        result = await flow.async_step_pair_actuator_details({"channel": channel})
        self.assertEqual(result["step_id"], "pair_actuator_instructions")
        return flow, entry

    async def _new_dimmer(self):
        entry = self._entry()
        flow = self._flow(entry)
        await flow.async_step_pair_actuator({"qr_code": "31:32:33:34"})
        await flow.async_step_pair_actuator_type(
            {"name": "Dimmer", "actuator_type": "dimmer_4bs"}
        )

        def _store_options(config_entry, *, options):
            object.__setattr__(config_entry, "options", MappingProxyType(options))

        with patch.object(
            self.hass.config_entries,
            "async_update_entry",
            side_effect=_store_options,
        ):
            result = await flow.async_step_pair_actuator_details(
                {"channel": 0, "sender_id": "05:9F:89:34"}
            )
        self.assertEqual(result["step_id"], "pair_actuator_instructions")
        device = entry.options[CONF_UI_DEVICES][0]
        entity = er.async_get(self.hass).async_get_or_create(
            device["platform"], DOMAIN, options_flow._unique_id_for(device)
        )
        return flow, entity.entity_id

    async def _start(self, flow):
        with (
            patch.object(options_flow, "PAIRING_TIMEOUT", 0.3),
            patch.object(options_flow, "PAIRING_RELAY_INTERVAL", 0.03),
        ):
            result = await flow.async_step_pair_actuator_instructions({})
            await asyncio.sleep(0)
        self.assertEqual(result["type"], FlowResultType.SHOW_PROGRESS)

    async def test_exact_directed_d2_bytes_and_no_early_options_write(self):
        flow, entry = await self._new_relay(channel=1)
        self.assertEqual(entry.options[CONF_UI_DEVICES], [])
        with patch.object(
            self.hass.config_entries,
            "async_update_entry",
            wraps=self.hass.config_entries.async_update_entry,
        ) as update:
            await self._start(flow)
            packet = entry.runtime_data.sent[0]
            self.assertEqual(packet.packet_type, PACKET.RADIO_ERP1)
            self.assertEqual(packet.data, [RORG.VLD, 1, 1, 100, 0, 0, 0, 0, 0])
            self.assertEqual(packet.optional, [3, 0x11, 0x22, 0x33, 0x44, 0xFF, 0])
            update.assert_not_called()
        flow.async_remove()

    async def test_esp3_ok_then_later_matching_on_status_required(self):
        flow, entry = await self._new_relay()
        await self._start(flow)
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, self._status())
        await asyncio.sleep(0)
        self.assertFalse(flow._pairing_task.done())
        entry.runtime_data.callbacks[0](True)
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, self._status())
        await asyncio.wait_for(flow._pairing_task, 1)
        self.assertEqual(flow._pairing_outcome, "success")

    async def test_rejected_response_and_wrong_feedback_never_confirm(self):
        flow, entry = await self._new_relay()
        await self._start(flow)
        entry.runtime_data.callbacks[0](False)
        for packet in (
            self._status("AA:BB:CC:DD"),
            self._status(channel=1),
            self._status(output=0),
        ):
            async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, packet)
        await asyncio.sleep(0.04)
        self.assertNotEqual(flow._pairing_outcome, "success")
        flow.async_remove()

    async def test_success_adds_one_final_default_row_preserving_metadata(self):
        flow, entry = await self._new_relay()
        await self._start(flow)
        entry.runtime_data.callbacks[0](True)
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, self._status())
        await flow._pairing_task
        await flow.async_step_pair_actuator_progress()
        result = await flow.async_step_pair_relay_success({})
        rows = result["data"][CONF_UI_DEVICES]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["switch_type"], "default")
        self.assertEqual(
            rows[0][CONF_RADIO_METADATA], flow._pairing_device[CONF_RADIO_METADATA]
        )
        self.assertEqual(entry.options[CONF_UI_DEVICES], [])

    async def test_existing_commission_preserves_options_and_registry_identity(self):
        row = self._row()
        entry = self._entry([row])
        registry = er.async_get(self.hass)
        entity = registry.async_get_or_create(
            "switch",
            DOMAIN,
            options_flow._unique_id_for(row),
            suggested_object_id="custom",
        )
        registry.async_update_entity(
            entity.entity_id, name="My name", area_id="kitchen"
        )
        before = dict(entry.options)
        flow = self._flow(entry)
        selected = await flow.async_step_commission_existing(
            {"device": options_flow._unique_id_for(row)}
        )
        self.assertEqual(selected["step_id"], "pair_actuator_instructions")
        await self._start(flow)
        entry.runtime_data.callbacks[0](True)
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, self._status())
        await flow._pairing_task
        result = await flow.async_step_pair_relay_success({})
        self.assertEqual(result["data"], before)
        current = registry.async_get(entity.entity_id)
        self.assertEqual(
            (current.entity_id, current.name, current.area_id),
            (entity.entity_id, "My name", "kitchen"),
        )

    async def test_concurrent_existing_deletion_never_resurrects(self):
        entry = self._entry([self._row()])
        flow = self._flow(entry)
        await flow.async_step_commission_existing(
            {"device": options_flow._unique_id_for(entry.options[CONF_UI_DEVICES][0])}
        )
        object.__setattr__(entry, "options", MappingProxyType({CONF_UI_DEVICES: []}))
        result = flow._finish_pairing()
        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(entry.options[CONF_UI_DEVICES], [])

    async def test_new_finalization_rechecks_concurrent_identity_collision(self):
        flow, entry = await self._new_relay()
        object.__setattr__(
            entry,
            "options",
            MappingProxyType({CONF_UI_DEVICES: [self._row(name="Other flow")]}),
        )
        result = flow._finish_pairing()
        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(entry.options[CONF_UI_DEVICES][0]["name"], "Other flow")

    async def test_timeout_actions_new_and_existing(self):
        for existing in (False, True):
            for action in ("retry", "keep", "delete"):
                entry = self._entry([self._row()] if existing else [])
                flow = self._flow(entry)
                if existing:
                    await flow.async_step_commission_existing(
                        {
                            "device": options_flow._unique_id_for(
                                entry.options[CONF_UI_DEVICES][0]
                            )
                        }
                    )
                else:
                    await flow.async_step_pair_actuator({"qr_code": "11:22:33:44"})
                    await flow.async_step_pair_actuator_type(
                        {"name": "Relay", "actuator_type": "relay_d2"}
                    )
                    await flow.async_step_pair_actuator_details({"channel": 0})
                result = await flow.async_step_pair_actuator_failure(
                    {"failure_action": action}
                )
                if action == "retry":
                    self.assertEqual(result["step_id"], "pair_actuator_instructions")
                elif existing or action == "delete":
                    self.assertEqual(result["data"], entry.options)
                else:
                    self.assertEqual(len(result["data"][CONF_UI_DEVICES]), 1)

    async def test_flow_removal_cancels_listener_task_and_future_sends(self):
        flow, entry = await self._new_relay()
        await self._start(flow)
        task = flow._pairing_task
        flow.async_remove()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        count = len(entry.runtime_data.sent)
        await asyncio.sleep(0.05)
        self.assertTrue(task.cancelled())
        self.assertEqual(len(entry.runtime_data.sent), count)
        self.assertIsNone(flow._pairing_unsubscribe)

    async def test_dimmer_three_accepted_teach_ins_end_only_in_honest_screen(self):
        calls = []

        async def _accept(call):
            calls.append(call.data[CONF_ENTITY_ID])

        self.hass.services.async_register(DOMAIN, SERVICE_SEND_TEACH_IN, _accept)
        flow, entity_id = await self._new_dimmer()
        with (
            patch.object(options_flow, "PAIRING_TIMEOUT", 0.5),
            patch.object(options_flow, "PAIRING_DIMMER_INTERVAL", 0.005),
            patch.object(options_flow, "PAIRING_DIMMER_ATTEMPTS", 3),
        ):
            await flow.async_step_pair_actuator_instructions({})
            await asyncio.wait_for(flow._pairing_task, 1)
            done = await flow.async_step_pair_actuator_progress()
            screen = await flow.async_step_pair_dimmer_success(None)
        self.assertEqual(calls, [entity_id, entity_id, entity_id])
        self.assertEqual(done["step_id"], "pair_dimmer_success")
        self.assertEqual(screen["type"], FlowResultType.FORM)
        self.assertEqual(screen["step_id"], "pair_dimmer_success")

    async def test_dimmer_rejected_teach_in_never_counts_as_sent_or_success(self):
        calls = 0

        async def _reject(_call):
            nonlocal calls
            calls += 1
            raise HomeAssistantError("rejected by dongle")

        self.hass.services.async_register(DOMAIN, SERVICE_SEND_TEACH_IN, _reject)
        flow, _entity_id = await self._new_dimmer()
        with (
            patch.object(options_flow, "PAIRING_TIMEOUT", 0.04),
            patch.object(options_flow, "PAIRING_DIMMER_INTERVAL", 0.005),
        ):
            await flow.async_step_pair_actuator_instructions({})
            await asyncio.wait_for(flow._pairing_task, 1)
            done = await flow.async_step_pair_actuator_progress()
        self.assertGreater(calls, 0)
        self.assertEqual(flow._pairing_outcome, "timeout")
        self.assertEqual(done["step_id"], "pair_actuator_failure")

    async def test_dimmer_blocking_teach_in_is_bounded_by_pairing_timeout(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def _block(_call):
            started.set()
            await release.wait()

        self.hass.services.async_register(DOMAIN, SERVICE_SEND_TEACH_IN, _block)
        flow, _entity_id = await self._new_dimmer()
        try:
            with patch.object(options_flow, "PAIRING_TIMEOUT", 0.04):
                await flow.async_step_pair_actuator_instructions({})
                await asyncio.wait_for(started.wait(), 1)
                await asyncio.wait_for(flow._pairing_task, 1)
            self.assertEqual(flow._pairing_outcome, "timeout")
        finally:
            release.set()

    async def test_abandon_dimmer_cancels_loop_without_later_service_calls(self):
        first_call = asyncio.Event()
        calls = []

        async def _accept(call):
            calls.append(call.data[CONF_ENTITY_ID])
            first_call.set()

        self.hass.services.async_register(DOMAIN, SERVICE_SEND_TEACH_IN, _accept)
        flow, _entity_id = await self._new_dimmer()
        with (
            patch.object(options_flow, "PAIRING_TIMEOUT", 1),
            patch.object(options_flow, "PAIRING_DIMMER_INTERVAL", 0.05),
        ):
            await flow.async_step_pair_actuator_instructions({})
            await asyncio.wait_for(first_call.wait(), 1)
            task = flow._pairing_task
            flow.async_remove()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            calls_at_remove = len(calls)
            await asyncio.sleep(0.08)
        self.assertTrue(task.cancelled())
        self.assertEqual(len(calls), calls_at_remove)

    async def test_default_ui_channel_31_accepted_32_rejected_rps_still_0_or_1(self):
        entry = self._entry()
        for channel, accepted in ((31, True), (32, False)):
            flow = self._flow(entry)
            flow._captured_id = [1, 2, 3, channel]
            flow._pending_platform = "switch"
            flow._pending_name = "Switch"
            result = await flow.async_step_device_details(
                {"channel": channel, "switch_type": "default"}
            )
            self.assertEqual(result["type"] == FlowResultType.CREATE_ENTRY, accepted)
        flow = self._flow(entry)
        flow._captured_id = [5, 6, 7, 8]
        flow._pending_platform = "switch"
        flow._pending_name = "RPS"
        self.assertEqual(
            (
                await flow.async_step_device_details(
                    {"channel": 1, "switch_type": "RPS"}
                )
            )["type"],
            FlowResultType.CREATE_ENTRY,
        )
        self.assertEqual(
            (
                await flow.async_step_device_details(
                    {"channel": 2, "switch_type": "RPS"}
                )
            )["errors"]["channel"],
            "invalid_channel_rps",
        )


if __name__ == "__main__":
    unittest.main()

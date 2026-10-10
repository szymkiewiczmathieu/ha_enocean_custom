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
    import voluptuous as vol
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.const import CONF_ENTITY_ID
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.enocean_custom import options_flow, switch
    from custom_components.enocean_custom.const import (
        DATA_ENOCEAN,
        DOMAIN,
        ENOCEAN_DONGLE,
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
    from custom_components.enocean_custom.switch import EnOceanSwitch

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - bare CI environment
    HA_AVAILABLE = False


class FakeDongle:
    """Capture packets and expose response callbacks under test control."""

    def __init__(self) -> None:
        self.sent = []
        self.callbacks = []
        self.queue_result = True
        self.base_id = [0xA1, 0xB2, 0xC3, 0xD4]

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
                    "eep_source": "radio_declared",
                    "evidence": "exact",
                },
            }
        )

    @staticmethod
    def _status(sender="11:22:33:44", channel=0, output=100):
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

    async def _identify_existing(self, flow, row, identity=None):
        """Select one existing row and bind the physical ID before any radio."""
        selected = await flow.async_step_commission_existing(
            {"device": options_flow._unique_id_for(row)}
        )
        self.assertEqual(selected["step_id"], "commission_existing_identity")
        return await flow.async_step_commission_existing_identity(
            {"qr_code": identity or ":".join(f"{byte:02X}" for byte in row["id"])}
        )

    async def test_asp_commissioning_is_explicitly_refused(self):
        flow, _entry = await self._new_relay()
        flow._pairing_device[CONF_RADIO_METADATA]["eep"] = "D2-01-0A"
        result = await flow.async_step_pair_actuator_instructions()
        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(result["reason"], "commissioning_asp_not_supported")
        self.assertEqual(flow._pairing_task, None)

    async def test_asp_commissioning_is_explicitly_refused_for_lowercase_eep(self):
        flow, _entry = await self._new_relay()
        flow._pairing_device[CONF_RADIO_METADATA]["eep"] = "d2-01-0a"
        result = await flow.async_step_pair_actuator_instructions()
        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(result["reason"], "commissioning_asp_not_supported")
        self.assertIsNone(flow._pairing_task)

    async def test_direct_qr_identification_is_radio_silent(self):
        entry = self._entry()
        flow = self._flow(entry)

        result = await flow.async_step_qr_code({"qr_code": "11:22:33:44"})

        self.assertEqual(result["step_id"], "device_form")
        self.assertEqual(entry.runtime_data.sent, [])

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
            self.assertEqual(
                packet.data,
                [RORG.VLD, 1, 1, 100, 0xA1, 0xB2, 0xC3, 0xD4, 0],
            )
            self.assertEqual(packet.optional, [3, 0x11, 0x22, 0x33, 0x44, 0xFF, 0])
            update.assert_not_called()
        flow.async_remove()

    async def test_relay_refuses_to_transmit_without_a_resolved_dongle_base_id(self):
        flow, entry = await self._new_relay()
        entry.runtime_data.base_id = None
        await self._start(flow)
        await asyncio.wait_for(flow._pairing_task, 1)
        self.assertEqual(flow._pairing_outcome, "base_id_unavailable")
        self.assertEqual(entry.runtime_data.sent, [])

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

    async def test_relay_feedback_output_must_match_the_directed_on_command(self):
        flow, entry = await self._new_relay()
        await self._start(flow)
        entry.runtime_data.callbacks[0](True)
        async_dispatcher_send(
            self.hass, SIGNAL_RECEIVE_MESSAGE, self._status(output=50)
        )
        await asyncio.sleep(0)
        self.assertFalse(flow._pairing_task.done())
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, self._status())
        await asyncio.wait_for(flow._pairing_task, 1)
        self.assertEqual(flow._pairing_outcome, "success")

    async def test_existing_default_switch_uses_base_id_and_refuses_unknown_sender(
        self,
    ):
        entry = self._entry()
        self.hass.data[DATA_ENOCEAN] = {ENOCEAN_DONGLE: entry.runtime_data}
        entity = EnOceanSwitch([0x11, 0x22, 0x33, 0x44], "Relay", 1, "default")
        entity.hass = self.hass
        entity.turn_on()
        self.assertEqual(
            entry.runtime_data.sent[0].data,
            [RORG.VLD, 1, 1, 100, 0xA1, 0xB2, 0xC3, 0xD4, 0],
        )
        entry.runtime_data.base_id = None
        entity.turn_off()
        self.assertEqual(len(entry.runtime_data.sent), 1)

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
        self.assertEqual(rows[0]["actuator_profile"], "ubiwizz_ubid1507c")
        self.assertEqual(
            rows[0][CONF_RADIO_METADATA], flow._pairing_device[CONF_RADIO_METADATA]
        )
        self.assertEqual(entry.options[CONF_UI_DEVICES], [])

    async def test_existing_commission_rejects_incompatible_or_unbound_rows(self):
        eligible = self._row()
        invalid_channel = self._row(channel=2, name="Out of range")
        incompatible = self._row(sender="22:33:44:55", name="Incompatible")
        incompatible[CONF_RADIO_METADATA] = {
            **incompatible[CONF_RADIO_METADATA],
            "eep": "A5-12-01",
            "eep_source": "manual",
            "evidence": "manual",
        }
        unbound = self._row(sender="33:44:55:66", name="Unbound evidence")
        unbound[CONF_RADIO_METADATA].pop("sender_id")
        entry = self._entry([eligible, invalid_channel, incompatible, unbound])
        flow = self._flow(entry)

        for device in (invalid_channel, incompatible, unbound):
            result = await flow.async_step_commission_existing(
                {"device": options_flow._unique_id_for(device)}
            )
            self.assertEqual(result["type"], FlowResultType.ABORT)
            self.assertEqual(result["reason"], "no_commissionable_devices")

        selected = await self._identify_existing(flow, eligible)
        self.assertEqual(selected["step_id"], "pair_actuator_instructions")

    async def test_migrated_existing_relay_requires_exact_physical_identity(self):
        """A YAML-migrated default switch is not silently excluded or trusted."""
        migrated = UI_DEVICE_SCHEMA(
            {
                "id": [0x11, 0x22, 0x33, 0x44],
                "platform": "switch",
                "name": "Migrated relay",
                "channel": 0,
                "switch_type": "default",
            }
        )
        entry = self._entry([migrated])
        flow = self._flow(entry)
        before = dict(entry.options)

        shown = await flow.async_step_commission_existing()
        self.assertEqual(shown["type"], FlowResultType.FORM)
        self.assertEqual(shown["step_id"], "commission_existing")
        selected = await flow.async_step_commission_existing(
            {"device": options_flow._unique_id_for(migrated)}
        )
        self.assertEqual(selected["step_id"], "commission_existing_identity")
        self.assertEqual(entry.runtime_data.sent, [])
        wrong = await flow.async_step_commission_existing_identity(
            {"qr_code": "AA:BB:CC:DD"}
        )
        self.assertEqual(wrong["errors"]["qr_code"], "commissioning_identity_mismatch")
        self.assertEqual(entry.runtime_data.sent, [])
        self.assertEqual(entry.options, before)

        confirm = await self._identify_existing(flow, migrated)
        self.assertEqual(confirm["step_id"], "commission_existing_confirm")
        declined = await flow.async_step_commission_existing_confirm(
            {"confirm_relay_d2": False}
        )
        self.assertEqual(declined["reason"], "commissioning_relay_unconfirmed")
        self.assertEqual(entry.runtime_data.sent, [])
        self.assertEqual(entry.options, before)

    async def test_migrated_timeout_or_flow_close_never_persists_manual_assertion(self):
        migrated = UI_DEVICE_SCHEMA(
            {
                "id": [0x11, 0x22, 0x33, 0x44],
                "platform": "switch",
                "name": "Migrated relay",
                "channel": 0,
                "switch_type": "default",
            }
        )
        entry = self._entry([migrated])
        before = dict(entry.options)
        flow = self._flow(entry)
        confirm = await self._identify_existing(flow, migrated)
        self.assertEqual(confirm["step_id"], "commission_existing_confirm")
        await flow.async_step_commission_existing_confirm({"confirm_relay_d2": True})
        timed_out = await flow.async_step_pair_actuator_failure(
            {"failure_action": "keep"}
        )
        self.assertEqual(timed_out["data"], before)
        self.assertEqual(entry.options, before)

        second_flow = self._flow(entry)
        confirm = await self._identify_existing(second_flow, migrated)
        self.assertEqual(confirm["step_id"], "commission_existing_confirm")
        await second_flow.async_step_commission_existing_confirm(
            {"confirm_relay_d2": True}
        )
        second_flow.async_remove()
        self.assertEqual(entry.options, before)

    async def test_migrated_existing_relay_persists_only_post_proof_manual_assertion(
        self,
    ):
        """The selected migrated row keeps its HA identity and raw options fields."""
        migrated = UI_DEVICE_SCHEMA(
            {
                "id": [0x11, 0x22, 0x33, 0x44],
                "platform": "switch",
                "name": "Migrated relay",
                "channel": 1,
                "switch_type": "default",
            }
        )
        entry = self._entry([migrated])
        registry = er.async_get(self.hass)
        entity = registry.async_get_or_create(
            "switch",
            DOMAIN,
            options_flow._unique_id_for(migrated),
            suggested_object_id="kept",
        )
        registry.async_update_entity(
            entity.entity_id, name="Kept label", area_id="hall"
        )
        before = dict(entry.options)
        flow = self._flow(entry)

        confirm = await self._identify_existing(
            flow, migrated, "30S000011223344+1P0123AABBCCDD"
        )
        self.assertEqual(confirm["step_id"], "commission_existing_confirm")
        instructions = await flow.async_step_commission_existing_confirm(
            {"confirm_relay_d2": True}
        )
        self.assertEqual(instructions["step_id"], "pair_actuator_instructions")
        self.assertEqual(entry.options, before)
        await self._start(flow)
        packet = entry.runtime_data.sent[0]
        self.assertEqual(packet.data, [RORG.VLD, 1, 1, 100, 0xA1, 0xB2, 0xC3, 0xD4, 0])
        entry.runtime_data.callbacks[0](True)
        async_dispatcher_send(
            self.hass, SIGNAL_RECEIVE_MESSAGE, self._status(channel=1)
        )
        await flow._pairing_task
        result = await flow.async_step_pair_relay_success({})
        committed = result["data"][CONF_UI_DEVICES][0]
        self.assertEqual(
            {
                key: value
                for key, value in committed.items()
                if key != CONF_RADIO_METADATA
            },
            {
                key: value
                for key, value in migrated.items()
                if key != CONF_RADIO_METADATA
            },
        )
        self.assertEqual(
            committed[CONF_RADIO_METADATA],
            {
                "sender_id": [0x11, 0x22, 0x33, 0x44],
                "product_id": "0123AABBCCDD",
                "manufacturer_id": 0x123,
                "product_reference": 0xAABBCCDD,
                "eep": "D2-01-12",
                "eep_source": "manual",
                "evidence": "manual",
                "support": "manual",
            },
        )
        self.assertEqual(entry.options, before)
        current = registry.async_get(entity.entity_id)
        self.assertEqual(
            (current.entity_id, current.unique_id, current.name, current.area_id),
            (entity.entity_id, entity.unique_id, "Kept label", "hall"),
        )

    async def test_existing_product_identity_is_preserved_when_typed_id_is_bound(self):
        sender = [0x11, 0x22, 0x33, 0x44]
        product_metadata = {
            "sender_id": sender,
            "product_id": "0123AABBCCDD",
            "manufacturer_id": 0x123,
            "product_reference": 0xAABBCCDD,
            "evidence": "assisted",
            "support": "unknown",
        }
        row = UI_DEVICE_SCHEMA(
            {
                "id": sender,
                "platform": "switch",
                "name": "Existing product relay",
                "channel": 0,
                "switch_type": "default",
                CONF_RADIO_METADATA: product_metadata,
            }
        )
        entry = self._entry([row])
        flow = self._flow(entry)
        confirm = await self._identify_existing(flow, row)
        self.assertEqual(confirm["step_id"], "commission_existing_confirm")
        await flow.async_step_commission_existing_confirm({"confirm_relay_d2": True})
        await self._start(flow)
        entry.runtime_data.callbacks[0](True)
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, self._status())
        await flow._pairing_task
        result = await flow.async_step_pair_relay_success({})
        metadata = result["data"][CONF_UI_DEVICES][0][CONF_RADIO_METADATA]
        self.assertEqual(metadata["product_id"], product_metadata["product_id"])
        self.assertEqual(
            metadata["manufacturer_id"], product_metadata["manufacturer_id"]
        )
        self.assertEqual(
            metadata["product_reference"], product_metadata["product_reference"]
        )
        self.assertEqual(
            (metadata["eep"], metadata["eep_source"], metadata["evidence"]),
            ("D2-01-12", "manual", "manual"),
        )

    async def test_existing_product_or_radio_conflict_fails_closed_before_radio(self):
        row = self._row()
        row[CONF_RADIO_METADATA]["manufacturer_id"] = 1
        entry = self._entry([row])
        flow = self._flow(entry)
        selected = await flow.async_step_commission_existing(
            {"device": options_flow._unique_id_for(row)}
        )
        self.assertEqual(selected["step_id"], "commission_existing_identity")
        conflict = await flow.async_step_commission_existing_identity(
            {"qr_code": "30S000011223344+1P0002AABBCCDD"}
        )
        self.assertEqual(
            conflict["errors"]["qr_code"], "commissioning_identity_conflict"
        )
        self.assertEqual(entry.runtime_data.sent, [])
        self.assertEqual(entry.options[CONF_UI_DEVICES], [row])

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
        selected = await self._identify_existing(flow, row)
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

    async def test_d2_pairing_menu_discloses_no_automatic_ute(self):
        """The guided D2 actions must not imply unsolicited UTE pairing."""
        import json
        from pathlib import Path

        data = json.loads(
            (
                Path(__file__).parents[1]
                / "custom_components/enocean_custom/strings.json"
            ).read_text()
        )
        menu = data["options"]["step"]["init"]["menu_options"]
        self.assertIn("no automatic UTE", menu["pair_actuator"])
        self.assertIn("no automatic UTE", menu["commission_existing"])

        ui_row = {
            "id": [0x11, 0x22, 0x33, 0x44],
            "platform": "switch",
            "name": "Relay",
            "channel": 32,
            "switch_type": "default",
        }
        with self.assertRaises(vol.Invalid):
            UI_DEVICE_SCHEMA(ui_row)
        with self.assertRaises(vol.Invalid):
            switch.PLATFORM_SCHEMA(
                {
                    "platform": DOMAIN,
                    "id": [0x11, 0x22, 0x33, 0x44],
                    "name": "Relay",
                    "channel": 32,
                    "switch_type": "default",
                }
            )


if __name__ == "__main__":
    unittest.main()

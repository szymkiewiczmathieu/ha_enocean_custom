"""Focused standard-UTE commissioning regression coverage."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
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
        SIGNAL_RECEIVE_MESSAGE,
    )
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
    from custom_components.enocean_custom.switch import EnOceanSwitch
    from custom_components.enocean_custom.ute import (
        build_ute_teach_in_query,
        parse_ute_teach_in_response,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - bare environment
    HA_AVAILABLE = False

TARGET_ID = [0x00, 0x3A, 0xFC, 0x98]
BASE_ID = [0xA1, 0xB2, 0xC3, 0xD4]


class FakeDongle:
    """Capture queued UTE packets and manually drive lifecycle callbacks."""

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


def _ute_response(
    *,
    target: list[int] = TARGET_ID,
    destination: list[int] = BASE_ID,
    channel: int = 0,
    eep_type: int = 0x12,
    manufacturer_id: int = 0,
    result: int = 0x01,
) -> UTETeachInPacket:
    """Build an addressed D4 CMD 1 response to a gateway D4 CMD 0 query."""
    return UTETeachInPacket(
        PACKET.RADIO_ERP1,
        data=[
            RORG.UTE,
            0x81 | (result << 4),
            channel,
            manufacturer_id & 0xFF,
            (manufacturer_id >> 8) & 0x07,
            eep_type,
            0x01,
            RORG.VLD,
            *target,
            0x00,
        ],
        optional=[0x03, *destination, 0xFF, 0x00],
    )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class UtePacketTests(unittest.TestCase):
    """Prove UTE bytes and exact response binding independently of the flow."""

    def test_d4_cmd0_query_is_broadcast_for_d2_01_12(self) -> None:
        packet = build_ute_teach_in_query(
            sender_id=BASE_ID,
            eep="D2-01-12",
            channel=1,
        )

        self.assertEqual(packet.packet_type, PACKET.RADIO_ERP1)
        self.assertEqual(
            packet.data,
            [
                RORG.UTE,
                0x80,
                0x01,
                0x00,
                0x00,
                0x12,
                0x01,
                RORG.VLD,
                *BASE_ID,
                0x00,
            ],
        )
        self.assertEqual(
            packet.optional,
            [0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00],
        )

    def test_query_rejects_missing_or_invalid_base_id(self) -> None:
        for sender in (None, [0xA1, 0xB2, 0xC3], [0xA1, 0xB2, 0xC3, 0x100]):
            with self.subTest(sender=sender), self.assertRaises(ValueError):
                build_ute_teach_in_query(
                    sender_id=sender,
                    eep="D2-01-12",
                    channel=0,
                )

    def test_response_parser_requires_every_query_binding_field(self) -> None:
        response = _ute_response(channel=1)
        parsed = parse_ute_teach_in_response(
            response,
            requester_id=BASE_ID,
            eep="D2-01-12",
            channel=1,
            manufacturer_id=0,
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["sender_id"], TARGET_ID)
        self.assertEqual(parsed["destination"], BASE_ID)
        self.assertEqual(parsed["response_code"], 0x01)

        for wrong_response in (
            _ute_response(destination=[0x01, 0x02, 0x03, 0x04], channel=1),
            _ute_response(channel=0),
            _ute_response(channel=1, eep_type=0x11),
            _ute_response(channel=1, manufacturer_id=1),
            _ute_response(channel=1, result=0x00),
        ):
            with self.subTest(response=wrong_response.data):
                if wrong_response.data[1] == 0x81:
                    self.assertIsNotNone(
                        parse_ute_teach_in_response(
                            wrong_response,
                            requester_id=BASE_ID,
                            eep="D2-01-12",
                            channel=1,
                            manufacturer_id=0,
                        )
                    )
                else:
                    self.assertIsNone(
                        parse_ute_teach_in_response(
                            wrong_response,
                            requester_id=BASE_ID,
                            eep="D2-01-12",
                            channel=1,
                            manufacturer_id=0,
                        )
                    )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class UteDiscoveryFlowTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the bounded gateway-query / actuator-response association flow."""

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

    async def _start(self, flow, *, name="New relay", channel=0):
        result = await flow.async_step_ute_discover({"name": name, "channel": channel})
        self.assertEqual(result["type"], FlowResultType.SHOW_PROGRESS)
        self.assertEqual(result["step_id"], "ute_discover_progress")
        self.assertTrue(get_learn_manager(self.hass).is_active)
        dongle = flow.config_entry.runtime_data
        self.assertEqual(len(dongle.sent), 1)
        self.assertEqual(dongle.sent[0].data[0:3], [RORG.UTE, 0x80, channel])
        written = dongle.written_callbacks[0]
        self.assertIsNotNone(written)
        written()
        return dongle

    async def _finished_result(self, flow):
        await self.hass.async_block_till_done()
        progressed = await flow.async_step_ute_discover_progress()
        self.assertEqual(progressed["type"], FlowResultType.SHOW_PROGRESS_DONE)
        return await flow.async_step_ute_discover_result()

    async def test_matched_cmd1_and_esp3_ok_create_selected_output(self):
        entry = self._entry()
        flow = self._flow(entry)
        dongle = await self._start(flow, name="Boiler output 2", channel=1)

        async_dispatcher_send(
            self.hass,
            SIGNAL_RECEIVE_MESSAGE,
            _ute_response(channel=0),
        )
        async_dispatcher_send(
            self.hass,
            SIGNAL_RECEIVE_MESSAGE,
            _ute_response(channel=1, destination=[0x01, 0x02, 0x03, 0x04]),
        )
        self.assertTrue(get_learn_manager(self.hass).is_active)

        async_dispatcher_send(
            self.hass, SIGNAL_RECEIVE_MESSAGE, _ute_response(channel=1)
        )
        dongle.response_callbacks[0](True)
        result = await self._finished_result(flow)

        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        row = result["data"][CONF_UI_DEVICES][0]
        self.assertEqual(row["id"], TARGET_ID)
        self.assertEqual(row["channel"], 1)
        self.assertEqual(row["platform"], "switch")
        self.assertEqual(row["actuator_profile"], "ubiwizz_ubid1507c")
        self.assertEqual(row["radio_metadata"]["eep"], "D2-01-12")
        self.assertEqual(row["radio_metadata"]["manufacturer_id"], 0)
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
                    "manufacturer_id": 0,
                    "evidence": "exact",
                    "eep_source": "radio_declared",
                },
            }
        )
        entry = self._entry([existing])
        flow = self._flow(entry)
        dongle = await self._start(flow, channel=1)
        async_dispatcher_send(
            self.hass, SIGNAL_RECEIVE_MESSAGE, _ute_response(channel=1)
        )
        dongle.response_callbacks[0](True)

        result = await self._finished_result(flow)
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        rows = result["data"][CONF_UI_DEVICES]
        self.assertEqual(
            [(row["id"], row["channel"]) for row in rows],
            [(TARGET_ID, 0), (TARGET_ID, 1)],
        )

    async def test_no_base_id_transport_rejection_timeout_and_rejection_do_not_save(
        self,
    ):
        no_base_entry = self._entry(base_id=None)
        no_base_flow = self._flow(no_base_entry)
        no_base = await no_base_flow.async_step_ute_discover(
            {"name": "No base", "channel": 0}
        )
        self.assertEqual(no_base["type"], FlowResultType.ABORT)
        self.assertEqual(no_base["reason"], "ute_discovery_base_id_unavailable")

        rejected_entry = self._entry()
        rejected_entry.runtime_data.queue_result = False
        rejected_flow = self._flow(rejected_entry)
        started = await rejected_flow.async_step_ute_discover(
            {"name": "Rejected", "channel": 0}
        )
        self.assertEqual(started["type"], FlowResultType.SHOW_PROGRESS)
        rejected = await self._finished_result(rejected_flow)
        self.assertEqual(rejected["type"], FlowResultType.ABORT)
        self.assertEqual(rejected["reason"], "ute_discovery_transport_rejected")

        response_entry = self._entry()
        response_flow = self._flow(response_entry)
        response_dongle = await self._start(response_flow, channel=0)
        async_dispatcher_send(
            self.hass, SIGNAL_RECEIVE_MESSAGE, _ute_response(channel=0, result=0x03)
        )
        response_dongle.response_callbacks[0](True)
        response_result = await self._finished_result(response_flow)
        self.assertEqual(response_result["type"], FlowResultType.ABORT)
        self.assertEqual(response_result["reason"], "ute_discovery_response_rejected")

        timeout_entry = self._entry()
        timeout_flow = self._flow(timeout_entry)
        with patch.object(options_flow, "UTE_DISCOVERY_TIMEOUT", 0.001):
            await self._start(timeout_flow, channel=0)
            await asyncio.sleep(0.01)
        timeout = await self._finished_result(timeout_flow)
        self.assertEqual(timeout["type"], FlowResultType.ABORT)
        self.assertEqual(timeout["reason"], "ute_discovery_timeout")

    async def test_stale_transport_callback_cannot_persist_after_removal(self):
        entry = self._entry()
        flow = self._flow(entry)
        dongle = await self._start(flow, channel=0)
        async_dispatcher_send(
            self.hass, SIGNAL_RECEIVE_MESSAGE, _ute_response(channel=0)
        )
        response_callback = dongle.response_callbacks[0]
        flow.async_remove()
        response_callback(True)
        await self.hass.async_block_till_done()

        self.assertEqual(entry.options[CONF_UI_DEVICES], [])
        self.assertIsNone(flow._ute_discovery_outcome)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class UteCommissioningActionTests(unittest.IsolatedAsyncioTestCase):
    """Keep the entity action scoped, callback-driven, and D2-command-free."""

    def _entity(self, base_id: list[int] | None = None):
        dongle = FakeDongle(base_id)
        entity = EnOceanSwitch(
            TARGET_ID,
            "Dressing output 2",
            1,
            "default",
            "D2-01-12",
            "ubiwizz_ubid1507c",
        )
        entity.hass = SimpleNamespace(
            data={DATA_ENOCEAN: {ENOCEAN_DONGLE: dongle}},
            loop=asyncio.get_running_loop(),
        )
        entity.async_write_ha_state = Mock()
        return entity, dongle

    async def asyncTearDown(self) -> None:
        for entity in getattr(self, "_entities", []):
            entity._cancel_ute_commission_timeout()

    async def test_action_starts_deadline_only_when_query_is_written(self):
        entity, dongle = self._entity(BASE_ID)
        self._entities = [entity]

        await entity.async_start_ute_commissioning()
        self.assertEqual(
            entity.extra_state_attributes["ute_commissioning_state"], "queued"
        )
        self.assertEqual(dongle.sent[0].data[0:3], [RORG.UTE, 0x80, 1])

        dongle.written_callbacks[0]()
        self.assertEqual(
            entity.extra_state_attributes["ute_commissioning_state"], "awaiting_esp3"
        )
        dongle.response_callbacks[0](True)
        self.assertEqual(
            entity.extra_state_attributes["ute_commissioning_state"],
            "awaiting_ute_response",
        )

        entity.value_changed(_ute_response(channel=1))
        attributes = entity.extra_state_attributes
        self.assertEqual(attributes["ute_commissioning_state"], "accepted")
        self.assertTrue(attributes["ute_commissioning_last_esp3_ack"])
        self.assertEqual(attributes["ute_commissioning_response_code"], 0x01)
        self.assertEqual(attributes["ute_commissioning_transmissions"], 1)

    async def test_absent_base_unsupported_entity_and_bad_responses_do_not_confirm(
        self,
    ):
        entity, dongle = self._entity(None)
        self._entities = [entity]
        await entity.async_start_ute_commissioning()
        self.assertEqual(dongle.sent, [])
        self.assertEqual(
            entity.extra_state_attributes["ute_commissioning_state"],
            "base_id_unavailable",
        )

        unsupported, unsupported_dongle = self._entity(BASE_ID)
        unsupported._actuator_profile = None
        self._entities.append(unsupported)
        with self.assertRaises(HomeAssistantError):
            await unsupported.async_start_ute_commissioning()
        self.assertEqual(unsupported_dongle.sent, [])

        entity, dongle = self._entity(BASE_ID)
        self._entities.append(entity)
        await entity.async_start_ute_commissioning()
        dongle.written_callbacks[0]()
        dongle.response_callbacks[0](True)
        entity.value_changed(_ute_response(channel=0))
        entity.value_changed(_ute_response(channel=1, eep_type=0x11))
        self.assertEqual(
            entity.extra_state_attributes["ute_commissioning_state"],
            "awaiting_ute_response",
        )
        entity.value_changed(_ute_response(channel=1, result=0x03))
        self.assertEqual(
            entity.extra_state_attributes["ute_commissioning_state"],
            "response_rejected",
        )

    async def test_late_response_after_700ms_deadline_is_ignored(self):
        entity, dongle = self._entity(BASE_ID)
        self._entities = [entity]
        with patch.object(switch, "UTE_QUERY_MAX_RESPONSE_SECONDS", 0.001):
            await entity.async_start_ute_commissioning()
            dongle.written_callbacks[0]()
            dongle.response_callbacks[0](True)
            await asyncio.sleep(0.01)
        self.assertEqual(
            entity.extra_state_attributes["ute_commissioning_state"], "response_timeout"
        )
        entity.value_changed(_ute_response(channel=1))
        self.assertEqual(
            entity.extra_state_attributes["ute_commissioning_state"], "response_timeout"
        )


if __name__ == "__main__":
    unittest.main()

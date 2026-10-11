"""Focused directed-UTE commissioning regression coverage."""

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
    from homeassistant.const import EntityCategory
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.enocean_custom import options_flow, switch
    from custom_components.enocean_custom.button import UTECommissionButton
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
        build_ute_teach_in_response,
        parse_ute_teach_in_request,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - bare environment
    HA_AVAILABLE = False

TARGET_ID = [0x00, 0x3A, 0xFC, 0x98]
OTHER_ID = [0x00, 0x3A, 0xFC, 0x99]
BASE_ID = [0xA1, 0xB2, 0xC3, 0xD4]
# Keep module importable in the CI environment that intentionally lacks HA.
UTE_RORG_VLD = 0xD2
UTE_REQUEST_FIELDS = [0xA0, 0x02, 0x46, 0x00, 0x12, 0x01, UTE_RORG_VLD]
UTE_RESPONSE_FIELDS = [0x91, 0x02, 0x46, 0x00, 0x12, 0x01, UTE_RORG_VLD]


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


def _ute_request(
    *,
    target: list[int] = TARGET_ID,
    destination: list[int] | None = None,
    flags: int = 0xA0,
    number_of_channels: int = 0x02,
    eep_type: int = 0x12,
    manufacturer_id: int = 0x046,
) -> UTETeachInPacket:
    """Build the UBID1507C broadcast D4/A0 request seen after PRESS mode."""
    return UTETeachInPacket(
        PACKET.RADIO_ERP1,
        data=[
            RORG.UTE,
            flags,
            number_of_channels,
            manufacturer_id & 0xFF,
            (manufacturer_id >> 8) & 0x07,
            eep_type,
            0x01,
            RORG.VLD,
            *target,
            0x00,
        ],
        optional=[0x01, *(destination or [0xFF, 0xFF, 0xFF, 0xFF]), 0x40, 0x00],
    )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class UtePacketTests(unittest.TestCase):
    """Prove exact D4/A0 request matching and directed D4/91 response bytes."""

    def test_directed_response_matches_field_capture(self) -> None:
        response = build_ute_teach_in_response(
            sender_id=BASE_ID,
            target_id=TARGET_ID,
            eep="D2-01-12",
            number_of_channels=2,
            manufacturer_id=0x046,
        )

        self.assertEqual(response.packet_type, PACKET.RADIO_ERP1)
        self.assertEqual(
            response.data, [RORG.UTE, *UTE_RESPONSE_FIELDS, *BASE_ID, 0x00]
        )
        self.assertEqual(response.optional, [0x03, *TARGET_ID, 0xFF, 0x00])

    def test_request_parser_binds_label_eurid_and_every_ute_field(self) -> None:
        request = _ute_request()
        parsed = parse_ute_teach_in_request(
            request,
            target_id=TARGET_ID,
            eep="D2-01-12",
            number_of_channels=2,
            manufacturer_id=0x046,
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["sender_id"], TARGET_ID)
        self.assertEqual(parsed["number_of_channels"], 2)
        self.assertEqual(parsed["manufacturer_id"], 0x046)
        self.assertEqual(parsed["eep"], "D2-01-12")

        for wrong_request in (
            _ute_request(target=OTHER_ID),
            _ute_request(destination=BASE_ID),
            _ute_request(flags=0x80),
            _ute_request(number_of_channels=1),
            _ute_request(eep_type=0x11),
            _ute_request(manufacturer_id=0),
        ):
            with self.subTest(request=wrong_request.data):
                self.assertIsNone(
                    parse_ute_teach_in_request(
                        wrong_request,
                        target_id=TARGET_ID,
                        eep="D2-01-12",
                        number_of_channels=2,
                        manufacturer_id=0x046,
                    )
                )

    def test_response_rejects_missing_or_invalid_ids(self) -> None:
        for sender, target in (
            (None, TARGET_ID),
            ([0xA1, 0xB2, 0xC3], TARGET_ID),
            (BASE_ID, [0x00, 0x3A, 0xFC]),
            (BASE_ID, [0x00, 0x3A, 0xFC, 0x100]),
        ):
            with (
                self.subTest(sender=sender, target=target),
                self.assertRaises(ValueError),
            ):
                build_ute_teach_in_response(
                    sender_id=sender,
                    target_id=target,
                    eep="D2-01-12",
                    number_of_channels=2,
                    manufacturer_id=0x046,
                )

    def test_commission_button_uses_home_assistant_enum(self) -> None:
        button = UTECommissionButton(SimpleNamespace(), TARGET_ID, "Ubiwizz", 0)
        self.assertIs(button.entity_category, EntityCategory.CONFIG)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class UteDiscoveryFlowTests(unittest.IsolatedAsyncioTestCase):
    """Exercise explicit-EURID UTE request -> directed-response association."""

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
        result = await flow.async_step_ute_discover(
            {
                "name": name,
                "qr_code": ":".join(f"{byte:02X}" for byte in target),
                "channel": channel,
            }
        )
        self.assertEqual(result["type"], FlowResultType.SHOW_PROGRESS)
        self.assertEqual(result["step_id"], "ute_discover_progress")
        self.assertTrue(get_learn_manager(self.hass).is_active)
        dongle = flow.config_entry.runtime_data
        self.assertEqual(dongle.sent, [])
        return dongle

    async def _finished_result(self, flow):
        await self.hass.async_block_till_done()
        progressed = await flow.async_step_ute_discover_progress()
        self.assertEqual(progressed["type"], FlowResultType.SHOW_PROGRESS_DONE)
        return await flow.async_step_ute_discover_result()

    async def test_exact_d4_a0_and_esp3_ok_create_selected_output(self):
        entry = self._entry()
        flow = self._flow(entry)
        dongle = await self._start(flow, name="Boiler output 2", channel=1)

        async_dispatcher_send(
            self.hass, SIGNAL_RECEIVE_MESSAGE, _ute_request(target=OTHER_ID)
        )
        async_dispatcher_send(
            self.hass, SIGNAL_RECEIVE_MESSAGE, _ute_request(number_of_channels=1)
        )
        self.assertEqual(dongle.sent, [])
        self.assertTrue(get_learn_manager(self.hass).is_active)

        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, _ute_request())
        self.assertEqual(len(dongle.sent), 1)
        self.assertEqual(
            dongle.sent[0].data, [RORG.UTE, *UTE_RESPONSE_FIELDS, *BASE_ID, 0x00]
        )
        self.assertEqual(dongle.sent[0].optional, [0x03, *TARGET_ID, 0xFF, 0x00])
        dongle.response_callbacks[0](True)
        result = await self._finished_result(flow)

        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        row = result["data"][CONF_UI_DEVICES][0]
        self.assertEqual(row["id"], TARGET_ID)
        self.assertEqual(row["channel"], 1)
        self.assertEqual(row["platform"], "switch")
        self.assertEqual(row["actuator_profile"], "ubiwizz_ubid1507c")
        self.assertEqual(row["radio_metadata"]["eep"], "D2-01-12")
        self.assertEqual(row["radio_metadata"]["manufacturer_id"], 0x046)
        self.assertNotIn("channel", row["radio_metadata"])
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
                    "evidence": "exact",
                    "eep_source": "radio_declared",
                },
            }
        )
        entry = self._entry([existing])
        flow = self._flow(entry)
        dongle = await self._start(flow, channel=1)
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, _ute_request())
        dongle.response_callbacks[0](True)

        result = await self._finished_result(flow)
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        rows = result["data"][CONF_UI_DEVICES]
        self.assertEqual(
            [(row["id"], row["channel"]) for row in rows],
            [(TARGET_ID, 0), (TARGET_ID, 1)],
        )

    async def test_invalid_target_queue_rejection_timeout_and_collision_do_not_save(
        self,
    ):
        entry = self._entry()
        invalid_flow = self._flow(entry)
        invalid = await invalid_flow.async_step_ute_discover(
            {"name": "Invalid", "qr_code": "not-an-eurid", "channel": 0}
        )
        self.assertEqual(invalid["type"], FlowResultType.FORM)
        self.assertEqual(invalid["errors"], {"qr_code": "invalid_ute_target"})

        no_base_entry = self._entry(base_id=None)
        no_base_flow = self._flow(no_base_entry)
        no_base = await no_base_flow.async_step_ute_discover(
            {"name": "No base", "qr_code": "00:3A:FC:98", "channel": 0}
        )
        self.assertEqual(no_base["type"], FlowResultType.ABORT)
        self.assertEqual(no_base["reason"], "ute_discovery_base_id_unavailable")

        rejected_entry = self._entry()
        rejected_entry.runtime_data.queue_result = False
        rejected_flow = self._flow(rejected_entry)
        rejected_dongle = await self._start(rejected_flow)
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, _ute_request())
        self.assertEqual(len(rejected_dongle.sent), 1)
        rejected = await self._finished_result(rejected_flow)
        self.assertEqual(rejected["type"], FlowResultType.ABORT)
        self.assertEqual(rejected["reason"], "ute_discovery_transport_rejected")

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
        collision_dongle = await self._start(collision_flow, channel=0)
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, _ute_request())
        self.assertEqual(collision_dongle.sent, [])
        collision = await self._finished_result(collision_flow)
        self.assertEqual(collision["type"], FlowResultType.ABORT)
        self.assertEqual(collision["reason"], "ute_discovery_collision")

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
        async_dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, _ute_request())
        response_callback = dongle.response_callbacks[0]
        flow.async_remove()
        response_callback(True)
        await self.hass.async_block_till_done()

        self.assertEqual(entry.options[CONF_UI_DEVICES], [])
        self.assertIsNone(flow._ute_discovery_outcome)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class UteCommissioningActionTests(unittest.IsolatedAsyncioTestCase):
    """Keep the entity action targeted, callback-driven, and D2-command-free."""

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

    async def test_action_listens_then_sends_exact_directed_response(self):
        entity, dongle = self._entity(BASE_ID)
        self._entities = [entity]

        await entity.async_start_ute_commissioning()
        self.assertEqual(
            entity.extra_state_attributes["ute_commissioning_state"],
            "awaiting_ute_request",
        )
        self.assertEqual(dongle.sent, [])

        entity.value_changed(_ute_request(target=OTHER_ID))
        entity.value_changed(_ute_request(number_of_channels=1))
        self.assertEqual(dongle.sent, [])

        entity.value_changed(_ute_request())
        self.assertEqual(
            entity.extra_state_attributes["ute_commissioning_state"], "response_queued"
        )
        self.assertEqual(
            dongle.sent[0].data, [RORG.UTE, *UTE_RESPONSE_FIELDS, *BASE_ID, 0x00]
        )
        self.assertEqual(dongle.sent[0].optional, [0x03, *TARGET_ID, 0xFF, 0x00])
        self.assertEqual(
            entity.extra_state_attributes["ute_commissioning_response_code"], 0x01
        )
        self.assertEqual(
            entity.extra_state_attributes["ute_commissioning_transmissions"], 1
        )

        dongle.response_callbacks[0](True)
        attributes = entity.extra_state_attributes
        self.assertEqual(attributes["ute_commissioning_state"], "response_sent")
        self.assertTrue(attributes["ute_commissioning_last_esp3_ack"])
        self.assertFalse(any(packet.data[0] == RORG.VLD for packet in dongle.sent))

    async def test_absent_base_unsupported_and_late_request_do_not_confirm(self):
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

        late, late_dongle = self._entity(BASE_ID)
        self._entities.append(late)
        with patch.object(switch, "UTE_ASSOCIATION_WINDOW_SECONDS", 0.001):
            await late.async_start_ute_commissioning()
            await asyncio.sleep(0.01)
        self.assertEqual(
            late.extra_state_attributes["ute_commissioning_state"], "request_timeout"
        )
        late.value_changed(_ute_request())
        self.assertEqual(late_dongle.sent, [])


if __name__ == "__main__":
    unittest.main()

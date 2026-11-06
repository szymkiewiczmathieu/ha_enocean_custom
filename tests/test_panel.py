"""Admin panel snapshot, bounded stream and registration tests."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from homeassistant import loader
from homeassistant.components import panel_custom, websocket_api
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.enocean_custom import panel
from custom_components.enocean_custom.const import DOMAIN, SIGNAL_RECEIVE_MESSAGE
from custom_components.enocean_custom.dongle import GatewayInfo, RepeaterState
from custom_components.enocean_custom.eep_catalog import generic_profile
from custom_components.enocean_custom.enocean_library.protocol.constants import RORG
from custom_components.enocean_custom.enocean_library.protocol.d2 import (
    parse_d2_01_actuator_status,
)
from custom_components.enocean_custom.identity import cover_unique_id
from custom_components.enocean_custom.inbox import DeviceInbox, PacketObservation
from custom_components.enocean_custom.schema import CONF_UI_DEVICES, UI_DEVICE_SCHEMA

SENDER = 0x058F2233
SENDER_BYTES = [0x05, 0x8F, 0x22, 0x33]
BASE_ID = [0xFF, 0x87, 0xCA, 0x00]


def _packet(simulated=False):
    return SimpleNamespace(
        data=[RORG.RPS, 0x10, *SENDER_BYTES, 0x30],
        sender_int=SENDER,
        rorg=RORG.RPS,
        dBm=-64,
        status=0x30,
        simulated=simulated,
    )


class MonitorTests(unittest.TestCase):
    def test_disabled_by_default_bounded_masked_and_cleared_on_unsubscribe(self):
        monitor = panel.TelegramMonitor()
        monitor.record(_packet())
        self.assertEqual(monitor.snapshot(), [])
        self.assertFalse(monitor.enabled)

        events = []
        unsubscribe = monitor.subscribe(events.append, anonymize=True)
        self.assertTrue(monitor.enabled)
        monitor.record(_packet(simulated=True))
        self.assertEqual(len(events), 1)
        record = events[0]
        self.assertIs(record["simulated"], True)
        self.assertEqual(record["sender"], "••:••:••:••")
        self.assertEqual(record["data"][-5:-1], [0, 0, 0, 0])
        self.assertEqual(record["rssi_dbm"], -64)

        for _ in range(105):
            monitor.record(_packet())
        self.assertEqual(len(monitor.snapshot()), 100)
        unsubscribe()
        self.assertFalse(monitor.enabled)
        self.assertEqual(monitor.snapshot(), [])

    def test_malformed_rps_payload_is_bounded_and_never_breaks_the_dispatcher(self):
        monitor = panel.TelegramMonitor()
        events = []
        monitor.subscribe(events.append, anonymize=True)
        packet = _packet()
        packet.data = [RORG.RPS]
        monitor.record(packet)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "RPS telegram")
        self.assertIsNone(events[0]["data"])

        packet = _packet()
        packet.status = 0x80
        monitor.record(packet)
        self.assertEqual(events[1]["summary"], "RPS unknown (0x10)")

    def test_explicit_unmask_is_per_subscription_not_persistent(self):
        monitor = panel.TelegramMonitor()
        unsubscribe = monitor.subscribe(lambda event: None, anonymize=False)
        events = []
        monitor.subscribe(events.append, anonymize=True)
        monitor.record(_packet())
        self.assertEqual(events[0]["sender"], "••:••:••:••")
        views = monitor.snapshot(anonymize=False)
        self.assertEqual(views[0]["sender"], "05:8F:22:33")
        unsubscribe()


class PreviewTelegramTests(unittest.TestCase):
    def _record(self, eep):
        source = (ROOT / "scripts" / "panel_preview.html").read_text(encoding="utf-8")
        line = next(line for line in source.splitlines() if f'eep: "{eep}"' in line)
        summary_match = re.search(r'summary: "([^"]+)"', line)
        data_match = re.search(r"data: \[([^]]+)\]", line)
        assert summary_match is not None
        assert data_match is not None
        summary = summary_match.group(1)
        data = [int(value.strip(), 0) for value in data_match.group(1).split(",")]
        return summary, data

    def test_temperature_demo_bytes_decode_to_the_displayed_value(self):
        summary, data = self._record("A5-02-05")
        self.assertEqual(data[3], 0x76)  # DB1: inverted 0..40 °C scale.
        profile = generic_profile("A5-02-05")
        assert profile is not None
        self.assertEqual(profile.decode(data), {"tmp": 21.49})
        self.assertEqual(summary, "tmp=21.49")

    def test_dimmer_demo_bytes_decode_to_the_displayed_output(self):
        summary, data = self._record("D2-01-01")
        status = parse_d2_01_actuator_status(data)
        assert status is not None
        self.assertEqual((status.channel, status.output_value), (0, 100))
        self.assertEqual(data[3], 0x64)
        self.assertEqual(summary, "Channel 0 output 100 %")


class PanelFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        custom_components = Path(self._config_dir.name) / "custom_components"
        custom_components.mkdir()
        (custom_components / DOMAIN).symlink_to(
            ROOT / "custom_components" / DOMAIN, target_is_directory=True
        )
        self.hass = HomeAssistant(self._config_dir.name)
        loader.async_setup(self.hass)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        row = UI_DEVICE_SCHEMA(
            {
                "id": SENDER_BYTES,
                "platform": "cover",
                "name": "Bedroom shutter",
                "channel": 0,
                "radio_metadata": {"eep": "D2-05-00"},
            }
        )
        self.entry = ConfigEntry(
            data={"device": "/dev/test-panel"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=3,
            options={CONF_UI_DEVICES: [row]},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        self.gateway = SimpleNamespace(
            entry_id=self.entry.entry_id,
            available=True,
            base_id=BASE_ID,
            gateway_info=GatewayInfo(
                "2.15.0.0", "2.6.3.0", "0F0E0D0C", "2.15.0.0", "FAKE ESP3"
            ),
            repeater=RepeaterState(False, 1, 0),
            received_radio_packets=3,
            last_packet_at=None,
            inbox=DeviceInbox(lambda: {SENDER}),
            telegram_monitor=None,
        )
        self.gateway.diagnostics = lambda: {
            "available": True,
            "thread_alive": True,
            "last_error": None,
            "received_radio_packets": 3,
        }
        self.entry.runtime_data = self.gateway
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry
        self.gateway.inbox.observe(
            PacketObservation(SENDER, RORG.RPS, -61, 1, (RORG.RPS, 0, 0, 0, 0, 0))
        )
        self.gateway.inbox.observe(
            PacketObservation(0x01020304, RORG.BS1, -75, 0, None)
        )
        er.async_get(self.hass).async_get_or_create(
            "cover",
            DOMAIN,
            cover_unique_id(SENDER_BYTES, 0),
            suggested_object_id="bedroom_shutter",
        )
        self.hass.states.async_set("cover.bedroom_shutter", "open")
        panel.async_setup_entry_panel(self.hass, self.entry, self.gateway)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def test_snapshot_masks_ids_and_includes_devices_nearby_gateway(self):
        payload = panel.build_panel_snapshot(
            self.hass, self.entry, self.gateway, anonymize=True
        )
        self.assertEqual(len(payload["devices"]), 1)
        device = payload["devices"][0]
        self.assertEqual(device["sender"], "••:••:••:••")
        self.assertEqual(device["state"], "open")
        self.assertEqual(device["last_rssi_dbm"], -61)
        self.assertEqual(device["last_repeater_count"], 1)
        self.assertEqual(device["eep"], "D2-05-00")
        self.assertEqual(len(payload["nearby"]), 2)
        self.assertTrue(
            all(row["sender"] == "••:••:••:••" for row in payload["nearby"])
        )
        self.assertEqual(payload["gateway"]["base_id"], "••:••:••:••")
        self.assertEqual(payload["gateway"]["app_version"], "2.15.0.0")
        self.assertTrue(payload["gateway"]["worker_alive"])

        revealed = panel.build_panel_snapshot(
            self.hass, self.entry, self.gateway, anonymize=False
        )
        self.assertEqual(revealed["gateway"]["base_id"], "FF:87:CA:00")
        self.assertEqual(revealed["devices"][0]["sender"], "05:8F:22:33")

    async def test_snapshot_unit_is_additive_and_not_an_identifier(self):
        self.hass.states.async_set(
            "cover.bedroom_shutter", "21.4", {"unit_of_measurement": "°C"}
        )
        for anonymize in (True, False):
            with self.subTest(anonymize=anonymize):
                payload = panel.build_panel_snapshot(
                    self.hass, self.entry, self.gateway, anonymize=anonymize
                )
                self.assertEqual(payload["devices"][0]["unit"], "°C")
        self.hass.states.async_set("cover.bedroom_shutter", "21.4")
        payload = panel.build_panel_snapshot(
            self.hass, self.entry, self.gateway, anonymize=True
        )
        self.assertIsNone(payload["devices"][0]["unit"])
        self.assertEqual(payload["devices"][0]["state"], "21.4")

    async def test_websocket_commands_require_admin_and_stream_live_packets(self):
        connection = SimpleNamespace(
            user=SimpleNamespace(is_admin=True),
            send_message=Mock(),
            subscriptions={},
        )
        panel.ws_panel_snapshot(
            self.hass,
            connection,
            {"id": 7, "type": "enocean_custom/panel", "anonymize": True},
        )
        result = connection.send_message.call_args.args[0]
        self.assertEqual(result["id"], 7)
        self.assertTrue(result["success"])
        self.assertEqual(result["result"]["gateway"]["base_id"], "••:••:••:••")

        panel.ws_telegram_subscribe(
            self.hass,
            connection,
            {
                "id": 8,
                "type": "enocean_custom/telegram_stream/subscribe",
                "anonymize": True,
            },
        )
        self.assertIn(8, connection.subscriptions)
        self.assertTrue(self.gateway.telegram_monitor.enabled)
        async_dispatcher_send(
            self.hass, SIGNAL_RECEIVE_MESSAGE, _packet(simulated=True)
        )
        event = connection.send_message.call_args.args[0]
        self.assertEqual(event["type"], "event")
        self.assertIs(event["event"]["simulated"], True)
        self.assertEqual(event["event"]["sender"], "••:••:••:••")

        connection.user.is_admin = False
        with self.assertRaises(Unauthorized):
            panel.ws_panel_snapshot(
                self.hass,
                connection,
                {"id": 9, "type": "enocean_custom/panel"},
            )

    async def test_registration_and_loader_accept_dependencies(self):
        manifest = await loader.async_get_integration(self.hass, DOMAIN)
        dependencies = set(manifest.manifest["dependencies"])
        self.assertEqual(
            dependencies,
            {"usb", "frontend", "http", "panel_custom", "websocket_api"},
        )
        for dependency in dependencies - {"usb"}:
            await loader.async_get_integration(self.hass, dependency)

        with (
            unittest.mock.patch.object(
                panel_custom, "async_register_panel", new=AsyncMock()
            ) as register,
            unittest.mock.patch.object(
                websocket_api, "async_register_command", new=Mock()
            ) as register_ws,
            unittest.mock.patch.object(
                self.hass,
                "http",
                SimpleNamespace(async_register_static_paths=AsyncMock()),
            ) as http,
        ):
            await panel.async_setup_panel(self.hass)
        self.assertIs(register.call_args.kwargs["require_admin"], True)
        self.assertEqual(
            register.call_args.kwargs["webcomponent_name"],
            "enocean-custom-panel",
        )
        self.assertGreaterEqual(register_ws.call_count, 2)
        http.async_register_static_paths.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

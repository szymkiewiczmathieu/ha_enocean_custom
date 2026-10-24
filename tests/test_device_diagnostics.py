"""Per-device diagnostics stay anonymous: shape and radio facts, no identity."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.diagnostics import (
        async_get_config_entry_diagnostics,
        async_get_device_diagnostics,
    )
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RORG,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import Packet
    from custom_components.enocean_custom.identity import gateway_device_identifier
    from custom_components.enocean_custom.inbox import DeviceInbox, packet_observation
    from custom_components.enocean_custom.schema import CONF_UI_DEVICES

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [0x05, 0x8F, 0x22, 0x33]
SENDER_HEX = "058F2233"
SENDER_DECIMAL = "93266483"
NAME = "Plafonnier escalier secret"


def _parsed(data, dbm=61):
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=data,
        optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, dbm, 0x00],
    ).build()
    return Packet.parse_msg(bytearray(wire))[2]


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class InboxPayloadTests(unittest.TestCase):
    def test_last_payload_blanks_the_sender_bytes(self):
        observation = packet_observation(_parsed([RORG.RPS, 0x50, *SENDER, 0x30]))
        self.assertEqual(observation.payload, (0xF6, 0x50, 0, 0, 0, 0, 0x30))
        inbox = DeviceInbox(set)
        inbox.observe(observation)
        self.assertEqual(inbox.get(0x058F2233).last_payload, observation.payload)

    def test_odd_data_yields_no_payload(self):
        for data in (None, [0xF6, 0x50], list(range(40)), [0xF6, 1.5, 0, 0, 0, 0, 0]):
            with self.subTest(data=data):
                observation = packet_observation(
                    SimpleNamespace(
                        sender_int=1, rorg=RORG.RPS, dBm=-40, status=0, data=data
                    )
                )
                self.assertIsNone(observation.payload)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DeviceDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        rows = [
            {
                "id": SENDER,
                "platform": "switch",
                "name": NAME,
                "channel": 0,
                "switch_type": "RPS",
                "actuator_profile": "ubiwizz_ubid1507c",
                "radio_metadata": {
                    "sender_id": SENDER,
                    "eep": "D2-01-12",
                    "eep_source": "manual",
                    "evidence": "manual",
                    "product_id": "0046DEADBEEF",
                },
            },
            {
                "id": SENDER,
                "platform": "switch",
                "name": NAME,
                "channel": 1,
                "switch_type": "RPS",
            },
            {"id": [1, 2, 3, 4], "platform": "binary_sensor", "name": "Other"},
        ]
        self.entry = ConfigEntry(
            data={"device": "/dev/serial/by-id/private"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=2,
            options={CONF_UI_DEVICES: rows, "availability": {"enabled": True}},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        self.inbox = DeviceInbox(set)
        self.inbox.observe(
            packet_observation(_parsed([RORG.VLD, 0x04, 0x00, 0x64, *SENDER, 0x00]))
        )
        self.entry.runtime_data = SimpleNamespace(
            entry_id=self.entry.entry_id,
            inbox=self.inbox,
            diagnostics=lambda: {"health": "ok"},
        )
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry
        registry = dr.async_get(self.hass)
        self.gateway = registry.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, gateway_device_identifier(self.entry.entry_id))},
        )
        self.device = registry.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, SENDER_HEX)},
            name=NAME,
        )
        entities = er.async_get(self.hass)
        entities.async_get_or_create(
            "switch", DOMAIN, f"{SENDER_DECIMAL}-0", device_id=self.device.id
        )
        entities.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{SENDER_DECIMAL}-radio_rssi",
            device_id=self.device.id,
            translation_key="radio_rssi",
            disabled_by=er.RegistryEntryDisabler.INTEGRATION,
        )

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def test_sender_device_export_is_shaped_and_anonymous(self):
        export = await async_get_device_diagnostics(self.hass, self.entry, self.device)
        rendered = repr(export)
        for secret in (
            NAME,
            SENDER_HEX,
            SENDER_DECIMAL,
            "0046DEADBEEF",
            "private",
            "5, 143",
        ):
            self.assertNotIn(secret, rendered, secret)
        self.assertEqual(export["kind"], "radio_sender")
        self.assertEqual(len(export["rows"]), 2)
        first = export["rows"][0]
        self.assertEqual(first["platform"], "switch")
        self.assertEqual(first["actuator_profile"], "ubiwizz_ubid1507c")
        self.assertEqual(first["profile"]["eep"], "D2-01-12")
        self.assertEqual(first["profile"]["evidence"], "manual")
        self.assertNotIn("product_id", first["profile"])
        self.assertIsNone(first["silence_category"])
        radio = export["radio"]
        self.assertEqual(radio["packet_count"], 1)
        self.assertEqual(radio["last_rssi_dbm"], -61)
        self.assertEqual(radio["rorgs"], ["0xD2"])
        self.assertEqual(radio["last_telegram"], "D2 04 00 64 00 00 00 00 00")
        self.assertIsNone(radio["last_signal"], "no SIGNAL telegram observed yet")
        entities = {entity["unique_id_suffix"]: entity for entity in export["entities"]}
        self.assertEqual(set(entities), {"-0", "-radio_rssi"})
        self.assertEqual(entities["-radio_rssi"]["disabled_by"], "integration")
        self.assertEqual(entities["-radio_rssi"]["translation_key"], "radio_rssi")
        self.assertTrue(export["availability"]["enabled"])

    async def test_sender_device_export_includes_signal_facts(self):
        # MID 0x12 Product ID: manufacturer 0x046D, product reference 0xDEADBEEF.
        self.inbox.observe(
            packet_observation(
                _parsed(
                    [
                        RORG.SIGNAL,
                        0x12,
                        0x04,
                        0x6D,
                        0xDE,
                        0xAD,
                        0xBE,
                        0xEF,
                        *SENDER,
                        0x00,
                    ]
                )
            )
        )
        export = await async_get_device_diagnostics(self.hass, self.entry, self.device)
        self.assertNotIn("1133", repr(export), "manufacturer_id must be redacted")
        signal = export["radio"]["last_signal"]
        self.assertEqual(signal["mid"], "0x12")
        self.assertEqual(signal["product_id"], "**REDACTED**")
        self.assertIsNone(signal["energy_status_percent"])
        self.assertFalse(signal["heartbeat"])

        # "Last signal", not an accumulation: a later MID replaces the shape.
        self.inbox.observe(
            packet_observation(_parsed([RORG.SIGNAL, 0x06, 55, *SENDER, 0x00]))
        )
        export = await async_get_device_diagnostics(self.hass, self.entry, self.device)
        signal = export["radio"]["last_signal"]
        self.assertEqual(signal["mid"], "0x06")
        self.assertEqual(signal["energy_status_percent"], 55)
        self.assertIsNone(signal["product_id"])

    async def test_gateway_device_exports_the_dongle_diagnostics(self):
        export = await async_get_device_diagnostics(self.hass, self.entry, self.gateway)
        self.assertEqual(export, {"kind": "gateway", "dongle": {"health": "ok"}})

    async def test_unknown_device_and_entry_export(self):
        foreign = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id, identifiers={(DOMAIN, "weird")}
        )
        export = await async_get_device_diagnostics(self.hass, self.entry, foreign)
        self.assertEqual(export, {"kind": "unknown", "identifiers": ["weird"]})
        entry_export = await async_get_config_entry_diagnostics(self.hass, self.entry)
        self.assertNotIn("private", repr(entry_export))
        self.assertEqual(entry_export["inbox"], {"observed_senders_count": 1})
        self.assertTrue(entry_export["availability"]["enabled"])


if __name__ == "__main__":
    unittest.main()

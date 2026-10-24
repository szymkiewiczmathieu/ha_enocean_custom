"""Per-device radio diagnostics: RSSI, last telegram and repeater hops.

One set per sender, disabled by default, fed by the radio envelope of every
telegram the sender transmits, and removed with the last row of that sender.
The frozen row identities (``ui_device_entity_identities``) are untouched.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.components.sensor import SensorDeviceClass
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.entity import EntityCategory

    from custom_components.enocean_custom import options_flow, sensor
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        PARSE_RESULT,
        RORG,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import Packet
    from custom_components.enocean_custom.identity import (
        RADIO_DIAGNOSTIC_KEYS,
        device_names_by_sender,
        sender_diagnostic_identities,
        ui_device_entity_identities,
    )
    from custom_components.enocean_custom.schema import (
        CONF_UI_DEVICES,
        UI_DEVICE_SCHEMA,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

COMPONENT = ROOT / "custom_components/enocean_custom"
SENDER = [0x05, 0x8F, 0x22, 0x33]
DECIMAL = "93266483"


def _parse(data: list[int], *, dbm: int) -> object:
    """Build and parse a real ESP3 RADIO_ERP1 frame with the given RSSI."""
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=data,
        optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, dbm, 0x00],
    ).build()
    result, remaining, packet = Packet.parse_msg(bytearray(wire))
    if result != PARSE_RESULT.OK or remaining:
        raise AssertionError(f"could not parse synthetic frame: {result}")
    return packet


class IconCatalogTests(unittest.TestCase):
    """Range icons are declared with ascending numeric thresholds."""

    def test_rssi_range_icons(self):
        icons = json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))
        rssi = icons["entity"]["sensor"]["radio_rssi"]
        thresholds = [float(key) for key in rssi["range"]]
        self.assertEqual(thresholds, sorted(thresholds))
        self.assertTrue(
            all(value.startswith("mdi:") for value in rssi["range"].values())
        )
        for key in ("radio_last_seen", "radio_repeater_count"):
            self.assertIn("default", icons["entity"]["sensor"][key])

    def test_every_language_names_the_diagnostics(self):
        for name in ("strings.json", "translations/en.json", "translations/fr.json"):
            sensors = json.loads((COMPONENT / name).read_text(encoding="utf-8"))[
                "entity"
            ]["sensor"]
            for key in (
                "radio_rssi",
                "radio_last_seen",
                "radio_repeater_count",
                "signal_energy",
            ):
                self.assertTrue(sensors[key]["name"], f"{name}: {key}")


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class IdentityTests(unittest.TestCase):
    """Diagnostics have their own identities; row identities stay frozen."""

    def test_identities_are_sensor_suffixes_outside_the_frozen_formulas(self):
        self.assertEqual(
            sender_diagnostic_identities(SENDER),
            (
                ("sensor", f"{DECIMAL}-radio_rssi"),
                ("sensor", f"{DECIMAL}-radio_last_seen"),
                ("sensor", f"{DECIMAL}-radio_repeater_count"),
                ("sensor", f"{DECIMAL}-signal_energy"),
            ),
        )
        row = UI_DEVICE_SCHEMA(
            {"id": SENDER, "platform": "switch", "name": "Stairs", "switch_type": "RPS"}
        )
        self.assertEqual(
            ui_device_entity_identities(row), (("switch", f"{DECIMAL}-0"),)
        )

    def test_device_name_is_the_first_row_of_the_sender(self):
        rows = [
            {"id": SENDER, "name": "Stairs"},
            {"id": SENDER, "name": "Stairs bottom"},
            {"id": [1, 2, 3, 4], "name": "Other"},
        ]
        self.assertEqual(
            device_names_by_sender(rows), {0x058F2233: "Stairs", 0x01020304: "Other"}
        )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DiagnosticEntityTests(unittest.IsolatedAsyncioTestCase):
    """Creation, defaults and decoding from real frames."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def _entities(self, rows):
        entry = SimpleNamespace(options={CONF_UI_DEVICES: rows})
        created: list = []
        await sensor.async_setup_entry(self.hass, entry, created.extend)
        return created

    async def test_one_set_per_sender_disabled_and_diagnostic(self):
        rows = [
            {
                "id": SENDER,
                "platform": "switch",
                "name": "Stairs",
                "channel": 0,
                "switch_type": "RPS",
            },
            {
                "id": SENDER,
                "platform": "switch",
                "name": "Stairs bottom",
                "channel": 1,
                "switch_type": "RPS",
            },
            {"id": [1, 2, 3, 4], "platform": "binary_sensor", "name": "Rocker"},
        ]
        created = await self._entities(rows)
        diagnostics = [
            entity
            for entity in created
            if isinstance(entity, sensor.EnOceanRadioDiagnosticSensor)
        ]
        self.assertEqual(len(diagnostics), 8)
        stairs = [e for e in diagnostics if e.dev_id == SENDER]
        self.assertEqual(
            sorted(e.unique_id for e in stairs),
            sorted(
                unique_id
                for _platform, unique_id in sender_diagnostic_identities(SENDER)
            ),
        )
        for entity in diagnostics:
            self.assertFalse(entity.entity_registry_enabled_default)
            self.assertEqual(entity.entity_category, EntityCategory.DIAGNOSTIC)
            self.assertTrue(entity.has_entity_name)
            self.assertIn(entity.translation_key, RADIO_DIAGNOSTIC_KEYS)
        # Named after the device (first row), not after the second channel.
        self.assertTrue(all(e.dev_name == "Stairs" for e in stairs))
        self.assertEqual(stairs[0].device_info["name"], "Stairs")

    async def test_values_come_from_the_radio_envelope_of_real_frames(self):
        rows = [{"id": SENDER, "platform": "binary_sensor", "name": "Rocker"}]
        rssi, last_seen, repeater, energy = [
            entity
            for entity in await self._entities(rows)
            if isinstance(entity, sensor.EnOceanRadioDiagnosticSensor)
        ]
        for entity in (rssi, last_seen, repeater, energy):
            entity.schedule_update_ha_state = lambda: None
        self.assertEqual(rssi.device_class, SensorDeviceClass.SIGNAL_STRENGTH)
        self.assertEqual(rssi.native_unit_of_measurement, "dBm")
        self.assertEqual(last_seen.device_class, SensorDeviceClass.TIMESTAMP)
        self.assertEqual(energy.device_class, SensorDeviceClass.BATTERY)
        self.assertEqual(energy.native_unit_of_measurement, "%")

        # RPS press, RSSI -61 dBm, status 0x32: N-message with 2 repeater hops.
        rps = _parse([RORG.RPS, 0x50, *SENDER, 0x32], dbm=61)
        before = datetime.now(UTC)
        for entity in (rssi, last_seen, repeater, energy):
            entity.value_changed(rps)
        self.assertEqual(rssi.native_value, -61)
        self.assertEqual(repeater.native_value, 2)
        self.assertGreaterEqual(last_seen.native_value, before)
        self.assertIsNone(energy.native_value, "RPS carries no SIGNAL payload")

        # A VLD telegram carries RSSI and a timestamp but no repeater nibble.
        vld = _parse([RORG.VLD, 0x04, 0x00, 0x64, *SENDER, 0x00], dbm=80)
        for entity in (rssi, last_seen, repeater, energy):
            entity.value_changed(vld)
        self.assertEqual(rssi.native_value, -80)
        self.assertEqual(repeater.native_value, 2, "kept: VLD has no hop count")

        # SIGNAL telegram, MID 0x06, energy status 42 %; it still carries an
        # RSSI envelope like any other telegram.
        signal = _parse([RORG.SIGNAL, 0x06, 42, *SENDER, 0x00], dbm=70)
        for entity in (rssi, last_seen, repeater, energy):
            entity.value_changed(signal)
        self.assertEqual(rssi.native_value, -70)
        self.assertEqual(energy.native_value, 42)
        self.assertEqual(repeater.native_value, 2, "kept: SIGNAL has no hop count")

        # Reserved energy values (101..255) are dropped, not guessed at.
        reserved = _parse([RORG.SIGNAL, 0x06, 255, *SENDER, 0x00], dbm=70)
        energy.value_changed(reserved)
        self.assertEqual(energy.native_value, 42, "kept: reserved value ignored")

        # Telegrams from another sender are filtered by the base entity.
        other = _parse([RORG.RPS, 0x70, 1, 2, 3, 4, 0x30], dbm=40)
        rssi._message_received_callback(other)
        self.assertEqual(rssi.native_value, -70)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DeletionTests(unittest.IsolatedAsyncioTestCase):
    """Deleting the last row of a sender also removes its diagnostics."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.registry = er.async_get(self.hass)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _flow(self, rows):
        entry = ConfigEntry(
            data={"device": "/dev/test"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=2,
            options={CONF_UI_DEVICES: list(rows)},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        self.hass.config_entries._entries[entry.entry_id] = entry
        flow = options_flow.EnOceanOptionsFlow()
        flow.hass, flow.handler = self.hass, entry.entry_id
        return flow

    def _seed_registry(self, rows):
        for row in rows:
            for platform, unique_id in ui_device_entity_identities(row):
                self.registry.async_get_or_create(platform, DOMAIN, unique_id)
        for platform, unique_id in sender_diagnostic_identities(SENDER):
            self.registry.async_get_or_create(platform, DOMAIN, unique_id)

    def _diagnostics_present(self) -> list[bool]:
        return [
            self.registry.async_get_entity_id(platform, DOMAIN, unique_id) is not None
            for platform, unique_id in sender_diagnostic_identities(SENDER)
        ]

    async def test_diagnostics_survive_while_another_row_shares_the_sender(self):
        rows = [
            UI_DEVICE_SCHEMA(
                {
                    "id": SENDER,
                    "platform": "switch",
                    "name": "A",
                    "channel": 0,
                    "switch_type": "default",
                }
            ),
            UI_DEVICE_SCHEMA(
                {
                    "id": SENDER,
                    "platform": "switch",
                    "name": "B",
                    "channel": 1,
                    "switch_type": "default",
                }
            ),
        ]
        self._seed_registry(rows)
        flow = self._flow(rows)
        await flow.async_step_manage({"device": "1"})
        result = await flow.async_step_delete_confirm({"confirm": True})
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        self.assertEqual(len(result["data"][CONF_UI_DEVICES]), 1)
        self.assertEqual(self._diagnostics_present(), [True, True, True, True])

    async def test_diagnostics_go_with_the_last_row_of_the_sender(self):
        rows = [
            UI_DEVICE_SCHEMA(
                {
                    "id": SENDER,
                    "platform": "switch",
                    "name": "A",
                    "channel": 0,
                    "switch_type": "default",
                }
            ),
            UI_DEVICE_SCHEMA(
                {"id": [1, 2, 3, 4], "platform": "binary_sensor", "name": "Other"}
            ),
        ]
        self._seed_registry(rows)
        flow = self._flow(rows)
        await flow.async_step_manage({"device": "0"})
        result = await flow.async_step_delete_confirm({"confirm": True})
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        self.assertEqual(self._diagnostics_present(), [False, False, False, False])
        # The other sender's row is untouched.
        self.assertIsNotNone(
            self.registry.async_get_entity_id("binary_sensor", DOMAIN, "16909060-None")
        )


if __name__ == "__main__":
    unittest.main()

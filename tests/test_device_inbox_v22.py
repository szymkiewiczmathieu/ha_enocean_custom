"""Unit tests for the passive v2.2 inbox, DDF artifact and A5-14-01."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace

from scripts.import_ddf import report_conflicts

try:
    from custom_components.enocean_custom.binary_sensor import EnOceanA514Contact
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        PARSE_RESULT,
        RORG,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import Packet
    from custom_components.enocean_custom.inbox import (
        MAX_UNCONFIGURED_SENDERS,
        DeviceInbox,
        packet_observation,
    )
    from custom_components.enocean_custom.sensor import EnOceanA514Voltage

    HA_AVAILABLE = True
except ModuleNotFoundError:
    HA_AVAILABLE = False

_ROOT = Path(__file__).resolve().parents[1]


def _parse_4bs(db3: int, db2: int, db1: int, db0: int, *, status: int = 0x00):
    """Build and parse a real ESP3 RADIO_ERP1 4BS frame, as the dongle would.

    Going through the wire format is what proves the byte offsets used by the
    A5-14-01 decoders: a hand-built stub would agree with any indexing mistake.
    """
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.BS4, db3, db2, db1, db0, 0x01, 0x02, 0x03, 0x04, status],
        optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
    ).build()
    result, remaining, packet = Packet.parse_msg(bytearray(wire))
    if result != PARSE_RESULT.OK or remaining:
        raise AssertionError(f"could not parse synthetic 4BS frame: {result}")
    return packet


class DDFArtifactTests(unittest.TestCase):
    """The committed local artifact preserves exact TX/RX DDF evidence."""

    def test_cositherm_variants_are_not_conflated(self):
        catalog = json.loads(
            (
                _ROOT / "custom_components/enocean_custom/data/ddf_catalog.json"
            ).read_text()
        )["products"]
        for product_id in ("002D00000004", "002D00000005"):
            self.assertEqual(catalog[product_id]["tx_eeps"], ["B0-00-00"])
            self.assertEqual(len(catalog[product_id]["rx_eeps"]), 11)
            self.assertNotIn("D2-34-10", catalog[product_id]["rx_eeps"])
        for product_id in ("002D0000000A", "002D0000000B"):
            self.assertEqual(catalog[product_id]["tx_eeps"], ["D2-34-10"])
            self.assertIn("D2-34-10", catalog[product_id]["rx_eeps"])

    def test_provenance_is_auditable(self):
        catalog = json.loads(
            (
                _ROOT / "custom_components/enocean_custom/data/ddf_catalog.json"
            ).read_text()
        )
        self.assertEqual(catalog["provenance"]["schema"], "1.3")
        self.assertEqual(catalog["provenance"]["audit_date"], "2026-08-05")

    def test_generated_json_is_canonical_and_ddf_conflict_wins(self):
        path = _ROOT / "custom_components/enocean_custom/data/ddf_catalog.json"
        catalog = json.loads(path.read_text())
        self.assertEqual(
            path.read_text(), json.dumps(catalog, indent=2, sort_keys=True) + "\n"
        )
        errors = io.StringIO()
        with redirect_stderr(errors):
            conflicts = report_conflicts(catalog, {"002D00000004": "D2-34-10"})
        self.assertEqual(conflicts, 1)
        self.assertIn("DDF wins", errors.getvalue())


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class PassiveInboxTests(unittest.TestCase):
    """Inbox updates are bounded and contain only received radio facts."""

    def test_unknown_lru_is_strictly_capped_but_configured_sender_survives(self):
        configured = {0xFFFFFFFF}
        inbox = DeviceInbox(lambda: configured)
        for sender in range(MAX_UNCONFIGURED_SENDERS + 5):
            inbox.observe(
                packet_observation(
                    SimpleNamespace(sender_int=sender, rorg=RORG.BS4, dBm=-70, status=2)
                )
            )
        inbox.observe(
            packet_observation(
                SimpleNamespace(sender_int=0xFFFFFFFF, rorg=RORG.RPS, dBm=-42, status=3)
            )
        )
        self.assertEqual(inbox.observed_senders_count, MAX_UNCONFIGURED_SENDERS + 1)
        self.assertIsNotNone(inbox.get(0xFFFFFFFF))
        self.assertIsNone(inbox.get(0))

    def test_the_configured_set_is_not_resolved_on_every_telegram(self):
        # Resolving it re-validates every UI row, so an inbox that cannot
        # possibly be over its cap must not pay that cost per packet.
        calls = 0

        def configured():
            nonlocal calls
            calls += 1
            return ()

        inbox = DeviceInbox(configured)
        for sender in range(MAX_UNCONFIGURED_SENDERS):
            inbox.observe(
                packet_observation(
                    SimpleNamespace(sender_int=sender, rorg=RORG.BS4, dBm=-70, status=0)
                )
            )
        self.assertEqual(calls, 0)
        inbox.observe(
            packet_observation(
                SimpleNamespace(sender_int=0xABCDEF, rorg=RORG.BS4, dBm=-70, status=0)
            )
        )
        self.assertEqual(calls, 1)
        self.assertEqual(inbox.observed_senders_count, MAX_UNCONFIGURED_SENDERS)

    def test_status_rssi_rorg_and_reset(self):
        inbox = DeviceInbox(set)
        inbox.observe(
            packet_observation(
                SimpleNamespace(sender_int=1, rorg=RORG.BS4, dBm=-61, status=0xA2)
            )
        )
        entry = inbox.get(1)
        self.assertEqual(entry.last_rssi_dbm, -61)
        self.assertEqual(entry.last_repeater_count, 2)
        self.assertEqual(entry.rorgs, {int(RORG.BS4)})
        inbox.clear()
        self.assertEqual(inbox.observed_senders_count, 0)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class A51401DecoderTests(unittest.TestCase):
    """Decode documented A5-14-01 field vectors from real parsed frames."""

    def _decoders(self):
        contact = EnOceanA514Contact([1, 2, 3, 4], "Door")
        voltage = EnOceanA514Voltage(
            [1, 2, 3, 4], "Door", SimpleNamespace(key="voltage", name="Voltage")
        )
        contact.schedule_update_ha_state = lambda: None
        voltage.schedule_update_ha_state = lambda: None
        return contact, voltage

    def test_open_contact_and_supply_voltage(self):
        # EEP 2.6.8 A5-14-01: DB3 SVC=125 => 2.5 V; DB0.0 CT=1 => open.
        # DB0.3 is set, marking a data telegram rather than a teach-in.
        packet = _parse_4bs(125, 0, 0, 0x09)
        self.assertFalse(packet.learn)
        contact, voltage = self._decoders()
        contact.value_changed(packet)
        voltage.value_changed(packet)
        self.assertTrue(contact._attr_is_on)
        self.assertEqual(voltage._attr_native_value, 2.5)

    def test_closed_contact(self):
        contact, _voltage = self._decoders()
        contact.value_changed(_parse_4bs(250, 0, 0, 0x08))
        self.assertFalse(contact._attr_is_on)

    def test_reserved_voltage_error_is_unknown(self):
        _contact, voltage = self._decoders()
        voltage.value_changed(_parse_4bs(251, 0, 0, 0x08))
        self.assertIsNone(getattr(voltage, "_attr_native_value", None))

    def test_a_teach_in_telegram_never_becomes_a_measurement(self):
        # 4BS teach-in with EEP: DB3/DB2/DB1 carry FUNC/TYPE/manufacturer and
        # DB0.3 is clear. Decoding it would publish CT=closed and SVC=1.6 V.
        packet = _parse_4bs(0x50, 0x08, 0x16, 0x80)
        self.assertTrue(packet.learn)
        contact, voltage = self._decoders()
        contact.value_changed(packet)
        voltage.value_changed(packet)
        self.assertIsNone(getattr(contact, "_attr_is_on", None))
        self.assertIsNone(getattr(voltage, "_attr_native_value", None))

    def test_a_real_frame_feeds_the_inbox(self):
        inbox = DeviceInbox(set)
        inbox.observe(packet_observation(_parse_4bs(125, 0, 0, 0x09, status=0x02)))
        entry = inbox.get(0x01020304)
        self.assertEqual(entry.last_rssi_dbm, -0x40)
        self.assertEqual(entry.last_repeater_count, 2)
        self.assertEqual(entry.rorgs, {int(RORG.BS4)})


if __name__ == "__main__":
    unittest.main()

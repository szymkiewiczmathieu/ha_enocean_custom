"""RORG 0xD0 SIGNAL telegrams: decoder correctness against the official spec.

Byte layouts are taken from the EnOcean Alliance "Signal Telegram" system
specification V3.6 (https://www.enocean-alliance.org/st/), cross-checked
against the worked RX-channel-quality example in the EnOcean TCM 515 user
manual. Frames are built and parsed through the real ESP3 wire format so a
mistake in the vendored library's framing would also be caught here.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RORG,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import Packet
    from custom_components.enocean_custom.signal_telegram import (
        MID_ENERGY_STATUS,
        MID_HEARTBEAT,
        MID_PRODUCT_ID,
        MID_REVISION,
        MID_RX_CHANNEL_QUALITY,
        decode_energy_status,
        decode_product_id,
        decode_revision,
        decode_rx_channel_quality,
        decode_signal_telegram,
        is_heartbeat,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [0x05, 0x8F, 0x22, 0x33]


def _frame(payload: list[int]) -> object:
    """Build and parse a real ESP3 RADIO_ERP1 frame carrying ``payload``."""
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.SIGNAL, *payload, *SENDER, 0x00],
        optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
    ).build()
    return Packet.parse_msg(bytearray(wire))[2]


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class RorgTests(unittest.TestCase):
    def test_signal_rorg_value(self):
        self.assertEqual(int(RORG.SIGNAL), 0xD0)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class EnergyStatusTests(unittest.TestCase):
    def test_typical_value(self):
        status = decode_energy_status(_frame([MID_ENERGY_STATUS, 73]).data)
        self.assertEqual(status.percent, 73)

    def test_gasp_of_death_is_zero_not_reserved(self):
        status = decode_energy_status(_frame([MID_ENERGY_STATUS, 0]).data)
        self.assertEqual(status.percent, 0)

    def test_values_above_100_are_reserved(self):
        for raw in (101, 200, 255):
            with self.subTest(raw=raw):
                self.assertIsNone(
                    decode_energy_status(_frame([MID_ENERGY_STATUS, raw]).data)
                )

    def test_wrong_mid_or_length_is_rejected(self):
        self.assertIsNone(decode_energy_status(_frame([MID_HEARTBEAT, 50]).data))
        self.assertIsNone(decode_energy_status([RORG.SIGNAL, MID_ENERGY_STATUS]))
        self.assertIsNone(decode_energy_status(None))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class RevisionTests(unittest.TestCase):
    def test_sw_and_hw_version_format(self):
        revision = decode_revision(_frame([MID_REVISION, 1, 2, 3, 4, 5, 6, 7, 8]).data)
        self.assertEqual(revision.sw_version, "1.2.3.4")
        self.assertEqual(revision.hw_version, "5.6.7.8")

    def test_wrong_length_is_rejected(self):
        self.assertIsNone(decode_revision(_frame([MID_REVISION, 1, 2, 3]).data))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class HeartbeatTests(unittest.TestCase):
    def test_heartbeat_has_no_payload(self):
        self.assertTrue(is_heartbeat(_frame([MID_HEARTBEAT]).data))

    def test_extra_bytes_are_not_a_heartbeat(self):
        self.assertFalse(is_heartbeat(_frame([MID_HEARTBEAT, 0]).data))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class RxChannelQualityTests(unittest.TestCase):
    def test_worked_example_from_the_spec(self):
        # EURID 0x058F2233, worst -40 dBm (127-167=-40 => raw 167), best -10
        # dBm (raw 137), 4 sub-telegrams, repeater level 1.
        quality = decode_rx_channel_quality(
            _frame(
                [MID_RX_CHANNEL_QUALITY, 0x05, 0x8F, 0x22, 0x33, 167, 137, 0x41]
            ).data
        )
        self.assertEqual(quality.reported_sender_id, 0x058F2233)
        self.assertEqual(quality.dbm_worst, -40)
        self.assertEqual(quality.dbm_best, -10)
        self.assertEqual(quality.subtelegram_count, 4)
        self.assertEqual(quality.max_repeater_level, 1)

    def test_unknown_and_reserved_enumerations(self):
        quality = decode_rx_channel_quality(
            _frame([MID_RX_CHANNEL_QUALITY, 0, 0, 0, 0, 0xFF, 0xFF, 0x0F]).data
        )
        self.assertIsNone(quality.dbm_worst)
        self.assertIsNone(quality.dbm_best)
        self.assertIsNone(quality.subtelegram_count, "0x0 nibble means unknown")
        self.assertIsNone(quality.max_repeater_level, "0xF nibble means unknown")

        reserved = decode_rx_channel_quality(
            _frame([MID_RX_CHANNEL_QUALITY, 0, 0, 0, 0, 0, 0, 0x1D]).data
        )
        self.assertIsNone(reserved.max_repeater_level, "0x3..0xE is reserved")

    def test_dbm_extremes(self):
        quality = decode_rx_channel_quality(
            _frame([MID_RX_CHANNEL_QUALITY, 0, 0, 0, 0, 0, 254, 0x00]).data
        )
        self.assertEqual(quality.dbm_worst, 127)
        self.assertEqual(quality.dbm_best, -127)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ProductIdTests(unittest.TestCase):
    def test_manufacturer_and_reference_split(self):
        product = decode_product_id(
            _frame([MID_PRODUCT_ID, 0x04, 0x6D, 0xDE, 0xAD, 0xBE, 0xEF]).data
        )
        self.assertEqual(product.manufacturer_id, 0x046D)
        self.assertEqual(product.product_reference, 0xDEADBEEF)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DispatcherTests(unittest.TestCase):
    def test_dispatch_picks_the_right_decoder(self):
        snapshot = decode_signal_telegram(_frame([MID_ENERGY_STATUS, 60]).data)
        self.assertEqual(snapshot.mid, MID_ENERGY_STATUS)
        self.assertEqual(snapshot.energy_status.percent, 60)
        self.assertIsNone(snapshot.revision)
        self.assertFalse(snapshot.heartbeat)

        snapshot = decode_signal_telegram(_frame([MID_HEARTBEAT]).data)
        self.assertTrue(snapshot.heartbeat)
        self.assertIsNone(snapshot.energy_status)

    def test_non_signal_or_unsupported_mid_is_none(self):
        self.assertIsNone(decode_signal_telegram([RORG.VLD, MID_ENERGY_STATUS, 50]))
        unsupported = _frame([0x11, 0, 0]).data  # MID 0x11 is not implemented.
        self.assertIsNone(decode_signal_telegram(unsupported))
        self.assertIsNone(decode_signal_telegram(None))
        self.assertIsNone(decode_signal_telegram([RORG.SIGNAL]))


if __name__ == "__main__":
    unittest.main()

"""Release 2.15 workstream 3: specification-backed additive A5 profiles."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.enocean_custom.eep_catalog import generic_profile
from custom_components.enocean_custom.enocean_library.protocol.constants import (
    PACKET,
    RORG,
)
from custom_components.enocean_custom.enocean_library.protocol.packet import Packet
from custom_components.enocean_custom.identity import generic_eep_identities

SENDER = [0x01, 0x02, 0x03, 0x04]
DECIMAL = "16909060"
NEW_PROFILES = ("A5-04-02", "A5-07-02", "A5-07-03", "A5-08-02", "A5-08-03")


def _parsed(data):
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=data,
        optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
    ).build()
    return Packet.parse_msg(bytearray(wire))[2]


def _4bs(db3, db2, db1, db0):
    return _parsed([RORG.BS4, db3, db2, db1, db0, *SENDER, 0x00])


class AdditiveProfileTests(unittest.TestCase):
    def test_new_profiles_have_new_identities_and_reject_teach_in(self):
        for eep in NEW_PROFILES:
            with self.subTest(eep=eep):
                profile = generic_profile(eep)
                self.assertIsNotNone(profile)
                identities = generic_eep_identities(SENDER, eep)
                self.assertTrue(identities)
                self.assertTrue(
                    all(uid.startswith(f"{DECIMAL}-eep_") for _p, uid in identities)
                )
                # Same layout bytes with LRN=0 carry teach-in data, not measurements.
                self.assertIsNone(profile.decode(_4bs(0x10, 0x28, 0x00, 0x80).data))

    def test_a5_04_02_humidity_temperature(self):
        profile = generic_profile("A5-04-02")
        self.assertEqual(
            profile.decode(_4bs(0x00, 0x7D, 0x7D, 0x0A).data),
            {"hum": 50.0, "tmp": 20.0},
        )

    def test_a5_07_02_supply_and_motion_polarity(self):
        profile = generic_profile("A5-07-02")
        self.assertEqual(
            profile.decode(_4bs(0xB4, 0x00, 0x00, 0x88).data),
            {"svc": 3.6, "pirm": 1},
        )
        self.assertEqual(profile.decode(_4bs(0xB4, 0x00, 0x00, 0x08).data)["pirm"], 0)
        self.assertIsNone(profile.decode(_4bs(251, 0x00, 0x00, 0x88).data).get("svc"))

    def test_a5_07_03_ten_bit_illuminance_and_motion(self):
        profile = generic_profile("A5-07-03")
        self.assertEqual(
            profile.decode(_4bs(0xB4, 0x19, 0x00, 0x08).data),
            {"svc": 3.6, "ill": 100.0, "pirm": 0},
        )
        self.assertEqual(profile.decode(_4bs(0xB4, 0x19, 0x00, 0x88).data)["pirm"], 1)
        for raw in (1001, 1002, 1023):
            db2, db1 = raw >> 2, (raw & 3) << 6
            with self.subTest(raw=raw):
                self.assertNotIn("ill", profile.decode(_4bs(0xB4, db2, db1, 0x08).data))

    def test_a5_08_02_and_03_ranges(self):
        profile02 = generic_profile("A5-08-02")
        self.assertEqual(
            profile02.decode(_4bs(0xFF, 0xFF, 0xFF, 0x08).data),
            {"svc": 5.1, "ill": 1020.0, "tmp": 51.0, "pirs": 0},
        )
        profile03 = generic_profile("A5-08-03")
        self.assertEqual(
            profile03.decode(_4bs(0xFF, 0xFF, 0xFF, 0x0A).data),
            {"svc": 5.1, "ill": 1530.0, "tmp": 50.0, "pirs": 1},
        )
        self.assertEqual(profile03.decode(_4bs(0, 0, 0, 0x08).data)["tmp"], -30.0)

    def test_a5_08_01_polarity_is_unchanged(self):
        profile = generic_profile("A5-08-01")
        self.assertEqual(profile.decode(_4bs(125, 128, 128, 0x09).data)["pirs"], 0)
        pirs = next(field for field in profile.fields if field.shortcut == "PIRS")
        self.assertEqual(pirs.semantics.on_values, frozenset({0}))


if __name__ == "__main__":
    unittest.main()

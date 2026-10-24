"""Every one of the 231 production entities is minted by a frozen formula.

The 2.10.2 installation this release is deployed on has 231 enocean_custom
entities. This test recomputes, from the configured rows alone, every
``(domain, unique_id)`` the platforms create -- row entities, per-sender radio
diagnostics, rocker events and the gateway's own entities -- and requires the
result to be exactly the registry content: nothing missing, nothing extra.
It then does the same from the rows as the device subentries hold them after
the minor-3 migration, which proves the migration cannot change an identity.

Runs on the anonymized copy by default and on the real export when
``ENOCEAN_LIVE_SNAPSHOT_DIR`` is set (see ``live_snapshot.py``).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from tests.live_snapshot import expected_identities, load_snapshot

try:  # The bare unit-test environment has no Home Assistant installed.
    from custom_components.enocean_custom.device_rows import (
        build_device_subentry,
        group_rows_by_sender,
        subentry_rows,
    )
    from custom_components.enocean_custom.schema import valid_ui_devices

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class LiveParityTests(unittest.TestCase):
    """231 of 231, from the options rows and from the subentry rows alike."""

    @classmethod
    def setUpClass(cls):
        cls.snapshot = load_snapshot()
        cls.rows = valid_ui_devices(cls.snapshot["entry"]["options"]["ui_devices"])
        cls.registry = {
            (entity["domain"], entity["unique_id"])
            for entity in cls.snapshot["entities"]
        }

    def test_snapshot_has_the_production_shape(self):
        self.assertEqual(len(self.snapshot["entry"]["options"]["ui_devices"]), 43)
        self.assertEqual(len(self.rows), 43, "every production row is valid")
        self.assertEqual(len(group_rows_by_sender(self.rows)), 40)
        self.assertEqual(len(self.snapshot["devices"]), 41)
        self.assertEqual(len(self.snapshot["entities"]), 231)
        self.assertEqual(len(self.registry), 231, "unique_ids are unique")
        self.assertEqual(
            sum(1 for e in self.snapshot["entities"] if e["disabled_by"]), 161
        )

    def test_every_registry_entity_is_computed_from_the_options_rows(self):
        computed = expected_identities(self.rows, self.snapshot["entry_id"])
        self.assertEqual(sorted(self.registry - computed), [], "live, not computed")
        self.assertEqual(sorted(computed - self.registry), [], "computed, not live")
        self.assertEqual(len(computed), 231)

    def test_subentry_rows_mint_the_very_same_231_identities(self):
        subentry_based: list[dict] = []
        for sender_rows in group_rows_by_sender(self.rows).values():
            subentry = build_device_subentry(sender_rows)
            self.assertEqual(subentry.unique_id, f"{int(subentry.unique_id, 16):08X}")
            subentry_based.extend(subentry_rows(subentry))
        self.assertEqual(len(subentry_based), 43)
        self.assertEqual(
            expected_identities(subentry_based, self.snapshot["entry_id"]),
            self.registry,
        )


if __name__ == "__main__":
    unittest.main()

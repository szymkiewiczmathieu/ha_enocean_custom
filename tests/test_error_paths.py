"""Error paths: a task exception nobody retrieves, an unbounded map, bad options.

* The D2-05-00 start-up query runs in a background task. A command the dongle
  refuses there (worker stopped in between, transmit queue full) must end the
  task with a log line, not with an exception nobody retrieves.
* The repeated-UTE-request tracker keeps one timestamp list per sender. Senders
  that stop asking must not stay in memory for the lifetime of the dongle.
* ``ui_devices`` is hand-editable in ``.storage``. A value that is not a list
  (``null``, a number, a boolean) means "no rows": it must not raise where the
  entry is set up or where the mirror is synchronised.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant

    import custom_components.enocean_custom as integration
    from custom_components.enocean_custom import cover
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.device_rows import (
        async_sync_mirror,
        mirror_rows,
        stored_ui_rows,
    )
    from custom_components.enocean_custom.dongle import RepeatedTeachInTracker

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

ACTUATOR = [0x01, 0x02, 0x03, 0x04]
BASE_ID = [0xFF, 0x80, 0x00, 0x00]


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class CoverStartupQueryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def test_a_refused_start_up_query_ends_the_task_without_an_exception(self):
        entity = cover.EnOceanCover(ACTUATOR, "Bedroom shutter")
        entity.hass = self.hass
        # The Base ID is resolved, but the dongle refuses the command.
        gateway = SimpleNamespace(base_id=BASE_ID)
        entity.send_command = lambda *args, **kwargs: False

        with (
            patch.object(cover, "get_gateway", return_value=gateway),
            self.assertLogs("custom_components.enocean_custom", "WARNING") as logs,
        ):
            task = asyncio.ensure_future(entity._async_query_after_start())
            await asyncio.wait([task], timeout=2)

        self.assertTrue(task.done())
        self.assertIsNone(
            task.exception(), "the refusal escaped as an unretrieved task exception"
        )
        self.assertTrue(any("start-up query" in line for line in logs.output))
        self.assertIs(entity._last_command_esp3_accepted, False)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class RepeatedTeachInTrackerTests(unittest.TestCase):
    def test_senders_that_stopped_asking_are_forgotten(self):
        tracker = RepeatedTeachInTracker()
        for sender in range(5_000):
            tracker.observe(sender, 100.0)
        # Long after the 300 s window, one more request arrives.
        tracker.observe(0xAABBCCDD, 100.0 + 10 * tracker.WINDOW_SECONDS)
        self.assertLessEqual(len(tracker._requests), tracker.MAX_TRACKED_SENDERS)
        self.assertIn(0xAABBCCDD, tracker._requests)

    def test_a_flood_of_distinct_senders_stays_bounded(self):
        tracker = RepeatedTeachInTracker()
        for sender in range(20_000):
            tracker.observe(sender, 100.0 + sender * 0.001)
        self.assertLessEqual(len(tracker._requests), tracker.MAX_TRACKED_SENDERS)

    def test_the_hint_still_fires_on_the_fourth_request_in_the_window(self):
        tracker = RepeatedTeachInTracker()
        results = [tracker.observe(7, 100.0 + second) for second in range(5)]
        self.assertEqual(results, [False, False, False, True, False])
        self.assertEqual(tracker.hinted_senders(105.0), 1)
        self.assertEqual(tracker.hinted_senders(105.0 + tracker.WINDOW_SECONDS), 0)

    def test_a_sender_being_counted_survives_a_flood(self):
        tracker = RepeatedTeachInTracker()
        for second in range(3):
            tracker.observe(7, 100.0 + second)
        for sender in range(1_000, 1_000 + 2 * tracker.MAX_TRACKED_SENDERS):
            tracker.observe(sender, 103.0)
        # The fourth request of the retrying sender still raises the hint.
        self.assertTrue(tracker.observe(7, 104.0))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class CorruptedOptionsTests(unittest.IsolatedAsyncioTestCase):
    """A ``ui_devices`` value that is not a list reads as "no rows"."""

    NOT_A_LIST = (None, 5, True, 1.5)

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _entry(self, stored) -> ConfigEntry:
        entry = ConfigEntry(
            data={"device": "/dev/test"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=3,
            options={"ui_devices": stored},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        self.hass.config_entries._entries[entry.entry_id] = entry
        return entry

    def test_stored_rows_are_a_copy_of_a_list_and_empty_otherwise(self):
        rows = [{"id": [1, 2, 3, 4]}, "not a row"]
        copy = stored_ui_rows({"ui_devices": rows})
        self.assertEqual(copy, rows)
        self.assertIsNot(copy, rows)
        for stored in (*self.NOT_A_LIST, "text", {"a": 1}):
            with self.subTest(stored=stored):
                self.assertEqual(stored_ui_rows({"ui_devices": stored}), [])
        self.assertEqual(stored_ui_rows({}), [])
        self.assertEqual(stored_ui_rows(None), [])

    async def test_the_update_listener_is_built_from_a_corrupted_value(self):
        for stored in self.NOT_A_LIST:
            with self.subTest(stored=stored):
                entry = self._entry(stored)
                listener = integration._async_entry_updated(entry)
                # A notification that changes nothing reconciles nothing.
                await listener(self.hass, entry)
                self.assertEqual(entry.options["ui_devices"], stored)

    async def test_the_mirror_is_left_alone_when_there_is_nothing_to_mirror(self):
        for stored in self.NOT_A_LIST:
            with self.subTest(stored=stored):
                entry = self._entry(stored)
                self.assertEqual(mirror_rows(entry), [])
                self.assertFalse(async_sync_mirror(self.hass, entry))
                self.assertEqual(entry.options["ui_devices"], stored)


if __name__ == "__main__":
    unittest.main()

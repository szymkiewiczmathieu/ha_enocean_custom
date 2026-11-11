"""What one received telegram may cost on the event loop.

Resolving the configured senders re-validates every stored row (a few
milliseconds for a hundred rows). That is fine when the configuration
changes; it must not happen for every telegram of an installation that has
heard more than 64 senders, nor for every telegram while the admin panel's
live stream is open. The last test covers the Base ID retry budget, which is
granted per serial worker and must come back with a new worker.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.enocean_custom import dongle as dongle_module
    from custom_components.enocean_custom import panel
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.dongle import EnOceanDongle
    from custom_components.enocean_custom.enocean_library.protocol.constants import RORG
    from custom_components.enocean_custom.inbox import (
        MAX_UNCONFIGURED_SENDERS,
        DeviceInbox,
        PacketObservation,
    )
    from custom_components.enocean_custom.schema import (
        CONF_UI_DEVICES,
        UI_DEVICE_SCHEMA,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

CONFIGURED = range(0x01000000, 0x01000000 + 70)
NEIGHBOURS = range(0x02000000, 0x02000000 + 10)
SHUTTER_BYTES = (0x01, 0x02, 0x03, 0x04)
SHUTTER = 0x01020304


def _observation(sender: int) -> PacketObservation:
    return PacketObservation(sender, RORG.BS4, -70, 0)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class InboxHotPathTests(unittest.TestCase):
    """Eviction is only due when the set of observed senders grows."""

    def setUp(self):
        self.configured = set(CONFIGURED)
        self.calls = 0

        def configured_ids():
            self.calls += 1
            return self.configured

        self.inbox = DeviceInbox(configured_ids)
        for sender in (*CONFIGURED, *NEIGHBOURS):
            self.inbox.observe(_observation(sender))
        self.assertGreater(self.inbox.observed_senders_count, MAX_UNCONFIGURED_SENDERS)
        self.calls = 0

    def test_known_senders_never_resolve_the_configuration_again(self):
        senders = (*CONFIGURED, *NEIGHBOURS)
        for index in range(400):
            self.inbox.observe(_observation(senders[index % len(senders)]))
        self.assertEqual(self.calls, 0, "every telegram re-validated the stored rows")
        self.assertEqual(self.inbox.get(senders[0]).packet_count, 6)

    def test_a_new_sender_still_enforces_the_cap(self):
        for sender in range(0x03000000, 0x03000000 + MAX_UNCONFIGURED_SENDERS):
            self.inbox.observe(_observation(sender))
        unknown = [
            sender
            for sender, _entry in self.inbox.observations()
            if sender not in self.configured
        ]
        self.assertEqual(len(unknown), MAX_UNCONFIGURED_SENDERS)
        # The ten oldest unknown senders went first; configured ones all stay.
        self.assertIsNone(self.inbox.get(NEIGHBOURS[0]))
        self.assertTrue(all(self.inbox.get(sender) for sender in CONFIGURED))
        self.assertEqual(self.calls, MAX_UNCONFIGURED_SENDERS)

    def test_lru_order_still_follows_the_last_telegram(self):
        # The oldest neighbour speaks again, then the cap is exceeded by one:
        # the neighbour that has been quiet the longest is the one evicted.
        self.inbox.observe(_observation(NEIGHBOURS[0]))
        for sender in range(0x03000000, 0x03000000 + MAX_UNCONFIGURED_SENDERS - 9):
            self.inbox.observe(_observation(sender))
        self.assertIsNotNone(self.inbox.get(NEIGHBOURS[0]))
        self.assertIsNone(self.inbox.get(NEIGHBOURS[1]))

    def test_a_sender_removed_from_the_configuration_leaves_with_the_next_new_one(
        self,
    ):
        # 60 senders are dropped from the configuration: with the ten
        # neighbours that makes 70 unknown senders, six over the cap.
        self.configured = set(list(CONFIGURED)[60:])
        self.inbox.observe(_observation(CONFIGURED[0]))
        self.assertEqual(self.calls, 0, "known senders never trigger an eviction")
        self.inbox.observe(_observation(0x03000000))
        unknown = [
            sender
            for sender, _entry in self.inbox.observations()
            if sender not in self.configured
        ]
        self.assertEqual(len(unknown), MAX_UNCONFIGURED_SENDERS)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class PanelStreamHotPathTests(unittest.IsolatedAsyncioTestCase):
    """The live stream looks a sender's profile up without re-validating rows."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.entry = ConfigEntry(
            data={"device": "/dev/test-panel"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=3,
            options={CONF_UI_DEVICES: [self._row("D2-05-00")]},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry
        self.gateway = SimpleNamespace(
            entry_id=self.entry.entry_id, telegram_monitor=None
        )
        panel.async_setup_entry_panel(self.hass, self.entry, self.gateway)
        self.monitor = self.gateway.telegram_monitor
        self.records: list[dict] = []
        self.monitor.subscribe(self.records.append, anonymize=True)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _row(self, eep: str) -> dict:
        return UI_DEVICE_SCHEMA(
            {
                "id": list(SHUTTER_BYTES),
                "platform": "cover",
                "name": "Bedroom shutter",
                "channel": 0,
                "radio_metadata": {"eep": eep},
            }
        )

    def _packet(self):
        return SimpleNamespace(
            sender_int=SHUTTER,
            rorg=RORG.VLD,
            dBm=-60,
            status=0,
            data=[RORG.VLD, 40, 0x7F, 0x00, 0x04, *SHUTTER_BYTES, 0x00],
        )

    async def test_the_rows_are_validated_once_for_a_stream_of_telegrams(self):
        real = panel.configured_rows
        with patch.object(panel, "configured_rows", Mock(side_effect=real)) as rows:
            for _ in range(50):
                self.monitor.record(self._packet())
        self.assertEqual(len(self.records), 50)
        self.assertEqual(self.records[-1]["eep"], "D2-05-00")
        self.assertEqual(self.records[-1]["summary"], "Cover 60 % open, lock 0")
        self.assertLessEqual(
            rows.call_count, 1, "every telegram re-validated the stored rows"
        )

    async def test_a_configuration_change_is_seen_by_the_next_telegram(self):
        self.monitor.record(self._packet())
        self.assertEqual(self.records[-1]["eep"], "D2-05-00")

        self.hass.config_entries.async_update_entry(
            self.entry, options={CONF_UI_DEVICES: []}
        )
        await asyncio.sleep(0)
        self.monitor.record(self._packet())
        self.assertIsNone(self.records[-1]["eep"])
        self.assertEqual(self.records[-1]["summary"], "VLD telegram")

    async def test_the_entry_keeps_a_single_update_listener(self):
        # Change detection needs no listener of its own.
        self.assertEqual(self.entry.update_listeners, [])

    async def test_an_unknown_sender_has_no_profile(self):
        packet = self._packet()
        packet.sender_int = 0x0A0B0C0D
        self.monitor.record(packet)
        self.assertIsNone(self.records[-1]["eep"])


class FakeWorker:
    """A serial worker double that records the Base ID read of each start."""

    def __init__(self, *args, **kwargs):
        self.current_base_id = None
        self.base_id_request_pending = False
        self.base_id_requests = 0
        self.ident = None

    def start(self):
        self.ident = 1

    def is_alive(self):
        return self.ident is not None

    def join(self, _timeout=None):
        return None

    def request_base_id(self):
        self.base_id_requests += 1
        return True

    def request_common_command(self, _data, _handler):
        return True


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class BaseIdRetryBudgetTests(unittest.IsolatedAsyncioTestCase):
    """``BASE_ID_RETRY_LIMIT`` is documented as a budget *per worker*."""

    async def test_a_new_worker_gets_a_fresh_base_id_retry_budget(self):
        with patch.object(dongle_module, "SerialCommunicator", FakeWorker):
            dongle = EnOceanDongle(MagicMock(), "/dev/test")
        dongle.hass.async_add_executor_job.side_effect = lambda fn, *args: (
            asyncio.to_thread(fn, *args)
        )
        dongle._notify_status = Mock()
        dongle._build_communicator = FakeWorker
        dongle._communicator.start()
        dongle._available = True

        # The first worker's Base ID read was refused and every retry spent.
        for _ in range(EnOceanDongle.BASE_ID_RETRY_LIMIT):
            dongle._base_id_retry_not_before = 0.0
            self.assertTrue(dongle.retry_base_id())
        self.assertFalse(dongle.retry_base_id(), "the budget is spent")

        # The link drops and the first worker exits.
        dongle._available = False
        dongle._communicator.ident = None
        with patch.object(dongle_module, "ir"):
            await dongle._attempt_reconnect(None)
        self.assertTrue(dongle.available)
        self.assertEqual(dongle._base_id_retry_count, 0)

        # The new worker's own start-up read is refused too: a command that
        # needs the Base ID may ask again instead of failing until a reload.
        self.assertTrue(
            dongle.retry_base_id(), "the new worker inherited a spent budget"
        )


if __name__ == "__main__":
    unittest.main()

"""A smoke detector must never replay an old alarm when it comes back.

Once a detector is silent (or the dongle dropped) its entity is unavailable.
The next telegram lifts that, and the entity used to write its *previous*
state before decoding the telegram: an alarm that was on before the silence
produced ``unavailable -> on -> off`` and therefore a second, false fire alert.
The remembered alarm must not survive the loss of contact.
"""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant import loader
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import issue_registry as ir

    from custom_components.enocean_custom import binary_sensor
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.enocean_library.protocol.constants import RORG

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [0x05, 0x8C, 0x6B, 0x8D]
SENDER_INT = 0x058C6B8D


def _rps(db0: int):
    return SimpleNamespace(sender_int=SENDER_INT, rorg=RORG.RPS, data=[RORG.RPS, db0])


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class StaleAlarmTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        loader.async_setup(self.hass)
        await ir.async_load(self.hass)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        entry = ConfigEntry(
            data={"device": "/dev/test"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=2,
            options={},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        entry.runtime_data = SimpleNamespace(available=True, entry_id=entry.entry_id)
        self.hass.config_entries._entries[entry.entry_id] = entry

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def _detector(self):
        entity = binary_sensor.EnOceanSmokeDetector(SENDER, "Smoke", "smoke")
        entity.hass = self.hass
        entity.entity_id = "binary_sensor.smoke"
        entity.configure_silence(timedelta(minutes=65), repair=True)
        await entity.async_added_to_hass()
        self.written: list[tuple[bool, bool | None]] = []
        real = entity.async_write_ha_state

        def record():
            self.written.append((entity.available, entity.is_on))
            real()

        entity.async_write_ha_state = record
        entity.schedule_update_ha_state = lambda *a, **k: record()
        return entity

    async def test_alarm_before_silence_is_not_replayed_when_the_sender_returns(self):
        entity = await self._detector()
        entity._message_received_callback(_rps(0x10))
        self.assertIs(entity.is_on, True)

        entity._silence_expired()
        self.assertFalse(entity.available)
        self.written.clear()

        entity._message_received_callback(_rps(0x00))  # the next "Alarm OFF" heartbeat
        self.assertIs(entity.is_on, False)
        self.assertNotIn(
            (True, True),
            self.written,
            "an alarm remembered from before the silence was written back",
        )

    async def test_alarm_before_a_dongle_drop_is_not_replayed_on_reconnection(self):
        entity = await self._detector()
        entity._message_received_callback(_rps(0x10))
        entity._dongle_status_changed(False)
        self.assertFalse(entity.available)
        self.written.clear()

        entity._dongle_status_changed(True)
        self.assertNotIn(
            (True, True),
            self.written,
            "the old alarm was published again when the dongle came back",
        )
        entity._message_received_callback(_rps(0x00))
        self.assertIs(entity.is_on, False)

    async def test_a_real_alarm_after_silence_still_turns_on(self):
        entity = await self._detector()
        entity._message_received_callback(_rps(0x00))
        entity._silence_expired()
        entity._message_received_callback(_rps(0x10))
        self.assertTrue(entity.available)
        self.assertIs(entity.is_on, True)

    async def test_a_live_alarm_is_untouched_while_the_sender_keeps_talking(self):
        entity = await self._detector()
        entity._message_received_callback(_rps(0x10))
        entity._message_received_callback(_rps(0x10))
        self.assertIs(entity.is_on, True)
        self.assertTrue(entity.available)


if __name__ == "__main__":
    unittest.main()

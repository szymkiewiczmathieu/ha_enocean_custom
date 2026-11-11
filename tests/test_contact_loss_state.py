"""What an entity publishes when contact with its sender is lost and comes back.

Two rules, for every entity type:

* a sender that returns after a silence is published once, with what its
  telegram says. The entity must not first write the state it held before the
  silence: ``unavailable -> open -> closed`` is a door opening that never
  happened;
* silence is only evidence while the dongle is listening. A dongle outage
  must not expire the silence timers, raise one "silent device" Repair per
  sender, or leave the senders unavailable once the dongle is back.
"""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant import loader
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import issue_registry as ir

    from custom_components.enocean_custom import binary_sensor, sensor
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.enocean_library.protocol.constants import RORG

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [0x01, 0x02, 0x03, 0x04]
SENDER_INT = 0x01020304
OPEN, CLOSED = 0x08, 0x09


def _contact(db0: int):
    return SimpleNamespace(sender_int=SENDER_INT, rorg=RORG.BS1, data=[RORG.BS1, db0])


def _handle(db0: int):
    return SimpleNamespace(sender_int=SENDER_INT, rorg=RORG.RPS, data=[RORG.RPS, db0])


class _EntityCase(unittest.IsolatedAsyncioTestCase):
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
        self.written: list[tuple[bool, object]] = []

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    async def _add(self, entity, entity_id: str, *, state, real_write: bool = True):
        """Add an entity with silence detection and record every state write.

        ``real_write=False`` records without calling Home Assistant's state
        machine, for entity types that need a platform to compute a state.
        """
        entity.hass = self.hass
        entity.entity_id = entity_id
        entity.configure_silence(timedelta(minutes=30), repair=True)
        await entity.async_added_to_hass()
        real = entity.async_write_ha_state

        def record():
            self.written.append((entity.available, state(entity)))
            if real_write:
                real()

        entity.async_write_ha_state = record
        entity.schedule_update_ha_state = lambda *a, **k: record()
        return entity

    async def _door(self):
        return await self._add(
            binary_sensor.EnOceanBinarySensor(SENDER, "Pantry", "door"),
            "binary_sensor.pantry",
            state=lambda entity: entity.is_on,
        )

    def _issue(self):
        return ir.async_get(self.hass).async_get_issue(DOMAIN, "silent_device_01020304")


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ReturnFromSilenceTests(_EntityCase):
    """The first write after a silence carries the returning telegram."""

    async def test_a_door_that_returns_closed_is_never_published_open(self):
        entity = await self._door()
        entity._message_received_callback(_contact(OPEN))
        self.assertIs(entity.is_on, True)

        entity._silence_expired()
        self.assertFalse(entity.available)
        self.written.clear()

        entity._message_received_callback(_contact(CLOSED))
        self.assertTrue(entity.available)
        self.assertIs(entity.is_on, False)
        self.assertNotIn(
            (True, True),
            self.written,
            "the door was written back as open before its telegram was decoded",
        )
        self.assertTrue(self.written, "the return itself must be written")
        self.assertIsNone(self._issue(), "the repair clears itself")

    async def test_a_handle_that_returns_closed_is_never_published_open(self):
        handle = sensor.EnOceanWindowHandle(
            SENDER, "Kitchen", sensor.SENSOR_DESC_WINDOWHANDLE
        )
        with patch.object(
            handle, "async_get_last_sensor_data", AsyncMock(return_value=None)
        ):
            entity = await self._add(
                handle,
                "sensor.kitchen_window_handle",
                state=lambda entity: entity.native_value,
                real_write=False,
            )
        entity._message_received_callback(_handle(0xC0))
        self.assertEqual(entity.native_value, "open")

        entity._silence_expired()
        self.written.clear()

        entity._message_received_callback(_handle(0xF0))
        self.assertEqual(entity.native_value, "closed")
        self.assertNotIn((True, "open"), self.written)
        self.assertIn((True, "closed"), self.written)

    async def test_a_telegram_the_entity_cannot_decode_still_ends_the_silence(self):
        entity = await self._door()
        entity._message_received_callback(_contact(CLOSED))
        entity._silence_expired()
        self.written.clear()

        # A 1BS teach-in telegram (LRN bit clear) carries no contact state.
        entity._message_received_callback(_contact(0x00))
        self.assertTrue(entity.available)
        self.assertEqual(self.written, [(True, False)])
        self.assertIsNotNone(entity._silence_unsub, "timer re-armed")

    async def test_a_malformed_telegram_still_ends_the_silence(self):
        entity = await self._door()
        entity._silence_expired()
        self.written.clear()

        def broken(packet):
            raise ValueError("malformed")

        entity.value_changed = broken
        with self.assertLogs("custom_components.enocean_custom", "WARNING"):
            entity._message_received_callback(_contact(CLOSED))
        self.assertTrue(entity.available)
        self.assertEqual(len(self.written), 1)

    async def test_a_sender_that_keeps_talking_is_written_as_before(self):
        entity = await self._door()
        entity._message_received_callback(_contact(OPEN))
        entity._message_received_callback(_contact(CLOSED))
        self.assertEqual(self.written, [(True, True), (True, False)])


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DongleOutageTests(_EntityCase):
    """A dongle outage is not a sender's silence."""

    async def test_no_silence_is_declared_while_the_dongle_is_down(self):
        entity = await self._door()
        entity._message_received_callback(_contact(CLOSED))
        self.assertIsNotNone(entity._silence_unsub)

        entity._dongle_status_changed(False)
        self.assertFalse(entity.available)
        self.assertIsNone(
            entity._silence_unsub, "the silence timer keeps running while deaf"
        )

        # Even a timer that was already due proves nothing during the outage.
        entity._silence_expired()
        self.assertFalse(entity.silent)
        self.assertIsNone(self._issue(), "a Repair was raised for a dongle outage")

    async def test_reconnection_restarts_the_grace_period(self):
        entity = await self._door()
        entity._message_received_callback(_contact(CLOSED))
        entity._dongle_status_changed(False)

        entity._dongle_status_changed(True)
        self.assertTrue(entity.available)
        self.assertFalse(entity.silent)
        self.assertIsNotNone(entity._silence_unsub, "no grace timer after reconnect")
        self.assertIs(entity.is_on, False, "other entity types keep their state")

        # A sender that then stays quiet for a whole timeout is still flagged.
        entity._silence_expired()
        self.assertTrue(entity.silent)
        self.assertIsNotNone(self._issue())

    async def test_a_sender_already_silent_stays_silent_across_an_outage(self):
        entity = await self._door()
        entity._silence_expired()
        self.assertIsNotNone(self._issue())

        entity._dongle_status_changed(False)
        entity._dongle_status_changed(True)
        self.assertTrue(entity.silent)
        self.assertFalse(entity.available)
        self.assertIsNotNone(self._issue(), "only a telegram clears the repair")

        entity._message_received_callback(_contact(CLOSED))
        self.assertTrue(entity.available)
        self.assertIsNone(self._issue())

    async def test_entities_without_silence_detection_are_untouched(self):
        entity = binary_sensor.EnOceanBinarySensor(SENDER, "Pantry", "door")
        entity.hass = self.hass
        entity.entity_id = "binary_sensor.pantry"
        await entity.async_added_to_hass()
        entity.async_write_ha_state = lambda: None
        entity._dongle_status_changed(False)
        entity._dongle_status_changed(True)
        self.assertIsNone(entity._silence_unsub)
        self.assertTrue(entity.available)


if __name__ == "__main__":
    unittest.main()

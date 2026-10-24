"""Minor version 3: every radio device moves into its own config subentry.

The migration is replayed on a real Home Assistant device and entity registry
rebuilt from the production snapshot (``live_snapshot.py``): 43 rows, 40
senders, 41 devices, 231 entities. It must create one subentry per sender,
re-attach each sender's device and entities to it, and change nothing else:
not one ``unique_id``, ``entity_id`` or ``device_id``, no custom name, area or
disabled state, no deletion, and ``options["ui_devices"]`` byte for byte --
that is what a 2.10.x release reads after a downgrade. The test runs on both
Home Assistant releases the CI covers, whose device registries move a device
to a subentry through different APIs.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from tests.live_snapshot import expected_identities, load_snapshot

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from custom_components.enocean_custom import async_migrate_entry
    from custom_components.enocean_custom.const import DOMAIN, SUBENTRY_TYPE_DEVICE
    from custom_components.enocean_custom.device_rows import (
        configured_rows,
        device_subentries,
        device_subentry_id,
        legacy_rows,
        mirror_rows,
        rows_by_subentry,
        sender_identifier,
        subentry_rows,
    )
    from custom_components.enocean_custom.identity import gateway_device_identifier
    from custom_components.enocean_custom.schema import (
        CONF_UI_DEVICES,
        valid_ui_devices,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class SubentryMigrationTests(unittest.IsolatedAsyncioTestCase):
    """Replay the 2.10.2 -> 2.11.0 migration on the production registries."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.snapshot = load_snapshot()
        self.entry = self._build_entry()
        self.device_ids = await self._rebuild_devices()
        await self._rebuild_entities()

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _build_entry(self) -> ConfigEntry:
        snapshot_entry = self.snapshot["entry"]
        entry = ConfigEntry(
            data={"device": "/dev/serial/by-id/fixture"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            entry_id=self.snapshot["entry_id"],
            minor_version=snapshot_entry["minor_version"],
            options=snapshot_entry["options"],
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=snapshot_entry["version"],
        )
        self.hass.config_entries._entries[entry.entry_id] = entry
        return entry

    async def _rebuild_devices(self) -> dict[str, str]:
        """Recreate every device; return snapshot device id -> registry id."""
        registry = dr.async_get(self.hass)
        mapping: dict[str, str] = {}
        devices = sorted(
            self.snapshot["devices"], key=lambda d: d["via_device_id"] is not None
        )
        for device in devices:
            created = registry.async_get_or_create(
                config_entry_id=self.entry.entry_id,
                identifiers={tuple(pair) for pair in device["identifiers"]},
                name=device["name"],
                model=device.get("model"),
                sw_version=device.get("sw_version"),
            )
            updates = {}
            if device.get("name_by_user"):
                updates["name_by_user"] = device["name_by_user"]
            if device.get("area_id"):
                updates["area_id"] = device["area_id"]
            if device.get("via_device_id"):
                updates["via_device_id"] = mapping[device["via_device_id"]]
            if updates:
                created = registry.async_update_device(created.id, **updates)
            mapping[device["id"]] = created.id
        return mapping

    async def _rebuild_entities(self) -> None:
        registry = er.async_get(self.hass)
        for entity in self.snapshot["entities"]:
            domain, object_id = entity["entity_id"].split(".", 1)
            created = registry.async_get_or_create(
                domain,
                DOMAIN,
                entity["unique_id"],
                config_entry=self.entry,
                device_id=self.device_ids[entity["device_id"]],
                suggested_object_id=object_id,
                disabled_by=(
                    er.RegistryEntryDisabler.INTEGRATION
                    if entity["disabled_by"] == "integration"
                    else None
                ),
            )
            self.assertEqual(created.entity_id, entity["entity_id"])

    def _entity_facts(self) -> dict[str, tuple]:
        registry = er.async_get(self.hass)
        return {
            entity.entity_id: (
                entity.unique_id,
                entity.device_id,
                entity.disabled_by,
                entity.domain,
            )
            for entity in registry.entities.values()
        }

    def _device_facts(self) -> dict[str, tuple]:
        registry = dr.async_get(self.hass)
        return {
            device.id: (
                frozenset(device.identifiers),
                device.name,
                device.name_by_user,
                device.area_id,
                device.via_device_id,
            )
            for device in dr.async_entries_for_config_entry(
                registry, self.entry.entry_id
            )
        }

    def _subentry_of_device(self, device) -> str | None:
        return device_subentry_id(device, self.entry.entry_id)

    async def test_registries_are_rebuilt_to_the_production_shape(self):
        self.assertEqual(len(self._entity_facts()), 231)
        self.assertEqual(len(self._device_facts()), 41)
        self.assertEqual(
            sum(1 for facts in self._entity_facts().values() if facts[2]), 161
        )
        registry = er.async_get(self.hass)
        self.assertEqual(
            {(e.domain, e.unique_id) for e in registry.entities.values()},
            expected_identities(
                valid_ui_devices(self.entry.options[CONF_UI_DEVICES]),
                self.entry.entry_id,
            ),
        )

    async def test_migration_moves_everything_and_changes_nothing(self):
        original_rows = list(self.entry.options[CONF_UI_DEVICES])
        entities_before = self._entity_facts()
        devices_before = self._device_facts()
        device_registry = dr.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(
            device_registry, self.entry.entry_id
        ):
            self.assertIsNone(self._subentry_of_device(device))
        self.assertEqual(self.entry.minor_version, 2)

        self.assertTrue(await async_migrate_entry(self.hass, self.entry))

        # -- the entry: minor 3, 40 device subentries, options untouched.
        self.assertEqual((self.entry.version, self.entry.minor_version), (1, 3))
        subentries = device_subentries(self.entry)
        self.assertEqual(len(subentries), 40)
        self.assertEqual(
            {subentry.subentry_type for subentry in subentries.values()},
            {SUBENTRY_TYPE_DEVICE},
        )
        rows = valid_ui_devices(original_rows)
        senders = {sender_identifier(row["id"]) for row in rows}
        self.assertEqual(
            {subentry.unique_id for subentry in subentries.values()}, senders
        )
        by_sender = {subentry.unique_id: subentry for subentry in subentries.values()}
        for sender in senders:
            expected = [row for row in rows if sender_identifier(row["id"]) == sender]
            self.assertEqual(subentry_rows(by_sender[sender]), expected)
            self.assertEqual(by_sender[sender].title, expected[0]["name"])
        self.assertEqual(sum(len(subentry_rows(s)) for s in subentries.values()), 43)
        # options["ui_devices"] is exactly what 2.10.2 wrote: same objects,
        # same order, so a downgrade reads every device back.
        self.assertEqual(list(self.entry.options[CONF_UI_DEVICES]), original_rows)
        for before, after in zip(
            original_rows, self.entry.options[CONF_UI_DEVICES], strict=True
        ):
            self.assertIs(before, after)
        self.assertEqual(legacy_rows(self.entry), [])
        self.assertEqual(len(configured_rows(self.entry)), 43)
        self.assertEqual(
            [sid for sid, _ in rows_by_subentry(self.entry)].count(None), 0
        )
        # The mirror is already consistent: the listener would not rewrite it.
        self.assertEqual(mirror_rows(self.entry), original_rows)

        # -- the entity registry: 231 entities, identities unchanged, every
        # radio entity attached to its device's subentry, gateway ones not.
        entities_after = self._entity_facts()
        self.assertEqual(entities_after, entities_before)
        self.assertEqual(len(entities_after), 231)
        gateway_identifier = (DOMAIN, gateway_device_identifier(self.entry.entry_id))
        gateway_device = next(
            d
            for d in dr.async_entries_for_config_entry(
                device_registry, self.entry.entry_id
            )
            if gateway_identifier in d.identifiers
        )
        moved = 0
        for entity in entity_registry.entities.values():
            device = device_registry.async_get(entity.device_id)
            if entity.device_id == gateway_device.id:
                self.assertIsNone(entity.config_subentry_id, entity.entity_id)
                continue
            sender = next(
                identifier
                for domain, identifier in device.identifiers
                if domain == DOMAIN
            )
            self.assertEqual(
                entity.config_subentry_id,
                by_sender[sender].subentry_id,
                entity.entity_id,
            )
            moved += 1
        self.assertEqual(moved, 231 - 5)

        # -- the device registry: 41 devices, same ids and facts, each radio
        # device in its subentry, the gateway on the main entry.
        devices_after = self._device_facts()
        self.assertEqual(devices_after, devices_before)
        self.assertEqual(len(devices_after), 41)
        for device in dr.async_entries_for_config_entry(
            device_registry, self.entry.entry_id
        ):
            if device.id == gateway_device.id:
                self.assertIsNone(self._subentry_of_device(device))
                continue
            sender = next(
                identifier
                for domain, identifier in device.identifiers
                if domain == DOMAIN
            )
            self.assertEqual(
                self._subentry_of_device(device), by_sender[sender].subentry_id
            )
        self.assertEqual(
            sum(1 for d in devices_after.values() if d[2]),
            3,
            "custom names survive",
        )
        self.assertEqual(
            sum(1 for d in devices_after.values() if d[3]), 3, "areas survive"
        )

        # -- the identities computed from the subentry rows are the 231 of
        # the registry, so the platforms create exactly the same entities.
        self.assertEqual(
            expected_identities(configured_rows(self.entry), self.entry.entry_id),
            {(e.domain, e.unique_id) for e in entity_registry.entities.values()},
        )

        # -- idempotent: a second run (downgrade/upgrade cycle) changes nothing.
        self.assertTrue(await async_migrate_entry(self.hass, self.entry))
        self.assertEqual(len(device_subentries(self.entry)), 40)
        self.assertEqual(self._entity_facts(), entities_before)
        self.assertEqual(self._device_facts(), devices_before)
        self.assertEqual(list(self.entry.options[CONF_UI_DEVICES]), original_rows)


if __name__ == "__main__":
    unittest.main()

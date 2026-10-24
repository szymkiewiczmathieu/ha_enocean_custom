"""Set up the EnOcean Custom integration."""

from __future__ import annotations

import re
from typing import Any

import serial
import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity_platform import async_get_platforms
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONFIG_ENTRY_MINOR_VERSION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    FORWARDED_PLATFORMS,
    LOGGER,
    MANUFACTURER,
    SIGNAL_GATEWAY_UPDATE,
    SIGNAL_SUBENTRY_SETUP,
)
from .device_rows import (
    SUBENTRY_ROWS,
    async_adopt_rows,
    async_sync_mirror,
    configured_rows,
    device_subentries,
    device_subentry_id,
    sender_from_identifier,
    sender_identifier,
    subentries_by_sender,
    subentry_rows,
)
from .dongle import EnOceanDongle
from .enocean_library.utils import combine_hex
from .identity import gateway_device_identifier
from .inbox import DeviceInbox
from .learn import async_register_learn_service, get_known_ids
from .migration import async_adopt_legacy_rows, explicit_rps_actuator_profiles
from .schema import CONF_UI_DEVICES, valid_ui_devices
from .yaml_import import purge_yaml_inventory

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Required(CONF_DEVICE): cv.string})},
    extra=vol.ALLOW_EXTRA,
)

# Radio devices are identified by their four sender bytes as eight hex digits.
_SENDER_IDENTIFIER = re.compile(r"\A[0-9A-Fa-f]{8}\Z")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration-wide action and import a legacy YAML gateway.

    ``enocean_custom.learn`` does not depend on a loaded entry, so it is
    registered here rather than in ``async_setup_entry`` (Quality Scale
    ``action-setup``): a call made while the entry is unloaded fails with a
    clear error instead of "service not found".
    """
    async_register_learn_service(hass)
    if DOMAIN not in config or hass.config_entries.async_entries(DOMAIN):
        return True
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data=config[DOMAIN],
        )
    )
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Rewrite options stored by an older release; entry data never changes.

    Minor version 2 makes the former runtime inference of the Ubiwizz
    UBID1507C profile explicit in the affected ``RPS`` switch rows. Minor
    version 3 gives every radio device its own config subentry and re-attaches
    the device's registry device and entities to it, without touching
    ``options["ui_devices"]`` (see ``migration.py``). A newer minor version
    (downgrade scenario) is left alone; a newer major version is refused.
    """
    if entry.version > CONFIG_ENTRY_VERSION:
        return False
    if entry.minor_version < 2:
        rows, changed = explicit_rps_actuator_profiles(
            entry.options.get(CONF_UI_DEVICES, [])
        )
        options = dict(entry.options)
        if changed:
            options[CONF_UI_DEVICES] = rows
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
        LOGGER.info(
            "Migrated EnOcean options to minor version 2: %d RPS switch rows "
            "now carry the explicit ubiwizz_ubid1507c profile",
            changed,
        )
    if entry.minor_version < 3:
        created = async_adopt_legacy_rows(hass, entry)
        hass.config_entries.async_update_entry(
            entry, minor_version=CONFIG_ENTRY_MINOR_VERSION
        )
        LOGGER.info(
            "Migrated EnOcean entry to minor version %d: %d radio device(s) now "
            "live in their own config subentry; ui_devices is kept as a "
            "downgrade-safe mirror",
            CONFIG_ENTRY_MINOR_VERSION,
            len(created),
        )
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[EnOceanDongle],
) -> bool:
    """Start the serial owner and forward all UI-capable platforms.

    The dongle and its passive inbox live on ``entry.runtime_data`` only;
    platforms and flows resolve them through ``runtime.get_gateway``.
    """
    try:
        gateway = await hass.async_add_executor_job(
            EnOceanDongle,
            hass,
            entry.data[CONF_DEVICE],
            entry.entry_id,
        )
    except serial.SerialException:
        raise ConfigEntryNotReady(
            "Unable to open the configured EnOcean dongle"
        ) from None

    def _configured_ids() -> set[int]:
        configured = set(get_known_ids(hass))
        for row in configured_rows(entry):
            configured.add(combine_hex(row["id"]))
        return configured

    inbox = DeviceInbox(_configured_ids)
    gateway.set_inbox(inbox)
    entry.runtime_data = gateway
    # The gateway device must exist before the radio devices reference it
    # through ``via_device``; its firmware facts arrive asynchronously.
    async_register_gateway_device(hass, entry, gateway)

    @callback
    def _async_gateway_updated() -> None:
        # Must be a @callback: Home Assistant runs a plain function (or a
        # lambda) dispatcher target in the executor, and the device registry
        # refuses updates from a thread.
        async_register_gateway_device(hass, entry, gateway)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_GATEWAY_UPDATE, _async_gateway_updated)
    )
    # Rows a 2.10.x release wrote during a downgrade, or hand-edited into
    # ``.storage``, get their subentry before any platform reads the entry.
    # Idempotent: right after the minor-3 migration there is nothing to do.
    async_adopt_legacy_rows(hass, entry)
    async_remove_stale_devices(hass, entry)
    await gateway.async_setup()

    try:
        await hass.config_entries.async_forward_entry_setups(
            entry,
            FORWARDED_PLATFORMS,
        )
    except Exception:
        gateway.set_inbox(None)
        await gateway.async_unload()
        inbox.clear()
        entry.runtime_data = None
        raise

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated(entry)))
    return True


@callback
def async_register_gateway_device(
    hass: HomeAssistant, entry: ConfigEntry, gateway: EnOceanDongle
) -> dr.DeviceEntry:
    """Create or refresh the dongle's device registry entry.

    Called once at setup with whatever is known, then again on every
    SIGNAL_GATEWAY_UPDATE so the firmware version, chip version and
    application name reported by CO_RD_VERSION appear as soon as the dongle
    answers. The model never pretends to know the hardware: it is the
    application description the dongle itself reports, or a generic label.
    """
    info = gateway.gateway_info
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, gateway_device_identifier(entry.entry_id))},
        manufacturer=MANUFACTURER,
        translation_key="gateway",
        model=info.app_description if info and info.app_description else "ESP3 gateway",
        sw_version=info.app_version if info else None,
        hw_version=info.chip_version if info else None,
        serial_number=info.chip_id if info else None,
    )


def _configured_sender_identifiers(entry: ConfigEntry) -> set[str]:
    """Return the registry identifiers of every sender a valid row names."""
    return {sender_identifier(row["id"]) for row in configured_rows(entry)}


@callback
def async_remove_stale_devices(hass: HomeAssistant, entry: ConfigEntry) -> int:
    """Drop registry devices of this entry that no row names any more.

    Deleting a device used to leave the device entry that grouped its
    entities behind as an empty shell. Devices are keyed by sender, so a
    device is stale only when none of the entry's rows (legacy or subentry)
    uses that sender. The gateway device is never stale. Returns how many
    devices were removed.
    """
    registry = dr.async_get(hass)
    keep = _configured_sender_identifiers(entry)
    keep.add(gateway_device_identifier(entry.entry_id))
    removed = 0
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        ours = {
            identifier for domain, identifier in device.identifiers if domain == DOMAIN
        }
        if not ours or ours & keep:
            continue
        # A device belongs to exactly one config entry (enforced since Home
        # Assistant 2026.10), so detaching it means removing it.
        registry.async_remove_device(device.id)
        removed += 1
    if removed:
        LOGGER.info("Removed %d stale EnOcean device(s) from the registry", removed)
    return removed


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting a radio device from its device page.

    A device that lives in its own subentry is removed by removing that
    subentry: Home Assistant then deletes its entities and the device itself,
    and the update listener drops its rows from the ``ui_devices`` mirror. A
    legacy device (rows not adopted yet) has its rows removed from the
    options. The gateway device and senders that are still configured in YAML
    cannot be removed from the UI.
    """
    ours = {
        identifier
        for domain, identifier in device_entry.identifiers
        if domain == DOMAIN
    }
    if not ours or gateway_device_identifier(entry.entry_id) in ours:
        return False
    senders = {
        int(identifier, 16)
        for identifier in ours
        if _SENDER_IDENTIFIER.match(identifier)
    }
    if not senders or senders & get_known_ids(hass):
        return False
    subentry_id = device_subentry_id(device_entry, entry.entry_id)
    if subentry_id is not None and subentry_id in device_subentries(entry):
        # Home Assistant's own cleanup of the subentry's entities and device
        # follows; both the 2026.7 and 2026.10 device pages tolerate the device
        # being gone when they come back to detach it.
        hass.config_entries.async_remove_subentry(entry, subentry_id)
        return True
    raw_rows = list(entry.options.get(CONF_UI_DEVICES, []))
    remaining = [
        raw
        for raw in raw_rows
        if not (valid := valid_ui_devices([raw]))
        or combine_hex(valid[0]["id"]) not in senders
    ]
    if len(remaining) != len(raw_rows):
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_UI_DEVICES: remaining}
        )
    return True


def _settings_snapshot(options: Any) -> dict[str, Any]:
    """Return the options that still require a reload when they change."""
    return {
        key: value for key, value in dict(options).items() if key != CONF_UI_DEVICES
    }


def _subentry_snapshot(
    entry: ConfigEntry,
) -> dict[str, tuple[str, dict[str, Any], str | None]]:
    """Return a comparable copy of the device subentries (title, data, sender)."""
    return {
        subentry_id: (subentry.title, dict(subentry.data), subentry.unique_id)
        for subentry_id, subentry in device_subentries(entry).items()
    }


def _async_entry_updated(entry: ConfigEntry):
    """Return the update listener that keeps rows, subentries and entities in step.

    Three kinds of change reach the listener:

    * a setting other than the device rows changed (availability, D2-01
      start-up query): the entry is reloaded, exactly as before;
    * the subentries changed (the *Add device* subentry flow, a reconfigure,
      a rename or a delete from the integration page): the ``ui_devices``
      mirror is rewritten and the entities of an added or rewritten subentry
      are (re)created in place;
    * the ``ui_devices`` rows changed (every guided options flow still writes
      that list): the subentries are made to match -- new senders get a
      subentry, changed senders are rewritten, senders left without a row are
      removed -- and the entities follow, again without a reload.

    Home Assistant starts update listeners eagerly, so the listener's own
    writes re-enter it *synchronously*, in the middle of a reconciliation.
    A re-entrant call only leaves a note; the outer call reconciles again
    from the fresh snapshot once it is done, until nothing changes.
    """
    state: dict[str, Any] = {
        "rows": list(entry.options.get(CONF_UI_DEVICES, [])),
        "subentries": _subentry_snapshot(entry),
        "settings": _settings_snapshot(entry.options),
        "busy": False,
        "pending": False,
    }

    @callback
    def _reconcile_once(
        hass: HomeAssistant, updated: ConfigEntry
    ) -> _ReconcilePlan | None:
        """Diff against the snapshot, apply, refresh the snapshot; None = reload."""
        previous_rows = state["rows"]
        previous_subentries = state["subentries"]
        previous_settings = state["settings"]
        state["rows"] = list(updated.options.get(CONF_UI_DEVICES, []))
        state["subentries"] = _subentry_snapshot(updated)
        state["settings"] = _settings_snapshot(updated.options)
        if state["settings"] != previous_settings:
            return None
        if state["subentries"] != previous_subentries:
            plan = _async_reconcile_from_subentries(hass, updated, previous_subentries)
        elif state["rows"] != previous_rows:
            plan = _async_reconcile_from_rows(hass, updated)
        else:
            plan = _ReconcilePlan()
        state["rows"] = list(updated.options.get(CONF_UI_DEVICES, []))
        state["subentries"] = _subentry_snapshot(updated)
        return plan

    async def _listener(hass: HomeAssistant, updated: ConfigEntry) -> None:
        if state["busy"]:
            state["pending"] = True
            return
        state["busy"] = True
        try:
            while True:
                state["pending"] = False
                plan = _reconcile_once(hass, updated)
                if plan is None:
                    await hass.config_entries.async_reload(updated.entry_id)
                    return
                for subentry_id in plan.teardown:
                    await _async_remove_live_entities(hass, updated, subentry_id)
                for subentry_id in plan.setup:
                    if subentry_id in device_subentries(updated):
                        async_dispatcher_send(
                            hass, SIGNAL_SUBENTRY_SETUP, updated.entry_id, subentry_id
                        )
                if not state["pending"]:
                    return
        finally:
            state["busy"] = False

    return _listener


class _ReconcilePlan:
    """Subentries whose live entities must be torn down and/or (re)created."""

    __slots__ = ("setup", "teardown")

    def __init__(self) -> None:
        self.teardown: list[str] = []
        self.setup: list[str] = []

    def rewrite(self, subentry_id: str) -> None:
        if subentry_id not in self.teardown:
            self.teardown.append(subentry_id)
        if subentry_id not in self.setup:
            self.setup.append(subentry_id)


@callback
def _async_reconcile_from_subentries(
    hass: HomeAssistant,
    entry: ConfigEntry,
    previous: dict[str, tuple[str, dict[str, Any], str | None]],
) -> _ReconcilePlan:
    """Apply a change made to the subentries themselves (flow, rename, delete)."""
    plan = _ReconcilePlan()
    current = device_subentries(entry)
    for subentry_id, subentry in current.items():
        before = previous.get(subentry_id)
        if before is None:
            plan.setup.append(subentry_id)
            continue
        title_before, data_before, _sender = before
        if dict(subentry.data) != data_before:
            plan.rewrite(subentry_id)
            continue
        if subentry.title != title_before:
            # A rename from the integration page names the device: the first
            # row carries the device name, so it follows the title. The data
            # change re-enters the listener and is absorbed by the snapshot.
            rows = subentry_rows(subentry)
            if rows and rows[0]["name"] != subentry.title:
                rows[0] = {**rows[0], "name": subentry.title}
                hass.config_entries.async_update_subentry(
                    entry, subentry, data={**subentry.data, SUBENTRY_ROWS: rows}
                )
                _async_rename_sender_device(hass, entry, subentry)
                plan.rewrite(subentry_id)
    # Removed subentries: Home Assistant already deleted their registry
    # entities (which removed the live entities) and their device; their rows
    # leave the mirror too, or they would come back as legacy rows to adopt.
    removed_senders = {
        sender_from_identifier(sender)
        for subentry_id, (_title, _data, sender) in previous.items()
        if subentry_id not in current
    }
    async_sync_mirror(hass, entry, {s for s in removed_senders if s is not None})
    return plan


@callback
def _async_reconcile_from_rows(
    hass: HomeAssistant, entry: ConfigEntry
) -> _ReconcilePlan:
    """Make the subentries match ``ui_devices`` after an options-flow write."""
    plan = _ReconcilePlan()
    desired = {
        sender: rows
        for sender, rows in _group_by_sender(
            valid_ui_devices(entry.options.get(CONF_UI_DEVICES, []))
        ).items()
    }
    owned = subentries_by_sender(entry)
    removed_senders: set[int] = set()
    for sender, subentry in list(owned.items()):
        rows = desired.get(sender)
        if not rows:
            # Every row of this sender was removed: the flow already deleted
            # the entities it created; Home Assistant now deletes the device.
            hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
            removed_senders.add(sender)
            continue
        if rows == subentry_rows(subentry):
            continue
        hass.config_entries.async_update_subentry(
            entry,
            subentry,
            data={**subentry.data, SUBENTRY_ROWS: rows},
            title=str(rows[0]["name"]),
        )
        plan.rewrite(subentry.subentry_id)
    for subentry_id in async_adopt_rows(
        hass,
        entry,
        [
            row
            for sender, rows in desired.items()
            if sender not in owned
            for row in rows
        ],
    ):
        plan.setup.append(subentry_id)
    async_sync_mirror(hass, entry, removed_senders)
    return plan


def _group_by_sender(rows: list[dict]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(combine_hex(row["id"]), []).append(row)
    return grouped


@callback
def _async_rename_sender_device(
    hass: HomeAssistant, entry: ConfigEntry, subentry
) -> None:
    """Rename the registry device of a subentry after its title changed."""
    rows = subentry_rows(subentry)
    if not rows:
        return
    registry = dr.async_get(hass)
    identifier = (DOMAIN, sender_identifier(rows[0]["id"]))
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if identifier in device.identifiers:
            registry.async_update_device(device.id, name=subentry.title)
            return


async def _async_remove_live_entities(
    hass: HomeAssistant, entry: ConfigEntry, subentry_id: str
) -> None:
    """Remove the running entities of one subentry; their registry rows stay.

    The subentry is about to be set up again from its rewritten rows: the
    recreated entities find their registry rows (same unique_id) and keep
    entity_id, custom name, area and disabled state.
    """
    registry = er.async_get(hass)
    entity_ids = {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        if entity.config_subentry_id == subentry_id
    }
    if not entity_ids:
        return
    for platform in async_get_platforms(hass, DOMAIN):
        if platform.config_entry is not entry:
            continue
        for entity_id in list(platform.entities):
            if entity_id in entity_ids:
                await platform.async_remove_entity(entity_id)


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[EnOceanDongle],
) -> bool:
    """Stop platforms and the serial thread before permitting a reload."""
    platforms_unloaded = await hass.config_entries.async_unload_platforms(
        entry,
        FORWARDED_PLATFORMS,
    )
    if not platforms_unloaded:
        return False

    # A teach-in window is intentionally not entry-scoped. It remains active
    # across an options-triggered reload and closes on its own bounded timeout.
    gateway = entry.runtime_data
    inbox = gateway.inbox
    gateway.set_inbox(None)
    if not await gateway.async_unload():
        gateway.set_inbox(inbox)
        return False

    if inbox is not None:
        inbox.clear()
    purge_yaml_inventory(hass)
    return True

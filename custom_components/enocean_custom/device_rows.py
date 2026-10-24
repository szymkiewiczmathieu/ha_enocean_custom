"""Where the configured radio devices live: subentries and their options mirror.

Since config entry minor version 3 (v2.11.0) every EnOcean radio device is one
Home Assistant config subentry of type ``device``:

* ``unique_id`` is the sender, eight uppercase hex digits (``FED5842C``), so a
  sender can never own two subentries;
* ``title`` is the device name shown by Home Assistant (the name of the
  device's first row, exactly as the device registry already showed it);
* ``data["rows"]`` holds every UI row configured for that sender, in order,
  in the very same ``UI_DEVICE_SCHEMA`` shape the options flow has always
  written to ``options["ui_devices"]``.

``options["ui_devices"]`` does not disappear: it stays a complete mirror of
the rows every subentry holds. That is what keeps a downgrade to 2.10.x
non-destructive (that release reads nothing else) and what lets the guided
options flows keep reading and writing one flat list of rows. The update
listener in ``__init__`` keeps both sides equal; this module only computes.

Every helper tolerates the bare ``SimpleNamespace(options=...)`` entries the
test-suite feeds the platforms: an object without ``subentries`` simply has
no subentry, and all its valid rows are legacy rows.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Iterable, Mapping
from functools import lru_cache
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, LOGGER, SIGNAL_SUBENTRY_SETUP, SUBENTRY_TYPE_DEVICE
from .enocean_library.utils import combine_hex
from .schema import CONF_UI_DEVICES, valid_ui_devices

# The one key of a device subentry's data.
SUBENTRY_ROWS = "rows"


def sender_identifier(dev_id: Iterable[int]) -> str:
    """Return the registry identifier / subentry unique_id of a sender."""
    return "".join(f"{byte:02X}" for byte in dev_id)


def sender_from_identifier(identifier: Any) -> int | None:
    """Return the sender of an eight-hex-digit identifier, else None."""
    if not isinstance(identifier, str) or len(identifier) != 8:
        return None
    try:
        return int(identifier, 16)
    except ValueError:
        return None


def row_key(row: Mapping[str, Any]) -> str:
    """Return a canonical, hashable rendering of one validated row."""
    return json.dumps(row, sort_keys=True, default=str)


def device_subentries(entry: Any) -> dict[str, ConfigSubentry]:
    """Return the ``device`` subentries of an entry, keyed by subentry id."""
    subentries = getattr(entry, "subentries", None)
    if not isinstance(subentries, Mapping):
        return {}
    return {
        subentry_id: subentry
        for subentry_id, subentry in subentries.items()
        if getattr(subentry, "subentry_type", None) == SUBENTRY_TYPE_DEVICE
    }


def subentry_sender(subentry: Any) -> int | None:
    """Return the sender a device subentry stands for (from its unique_id)."""
    return sender_from_identifier(getattr(subentry, "unique_id", None))


def subentry_rows(subentry: Any) -> list[dict]:
    """Return the valid rows of one device subentry, all for its own sender.

    Subentry data is hand-editable in ``.storage`` like the options are: a
    malformed row, or one that names another sender, is skipped and never
    crashes a platform.
    """
    sender = subentry_sender(subentry)
    data = getattr(subentry, "data", None)
    rows = data.get(SUBENTRY_ROWS) if isinstance(data, Mapping) else None
    if sender is None or not isinstance(rows, list):
        return []
    return [row for row in valid_ui_devices(rows) if combine_hex(row["id"]) == sender]


def subentries_by_sender(entry: Any) -> dict[int, ConfigSubentry]:
    """Map every sender that owns a device subentry to that subentry."""
    result: dict[int, ConfigSubentry] = {}
    for subentry in device_subentries(entry).values():
        sender = subentry_sender(subentry)
        if sender is not None:
            result.setdefault(sender, subentry)
    return result


def legacy_rows(entry: Any) -> list[dict]:
    """Return the valid ``ui_devices`` rows whose sender owns no subentry.

    Before the minor-3 migration this is every row; afterwards it is normally
    empty, and transiently holds rows written by the options flow (or by a
    2.10.x release during a downgrade) until the listener adopts them.
    """
    owned = subentries_by_sender(entry)
    options = getattr(entry, "options", None) or {}
    return [
        row
        for row in valid_ui_devices(options.get(CONF_UI_DEVICES, []))
        if combine_hex(row["id"]) not in owned
    ]


def rows_by_subentry(entry: Any) -> list[tuple[str | None, list[dict]]]:
    """Return ``(subentry_id, rows)`` groups: legacy rows first, then subentries.

    Platforms create one batch of entities per group and attach it to the
    group's subentry (``None`` means the main entry, exactly as before).
    """
    groups: list[tuple[str | None, list[dict]]] = []
    legacy = legacy_rows(entry)
    if legacy:
        groups.append((None, legacy))
    for subentry_id, subentry in device_subentries(entry).items():
        rows = subentry_rows(subentry)
        if rows:
            groups.append((subentry_id, rows))
    return groups


def configured_rows(entry: Any) -> list[dict]:
    """Return every valid configured row, legacy and subentry alike."""
    return [row for _subentry_id, rows in rows_by_subentry(entry) for row in rows]


def rows_for_sender(entry: Any, sender: int) -> list[dict]:
    """Return the configured rows of one sender."""
    return [row for row in configured_rows(entry) if combine_hex(row["id"]) == sender]


def group_rows_by_sender(rows: Iterable[Mapping[str, Any]]) -> dict[int, list[dict]]:
    """Group validated rows by sender, keeping first-appearance order twice over."""
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(combine_hex(row["id"]), []).append(dict(row))
    return grouped


def build_device_subentry(rows: list[dict]) -> ConfigSubentry:
    """Return a new device subentry holding the rows of exactly one sender."""
    senders = {combine_hex(row["id"]) for row in rows}
    if len(senders) != 1:
        raise ValueError("a device subentry holds the rows of exactly one sender")
    return ConfigSubentry(
        data=MappingProxyType({SUBENTRY_ROWS: [dict(row) for row in rows]}),
        subentry_type=SUBENTRY_TYPE_DEVICE,
        title=str(rows[0]["name"]),
        unique_id=sender_identifier(rows[0]["id"]),
    )


def mirror_rows(entry: Any, drop_senders: Iterable[int] = ()) -> list[Any]:
    """Return what ``options["ui_devices"]`` must hold to mirror the subentries.

    Order and bytes of the rows already present are preserved whenever the
    owning subentry still holds an equal row; malformed rows and rows of a
    sender that owns no subentry (legacy, about to be adopted) are carried
    over untouched; rows the subentries hold but the options lack are
    appended. A row the owning subentry no longer holds is dropped, and so is
    every row of ``drop_senders``: the senders whose subentry was just
    removed, which must not come back as legacy rows to adopt.
    """
    owned: dict[int, list[dict]] = {
        sender: subentry_rows(subentry)
        for sender, subentry in subentries_by_sender(entry).items()
    }
    dropped = set(drop_senders)
    options = getattr(entry, "options", None) or {}
    current = options.get(CONF_UI_DEVICES, [])
    if not isinstance(current, list):
        current = []
    mirror: list[Any] = []
    seen: set[str] = set()
    for raw in current:
        valid = valid_ui_devices([raw])
        if not valid:
            mirror.append(raw)
            continue
        row = valid[0]
        sender = combine_hex(row["id"])
        if sender in dropped:
            continue
        if sender not in owned:
            mirror.append(raw)
            continue
        key = row_key(row)
        if row in owned[sender] and key not in seen:
            mirror.append(raw)
            seen.add(key)
    for rows in owned.values():
        for row in rows:
            key = row_key(row)
            if key not in seen:
                mirror.append(row)
                seen.add(key)
    return mirror


def device_subentry_id(device: Any, entry_id: str) -> str | None:
    """Return the subentry a registry device belongs to under this entry.

    Home Assistant 2026.10 gives a device exactly one ``config_subentry_id``;
    2026.7 keeps a ``config_entries_subentries`` mapping of sets.
    """
    if hasattr(device, "config_subentry_id"):
        if getattr(device, "config_entry_id", entry_id) != entry_id:
            return None
        return device.config_subentry_id
    memberships = getattr(device, "config_entries_subentries", None) or {}
    for subentry_id in memberships.get(entry_id, ()):
        if subentry_id is not None:
            return subentry_id
    return None


def device_belongs_to_entry(device: Any, entry_id: str) -> bool:
    """Return whether a registry device is owned by (or shared with) the entry."""
    if hasattr(device, "config_entry_id"):
        return device.config_entry_id == entry_id
    return entry_id in (getattr(device, "config_entries", None) or ())


@lru_cache(maxsize=1)
def _device_registry_moves_with_new_subentry_id() -> bool:
    """Return whether this Home Assistant moves devices with ``new_config_subentry_id``."""
    parameters = inspect.signature(dr.DeviceRegistry.async_update_device).parameters
    return "new_config_subentry_id" in parameters


@callback
def async_get_sender_device(
    registry: dr.DeviceRegistry, entry_id: str, identifier: tuple[str, str]
) -> dr.DeviceEntry | None:
    """Return the registry device carrying one identifier under this entry.

    Home Assistant 2026.10 deprecates the entry-agnostic ``async_get_device``
    in favour of ``async_get_device_by_identifier``; 2026.7 only has the
    former. Either way the result is checked against the owning entry.
    """
    by_identifier = getattr(registry, "async_get_device_by_identifier", None)
    if by_identifier is not None:
        return by_identifier(identifier, entry_id)
    device = registry.async_get_device(identifiers={identifier})
    if device is None or not device_belongs_to_entry(device, entry_id):
        return None
    return device


@callback
def async_move_device_to_subentry(
    hass: HomeAssistant, device_id: str, entry_id: str, subentry_id: str
) -> None:
    """Attach an existing registry device to a subentry, keeping its identity.

    The device entry (id, name, name_by_user, area, labels, via_device) is
    updated in place, never recreated. Home Assistant 2026.10 moves a device
    with ``new_config_subentry_id``; 2026.7 adds the subentry membership first
    and then drops the main-entry membership, in two calls because a single
    call computes the removal from the pre-update memberships.
    """
    registry = dr.async_get(hass)
    if _device_registry_moves_with_new_subentry_id():
        registry.async_update_device(device_id, new_config_subentry_id=subentry_id)
        return
    registry.async_update_device(
        device_id,
        add_config_entry_id=entry_id,
        add_config_subentry_id=subentry_id,
    )
    registry.async_update_device(
        device_id,
        remove_config_entry_id=entry_id,
        remove_config_subentry_id=None,
    )


@callback
def async_attach_sender_registry(
    hass: HomeAssistant, entry_id: str, subentry_id: str, sender: int
) -> tuple[int, int]:
    """Move the entities of a sender, then its registry device, to a subentry.

    Returns how many devices and entities were moved. Nothing is created,
    renamed or removed: ``unique_id``, ``entity_id``, ``device_id``, custom
    names, areas, labels and disabled states all stay exactly as they are.

    The order is load-bearing. When a device changes subentry, the entity
    registry removes every entity of that device still attached to the old
    subentry (``EntityRegistry.async_device_modified``, both 2026.7 and
    2026.10). Attaching the entities first leaves nothing behind to remove.
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    identifier = (DOMAIN, sender_identifier(sender.to_bytes(4, "big")))
    device = async_get_sender_device(device_registry, entry_id, identifier)
    if device is None:
        return 0, 0
    entities_moved = 0
    for entity in er.async_entries_for_device(
        entity_registry, device.id, include_disabled_entities=True
    ):
        if entity.config_entry_id != entry_id or entity.platform != DOMAIN:
            continue
        if entity.config_subentry_id == subentry_id:
            continue
        entity_registry.async_update_entity(
            entity.entity_id, config_subentry_id=subentry_id
        )
        entities_moved += 1
    devices_moved = 0
    if device_subentry_id(device, entry_id) != subentry_id:
        async_move_device_to_subentry(hass, device.id, entry_id, subentry_id)
        devices_moved = 1
    return devices_moved, entities_moved


@callback
def async_adopt_rows(
    hass: HomeAssistant,
    entry: Any,
    rows: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Give every row a subentry: new ones per unowned sender, appended otherwise.

    Rows whose sender already owns a subentry and that the subentry does not
    hold yet are appended to it. Returns the ids of the subentries created or
    rewritten, in order. Registry devices and entities of a newly adopted
    sender are moved to its subentry (see ``async_attach_sender_registry``).
    """
    touched: list[str] = []
    owned = subentries_by_sender(entry)
    for sender, sender_rows in group_rows_by_sender(rows).items():
        existing = owned.get(sender)
        if existing is None:
            subentry = build_device_subentry(sender_rows)
            hass.config_entries.async_add_subentry(entry, subentry)
            devices, entities = async_attach_sender_registry(
                hass, entry.entry_id, subentry.subentry_id, sender
            )
            LOGGER.debug(
                "Adopted %d row(s) into a new device subentry (%d device, %d "
                "entities re-attached)",
                len(sender_rows),
                devices,
                entities,
            )
            touched.append(subentry.subentry_id)
            continue
        current = subentry_rows(existing)
        missing = [row for row in sender_rows if row not in current]
        if not missing:
            continue
        hass.config_entries.async_update_subentry(
            entry,
            existing,
            data={**existing.data, SUBENTRY_ROWS: [*current, *missing]},
        )
        touched.append(existing.subentry_id)
    return touched


@callback
def async_sync_mirror(
    hass: HomeAssistant, entry: Any, drop_senders: Iterable[int] = ()
) -> bool:
    """Rewrite ``options["ui_devices"]`` from the subentries if it differs."""
    mirror = mirror_rows(entry, drop_senders)
    if list(entry.options.get(CONF_UI_DEVICES, [])) == mirror:
        return False
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_UI_DEVICES: mirror}
    )
    return True


def add_entities_for_group(
    async_add_entities: Callable[..., Any],
    entities: list[Any],
    subentry_id: str | None,
) -> None:
    """Hand a group's entities to the platform, attached to its subentry.

    The legacy group (``None``) is added exactly as every release did, so a
    bare ``list.extend`` callback from the test-suite keeps working; a
    subentry group is skipped when empty.
    """
    if subentry_id is None:
        async_add_entities(entities)
    elif entities:
        async_add_entities(entities, config_subentry_id=subentry_id)


@callback
def async_connect_subentry_setup(
    hass: HomeAssistant,
    entry: Any,
    async_add_entities: Callable[..., Any],
    build: Callable[[list[dict]], list[Any]],
) -> None:
    """Create a platform's entities for a device subentry set up at runtime.

    ``__init__`` fires ``SIGNAL_SUBENTRY_SETUP`` for a subentry added or
    rewritten while the entry is loaded; the platform then builds that one
    device's entities with ``build`` and adds them under the subentry, without
    the whole entry being reloaded. A bare test entry (no ``async_on_unload``)
    is never connected.
    """
    if not callable(getattr(entry, "async_on_unload", None)):
        return

    @callback
    def _async_subentry_setup(entry_id: str, subentry_id: str) -> None:
        if entry_id != entry.entry_id:
            return
        subentry = device_subentries(entry).get(subentry_id)
        if subentry is None:
            return
        entities = build(subentry_rows(subentry))
        if entities:
            async_add_entities(entities, config_subentry_id=subentry_id)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_SUBENTRY_SETUP, _async_subentry_setup)
    )

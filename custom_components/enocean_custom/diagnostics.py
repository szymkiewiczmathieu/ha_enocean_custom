"""Diagnostics support for the EnOcean config entry and its devices.

Both exports are written for remote support: they describe lifecycle,
configuration shape and radio health without ever including a sender ID, a
Product ID, a device name or the serial path. The per-device export adds the
rows configured for that sender, the inbox facts and the last raw telegram with
its sender bytes blanked.
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .availability import availability_settings, silence_category
from .const import DOMAIN
from .dongle import EnOceanDongle
from .enocean_library.utils import combine_hex
from .identity import gateway_device_identifier
from .inbox import get_device_inbox
from .schema import CONF_UI_DEVICES, valid_ui_devices
from .ubiwizz_repeater import ubiwizz_repeater_diagnostics

TO_REDACT = {
    CONF_DEVICE,
    "id",
    "sender_id",
    "product_id",
    "product_reference",
    "manufacturer_id",
    "radio_metadata",
    "ui_devices",
    "name",
}

_SENDER_IDENTIFIER = re.compile(r"\A[0-9A-Fa-f]{8}\Z")

# Row fields that describe configuration shape without identifying anything.
_ROW_SHAPE_FIELDS = (
    "platform",
    "device_class",
    "channel",
    "switch_type",
    "actuator_profile",
)
_METADATA_SHAPE_FIELDS = (
    "eep",
    "eep_source",
    "evidence",
    "support",
    "manufacturer_conflict",
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[EnOceanDongle]
) -> dict[str, Any]:
    """Return config and serial-worker health without exposing the full path."""
    inbox = get_device_inbox(hass)
    return {
        "config_entry": async_redact_data(dict(entry.data), TO_REDACT),
        "dongle": entry.runtime_data.diagnostics(),
        "inbox": {
            "observed_senders_count": inbox.observed_senders_count if inbox else 0
        },
        "availability": availability_settings(
            getattr(entry, "options", {})
        ).as_options(),
        "ubiwizz_repeater": ubiwizz_repeater_diagnostics(),
    }


def _row_shape(row: dict[str, Any]) -> dict[str, Any]:
    """Describe one UI row by its configuration shape only."""
    shape: dict[str, Any] = {field: row.get(field) for field in _ROW_SHAPE_FIELDS}
    metadata = row.get("radio_metadata") or {}
    # Named "profile", not "radio_metadata": the latter is a redacted key
    # because it carries identifiers in the options; only its shape is kept.
    shape["profile"] = {
        field: metadata.get(field)
        for field in _METADATA_SHAPE_FIELDS
        if field in metadata
    }
    shape["silence_category"] = silence_category(row)
    return shape


def _entity_shape(entity: er.RegistryEntry, sender_decimal: str) -> dict[str, Any]:
    """Describe a registry entity without its identity."""
    unique_id = entity.unique_id or ""
    suffix = (
        unique_id[len(sender_decimal) :]
        if unique_id.startswith(sender_decimal)
        else None
    )
    return {
        "domain": entity.domain,
        "unique_id_suffix": suffix,
        "translation_key": entity.translation_key,
        "original_device_class": entity.original_device_class,
        "disabled_by": entity.disabled_by.value if entity.disabled_by else None,
        "entity_category": (
            entity.entity_category.value if entity.entity_category else None
        ),
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[EnOceanDongle], device: dr.DeviceEntry
) -> dict[str, Any]:
    """Return anonymized facts about one device: the dongle or one radio sender."""
    # "kind", not "device": CONF_DEVICE is a redacted key (the serial path).
    ours = {identifier for domain, identifier in device.identifiers if domain == DOMAIN}
    if gateway_device_identifier(entry.entry_id) in ours:
        return {"kind": "gateway", "dongle": entry.runtime_data.diagnostics()}

    senders = [
        identifier for identifier in ours if _SENDER_IDENTIFIER.match(identifier)
    ]
    if not senders:
        return {"kind": "unknown", "identifiers": sorted(ours)}
    sender_bytes = [int(senders[0][index : index + 2], 16) for index in range(0, 8, 2)]
    sender_int = combine_hex(sender_bytes)

    rows = [
        _row_shape(row)
        for row in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, []))
        if combine_hex(row["id"]) == sender_int
    ]
    inbox = get_device_inbox(hass)
    observation = inbox.get(sender_int) if inbox is not None else None
    radio: dict[str, Any] | None = None
    if observation is not None:
        radio = {
            "last_seen": observation.last_seen.isoformat(),
            "packet_count": observation.packet_count,
            "last_rssi_dbm": observation.last_rssi_dbm,
            "last_repeater_count": observation.last_repeater_count,
            "rorgs": sorted(f"0x{rorg:02X}" for rorg in observation.rorgs),
            # Sender bytes are already blanked by the inbox.
            "last_telegram": (
                " ".join(f"{byte:02X}" for byte in observation.last_payload)
                if observation.last_payload is not None
                else None
            ),
        }
    sender_decimal = str(sender_int)
    entities = [
        _entity_shape(entity, sender_decimal)
        for entity in er.async_entries_for_device(
            er.async_get(hass), device.id, include_disabled_entities=True
        )
        if entity.platform == DOMAIN
    ]
    return async_redact_data(
        {
            "kind": "radio_sender",
            "rows": rows,
            "radio": radio,
            "entities": entities,
            "availability": availability_settings(entry.options).as_options(),
        },
        TO_REDACT,
    )

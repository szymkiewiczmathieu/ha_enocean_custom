"""Load the production-shaped registry snapshot the migration tests run on.

By default the anonymized copy committed under ``tests/fixtures`` is used:
43 ``ui_devices`` rows on 40 senders, 41 devices (the gateway included),
231 entities (161 disabled by the integration), three custom names and
three areas -- exactly the shape of the maintainer's 2.10.2 installation
(``scripts/anonymize_live_snapshot.py`` documents how it was derived). Set
``ENOCEAN_LIVE_SNAPSHOT_DIR`` to a directory holding the real, un-anonymized
exports to run the very same tests against production identities; those
files are never committed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).parent / "fixtures" / "live_2102_anonymized.json"


def load_snapshot() -> dict[str, Any]:
    """Return ``{"entry_id", "entry", "entities", "devices"}``."""
    live_dir = os.environ.get("ENOCEAN_LIVE_SNAPSHOT_DIR")
    if not live_dir:
        return json.loads(FIXTURE.read_text(encoding="utf-8"))
    source = Path(live_dir)
    entry = json.loads((source / "live_entry_2102.json").read_text(encoding="utf-8"))
    entities = json.loads(
        (source / "live_entities_full_2102.json").read_text(encoding="utf-8")
    )
    devices = json.loads(
        (source / "live_devices_2102.json").read_text(encoding="utf-8")
    )
    entry_id = next(
        identifier.split(":", 1)[1]
        for device in devices
        for _domain, identifier in device["identifiers"]
        if identifier.startswith("gateway:")
    )
    return {
        "entry_id": entry_id,
        "entry": entry,
        "entities": entities,
        "devices": devices,
    }


def expected_identities(rows: list[dict], entry_id: str) -> set[tuple[str, str]]:
    """Return every ``(domain, unique_id)`` the platforms mint for these rows.

    Row entities (``ui_device_entity_identities``), one radio-diagnostic set
    per sender, one rocker ``event`` per rocker sender (every binary_sensor
    row whose declared EEP is not a contact or smoke profile, the rule of
    ``event.py``) and the five gateway entities.
    """
    from custom_components.enocean_custom.enocean_library.utils import combine_hex
    from custom_components.enocean_custom.identity import (
        A514_EEP,
        D2_14_30_EEP,
        GATEWAY_KEY_BASE_ID,
        GATEWAY_KEY_CONNECTED,
        GATEWAY_KEY_LAST_TELEGRAM,
        GATEWAY_KEY_RECEIVED_TELEGRAMS,
        GATEWAY_KEY_REPEATER,
        SMOKE_EEP,
        gateway_unique_id,
        rocker_event_unique_id,
        sender_diagnostic_identities,
        ui_device_entity_identities,
    )

    identities: set[tuple[str, str]] = set()
    seen_senders: set[int] = set()
    rocker_senders: set[int] = set()
    for row in rows:
        identities.update(ui_device_entity_identities(row))
        sender = combine_hex(row["id"])
        if sender not in seen_senders:
            seen_senders.add(sender)
            identities.update(sender_diagnostic_identities(row["id"]))
        metadata = row.get("radio_metadata") or {}
        if (
            row["platform"] == "binary_sensor"
            and metadata.get("eep") not in (A514_EEP, SMOKE_EEP, D2_14_30_EEP)
            and sender not in rocker_senders
        ):
            rocker_senders.add(sender)
            identities.add(("event", rocker_event_unique_id(row["id"])))
    identities.update(
        {
            ("binary_sensor", gateway_unique_id(entry_id, GATEWAY_KEY_CONNECTED)),
            ("sensor", gateway_unique_id(entry_id, GATEWAY_KEY_RECEIVED_TELEGRAMS)),
            ("sensor", gateway_unique_id(entry_id, GATEWAY_KEY_LAST_TELEGRAM)),
            ("sensor", gateway_unique_id(entry_id, GATEWAY_KEY_BASE_ID)),
            ("select", gateway_unique_id(entry_id, GATEWAY_KEY_REPEATER)),
        }
    )
    return identities

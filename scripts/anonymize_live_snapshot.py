"""Turn a real registry export into the anonymized fixture the test-suite uses.

``tests/test_subentry_migration.py`` and ``tests/test_live_parity.py`` prove
the minor-3 (subentry) migration on the exact *shape* of the maintainer's
production installation: 43 ``ui_devices`` rows on 40 senders (three of them
with two rows), 41 registry devices including the gateway, 231 entities of
which 161 are disabled, three custom names and three areas. The real export
holds radio identifiers, room names and the config entry id; this script
rewrites all of them consistently so the fixture can live in a public
repository while every structural relation -- which row makes which entity,
which entity hangs off which device, which identity formula applies -- is
preserved byte for byte in form.

Usage::

    python scripts/anonymize_live_snapshot.py <dir-with-live-exports> \
        tests/fixtures/live_2102_anonymized.json

The directory must hold ``live_entry_2102.json`` (``version``,
``minor_version``, ``options``), ``live_entities_full_2102.json`` and
``live_devices_2102.json`` as produced by the maintainer's export notebook.
The real files themselves are never committed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SYNTHETIC_ENTRY_ID = "01TESTENTRYSUBENTRIES00000"
_SENDER_HEX = re.compile(r"\A[0-9A-Fa-f]{8}\Z")


def _combine(dev_id: list[int]) -> int:
    value = 0
    for byte in dev_id:
        value = (value << 8) | byte
    return value


def _synthetic_sender(index: int) -> list[int]:
    # 0x01xxxxxx is far from the dongle Base ID range (0xFF8xxxxx) and from
    # every real product id seen so far.
    return [0x01, 0x8A, (index >> 8) & 0xFF, index & 0xFF]


def anonymize(entry: dict, entities: list[dict], devices: list[dict]) -> dict:
    """Return the anonymized bundle."""
    rows = entry["options"]["ui_devices"]
    senders: dict[int, list[int]] = {}
    for row in rows:
        real = _combine(row["id"])
        if real not in senders:
            senders[real] = _synthetic_sender(len(senders) + 1)
    real_entry_id = next(
        identifier.split(":", 1)[1]
        for device in devices
        for _domain, identifier in device["identifiers"]
        if identifier.startswith("gateway:")
    )

    def sender_map(real_hex: str) -> str:
        synthetic = senders.get(int(real_hex, 16))
        if synthetic is None:
            raise ValueError(f"device identifier {real_hex} names no configured row")
        return "".join(f"{byte:02X}" for byte in synthetic)

    def unique_id_map(unique_id: str) -> str:
        if unique_id.startswith("gateway:"):
            return unique_id.replace(real_entry_id, SYNTHETIC_ENTRY_ID)
        decimal, _, suffix = unique_id.partition("-")
        synthetic = senders[int(decimal)]
        return f"{_combine(synthetic)}{'-' + suffix if _ else ''}"

    # Rows: synthetic sender, synthetic names (second rows of a sender get a
    # suffix so the "first row names the device" rule stays observable).
    seen_per_sender: dict[int, int] = {}
    new_rows = []
    for row in rows:
        real = _combine(row["id"])
        synthetic = senders[real]
        seen_per_sender[real] = seen_per_sender.get(real, 0) + 1
        ordinal = list(senders).index(real) + 1
        name = f"Device {ordinal:02d}"
        if seen_per_sender[real] > 1:
            name += f" {'ABCDEFG'[seen_per_sender[real] - 2]}"
        new_row = {**row, "id": synthetic, "name": name}
        metadata = row.get("radio_metadata")
        if isinstance(metadata, dict) and metadata.get("sender_id") is not None:
            # The schema requires the metadata sender to equal the row id.
            new_row["radio_metadata"] = {**metadata, "sender_id": synthetic}
        new_rows.append(new_row)

    device_names: dict[str, str] = {}
    new_devices = []
    area_names = {}
    for device in devices:
        identifiers = []
        name = "EnOcean gateway"
        for domain, identifier in device["identifiers"]:
            if identifier.startswith("gateway:"):
                identifiers.append([domain, f"gateway:{SYNTHETIC_ENTRY_ID}"])
            else:
                mapped = sender_map(identifier)
                identifiers.append([domain, mapped])
                ordinal = list(senders).index(int(identifier, 16)) + 1
                name = f"Device {ordinal:02d}"
        device_names[device["id"]] = name
        area_id = device.get("area_id")
        if area_id is not None:
            area_names.setdefault(area_id, f"area_{len(area_names) + 1}")
        new_devices.append(
            {
                "id": device["id"],
                "identifiers": identifiers,
                "name": name,
                "name_by_user": (
                    f"Custom name {len(device_names):02d}"
                    if device.get("name_by_user")
                    else None
                ),
                "area_id": area_names[area_id] if area_id is not None else None,
                "via_device_id": device.get("via_device_id"),
                "config_entries": [SYNTHETIC_ENTRY_ID],
                "config_entries_subentries": {SYNTHETIC_ENTRY_ID: [None]},
                "model": device.get("model"),
                "sw_version": device.get("sw_version"),
            }
        )

    counters: dict[str, int] = {}
    new_entities = []
    for entity in entities:
        domain = entity["domain"]
        counters[domain] = counters.get(domain, 0) + 1
        new_entities.append(
            {
                "entity_id": f"{domain}.fixture_{domain}_{counters[domain]:03d}",
                "unique_id": unique_id_map(entity["unique_id"]),
                "domain": domain,
                "disabled_by": entity["disabled_by"],
                "device_id": entity["device_id"],
                "config_subentry_id": entity.get("config_subentry_id"),
            }
        )

    return {
        "entry_id": SYNTHETIC_ENTRY_ID,
        "entry": {
            "version": entry["version"],
            "minor_version": entry["minor_version"],
            "options": {**entry["options"], "ui_devices": new_rows},
            "subentries": entry.get("subentries", 0),
        },
        "entities": new_entities,
        "devices": new_devices,
    }


def main(argv: list[str]) -> int:
    """Read the three exports from a directory and write the fixture."""
    if len(argv) != 3:
        print(__doc__)
        return 2
    source = Path(argv[1])
    target = Path(argv[2])
    entry = json.loads((source / "live_entry_2102.json").read_text(encoding="utf-8"))
    entities = json.loads(
        (source / "live_entities_full_2102.json").read_text(encoding="utf-8")
    )
    devices = json.loads(
        (source / "live_devices_2102.json").read_text(encoding="utf-8")
    )
    bundle = anonymize(entry, entities, devices)
    target.write_text(
        json.dumps(bundle, indent=1, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"{len(bundle['entry']['options']['ui_devices'])} rows, "
        f"{len(bundle['devices'])} devices, {len(bundle['entities'])} entities -> {target}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

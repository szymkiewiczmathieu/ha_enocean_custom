#!/usr/bin/env python3
"""Import the local EnOcean Alliance DDF index into a deterministic catalog."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

AUDIT_DATE = "2026-08-05"
SOURCE = "EnOcean-Alliance/enocean-alliance-ddf"


def _hex_eep(node: ET.Element) -> str | None:
    values = []
    for tag in ("Rorg", "Func", "Type"):
        text = node.findtext(tag)
        if text is None:
            return None
        values.append(int(text, 0))
    return "-".join(f"{value:02X}" for value in values)


def parse_repository(root: Path) -> dict[str, object]:
    """Parse only files named by index.xml; examples are not product evidence."""
    products: dict[str, dict[str, object]] = {}
    index = ET.parse(root / "index.xml").getroot()
    for indexed in index.findall("Product"):
        relative = indexed.attrib["Path"]
        if Path(relative).parts[0] == "Examples":
            continue
        document = ET.parse(root / relative).getroot()
        if document.attrib.get("schemaVersion") != "1.3":
            raise ValueError(f"unsupported DDF schema in {relative}")
        device = document.find("Device")
        if device is None:
            raise ValueError(f"missing Device in {relative}")
        product_id = device.attrib["Product_ID"].removeprefix("0x").upper()
        indexed_id = indexed.attrib["Product_ID"].removeprefix("0x").upper()
        if product_id != indexed_id:
            raise ValueError(f"Product_ID mismatch in {relative}")
        manufacturer = Path(relative).parts[0]
        name = (device.findtext("Name") or "").strip()
        description = (device.findtext("Description") or "").strip()
        model = name.removeprefix(f"{manufacturer} ")
        if manufacturer == "BSC Computer" and description:
            model = f"{model} {description.lower()}"
        tx = sorted(
            {eep for node in device.findall("./TX//EEP") if (eep := _hex_eep(node))}
        )
        rx = sorted(
            {eep for node in device.findall("./RX//EEP") if (eep := _hex_eep(node))}
        )
        products[product_id] = {
            "manufacturer": manufacturer,
            "model": model,
            "rx_eeps": rx,
            "source_path": relative,
            "tx_eeps": tx,
        }
    return {
        "provenance": {"audit_date": AUDIT_DATE, "schema": "1.3", "source": SOURCE},
        "products": dict(sorted(products.items())),
    }


def report_conflicts(catalog: dict[str, object], assertions: dict[str, str]) -> int:
    """Report development-time EEP assertions contradicted by exact DDF data."""
    conflicts = 0
    products = catalog["products"]
    assert isinstance(products, dict)
    for product_id, asserted_eep in sorted(assertions.items()):
        product = products.get(product_id)
        if not isinstance(product, dict):
            continue
        proven = set(product["tx_eeps"]) | set(product["rx_eeps"])
        if asserted_eep not in proven:
            conflicts += 1
            print(
                f"DDF conflict for {product_id}: catalog={asserted_eep}, ddf={sorted(proven)}; DDF wins",
                file=sys.stderr,
            )
    return conflicts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source", type=Path, nargs="?", default=Path("/tmp/enocean-alliance-ddf")
    )
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        default=Path("custom_components/enocean_custom/data/ddf_catalog.json"),
    )
    args = parser.parse_args()
    catalog = parse_repository(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

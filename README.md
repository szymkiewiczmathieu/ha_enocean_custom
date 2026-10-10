# EnOcean Custom

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![Validation](https://github.com/szymkiewiczmathieu/ha_enocean_custom/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/szymkiewiczmathieu/ha_enocean_custom/actions/workflows/validate.yml)

Local-first Home Assistant integration for ESP3 EnOcean USB dongles. It provides
configuration-entry and YAML setup, guided device learning, diagnostics, native
rocker triggers, and bounded actuator control without cloud services or
telemetry.

**Current release:** [v2.8.0](CHANGELOG.md) · Apache-2.0 · HACS custom repository

## Install

1. [Install HACS](https://hacs.xyz/docs/setup/download/).
2. In HACS, add
   `https://github.com/szymkiewiczmathieu/ha_enocean_custom` as an
   **Integration** custom repository.
3. Install **EnOcean Custom**, then restart Home Assistant.
4. Open **Settings → Devices & services**, add **EnOcean Custom**, and select a
   persistent serial path such as `/dev/serial/by-id/...`.

A dongle must have one reader. Do not configure `enocean` and `enocean_custom`
against the same serial device. If the port changes, use **Reconfigure** on the
existing entry rather than creating a second entry.

## Configure devices

Use **Configure** on the integration to learn a device, add a QR/ID, manage
UI-backed devices, or import eligible YAML `binary_sensor` and `switch` rows.
YAML remains supported and can coexist with UI-managed devices.

```yaml
switch:
  - platform: enocean_custom
    name: Living room rocker
    id: [0xFF, 0xD9, 0x04, 0x81]
    switch_type: RPS
    channel: 0
```

The integration supports binary sensors, switches, lights, sensors, and climate
entities. Platform-specific schemas, migration boundaries, and the local
identity model are in [Device Intelligence](docs/device-intelligence.md).

### Configuration and radio boundaries

- **Learn and QR are different operations.** A learn session captures an unknown
  sender; adding a QR/typed ID is configuration-only and does not commission a
  factory-fresh actuator.
- **An EEP is not a product identity.** A sender ID identifies a radio and an
  EEP identifies a data profile. Model claims require exact Product-ID evidence;
  manual EEP values remain operator assertions.
- **No automatic UTE acknowledgement.** Radio transmission happens only through
  an explicit supported action or service.
- **ESP3 `OK` is transport evidence, not device evidence.** Switch and light
  state changes require matching inbound feedback where the profile supports it.
- **Diagnostics are privacy-aware.** They expose lifecycle and aggregate radio
  evidence while redacting configured paths and device identifiers.

## Ubiwizz UBID1507C

The explicit `ubiwizz_ubid1507c` profile represents the documented two-output
`D2-01-12` module. It permits only channels `0` and `1`; selecting that profile
is an operator choice and is never inferred from a telegram, QR code, or EURID.

```yaml
switch:
  - platform: enocean_custom
    name: Ubiwizz output 1
    id: [0x01, 0x02, 0x03, 0x04]
    eep: D2-01-12
    actuator_profile: ubiwizz_ubid1507c
    channel: 0
```

Read [the Ubiwizz installation guide](docs/ubiwizz-installation.md) before
using local association, directed D2 control, or the repeater service. It
covers both outputs, HOPPE/Ubiwizz handles, D5-00-01 contacts, NodOn boundaries,
and the required safety checks.

### Repeater safety limit

The **Ubiwizz repeater diagnostic** in the options flow is deliberately
read-only: it sends no radio, observes no state, and persists nothing.

v2.8.0 also exposes an advanced
`enocean_custom.repeater_set_level` service for eligible UI-managed Ubiwizz
switches. It is a live radio configuration write, not a confirmed read-back.
Use it only after confirming the exact module and a safe test setup. An ESP3
acknowledgement only proves dongle acceptance; it does not prove the module
received, retained, or applied a repeater level. The service must not be used
for generic D2 devices, NodOn SmartPlugs, or to configure the USB gateway.

## Diagnostics and support

Use **Download diagnostics** when reporting an issue. Include the Home
Assistant and integration versions, dongle model, exact profile, reproduction
steps, and redacted logs. See [SUPPORT.md](SUPPORT.md) for the issue checklist
and support options.

## Release and licensing

- [CHANGELOG.md](CHANGELOG.md) is the release record.
- [NOTICE](NOTICE) lists third-party attributions; the vendored EnOcean library
  retains its MIT license.
- This is an independent custom integration, not the official Home Assistant
  EnOcean integration.

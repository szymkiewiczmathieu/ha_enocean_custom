# EnOcean Custom

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![Validation](https://github.com/szymkiewiczmathieu/ha_enocean_custom/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/szymkiewiczmathieu/ha_enocean_custom/actions/workflows/validate.yml)

Local-first Home Assistant integration for ESP3 EnOcean USB dongles (USB 300
and compatible). It provides configuration-entry and YAML setup, guided device
learning, diagnostics, native rocker triggers, automation blueprints and
bounded actuator control without cloud services or telemetry.

**Current release:** [v2.9.8](CHANGELOG.md) · Apache-2.0 · HACS custom repository

## Contents

- [Install](#install)
- [Configure devices](#configure-devices)
- [How data is updated](#how-data-is-updated)
- [Actions](#actions)
- [Triggers and events](#triggers-and-events)
- [Automation blueprints](#automation-blueprints)
- [Use cases](#use-cases)
- [Supported devices](#supported-devices)
- [Known limitations](#known-limitations)
- [Ubiwizz UBID1507C](#ubiwizz-ubid1507c)
- [Removing devices or the integration](#removing-devices-or-the-integration)
- [Troubleshooting](#troubleshooting)
- [Diagnostics and support](#diagnostics-and-support)
- [Release and licensing](#release-and-licensing)

## Install

1. [Install HACS](https://hacs.xyz/docs/setup/download/).
2. In HACS, add
   `https://github.com/szymkiewiczmathieu/ha_enocean_custom` as an
   **Integration** custom repository.
3. Install **EnOcean Custom**, then restart Home Assistant.
4. Open **Settings → Devices & services**, add **EnOcean Custom**, and select a
   persistent serial path such as `/dev/serial/by-id/...`. A USB 300 plugged in
   is also discovered automatically and offered for confirmation.

The only installation parameter is the serial device:

| Parameter | Meaning |
| --- | --- |
| `device` | Path of the ESP3 serial port, preferably the stable `/dev/serial/by-id/usb-EnOcean_GmbH_EnOcean_USB_300_...` alias. |

A dongle must have one reader. Do not configure `enocean` and `enocean_custom`
against the same serial device. If the port changes, use **Reconfigure** on the
existing entry rather than creating a second entry. Minimum Home Assistant
release: 2026.7.3 (declared in `hacs.json`); the test matrix also validates
2026.10.x.

## Configure devices

Use **Configure** on the integration to learn a device, add a QR/ID, pair an
actuator, declare the profile of an existing device, manage UI-backed devices,
or import eligible YAML `binary_sensor` and `switch` rows. YAML remains
supported and can coexist with UI-managed devices.

**Declare the profile of an existing device** records an EEP on a row whose
profile is unknown or was only asserted by hand. The existing entity keeps its
identity; decoders and companion entities follow the declared profile. The
typical case is a smoke detector added as a plain `smoke` binary sensor: declare
`F6-05-02` and it becomes a real detector (alarm from DB0 `0x10`/`0x00`) with a
diagnostic **Battery low** entity (`0x30`). A profile that would turn the
entity into a different class is refused; remove and re-add the device instead.

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
- **UTE association is request-driven and bounded.** For UBID1507C, enter the
  exact label EURID and the output in the guided flow. Home Assistant arms a
  60-second listener and sends nothing. Then run the documented local
  association sequence (three short `PRESS` presses for output 1, plus one more
  for output 2): the module transmits its UTE teach-in request (`D4`, CMD0).
  Only a matching request (exact EURID, bidirectional with response expected,
  `D2-01-08`/`D2-01-12`, manufacturer `0x046`, DB5 equal to the selected
  output, `0xFF`, or the documented `0x02`), parsed less than 500 ms earlier,
  is answered with exactly one directed standard
  `D4 91 [DB5 from request] 46 00 [12 or 08] 01 D2 [dongle Base ID] 00` telegram
  to that EURID. The switch row is saved only after the dongle confirms the
  physical write and ESP3 `OK`, and its profile comes from the module's own
  request. The flow never broadcasts UTE and never sends a D2 relay command.
  A separate **passive radio test** listens for one telegram from an EURID and
  sends nothing at all.
- **ESP3 `OK` is transport evidence, not device evidence.** Switch and light
  state changes require matching inbound feedback where the profile supports it.
- **Diagnostics are privacy-aware.** They expose lifecycle and aggregate radio
  evidence while redacting configured paths and device identifiers.

## How data is updated

The integration is `local_push`: nothing is polled. A single serial worker
thread owns the dongle, parses every ESP3 frame and dispatches radio telegrams
on Home Assistant's event loop. Each entity listens only to telegrams from its
own four-byte sender ID.

- **Battery-less transmitters** (rockers, handles, contacts, A5 sensors) update
  the instant their telegram is received; between telegrams the last value is
  kept.
- **Actuators** (switches, lights) change state only on their own feedback
  telegram (D2-01 status, A5-12 power report), never on the command alone.
  ESP3 `OK` is recorded in the `last_command_esp3_accepted` attribute.
- **Availability** mirrors the serial worker: when the dongle stops, every
  entity becomes unavailable and a Repair issue is raised; when the integration
  reloads, availability returns.
- **Silence detection is optional.** Under **Configure → Availability and
  silent devices** you can make sensors, contacts, smoke detectors and handles
  unavailable after a configurable number of minutes without a telegram
  (defaults: sensors 180 min, contacts 24 h, smoke detectors and handles 0 =
  never, because many of those models only transmit on events). Contacts,
  smoke detectors and handles additionally raise a Repair issue that clears
  itself on the next telegram. Rockers, relays and climate controllers never
  time out. The grace period restarts at every reload.
- **Devices can be deleted from their device page.** Deleting a radio device
  removes its UI rows and entities; the gateway device and YAML-configured
  senders refuse deletion. Devices whose rows were all removed are cleaned up
  at the next start.
- **Exactly one ESP3 command is in flight.** The worker waits for the dongle's
  response before writing the next packet; a missing response stops the
  transport instead of mis-attributing a later response.
- **Radio health is observable.** The dongle appears as its own device
  (firmware and chip version read with `CO_RD_VERSION`, received-telegram
  counter, last telegram, connectivity, Base ID). Every radio device carries
  three diagnostic sensors, disabled by default: signal strength (dBm, icon by
  range), last telegram and repeater hops. Enable them from the device page to
  build a coverage dashboard; they cost nothing while disabled.

## Actions

| Action | Target / fields | What it does | Radio transmission |
| --- | --- | --- | --- |
| `enocean_custom.learn` | `timeout` (15–300 s, default 60) | Opens one teach-in window; the first unknown sender fires `enocean_custom_device_learned` with its ID, hex form and safe radio evidence. Refused while another window is open or the dongle is not loaded. | none |
| `enocean_custom.send_teach_in` | one or more `light` entities | Sends the A5-38-08 4BS teach-in telegram with the light's configured sender ID. | yes, on request |
| `enocean_custom.climate_teach_in_actor` | one or more `climate` entities | Sends the configured profile's teach-in telegram (SRC-D08 panel or A5-20-04 variation 3). | yes, on request |
| `enocean_custom.climate_teach_in_actor_switch` | one or more `climate` entities | Sends the legacy RPS rocker teach-in of an SRC-D08; refused on other profiles. | yes, on request |
| `enocean_custom.reload` | — | Reloads YAML-configured platforms. | none |

Every refusal (unknown Base ID, dongle unavailable, missing sender ID,
rejected teach-in, unsupported profile) is reported as a translated error in
the UI and in automation traces.

## Triggers and events

Every rocker device (a `binary_sensor` row for an F6-02 switch) offers native
device triggers in the automation editor:

| Trigger type | Fires when |
| --- | --- |
| `button_pressed` | any button is pressed |
| `button_released` | the rocker is released |
| `button_pressed_channel_0` | the first rocker (A) is pressed |
| `button_pressed_channel_1` | the second rocker (B) is pressed |

Each trigger accepts the optional filters `which` (`0`, `1`, `10` for both) and
`onoff` (`1` = upper/I side, `0` = lower/O side). The same information is
available in the `button_pressed` bus event:

```yaml
event_type: button_pressed
data:
  name: Kitchen rocker
  id: [0, 58, 170, 240]
  pushed: 1          # 1 pressed, 0 released, null for other telegrams
  which: 0           # rocker half: 0, 1, or 10 for both
  onoff: 1           # 1 = I (upper), 0 = O (lower)
  repeated_telegram: 0
```

`enocean_custom_device_learned` is fired once per captured sender during a
learn window (`id`, `hex`, `evidence`, `support`, and the declared `eep` when a
UTE or 4BS teach-in carried one).

## Automation blueprints

Three ready-made automations live in
[`blueprints/automation/enocean_custom/`](blueprints/automation/enocean_custom/).
Import each one with its button (or paste the raw file URL into **Settings →
Automations & scenes → Blueprints → Import blueprint**), then create an
automation from the blueprint and fill in the inputs.

| Blueprint | What it does | Import |
| --- | --- | --- |
| `rocker_to_light.yaml` | One half of an F6-02 rocker turns a light on (I) and off (O) through the native device trigger. Home Assistant transmits nothing: the rocker keeps driving its directly paired relays as well. | [Import](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fszymkiewiczmathieu%2Fha_enocean_custom%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fenocean_custom%2Frocker_to_light.yaml) |
| `handle_open_hvac_off.yaml` | A window handle left `open` or `tilt` beyond a grace period switches the chosen climate entities off and notifies you; `closed` restores the HVAC mode you pick. | [Import](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fszymkiewiczmathieu%2Fha_enocean_custom%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fenocean_custom%2Fhandle_open_hvac_off.yaml) |
| `smoke_alarm_notify.yaml` | A smoke alarm notifies every phone (`notify.notify`) and turns the chosen lights fully on; a second message is sent when the alarm clears. | [Import](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fszymkiewiczmathieu%2Fha_enocean_custom%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fenocean_custom%2Fsmoke_alarm_notify.yaml) |

## Use cases

- **Lighting from battery-less rockers.** Learn each F6-02 wall switch, keep it
  paired with its relay, and additionally drive Home Assistant lights or scenes
  from the native triggers (blueprint `rocker_to_light.yaml`).
- **Window handles that pause heating.** HOPPE/Ubiwizz F6-10-00 handles report
  `open`, `tilt` and `closed`; pause the room's climate entity while a window is
  open and restore it when it closes (blueprint `handle_open_hvac_off.yaml`).
- **Smoke alarms on every phone.** A smoke `binary_sensor` turns the escape
  route on and notifies the household (blueprint `smoke_alarm_notify.yaml`).
- **Relays with real feedback.** Ubiwizz UBID1507C and NodOn D2-01 actuators are
  switched with directed D2 commands and only report `on`/`off` after their own
  status telegram, so dashboards never show a state the relay did not confirm.
- **Door and window contacts.** D5-00-01 contacts and A5-14-01 window sensors
  feed presence, security and ventilation automations.

## Supported devices

The authoritative matrix of decoded profiles (EEP), their platforms,
configuration mode and support level is maintained in
[docs/device-intelligence.md](docs/device-intelligence.md#implementation-matrix).
In short: F6-02-01/02 rockers, F6-05-02 smoke detectors, F6-10-00 handles,
D5-00-01 contacts, A5-14-01 window sensors, A5-12-01 meters, 8-bit A5
temperature/humidity profiles with a manual scale, A5-38-08 dimmer commands,
A5-20-04 valves (YAML), and D2-01 actuators (D2-01-0A/0B/08/12).

## Known limitations

- **One dongle per Home Assistant instance** (`single_config_entry`). A second
  dongle needs a second instance.
- **No automatic UTE acknowledgement.** The integration never answers a
  teach-in it did not explicitly ask for; pairing is always an operator action
  in the guided flows. Devices that require an unsolicited answer must be
  paired through those flows.
- **Ordinary RPS (F6) and 1BS (D5) telegrams carry no EEP.** Rockers, handles
  and contacts stay `profile_unknown` until you choose the platform/profile;
  nothing is inferred from an ID.
- **8-bit A5 sensors need a manual scale** (`min_temp`, `max_temp`,
  `range_from`, `range_to`) in this release.
- **ESP3 `OK` only proves the dongle transmitted.** It never proves that a
  relay switched or that a device retained a pairing.
- **Adding or removing a UI device reloads the whole entry.** Entities are
  unavailable for about a second while the serial worker restarts.
- **Secure (encrypted) telegrams, Smart Ack and Remote Management are not
  supported.** Secure telegrams are parsed as opaque frames and ignored.
- **The 868 MHz band allows about 1 % transmit duty cycle.** Avoid automations
  that hammer D2 actuators with commands every few seconds.

## Ubiwizz UBID1507C

The explicit `ubiwizz_ubid1507c` profile represents the documented two-output
`D2-01-12` module. It permits only channels `0` and `1`; selecting that profile
is an operator choice and is never inferred from a telegram, QR code, EURID or
switch type. Rows created before 2.10.0 with `switch_type: RPS`, which the
switch platform used to treat as UBID1507C at runtime, were migrated once to
carry the profile explicitly so their behaviour is unchanged.

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
using local association or directed D2 control. It covers both outputs,
HOPPE/Ubiwizz handles, D5-00-01 contacts, NodOn boundaries, and the required
safety checks.

### Repeater safety boundary

The **Ubiwizz repeater diagnostic** in the options flow is deliberately
read-only: it sends no radio, observes no state, and persists nothing. The
integration exposes no remote repeater write service because supplied Ubiwizz
documentation does not define a module-specific protocol or read-back. Do not
confuse the module repeater with the USB gateway's `CO_WR_REPEATER` command.

The **dongle's own repeater** is a different thing and is exposed as the
`select` entity **Repeater** on the gateway device (off, level 1, level 2). It
uses the documented ESP3 commands `CO_WR_REPEATER` and `CO_RD_REPEATER`: the
setting is read at start-up, written only when you pick an option, and the
displayed value always comes from the read-back that follows the write. An
enabled repeater makes the USB 300 retransmit every telegram it hears, which
extends coverage from a central location but adds radio traffic; leave it off
unless a far device needs it.

## Removing devices or the integration

- **One UI-managed device:** **Configure → Manage UI devices**, pick the device,
  confirm. The row is removed from the entry options and every entity registry
  row it created is deleted; the sender ID becomes teachable again immediately.
- **A YAML device:** delete its block from `configuration.yaml` and restart
  Home Assistant. A UI deletion is refused while the same ID is still in YAML.
- **The whole integration:** **Settings → Devices & services → EnOcean Custom →
  Delete**. The serial worker is stopped and joined before the entry is removed,
  UI devices disappear with the entry, and no files are written outside
  Home Assistant's own `.storage`. Remove any remaining `enocean_custom:` YAML
  blocks and imported blueprints yourself; the dongle keeps its own
  configuration (Base ID, learned links) untouched.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| **"No valid dongle found for this path"** when adding the integration | Use the `/dev/serial/by-id/...` alias, make sure no other integration or container opens the port, and that the user running Home Assistant may access it. |
| **Repair issue "The EnOcean serial worker stopped"** and all entities unavailable | The dongle was unplugged, the port vanished or a response timed out. Reconnect the dongle, then reload the integration (**Settings → Devices & services → EnOcean Custom → ⋮ → Reload**). |
| **Error "The dongle has not reported its Base ID"** on a switch | The start-up `CO_RD_IDBASE` read was refused or lost. The integration retries it automatically (three attempts with backoff); wait a few seconds or reload. |
| **Error "The dongle refused to queue the command"** | The serial worker is stopped or its transmit queue is full. Check the Repair issue and the logs. |
| **A rocker/contact entity never changes** | Confirm the four-byte ID with a learn session or the passive radio test; the row must use the sender ID printed in the telegram, not the dongle's Base ID. |
| **A switch stays `unknown` after a command** | Normal until the actuator answers with its status telegram. If it never does, the module is not paired with the dongle's Base ID: run the guided pairing or UTE association. |
| **Handle shows `unknown`** | Only the documented F6-10-00 nibbles map to `open`/`tilt`/`closed`; any other value is deliberately `unknown`. |
| **Repair issue "… has gone silent"** or a sensor/contact is unavailable while the dongle works | Silence detection is enabled for that category and the timeout elapsed. Check the device's battery/energy harvesting and range (signal strength sensor), or raise/disable the timeout under **Configure → Availability and silent devices**. |
| **Need details for a bug report** | Enable debug logging for `custom_components.enocean_custom` and download the diagnostics (identifiers are redacted). |

## Diagnostics and support

Use **Download diagnostics** when reporting an issue, either on the integration
(serial worker health, counters, dongle version, availability settings) or on a
single device (its configured rows by shape, radio facts from the inbox, the
last raw telegram with its sender bytes blanked, and its entities). Both exports
redact identifiers, names and the serial path. Include the Home Assistant and
integration versions, dongle model, exact profile, reproduction steps, and
redacted logs. See [SUPPORT.md](SUPPORT.md) for the issue checklist and support
options.

## Release and licensing

- [CHANGELOG.md](CHANGELOG.md) is the release record.
- [NOTICE](NOTICE) lists third-party attributions; the vendored EnOcean library
  retains its MIT license.
- This is an independent custom integration, not the official Home Assistant
  EnOcean integration.

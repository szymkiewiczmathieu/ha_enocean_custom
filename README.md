# EnOcean Custom

[![HACS](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/default)
[![Validation](https://github.com/szymkiewiczmathieu/ha_enocean_custom/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/szymkiewiczmathieu/ha_enocean_custom/actions/workflows/validate.yml)
[![GitHub stars](https://img.shields.io/github/stars/szymkiewiczmathieu/ha_enocean_custom?style=for-the-badge&logo=github&label=Stars)](https://github.com/szymkiewiczmathieu/ha_enocean_custom/stargazers)

Local-first Home Assistant integration for ESP3 EnOcean USB dongles (USB 300
and compatible). It provides configuration-entry and YAML setup, guided device
learning, diagnostics, native rocker triggers, automation blueprints and
bounded actuator control without cloud services or telemetry.

**Current release:** [v2.11.2](CHANGELOG.md) · Apache-2.0 · available in the HACS default store

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
- [Device subentries, migration and rollback](#device-subentries-migration-and-rollback)
- [Removing devices or the integration](#removing-devices-or-the-integration)
- [Troubleshooting](#troubleshooting)
- [Diagnostics and support](#diagnostics-and-support)
- [Release and licensing](#release-and-licensing)

## Install

1. [Install HACS](https://hacs.xyz/docs/setup/download/).
2. In HACS, search for **EnOcean Custom** (it is listed in the HACS default
   store, no custom repository needed) and install it.
3. Restart Home Assistant.
4. Open **Settings → Devices & services**, add **EnOcean Custom**, and select a
   persistent serial path such as `/dev/serial/by-id/...`. A USB 300 plugged in
   is also discovered automatically and offered for confirmation.

The only installation parameter is the serial device:

| Parameter | Meaning |
| --- | --- |
| `device` | Path of the ESP3 serial port, preferably the stable `/dev/serial/by-id/usb-EnOcean_GmbH_EnOcean_USB_300_...` alias. A `socket://host:port` or `rfc2217://host:port` URL also works, for a dongle shared over the network through ser2net, ESPHome's `stream_server`, or esp-ha-usb-gateway. |

A network link reconnects on its own after an unexpected drop: a growing
backoff (2s, 5s, 15s, 30s, 60s), then it gives up and waits for a manual
reload, so a flaky link never retries forever. A local device path never
reconnects on its own — this is unchanged and matches how a lost USB
connection has always behaved.

A dongle must have one reader. Do not configure `enocean` and `enocean_custom`
against the same serial device. If the port changes, use **Reconfigure** on the
existing entry rather than creating a second entry. Minimum Home Assistant
release: 2026.7.3 (declared in `hacs.json`); the test matrix also validates
2026.10.x.

## Configure devices

Since 2.11.0 every radio device is its own **config subentry** under the
integration (Settings → Devices & services → EnOcean Custom): the integration
page lists the devices, each with *Reconfigure*, *Rename* and *Delete*, and an
**Add device** button for a radio-silent add from an EnOcean ID or an EnOcean
Alliance commissioning label. Adding, reconfiguring or deleting a device
creates or removes that device's entities in place; the other devices are not
reloaded any more. See [Device subentries, migration and
rollback](#device-subentries-migration-and-rollback).

Use **Configure** on the integration for everything that involves radio or a
guided procedure: learn a device from its next telegram, add a QR/ID, pair an
actuator, associate a Ubiwizz module from its UTE request, declare the profile
of an existing device, manage UI-backed devices, or import eligible YAML
`binary_sensor` and `switch` rows. Those flows write the same device rows and
end up in the same subentries. YAML remains supported and can coexist with
UI-managed devices.

**Declare the profile of an existing device** records an EEP on a row whose
profile is unknown or was only asserted by hand. The existing entity keeps its
identity; decoders and companion entities follow the declared profile. The
typical case is a smoke detector added as a plain `smoke` binary sensor: declare
`F6-05-02` and it becomes a real detector (alarm from DB0 `0x10`/`0x00`) with a
diagnostic **Battery low** entity (`0x30`). A profile that would turn the
entity into a different class is refused; remove and re-add the device instead.
Instead of typing the EEP, a **Product** picker lists catalog recipes whose
platform matches the row (e.g. "Nexelec Insafe+ Origin" for a smoke detector,
"NodOn ASP-2-1-00" for a switch); picking one fills in the exact same EEP
field and is recorded exactly the same way, `eep_source: manual` included —
a recipe is a convenience, never proof of what the hardware actually is. The
bundled catalog (`custom_components/enocean_custom/data/products.json`)
covers NodOn ASP-2-1-00/01, SIN-2-2-01, SIN-2-RS-01 (documented only — this
integration has no cover platform), CWS-2-1-01, Eltako FT55, HOPPE
SecuSignal, Nexelec Insafe+ Origin, and placeholders for the Micropelt
MVA005 and Kieback & Peter MD15 valve actuators pending the experimental
valve item below.

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

**Profiles the bundled catalog lacks can be described in
`<config>/enocean_custom/profiles.json`** (plain JSON, strict schema, nothing
executed): they join the generic decoder's picker without a release. A profile
a built-in decoder already covers is refused rather than overridden, and an
invalid file disables every user profile and raises a Repair issue. Format and
rules: [User profile definitions](docs/device-intelligence.md#user-profile-definitions-configenocean_customprofilesjson).

**Adding a light or climate actor suggests a sender ID instead of asking you
to invent one.** A5-38-08 dimmer teach-in and the SRC-D08 climate panel each
need their own virtual transmitter identity, distinct from every other one
and from the dongle itself. The *Add device* form now pre-fills the Sender
ID field with the dongle's own Base ID plus the lowest free offset (1..127,
computed in software — never `CO_WR_IDBASE`), still free to override, for
instance to re-use the exact ID a receiver was already taught by a device
this one replaces. A sender ID already in use by another light, climate
actor or RPS-emulated switch is refused rather than silently shared. Every
existing row keeps the sender ID it already has.

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
- **D2-01 actuators can be asked for their state.** After a start-up a D2-01
  relay stays `unknown` until it sends a telegram. Two ways to ask it, both
  radio-silent unless you act: the **Refresh state (channel N)** button on
  each D2-01 actuator sends one Actuator Status Query (CMD 0x3) on demand;
  **Configure → D2-01 actuators** optionally (off by default) sends that same
  query to every D2-01 actuator channel once after each start-up or reload.
  Queries are serialized (one ESP3 command in flight), at least 250 ms apart,
  never repeated, and the actuator's answer is awaited within the 300 ms
  window the EEP specification gives it (EEP 2.6.7, D2-01 CMD 0x3, p. 135).
  Profiles whose TYPE reports measurements on query (EEP 2.6.7 D2-01
  capability table, pp. 131-132: for instance D2-01-0B, not D2-01-0A nor
  D2-01-12) also get an energy and/or power Measurement Query (CMD 0x6) when
  a power/energy sensor row exists on the same sender to receive the answer.
  Rows emulating a rocker (Ubiwizz UBID1507C `RPS` rows) are never queried.
  The only local setting an actuator reports back, local control, is shown
  read-only as `d2_local_control_enabled`; see Known limitations for why the
  local-settings write (CMD 0x2) is not offered.
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
- **Devices can be deleted from their device page or from the integration
  page.** Deleting a radio device removes its subentry, its rows and its
  entities immediately; the gateway device and YAML-configured senders refuse
  deletion.
- **Exactly one ESP3 command is in flight.** The worker waits for the dongle's
  response before writing the next packet; a missing response stops the
  transport instead of mis-attributing a later response.
- **Radio health is observable.** The dongle appears as its own device
  (firmware and chip version read with `CO_RD_VERSION`, received-telegram
  counter, last telegram, connectivity, Base ID). Every radio device carries
  four diagnostic sensors, disabled by default: signal strength (dBm, icon by
  range), last telegram, repeater hops, and remaining energy as self-reported
  by a SIGNAL telegram (RORG `0xD0`, MID `0x06`) on the rare devices that send
  one. Enable them from the device page to build a coverage dashboard; they
  cost nothing while disabled. A device that also sends other SIGNAL MIDs
  (firmware revision, heartbeat, RX-channel quality, Product ID) shows the
  latest one in its diagnostics download; this integration only ever listens
  for them, never triggers them — see
  [`docs/device-intelligence.md`](docs/device-intelligence.md).

## Actions

| Action | Target / fields | What it does | Radio transmission |
| --- | --- | --- | --- |
| `enocean_custom.learn` | `timeout` (15–300 s, default 60) | Opens one teach-in window; the first unknown sender fires `enocean_custom_device_learned` with its ID, hex form and safe radio evidence. Refused while another window is open or the dongle is not loaded. | none |
| `enocean_custom.send_teach_in` | one or more `light` entities | Sends the A5-38-08 4BS teach-in telegram with the light's configured sender ID. | yes, on request |
| `enocean_custom.climate_teach_in_actor` | one or more `climate` entities | Sends the configured profile's 4BS Variation-3 teach-in response (SRC-D08 panel, A5-20-04, or the experimental A5-20-01/A5-20-06). | yes, on request |
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

Every rocker device also gets a companion `event` entity, enabled by default,
next to the existing triggers above (nothing above changes or stops working).
It gives the same facts a typed, translatable shape with its own history
timeline in the UI, with no custom trigger needed to react to a specific
button: `channel_0_pressed`/`released`, `channel_1_pressed`/`released`, and
`both_pressed`/`released` for the bitfield telegram some rockers send when
both halves are pressed together. A rocker has no telegram of its own for a
long press, so `channel_0_long_pressed`, `channel_1_long_pressed` and
`both_long_pressed` are computed locally: held for one full second between
the press and the release telegrams, fired right after the matching
`_released` event. The entity only ever listens to the same `button_pressed`
bus event above; it never parses a radio telegram itself, so the two can
never disagree.

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
temperature/humidity profiles with a manual scale, D2-14-30/41 multi-sensors,
the generic EEP.xml sensor decoder, A5-38-08 dimmer commands, A5-20-04 valves
(YAML), and D2-01 actuators (D2-01-0A/0B/08/12). A5-20-01 and A5-20-06 valves
are also implemented but **experimental and not validated on real
hardware**: YAML-only, refused without an explicit `experimental_valve_ack:
true`, see [docs/device-intelligence.md](docs/device-intelligence.md#experimental-valve-strategies-a5-20-01-and-a5-20-06).

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
- **A5-20-01 and A5-20-06 valves are experimental and unvalidated on real
  hardware.** They reply to the valve's own status telegram from inside the
  dispatcher callback to respect the EEP's one-second budget; the actual
  round-trip latency has never been measured against a real dongle or valve.
- **ESP3 `OK` only proves the dongle transmitted.** It never proves that a
  relay switched or that a device retained a pairing.
- **Changing the availability or D2-01 settings still reloads the whole
  entry** (entities are unavailable for about a second while the serial worker
  restarts). Adding, reconfiguring or removing a device no longer does.
- **D2-01 local settings (CMD 0x2) and measurement settings (CMD 0x5) are not
  written.** CMD 0x2 rewrites every local setting at once (taught-in devices
  enable, over-current behaviour, local control, dim timers, day/night mode,
  power-failure detection, default state) with no "keep" value, and the D2-01
  family defines no read-back for most of them ("Response Timing: None",
  EEP 2.6.7 pp. 133-134), so a change could neither be limited to one setting
  nor verified. Only the local-control flag the actuator reports in its own
  status (CMD 0x4) is exposed, read-only.
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

## Device subentries, migration and rollback

**What changed in 2.11.0.** Each radio device (one four-byte EnOcean sender)
is a Home Assistant *config subentry* of the EnOcean Custom entry: its unique
ID is the sender, its title the device name, its data the configuration rows
of that sender. Home Assistant's rule "a device belongs to exactly one config
entry or subentry" (enforced since 2026.10) is therefore met by construction,
and adding, reconfiguring or deleting one device touches only that device's
entities. The dongle itself (the *EnOcean gateway* device and its diagnostic
entities) stays on the main entry.

**Migration.** On the first start with 2.11.0 the config entry moves to minor
version 3: the existing `ui_devices` rows are grouped by sender, one subentry
is created per sender, and each sender's existing registry device and
entities are re-attached to that subentry in place. Nothing is created,
renamed or deleted: every `unique_id`, `entity_id` and device id, every custom
name, area, label and disabled state is exactly as before, so automations,
dashboards and history are untouched. The migration is replayed by the test
suite on a copy of a real 43-row / 41-device / 231-entity installation
against both supported Home Assistant releases. The log line
`Migrated EnOcean entry to minor version 3: N radio device(s) now live in
their own config subentry` confirms it ran.

**The `ui_devices` option stays as a mirror.** The rows every subentry holds
are also kept in the entry's `ui_devices` option, exactly where 2.10.x stores
them. The guided flows under *Configure* keep reading and writing that list,
and the integration keeps the two in step (a device added from the
integration page shows up in *Manage UI devices*, a device removed under
*Configure* loses its subentry, and so on).

**Rollback.** The recommended way back is to restore the Home Assistant
backup taken before the upgrade; Home Assistant has no "un-migrate".
Downgrading in place to 2.10.x is nevertheless non-destructive: 2.10.x only
reads `ui_devices`, which still holds every row, so every device keeps
working with its existing entities (the registry re-attaches them to the
main entry at the next start; Home Assistant 2026.10 logs one deprecation
line per device while doing so). The subentries then sit unused in
`.storage/core.config_entries` until you upgrade again, at which point they
are the source of truth: devices you added under 2.10.x in the meantime are
adopted into new subentries, while devices you deleted under 2.10.x come back
because their subentry still exists. Delete them again from the device page.

## Removing devices or the integration

- **One UI-managed device:** from its device page (⋮ → *Delete*), from the
  integration page (its subentry → *Delete*), or **Configure → Manage UI
  devices** to remove a single row of a multi-channel device. The device's
  subentry and every entity registry row it created are deleted; the sender
  ID becomes teachable again immediately.
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
| **A D2-01 switch stays `unknown` after a restart** | Press its **Refresh state** button, or enable **Configure → D2-01 actuators** to ask every D2-01 actuator once after each start-up. If the button leaves it `unknown`, the actuator did not answer within 300 ms: check pairing, range (signal strength sensor) and that the row's channel matches the actuator's output. |
| **"Add device" says the ID is already configured** | That sender already has a subentry (one per EnOcean ID). Open that device to reconfigure it, or use **Configure → Add a device** to add another channel/row to the same sender. |
| **A device deleted under 2.10.x came back after upgrading again** | Its subentry still existed; subentries win over the `ui_devices` mirror on upgrade. Delete the device from its page once more. |
| **After the upgrade a device shows no entities** | Check the log for `Migrated EnOcean entry to minor version 3`; download the integration diagnostics and look at `subentries.legacy_rows` (should be 0) and the device diagnostics `in_subentry` (should be true). Reload the integration once; if a row stays legacy, its sender has a malformed row in `.storage` that was never a working entity. |
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
- Each release carries one asset, `enocean_custom.zip`, from which HACS
  installs and updates the integration (`hacs.json`: `zip_release`). It is the
  content of `custom_components/enocean_custom/`, built reproducibly by
  `scripts/build_release_zip.py`; the maintainer procedure is in
  [docs/releasing.md](docs/releasing.md).
- [NOTICE](NOTICE) lists third-party attributions; the vendored EnOcean library
  retains its MIT license.
- This is an independent custom integration, not the official Home Assistant
  EnOcean integration.

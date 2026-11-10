# Changelog

## 2.16.1 - 2026-11-10

### Smoke detectors

- **A smoke alarm is no longer replayed when a detector comes back.** When a
  detector had been silent for longer than the silence timeout, or when the
  dongle dropped, the entity used to republish its previous state before
  decoding the next telegram. An alarm that was on before the loss of contact
  therefore produced `unavailable -> on -> off`, and an automation waiting for
  `on` fired a second, false alert. The entity now forgets its state as soon as
  contact is lost and takes the next decoded telegram as it comes. A real
  alarm is unaffected: F6-05-02 repeats "alarm on" every 10 seconds, so it is
  received again right away. Other entity types keep their last state exactly
  as before.
- **New `smoke_alarm_off` preset for the simulated telegrams.** It sends the
  F6-05-02 "Smoke Alarm OFF" value (EEP 2.6.8) so a simulated alarm can be
  ended without waiting for the detector's own 20-minute repeat. It is still
  admin-only, opt-in, marked `simulated: true` and never transmits by radio.
- No identifier, entity name or option changes; a code-only rollback to 2.16.0
  remains possible. **Not verified on hardware:** a real detector's behaviour
  after a long silence; the change is covered by simulated telegrams only.

## 2.16.0 - 2026-11-10

### Home Assistant 2026.11 compatibility

- **Climate entities use the `native_` temperature members.** Home Assistant
  2026.11 renamed `temperature_unit`, `current_temperature` and
  `target_temperature` (and the high/low pair) with a `native_` prefix and logs
  `EnOceanClimate provides the deprecated _attr_temperature_unit` /
  `... target_temperature` at start-up; the old names stop working in 2027.11.
  The climate platform now provides the native members on a core that knows
  them and keeps serving the unprefixed ones on older supported cores
  (2026.7 to 2026.10). The room temperature moved from
  `_attr_current_temperature` to `_attr_native_current_temperature`, which
  also removes a third warning (`writes the deprecated
  EnOceanClimate._attr_current_temperature`) that only installations with a
  climate row would have seen. No state, attribute, unit, identifier or radio
  behavior changes.

### Serial link diagnostics

- **`Message incomplete, buffered_bytes=N` no longer fills the warning log.**
  The vendored ESP3 parser logs that line whenever a serial read ends inside
  the six-byte frame header. It is ordinary read fragmentation: the bytes stay
  buffered and the next read completes the frame (one installation logged it
  120 times in 22 hours for 1601 telegrams, with no CRC error and no frame
  time-out). The integration now counts the event, keeps it available at debug
  level and stops it from reaching the warning log.
- **New `esp3_framing` block in the integration diagnostics**, counted since
  the dongle was set up: `partial_header_reads` (the benign case above) and the
  real link faults `header_crc_errors`, `data_crc_errors`,
  `invalid_frame_lengths` and `frame_timeouts`. The faults are still logged as
  errors, exactly as before. The counters hold no identifier.
- The vendored library is untouched: the counters come from a logging filter
  owned by the integration, attached to the library's two loggers while the
  entry is loaded and removed on unload. A test pins every watched log
  template to the vendored sources so a library update cannot drift silently.

### Smoke detectors

- **New Repair issue "… needs its smoke detector profile declared".** A
  `smoke` binary sensor row without a declared profile keeps the historical
  rocker decoder, whose state follows the ERP1 status bits of a press or a
  release. An F6-05-02 detector is not a rocker: on one installation an
  Ubiwizz UBILD001 repeated "Smoke Alarm OFF" every 20 minutes (66 telegrams
  in 22 hours, as EEP 2.6.8 specifies) and the entity never left `unknown`,
  so an alarm automation waiting for `on` could not be relied upon. Such a row
  now raises a non-fixable Repair issue naming the device and the profile to
  declare under **Configure → Declare the profile of an existing device**. It
  is raised once per entity, only for F6-05-02 values (`0x00`, `0x10`, `0x30`)
  received with status bits that are neither a press nor a release, and it
  clears itself when the profile is declared or the device is removed.
  Translated in English, French, German and Dutch.
- Decoding is deliberately unchanged: a profile is still never inferred from a
  telegram, rocker-shaped smoke rows keep working without any issue, and
  declaring `F6-05-02` keeps the entity's identity as before.
- README: the 20-minute F6-05-02 repeat interval and a suggested 60 to 90
  minute silence timeout are documented; the default stays 0 (never).

### Compatibility

- No `unique_id`, entity name, translation key or device identifier changes:
  an identity dump of 61 row shapes, 51 generic profiles and the 248 entities
  built from one real installation's rows is identical before and after, on
  Home Assistant 2026.7.3 and 2026.10.2.
- No new dependency, no config entry migration (`ConfigFlow.VERSION` stays 1),
  no new radio transmission. `teach_in=False` is unchanged and no UTE answer is
  ever sent automatically.

### Not verified on hardware

- The climate change was exercised against the real climate base class of
  Home Assistant 2026.11.0 laid over a 2026.10.2 environment (three warnings
  before, none after), not on a running 2026.11 instance, and no A5-20 valve
  or SRC-D08 thermostat was driven.
- No smoke was produced: the alarm telegram of an Ubiwizz UBILD001, its status
  bits and what its test button transmits are unknown. Only the periodic
  "Alarm OFF" telegram was observed, and its status bits are inferred from the
  entity staying `unknown`, not read from a capture.
- The framing counters were driven by the real parser with split and corrupted
  frames, not by a physical USB 300 on a marginal link.

## 2.15.1 - 2026-11-09

### Fixes

- **No more blocking file read at start-up.** Home Assistant logged
  `Detected blocking call to read_text ... inside the event loop` when the
  integration loaded, because the generic sensor catalog read its bundled
  profile file on the event loop. The catalog is now built once in the
  executor during set-up. No entity, identifier or behavior changes; a
  code-only rollback to 2.15.0 remains possible.

## 2.15.0 - 2026-11-09

### Measurement corrections and new sensors

- **A5-09-05 now applies the SCM scale multiplier.** The existing
  `<id>-eep_conc` entity keeps its identity and ppb unit. The received
  `scale_multiplier` and `voc_id` are exposed as attributes to help diagnose
  older transmitters that may leave SCM at zero.
- **A5-12-01 gets an additive, correctly scaled kWh energy entity:**
  `<id>-a5_12_01_energy`, enabled by default only when the row declares
  A5-12-01. The historical `<id>-energy` (Wh) and `<id>-powersensor` behavior is
  deliberately unchanged, so existing templates and long-term statistics do
  not jump. A non-fixable Repair asks affected users to select the new entity
  in the Energy dashboard; freezing the historical entity is planned for 2.16.
  **No historical statistic is rewritten.**
- Added generic receive-only decoders for A5-04-02, A5-07-02/03, A5-08-02/03,
  A5-12-02 gas and A5-12-03 water meters, and dual-unit A5-09-0C VOC. Gas and
  water expose cumulative m³ and L/s flow with tariff information; VOC has
  independent ppb and µg/m³ entities updated only for their declared unit.
- A5-13 weather stations now expose day/night plus A5-13-02 west/south/east sun
  intensity in lx and hemisphere information. Existing dawn, temperature, wind
  and rain identities and values are unchanged. A5-07 motion uses 1 = motion;
  A5-08 occupancy keeps its previous 0 = occupied polarity.

### Serial recovery and diagnostics

- **Local USB paths and network URLs now reconnect automatically** after an
  unexpected stop, with 2/5/15/30/60-second delays and an unlimited 60-second
  plateau. The old worker is joined before reopening, a missing local path is
  not opened, and the retry counter resets only after a valid CO_RD_VERSION
  response. Unload cancels the next attempt. A stable `/dev/serial/by-id` path
  remains recommended for USB.
- Diagnostics now include the oldest transmit-queue age, CO_RD_IDBASE optional
  remaining write cycles (`0..254`, `unlimited`, or unknown when absent), and an
  anonymized hint when four UTE requests repeat within 300 seconds. These paths
  are read-only: no Base ID write, automatic UTE response or 40 ms TX pacing was
  added.

### D2-01-0C pilot wire

- Declared default D2-01-0C switch rows get an additive six-mode pilot-wire
  select: Off, Comfort, Eco, Anti-freeze, Comfort-1 and Comfort-2. An explicit
  user selection sends exactly one CMD 0x8, waits for its ESP3 result, sends one
  CMD 0x9 and accepts only a valid CMD 0xA within 300 ms. Missing or invalid
  feedback raises a translated error and leaves the old state unchanged.
- Existing D2-01 measurement behavior remains W and Wh. CMD 0x2 local settings,
  CMD 0x5 measurement settings, autonomous configuration changes and automatic
  radio commands are not implemented.

### Compatibility and validation boundaries

- Existing unique IDs, config-entry version 1 and the manifest requirements are
  unchanged. New companion identities are additive and a code-only rollback to
  2.14.1 remains possible. `enocean_library/` is unchanged.
- **NOT VERIFIED ON HARDWARE:** actual SCM values of old VOC transmitters;
  A5-12-01/02/03 readings against meter displays; A5-07/08 occupancy polarity
  and illuminance; A5-13 directions and night state; the real unit selected by
  an A5-09-0C sensor; USB300 unplug/replug and network outages; and every
  pilot-wire mode applied and read back on a physical D2-01-0C module. All were
  tested with exact byte vectors and simulated transports only.
- ReCom, MQTT, free-form radio sending and TX pacing are outside this release.

## 2.14.1 - 2026-11-06

Fixes the admin panel looking unchanged after an update, and README screenshots that did not show inside Home Assistant. Nothing to migrate; a plain code rollback to 2.14.0 stays possible.

### Fixes

- **The EnOcean side panel now reloads after every update.** The panel script was served from a fixed URL, so browsers and the mobile app kept showing the previous version until their cache was cleared. The URL now carries the integration version, which forces a fresh download on each release.
- **README screenshots use absolute URLs**, so they also display where the README is rendered outside GitHub, such as the Home Assistant Marketplace.

## 2.14.0 - 2026-11-06

A redesigned, more graphical admin panel and a README that fits small screens. Nothing to migrate; a plain code rollback to 2.13.0 stays possible.

### Admin panel redesign

- Reworked the EnOcean side panel with a live gateway health banner, signal
  gauges, grouped device cards, freshness and state badges, platform/EEP icons,
  client-side search and filters, and a card-based Nearby view sorted by signal
  strength. Nearby adoption still opens the existing commissioning flow.
- Refined the visual hierarchy with consistent auto-fill grids, uniform freshness
  accents, last-known states and measurement units, compact Details controls,
  clearer KPI tiles and a client-sampled 30-minute packet activity chart. Masked
  identifier rows are omitted; repeater details appear only when hops exist.
- Rebuilt the telegram view as a compact live feed with distinct RORG colors, a
  separate simulated-provenance marker, short timestamps, entry transitions,
  accessible signal quality and Start, Stop and Clear controls. The stream
  remains opt-in and bounded to 100 in-memory records; identifier masking
  remains session-only.
- Added responsive light and dark styling, a fixed mobile tab bar with icons,
  keyboard tab navigation, visible focus, reduced-motion support and measured
  small-text contrast. Added a fictional-data browser preview, desktop, Nearby
  and mobile screenshots, and Node-backed tests for pure presentation helpers.
- Added the strictly additive `unit` field to each device in the panel snapshot,
  sourced from the Home Assistant state's `unit_of_measurement` attribute. No
  service, dependency or radio behavior changed.

## 2.13.0 - 2026-11-05

Shutters, a hardware-free test bench and an admin side panel. Everything new is
additive: nothing to migrate, and a plain code rollback to 2.12.0 stays possible.

### Roller shutters

- **D2-05-00 TYPE 0x00 covers can now be configured from the UI.** Open, close,
  set an open percentage and stop a NodOn SIN-2-RS-01 or an equivalent
  explicitly declared actuator. Position feedback is decoded from CMD 0x4,
  with the specification's 0 % open convention inverted for Home Assistant.
  A start-up query and one delayed post-stop query refresh the actual position;
  the last known position is restored without pretending that a new command
  already moved the shutter. Slat angle is preserved as a diagnostic attribute,
  but no unsupported tilt entity is invented.
- **No automatic UTE answer and no new migration.** Commissioning still uses
  the existing explicit flows. Only channel `0` is accepted for this one-output
  TYPE; CMD 0x5 motor/alarm parameter writes are deliberately not implemented.
  The new cover identity is additive and existing identities, options and
  config-entry versions are unchanged. Physical shutter validation is still
  required before relying on the profile with an unfamiliar device.

### ESP3 emulator

- **The real serial gateway can now be tested without hardware.**
  `scripts/fake_esp3_module.py` implements a CRC-checked ESP3 peer over a Linux
  or macOS pseudo-terminal, or a loopback-only TCP socket. It answers the
  version, Base ID and repeater requests made by the integration, acknowledges
  outgoing radio frames, and injects RPS, 1BS, 4BS and VLD telegrams, including
  D2-01, A5-20-04 and D2-05-00 examples.
- Integration tests run the actual dongle and communicator against the
  emulator for start-up identification, inbound and outbound traffic, link
  loss, bounded reconnection and clean unload. This is protocol-level proof,
  not a substitute for validation with a physical module.

### D2-01 command feedback check

- **An optional check detects actuators that never confirm a command.** Under
  *Configure → D2-01 actuators*, enable *Verify state after D2-01 commands* to
  wait 1.5 seconds for the requested state and make at most two retries. After
  three total attempts, `enocean_custom_no_feedback` reports `entity_id`,
  `device_id`, `command` and `attempts`. Matching feedback stops the check;
  opposite feedback does not. The option is off by default, RPS-emulated
  switches are unaffected, and displayed state remains feedback-only.

### Admin diagnostics and simulation

- **A new admin-only EnOcean side panel** groups configured devices, passive
  nearby transmitters, a bounded live telegram view and gateway health. It uses
  Home Assistant's authenticated websocket API and `panel_custom`; there is no
  unauthenticated data endpoint. Sender IDs and the Base ID are masked by
  default, with a session-only reveal control. Live recording starts only on
  request, keeps the newest 100 records in memory and is cleared when the last
  subscriber leaves. *Open commissioning* starts the existing learn flow. The
  panel JavaScript has passed syntax validation, but has not been exercised in
  a browser.
- **`enocean_custom.simulate` is an admin-only, opt-in receive-chain test.**
  Enable *Configure → Simulated telegrams*, then inject a window contact,
  temperature, rocker, smoke or cover-position preset for an explicit sender.
  Nothing is transmitted or persisted. Every request logs a warning and fires
  `enocean_custom_simulated_telegram` with `simulated: true`; the rocker event
  and panel records carry the same marker. There is no teach-in preset and no
  automatic radio response.

### Validation and compatibility

- No existing `unique_id`, entity name, default state, option or stored schema
  changes. `ConfigFlow.VERSION` remains `1`; the new option keys and cover rows
  are ignored by 2.12.0 rather than causing a migration. No Python dependency
  was added and the vendored `enocean_library` is unchanged.
- The unit suite grows from 598 to 626 tests. Protocol emulation, cover bytes,
  bounded feedback retries, simulation permissions and panel privacy are
  covered by automated tests. No physical dongle, shutter, relay or smoke
  detector was used for this release.

## 2.12.0 - 2026-11-04

Reliability and privacy release. It fixes a false "unavailable" state that a busy
dongle could cause, lets a failing heating valve tell you about it, puts teach-in on
the device page, closes two diagnostics leaks and removes two lifecycle races.

**Nothing to migrate.** No entity id, unique id, name, state, option or stored
schema of an existing device changes, and a config entry written by 2.12.0 is still
readable by 2.11.2, so a plain code rollback stays possible. The only new entities
are the teach-in buttons below, created for devices you already have.

### Your devices stay available when the dongle is merely busy

- **A command that the dongle refuses for a moment no longer turns the entity
  `unavailable`.** When the transmit queue is full the dongle is healthy, yet the
  previous release marked the commanding light or switch unavailable until the next
  telegram. The command now returns `False`, logs a warning, and leaves availability
  to what really measures it: the dongle health signal and silence detection. A
  stopped dongle still makes every entity unavailable, and a missing gateway is
  handled exactly as before.

### A failing valve now tells you

- **A5-20-04 valve failures raise a Repair issue.** A non-zero failure code in the
  valve feedback creates a translated issue naming the valve and showing the code;
  code `0x00` or a normal status telegram clears it by itself. The `valve_failure`
  attribute is unchanged. The issue is not persisted across a Home Assistant restart:
  the next failing telegram raises it again.

### Teach-in is one click from the device page

- **Teach-in buttons for UI-managed dimmers and climate devices.** Each one calls the
  existing `send_teach_in`, `climate_teach_in_actor` or
  `climate_teach_in_actor_switch` action, with the same validation and the same
  translated errors. A button contains no radio code of its own. Dimmer rows get one
  button, climate rows get an actuator button and a rocker-input button, all in the
  device's configuration section. Deleting a device removes its buttons. Devices
  declared in YAML keep using the actions directly.

### Privacy: two diagnostics leaks closed

- **SIGNAL telegrams no longer export embedded identifiers.** The Product ID carried
  by a SIGNAL telegram (MID `0x12`) and the second sender reported by an RX quality
  telegram (MID `0x0A`) were returned as raw bytes in the last-telegram dump even
  though the structured fields were redacted. They are now zeroed like the ERP1
  sender.
- **Unknown registry identifiers are redacted.** An identifier that is not a radio
  sender (a Product ID, or any free text) was returned verbatim by the device
  diagnostics. It is now `**REDACTED**`; the shape of the report is unchanged.

### Lifecycle

- **Unloading during a network reconnection no longer brings the serial worker back
  up.** If the unload won the race while the connection was being opened, the freshly
  opened descriptor is now closed instead of starting a worker on an integration that
  is going away.
- **A cancelled repeater read-back releases its waiter.** Cancelling the wait
  (entity removal, unload) left a dead future registered; the waiter is now released
  on success, timeout and cancellation alike.
- **Action buttons and rocker event entities no longer listen to raw radio.** They
  never decoded it. Each one used to subscribe to every telegram for nothing; they now
  keep only the dongle availability signal. Their real triggers are unchanged.

### Tests and compatibility

- The suite grows from 588 to 598 tests, and the real-Home-Assistant suite from 13 to
  23. It now covers every statement of `config_flow.py` and `diagnostics.py`, and
  presses the new buttons through the real entity services and device registry.
- Every fix above was first reproduced by a failing test. The 598 tests and the smoke
  test pass on Home Assistant 2026.7.3 and 2026.10.2.
- Not covered here: no physical dongle or A5-20-04 valve was used for this release;
  the valve Repair and the buttons are proven by tests on the exact telegram bytes and
  actions, not by a new field test.

## 2.11.2 - 2026-10-24

- **No more `via_device` deprecation warning on Home Assistant 2026.10+.** Each
  radio device is linked to the "EnOcean gateway" device through
  `via_device_id` (the registry id of the gateway device) instead of the
  `via_device` identifier tuple, which Home Assistant 2026.10 deprecates and
  will stop accepting in 2027.8. On older supported cores (2026.7, which have
  no `via_device_id`) the previous link is kept. The device tree, device ids,
  entity ids and unique ids do not change: the same gateway device is
  referenced, only the way it is designated.
- Tests: the unit test covers both cores, and a real-core setup test fails if
  the device registry logs the `via_device` deprecation and checks that every
  radio device hangs off the gateway.

## 2.11.1 - 2026-10-24

Packaging only, no runtime change: the integration code, strings and
translations are byte for byte those of 2.11.0; only the version in
`manifest.json` moves.

- **HACS installs each release from a `enocean_custom.zip` asset** attached to
  it (`hacs.json`: `"zip_release": true`, `"filename": "enocean_custom.zip"`)
  instead of the Git tree. Why: the download figure HACS shows in its store is
  the GitHub download count of a release asset, so from this release on it
  counts real installations and updates (the "1" shown until now was a leftover
  of an asset of August 2026). Nothing changes for users: same HACS install and
  update steps, same files installed; earlier releases keep installing from the
  Git tree because HACS reads the `hacs.json` of the version it installs.
- `scripts/build_release_zip.py` builds the asset deterministically (content of
  `custom_components/enocean_custom/` at the archive root, sorted entries, fixed
  timestamp, stored) and `--check` refuses an archive whose layout, paths, file
  list, content or manifest version is wrong; `tests/test_release_zip.py` covers
  it. A new `release-zip` CI job builds the archive twice, checks it, extracts it
  the way HACS does and runs the Home Assistant smoke test on the extracted
  tree. A new `Release asset` workflow re-verifies the attached asset at
  publication (sha256 against a rebuild from the tag) and attaches it if it was
  forgotten, so a forgotten upload never leaves HACS updates failing.
- `docs/releasing.md`: release procedure (draft release → asset → check →
  publish), what happens when the asset is missing and how to repair.
- README: GitHub stars badge.

## 2.11.0 - 2026-10-24

Lot 2 of the roadmap: config subentries (A1), D2-01 state queries (M11),
German and Dutch translations (A8).

### One config subentry per radio device (A1)

- Every EnOcean radio device (one four-byte sender) is now a Home Assistant
  **config subentry** of type `device`: its unique ID is the sender, its title
  the device name, its data the configuration rows of that sender. The
  integration page lists the devices with *Reconfigure*, *Rename* and
  *Delete*, plus an **Add device** button (radio-silent: EnOcean ID or
  Alliance commissioning label, platform, details). Adding, reconfiguring,
  renaming or deleting a device creates or removes that device's entities in
  place; **the entry is no longer reloaded** for device changes (it still is
  for the availability and D2-01 settings). The dongle's own gateway device
  and entities stay on the main entry. This meets Home Assistant's "a device
  belongs to exactly one config entry or subentry" rule, enforced since
  2026.10.
- **Migration to config entry minor version 3**, automatic and idempotent on
  the first start: the `ui_devices` rows are grouped by sender, one subentry
  is created per sender, and each sender's existing registry device and
  entities are re-attached to it in place. No `unique_id`, `entity_id` or
  device id changes; custom names, areas, labels and disabled states are
  untouched; nothing is deleted. The entities are moved before their device
  on purpose: both HA 2026.7 and 2026.10 delete the entities of a device
  that changes subentry while they still point at the old one. The device
  move uses `new_config_subentry_id` on 2026.10 and the add/remove pair on
  2026.7. Verified by replaying the migration on a copy of a real 43-row,
  41-device, 231-entity installation through the real registries of both
  releases (`tests/test_subentry_migration.py`,
  `tests/fixtures/live_2102_anonymized.json`), with 231/231 identity parity
  (`tests/test_live_parity.py`).
- **`ui_devices` stays as a complete mirror** of the subentry rows. Every
  guided options flow (learn, QR, UTE association, D2 pairing, declare
  profile, manage, YAML import) keeps reading and writing that list, so their
  behaviour and regression tests are unchanged; one re-entrancy-safe update
  listener keeps the mirror and the subentries equal in both directions. The
  mirror is also what makes a **downgrade to 2.10.x non-destructive**: that
  release reads `ui_devices` and finds every device. The recommended rollback
  is still restoring the Home Assistant backup taken before the upgrade; see
  the README section "Device subentries, migration and rollback".
- The device-details form and its validation move to `device_form.py`,
  shared by the options flow and the new subentry flow. New strings under
  `config_subentries.device`.

### D2-01 actuators: state query and refresh button (M11)

- **Configure → D2-01 actuators**: a new entry option, off by default. When
  enabled, every D2-01 actuator channel (`switch` rows with
  `switch_type: default`) is sent one Actuator Status Query (CMD 0x3, EEP
  2.6.7 p. 135) once after each start-up, in a single serialized pass: one
  ESP3 command in flight, the actuator's CMD 0x4 answer awaited within the
  specification's 300 ms window counted from the physical write, at least
  250 ms between two transmissions, never retried. Nothing is transmitted
  unless the option is on.
- A **"Refresh state (channel N)" button** per D2-01 actuator channel (new
  `button` platform) transmits the same query on demand. A refused queue or
  an unresolved Base ID raises the existing translated errors.
- Measurement queries (CMD 0x6, p. 137) are added to a pass only for a
  declared D2-01 TYPE whose capability matrix (pp. 131-132, transcribed in
  `d2_01.py`, nothing inferred) marks "Measurement report on query", and only
  when a power/energy sensor row exists on the same sender to receive the
  CMD 0x7 answer. D2-01-0A (NodOn ASP-2-1-00) has no metering; D2-01-0B
  (ASP-2-1-01) gets energy and power.
- **Not implemented, on purpose:** CMD 0x2 Actuator Set Local and CMD 0x5
  Actuator Set Measurement. CMD 0x2 rewrites every local setting at once with
  no "keep" value and the family defines no read-back for most of them
  ("Response Timing: None", pp. 133-134), so a write could neither be limited
  to one setting nor verified. The one local setting an actuator reports in
  CMD 0x4, local control, is exposed read-only as the new
  `d2_local_control_enabled` attribute.
- Ubiwizz UBID1507C rows emulating a rocker (`switch_type: RPS`) are never
  queried; a UBID1507C associated through its UTE request is a plain
  D2-01-12 actuator and is queried like any other. No Ubiwizz repeater
  command, as before.

### Documentation and translations (A8)

- Complete German (`de.json`) and Dutch (`nl.json`) translations; a new
  test requires every language to carry exactly the keys and placeholders of
  `strings.json`.
- README: device subentries, migration and rollback; D2-01 state query,
  measurement and the CMD 0x2 boundary; new troubleshooting rows. Known
  limitations updated (device changes no longer reload the entry).
- Entry diagnostics report the subentry counts; device diagnostics report
  whether the device is in its subentry.

### User profile definitions (A6)

- `<config>/enocean_custom/profiles.json` may add generic profiles the
  vendored `EEP.xml` lacks: plain JSON, strict schema (unknown keys and
  unbounded numbers refused, 4BS and VLD only), nothing executed. A profile
  a built-in decoder already covers is refused, never overridden; an invalid
  file disables every user profile and raises a Repair issue quoting the
  error; refused profiles of a valid file raise a second Repair issue while
  the rest loads. Accepted profiles show up in the generic-decoder picker and
  decode through the existing generic path with the existing identity
  formulas. Documented in `docs/device-intelligence.md`.

## 2.10.2 - 2026-10-24

Fixes found on a live HA 2026.10.2 + USB 300 (firmware GATEWAYCTRL 2.15.0.0)
right after installing 2.10.1:

- The gateway device never received the firmware facts read by
  `CO_RD_VERSION`: the `SIGNAL_GATEWAY_UPDATE` dispatcher target was a lambda,
  which Home Assistant runs in the executor, so the device registry refused the
  update ("calls device_registry._async_update_device from a thread") and
  logged an error. The target is now an `@callback`; a new AST test rejects any
  lambda or non-callback dispatcher target, including learn-window handlers.
- Local ESP3 commands (`CO_RD_IDBASE`, `CO_RD_VERSION`, `CO_RD_REPEATER`,
  `CO_WR_REPEATER`) are built with an explicit empty `optional`, so the
  vendored packet class no longer logs "Replacing Packet.optional with default
  value." at WARNING level on every start.

## 2.10.1 - 2026-10-24

First published release of the 2.10 line; it ships everything listed under
2.10.0 below (the `v2.10.0` tag exists but was never released).

- CI: pin the pytest-homeassistant-custom-component job to Home Assistant
  2026.10.2, the exact version `pytest-homeassistant-custom-component`
  0.13.373 requires; the unittest/Ruff/smoke matrix still runs 2026.7.3 and
  2026.10.3. No runtime change.
- Tests: the pytest diagnostics snapshots are committed JSON files instead
  of syrupy `.ambr` (syrupy did not find the committed file on GitHub
  runners). Same pinned shape.
- README: EnOcean Custom is listed in the HACS default store; the badge and
  install steps no longer describe a custom repository.

## 2.10.0 - 2026-10-24

- Move the shared base entity from `device.py` to `entity.py`, register the
  `learn` action in `async_setup` instead of `async_setup_entry`, resolve the
  dongle and inbox only through `ConfigEntry.runtime_data` (drop the
  `hass.data` mirror), and declare `PARALLEL_UPDATES = 0` on every platform.
  No functional change.
- Raise translated `HomeAssistantError`/`ServiceValidationError` (new
  `exceptions` section, EN/FR) for a D2 command without a resolved Base ID, a
  command the dongle refuses, a light without sender identity, a rejected
  teach-in telegram, and a rocker teach-in attempted on a valve profile;
  transport results are still recorded on the entity before the error is
  raised.
- Stop inferring the Ubiwizz UBID1507C profile for every RPS switch row (true
  since 2.7.0, contrary to what the README promised). The config entry gains
  minor version 2 with a one-time, explicit migration: legacy inferred rows
  get the profile written once, where it is visible and editable; everything
  else is carried over untouched. No `unique_id` changes, and a downgrade to
  2.9.x still loads the entry.
- F6-10-00 window handles and D5-00-01 contacts become ENUM sensors with
  frozen option lists (`open`/`tilt`/`closed` and `open`/`closed`), translated
  state labels and per-state icons. The state values themselves do not
  change, so existing automations keep matching; an undocumented handle
  nibble still renders as "unknown", and a stray pre-2.10 "unknown" value is
  dropped on restore instead of being rejected by the new option list.
- Ship three automation blueprints under
  `blueprints/automation/enocean_custom/` (rocker to light, handle left open
  to HVAC off plus notification, smoke alarm to notification and lights),
  each validated against the blueprint and automation schemas with sample
  inputs.
- Document every action, device trigger, bus event, the update model, use
  cases and troubleshooting in the README; declare the minimum Home
  Assistant release in `hacs.json`; run CI against both Home Assistant
  2026.7.3 and 2026.10.3.
- Add a gateway device, populated from `CO_RD_VERSION` and `CO_RD_REPEATER`
  at start-up (manufacturer, model, firmware/hardware version, chip ID as
  serial number); every radio sender device now links to it through
  `via_device`. New disabled-by-default hub sensors report the received
  telegram count, the last telegram and the Base ID, alongside a
  connectivity binary sensor.
- Add per-sender radio diagnostics, disabled by default and restored across
  restarts: signal strength in dBm with range-based icons, last-telegram
  timestamp and repeater-hop count. One set per sender, not per row; existing
  entity identities are unchanged.
- Add a `select` entity on the gateway device for the dongle's own repeater
  (off, level 1, level 2), using only the documented `CO_WR_REPEATER`
  followed by a mandatory `CO_RD_REPEATER` read-back; a refused write or a
  missing read-back is a translated error that leaves the displayed state
  untouched. The separate, still-blocked Ubiwizz module repeater is
  untouched.
- Add opt-in silence detection, configured under Configure → "Availability
  and silent devices" with one timeout per category. A sender that stays
  silent past its timeout has its entities become unavailable; contacts,
  smoke detectors and handles also raise a Repair issue that clears itself on
  the next telegram. Devices can now be deleted from their device page, and
  devices whose rows were all removed are cleaned up at start-up.
- Add downloadable per-device diagnostics (anonymized row shapes, inbox
  facts, the last raw telegram with its sender bytes blanked, entities by
  domain) alongside the existing per-entry diagnostics.
- Add F6-05-02 smoke detector support (alarm plus a diagnostic low-battery
  companion) and a "Declare the profile of an existing device" options-flow
  step to assert an EEP by hand on an existing row, with identity-collision
  checks; the profile is still never inferred from the raw telegram.
- Add a generic, `EEP.xml`-driven decoder (`device_class: eep`) covering 43
  A5 sensor profiles plus D2-14-41, in place of manual-scale guessing for
  CO2, presence, weather-station and similar profiles; selectable from a
  pre-filled 4BS teach-in or a manual assertion. Existing manual-scale rows
  are untouched.
- Add D2-14-30 multi-function smoke detector decoding (alarm, sensor fault,
  maintenance due, temperature/humidity/comfort/air-quality, energy storage,
  remaining lifetime). The byte layout comes from two independent, mutually
  agreeing open-source implementations rather than the vendored `EEP.xml`,
  and is marked "needs hardware validation" throughout the documentation.
- Add passive decoding of SIGNAL telegrams (RORG 0xD0: energy status,
  revision, heartbeat, RX-channel quality, Product ID). Energy status
  becomes a fourth disabled-by-default per-sender diagnostic sensor; the
  other four surface read-only in the per-device diagnostics export. Nothing
  in this integration ever transmits a command that would make a device
  start emitting SIGNAL telegrams on its own.
- Add an `event` entity per rocker (channel_0, channel_1 and both, each with
  pressed, released and long_pressed) alongside the existing device triggers
  and bus event; all three are built from the exact same source of truth, so
  they can never disagree.
- Add a JSON-backed product-recipe catalog (`data/products.json`: NodOn,
  Eltako, HOPPE, Nexelec, Micropelt, Kieback & Peter) as a picker in the
  "declare profile" step. A picked recipe feeds the same validated manual-EEP
  path as free text and is recorded the same way; it is a convenience, never
  proof.
- Add a software-only sender ID pool (Base ID plus offset 1-127) that
  pre-fills a free offset for new lights, climate panels and RPS-emulated
  switches, and refuses a submitted ID already in use by another row.
  `CO_WR_IDBASE` is never written. A new `sender_id` diagnostic attribute
  shows the identity each such entity transmits as.
- Accept `socket://` and `rfc2217://` dongle paths through
  `serial.serial_for_url`, for a dongle shared over the network (ser2net,
  ESPHome stream_server, esp-ha-usb-gateway). A network link now reconnects
  on an unexpected drop with a bounded, growing backoff (2/5/15/30/60 s, 5
  attempts) before giving up until a manual reload; a local device path is
  unaffected and still requires a reload exactly as before.
- Add experimental, YAML-only climate support for the A5-20-01 and A5-20-06
  valve profiles, gated behind an explicit `experimental_valve_ack: true`
  because neither profile has been validated against real hardware in this
  environment. Both profiles poll this integration for a same-second reply,
  so the exchange happens synchronously, inside the radio callback itself.
- Migrate to `has_entity_name`/`translation_key` on switch, light, climate
  and the primary binary_sensor entity, but only where a row's own name
  already equals its device's displayed name, so the text shown today does
  not change; a row sharing a sender with a differently named earlier row
  keeps its explicit name untouched. Displayed names for other, newly
  migrated rows follow Home Assistant's own device/entity naming from here
  on and may read differently after upgrading; `entity_id` and `unique_id`
  never change, and a registry custom name, where the operator set one,
  still takes priority either way.
- Add a `tests/pytest` suite (pytest-homeassistant-custom-component against
  Home Assistant 2026.10.3) covering the config flow and diagnostics through
  the real flow manager and device registry, with measured coverage and
  frozen-time diagnostics snapshots. It is additive to, not a replacement
  for, the existing unittest suite, which keeps running unchanged on every
  supported Home Assistant release.

## 2.9.8 - 2026-10-23

- Drive the Ubiwizz UTE association from the module's own request: the guided
  flow arms a bounded listener, answers only a matching, fresh CMD0 from the
  entered EURID with one directed `D4 91` response, and persists only after
  the physical write and ESP3 `OK`, with the profile the module declared.
  Manual placeholder rows are promoted in place instead of being duplicated.
- Fix the UTE flow concluding "no matching request" after it had already
  answered: the dongle's write and ESP3 callbacks arrive asynchronously and
  are now awaited (bounded by 30 s). A missing ESP3 result is reported as
  `ute_discovery_timeout`.
- Accept the documented request value DB5 `0x02` (the module's channel count)
  in addition to the selected output and `0xFF`; log the received DB5 at
  debug level. The stored row always uses the output chosen by the operator.
- Add a passive, targeted radio test that reports whether one UTE or non-UTE
  telegram from an EURID reached the dongle, without transmitting anything.
- Fix `switch` `turn_on`/`turn_off` writing state from the executor thread when
  the dongle refuses a command; the write is now scheduled on the event loop.
- Re-request a refused or lost dongle Base ID on demand (at most three
  attempts with 5/30/120 s backoff) instead of blocking D2, pairing and UTE
  commands until a reload. Diagnostics expose `base_id_resolved` and
  `base_id_retries`, never the identifier itself.
- Share the entity identity formulas between the platforms and the options
  flow: deleting a UI device now removes every entity it created (energy
  counter, D5-00-01 contact, A5-14-01 contact and supply voltage), and
  collisions are checked against the real registry identities. No existing
  `unique_id` changes.
- Rewrite the README and the Ubiwizz guide to describe the request-driven
  association, and remove the unused `ute.py` encoder.
- Lint every maintained Python file with Ruff (the vendored library is excluded
  through `ruff.toml`) and synchronize the French translations.

## 2.9.7 - 2026-10-11

- Add an evidence-backed commissioning catalog for field-verified Ubiwizz lighting modules.
- Simplify Ubiwizz association: the normal flow no longer asks the operator for an EEP.
- Encode the validated `D2-01-08` directed UTE profile and add regression coverage.
- Keep unknown products out of automatic inference and preserve the expert path.

## 2.9.6 - 2026-10-11

- Ignore the benign late serial-to-Home-Assistant dispatch race when the event
  loop has already closed; parsing and lifecycle counters remain intact.
- Render literal UTE byte placeholders with square brackets in source strings,
  translations, and installation documentation.
- Align the public release marker with the manifest and cite the ESP3 framing
  specification alongside the EEP reference.

## 2.9.0 - 2026-10-11

- Add standards-based UTE discovery and association for Ubiwizz UBID1507C/D2-01-12.
- Replace the passive D4/A0 listener with an explicit post-PRESS, gateway-initiated directed D4/91 UTE telegram bound to the entered EURID and resolved dongle Base ID; persist only after its ESP3 callback.
- Harden dongle Base ID handling, ESP3 callbacks, session ownership, collision checks, and stale callback rejection.
- Validate climate numeric inputs at both persistence and runtime boundaries.
- Remove unsupported Ubiwizz repeater radio writes and stale repeater telemetry; diagnostics remain no-radio.
- Add full regression coverage and HA smoke validation.

## 2.8.1 - 2026-10-11
- Harden HA on/off command delivery and separate transport ACKs from actuator feedback.
- Add regression coverage for directed D2 commands, stale callbacks, RPS multi-frame delivery, and Ubiwizz repeater telemetry.
- Professionalize the README and consolidate Ubiwizz/device-intelligence documentation.

## 2.8.0 - 2026-10-11

- Expose repeater level, transmission count, and ESP3 acknowledgement telemetry on each Ubiwizz switch.

## 2.7.8 - 2026-10-10

- Log ESP3 response acceptance for repeater transmissions.

## 2.7.7 - 2026-10-10

- Emit repeater transmission evidence at warning level for live verification.

## 2.7.6 - 2026-10-10

- Use a synchronous, warning-level service callback for deterministic HA dispatch diagnostics.

## 2.7.5 - 2026-10-10

- Add unambiguous `enocean_custom.repeater_set_level` service for live repeater control.

## 2.7.4 - 2026-10-10

- Normalize and trace repeater service entity targets for live HA diagnosis.

## 2.7.3 - 2026-10-10

- Resolve target entities at service-call time after Home Assistant assigns entity IDs.

## 2.7.2 - 2026-10-10

- Replace the non-targetable entity-service registration with an explicit targetable HA service schema.

## 2.7.1 - 2026-10-10

- Add explicit MSC repeater transmission logging for live transport verification.

## 2.7.0 - 2026-10-10

- Register the repeater service during platform setup so Home Assistant exposes it reliably.
- Infer the Ubiwizz UBID1507C profile for configured RPS actuator rows while preserving explicit profiles and NodOn default rows.

## 2.6.7 - 2026-10-10

- Add Home Assistant entity service `enocean_custom.configure_repeater` for Ubiwizz UBID1507C/D2-01-12.
- Send the published MSC repeater payloads for off, level 1, and level 2.

## 2.6.6 - 2026-10-10

- Add installation-driven Ubiwizz UBID1507C/D2-01-12 two-channel support.
- Add HOPPE/Ubiwizz F6-10-00 handle states and first-class D5-00-01 contacts.
- Keep D2 state feedback-only and repeater writes blocked until documented.

## 2.6.3 - 2026-10-03

- Keep privacy-safe diagnostics available when a transmit queue backend does not expose `qsize()`.
- Report an unavailable queue depth as `null` and log the condition at debug level.

## 2.6.0 - 2026-10-03

- Add D5-00-01 / 1BS binary sensor decoding: `0x08` open and `0x09` closed.
- Ignore D5 teach-in frames (`0x00`/`0x01`) so they cannot create false state changes.
- Include the D2 commissioning identity fix from PR #4.

## 2.5.0 - 2026-08-31

- Draft a safety-gated Ubiwizz commissioning flow using directed D2 commands
  whose ERP1 sender is the resolved dongle Base ID, causal ESP3-OK-plus-matching
  channel/OV=100 feedback proof, delayed one-time persistence, and an
  identity-preserving path for existing default switches, restricted fail-closed
  to exact radio-declared D2-01-12 evidence bound to the same sender on channels
  0/1 (an EEP is not a product identity). D2 channels are schema-bounded to
  0–31. Direct QR remains radio-silent and the unsafe guided
  RPS variant is no longer offered.
- Release gate: validate a factory-reset UBID1507C on both physical channels,
  including ESP3 OK, directed D2 ON, and matching feedback; power-cycle the
  module; repeat directed control and matching feedback; verify preserved
  entity identity; and finish with an all-green CI run. No persistent hardware
  commissioning success is claimed yet.
- Add optional `which` and `onoff` capabilities to native EnOcean device
  triggers, allowing an exact conversion of historical `button_pressed` event
  filters while preserving the broad v2.4.0 behavior when they are omitted.
- Reject optional channel filters that contradict a fixed channel trigger type.

## 2.2.0 - 2026-08-05

- Add a strictly passive, entry-owned in-memory radio inbox with a 64-entry
  unknown-sender LRU, UTC last-seen, packet count, RSSI, repeater count and
  observed RORGs. The options radio card shows available signal facts; unload
  clears the registry and diagnostics expose only an aggregate sender count.
- Add deterministic local DDF import (`scripts/import_ddf.py`) and the versioned
  `data/ddf_catalog.json`. Runtime merges by exact Product ID without network
  access or forgeable identity strings.
- Correct Cositherm DDF evidence per exact variant: non-GW products transmit GP
  `B0-00-00` and receive eleven A5-10 profiles; GW products transmit/receive
  `D2-34-10`. No D2-34-10 decoder is invented without a public bit-level spec.
- Add a short sourced manufacturer-ID registry. An observed ID that is absent
  from it renders as the stable `not_registered` token, while an ID that was
  never observed stays `—`; neither is ever guessed from adjacent evidence.
- Support A5-14-01 contact and supply voltage decoding. The contact is a door
  binary sensor; voltage is a disabled-by-default diagnostic entity and EEP
  reserved error codes remain unknown. A5-10 is not broadened without proven
  real-frame tests.

## 2.1.0 - 2026-08-05

- Disable automatic UTE acknowledgement outside an explicitly opened session.
  The vendored communicator used to answer `TEACHIN_ACCEPTED` to every UTE
  request it received, and the serial worker was started in that mode, so a
  neighbouring device could pair itself with the dongle without any operator
  action. The worker is now started with acknowledgement off, the vendored
  default is off, and a teach-in deferred while the Base ID is resolved is
  dropped if the session closes meanwhile. A UTE telegram is still parsed and
  dispatched, so the v2.1 passive UTE/EEP extraction is unchanged; only the
  unsolicited radio answer is removed. Pairing stays operator-driven through
  the guided flows and the existing teach-in services.
- Add local Device Intelligence for exact UTE and enriched 4BS teach-in
  identities, strict Alliance QR Product ID parsing, and safe bounded
  `radio_metadata` persistence. Security containers and raw QR data are never
  retained. No network lookup, telemetry, automatic UTE acknowledgement or new
  runtime dependency is introduced.
- Keep RPS F6 and 1BS D5 profiles unknown unless a separate declaration or
  manual choice supplies the EEP. Separate evidence, configuration mode and
  implementation support; unknown catalog entries remain unknown.
- Reject rather than silently repair malformed commissioning input: a 48-bit
  EURID and a Product ID whose reserved manufacturer bits are set are refused
  instead of being truncated or masked to 11 bits.
- Add an optional manual EEP field to the device form, offered only when
  neither a radio telegram nor a Product ID has declared a profile. It accepts
  the strict `XX-XX-XX` form (lowercase normalized to uppercase), is recorded
  as `evidence=manual` / `eep_source=manual`, never pre-selects a platform, and
  can never overwrite a declared profile — including through a forged
  submission. Existing safe QR fields are preserved.
- Add a translated radio card and unambiguous platform suggestions to the
  options flow, with manufacturer-conflict handling and fully changeable
  selections. Each field is a separate placeholder, so French and English
  descriptions are genuinely translated rather than sharing one English string.
- Group entities by stable sender identifiers in the Home Assistant Device
  Registry, including historical YAML devices. `manufacturer`, `model` and
  `model_id` come only from an exactly cataloged Product ID resolved at
  runtime; they are never persisted, and a declared EEP alone never becomes a
  model.
- Ship a small Product ID catalog sourced from the official EnOcean Alliance
  DDF repository (Afriso Cositherm 2/6-Channel, BSC Computer eTronic). These
  devices are identified but remain `unsupported`: no exact decoder exists here.
- Keep device, product, radio and UI-device identities out of diagnostics;
  document the local implementation matrix, sources and privacy boundaries in
  `docs/device-intelligence.md`.

## 2.0.1 - 2026-07-29

- Ship the official EnOcean brand images (`brand/` directory, HA 2026.3+
  mechanism) replacing the placeholder icon.
- No functional change.

## 2.0.0 - 2026-07-29

- Rebuild the integration on the Apache-2.0 Home Assistant Core 2026.7.3
  EnOcean component and remove the former unlicensed implementation lineage.
- Preserve the `enocean_custom` domain, YAML schemas, UI-managed devices,
  unique IDs, serial lifecycle guarantees, teach-in flows, D2 feedback, and
  transactional actuator state.
- Independently implement F6-02-01 RPS commands, A5-38-08 dimmer commands and
  teach-in, D2-01 power/energy measurements, and A5-20-04 valve control and
  feedback.
- Add the Apache-2.0 `LICENSE`, third-party `NOTICE`, core `icons.json`, and
  current hassfest manifest metadata.
- Add Home Assistant USB discovery while keeping safe path probing,
  reconfiguration through serial aliases, and diagnostic/error redaction.

## 1.5.0 - 2026-07-29

- Add a guided actuator-pairing wizard to the config-entry options UI. It
  creates supported receivers through the existing `ui_devices` persistence
  and reload path, then drives their existing entity services during a bounded
  120-second pairing window.
- Pair Ubiwizz-style RPS relays by toggling the created switch about every four
  seconds and confirm success only after a valid D2-01 status telegram from
  the actuator. Timeout handling offers retry, keep, or protected removal.
- Send the existing A5-38-08 teach-in service three times for Eltako-style 4BS
  dimmers. Because that profile provides no confirmation response, the wizard
  explicitly requires physical verification instead of claiming radio
  success.
- Cancel pairing tasks and dispatcher listeners when the options flow is
  abandoned, and stop further commands immediately after success or timeout.
  The deadline also bounds each individual service call; a command already
  running inside Home Assistant's executor may still complete once (it cannot
  be killed), but no further command is ever issued.
- Adversarial-review hardening: relay success requires the D2-01 status to
  match the configured channel; a concurrently deleted device can no longer
  reach the success screen; `send_teach_in` raises when the dongle rejects
  the telegram locally, so rejected attempts never count as sent.

## 1.4.0 - 2026-07-29

- Add bidirectional EEP D2-01-12 status feedback for Ubiwizz UBID1507C
  actuators. Parsed VLD Actuator Status Response telegrams now synchronize
  matching default switch channels and light on/off/brightness state, including
  local wall-switch changes.
- Expose the latest D2 channel, output value, power-failure flags, and timestamp
  as entity state attributes while ignoring malformed or unknown-sender
  telegrams. RPS switches consume it too: a physical wall-switch toggle on a
  directly-paired module now syncs HA state (channel-filtered).
- Add EnOcean Alliance product-label QR commissioning with a strict
  `AA:BB:CC:DD` fallback and rejection of unsupported 48-bit EURIDs.
- Extend config-entry options management to `climate` and `sensor`, preserving
  the YAML field defaults, bounds, and unique-ID formulas.
- Add the light-targeted `enocean_custom.send_teach_in` service for Eltako 4BS
  dimmers using EEP A5-38-08; calling it on a light without a `sender_id`
  fails with a clean error instead of a `TypeError`.
- Add an optional `channel` (0-31) to lights (YAML and UI): D2-01-12
  multi-gang actuators report every channel, and only the configured one now
  drives the entity (review finding: the last received telegram used to win
  regardless of channel).
- Refuse UI deletion when the same EnOcean ID is also managed by YAML,
  including send-only YAML lights whose identity is their `sender_id`.
- Strict commissioning-code parsing: the typed fallback accepts only exact
  `AA:BB:CC:DD`, and product labels must be clean MH10.8.2 containers
  (malformed input can no longer smuggle an ID into the device form).
- On platform-forwarding failure the dongle reference is dropped from
  `hass.data`/`runtime_data`, so a retry builds a fresh dongle instead of
  reusing the unloaded one. (A single platform failing during forwarding is
  absorbed by Home Assistant itself and does not fail the whole entry; that
  is core behavior, unchanged.)

## 1.3.0 - 2026-07-29

- Add a config-entry options flow to add and remove `binary_sensor`, `switch`,
  and `light` EnOcean devices entirely from the UI, without editing
  `configuration.yaml`. YAML-configured devices keep working unchanged and
  cohabit with UI-managed ones on the same platform.
- Add a teach-in ("learn") mode: start a bounded listening window (default 60
  seconds, configurable 15-300), press the physical device's button, and the
  first EnOcean sender not already known (from YAML or the UI) is captured and
  offered in a device form (platform, name, and platform-specific fields,
  including `switch_type` `default`/`RPS` with the RPS channel 0/1 rule).
- Add the `enocean_custom.learn` service, which starts the same listening
  window and fires `enocean_custom_device_learned` with the captured ID once
  an unknown sender is heard.
- Compute UI device `unique_id`s with the exact same formula as the matching
  YAML platform, so a future YAML-to-UI migration preserves `entity_id` and
  automations. Adding a device whose identity already exists in the entity
  registry is refused with an explicit error; teach-in never merges or
  overwrites an existing device.
- Add a "Manage UI devices" screen to remove a UI-managed device, which
  deletes both its config-entry option entry and its exact entity registry
  row (matched by domain, platform, and unique_id), never a row belonging to
  another platform.
- Forward `binary_sensor`, `switch`, and `light` config-entry platform setup
  (`climate` and `sensor` remain YAML-only) and reload the entry automatically
  when UI devices are added or removed. If platform forwarding fails, the
  serial dongle is unloaded again instead of leaving a live reader behind.
- Harden teach-in after adversarial review: device forms use only
  websocket-serializable selectors (number/select/text); learn windows are
  serialized and owned (a concurrent `learn` service call is refused loudly,
  and a discarded UI flow can no longer kill a service-owned window); captures
  are timestamped so a stale capture is never adopted by a later flow;
  deleting a UI device frees its EnOcean ID immediately (known ids are
  re-seeded from the entry options on every learn window); YAML `sensor` and
  `climate` devices now also register their sender ids so teach-in never
  offers them; malformed hand-edited `ui_devices` options rows are skipped
  instead of crashing platform setup.
- Add a French translation (`translations/fr.json`) alongside the existing
  English one, kept in sync with `strings.json` by an automated key-structure
  check.

## 1.2.4 - 2026-07-28

- Stop and join the serial communicator outside Home Assistant's event loop.
- Abort config-entry reload if the old serial reader remains alive.
- Guarantee serial descriptor closure when the worker exits.
- Bound serial writes and actively cancel pending reads/writes during shutdown.
- Stop accepting or draining queued writes as soon as shutdown begins.
- Drop malformed outbound packets without terminating the serial worker.
- Close temporary serial descriptors opened by config-flow validation.
- Open the USB communicator in Home Assistant's executor.
- Remove the unsafe raw `send_packet` service.
- Declare all runtime dependencies and enforce a single config entry.
- Cancel periodic climate control callbacks when an entity is removed.
- Always transmit climate switch-off even when the temperature sensor is unavailable.
- Reject non-finite and physically out-of-range climate control parameters.
- Remove the accidental dependency on the native `enocean` integration.
- Add deterministic lifecycle tests plus HACS and hassfest CI.
- Propagate serial-worker availability to every EnOcean entity.
- Add redacted config-entry diagnostics with RX/TX and lifecycle counters.
- Create a native Home Assistant Repair issue after unexpected serial exit.
- Add a reconfigure flow for changing the USB serial path safely.
- Reject invalid IDs, sensor types, RPS channels, and degenerate sensor ranges.
- Drop truncated platform telegrams at the entity boundary.
- Prevent false optimistic switch/light state after a rejected transmission.
- Correct light brightness mapping at the 100%/255 boundary.
- Give send-only lights stable, non-colliding sender-based unique IDs and migrate
  the historical `0` identity while preserving its entity ID and automations.
- Add pinned Ruff and runtime dependency-audit jobs to CI.
- Reject non-finite integer climate ranges with `vol.Invalid`, including YAML
  `.inf` and `-.inf`.
- Send complete seven-byte ESP3 optional data with outbound ERP1 frames.
- Correlate ESP3 responses in serial-write order and apply switch/light state
  only after dongle `OK`, including both packets of RPS press/release sequences.
- Keep exactly one ESP3 command in flight; if its response is missing, stop the
  transport before any later command can inherit the stale response slot.
- Initialize actuator state as unknown and mark optimistic states as assumed.
- Clear switch state when power feedback drops below the defined threshold.
- Preserve fresh temperature attributes during asynchronous state restoration.
- Preserve legacy binary-sensor IDs to avoid collapsing multiple registry
  entries that share a sender but use different device classes.
- Harden GitHub checkout credentials and pass a zero-finding `zizmor` audit.
- Reconfigure aliases of the active serial device without opening a competing
  validation descriptor.
- Redact hardware serial identifiers from Repairs, diagnostics, thread names,
  exceptions, and runtime packet logs.
- Guarantee parser progress after a malformed inbound ESP3 frame instead of
  retrying the same poisoned buffer forever.
- Request and cache the dongle Base ID asynchronously so UTE handling neither
  blocks the reader nor consumes the following valid ESP3 frame.
- Retry a refused Base-ID request once for a pending UTE transaction and release
  the retry latch after refusal, timeout, or shutdown.
- Serialize climate user, sensor, and periodic transactions until all expected
  ESP3 responses arrive, preserving the last committed visible state on failure.
- Validate the ESP3 header CRC before trusting its declared payload length so a
  corrupt header cannot strand the following valid frame.
- Preserve the historical sender-based light unique ID when a receiver ID is
  configured, avoiding duplicate registry entities after upgrade.
- Pin the HACS and Hassfest containers by digest, use Node-24 checkout v5,
  validate formatting in CI, and verify the existing local brand icon for HACS.

## 1.2.3 - 2026-07-28

- Initial bounded `stop()` and `join()` fix for config-entry unload.

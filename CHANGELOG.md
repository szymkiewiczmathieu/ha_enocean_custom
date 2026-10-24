# Changelog

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

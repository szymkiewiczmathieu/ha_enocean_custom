# Changelog

## 2.4.1 - Unreleased

- Add optional `which` and `onoff` capabilities to native EnOcean device
  triggers, allowing an exact conversion of historical `button_pressed` event
  filters while preserving the broad v2.4.0 behavior when they are omitted.
- Reject optional channel filters that contradict a fixed channel trigger type.

## 2.4.0 - Unreleased

- Add native Home Assistant device triggers for EnOcean F6/RPS rocker presses,
  releases, and channel 1/2 presses. Generated automations reference the device
  registry entry and therefore appear in the device page's "Used by" view.
- Exclude A5-14-01 contacts, identified by their own unique ID, because they
  decode 4BS telegrams and never emit rocker events. Rockers keep their device
  triggers whatever `device_class` they were configured with.

## 2.3.0 - Unreleased

- Add an explicit options-flow import for legacy YAML `binary_sensor` and
  `switch` devices. It preserves their exact unique IDs, skips existing UI
  identities, reports invalid and non-importable YAML rows, and never invents
  radio metadata.
- Keep the YAML inventory memory-only and clear it on integration unload. The
  confirmation and result screens document the safe order: import, remove the
  imported YAML blocks, restart, then verify entity IDs and automations.
- Add English and French options-flow translations plus unit coverage for
  identity parity, defaults, confirmation, visibility, duplicates, invalid
  rows, restart absence, and inventory cleanup.

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

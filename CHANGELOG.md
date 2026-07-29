# Changelog

## Unreleased

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

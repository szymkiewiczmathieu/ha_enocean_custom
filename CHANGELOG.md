# Changelog

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

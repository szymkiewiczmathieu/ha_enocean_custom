# EnOcean serial lifecycle hardening and UI teach-in

## v1.3.0 — UI teach-in for binary_sensor/switch/light

`v1.3.0` adds a config-entry options flow to add and remove devices from the
UI, on top of the `v1.2.4` serial lifecycle hardening below. Read this
honestly before relying on it:

- **In scope:** `binary_sensor`, `switch`, and `light` only. A device is
  captured by listening for the first EnOcean sender not already known (from
  YAML or the UI), then filled in through a short device form.
- **Out of scope:** `climate` and `sensor` remain YAML-only; they are not
  offered by the options flow and are not affected by this feature.
- **YAML is still fully supported.** Nothing in `configuration.yaml` needs to
  change, and YAML-configured devices cohabit on the same platform as
  UI-managed ones. There is no YAML-to-UI migration in this release.
- **One capture per physical device identity.** "Unknown" means absent from
  the combined set of YAML and UI device IDs, regardless of platform. Once any
  entity (YAML or UI, on any of the five platforms) exists for a given
  EnOcean ID, teach-in will never offer that ID again. A second entity for an
  already-known multi-profile device (e.g. an actuator that is both a
  `binary_sensor` and a `switch`) still requires YAML.
- **No merging, ever.** If the computed `unique_id` already exists in the
  entity registry, the add form is refused with an explicit error. Deleting a
  UI device removes only the exact registry row matching its own domain,
  platform, and `unique_id`.
- Options changes reload the config entry automatically; this only forwards
  `binary_sensor`, `switch`, and `light` platform setup, not `climate` or
  `sensor`.

## v1.2.4 — EnOcean serial lifecycle hardening

Version `v1.2.4` hardens the serial lifecycle on top of upstream `v1.2.2`:

- stop the `SerialCommunicator` during config-entry unload;
- interrupt pending serial reads/writes, then wait up to one second for the
  worker to exit and close the USB descriptor;
- stop accepting and draining queued writes when shutdown starts;
- warn and abort reload if the previous reader remains alive;
- close config-flow probe descriptors;
- remove the unsafe raw packet service;
- declare the vendored library's runtime dependencies and MIT notice;
- cancel climate control timers when their entity is removed;
- guarantee a physical switch-off command even without a sensor value;
- reject NaN, infinities, and out-of-range climate control parameters;
- remove the accidental dependency on the native `enocean` integration;
- expose serial health through entity availability, redacted diagnostics, and
  a Home Assistant Repair issue;
- support safe serial-path reconfiguration from the config entry;
- validate IDs and platform-specific ranges before creating entities;
- ignore truncated telegrams and refuse false optimistic state after TX reject;
- emit complete ESP3 ERP1 optional data, keep one command physically in flight,
  stop the transport on response timeout, and reject switch/light state
  transitions when the dongle returns an error;
- serialize climate user, sensor and periodic transactions until their complete
  ESP3 response sets resolve;
- represent unconfirmed actuator state as unknown/assumed instead of false OFF;
- preserve legacy binary-sensor identities instead of performing an ambiguous
  many-to-one registry migration;
- recognize serial aliases without briefly creating a competing second reader;
- redact both the configured path and hardware serial identifier from
  diagnostics, exceptions, thread names and runtime packet logs;
- validate the header CRC before trusting its payload length, discard one byte
  only when malformed-frame parsing consumed nothing, and cache/retry Base ID
  asynchronously so UTE handling preserves the following frame.

This prevents a Home Assistant config-entry reload from creating two readers
for the same EnOcean ESP3 serial stream. The only intentional API removal is
`enocean_custom.send_packet`; normal entities use the integration's private,
bounded dongle client and are unaffected.

## Live validation gate

Do not label this candidate production-ready until the real dongle passes all
of the following in sequence:

1. start with exactly one `EnOceanSerialCommunicator` thread;
2. receive fresh telegrams and confirm the RX counter advances;
3. perform at least five config-entry unload/reload cycles;
4. after every unload, verify the previous thread is dead and the port closed;
5. verify there is no `multiple access on port` message;
6. disconnect the dongle once and confirm entities become unavailable, a Repair
   issue is created, and diagnostics contain the terminal reason;
7. reconnect, reload once, and confirm one reader plus resumed telegrams;
8. test climate `off` with its temperature sensor unavailable;
9. send at least one light, RPS, D2, and climate command and verify diagnostics
   report ESP3 `OK` rather than `WRONG_PARAM` or `OPERATION_DENIED`;
10. keep any existing deployment-level Core-restart watchdog fallback (outside
    this repository/integration) until these cycles remain stable over several
    days; do not switch it automatically to targeted reload.
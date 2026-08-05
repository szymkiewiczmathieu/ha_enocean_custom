# EnOcean Custom

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![Validate](https://github.com/szymkiewiczmathieu/ha_enocean_custom/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/szymkiewiczmathieu/ha_enocean_custom/actions/workflows/validate.yml)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7.3-18BCF2.svg)](https://www.home-assistant.io/)
[![Python](https://img.shields.io/badge/Python-3.14-3776AB.svg)](https://www.python.org/)

Apache-2.0 Home Assistant custom integration for EnOcean devices, rebuilt on
the official Home Assistant Core 2026.7.3 EnOcean component. The integration
keeps the established `enocean_custom` domain and configuration formats while
using its hardened, entry-owned serial transport.

Version 2.0.0 stops and joins the USB reader thread before a config-entry reload
and closes probe descriptors deterministically. Compatibility is confirmed only
after the live test procedure described in [PATCH_NOTES.md](PATCH_NOTES.md).

## Why this integration is different

`v1.2.4` treats the USB dongle as a single-owner runtime resource rather than
just another background thread:

- shutdown cancels blocked reads and writes, abandons queued transmissions and
  refuses new packets as soon as stopping begins;
- Home Assistant will refuse an unload/reload while the old reader survives;
- all EnOcean entities become unavailable when the serial worker exits;
- an unexpected worker exit creates a native Home Assistant Repair issue;
- config-entry diagnostics distinguish locally queued transmissions from ESP3
  `OK`/error responses, expose thread health and queue depth, and redact the
  configured device path;
- the serial path can be changed through **Reconfigure**, without deleting the
  config entry;
- every configured EnOcean ID is exactly four bytes and all protocol-specific
  ranges are validated before an entity is created;
- malformed inbound telegrams and rejected outbound commands cannot kill the
  serial worker or create a false optimistic state;
- ESP3 responses are correlated in serial-write order, so switch/light state is
  applied only after the dongle returns `OK`; RPS press/release requires two
  successful responses;
- outbound ERP1 frames carry the seven ESP3 optional bytes required by the
  protocol. Light and switch states remain marked as assumed because ESP3 `OK`
  confirms dongle acceptance, not remote execution; incoming telegrams can
  still update their displayed state.

These are software guarantees covered by tests. They do **not** replace the
live dongle validation checklist in [PATCH_NOTES.md](PATCH_NOTES.md).

## Background

Version 2.0.0 replaces the former implementation with an Apache-2.0 codebase
using Home Assistant Core 2026.7.3 as its authorized base. Project-specific
features were ported or independently reimplemented against the EnOcean EEP
specification. The official Home Assistant EnOcean integration remains active
and follows a different protocol-library path; this repository is an
independent custom integration, not its replacement or an official Home
Assistant project.

## Installation

1. [Install HACS](https://hacs.xyz/docs/setup/download/)
2. Open HACS in your Home Assistant installation
3. Add `https://github.com/szymkiewiczmathieu/ha_enocean_custom` as a HACS [custom repository](https://hacs.xyz/docs/faq/custom_repositories): `Integrations > Three Dots > Custom repositories > Integration`
4. Install `EnOcean Custom`

Do not configure the native `enocean` integration and `enocean_custom` against
the same serial device. A dongle must have exactly one reader.

### Reconfigure and diagnostics

Open **Settings > Devices & services > EnOcean Custom** and use:

- **Reconfigure** to move from an unstable `/dev/ttyUSB*` path to a persistent
  `/dev/serial/by-id/*` path;
- **Download diagnostics** to capture serial lifecycle evidence without sharing
  the full configured device path. `transmit_queued` means the local worker
  accepted a packet; `last_response_code=OK` means the dongle accepted the ESP3
  command. Neither alone proves that the remote actuator executed it.

If the worker stops unexpectedly, Home Assistant raises a Repair issue and all
EnOcean entities become unavailable. Do not blindly start another reader.
Resolve the USB/ownership problem first, then reload the config entry. If the
old thread does not stop within the bounded join, the reload intentionally
fails instead of opening the port a second time.

## Description

This custom integration contains Apache-2.0 Home Assistant-derived platform code
and a vendored MIT-licensed snapshot of the
[`kipe/enocean`](https://github.com/kipe/enocean) library. To use it, specify
`- platform: enocean_custom` instead of `- platform: enocean` when defining an
EnOcean entity in `configuration.yaml`.

The repository is licensed under Apache-2.0; third-party attributions are in
[NOTICE](NOTICE), and the vendored library retains its own
[MIT license](custom_components/enocean_custom/enocean_library/LICENSE).

### Adding devices from the UI (teach-in)

#### Device Intelligence (v2.1)

Device Intelligence identifies devices locally: no network lookup, no
telemetry, no automatic UTE acknowledgement, and no new runtime dependency.

- A UTE or enriched 4BS teach-in declares an exact EEP. An ordinary RPS `F6` or
  1BS `D5` telegram does not, and stays `profile_unknown` rather than guessed.
- An EURID identifies a radio, an EEP identifies a data profile. Neither
  identifies or certifies a product, so a declared EEP alone never becomes a
  Device Registry model.
- A standardized QR label yields a manufacturer and a Product ID. Only an
  exactly cataloged Product ID produces a manufacturer/model, and a conflict
  with the radio teach-in suppresses every model claim and pre-selection.
- The scanned payload and its `10Z`/`11Z`/`13Z` security containers are never
  persisted, logged or exported. Diagnostics carry aggregates only.
- Entities of one sender are grouped in the Home Assistant Device Registry.
  Historical YAML configuration and older `ui_devices` rows stay valid.

The evidence/support glossary, the full implementation matrix and the bundled
Product ID catalog with its sources are in
[docs/device-intelligence.md](docs/device-intelligence.md).

Since `v1.3.0`, devices can also be
added and removed entirely from **Settings > Devices & services > EnOcean
Custom > Configure**, without editing `configuration.yaml`. YAML configuration
keeps working unchanged and cohabits with UI-managed devices on the same
platform; there is no automatic YAML-to-UI migration.

1. Open **Configure > Add a device**.
2. Put the physical device into learning mode and press its button. The
   integration listens for up to 60 seconds (configurable 15-300 seconds via
   the `enocean_custom.learn` service's `timeout` field) and captures the
   first EnOcean sender it does not already know about, from either YAML or
   the UI.
3. Fill in the form: platform (`binary_sensor`, `switch`, `light`, `sensor`, or
   `climate`),
   name, and platform-specific fields — `device_class` (optional) for
   `binary_sensor`, `channel` (default `0`) and `switch_type` (`default` or
   `RPS`, with RPS restricted to channels 0/1) for `switch`, or `sender_id`
   (required for `light`, typed as four hex bytes like `05:9F:89:34` — the
   virtual ID used for outbound commands) for `light`. The `sensor` and
   `climate` detail forms expose the same fields, bounds, and defaults as their
   YAML platform schemas.
4. The entity is created immediately with the same `unique_id` a matching
   YAML definition would produce.

Alternatively, choose **Configure > Add via QR code** and paste the decoded
text from an [EnOcean Alliance standardized product label](https://www.enocean-alliance.org/wp-content/uploads/2021/05/ProductIDandStandardizedLabelingSpecification-V1.8.pdf). The integration
requires the mandatory `30S` EURID and `1P` Product ID containers and supports
32-bit EURIDs encoded as `30S0000AABBCCDD`. Native 48-bit EURIDs are rejected
because this integration only supports four-byte radio IDs. You can also enter
the fallback form `AA:BB:CC:DD`. A valid value opens the same device form with
the extracted ID pre-filled; invalid input never creates an options row.

Use **Configure > Manage UI devices** to remove a device you added from the
UI; this deletes both its config-entry option entry and its exact entity
registry row. If the same radio ID is also configured in YAML, removal is
refused until the YAML configuration is removed.

Two things worth knowing:

- Only one teach-in window can be open at a time, whoever started it (the UI
  flow or the `enocean_custom.learn` service): a second start is refused until
  the first window closes. Deleting a UI device makes its EnOcean ID teachable
  again immediately, without restarting Home Assistant.
- "Unknown" means the captured ID is absent from every configured device
  (YAML or UI), not just the platform you're currently adding. Once any
  entity exists for a given EnOcean ID, teach-in will not offer that ID again
  — a second entity for an already-known multi-profile device still needs
  YAML. If the device's identity already exists in the entity registry, the
  form is refused with an explicit error: teach-in never merges or overwrites
  an existing device.

### Guided actuator pairing

Since `v1.5.0`, **Settings > Devices & services > EnOcean Custom > Configure >
Pair an actuator (guided)** pairs supported receivers without DolphinView.

1. Scan or paste the actuator's EnOcean Alliance commissioning label. You can
   instead type its four-byte radio ID exactly as `AA:BB:CC:DD`.
2. Give it a name and select its family:
   - **Relay (RPS, e.g. Ubiwizz)** creates an RPS `switch`; select channel `0`
     or `1`.
   - **4BS dimmer (e.g. Eltako)** creates a `light`; enter the required
     four-byte outbound `sender_id` and optionally select channel `0`–`31`.
3. Put the receiver into pairing mode. For a Ubiwizz UBID1507C, briefly press
   **PRESS** three times and check that its LED flashes, then continue.
4. During the 120-second window, the relay wizard resolves the new entity by
   its registry `unique_id` and calls its normal `switch.toggle` service about
   every four seconds. This reuses the entity's RPS press/release transaction
   and its dongle response handling. Pairing is confirmed only when a valid
   D2-01 actuator status arrives from the module's own radio ID.
5. For an Eltako dimmer, the wizard calls the existing
   `enocean_custom.send_teach_in` entity service three times, about five
   seconds apart. A5-38-08 provides no confirmation telegram: the final screen
   therefore asks you to verify physically that the dimmer responds.

The device is saved in the existing `ui_devices` config-entry option before
the pairing loop begins, and the normal options update listener reloads the
integration to create its entity. If the 120-second relay window expires, you
can retry, keep the saved device without pairing, or remove it through the
same protected UI-device removal path. Closing the flow cancels the loop and
its D2 listener, so it cannot keep transmitting in the background.

### Binary sensors

Binary sensors do not only trigger events but also have a state variable which may be `On` or `Off`. The state attributes `Onoff` and `Which` have been added to identify which pushbutton is being pressed. The state attribute `Repeated telegram` indicates if the received telegram was received by an EnOcean repeater.

### Support for shutter contacts

Add support for shutter contacts with EnOcean Equipment Profile EEP: D5-00-01. The sensor state can be `Open` or `Closed`.

### Power and energy sensors

`device_class: powersensor` exposes separate power and energy entities. It
continues to decode A5-12-01 meters and also accepts D2-01-0B measurement
responses (`W`/`kW` for power and `Ws`/`Wh`/`kWh` normalized to `Wh` for
energy). The configured device-class key remains part of the power entity's
unique ID; the companion energy entity uses an `-energy` suffix.

### Switches

Switches can be used to emulate physical pushbuttons to control actors for light etc. This way you can send commands from Home Assistant to your EnOcean devices. Each switch needs its own unique EnOcean identifier (ID). The IDs can not be set randomly but depend on the base ID of your EnOcean dongle, see [this community thread](https://community.home-assistant.io/t/enocean-switch/1958/36) for more information.
To emulate double rocker push buttons, the keywords `switch_type` and `channel` are being used. The definition of a switch may look like this:

```yaml
switch:
  - platform: enocean_custom
    name: switch_livingroom
    switch_type: RPS    # emulate double rocker push button
    channel: 0          # 0 for left rocker, 1 for right rocker
    id: [0xFF, 0xD9, 0x04, 0x81]
```

To teach-in the switch to your EnOcean device, put the device in learning mode and toggle the state of the switch entity in Home Assistant.

### D2-01-12 actuator feedback

Ubiwizz UBID1507C two-channel actuators report their actual output state with
EEP D2-01-12 VLD telegrams. A default (non-RPS) switch whose `id` matches the
actuator sender follows feedback for its configured `channel`. A receive-capable
light whose `id` matches the actuator maps the reported 0–100% output value to
Home Assistant brightness 0–255. This includes changes initiated by a wall
switch paired directly with the actuator.

After the first valid status, the entity exposes `d2_channel`,
`d2_output_value`, the power-failure capability/state flags, and `last_status`.
Malformed feedback and telegrams from other sender IDs are ignored. RPS switch
behavior is unchanged.

### Climate device

The climate platform supports the historical Thermokon `SRC-D08` controller
and the bidirectional `A5-20-04` radiator-valve profile. Both use the bounded PI
controller and transactional ESP3 acknowledgements. `A5-20-04` additionally
reports valve position, room temperature, local setpoint, and failure code.
Currently supported HVAC modes are `off` and `heat` with preset modes `comfort`,
`sleep`, `away` and `boost`.

Configuration variables:

- `device_type`: `"SRC-D08"` or `"A5-20-04"`. The A5-20-04 profile is
  currently configured through YAML; its `id` is the addressed valve and
  `id_switch` is the controller sender identity.
- `name`: entity name
- `id`: EnOcean ID to send temperature set point commands to the heating controller. Must fit to your [dongle's base ID](https://community.home-assistant.io/t/enocean-switch/1958/36). Commands replicate EnOcean room operating panel telegrams and use EEP A5-10-06 format.
- `id_switch`: EnOcean ID to send digital switch commands to the heating controller. Must fit to your [dongle's base ID](https://community.home-assistant.io/t/enocean-switch/1958/36).
- `sensor_entity_id`: Entity ID of the temperature sensor. `SRC-D08` expects an
  [EnOcean temperature sensor](https://www.home-assistant.io/integrations/enocean/#temperature-sensor),
  or another entity providing `SlideSwitch` and `SetPoint`. `A5-20-04` only
  requires a finite temperature state. For `SRC-D08`:
  - `SlideSwitch`: Set to preset mode comfort if equals `1` and preset mode sleep if equals `0`
  - `SetPoint`: Value in the range of `0...255` that represents the target temperature set by the room operating panel. Set to a constant value if not needed.
- `target_temperature_base_value`: Base value for comfort temperature, default: `21`. Make sure to program the heating controller accordingly.
- `sensor_target_temperature_range`: Scale used to map the sensor's `SetPoint`
  value (`0...255`) onto a target-temperature span around
  `target_temperature_base_value`, default: `5`. Make sure to program the
  heating controller accordingly. This does not set Home Assistant's displayed
  minimum/maximum, which remain `target_temperature_base_value ± 10 °C`.
  - Minimum sensor-mapped target: `target_temperature_base_value - sensor_target_temperature_range`
  - Maximum sensor-mapped target: `target_temperature_base_value + sensor_target_temperature_range`
- `target_temperature_reduction_night`: Offset for night time reduction of target temperature. Make sure to program the heating controller accordingly.
  - Night time absolute temperature: `target_temperature_base_value - target_temperature_reduction_night`
- `temperature_frost_protection`: Target temperature for frost protection, this value will be commanded when the climate entity is switched to HVAC mode `off`. Make sure to program the heating controller accordingly.
- `command_frequency`: The heating controller requires periodic commands; otherwise the actor switches to contingency operating mode. Default: `minutes: 17`.
- Heating controller PI parameter: The heating controller `SRC-D08` does not send status telegrams, so there is no information of the current valve position (which is internally calculated by a PI control law). To provide the controller output to Home Assistant, the integration calculates the controller output based on the provided controller parameters:
  - `pi_control_Kp`: Parameter for the proportional controller (`%/K`), default: `5`. Make sure to program the heating controller accordingly.
  - `pi_control_Tn`: Parameter for the integral controller (`min`), default: `240`. Make sure to program the heating controller accordingly.

All climate numeric parameters must be finite and remain within the physical
ranges enforced by the configuration schema. Switching the entity to `off`
always sends the actor's switch-off telegram, even if the temperature sensor is
temporarily unavailable.

Example definition of a climate entity:

```yaml
climate:
  - platform: enocean_custom
    name: heating_controller_livingroom
    device_type: "SRC-D08"
    id: [0x0F, 0x53, 0xD6, 0x83]
    id_switch: [0x12, 0x34, 0x56, 0x78]
    sensor_entity_id: "sensor.temperature_livingroom"
    target_temperature_base_value: 21
    target_temperature_reduction_night: 5
    sensor_target_temperature_range: 10
    temperature_frost_protection: 8
    command_frequency:
      minutes: 20
    pi_control_Kp: 5
    pi_control_Tn: 240
```

#### Teach-In

In order for the heating controller to accept commands received by the climate entity, you need to teach-in the corresponding EnOcean ID. The integration provides entity services to do so. First, you will need to put the heating controller into learning mode, afterwards run the service.

Teach-in the temperature sensor for entity `climate.heating_controller_livingroom`:

```yaml
service: enocean_custom.climate_teach_in_actor
target:
  entity_id:
    - climate.heating_controller_livingroom
```

Repeat the procedure to teach-in the digital switch sensor to the heating controller.

Teach-in the digital switch sensor for entity `climate.heating_controller_livingroom`:

```yaml
service: enocean_custom.climate_teach_in_actor_switch
target:
  entity_id:
    - climate.heating_controller_livingroom
```

### Integration services

The climate teach-in services documented above remain available. The old
`enocean_custom.send_packet` service was removed in `v1.2.4`: it allowed
arbitrary sender spoofing and malformed calls could terminate the serial
worker. Normal entities do not use that public service.

`enocean_custom.learn` (added in `v1.3.0`) starts the same listening window
used by **Configure > Add a device** and fires
`enocean_custom_device_learned` with `{"id": [...], "hex": "AA:BB:CC:DD"}`
once an unknown sender is captured. It accepts an optional `timeout` field
(seconds, 15-300, default 60).

To pair a 4BS Eltako dimmer controlled by an EnOcean light entity:

1. Put the dimmer into learn mode.
2. Call `enocean_custom.send_teach_in` and target the corresponding `light`
   entity.

The service broadcasts the
[EEP 2.6.7 A5-38-08](https://www.enocean-alliance.org/wp-content/uploads/2017/05/EnOcean_Equipment_Profiles_EEP_v2.6.7_public.pdf)
teach-in telegram with that entity's configured `sender_id`; subsequent
brightness commands use the same identity.

### Bug fixes

- Stop and join the serial communicator before config-entry reload, preventing
  stale readers and multiple access to the same USB port.
- Close serial descriptors opened during config-flow validation.
- Exception to handle parsing of malformed packets: With the official protocol library, the EnOcean integration would crash when receiving a malformed package. In practice, this happens every few weeks to months for some installations. An exception handler was added to drop malformed packages, see [PR for original protocol library](https://github.com/kipe/enocean/pull/138)

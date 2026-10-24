# Device Intelligence (v2.1)

Local EnOcean identification for `ha_enocean_custom`. Everything described here
runs offline: no network lookup, no telemetry, no cloud call, no new runtime
dependency, and no automatic UTE acknowledgement or radio transmission.

## Verdict

A sender ID (EURID) identifies a _radio_, not a model. An EEP identifies a
_data profile_, not a product, and an EEP known to the EnOcean Alliance is not
necessarily decodable by this repository. The integration therefore displays
five separate things and never collapses them:

1. the observed radio identity;
2. the EEP declared by a UTE/4BS proof, or chosen manually;
3. the manufacturer/reference carried by a standardized Product ID;
4. the real ability of this code to decode and/or drive that profile;
5. field validation of a precise model.

Levels 1 to 4 are implemented locally. Level 5 requires hardware proof and is
never inferred from an EEP alone. **Nothing in this integration certifies
hardware.**

## Evidence, configuration mode and support

Three independent axes. Confusing them is the failure mode this feature exists
to prevent.

### Evidence — how a profile assertion was obtained

| Value             | Meaning                                                                       |
| ----------------- | ----------------------------------------------------------------------------- |
| `exact`           | A UTE telegram or an enriched 4BS teach-in explicitly carried RORG/FUNC/TYPE. |
| `assisted`        | A valid 32-bit EURID and Product ID came from an Alliance label.              |
| `manual`          | The operator selected the profile or configuration details.                   |
| `profile_unknown` | The sender is known but its EEP is not proven.                                |

UTE and enriched 4BS declare a profile. An ordinary RPS `F6` or 1BS `D5`
telegram does not carry its EEP, so it stays `profile_unknown`; there is no
silent inference and the radio ID is never presented as profile proof.

### Configuration mode — what the operator must still decide

| Value       | Meaning                                                             |
| ----------- | ------------------------------------------------------------------- |
| `automatic` | One platform, no further required parameters.                       |
| `assisted`  | One platform, but parameters or the device class must be confirmed. |
| `manual`    | The platform is ambiguous or the decoder needs a range/class.       |
| `yaml_only` | Implemented, but with no options-flow surface yet.                  |

### Support — what this repository can actually do

| Value         | Meaning                                                        |
| ------------- | -------------------------------------------------------------- |
| `supported`   | An unambiguous implementation exists for the declared EEP.     |
| `manual`      | Implemented, but configuration requires a human choice.        |
| `unsupported` | The EEP is declared and this repository has no decoder for it. |
| `unknown`     | There is not enough evidence to say anything.                  |

Support is derived from the configuration mode in code, so the table below and
runtime behaviour cannot drift apart.

## Implementation matrix

Each row maps to code present in this repository. It describes neither EnOcean
Alliance certification nor tested hardware.

| Declared EEP | Implementation in this repository                                                                                            | Platforms                 | Configuration mode                                           | Support     |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------- | ------------------------- | ------------------------------------------------------------ | ----------- |
| `F6-02-01`   | `binary_sensor.EnOceanBinarySensor` rocker decoding; `switch.py` also simulates this profile                                 | `binary_sensor`, `switch` | `manual` (ambiguous, and ordinary F6 carries no EEP)         | `manual`    |
| `F6-02-02`   | as above                                                                                                                     | `binary_sensor`, `switch` | `manual`                                                     | `manual`    |
| `F6-05-02`   | `binary_sensor.EnOceanSmokeDetector` (DB0 `0x00` off, `0x10` alarm) plus the diagnostic `EnOceanSmokeBatteryLow` (`0x30`); declared via the options flow, never inferred from an RPS telegram | `binary_sensor`           | `assisted` (profile must be declared)                        | `supported` |
| `F6-10-00`   | `sensor.EnOceanWindowHandle`: high nibble `C/E` open, `D` tilt, `F` closed; any other/truncated RPS is `unknown`             | `sensor`                  | `assisted` (device class)                                    | `supported` |
| `D5-00-01`   | `sensor.EnOceanD50001Contact`: `0x08` open / `0x09` closed; legacy `shuttercontact` remains an alias                         | `sensor`                  | `assisted` (device class)                                    | `supported` |
| `A5-10-06`   | `sensor.EnOceanTemperatureSensor`, a _generic_ linear 8-bit A5 decoder                                                       | `sensor`                  | `manual` (scale and raw range must be supplied)              | `manual`    |
| `A5-12-01`   | `sensor.EnOceanPowerSensor` / `EnOceanEnergySensor` via `parse_eep(0x12, 0x01)`                                              | `sensor`                  | `assisted`                                                   | `supported` |
| `A5-14-01`   | `binary_sensor.EnOceanA514Contact`; optional disabled-by-default diagnostic `sensor.EnOceanA514Voltage`                      | `binary_sensor`           | `automatic`                                                  | `supported` |
| `A5-20-04`   | `climate.EnOceanClimate` valve control                                                                                       | `climate`                 | `yaml_only` (the options flow persists SRC-D08 only)         | `manual`    |
| `A5-38-08`   | `light.EnOceanLight` commands and teach-in (transmit only)                                                                   | `light`                   | `assisted` (a sender identity is required)                   | `supported` |
| `D2-01-0A`   | `switch.EnOceanSwitch` D2 CMD `0x1` switching and CMD `0x4` feedback                                                         | `switch`                  | `manual` (EEP does not prove NodOn hardware)                 | `manual`    |
| `D2-01-0B`   | `sensor._decode_d2_measurement` for D2-01 CMD `0x7`                                                                          | `sensor`                  | `assisted`                                                   | `supported` |
| `D2-01-08`   | Ubiwizz lighting profile from field-verified DolphinView capture | `light`, `switch` | manual (catalog recipe) | manual |
| `D2-01-12`   | switch/light actuator with D2-01 feedback; explicit `ubiwizz_ubid1507c` limits documented UBID1507C rows to channels `0`/`1` | `light`, `switch` | manual (two valid platforms; EEP is not hardware identity) | manual |
| `D2-14-30`   | `d2_14.decode_d2_14_30`: smoke alarm (row entity), sensor-fault and maintenance flags, temperature, humidity, energy storage, hygrothermal comfort, indoor air analysis, weeks since maintenance, remaining lifetime. Layout from two agreeing implementations (openHAB `D2_14_30.java`, FHEM `multiFuncSensor.30`); **not yet validated on a real detector** | `binary_sensor`           | `assisted` (profile must be declared)                        | `supported` |

A platform is pre-selected in the options flow only when the mode is
`automatic` or `assisted` **and** exactly one platform applies. The selection
always remains changeable, and a manufacturer conflict suppresses it entirely.

### Generic EEP.xml decoder (sensor device class `eep`)

Since 2.10.0 a `sensor` row may use the device class `eep` together with a
profile stored in `radio_metadata.eep`. The decoder (`eep_catalog.py`) is
driven entirely by the vendored `EEP.xml`: bit offsets, ranges and scales come
from the file, and only the specification *shortcuts* are mapped to Home
Assistant device classes and units (`TMP` → temperature °C, `HUM` → humidity %,
`ILL`/`ILL1`/`ILL2`/`DWS` → illuminance lx, `Conc` → CO₂ ppm or VOC ppb, `SVC`
→ supply voltage V (diagnostic, disabled by default), `WND` → wind speed m/s,
`SP` → raw set point, `ACX`/`ACY`/`ACZ` → acceleration g (disabled by default);
enums `PIR`/`PIRS` → occupancy, `CO` → contact, `RAN` → rain). Each numeric
field becomes one `sensor` (`<id>-eep_<shortcut>`), each mapped enum one
`binary_sensor`. 4BS teach-in telegrams (LRN bit clear) and raw values outside
the documented range are ignored. The profile is pre-filled when a 4BS teach-in
declared it, otherwise it is chosen from the list and recorded as a manual
assertion.

Profiles covered (43), excluding the dedicated decoders above, bidirectional
A5-20-01 and the A5-38-08 command profile:

| Profile(s) | Description (EEP.xml) | Entities |
| --- | --- | --- |
| `A5-02-01` … `A5-02-0B`, `A5-02-10` … `A5-02-1B`, `A5-02-20`, `A5-02-30` | Temperature sensors (8 and 10 bit ranges) | temperature |
| `A5-04-01`, `A5-04-03` | Temperature and humidity | humidity, temperature |
| `A5-06-01`, `A5-06-02` | Light sensors | supply voltage, illuminance range 1 and 2 |
| `A5-07-01` | Occupancy with supply voltage | supply voltage, occupancy |
| `A5-08-01` | Light, temperature and occupancy | supply voltage, illuminance, temperature, occupancy |
| `A5-09-04` | CO₂ sensor | humidity, CO₂, temperature |
| `A5-09-05` | VOC sensor | VOC |
| `A5-10-03`, `A5-10-05`, `A5-10-06`, `A5-10-10`, `A5-10-12` | Room operating panels | set point, humidity where present, temperature |
| `A5-11-02` | Temperature controller output | controller output, actual set point |
| `A5-11-03` | Blind status | blind position, blind angle |
| `A5-13-01` (command 1 only) | Weather station | dawn light, temperature, wind speed, rain |
| `A5-30-03` | Digital inputs, wake and temperature | temperature |
| `D2-14-41` | Indoor temperature, humidity, acceleration, illumination, contact | temperature, humidity, illuminance, acceleration X/Y/Z, contact |

The `EEP.xml` file dates from EEP 2.6.x. A generic profile is **implemented**,
not **validated**: compare the first real telegram of your product with the
entities before relying on them, and report mismatches with the device
diagnostics (the last raw telegram is included).

### D2-01 actuator queries (CMD 0x3 / CMD 0x6)

Since 2.11.0 a D2-01 actuator can be *asked* for its state instead of waiting
for its next telegram. `d2_01.py` transcribes the relevant parts of EEP 2.6.7
(D2-01, pages 131-146) and nothing else:

| What | Source | Rule in this repository |
| --- | --- | --- |
| Actuator Status Query, CMD 0x3 | EEP 2.6.7 p. 135 | Two data bytes (`D2 03 <channel>`), directed to the actuator, sent as the dongle's Base ID. Applies to every D2-01 TYPE. The CMD 0x4 answer "shall be received within a maximum of 300ms from the time of transmission"; the pass waits exactly that window from the physical write, then moves on, never retries. |
| Actuator Measurement Query, CMD 0x6 | EEP 2.6.7 p. 137 | `D2 06 <qu|channel>` with `qu` (offset 10) selecting energy or power; same 300 ms window. Sent only for a TYPE whose capability matrix (pp. 131-132) marks "Measurement report on query", energy and power each following their own row, and only when a `powersensor` sensor row exists on the same sender to decode the CMD 0x7 answer. |
| Capability matrix | EEP 2.6.7 pp. 131-132 | `ENERGY_MEASUREMENT_TYPES`, `POWER_MEASUREMENT_TYPES`, `MEASUREMENT_REPORT_ON_QUERY_TYPES`, `LOCAL_CONTROL_TYPES`, `POWER_FAILURE_DETECTION_TYPES`, `OUTPUT_CHANNELS_BY_TYPE`. D2-01-0A (ASP-2-1-00): no metering. D2-01-0B (ASP-2-1-01): energy + power, report on query. D2-01-12 (UBID1507C): no metering. |
| Actuator Set Local, CMD 0x2 | EEP 2.6.7 pp. 133-134 | **Not implemented.** The message rewrites every local setting at once with no "keep" value and has "Response Timing: None"; only the local-control flag echoed in CMD 0x4 is exposed, read-only. |
| Actuator Set Measurement, CMD 0x5 | EEP 2.6.7 p. 136 | **Not implemented**: changes the actuator's autonomous reporting, no read-back. |

Triggers: the per-channel **Refresh state** button (on demand) and the
`d2_01_query_on_start` entry option (one pass after each start-up, off by
default). Both run through one serialized runner per entry: one ESP3 command
in flight, at least 250 ms between writes, at most one query per device and
channel per pass. Rows emulating a rocker (`switch_type: RPS`, every Ubiwizz
UBID1507C profile row) are not D2 actuators from this integration's point of
view and are never queried. Hardware validation of the CMD 0x3/0x4 exchange on
the NodOn ASP-2-1-00 and on a UTE-associated UBID1507C is still pending.

### User profile definitions (`<config>/enocean_custom/profiles.json`)

Since 2.11.0 the operator can add generic profiles the vendored `EEP.xml`
lacks, without a release. The file is plain JSON, read once at every set-up
of the entry (edit it, then reload the integration):

```json
{
  "version": 1,
  "profiles": [
    {
      "eep": "A5-02-7A",
      "description": "Vendor temperature sensor, 10-bit, -40..62.5 °C",
      "fields": [
        {"shortcut": "TMP", "description": "Temperature", "kind": "value",
         "offset": 14, "size": 10, "unit": "C",
         "range": {"min": 1023, "max": 0}, "scale": {"min": -40, "max": 62.5}},
        {"shortcut": "PIR", "kind": "enum", "offset": 24, "size": 1}
      ]
    }
  ]
}
```

Rules, enforced by `user_profiles.py` and covered by `tests/test_user_profiles.py`:

- **Data only.** The file goes through `json.loads` and a strict voluptuous
  schema (unknown keys refused, every number bounded, at most 64 profiles of
  32 fields, 256 KiB). Nothing in it is executed. `offset`/`size` follow the
  EEP convention (MSB-first bit offsets within the data bytes); a `value`
  field needs `range` and `scale` exactly like the `<value>` elements of
  `EEP.xml`; an `enum` field becomes a binary sensor only for the mapped
  shortcuts (`PIR`, `PIRS`, `CO`, `RAN`), other enums are ignored. Only 4BS
  (`A5`) and VLD (`D2`) profiles can be defined; an optional `command`
  (`offset`, `size`, `value`) restricts decoding to one command value.
- **Built-in profiles are never overridden.** A profile the vendored
  `EEP.xml` already describes, or one of the families with a dedicated
  decoder (`D2-01-*`, `D2-14-30`, `A5-12-01`, `A5-14-01`, `A5-20-*`,
  `A5-38-08`, `F6-*`, `D5-*`), is refused and listed in the Repair issue
  "Some user EEP profile definitions were skipped"; the other profiles of the
  file still load.
- **Invalid means disabled.** A file that fails the schema disables every
  user profile and raises the Repair issue "User EEP profile definitions are
  invalid" with the validation error. Rows that declare a user profile create
  no entity while the file is invalid or absent, and come back unchanged
  (same `unique_id`s) once it is fixed: identities are the generic
  `<sender>-eep_<slug>` formulas, exactly as for XML profiles.
- An accepted profile appears in the *EEP profile for the generic decoder*
  picker of the device forms and decodes through the same code path as the
  XML profiles. It is still an operator assertion, never hardware proof.

### NodOn catalogue boundary

This bounded inventory records manufacturer documentation, not radio identity,
implementation support, or hardware certification. A captured EURID is never
attributed to NodOn from an EEP alone, and ESP3 `OK` remains transport
acceptance rather than proof that a load switched.

| Documented product or family                        | EEP / documented function                                                              | Status here                                              | Required evidence before a stronger claim                            |
| --------------------------------------------------- | -------------------------------------------------------------------------------------- | -------------------------------------------------------- | -------------------------------------------------------------------- |
| ASP-2-1-00 / ASP-2-1-10 / ASP-2-1-01 SmartPlug      | `D2-01-0A`; one-channel switching, local control, status, and power-failure parameters | Manual `switch`, channel `0` only; no consumption entity | Captured fixture and safe-load commissioning with matching feedback. |
| CWS-2-1-01 wall switch                              | `F6-02-01` rocker                                                                      | Manual binary-sensor or switch mapping                   | Captured RPS fixture and decoder test.                               |
| CCS-2-1-01 card switch                              | `F6-04-01` card insertion/removal                                                      | Unsupported                                              | Telegram fixture and decoder test.                                   |
| SIN-2-FP-01 pilot-wire module                       | `D2-01-0C` heating modes and telemetry                                                 | Unsupported                                              | Command and status fixtures.                                         |
| SIN-2-2-01 lighting relay                           | `D2-01-12` ON/OFF actuator                                                             | Manual mapping; untested                                 | Directed-command and feedback fixtures, then safe physical proof.    |
| SIN-2-RS-01 roller-shutter module                   | `D2-05-00` shutter actuator                                                            | Unsupported                                              | Range and status fixtures.                                           |
| CRC-2-6-01 Soft Remote                              | `D2-03-0A` scene/button transmitter                                                    | Unsupported                                              | Button-event fixture and decoder test.                               |
| Other transmitter families listed by the ASP manual | `F6-10-00`, `D5-00-01`, `A5-07-*`, `A5-08-*`, `A5-10-*`, `A5-14-*`                     | Only rows in the implementation matrix are supported     | Exact model evidence plus profile-specific fixture.                  |

A sanitized fixture must contain the sender, destination when applicable,
RORG/EEP, payload, status direction, and expected decoded event. It is not
hardware proof: actuators also need a safe physical ON/OFF test with matching
feedback.

Official manufacturer sources: [ASP SmartPlug support](https://support.nodon.fr/support/solutions/articles/150000192097-prise-intelligente-enocean-asp-2-1-00-), [CWS-2-1-01](https://support.nodon.fr/support/solutions/articles/150000192103-interrupteur-mural-enocean-cws-2-1-01-), [CCS-2-1-01](https://support.nodon.fr/support/solutions/articles/150000192099-interrupteur-%25C3%25A0-carte-enocean-ccs-2-1-01-), [SIN-2-FP-01](https://support.nodon.fr/support/solutions/articles/150000052161-module-chauffage-fil-pilote-enocean-sin-2-fp-01-), [SIN-2-2-01](https://support.nodon.fr/support/solutions/articles/150000052164-module-eclairage-on-off-enocean-sin-2-2-01-), [SIN-2-RS-01](https://support.nodon.fr/support/solutions/articles/150000052163-module-volet-roulant-enocean-sin-2-rs-01-), and [CRC-2-6-01](https://support.nodon.fr/support/solutions/articles/150000052270-t%C3%A9l%C3%A9commande-soft-remote-enocean-crc-2-6-01-).

## Manual EEP entry

`Add device` → learn, QR label or typed ID → the _Name the captured device_
step. That step carries an optional **Manual EEP profile** text field, shown
**only when neither a radio telegram nor a Product ID has already declared a
profile**. A declared EEP is never presented as editable, and a manual value
smuggled into a submission for such a device is ignored: proof is never
overwritten by a claim.

The field accepts the canonical `XX-XX-XX` hexadecimal form only. Lowercase is
accepted and normalized to uppercase; anything else is refused with the
`invalid_eep` error instead of being coerced. The value is an _assertion_, not
a measurement, so it never certifies hardware and never resolves a model.

| Submitted value                                         | `evidence`                                           | `eep_source` | `support`     |
| ------------------------------------------------------- | ---------------------------------------------------- | ------------ | ------------- |
| empty                                                   | unchanged (`profile_unknown` after a bare learn)     | unchanged    | unchanged     |
| valid, present in the implementation matrix             | `manual`                                             | `manual`     | `manual`      |
| valid, absent from the implementation matrix            | `manual`                                             | `manual`     | `unsupported` |
| valid, but the metadata carries a manufacturer conflict | `manual`                                             | `manual`     | `unknown`     |
| not `XX-XX-XX`                                          | rejected: the form is redisplayed with `invalid_eep` | —            | —             |

A manual assertion never pre-selects a platform, even when the EEP maps to a
single implementation: the operator always chooses it explicitly. Safe QR
fields already captured — `sender_id`, `manufacturer_id`, `product_id`,
`product_reference` and `manufacturer_conflict` — are preserved when the
assertion is added to a Product ID absent from the catalog. The scanned payload
and its security containers are not re-introduced: the transformation is
restricted to the persisted-field set listed below.

## Commissioning labels and Product IDs

- `30S` carries the EURID. Only the 32-bit radio identifier is supported; a
  genuine 48-bit EURID is **rejected**, never truncated.
- `1P` carries a 48-bit Product ID: an 11-bit manufacturer field inside the
  first two bytes, then a 32-bit product reference. The remaining bits are
  reserved: a manufacturer field above `0x7FF` is **rejected**, never masked.
- Security containers (`10Z`, `11Z`, `13Z`) are not needed for identification.
  They are dropped during parsing and are never persisted, logged, exposed in
  an event, or included in diagnostics.

### Bundled product catalog

Remote Commissioning associates a Product ID with a Device Description File
(DDF). The official EnOcean Alliance repository is a validation source, **not**
a world catalog: the 2026-08-05 audit found seven XML files in total. Entries
from its `Examples/` folder are deliberately excluded.

| Product ID     | Manufacturer | Model                       | DDF TX EEP | DDF RX EEP                   | Support here  |
| -------------- | ------------ | --------------------------- | ---------- | ---------------------------- | ------------- |
| `002D00000004` | Afriso       | Cositherm 2-Channel         | `B0-00-00` | 11 × `A5-10-xx`              | `unsupported` |
| `002D0000000A` | Afriso       | Cositherm 2-Channel         | `D2-34-10` | `D2-34-10` + 11 × `A5-10-xx` | `unsupported` |
| `002D00000005` | Afriso       | Cositherm 6-Channel         | `B0-00-00` | 11 × `A5-10-xx`              | `unsupported` |
| `002D0000000B` | Afriso       | Cositherm 6-Channel         | `D2-34-10` | `D2-34-10` + 11 × `A5-10-xx` | `unsupported` |
| `001600013045` | BSC Computer | eTronic window/door contact | `A5-14-01` | —                            | `supported`   |

Source: `EnOcean-Alliance/enocean-alliance-ddf`, audited 2026-08-05. Being
cataloged identifies hardware; it never overrides the decoder verdict, which is
why four of these five products stay `unsupported`.

The versioned `data/ddf_catalog.json` is generated deterministically by
`scripts/import_ddf.py`; it separates TX and RX profiles per exact Product ID.
`D2-34-10` remains unsupported: the DDF names the profile but publishes no
bit-level payload specification, so no decoder is invented. A5-10 profiles are
not added to the implementation matrix without a documented real-frame test.

### Pairing recipe catalog (declare profile picker)

Do not confuse this with the DDF catalog above: `data/products.json`
(loaded by `device_catalog.py`) is a *different* file, for a *different*
purpose. The DDF catalog passively resolves a Product ID actually observed
on the air into a manufacturer/model string; this one is an operator-chosen
shortcut in the "declare profile" step of the options flow, so a row can be
enriched by picking a product name instead of typing an EEP. A pick is
recorded exactly like a typed EEP (`eep_source: manual`) through the very
same validated path, identity-preservation and collision checks included —
one more confirmation of the repository's own doctrine: **a recipe is a
convenience, never proof**.

| Recipe | EEP | Platform | Selectable today |
| --- | --- | --- | --- |
| NodOn ASP-2-1-00 (Smart Plug) | `D2-01-0A` | `switch` | yes |
| NodOn ASP-2-1-01 (Smart Plug + Metering) | `D2-01-0B` | `switch` | yes (metering not decoded) |
| NodOn SIN-2-2-01 (dual switch micromodule) | `D2-01-12` | `switch` | yes |
| NodOn SIN-2-RS-01 (roller shutter module) | `D2-05-00` | — | no: no cover platform exists |
| NodOn CWS-2-1-01 (wireless wall switch) | `F6-02-01` | `binary_sensor` | yes, informational only (RPS declares no EEP) |
| Eltako FT55 (wireless rocker) | `F6-02-01` | `binary_sensor` | yes, informational only |
| HOPPE SecuSignal (window handle) | `F6-10-00` | `sensor` | yes |
| Nexelec Insafe+ Origin (smoke / air quality) | `D2-14-30` | `binary_sensor` | yes |
| Micropelt MVA005 (self-powered radiator valve) | `A5-20-06` | — | no: pending the experimental valve item |
| Kieback & Peter MD15 (radiator actuator) | `A5-20-01` | — | no: pending the experimental valve item; configurable family, not a hardware certainty |

Ubiwizz's own UBID1507C stays the original, hand-written
`CommissioningProfile` in `device_catalog.py` (field-verified: real
DolphinView capture and pairing procedure), unaffected by this catalog.

## Passive device inbox

Every received radio telegram updates an entry-owned in-memory observation:
UTC last-seen time, packet count, latest RSSI, repeater count for RPS/1BS/4BS,
and observed RORGs. The unknown-sender LRU is capped at 64; configured YAML/UI
senders may be enriched beyond that cap. Nothing is written to `.storage`, and
unload clears the registry. The learn/QR radio card shows the latest signal
facts when present. Exportable diagnostics expose only
`observed_senders_count`, never sender rows.

The radio card also resolves the 11-bit manufacturer ID against a short
registry sourced from the EEP specification. Placeholders stay language
neutral: a resolved ID shows the published vendor name, an observed but
unlisted ID shows the `not_registered` token, and an ID that was never observed
shows `—`. The distinction matters because "not registered" is an assertion
about the registry, which cannot be made without having read an ID.

### SIGNAL telegrams (RORG 0xD0)

Independently of its main EEP, a device may send a SIGNAL telegram (RORG
`0xD0`) to report a generic system condition, identified by a one-byte MID
that follows the RORG. `signal_telegram.py` decodes the five MIDs in scope
for this release, straight from the EnOcean Alliance "Signal Telegram"
system specification V3.6 (<https://www.enocean-alliance.org/st/>):

| MID    | Content                       | Surfaced as                                             |
| ------ | ------------------------------ | -------------------------------------------------------- |
| `0x06` | Energy status (remaining energy, 1..100 %, 0 = "gasp of death") | a disabled-by-default per-sender diagnostic sensor (`signal_energy`), alongside the existing RSSI/last-seen/repeater-hops set |
| `0x07` | Revision (SW version, then HW version, each `A.B.C.D`) | per-device diagnostics export only |
| `0x08` | Heartbeat ("still alive", no payload)                   | per-device diagnostics export only; also resets the silence timer like any other telegram from that sender |
| `0x0A` | RX-channel quality (debug link quality)                 | per-device diagnostics export only |
| `0x12` | Product ID (manufacturer ID + product reference)        | per-device diagnostics export only, redacted the same way the UTE-derived Product ID already is |

Every decoded fact is **display-only**: it is read from the inbox's
"latest telegram observed" slot and shown in `async_get_device_diagnostics`,
never written into a row's `radio_metadata`. A SIGNAL telegram never changes
a declared profile, the same way an RPS or 1BS telegram never does.

MID `0x0A` deserves a caveat: on EnOcean TCM 515/615 modules it is normally
only produced once a transmitter's debug "RSSI test mode" has been turned on
with a dedicated ESP3 write command, which the module's own user manual
warns "significantly increases the radio traffic" and "shall not be used in
production devices". This integration never sends that command, or any
other command that would make a device start emitting SIGNAL telegrams it
would not send on its own; a MID `0x0A` telegram is only decoded here if it
already arrived on the air. MID `0x07` (Revision) is 15 ERP1 data bytes,
one more than the 14-byte broadcast limit the same manual documents; on
real hardware it may need ERP2 chaining the vendored library does not
reassemble, in which case the integration simply never decodes it rather
than guessing — **needs hardware validation** before anyone should expect
to see it in practice.

## Privacy and integrity boundaries

- **Persisted fields.** `radio_metadata` stores only `sender_id`, `eep`,
  `eep_source`, `manufacturer_id`, `product_id`, `product_reference`,
  `evidence`, `support` and `manufacturer_conflict`. The scanned payload is
  never stored.
- **No forgeable strings.** `manufacturer`, `model` and `model_id` are _not_
  persisted. Config entry options are hand-editable in `.storage`, so those
  strings are resolved at runtime from `product_id` against the catalog above.
  A row that tries to persist them is rejected.
- **Self-consistency.** If `radio_metadata.sender_id` is present it must equal
  the `id` of the device row it belongs to; a mismatching row is rejected.
- **Diagnostics.** Exportable diagnostics contain aggregates only. No sender
  ID, Product ID, product reference, device name, `radio_metadata` or
  `ui_devices` is included.
- **No background UTE acknowledgement.** v2.1 does not accept UTE teach-ins in
  the background. A UTE telegram is read as evidence — that passive extraction
  is exactly what feeds `evidence=exact` — but it is never answered on the
  radio. The serial worker is started with automatic acknowledgement disabled,
  so no device can pair itself with your dongle by asking. Pairing stays an
  operator action, through the guided pairing flow and the existing teach-in
  services, which transmit only when you trigger them.

## Home Assistant Device Registry

Every entity of one sender is grouped under the stable identifier
`(enocean_custom, <8 hex digits>)`. This works for historical YAML entities
too, with no configuration change.

`manufacturer`, `model` and `model_id` are populated **only** when the Product
ID is exactly cataloged and free of manufacturer conflict. A declared EEP alone
never produces a model string: grouping happens without inventing identity.

## Experimental valve strategies: A5-20-01 and A5-20-06

**Experimental — needs hardware validation.** Disabled by default in the
sense that nothing offers them: YAML only (not in
`schema.UI_CLIMATE_DEVICE_TYPES`, so the options flow never lists them,
exactly like A5-20-04 before it), and even in YAML a row is refused unless
`experimental_valve_ack: true` is set, a typo-proof confirmation that the
operator has read this section.

Unlike A5-20-04 (which this repository drives from its own periodic
PI-loop send, `_async_control_heating`), A5-20-01 and A5-20-06 are
*polled by the valve*: the actuator contacts the controller every radio
cycle with a direction-1 status telegram and expects a direction-2 reply
inside one second, or it falls back to running its internal thermostat
autonomously and stops listening to Home Assistant
(EEP.xml A5-20-01; the official A5-20-06 EEP proposal, EH4/Micropelt,
V0.6, 2018-02-28, "Maximum response time must be less than 1 second").
That budget rules out the existing async commit machinery (a lock, a
`Future`, potentially other awaits mid-transaction): `valve_a5_20.py`'s
decode/encode functions are pure, and `climate.py` calls them directly
inside the dispatcher callback that already decodes the status telegram
— no `await`, no executor hop, so the reply is queued the moment the
status telegram is parsed, through the same always-synchronous
`send_command` → `gateway.send` path every other command in this
integration already uses.

Both profiles are only ever driven in **valve-position mode** (0..100 %,
not the alternative 0..40 °C temperature-setpoint mode each profile also
defines): simpler, and consistent with how this repository already drives
A5-20-04. Window-open, obstruction, energy and (for A5-20-06) local-offset
bits are decoded and exposed as diagnostic `extra_state_attributes`, never
acted on automatically. The 4BS Variation-3 bidirectional teach-in
response is computed by a single general formula
(`valve_a5_20.bidirectional_teach_in_response`), cross-checked bit for bit
against this repository's own, already field-verified A5-20-04 response —
see the module's own docstring for the derivation and the regression test
that pins it.

**Must be validated on real hardware before relying on it**:
- The actual round-trip latency of `send_command` → `gateway.send` →
  the serial worker's write, end to end, against the one-second budget —
  this environment has no real dongle or valve to measure it against.
- The exact byte layout against a real A5-20-01 actuator (Kieback & Peter
  MD15, Thermokon) and a real A5-20-06 actuator (Micropelt MVA005): the
  former comes from the vendored `EEP.xml`, the latter from the official
  but still-DRAFT EEP proposal PDF, neither captured from a real frame by
  this repository.
- Whether skipping the reply outright (rather than sending a defined
  fallback command) is the right behavior when the linked sensor or the
  target temperature is not yet available — the valve's own documented
  autonomous fallback may or may not be the best outcome in practice.

## Sender ID pool

`sender_pool.py` computes offsets inside the dongle's own Base ID
(`1..127`, read once at startup by the existing, radio-silent `CO_RD_IDBASE`
command) for the three kinds of row that need a *virtual* transmitter
identity: A5-38-08 dimmer teach-in (`light.py`), the SRC-D08 climate panel
(`climate.py`), and an RPS-emulated switch (`switch.py`, `switch_type:
RPS`). It never writes `CO_WR_IDBASE` — the Base ID itself can only be
changed ten times in the module's life, with no way back — and it never
proposes an ID outside what the dongle has already claimed. `schema.py`'s
`used_sender_ids` scans every existing row for the field it actually
*transmits from* (a light's `sender_id`, a climate row's `id_switch`, an
RPS switch's own `id`; a `default`/D2 switch row transmits as the dongle's
own Base ID instead, via `get_gateway_base_id`, so its `id` — the receiving
actuator's real address — is excluded), and the options flow's *add device*
form pre-fills the lowest free one. This is a one-time suggestion at
creation time only, always still editable: an RPS-emulated switch in
particular is often deliberately re-using an ID a receiver already learned
from a device it replaces, which auto-allocation must never override.
Nothing here retroactively touches an existing row's sender ID.

**Needs hardware validation**: that a real dongle actually refuses an
out-of-range self-ID (the prerequisite this item was built against) has not
been exercised against real hardware in this environment.

## Existing installations

The v2.1 upgrade groups entities by sender immediately, but it never
retroactively invents a manufacturer, Product ID, model or EEP for devices
configured before it. Precise identification requires a new conclusive
teach-in, a standardized QR label, or the manual EEP entry described above —
which stays an operator assertion and is labeled as such. Historical YAML
configuration and `ui_devices` rows without `radio_metadata` stay valid.

### YAML-to-UI migration boundary

The v2.3 options flow inventories YAML configuration in memory only. It imports
only `binary_sensor` and `switch`, because those platforms share their exact
legacy unique-ID formulas with the options flow through the `identity` module,
which every platform uses to mint its `unique_id`. In particular, a binary
sensor without `device_class` deliberately remains `<decimal-id>-None`. Sensor, light,
and climate YAML entries are reported as non-importable and remain untouched.

Imported rows carry no `radio_metadata`: migration is configuration transport,
not new evidence. It cannot manufacture an EEP, Product ID, sender identity, or
manufacturer. The required sequence is **import in the UI → remove the imported
YAML blocks → restart Home Assistant → verify entities and automations**.

## Sources

- Ubiwizz UBID1507C manual: <https://ubiwizz.com/index.php?controller=attachment&id_attachment=927>
- Ubiwizz UBID1507C product page: <https://ubiwizz.com/l-offre-produits-ubiwizz/11905-micromodule-radio-enocean-2-canaux-2x5a.html>
- EnOcean Equipment Profiles specification (F6-10-00, D5-00-01): <https://www.enocean-alliance.org/wp-content/uploads/2017/05/EnOcean_Equipment_Profiles_EEP_v2.6.7_public.pdf>
- NodOn product pages: <https://nodon.fr/en/products/enocean-wall-switch>, <https://nodon.fr/en/products/enocean-card-switch>, <https://nodon.fr/en/products/enocean-pilot-wire-heating-module>, <https://nodon.fr/en/products/enocean-on-off-lighting-relay-switch>, <https://nodon.fr/en/products/enocean-roller-shutter-relay-switch>, <https://nodon.fr/en/products/enocean-soft-button>
- EnOcean Alliance, EEP database: <https://www.enocean-alliance.org/products/eeps>
- EnOcean Alliance, Product ID and labelling: <https://www.enocean-alliance.org/productid>
- EnOcean Alliance, manufacturer list: <https://enoceanwiki.atlassian.net/wiki/spaces/IEC/pages/260669482>
- EnOcean Alliance, Remote Commissioning: <https://www.enocean-alliance.org/wp-content/uploads/2018/04/Remote_Commisioning_Short_description.pdf>
- Official DDF repository: <https://github.com/EnOcean-Alliance/enocean-alliance-ddf>
- Home Assistant EnOcean integration: <https://www.home-assistant.io/integrations/enocean>
- Home Assistant UTE incident (2025): <https://github.com/home-assistant/core/issues/151148>
- openHAB EnOcean binding: <https://www.openhab.org/addons/bindings/enocean>
- `enocean-async` teach-in documentation: <https://github.com/henningkerstan/enocean-async/blob/main/docs/TEACHIN.md>

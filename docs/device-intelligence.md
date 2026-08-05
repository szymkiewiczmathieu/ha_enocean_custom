# Device Intelligence (v2.1)

Local EnOcean identification for `ha_enocean_custom`. Everything described here
runs offline: no network lookup, no telemetry, no cloud call, no new runtime
dependency, and no automatic UTE acknowledgement or radio transmission.

## Verdict

A sender ID (EURID) identifies a *radio*, not a model. An EEP identifies a
*data profile*, not a product, and an EEP known to the EnOcean Alliance is not
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

| Value | Meaning |
| --- | --- |
| `exact` | A UTE telegram or an enriched 4BS teach-in explicitly carried RORG/FUNC/TYPE. |
| `assisted` | A valid 32-bit EURID and Product ID came from an Alliance label. |
| `manual` | The operator selected the profile or configuration details. |
| `profile_unknown` | The sender is known but its EEP is not proven. |

UTE and enriched 4BS declare a profile. An ordinary RPS `F6` or 1BS `D5`
telegram does not carry its EEP, so it stays `profile_unknown`; there is no
silent inference and the radio ID is never presented as profile proof.

### Configuration mode — what the operator must still decide

| Value | Meaning |
| --- | --- |
| `automatic` | One platform, no further required parameters. |
| `assisted` | One platform, but parameters or the device class must be confirmed. |
| `manual` | The platform is ambiguous or the decoder needs a range/class. |
| `yaml_only` | Implemented, but with no options-flow surface yet. |

### Support — what this repository can actually do

| Value | Meaning |
| --- | --- |
| `supported` | An unambiguous implementation exists for the declared EEP. |
| `manual` | Implemented, but configuration requires a human choice. |
| `unsupported` | The EEP is declared and this repository has no decoder for it. |
| `unknown` | There is not enough evidence to say anything. |

Support is derived from the configuration mode in code, so the table below and
runtime behaviour cannot drift apart.

## Implementation matrix

Each row maps to code present in this repository. It describes neither EnOcean
Alliance certification nor tested hardware.

| Declared EEP | Implementation in this repository | Platforms | Configuration mode | Support |
| --- | --- | --- | --- | --- |
| `F6-02-01` | `binary_sensor.EnOceanBinarySensor` rocker decoding; `switch.py` also simulates this profile | `binary_sensor`, `switch` | `manual` (ambiguous, and ordinary F6 carries no EEP) | `manual` |
| `F6-02-02` | as above | `binary_sensor`, `switch` | `manual` | `manual` |
| `F6-10-00` | `sensor.EnOceanWindowHandle` | `sensor` | `assisted` (device class) | `supported` |
| `D5-00-01` | `sensor.EnOceanShutterContact` | `sensor` | `assisted` (device class) | `supported` |
| `A5-10-06` | `sensor.EnOceanTemperatureSensor`, a *generic* linear 8-bit A5 decoder | `sensor` | `manual` (scale and raw range must be supplied) | `manual` |
| `A5-12-01` | `sensor.EnOceanPowerSensor` / `EnOceanEnergySensor` via `parse_eep(0x12, 0x01)` | `sensor` | `assisted` | `supported` |
| `A5-20-04` | `climate.EnOceanClimate` valve control | `climate` | `yaml_only` (the options flow persists SRC-D08 only) | `manual` |
| `A5-38-08` | `light.EnOceanLight` commands and teach-in (transmit only) | `light` | `assisted` (a sender identity is required) | `supported` |
| `D2-01-0B` | `sensor._decode_d2_measurement` for D2-01 CMD `0x7` | `sensor` | `assisted` | `supported` |
| `D2-01-12` | switch/light actuator with D2-01 feedback | `light`, `switch` | `manual` (two valid platforms) | `manual` |

A platform is pre-selected in the options flow only when the mode is
`automatic` or `assisted` **and** exactly one platform applies. The selection
always remains changeable, and a manufacturer conflict suppresses it entirely.

## Manual EEP entry

`Add device` → learn, QR label or typed ID → the *Name the captured device*
step. That step carries an optional **Manual EEP profile** text field, shown
**only when neither a radio telegram nor a Product ID has already declared a
profile**. A declared EEP is never presented as editable, and a manual value
smuggled into a submission for such a device is ignored: proof is never
overwritten by a claim.

The field accepts the canonical `XX-XX-XX` hexadecimal form only. Lowercase is
accepted and normalized to uppercase; anything else is refused with the
`invalid_eep` error instead of being coerced. The value is an *assertion*, not
a measurement, so it never certifies hardware and never resolves a model.

| Submitted value | `evidence` | `eep_source` | `support` |
| --- | --- | --- | --- |
| empty | unchanged (`profile_unknown` after a bare learn) | unchanged | unchanged |
| valid, present in the implementation matrix | `manual` | `manual` | `manual` |
| valid, absent from the implementation matrix | `manual` | `manual` | `unsupported` |
| valid, but the metadata carries a manufacturer conflict | `manual` | `manual` | `unknown` |
| not `XX-XX-XX` | rejected: the form is redisplayed with `invalid_eep` | — | — |

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

| Product ID | Manufacturer | Model | DDF TX EEP | Support here |
| --- | --- | --- | --- | --- |
| `002D00000004` | Afriso | Cositherm 2-Channel | `D2-34-10` | `unsupported` |
| `002D0000000A` | Afriso | Cositherm 2-Channel | `D2-34-10` | `unsupported` |
| `002D00000005` | Afriso | Cositherm 6-Channel | `D2-34-10` | `unsupported` |
| `002D0000000B` | Afriso | Cositherm 6-Channel | `D2-34-10` | `unsupported` |
| `001600013045` | BSC Computer | eTronic window/door contact | `A5-14-01` | `unsupported` |

Source: `EnOcean-Alliance/enocean-alliance-ddf`, audited 2026-08-05. These
devices are *identified* but remain `unsupported`, because no exact decoder for
`D2-34-10` or `A5-14-01` exists in this repository. Being cataloged never
overrides the decoder verdict.

## Privacy and integrity boundaries

- **Persisted fields.** `radio_metadata` stores only `sender_id`, `eep`,
  `eep_source`, `manufacturer_id`, `product_id`, `product_reference`,
  `evidence`, `support` and `manufacturer_conflict`. The scanned payload is
  never stored.
- **No forgeable strings.** `manufacturer`, `model` and `model_id` are *not*
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

## Existing installations

The v2.1 upgrade groups entities by sender immediately, but it never
retroactively invents a manufacturer, Product ID, model or EEP for devices
configured before it. Precise identification requires a new conclusive
teach-in, a standardized QR label, or the manual EEP entry described above —
which stays an operator assertion and is labeled as such. Historical YAML
configuration and `ui_devices` rows without `radio_metadata` stay valid.

## Sources

- EnOcean Alliance, EEP database: <https://www.enocean-alliance.org/products/eeps>
- EnOcean Alliance, Product ID and labelling: <https://www.enocean-alliance.org/productid>
- EnOcean Alliance, manufacturer list: <https://enoceanwiki.atlassian.net/wiki/spaces/IEC/pages/260669482>
- EnOcean Alliance, Remote Commissioning: <https://www.enocean-alliance.org/wp-content/uploads/2018/04/Remote_Commisioning_Short_description.pdf>
- Official DDF repository: <https://github.com/EnOcean-Alliance/enocean-alliance-ddf>
- Home Assistant EnOcean integration: <https://www.home-assistant.io/integrations/enocean>
- Home Assistant UTE incident (2025): <https://github.com/home-assistant/core/issues/151148>
- openHAB EnOcean binding: <https://www.openhab.org/addons/bindings/enocean>
- `enocean-async` teach-in documentation: <https://github.com/henningkerstan/enocean-async/blob/main/docs/TEACHIN.md>

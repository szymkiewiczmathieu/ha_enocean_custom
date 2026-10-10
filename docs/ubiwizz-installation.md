# Ubiwizz EnOcean installation profile

This guide covers the documented Ubiwizz UBID1507C two-channel micromodule,
HOPPE/Ubiwizz handles, D5-00-01 contacts, and existing NodOn SmartPlugs. It is
an integration and diagnostics guide, not hardware certification or a claim
that a radio ID identifies a product.

## Scope and source boundary

| Equipment                      | Explicit profile or configuration                      | Integration behavior                                                 |
| ------------------------------ | ------------------------------------------------------ | -------------------------------------------------------------------- |
| Ubiwizz UBID1507C              | `ubiwizz_ubid1507c`, `D2-01-12`, channels `0` and `1`  | Two default `switch` entities with channel-specific D2 feedback.     |
| HOPPE/Ubiwizz connected handle | `F6-10-00`, `sensor` with `device_class: windowhandle` | Reports `closed`, `open`, `tilt`, or `unknown`.                      |
| Magnetic/contact transmitter   | `D5-00-01`, `sensor` with `device_class: contact`      | Reports `open` or `closed`; 1BS teach-in values do not change state. |
| Existing NodOn SmartPlug       | `D2-01-0A`, channel `0`                                | Existing switch path only; the Ubiwizz guided flow refuses this EEP. |

The UBID1507C profile must be selected explicitly. It is **not** inferred from
a D2 telegram, EURID, QR label, or the `D2-01-12` EEP. The official material
documents two 5 A outputs and `D2-01-12`, but an EEP alone is not a product
identity.

- [Official UBID1507C manual][ubiwizz-manual]
- [Official UBID1507C product page][ubiwizz-product]

## UBID1507C outputs and feedback

Configure one default switch for each output. The explicit profile accepts only
channels `0` and `1`; an unprofiled generic `D2-01-12` switch retains the
integration's generic channel range.

```yaml
switch:
  - platform: enocean_custom
    name: Ubiwizz kitchen output 1
    id: [0x01, 0x02, 0x03, 0x04] # Replace with this module's EURID
    eep: D2-01-12
    actuator_profile: ubiwizz_ubid1507c
    channel: 0

  - platform: enocean_custom
    name: Ubiwizz kitchen output 2
    id: [0x01, 0x02, 0x03, 0x04] # Same module EURID, different channel
    eep: D2-01-12
    actuator_profile: ubiwizz_ubid1507c
    channel: 1
```

A D2-01 `CMD 0x4` Actuator Status Response updates only the matching channel.
Output value `0` is off; any value above `0` is on. `d2_channel`,
`d2_output_value`, power-failure fields, and `last_status` are feedback
attributes.

ESP3 `OK` acknowledges delivery to the USB dongle only. It does not prove that
the module switched its output, retained a configuration change, or belongs to
the configured profile. The entity state remains unknown until matching D2
status is received.

### Local association

The UBID1507C manual describes physical local association: make three short
`PRESS` presses to enter association mode; for output 2, make one further short
press before operating the compatible transmitter. Verify the actual module
revision and LED behavior in the manual. This is a physical procedure, not a
generated radio sequence.

The guided Ubiwizz flow uses the existing directed D2 path. It requires, in
order, a queued command, ESP3 `OK`, and later matching D2 `CMD 0x4` ON feedback
from the exact actuator and channel. That is evidence for this transaction only;
it does not prove product identity or persistent factory-reset commissioning.
Power-cycle validation remains a hardware-release check.

## Repeater boundary

The options-flow **Ubiwizz repeater diagnostic** is intentionally no-radio. It
accepts only documented candidate labels and levels for an operator note; it
reads no repeater state, creates no entity, persists no request, and transmits
no ERP1 or ESP3 packet.

The separately registered `enocean_custom.repeater_set_level` service is a live
radio configuration write. It is not the diagnostic flow and must be treated as
an advanced, experimental operation:

- It is meaningful only for a **UI-managed** switch whose
  `configured_actuator_profile` state attribute is exactly
  `ubiwizz_ubid1507c`.
- It accepts `level` `0`, `1`, or `2`. The integration records the requested
  level and ESP3 transport telemetry; it has no supported remote read-back.
- The supplied Ubiwizz sources do not document a remote repeater default,
  packet procedure, module-revision compatibility, or persistence across a
  power cycle. Do not represent a requested level as the module's state.
- Never target generic D2 devices, NodOn `D2-01-0A` SmartPlugs, or the USB
  gateway. `CO_WR_REPEATER` configures the gateway, not a Ubiwizz module.
- Test only on a known safe installation. This configuration write is not a
  relay command, but it is a real radio transmission and should not be used to
  experiment with mains-connected equipment.

Example for an eligible UI-managed switch:

```yaml
action: enocean_custom.repeater_set_level
data:
  entity_id: switch.ubiwizz_kitchen_output_1
  level: 1
```

After a call, inspect `repeater_level_requested`, `repeater_last_esp3_ack`,
`repeater_transmissions`, and `repeater_acknowledgements` on the switch. These
are local transmission facts only. A true acknowledgement does **not** confirm
remote reception, active repeater behavior, persistence, or physical output.

## Incoming devices

### HOPPE/Ubiwizz handles: F6-10-00

Create a sensor with `device_class: windowhandle` only after explicitly
selecting or declaring `F6-10-00`. Ordinary RPS telegrams do not declare their
EEP and are not silently classified as handles.

```yaml
sensor:
  - platform: enocean_custom
    name: Bedroom handle
    id: [0x05, 0x06, 0x07, 0x08] # Replace with the handle EURID
    device_class: windowhandle
```

| DB0 high nibble                          | Handle state |
| ---------------------------------------- | ------------ |
| `0xC` or `0xE`                           | `open`       |
| `0xD`                                    | `tilt`       |
| `0xF`                                    | `closed`     |
| Other values or a truncated RPS telegram | `unknown`    |

The decoder follows the F6-10-00 high nibble and ignores its documented
don't-care low nibble.

### D5-00-01 contacts

Use `device_class: contact` for a 1BS contact. The historical
`shuttercontact` class remains an alias for existing configurations and entity
identities.

```yaml
sensor:
  - platform: enocean_custom
    name: Pantry contact
    id: [0x09, 0x0A, 0x0B, 0x0C] # Replace with the contact EURID
    device_class: contact
```

| D5 DB0           | Contact state                                  |
| ---------------- | ---------------------------------------------- |
| `0x08`           | `open`                                         |
| `0x09`           | `closed`                                       |
| `0x00` or `0x01` | Ignored 1BS teach-in; prior state is retained. |

## Safe validation checklist

1. Record each EURID, room, module, output channel, and intended load before
   configuring entities. Never reuse the example IDs.
2. For a UBID1507C, configure both explicit profile rows and verify that status
   on one channel does not affect the other.
3. Confirm closed, open, and tilt telegrams for every handle before relying on
   an automation. Unsupported values intentionally report `unknown`.
4. Keep NodOn `D2-01-0A` at channel `0` and out of the Ubiwizz guided flow.
5. For relay control or repeater configuration, treat ESP3 `OK` as dongle
   transport evidence only. Validate the physical result and power-cycle
   behavior separately on safe hardware.
6. Keep local association, repeater experiments, and mains work outside routine
   Home Assistant deployment. Do not claim hardware certification from this
   integration's tests or diagnostics.

## References

- [Ubiwizz UBID1507C manual][ubiwizz-manual]
- [Ubiwizz UBID1507C product page][ubiwizz-product]
- [EnOcean Equipment Profiles specification][eep-spec], F6-10-00 and D5-00-01
- [Home Assistant EnOcean documentation][ha-enocean]

[ubiwizz-manual]: https://ubiwizz.com/index.php?controller=attachment&id_attachment=927
[ubiwizz-product]: https://ubiwizz.com/l-offre-produits-ubiwizz/11905-micromodule-radio-enocean-2-canaux-2x5a.html
[eep-spec]: https://www.enocean-alliance.org/wp-content/uploads/2017/05/EnOcean_Equipment_Profiles_EEP_v2.6.7_public.pdf
[ha-enocean]: https://www.home-assistant.io/integrations/enocean/

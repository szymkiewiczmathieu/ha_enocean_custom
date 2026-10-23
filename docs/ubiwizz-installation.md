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

### UTE association from the module's request

The UBID1507C manual describes the local physical sequence: make three short
`PRESS` presses to enter association mode; for output 2, make one further short
press before operating the compatible transmitter. Verify the actual module
revision and LED behavior in the manual.

For Home Assistant association, select **Associate a Ubiwizz module from its
UTE request**, enter the module EURID from its label (a typed `AA:BB:CC:DD` ID
or supported 30S/1P label), and choose relay output `0` or `1`. The output
choice selects the stored row only; it is never written into a UTE telegram.
Submitting the form arms a listener bounded to 60 seconds and sends no radio.
Confirm the second form, then start the local PRESS sequence.

The module transmits its UTE teach-in request (RORG `D4`, CMD0). Home
Assistant answers only when every check passes:

| Check         | Accepted                                                                              |
| ------------- | ------------------------------------------------------------------------------------- |
| Sender        | exactly the entered EURID                                                             |
| Request       | teach-in (not delete), bidirectional, response expected                               |
| Profile       | `D2-01-08` or `D2-01-12`, manufacturer `0x046`                                        |
| DB5 (channel) | the selected output, `0xFF` (all channels), or `0x02` (the documented field capture)  |
| Freshness     | parsed less than 500 ms before the check                                              |
| Base ID       | resolved on the dongle                                                                |
| Identity      | the `(EURID, output)` row does not exist yet, or is a manual placeholder to promote  |

Any other telegram is ignored silently; the received DB5 value is logged at
debug level. A matching request that is already too old aborts the flow with
"deadline missed" and is deliberately not answered.

The single response is a directed standard UTE telegram whose data bytes
DB5..DB0 are copied from the request, sent from the resolved dongle Base ID to
the module EURID:

```text
D4 91 [DB5 from request] 46 00 [12 or 08] 01 D2 [dongle Base ID] 00
optional: 03 [module EURID] FF 00
```

The listener closes before the response is queued, so one session answers at
most one request. The switch row is saved only after the dongle reports the
physical serial write of that exact packet and ESP3 `OK`, within 30 seconds;
the stored EEP, manufacturer, and evidence come from the module's own request
(`radio_declared`, `exact`). A window that expires without a matching request,
an unavailable Base ID, a queue refusal, a missing write confirmation, or a
missing or rejected ESP3 result each end the flow without saving anything.
ESP3 `OK` proves only that the USB dongle transmitted the response; it cannot
prove the module retained the association. Verify normal directed D2 feedback
and safe `turn_on`/`turn_off` control afterwards.

This association action never broadcasts UTE and never sends a D2 relay
command. Normal `turn_on` and `turn_off` control remains the separate directed
D2 path, and ESP3 `OK` on that path is also only transport evidence.

### Passive radio test

**Passively test a module radio signal** arms the same bounded listener for an
entered EURID and reports whether one complete UTE or non-UTE telegram from
that module reached the dongle. It never transmits, never creates a device, and
never changes options. Use it to tell a module that is not heard at all from a
module that speaks an unexpected protocol before retrying the association.

## Repeater boundary

The options-flow **Ubiwizz repeater diagnostic** is intentionally no-radio. It
accepts only documented candidate labels and levels for an operator note; it
reads no repeater state, creates no entity, persists no request, and transmits
no ERP1 or ESP3 packet.

The integration exposes no `repeater_set_level` service. Remote repeater writes
remain blocked because the supplied Ubiwizz documentation does not define a
module-specific protocol, default state, read-back, or persistence behavior.
`CO_WR_REPEATER` configures the USB gateway, not a Ubiwizz module.

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
5. For relay control, treat ESP3 `OK` as dongle transport evidence only. Validate
   the physical result and power-cycle behavior separately on safe hardware.
6. Keep local association, repeater diagnostics, and mains work outside routine
   Home Assistant deployment. Remote Ubiwizz repeater writes are unsupported.

## References

- [Ubiwizz UBID1507C manual][ubiwizz-manual]
- [Ubiwizz UBID1507C product page][ubiwizz-product]
- [EnOcean Serial Protocol 3 specification][esp3-spec], ERP1 framing and optional data
- [EnOcean Equipment Profiles specification][eep-spec], F6-10-00, D5-00-01, and D2-01
- [Home Assistant EnOcean documentation][ha-enocean]

[ubiwizz-manual]: https://ubiwizz.com/index.php?controller=attachment&id_attachment=927
[ubiwizz-product]: https://ubiwizz.com/l-offre-produits-ubiwizz/11905-micromodule-radio-enocean-2-canaux-2x5a.html
[esp3-spec]: https://www.enocean.com/wp-content/uploads/Knowledge-Base/EnOceanSerialProtocol3-1.pdf
[eep-spec]: https://www.enocean-alliance.org/wp-content/uploads/2017/05/EnOcean_Equipment_Profiles_EEP_v2.6.7_public.pdf
[ha-enocean]: https://www.home-assistant.io/integrations/enocean/

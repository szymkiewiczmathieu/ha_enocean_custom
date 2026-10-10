# Mathieu's Ubiwizz EnOcean installation

This is an installation profile for the documented equipment: four Ubiwizz
UBID1507C two-channel micromodules, incoming HOPPE/Ubiwizz window handles,
existing EnOcean switches/sensors, and existing NodOn SmartPlugs. It is a
configuration and diagnostics guide, not a hardware certification or a remote
commissioning protocol.

## Scope and source boundary

| Equipment | Explicit profile/configuration | Integration behavior |
| --- | --- | --- |
| Ubiwizz UBID1507C | `ubiwizz_ubid1507c`, `D2-01-12`, two output entities: channels `0` and `1` | Default `switch` entities; D2 output feedback is decoded per channel. |
| HOPPE/Ubiwizz connected handle | `F6-10-00`, sensor `device_class: windowhandle` | Reports `closed`, `open`, `tilt`, or `unknown`. |
| Magnetic/contact transmitter | `D5-00-01`, sensor `device_class: contact` | Reports `open` or `closed`; 1BS teach-in values do not change state. |
| Existing NodOn SmartPlug | `D2-01-0A`, one output channel `0` | Existing switch path remains unchanged; assisted Ubiwizz commissioning refuses this EEP. |

The UBID1507C profile is selected explicitly in configuration. It is **not**
inferred from a D2 telegram, EURID, QR code, or the common `D2-01-12` EEP.
The official Ubiwizz material documents the two 5 A outputs and `D2-01-12`,
but an EEP alone is not a product identity.

- [Official UBID1507C manual][ubiwizz-manual]
- [Official UBID1507C product page][ubiwizz-product]

## UBID1507C output entities and feedback

Configure one `switch` per output. The profile bounds the channels to `0` and
`1`; a generic `D2-01-12` switch remains 0–31 because no product model is
inferred for it.

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

Use the same physical sender ID for both outputs and distinct channel-aware
entity IDs. A D2-01 `CMD 0x4` Actuator Status Response changes only the entity
with the matching channel. The status output value is the actual entity state:
`0` is off and any value above `0` is on. Its `d2_channel`,
`d2_output_value`, power-failure fields, and `last_status` remain diagnostic
feedback.

An ESP3 `OK` acknowledges delivery to the USB dongle only. It never changes a
UBID1507C entity state. The entity remains unknown until matching D2 status is
received, so a local wall-switch change can synchronize Home Assistant without
an optimistic state claim.

The directed D2 command already implemented by the integration is the only
mains-control path documented here. This guide adds no raw packet service,
new relay encoding, or action for a NodeOn plug. Do not test outputs on a live
mains circuit merely to populate a state; use normal, safe installation and
hardware-validation procedures.

### Local association guidance

The Ubiwizz manual describes local association with the module's `PRESS`
button: make three short presses to enter association mode; for the second
output, make one further short press to move to channel 2, then operate the
compatible transmitter. Treat this as a physical manual procedure. Verify the
actual module revision and LED behavior from the manual before operating it.

The guided UI relay path records an explicit UBID1507C profile and channels
`0`/`1`, then uses the existing directed D2 path only. Its confirmation remains
strict: a queued command, ESP3 `OK`, and a later matching D2 `CMD 0x4`
ON status are all required. That proves neither permanent association nor
physical product identity; power-cycle and hardware-release validation remain
outside this repository.

## Incoming HOPPE/Ubiwizz handles: F6-10-00

Create a sensor with `device_class: windowhandle` after explicitly selecting
or declaring `F6-10-00`. Ordinary RPS telegrams do not declare their EEP, so
a learned sender is not silently classified as a handle.

```yaml
sensor:
  - platform: enocean_custom
    name: Bedroom handle
    id: [0x05, 0x06, 0x07, 0x08] # Replace with the handle EURID
    device_class: windowhandle
```

The F6-10-00 decoder follows the EEP high nibble of RPS DB0; its low nibble is
explicitly ignored because the profile marks those bits as don't-care:

| DB0 high nibble | Handle state |
| --- | --- |
| `0xC` or `0xE` | `open` |
| `0xD` | `tilt` |
| `0xF` | `closed` |
| anything else, or a truncated RPS telegram | `unknown` |

This mapping is based on the EnOcean Alliance F6-10-00 HOPPE profile's final
movement direction: left/right means open, up tilt, and down closed. It is
covered by payload tests that vary the ignored low nibble.

## D5-00-01 contacts

Use the new first-class `contact` sensor class for a 1BS contact. The old
`shuttercontact` class remains an alias for existing configurations and
entities.

```yaml
sensor:
  - platform: enocean_custom
    name: Pantry contact
    id: [0x09, 0x0A, 0x0B, 0x0C] # Replace with the contact EURID
    device_class: contact
```

| D5 DB0 | Contact state |
| --- | --- |
| `0x08` | `open` |
| `0x09` | `closed` |
| `0x00` or `0x01` (1BS teach-in) | ignored; retained state |

## Diagnostics and safety checklist

1. Record each EURID, room, module, output channel, and intended load before
   adding entities. Do not copy the example IDs above.
2. For each UBID1507C, create the two explicit profile rows and check that
   feedback for one channel never changes the other entity.
3. For each handle, record one closed, open, and tilt telegram before relying
   on an automation; unsupported RPS values appear as `unknown` instead of
   reusing a stale position.
4. Keep NodOn `D2-01-0A` as channel `0` only. Do not select it in the Ubiwizz
   guided commissioning path.
5. The `Ubiwizz repeater diagnostic` remains a no-radio boundary. The supplied
   Ubiwizz sources do not provide a remote repeater command, read-back,
   default-state claim, or packet sequence. It therefore exposes no write and
   sends no ERP1/ESP3 packet. Do not substitute the USB dongle's repeater
   command: that would configure the gateway, not a Ubiwizz module.
6. Keep repeaters, local association, and mains work separate from Home
   Assistant deployment. No live HA deployment or hardware action is performed
   by this repository change.

## References

- [Ubiwizz UBID1507C manual][ubiwizz-manual]
- [Ubiwizz UBID1507C product page][ubiwizz-product]
- [EnOcean Equipment Profiles specification][eep-spec], F6-10-00 (HOPPE) and
  D5-00-01 (1BS contact)
- [Home Assistant EnOcean documentation][ha-enocean], which lists F6-10-00
  HOPPE handles as `windowhandle` sensors.

[ubiwizz-manual]: https://ubiwizz.com/index.php?controller=attachment&id_attachment=927
[ubiwizz-product]: https://ubiwizz.com/l-offre-produits-ubiwizz/11905-micromodule-radio-enocean-2-canaux-2x5a.html
[eep-spec]: https://www.enocean-alliance.org/wp-content/uploads/2017/05/EnOcean_Equipment_Profiles_EEP_v2.6.7_public.pdf
[ha-enocean]: https://www.home-assistant.io/integrations/enocean/

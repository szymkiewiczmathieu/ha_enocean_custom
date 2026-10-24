"""EXPERIMENTAL: A5-20-01 and A5-20-06 bi-directional valve actuators.

**Experimental — needs hardware validation.** Disabled by default and never
offered by the UI "add device" flow (YAML only, and only with an explicit
``experimental_valve_ack: true``, see ``schema.py``). Unlike A5-20-04 (which
this repository drives from its own periodic PI-loop send), these two
profiles are *polled by the valve itself*: the actuator contacts the
controller every RF cycle with a direction-1 status telegram and expects a
direction-2 reply inside one second, or it falls back to running its
internal thermostat autonomously, ignoring the controller from then on
(EEP.xml A5-20-01; the official A5-20-06 EEP proposal, EH4/Micropelt, V0.6,
2018-02-28, "Maximum response time must be less than 1 second"). That
budget rules out ``climate.py``'s existing async control-commit machinery
(a lock, a Future, potentially other awaits): the reply here is built and
queued synchronously, directly inside the dispatcher callback that decodes
the status telegram, with no ``await`` and no executor hop in between.

Byte layouts, verbatim from the sources above:

A5-20-01, direction 1 (ACT -> RCU), DB3..DB0:
    DB3        CV   Current valve position, 0..100 %  (101..255 reserved)
    DB2.7      SO   Service on
    DB2.6      ENIE Energy input enabled
    DB2.5      ES   Energy storage sufficiently charged
    DB2.4      BCAP Battery capacity low (change battery soon)
    DB2.3      CCO  Contact / cover open
    DB2.2      FTS  Temperature sensor failure
    DB2.1      DWO  Window open detected
    DB2.0      ACO  Actuator obstructed
    DB1        TMP  Temperature, 0..255 -> 0..40 degC

A5-20-01, direction 2 (RCU -> ACT), DB3..DB0, valve-position mode only (SPS=0):
    DB3        SP   Valve position, 0..100 %
    DB2        TMP  Room temperature from RCU, 0..255 -> 0..40 degC
    DB1        all flags 0 (RIN/LFS/VO/VC/SB/SPS/SPN/RCU)
    DB0.3      LRN bit = 1 (data telegram)

A5-20-06, direction 1 (ACT -> RCU), DB3..DB0:
    DB3        CV   Current valve position, 0..100 %  (101..255 reserved)
    DB2.7      LOM  Local Offset Mode (0 relative / 1 absolute)
    DB2.6-0    LO   Local offset (7-bit; diagnostic only here)
    DB1        TMP  Ambient (TSL=0, 0..80->0..40 degC) or feed (TSL=1,
                    0..160->0..80 degC) temperature; 0xFF = sensor failure
    DB0.7      TSL  Temperature selection (0 ambient, 1 feed)
    DB0.6      ENIE Energy input enabled (harvesting active)
    DB0.5      ES   Energy storage sufficiently charged
    DB0.4      DWO  Window open detected
    DB0.3      LRNB LRN bit (0 teach-in, 1 data)
    DB0.2      RCE  Radio communication error (>=6 consecutive failures)
    DB0.1      RSS  Weak radio signal (<= -77 dBm)
    DB0.0      ACO  Actuator obstructed

A5-20-06, direction 2 (RCU -> ACT), valve-position mode only (SPS=0):
    DB3        SP   Valve position, 0..100 %
    DB2        TMP  Room temperature from RCU, 0..160 -> 0..40 degC (clipped
                    to 1..160 so the "use internal sensor" sentinels 0/0xFF
                    are never emitted by accident)
    DB1        all flags 0 (REF/RFC/SB/SPS/TSL/SBY): AUTO interval, ambient
               temperature requested, no reference-run, no standby
    DB0.3      LRN bit = 1 (data telegram)

4BS Teach-In, Variation 3 (bidirectional), EnOcean Equipment Profiles system
specification v3.1 section 3.2.3, cross-checked bit for bit against this
repository's own already-field-verified A5-20-04 response
(``climate.py``'s ``[0x80, 0x20, 0x00, 0xF0]``, FUNC 0x20 TYPE 0x04
manufacturer 0 -> this module's formula reproduces it exactly):
    DB3 = (FUNC & 0x3F) << 2 | (TYPE >> 5) & 0x03
    DB2 = (TYPE & 0x1F) << 3 | (manufacturer >> 8) & 0x07
    DB1 = manufacturer & 0xFF
    DB0 = 0xF0 (telegram with EEP+manufacturer ID, EEP supported, sender ID
          stored, response, teach-in telegram)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from .enocean_library.protocol.constants import RORG

FUNC_HVAC_COMPONENTS: Final = 0x20
TYPE_A5_20_01: Final = 0x01
TYPE_A5_20_06: Final = 0x06


def _valid_bs4_data(data: Any) -> bool:
    return (
        isinstance(data, (list, tuple))
        and len(data) == 10  # RORG + DB3..DB0 + 4 sender bytes + status
        and data[0] == RORG.BS4
        and all(
            isinstance(byte, int) and not isinstance(byte, bool) and 0 <= byte <= 0xFF
            for byte in data
        )
    )


def bidirectional_teach_in_response(
    func: int, type_: int, manufacturer: int = 0
) -> list[int]:
    """Return the 4BS Variation-3 bidirectional teach-in response DB3..DB0."""
    db3 = ((func & 0x3F) << 2) | ((type_ >> 5) & 0x03)
    db2 = ((type_ & 0x1F) << 3) | ((manufacturer >> 8) & 0x07)
    db1 = manufacturer & 0xFF
    return [db3, db2, db1, 0xF0]


@dataclass(frozen=True, slots=True)
class A52001Status:
    """One decoded A5-20-01 direction-1 (ACT -> RCU) telegram."""

    valve_position: int | None
    service_on: bool
    energy_input_enabled: bool
    energy_storage_charged: bool
    battery_capacity_low: bool
    contact_cover_open: bool
    temperature_sensor_failure: bool
    window_open: bool
    obstructed: bool
    temperature: float | None


def decode_a5_20_01(data: Any) -> A52001Status | None:
    """Decode an A5-20-01 direction-1 telegram, or None (incl. teach-in)."""
    if not _valid_bs4_data(data):
        return None
    db3, db2, db1, db0 = data[1:5]
    if not db0 & 0x08:  # LRN bit: 0 = teach-in telegram, never treated as data
        return None
    return A52001Status(
        valve_position=db3 if db3 <= 100 else None,
        service_on=bool(db2 & 0x80),
        energy_input_enabled=bool(db2 & 0x40),
        energy_storage_charged=bool(db2 & 0x20),
        battery_capacity_low=bool(db2 & 0x10),
        contact_cover_open=bool(db2 & 0x08),
        temperature_sensor_failure=bool(db2 & 0x04),
        window_open=bool(db2 & 0x02),
        obstructed=bool(db2 & 0x01),
        temperature=round(db1 * 40 / 255, 1),
    )


def encode_a5_20_01_command(position: int, room_temperature: float) -> list[int]:
    """Encode an A5-20-01 direction-2 command: valve-position mode (SPS=0)."""
    position = min(max(position, 0), 100)
    temp_raw = min(max(round(room_temperature * 255 / 40), 0), 255)
    return [position, temp_raw, 0x00, 0x08]


@dataclass(frozen=True, slots=True)
class A52006Status:
    """One decoded A5-20-06 direction-1 (ACT -> RCU) telegram."""

    valve_position: int | None
    local_offset_mode_absolute: bool
    local_offset_raw: int
    temperature: float | None
    feed_temperature_selected: bool
    energy_input_enabled: bool
    energy_storage_charged: bool
    window_open: bool
    radio_communication_error: bool
    weak_radio_signal: bool
    obstructed: bool


def decode_a5_20_06(data: Any) -> A52006Status | None:
    """Decode an A5-20-06 direction-1 telegram, or None (incl. teach-in)."""
    if not _valid_bs4_data(data):
        return None
    db3, db2, db1, db0 = data[1:5]
    if not db0 & 0x08:  # LRN bit: 0 = teach-in telegram, never treated as data
        return None
    feed = bool(db0 & 0x80)
    if db1 == 0xFF:
        temperature = None
    elif feed and db1 <= 160 or not feed and db1 <= 80:
        temperature = round(db1 / 2, 1)
    else:
        temperature = None
    return A52006Status(
        valve_position=db3 if db3 <= 100 else None,
        local_offset_mode_absolute=bool(db2 & 0x80),
        local_offset_raw=db2 & 0x7F,
        temperature=temperature,
        feed_temperature_selected=feed,
        energy_input_enabled=bool(db0 & 0x40),
        energy_storage_charged=bool(db0 & 0x20),
        window_open=bool(db0 & 0x10),
        radio_communication_error=bool(db0 & 0x04),
        weak_radio_signal=bool(db0 & 0x02),
        obstructed=bool(db0 & 0x01),
    )


def encode_a5_20_06_command(position: int, room_temperature: float) -> list[int]:
    """Encode an A5-20-06 direction-2 command: valve-position mode (SPS=0).

    The room temperature is clipped to 1..160 raw so the DB2 sentinels 0 and
    0xFF -- "use the actuator's own internal sensor" -- are never emitted by
    accident for a controller that has a real reading to send.
    """
    position = min(max(position, 0), 100)
    temp_raw = min(max(round(room_temperature * 4), 1), 160)
    return [position, temp_raw, 0x00, 0x08]

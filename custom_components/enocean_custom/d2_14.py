"""D2-14-30: smoke detector with air quality, comfort, temperature and humidity.

The vendored ``EEP.xml`` does not describe this profile. The layout below is
the table of EEP 2.6.8 (D2-14-30, submitter Nexelec); the bit positions agree
with the openHAB EnOcean binding (``D2_14_30.java``) and FHEM
(``10_EnOcean.pm``, ``multiFuncSensor.30``). It is a six-byte VLD payload,
MSB first::

    bit  0      smoke alarm
    bit  1      sensor fault
    bit  2      maintenance not done (maintenance due)
    bit  3      humidity out of the sensor's working range
    bit  4      temperature out of the sensor's working range
    bits 5-12   weeks since the last maintenance (0..250; 251..254 reserved,
                255 error)
    bits 13-14  energy storage: 0 high, 1 medium, 2 low, 3 critical
    bits 15-22  remaining product lifetime in months (0..120; 121..254
                reserved, 255 error)
    bits 23-30  temperature, 0..250 -> 0..50 °C (251..254 reserved, 255 error)
    bits 31-38  relative humidity, 0..200 -> 0..100 % (201..254 reserved,
                255 error)
    bits 39-40  hygrothermal comfort: 0 good, 1 medium, 2 bad, 3 error
    bits 41-43  indoor air analysis: 0 optimal, 1 dry, 2 humidity high,
                3 temperature and humidity high, 4 temperature or humidity
                out of the analysis range, 5..6 reserved, 7 error

Reserved and error values decode to ``None``: the entity keeps its last
valid reading. Nothing here has been checked against a real detector yet:
the matrix marks the profile as implemented, not validated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from .enocean_library.protocol.constants import RORG

D2_14_30_PAYLOAD_BYTES: Final = 6

ENERGY_STORAGE_STATES: Final = ("high", "medium", "low", "critical")
COMFORT_STATES: Final = ("good", "medium", "bad", "error")
AIR_QUALITY_STATES: Final = (
    "optimal",
    "dry",
    "humidity_high",
    "temperature_humidity_high",
    "out_of_range",
    "error",
)
# EEP 2.6.8, IAQTH (offset 41): 5 and 6 are reserved and decode to nothing.
_AIR_QUALITY_BY_CODE: Final = {
    0: "optimal",
    1: "dry",
    2: "humidity_high",
    3: "temperature_humidity_high",
    4: "out_of_range",
    7: "error",
}
# Highest valid raw value of each eight-bit field (EEP 2.6.8 valid ranges).
_MAX_MAINTENANCE_WEEKS: Final = 250
_MAX_REMAINING_LIFE_MONTHS: Final = 120
_MAX_TEMPERATURE_RAW: Final = 250
_MAX_HUMIDITY_RAW: Final = 200


@dataclass(frozen=True, slots=True)
class D21430Status:
    """One decoded D2-14-30 telegram."""

    smoke_alarm: bool
    sensor_fault: bool
    maintenance_due: bool
    humidity_out_of_range: bool
    temperature_out_of_range: bool
    maintenance_weeks: int | None
    energy_storage: str
    remaining_life_months: int | None
    temperature: float | None
    humidity: float | None
    comfort: str
    air_quality: str | None


def decode_d2_14_30(data: Any) -> D21430Status | None:
    """Decode the ERP1 data bytes of a D2-14-30 telegram, or None."""
    if (
        not isinstance(data, (list, tuple))
        or len(data) != 1 + D2_14_30_PAYLOAD_BYTES + 5
        or data[0] != RORG.VLD
    ):
        return None
    if any(
        isinstance(byte, bool) or not isinstance(byte, int) or not 0 <= byte <= 0xFF
        for byte in data
    ):
        return None
    b0, b1, b2, b3, b4, b5 = data[1:7]
    weeks = ((b0 & 0x07) << 5) | (b1 >> 3)
    remaining = ((b1 & 0x01) << 7) | (b2 >> 1)
    temperature_raw = ((b2 & 0x01) << 7) | (b3 >> 1)
    humidity_raw = ((b3 & 0x01) << 7) | (b4 >> 1)
    return D21430Status(
        smoke_alarm=bool(b0 & 0x80),
        sensor_fault=bool(b0 & 0x40),
        maintenance_due=bool(b0 & 0x20),
        humidity_out_of_range=bool(b0 & 0x10),
        temperature_out_of_range=bool(b0 & 0x08),
        maintenance_weeks=weeks if weeks <= _MAX_MAINTENANCE_WEEKS else None,
        energy_storage=ENERGY_STORAGE_STATES[(b1 & 0x06) >> 1],
        remaining_life_months=(
            remaining if remaining <= _MAX_REMAINING_LIFE_MONTHS else None
        ),
        temperature=(
            temperature_raw / 5 if temperature_raw <= _MAX_TEMPERATURE_RAW else None
        ),
        humidity=humidity_raw / 2 if humidity_raw <= _MAX_HUMIDITY_RAW else None,
        comfort=COMFORT_STATES[((b4 & 0x01) << 1) | (b5 >> 7)],
        air_quality=_AIR_QUALITY_BY_CODE.get((b5 >> 4) & 0x07),
    )

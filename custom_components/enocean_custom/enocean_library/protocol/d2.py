"""Helpers for EnOcean D2 Variable Length Data telegrams."""

from __future__ import annotations

from dataclasses import dataclass

from .constants import RORG


@dataclass(frozen=True)
class D201ActuatorStatus:
    """Decoded D2-01 Actuator Status Response (CMD 0x4)."""

    channel: int
    output_value: int
    power_failure_detection_enabled: bool
    power_failure_detected: bool
    over_current_switch_off: bool
    error_level: int
    local_control_enabled: bool


def parse_d2_01_actuator_status(data: list[int]) -> D201ActuatorStatus | None:
    """Decode a complete ERP1 radio payload carrying a D2-01 status response.

    The operational telegram does not carry FUNC/TYPE. The caller establishes
    that the sender uses D2-01; this decoder then validates the common D2-01
    CMD 0x4 wire format used by TYPE 0x12.
    """
    # EEP 2.6.7, §D2-01 CMD 0x4 "Actuator Status Response", pp. 135-136:
    # offsets 0..23 make a three-byte VLD payload. ERP1 appends sender
    # (4 bytes) and status (1 byte).
    if len(data) != 9 or data[0] != RORG.VLD:
        return None

    flags_command, channel_status, output_status = data[1:4]

    # EEP 2.6.7, §D2-01 CMD 0x4, p. 136, offsets 4..7: CMD 0x4 identifies an
    # actuator status response.
    if flags_command & 0x0F != 0x04:
        return None

    # EEP 2.6.7, §D2-01 CMD 0x4, p. 136, offsets 17..23: OV 0 means OFF,
    # 1..100 mean ON at that percentage, 101..126 are unused, and 127 means
    # invalid/not set. Invalid values are not state feedback.
    output_value = output_status & 0x7F
    if output_value > 100:
        return None

    # EEP 2.6.7, §D2-01 CMD 0x4, p. 136: PF/PFD are offsets 0/1, OC and EL
    # are offsets 8 and 9..10, I/O is offsets 11..15, and LC is offset 16.
    # Offsets are numbered MSB-first in the EEP tables.
    return D201ActuatorStatus(
        channel=channel_status & 0x1F,
        output_value=output_value,
        power_failure_detection_enabled=bool(flags_command & 0x80),
        power_failure_detected=bool(flags_command & 0x40),
        over_current_switch_off=bool(channel_status & 0x80),
        error_level=(channel_status >> 5) & 0x03,
        local_control_enabled=bool(output_status & 0x80),
    )

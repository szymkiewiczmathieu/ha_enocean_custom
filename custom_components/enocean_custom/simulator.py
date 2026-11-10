"""Admin-only, opt-in simulated telegrams through the real receive chain.

Presets contain only profile fields already implemented by this integration:
D5-00-01 contacts, a generic 8-bit A5 temperature, an F6-02 rocker, an F6-05-02
smoke alarm, and D2-05-00 CMD 0x4 position replies. No teach-in telegram and no
automatic radio response is generated. Simulations are never persisted.
"""

from __future__ import annotations

import re
from typing import Any, Final

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.service import async_register_admin_service

from .const import (
    CONF_ALLOW_SIMULATION,
    DOMAIN,
    EVENT_SIMULATED_TELEGRAM,
    LOGGER,
    SERVICE_SIMULATE,
)
from .d2_05 import CMD_REPLY, UNCHANGED
from .enocean_library.protocol.constants import PACKET, RORG
from .enocean_library.protocol.packet import RadioPacket
from .runtime import get_gateway
from .schema import exact_finite_int

PRESETS: Final = (
    "window_open",
    "window_closed",
    "temperature",
    "button",
    "smoke_alarm",
    "smoke_alarm_off",
    "cover_position",
)
_SENDER_TEXT = re.compile(
    r"^([0-9A-Fa-f]{2})[ :]([0-9A-Fa-f]{2})[ :]([0-9A-Fa-f]{2})[ :]([0-9A-Fa-f]{2})$"
)

SIMULATE_SCHEMA = vol.Schema(
    {
        vol.Required("preset"): vol.In(PRESETS),
        vol.Required("sender_id"): str,
        vol.Optional("position", default=50): vol.All(
            exact_finite_int, vol.Range(min=0, max=100)
        ),
    }
)


def parse_sender(value: Any) -> list[int]:
    """Parse exactly ``AA:BB:CC:DD`` (spaces also accepted), never an inferred ID."""
    if (
        not isinstance(value, str)
        or (match := _SENDER_TEXT.fullmatch(value.strip())) is None
    ):
        raise ValueError("sender_id must be four hexadecimal bytes")
    return [int(part, 16) for part in match.groups()]


def _received_optional() -> list[int]:
    # ESP3 2.1: receive sub-telegram 1, broadcast, -64 dBm, no security.
    return [0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00]


def build_simulated_packet(
    preset: str, sender: list[int], position: int = 50
) -> RadioPacket:
    """Build one documented inbound radio packet and mark its provenance."""
    if len(sender) != 4 or any(
        isinstance(byte, bool) or not isinstance(byte, int) or not 0 <= byte <= 255
        for byte in sender
    ):
        raise ValueError("sender must contain exactly four bytes")
    if preset == "window_open":
        rorg, payload, status = RORG.BS1, [0x08], 0x00
    elif preset == "window_closed":
        rorg, payload, status = RORG.BS1, [0x09], 0x00
    elif preset == "temperature":
        # A5-02-05: raw 118 -> 21.5 °C; DB0.3 LRN = data.
        rorg, payload, status = RORG.BS4, [0x00, 0x00, 118, 0x08], 0x00
    elif preset in ("button", "smoke_alarm"):
        # Rocker channel 1 upper / F6-05-02 alarm. The same payload is decoded
        # only according to the receiving row's declared profile.
        rorg, payload, status = RORG.RPS, [0x10], 0x30
    elif preset == "smoke_alarm_off":
        # F6-05-02 DB0 0x00 "Smoke Alarm OFF" (EEP 2.6.8): ends a simulated
        # alarm without waiting for the detector's own 20-minute repeat.
        rorg, payload, status = RORG.RPS, [0x00], 0x30
    elif preset == "cover_position":
        if (
            isinstance(position, bool)
            or not isinstance(position, int)
            or not 0 <= position <= 100
        ):
            raise ValueError("cover position must be an integer from 0 to 100")
        # EEP 2.6.7 D2-05-00 CMD 0x4 p. 159: POS counts 0 % open;
        # ANG=0x7F unknown, LOCK=0 normal, CHN=0. HA percentage is inverted.
        rorg = RORG.VLD
        payload = [100 - position, UNCHANGED, 0x00, CMD_REPLY]
        status = 0x00
    else:
        raise ValueError(f"unknown simulated telegram preset: {preset}")
    packet = RadioPacket(
        PACKET.RADIO_ERP1,
        data=[rorg, *payload, *sender, status],
        optional=_received_optional(),
    )
    packet.simulated = True
    return packet


async def _async_handle_simulate(call: ServiceCall) -> None:
    """Inject one preset if and only if the entry's explicit option allows it."""
    preset = call.data["preset"]
    LOGGER.warning("Simulated EnOcean telegram requested (preset=%s)", preset)
    gateway = get_gateway(call.hass)
    if gateway is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="gateway_not_loaded",
        )
    entry = call.hass.config_entries.async_get_entry(gateway.entry_id)
    if entry is None or entry.options.get(CONF_ALLOW_SIMULATION) is not True:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="simulation_disabled",
        )
    try:
        sender = parse_sender(call.data["sender_id"])
        packet = build_simulated_packet(preset, sender, call.data.get("position", 50))
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    # Publish provenance before any entity can react. Automations listening for
    # this event can always distinguish a drill from a physical smoke alarm.
    call.hass.bus.async_fire(
        EVENT_SIMULATED_TELEGRAM,
        {
            "simulated": True,
            "preset": preset,
            "sender_id": call.data["sender_id"],
        },
        context=call.context,
    )
    gateway.inject_simulated(packet)


def async_register_simulate_service(hass: HomeAssistant) -> None:
    """Register the admin-only action once, including while no entry is loaded."""
    if hass.services.has_service(DOMAIN, SERVICE_SIMULATE):
        return
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_SIMULATE,
        _async_handle_simulate,
        schema=SIMULATE_SCHEMA,
    )

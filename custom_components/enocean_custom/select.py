"""Select platform: gateway repeater and additive D2-01-0C pilot wire.

The gateway select follows serial read-back. The pilot-wire select transmits
only on an explicit user action, CMD 0x8 then CMD 0x9, and displays only a
matching CMD 0xA read-back. It never sends CMD 0x2 or CMD 0x5.
"""

from __future__ import annotations

from typing import Any, ClassVar, override

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .d2_01 import (
    PILOT_WIRE_OPTIONS,
    D201Target,
    decode_pilot_wire_mode,
    get_d2_01_runner,
)
from .device_rows import (
    add_entities_for_group,
    async_connect_subentry_setup,
    rows_by_subentry,
)
from .entity import EnOceanEntity, EnOceanGatewayEntity, get_gateway_base_id
from .identity import GATEWAY_KEY_REPEATER, d2_01_0c_pilot_wire_unique_id
from .runtime import get_gateway

# Nothing here polls; the state follows the dongle's read-back signal.
PARALLEL_UPDATES = 0

OPTION_OFF = "off"
OPTION_LEVEL_1 = "level_1"
OPTION_LEVEL_2 = "level_2"
REPEATER_OPTIONS = [OPTION_OFF, OPTION_LEVEL_1, OPTION_LEVEL_2]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create additive row selects and, for a real entry, the gateway select."""
    for subentry_id, rows in rows_by_subentry(entry):
        add_entities_for_group(
            async_add_entities, _pilot_wire_selects(rows), subentry_id
        )
    async_connect_subentry_setup(hass, entry, async_add_entities, _pilot_wire_selects)
    gateway = getattr(entry, "runtime_data", None)
    entry_id = getattr(entry, "entry_id", None)
    if gateway is not None and entry_id:
        async_add_entities([EnOceanGatewayRepeaterSelect(gateway, entry_id)])


def _pilot_wire_selects(rows: list[dict]) -> list[EnOceanD201PilotWireSelect]:
    """Build a select only for explicitly declared default D2-01-0C rows."""
    return [
        EnOceanD201PilotWireSelect(row).set_radio_metadata(row.get("radio_metadata"))
        for row in rows
        if row.get("platform") == "switch"
        and (row.get("switch_type") or "default") == "default"
        and (row.get("radio_metadata") or {}).get("eep") == "D2-01-0C"
    ]


class EnOceanD201PilotWireSelect(EnOceanEntity, SelectEntity):
    """Six-mode pilot-wire control whose state is exclusively CMD 0xA feedback."""

    _attr_has_entity_name = True
    _attr_translation_key = "d2_01_0c_pilot_wire"
    _attr_options: ClassVar[list[str]] = list(PILOT_WIRE_OPTIONS)

    def __init__(self, row: dict) -> None:
        """Bind one additive select to the actuator sender and configured channel."""
        super().__init__(row["id"], row["name"])
        self._target = D201Target(
            tuple(row["id"]),
            int(row.get("channel", 0)),
            str(row.get("name", "")),
            "D2-01-0C",
        )
        self._attr_unique_id = d2_01_0c_pilot_wire_unique_id(
            row["id"], self._target.channel
        )
        self._attr_current_option = None

    @override
    def value_changed(self, packet: Any) -> None:
        """Apply only a valid CMD 0xA; invalid PM values keep the old state."""
        mode = decode_pilot_wire_mode(packet)
        if mode is None:
            return
        self._attr_current_option = mode
        self.schedule_update_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Set then read back one mode; never optimistically update the state."""
        if option not in PILOT_WIRE_OPTIONS:
            raise ValueError(f"Unsupported pilot-wire option: {option}")
        gateway = get_gateway(self.hass)
        if gateway is None or not getattr(gateway, "available", False):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_rejected",
                translation_placeholders={"name": self.dev_name},
            )
        if get_gateway_base_id(gateway) is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="base_id_unavailable",
                translation_placeholders={"name": self.dev_name},
            )
        runner = get_d2_01_runner(self.hass, gateway.entry_id, gateway)
        outcome = await runner.async_pilot_wire_set(
            self._target, PILOT_WIRE_OPTIONS.index(option)
        )
        if outcome == "no_base_id":
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="base_id_unavailable",
                translation_placeholders={"name": self.dev_name},
            )
        if outcome == "rejected":
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_rejected",
                translation_placeholders={"name": self.dev_name},
            )
        if outcome != "answered" or self.current_option != option:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="pilot_wire_readback_timeout",
                translation_placeholders={"name": self.dev_name},
            )
        self.async_write_ha_state()


def repeater_option(state: Any) -> str | None:
    """Map a read-back to an option; a selective repeater shows its level."""
    if state is None:
        return None
    if not state.enabled:
        return OPTION_OFF
    return OPTION_LEVEL_2 if state.level == 2 else OPTION_LEVEL_1


class EnOceanGatewayRepeaterSelect(EnOceanGatewayEntity, SelectEntity):
    """Off / level 1 / level 2 repeater setting of the dongle."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = REPEATER_OPTIONS

    def __init__(self, gateway: Any, entry_id: str) -> None:
        """Bind the select to the dongle."""
        super().__init__(gateway, entry_id, GATEWAY_KEY_REPEATER)

    @property
    def current_option(self) -> str | None:
        """Return the last read-back, or None before the dongle answered."""
        return repeater_option(self._gateway.repeater)

    async def async_select_option(self, option: str) -> None:
        """Write the setting, then wait for the read-back that proves it."""
        enabled = option != OPTION_OFF
        level = 2 if option == OPTION_LEVEL_2 else 1
        await self._gateway.async_set_repeater(enabled, level)
        self.async_write_ha_state()

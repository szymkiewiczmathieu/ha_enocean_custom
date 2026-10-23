"""Select platform: the dongle's own repeater setting.

This is the USB gateway's ESP3 ``CO_WR_REPEATER`` / ``CO_RD_REPEATER`` pair,
documented in the serial protocol specification (2.5.11 and 2.5.12). It has
nothing to do with the repeater function of a Ubiwizz module, which stays
blocked (see ``ubiwizz_repeater.py``). The displayed option always comes from
the read-back, never from the write being accepted.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EnOceanGatewayEntity
from .identity import GATEWAY_KEY_REPEATER

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
    """Create the gateway repeater select for a real config entry."""
    gateway = getattr(entry, "runtime_data", None)
    entry_id = getattr(entry, "entry_id", None)
    if gateway is None or not entry_id:
        return
    async_add_entities([EnOceanGatewayRepeaterSelect(gateway, entry_id)])


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

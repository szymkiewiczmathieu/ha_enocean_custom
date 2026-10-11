"""Operator actions exposed as Home Assistant buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SERVICE_UTE_COMMISSION
from .device import EnOceanEntity
from .enocean_library.utils import combine_hex
from .schema import CONF_UI_DEVICES, valid_ui_devices
from .ubiwizz import UBID1507C


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Expose one commissioning button for each supported Ubiwizz output."""
    entities = []
    for row in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, [])):
        if row.get("platform") != "switch":
            continue
        profile = row.get("actuator_profile") or (
            UBID1507C.profile_id if row.get("switch_type") == "RPS" else None
        )
        if profile != UBID1507C.profile_id:
            continue
        entities.append(
            UTECommissionButton(
                hass,
                list(row["id"]),
                str(row.get("name") or "Ubiwizz"),
                int(row["channel"]),
            )
        )
    async_add_entities(entities)


class UTECommissionButton(EnOceanEntity, ButtonEntity):
    """Visible action delegating to the matching protected switch service."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:radio-tower"

    def __init__(
        self, hass: HomeAssistant, dev_id: list[int], name: str, channel: int
    ) -> None:
        """Store the exact switch identity instead of guessing an entity ID."""
        EnOceanEntity.__init__(self, dev_id, name)
        self.hass = hass
        self._channel = channel
        self._attr_name = f"Commission UTE — {name}"
        self._attr_unique_id = (
            f"{DOMAIN}_{''.join(f'{byte:02x}' for byte in dev_id)}_"
            f"{channel}_ute_commission"
        )

    async def async_press(self) -> None:
        """Resolve the channel-aware switch registry row and start its action."""
        target = er.async_get(self.hass).async_get_entity_id(
            "switch", DOMAIN, f"{combine_hex(self.dev_id)}-{self._channel}"
        )
        if target is None:
            raise HomeAssistantError(
                "The associated Ubiwizz switch entity is unavailable for UTE commissioning"
            )
        await self.hass.services.async_call(
            DOMAIN,
            SERVICE_UTE_COMMISSION,
            {"entity_id": target},
            blocking=True,
        )

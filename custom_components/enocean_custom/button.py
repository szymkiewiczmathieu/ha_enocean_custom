"""Button platform: actuator refresh and teach-in actions on the device page.

"Refresh state" transmits one D2-01 CMD 0x3 Actuator Status Query (and, for a
profile that reports measurements on query and has a power/energy sensor row,
one CMD 0x6 per measurement) and waits for the answer within the specification's
window (``d2_01.py``). The switch and sensors update themselves from the
actuator's reply; the button itself never changes a state.

Teach-in buttons contain no radio encoder. Each press resolves the target
light/climate registry entity and calls its existing entity service, preserving
the exact command path, validation and translated errors of that action.
Nothing is transmitted unless a button is pressed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SERVICE_SEND_TEACH_IN
from .d2_01 import D201Target, d2_01_targets, get_d2_01_runner
from .device_rows import (
    add_entities_for_group,
    async_connect_subentry_setup,
    rows_by_subentry,
)
from .entity import EnOceanEntity, get_gateway_base_id
from .identity import (
    climate_unique_id,
    d2_01_refresh_unique_id,
    light_unique_id,
    teach_in_button_identities,
)
from .runtime import get_gateway

# Pressing a button sends radio; nothing here polls.
PARALLEL_UPDATES = 0


def _refresh_buttons(rows: list[dict]) -> list[EnOceanD201RefreshButton]:
    """Build one refresh button per D2-01 actuator channel of a group of rows."""
    return [EnOceanD201RefreshButton(target) for target in d2_01_targets(rows)]


def _teach_in_buttons(rows: list[dict]) -> list[EnOceanTeachInButton]:
    """Build buttons that delegate to the light/climate teach-in services."""
    entities: list[EnOceanTeachInButton] = []
    for row in rows:
        platform = row["platform"]
        if platform not in ("light", "climate"):
            continue
        target_domain = platform
        target_unique_id = (
            light_unique_id(row["id"], row.get("sender_id"))
            if platform == "light"
            else climate_unique_id(row["id"])
        )
        actions = (
            ((SERVICE_SEND_TEACH_IN, "light_teach_in"),)
            if platform == "light"
            else (
                ("climate_teach_in_actor", "climate_teach_in_actor"),
                ("climate_teach_in_actor_switch", "climate_teach_in_actor_switch"),
            )
        )
        identities = teach_in_button_identities(row)
        for (service, translation_key), (_domain, unique_id) in zip(
            actions, identities, strict=True
        ):
            entities.append(
                EnOceanTeachInButton(
                    row["id"] or row.get("sender_id") or [],
                    row["name"],
                    target_domain=target_domain,
                    target_unique_id=target_unique_id,
                    service=service,
                    translation_key=translation_key,
                    unique_id=unique_id,
                )
            )
    return entities


def _all_buttons(rows: list[dict]) -> list[EnOceanEntity]:
    """Build every companion button of one device group."""
    return [*_refresh_buttons(rows), *_teach_in_buttons(rows)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create every companion button, one batch per device subentry."""
    for subentry_id, rows in rows_by_subentry(entry):
        add_entities_for_group(async_add_entities, _all_buttons(rows), subentry_id)
    async_connect_subentry_setup(hass, entry, async_add_entities, _all_buttons)


class EnOceanD201RefreshButton(EnOceanEntity, ButtonEntity):
    """Ask one D2-01 actuator channel for its status (and metering) on demand."""

    _attr_has_entity_name = True
    _attr_translation_key = "d2_01_refresh"
    _tracks_radio = False

    def __init__(self, target: D201Target) -> None:
        """Bind the button to one actuator channel."""
        super().__init__(list(target.dev_id), target.name)
        self._target = target
        self._attr_unique_id = d2_01_refresh_unique_id(
            list(target.dev_id), target.channel
        )
        self._attr_translation_placeholders = {"channel": str(target.channel)}

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Say which queries a press transmits, per the declared profile."""
        return {
            "d2_channel": self._target.channel,
            "queries": [
                "status",
                *(["energy"] if self._target.query_energy else []),
                *(["power"] if self._target.query_power else []),
            ],
        }

    def value_changed(self, packet: Any) -> None:
        """Do nothing: the switch and sensors decode the actuator's answer."""

    async def async_press(self) -> None:
        """Transmit the query pass for this one channel."""
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
        report = await runner.async_run([self._target], reason="button")
        if report.rejected and not report.sent:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_rejected",
                translation_placeholders={"name": self.dev_name},
            )


class EnOceanTeachInButton(EnOceanEntity, ButtonEntity):
    """Call one existing teach-in entity service for the same device."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _tracks_radio = False

    def __init__(
        self,
        dev_id: list[int],
        dev_name: str,
        *,
        target_domain: str,
        target_unique_id: str,
        service: str,
        translation_key: str,
        unique_id: str,
    ) -> None:
        """Store only registry/action identity, never a second radio encoder."""
        super().__init__(dev_id, dev_name)
        self._target_domain = target_domain
        self._target_unique_id = target_unique_id
        self._service = service
        self._attr_translation_key = translation_key
        self._attr_unique_id = unique_id

    def value_changed(self, packet: Any) -> None:
        """Do nothing: the target entity owns all radio feedback."""

    async def async_press(self) -> None:
        """Resolve the target now, then use its unchanged entity-service path."""
        entity_id = er.async_get(self.hass).async_get_entity_id(
            self._target_domain, DOMAIN, self._target_unique_id
        )
        if entity_id is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="teach_in_target_unavailable",
                translation_placeholders={"name": self.dev_name},
            )
        await self.hass.services.async_call(
            DOMAIN,
            self._service,
            {CONF_ENTITY_ID: entity_id},
            blocking=True,
        )

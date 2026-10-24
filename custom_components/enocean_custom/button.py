"""Button platform: "Refresh state" of one D2-01 actuator channel.

Pressing the button transmits one D2-01 CMD 0x3 Actuator Status Query (and,
for a profile that reports measurements on query and has a power/energy
sensor row, one CMD 0x6 per measurement) to that actuator and waits for the
answer within the specification's window (``d2_01.py``). The switch and the
sensors update themselves from the actuator's reply exactly as they do for
any other telegram; the button itself never changes a state. Nothing is
transmitted unless the button is pressed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .d2_01 import D201Target, d2_01_targets, get_d2_01_runner
from .device_rows import (
    add_entities_for_group,
    async_connect_subentry_setup,
    rows_by_subentry,
)
from .entity import EnOceanEntity, get_gateway_base_id
from .identity import d2_01_refresh_unique_id
from .runtime import get_gateway

# Pressing a button sends radio; nothing here polls.
PARALLEL_UPDATES = 0


def _refresh_buttons(rows: list[dict]) -> list[EnOceanD201RefreshButton]:
    """Build one refresh button per D2-01 actuator channel of a group of rows."""
    return [EnOceanD201RefreshButton(target) for target in d2_01_targets(rows)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the refresh buttons of every D2-01 actuator row, per device subentry."""
    for subentry_id, rows in rows_by_subentry(entry):
        add_entities_for_group(async_add_entities, _refresh_buttons(rows), subentry_id)
    async_connect_subentry_setup(hass, entry, async_add_entities, _refresh_buttons)


class EnOceanD201RefreshButton(EnOceanEntity, ButtonEntity):
    """Ask one D2-01 actuator channel for its status (and metering) on demand."""

    _attr_has_entity_name = True
    _attr_translation_key = "d2_01_refresh"

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

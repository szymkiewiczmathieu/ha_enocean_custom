"""Event platform: one companion ``event`` entity per RPS rocker sender.

Home Assistant's native ``event`` domain is the modern way to expose a
remote-control press: a translatable, typed timeline entry with no custom
device trigger needed to react to it from a dashboard or a template. It is
added *in addition to* the existing ``button_pressed`` bus event and the
native device triggers (``device_trigger.py``), which are both untouched
and keep firing exactly as before.

This entity never decodes a radio telegram itself. It listens to the same
``button_pressed`` bus event ``binary_sensor.EnOceanBinarySensor`` already
fires on every RPS telegram, the single source of truth for
press/release/which/onoff, so the two can never disagree. A rocker never
sends a dedicated "long press" telegram, so that event type is computed
locally: the time between a channel's press and its release is measured
against a fixed, non-configurable one-second threshold.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Final, override

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .binary_sensor import EVENT_BUTTON_PRESSED
from .enocean_library.utils import combine_hex
from .entity import EnOceanEntity
from .identity import A514_EEP, D2_14_30_EEP, SMOKE_EEP, rocker_event_unique_id
from .schema import CONF_UI_DEVICES, valid_ui_devices

# Nothing here polls, and reacting to the bus event never sends radio.
PARALLEL_UPDATES = 0

LONG_PRESS_SECONDS: Final = 1.0

# Matches the ``which`` values binary_sensor.py's action_map already assigns
# (0/1 single channel, 10 both channels pressed as one RPS bitfield telegram).
CHANNEL_NAMES: Final[dict[int, str]] = {0: "channel_0", 1: "channel_1", 10: "both"}
EVENT_TYPES: Final = tuple(
    f"{name}_{suffix}"
    for name in CHANNEL_NAMES.values()
    for suffix in ("pressed", "released", "long_pressed")
)
# EEPs whose primary binary_sensor entity is not EnOceanBinarySensor and so
# never fires button_pressed: offering this companion for them would be dead.
_NOT_A_ROCKER_EEPS: Final = frozenset((A514_EEP, SMOKE_EEP, D2_14_30_EEP))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one rocker event entity per sender that may fire button_pressed."""
    entities: list[EnOceanRockerEvent] = []
    seen: set[int] = set()
    for row in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, [])):
        if row["platform"] != "binary_sensor":
            continue
        metadata = row.get("radio_metadata") or {}
        if metadata.get("eep") in _NOT_A_ROCKER_EEPS:
            continue
        sender = combine_hex(row["id"])
        if sender in seen:
            continue
        seen.add(sender)
        entities.append(EnOceanRockerEvent(row["id"], row["name"]))
    async_add_entities(entities)


class EnOceanRockerEvent(EnOceanEntity, EventEntity):
    """Typed press/release/long-press timeline of one rocker sender."""

    _attr_has_entity_name = True
    _attr_translation_key = "rocker_event"
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types: ClassVar[list[str]] = list(EVENT_TYPES)

    def __init__(self, dev_id: list[int], dev_name: str) -> None:
        """Bind the companion event entity to one rocker sender."""
        super().__init__(dev_id, dev_name)
        self._attr_unique_id = rocker_event_unique_id(dev_id)
        self._pressed_at: dict[int, datetime] = {}

    @override
    async def async_added_to_hass(self) -> None:
        """Connect to the bus event binary_sensor.py already fires."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_BUTTON_PRESSED, self._handle_button_event)
        )

    @override
    def value_changed(self, packet: Any) -> None:
        """Do nothing: decoding happens once, in EnOceanBinarySensor."""

    @callback
    def _handle_button_event(self, event: Event) -> None:
        """Translate one button_pressed bus event into a typed event state."""
        data = event.data
        if data.get("id") != self.dev_id:
            return
        pushed = data.get("pushed")
        which = data.get("which")
        channel = CHANNEL_NAMES.get(which)
        if pushed is None or channel is None:
            return
        attributes = {
            "onoff": data.get("onoff"),
            "repeated_telegram": data.get("repeated_telegram"),
        }
        now = dt_util.utcnow()
        if pushed:
            self._pressed_at[which] = now
            self._trigger_event(f"{channel}_pressed", attributes)
            self.async_write_ha_state()
            return
        self._trigger_event(f"{channel}_released", attributes)
        self.async_write_ha_state()
        pressed_at = self._pressed_at.pop(which, None)
        if pressed_at is None:
            return
        if (now - pressed_at).total_seconds() >= LONG_PRESS_SECONDS:
            self._trigger_event(f"{channel}_long_pressed", attributes)
            self.async_write_ha_state()

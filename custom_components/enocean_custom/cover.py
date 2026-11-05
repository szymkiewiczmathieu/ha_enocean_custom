"""Cover platform for D2-05-00 roller shutters and blinds."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, LOGGER
from .d2_05 import (
    build_position_command,
    build_query_command,
    build_stop_command,
    parse_d2_05_00_status,
)
from .device_rows import (
    add_entities_for_group,
    async_connect_subentry_setup,
    rows_by_subentry,
)
from .entity import EnOceanEntity, get_gateway_base_id
from .identity import cover_unique_id, device_names_by_sender, row_names_the_device
from .runtime import get_gateway

PARALLEL_UPDATES = 0

STARTUP_QUERY_BASE_ID_WAIT_SECONDS = 20.0
# Local discipline, not an EEP deadline: let the stopped motor publish its own
# final reply first, then make one explicit CMD 0x3 request. No repeat timer.
POST_STOP_QUERY_DELAY_SECONDS = 2.0


def _cover_entities(rows: list[dict]) -> list[EnOceanCover]:
    """Build the D2-05-00 cover rows of one device group."""
    names_by_sender = device_names_by_sender(rows)
    entities: list[EnOceanCover] = []
    for row in rows:
        if row["platform"] != "cover":
            continue
        entity = EnOceanCover(
            row["id"], row["name"], row["channel"]
        ).set_radio_metadata(row.get("radio_metadata"))
        if row_names_the_device(row, names_by_sender):
            entity._attr_has_entity_name = True
            entity._attr_name = None
        entities.append(entity)
    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create covers stored in config subentries, including later additions."""
    for subentry_id, rows in rows_by_subentry(entry):
        add_entities_for_group(async_add_entities, _cover_entities(rows), subentry_id)
    async_connect_subentry_setup(hass, entry, async_add_entities, _cover_entities)


class EnOceanCover(EnOceanEntity, CoverEntity, RestoreEntity):
    """One D2-05-00 TYPE 0x00 actuator, with feedback-only position state."""

    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.STOP
    )
    _attr_translation_key = "d2_05_00"

    def __init__(self, dev_id: list[int], dev_name: str, channel: int = 0) -> None:
        """Initialize an actuator without assuming a position."""
        if isinstance(channel, bool) or not isinstance(channel, int) or channel != 0:
            raise ValueError("D2-05-00 supports only channel 0")
        super().__init__(dev_id, dev_name)
        self.channel = channel
        self._attr_name = dev_name
        self._attr_unique_id = cover_unique_id(dev_id, channel)
        self._attr_current_cover_position: int | None = None
        self._d2_angle: int | None = None
        self._d2_locking_mode: int | None = None
        self._last_command_esp3_accepted: bool | None = None
        self._command_sequence = 0
        self._startup_task: asyncio.Task[None] | None = None
        self._stop_query_unsub = None

    @property
    def is_closed(self) -> bool | None:
        """Return closed only when a real or restored position is known."""
        if self._attr_current_cover_position is None:
            return None
        return self._attr_current_cover_position == 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose only fields actually present in CMD 0x4 or ESP3 transport."""
        attributes: dict[str, Any] = {
            "d2_angle": self._d2_angle,
            "d2_locking_mode": self._d2_locking_mode,
            "last_status": self._last_status,
        }
        if self._last_command_esp3_accepted is not None:
            attributes["last_command_esp3_accepted"] = self._last_command_esp3_accepted
        return attributes

    async def async_added_to_hass(self) -> None:
        """Restore the last position, then make one bounded start-up query."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            restored = last.attributes.get(ATTR_CURRENT_POSITION)
            if (
                isinstance(restored, int)
                and not isinstance(restored, bool)
                and 0 <= restored <= 100
            ):
                self._attr_current_cover_position = restored
        self.async_on_remove(self._cancel_background_work)
        self._startup_task = self.hass.async_create_task(
            self._async_query_after_start(),
            f"EnOcean D2-05-00 start-up query for {self.entity_id}",
        )

    @callback
    def _cancel_background_work(self) -> None:
        if self._startup_task is not None and not self._startup_task.done():
            self._startup_task.cancel()
        self._startup_task = None
        if self._stop_query_unsub is not None:
            self._stop_query_unsub()
            self._stop_query_unsub = None

    async def _async_query_after_start(self) -> None:
        """Wait briefly for Base ID, then issue exactly one CMD 0x3."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STARTUP_QUERY_BASE_ID_WAIT_SECONDS
        while loop.time() < deadline:
            gateway = get_gateway(self.hass)
            if get_gateway_base_id(gateway) is not None:
                self._query_position()
                return
            await asyncio.sleep(0.05)
        LOGGER.warning(
            "D2-05-00 start-up query skipped for %s: dongle Base ID unresolved",
            self.dev_name,
        )

    def _query_position(self) -> None:
        gateway = get_gateway(self.hass)
        sender_id = get_gateway_base_id(gateway)
        if sender_id is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="base_id_unavailable",
                translation_placeholders={"name": self.dev_name},
            )
        self._send(build_query_command(sender_id, self.dev_id, self.channel))

    def _go_to(self, ha_position: int) -> None:
        gateway = get_gateway(self.hass)
        sender_id = get_gateway_base_id(gateway)
        if sender_id is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="base_id_unavailable",
                translation_placeholders={"name": self.dev_name},
            )
        self._send(
            build_position_command(sender_id, self.dev_id, self.channel, ha_position)
        )

    def _send(self, packet: Any, *, after_write=None) -> None:
        """Queue one packet; never infer the resulting position from the request."""
        self._command_sequence += 1
        sequence = self._command_sequence
        self._last_command_esp3_accepted = None

        def transport_result(accepted: bool) -> None:
            if sequence != self._command_sequence:
                return
            self._last_command_esp3_accepted = accepted
            self.schedule_update_ha_state()

        queued = self.send_command(
            packet.data,
            packet.optional,
            packet.packet_type,
            response_callback=transport_result,
            written_callback=after_write,
        )
        if not queued:
            self._last_command_esp3_accepted = False
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_rejected",
                translation_placeholders={"name": self.dev_name},
            )

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Request fully open (spec POS 0, HA 100)."""
        self._go_to(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Request fully closed (spec POS 100, HA 0)."""
        self._go_to(0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Request a Home Assistant open percentage."""
        self._go_to(kwargs[ATTR_POSITION])

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop now, then query once after the physical write plus a short delay."""
        gateway = get_gateway(self.hass)
        sender_id = get_gateway_base_id(gateway)
        if sender_id is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="base_id_unavailable",
                translation_placeholders={"name": self.dev_name},
            )
        if self._stop_query_unsub is not None:
            self._stop_query_unsub()
            self._stop_query_unsub = None
        self._send(
            build_stop_command(sender_id, self.dev_id, self.channel),
            after_write=self._schedule_post_stop_query,
        )

    @callback
    def _schedule_post_stop_query(self) -> None:
        self._stop_query_unsub = async_call_later(
            self.hass,
            POST_STOP_QUERY_DELAY_SECONDS,
            self._post_stop_query,
        )

    @callback
    def _post_stop_query(self, _now: Any) -> None:
        self._stop_query_unsub = None
        try:
            self._query_position()
        except HomeAssistantError:
            LOGGER.warning(
                "Post-stop D2-05-00 query for %s was rejected", self.dev_name
            )

    def value_changed(self, packet: Any) -> None:
        """Apply one matching CMD 0x4; unknown POS never erases a known value."""
        status = parse_d2_05_00_status(getattr(packet, "data", None))
        if status is None or status.channel != self.channel:
            return
        self._last_status = datetime.now(UTC).isoformat()
        self._d2_angle = status.angle
        self._d2_locking_mode = status.locking_mode
        if status.position is not None:
            self._attr_current_cover_position = status.position
        self.async_write_ha_state()

"""Admin-only EnOcean side panel and bounded live telegram stream.

The static JavaScript asset contains no data. Every runtime fact is returned by
an admin-decorated websocket command. Telegram recording is off until at least
one admin subscribes, is bounded to 100 in-memory entries, and is cleared when
the last subscription ends. No preference or telegram is persisted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import voluptuous as vol
from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import CONF_ALLOW_SIMULATION, DOMAIN, SIGNAL_RECEIVE_MESSAGE
from .d2_05 import parse_d2_05_00_status
from .device_rows import configured_rows
from .eep_catalog import generic_profile
from .enocean_library.protocol.constants import RORG
from .enocean_library.protocol.d2 import parse_d2_01_actuator_status
from .enocean_library.utils import combine_hex, to_hex_string
from .identity import ui_device_entity_identities
from .inbox import packet_observation
from .runtime import get_gateway

PANEL_URL_PATH: Final = "enocean_custom"
PANEL_COMPONENT: Final = "enocean-custom-panel"
PANEL_JS_URL: Final = "/api/enocean_custom/panel.js"
PANEL_COMMAND: Final = "enocean_custom/panel"
PANEL_STREAM_COMMAND: Final = "enocean_custom/telegram_stream/subscribe"
MASKED_ID: Final = "••:••:••:••"
MAX_TELEGRAMS: Final = 100
_DATA_PANEL_REGISTERED: Final = "enocean_custom_panel_registered"


def _format_sender(sender: int, anonymize: bool) -> str:
    if anonymize:
        return MASKED_ID
    return to_hex_string(list(sender.to_bytes(4, "big")))


def _summary(packet: Any, profile: dict[str, Any] | None) -> tuple[str | None, str]:
    """Decode only a profile declared by configuration, never infer one from ID."""
    data = getattr(packet, "data", [])
    rorg = data[0] if data else None
    eep = (profile or {}).get("eep")
    if rorg == RORG.RPS:
        if len(data) < 2:
            return eep, "RPS telegram"
        message_kind = getattr(packet, "status", 0) >> 4
        # Match binary_sensor.py: only T1=3 is pressed and T1=2 released.
        # Other ERP1 status values do not prove a rocker transition.
        action = {3: "pressed", 2: "released"}.get(message_kind, "unknown")
        if eep == "F6-05-02" and len(data) > 1:
            state = {0x00: "clear", 0x10: "alarm", 0x30: "battery low"}.get(data[1])
            if state:
                return eep, f"Smoke {state}"
        return eep, f"RPS {action} (0x{data[1]:02X})"
    if rorg == RORG.BS1 and len(data) > 1:
        state = {0x08: "open", 0x09: "closed"}.get(data[1])
        return eep, f"Contact {state}" if state else "1BS telegram"
    if rorg == RORG.BS4:
        if eep == "A5-20-04" and len(data) >= 5:
            if data[4] & 0x01:
                return eep, f"Valve failure 0x{data[3]:02X}"
            temperature = round(10 + data[3] * 20 / 255, 1)
            return eep, f"Valve {min(data[1], 100)} %, {temperature} °C"
        if eep:
            generic = generic_profile(eep)
            if generic is not None and (decoded := generic.decode(data)):
                facts = ", ".join(f"{key}={value}" for key, value in decoded.items())
                return eep, facts or "4BS data"
        return eep, "4BS data"
    if rorg == RORG.VLD:
        if eep == "D2-05-00":
            status = parse_d2_05_00_status(data)
            if status is not None:
                position = (
                    "unknown" if status.position is None else f"{status.position} %"
                )
                return eep, f"Cover {position} open, lock {status.locking_mode}"
        if eep and eep.startswith("D2-01-"):
            status = parse_d2_01_actuator_status(data)
            if status is not None:
                return eep, f"Channel {status.channel} output {status.output_value} %"
        return eep, "VLD telegram"
    if rorg == RORG.UTE:
        return eep, "UTE teach-in telegram"
    return eep, f"RORG 0x{rorg:02X}" if rorg is not None else "Radio telegram"


@dataclass(frozen=True, slots=True)
class TelegramRecord:
    """One bounded record; raw and sanitized views are derived per subscriber."""

    received_at: str
    sender_int: int
    rorg: int
    data: tuple[int, ...]
    sanitized_data: tuple[int, ...] | None
    rssi_dbm: int | None
    repeater_count: int | None
    simulated: bool
    eep: str | None
    summary: str


class TelegramMonitor:
    """Record only while an admin subscription exists; never persist anything."""

    def __init__(
        self, profile_for_sender: Callable[[int], dict[str, Any] | None] | None = None
    ) -> None:
        self._profile_for_sender = profile_for_sender or (lambda sender: None)
        self._records: list[TelegramRecord] = []
        self._listeners: list[tuple[Callable[[dict[str, Any]], None], bool]] = []

    @property
    def enabled(self) -> bool:
        """Return whether recording is currently enabled by a subscriber."""
        return bool(self._listeners)

    @callback
    def subscribe(
        self, listener: Callable[[dict[str, Any]], None], *, anonymize: bool
    ) -> Callable[[], None]:
        """Attach one subscriber and return its idempotent unsubscribe callback."""
        item = (listener, anonymize)
        self._listeners.append(item)
        subscribed = True

        def unsubscribe() -> None:
            nonlocal subscribed
            if not subscribed:
                return
            subscribed = False
            if item in self._listeners:
                self._listeners.remove(item)
            if not self._listeners:
                self._records.clear()

        return unsubscribe

    @callback
    def record(self, packet: Any) -> None:
        """Capture one packet from the real dispatcher and notify subscribers."""
        if not self._listeners:
            return
        observation = packet_observation(packet)
        if observation is None:
            return
        data = getattr(packet, "data", [])
        profile = self._profile_for_sender(observation.sender_id)
        eep, summary = _summary(packet, profile)
        record = TelegramRecord(
            received_at=datetime.now(UTC).isoformat(),
            sender_int=observation.sender_id,
            rorg=observation.rorg,
            data=tuple(data),
            sanitized_data=observation.payload,
            rssi_dbm=observation.rssi_dbm,
            repeater_count=observation.repeater_count,
            simulated=getattr(packet, "simulated", False) is True,
            eep=eep,
            summary=summary,
        )
        self._records.append(record)
        del self._records[:-MAX_TELEGRAMS]
        for listener, anonymize in tuple(self._listeners):
            listener(self._view(record, anonymize))

    def _view(self, record: TelegramRecord, anonymize: bool) -> dict[str, Any]:
        data = record.sanitized_data if anonymize else record.data
        return {
            "received_at": record.received_at,
            "sender": _format_sender(record.sender_int, anonymize),
            "rorg": f"0x{record.rorg:02X}",
            "data": list(data) if data is not None else None,
            "rssi_dbm": record.rssi_dbm,
            "repeater_count": record.repeater_count,
            "simulated": record.simulated,
            "eep": record.eep,
            "summary": record.summary,
        }

    def snapshot(self, *, anonymize: bool = True) -> list[dict[str, Any]]:
        """Return a copy of the bounded backlog in the caller's privacy mode."""
        return [self._view(record, anonymize) for record in self._records]

    @callback
    def close(self) -> None:
        """Disable recording and forget every observation."""
        self._listeners.clear()
        self._records.clear()


def _observation_payload(observation: Any, anonymize: bool) -> dict[str, Any]:
    return {
        "last_seen": observation.last_seen.isoformat(),
        "packet_count": observation.packet_count,
        "last_rssi_dbm": observation.last_rssi_dbm,
        "last_repeater_count": observation.last_repeater_count,
    }


def build_panel_snapshot(
    hass: HomeAssistant,
    entry: ConfigEntry,
    gateway: Any,
    *,
    anonymize: bool,
) -> dict[str, Any]:
    """Build the current read-only devices/nearby/gateway view on the HA loop."""
    rows = configured_rows(entry)
    by_sender: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_sender.setdefault(combine_hex(row["id"]), []).append(row)
    inbox = gateway.inbox
    devices: list[dict[str, Any]] = []
    for sender, sender_rows in by_sender.items():
        first = sender_rows[0]
        observation = inbox.get(sender) if inbox is not None else None
        platform, unique_id = ui_device_entity_identities(first)[0]
        entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
        state = hass.states.get(entity_id) if entity_id is not None else None
        devices.append(
            {
                "name": first["name"],
                "sender": _format_sender(sender, anonymize),
                "entity_id": entity_id,
                "state": state.state if state is not None else None,
                "available": gateway.available,
                "platforms": sorted({row["platform"] for row in sender_rows}),
                "eep": (first.get("radio_metadata") or {}).get("eep"),
                **(
                    _observation_payload(observation, anonymize)
                    if observation is not None
                    else {
                        "last_seen": None,
                        "packet_count": 0,
                        "last_rssi_dbm": None,
                        "last_repeater_count": None,
                    }
                ),
            }
        )
    nearby: list[dict[str, Any]] = []
    if inbox is not None:
        for sender, observation in inbox.observations():
            nearby.append(
                {
                    "sender": _format_sender(sender, anonymize),
                    "configured": sender in by_sender,
                    "rorgs": sorted(f"0x{rorg:02X}" for rorg in observation.rorgs),
                    **_observation_payload(observation, anonymize),
                }
            )
    info = gateway.gateway_info
    diagnostics = gateway.diagnostics()
    monitor = gateway.telegram_monitor
    return {
        "devices": devices,
        "nearby": nearby,
        "gateway": {
            "available": gateway.available,
            "worker_alive": diagnostics.get("thread_alive", False),
            "app_version": info.app_version if info else None,
            "api_version": info.api_version if info else None,
            "chip_version": info.chip_version if info else None,
            "app_description": info.app_description if info else None,
            "base_id": (
                _format_sender(combine_hex(gateway.base_id), anonymize)
                if gateway.base_id
                else None
            ),
            "repeater": (
                {
                    "enabled": gateway.repeater.enabled,
                    "level": gateway.repeater.level,
                }
                if gateway.repeater
                else None
            ),
            "received_radio_packets": gateway.received_radio_packets,
            "last_packet_at": (
                gateway.last_packet_at.isoformat() if gateway.last_packet_at else None
            ),
            "last_error": diagnostics.get("last_error"),
        },
        "stream": {
            "enabled": bool(monitor and monitor.enabled),
            "size": len(monitor.snapshot()) if monitor else 0,
        },
        "simulation_allowed": entry.options.get(CONF_ALLOW_SIMULATION) is True,
        "adoption_url": "/config/integrations/integration/enocean_custom",
    }


def _loaded_panel_context(hass: HomeAssistant) -> tuple[ConfigEntry, Any] | None:
    gateway = get_gateway(hass)
    if gateway is None or gateway.entry_id is None:
        return None
    entry = hass.config_entries.async_get_entry(gateway.entry_id)
    if entry is None:
        return None
    return entry, gateway


@websocket_api.websocket_command(
    {
        vol.Required("type"): PANEL_COMMAND,
        vol.Optional("anonymize", default=True): bool,
    }
)
@websocket_api.require_admin
@callback
def ws_panel_snapshot(
    hass: HomeAssistant, connection: Any, msg: dict[str, Any]
) -> None:
    """Return one admin snapshot; no unauthenticated HTTP data endpoint exists."""
    context = _loaded_panel_context(hass)
    if context is None:
        connection.send_message(
            websocket_api.error_message(
                msg["id"], "gateway_not_loaded", "The EnOcean gateway is not loaded"
            )
        )
        return
    entry, gateway = context
    connection.send_message(
        websocket_api.result_message(
            msg["id"],
            build_panel_snapshot(hass, entry, gateway, anonymize=msg["anonymize"]),
        )
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): PANEL_STREAM_COMMAND,
        vol.Optional("anonymize", default=True): bool,
    }
)
@websocket_api.require_admin
@callback
def ws_telegram_subscribe(
    hass: HomeAssistant, connection: Any, msg: dict[str, Any]
) -> None:
    """Enable the bounded stream for one admin connection and send its backlog."""
    context = _loaded_panel_context(hass)
    if context is None:
        connection.send_message(
            websocket_api.error_message(
                msg["id"], "gateway_not_loaded", "The EnOcean gateway is not loaded"
            )
        )
        return
    _entry, gateway = context
    monitor = gateway.telegram_monitor
    message_id = msg["id"]
    if (previous := connection.subscriptions.pop(message_id, None)) is not None:
        previous()
    anonymize = msg["anonymize"]
    unsubscribe = monitor.subscribe(
        lambda record: connection.send_message(
            websocket_api.event_message(message_id, record)
        ),
        anonymize=anonymize,
    )
    connection.subscriptions[message_id] = unsubscribe
    connection.send_message(
        websocket_api.result_message(
            message_id,
            {
                "enabled": True,
                "anonymize": anonymize,
                "telegrams": monitor.snapshot(anonymize=anonymize),
            },
        )
    )


def async_setup_entry_panel(
    hass: HomeAssistant, entry: ConfigEntry, gateway: Any
) -> None:
    """Attach one entry-scoped monitor to the real receive dispatcher."""
    if getattr(gateway, "telegram_monitor", None) is not None:
        return

    def profile_for_sender(sender: int) -> dict[str, Any] | None:
        for row in configured_rows(entry):
            if combine_hex(row["id"]) == sender:
                return {
                    "eep": (row.get("radio_metadata") or {}).get("eep"),
                    "platform": row["platform"],
                    "device_class": row.get("device_class"),
                }
        return None

    monitor = TelegramMonitor(profile_for_sender)
    gateway.telegram_monitor = monitor
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_RECEIVE_MESSAGE, monitor.record)
    )
    entry.async_on_unload(monitor.close)


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the static shell, admin panel and admin websocket commands once."""
    if hass.data.get(_DATA_PANEL_REGISTERED):
        return
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                PANEL_JS_URL,
                str(Path(__file__).with_name("www") / "enocean-custom-panel.js"),
                False,
            )
        ]
    )
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title="EnOcean",
        sidebar_icon="mdi:radio-tower",
        module_url=PANEL_JS_URL,
        require_admin=True,
    )
    websocket_api.async_register_command(hass, ws_panel_snapshot)
    websocket_api.async_register_command(hass, ws_telegram_subscribe)
    hass.data[_DATA_PANEL_REGISTERED] = True

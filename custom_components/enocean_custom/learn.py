"""Teach-in support: capture unknown EnOcean senders and expose them to the UI."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    DATA_ENOCEAN,
    DATA_KNOWN_IDS,
    DATA_LEARN_MANAGER,
    DOMAIN,
    EVENT_DEVICE_LEARNED,
    LEARN_TIMEOUT_DEFAULT,
    LEARN_TIMEOUT_MAX,
    LEARN_TIMEOUT_MIN,
    SERVICE_LEARN,
    SIGNAL_RECEIVE_MESSAGE,
)
from .device_intelligence import extract_radio_identity, safe_metadata
from .enocean_library.utils import combine_hex, to_hex_string
from .schema import CONF_UI_DEVICES, exact_finite_int, valid_ui_devices

SERVICE_LEARN_SCHEMA = vol.Schema(
    {
        vol.Optional("timeout", default=LEARN_TIMEOUT_DEFAULT): vol.All(
            exact_finite_int, vol.Range(min=LEARN_TIMEOUT_MIN, max=LEARN_TIMEOUT_MAX)
        )
    }
)


def get_known_ids(hass: HomeAssistant) -> set[int]:
    """Return the mutable set of sender ids claimed by static (YAML) devices."""
    enocean_data = hass.data.setdefault(DATA_ENOCEAN, {})
    return enocean_data.setdefault(DATA_KNOWN_IDS, set())


def register_known_id(hass: HomeAssistant, dev_id: list[int] | None) -> None:
    """Record dev_id as claimed so teach-in never re-offers it."""
    if not dev_id:
        return
    get_known_ids(hass).add(combine_hex(dev_id))


def _ui_known_ids(hass: HomeAssistant) -> set[int]:
    """Return sender ids claimed by UI-managed devices, read live from options.

    Read fresh on every learn window so a deleted UI device becomes teachable
    again immediately, without a Home Assistant restart.
    """
    known: set[int] = set()
    config_entries = getattr(hass, "config_entries", None)
    if config_entries is None:
        return known
    for entry in config_entries.async_entries(DOMAIN):
        # valid_ui_devices also shields a hand-edited .storage: a malformed
        # row must be skipped, never crash the learn machinery.
        for device in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, [])):
            known.add(combine_hex(device["id"]))
    return known


def get_learn_manager(hass: HomeAssistant) -> LearnManager:
    """Return the shared learn manager, creating it on first use."""
    enocean_data = hass.data.setdefault(DATA_ENOCEAN, {})
    manager = enocean_data.get(DATA_LEARN_MANAGER)
    if manager is None:
        manager = LearnManager(hass)
        enocean_data[DATA_LEARN_MANAGER] = manager
    return manager


class LearnManager:
    """Capture the first unrecognized EnOcean sender seen during a learn window.

    Windows are serialized and owned: start() refuses while another window is
    active, and stop(owner=...) only closes a window owned by the caller, so a
    dying options flow can never kill a service-owned window (or vice versa).
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize an idle learn manager."""
        self.hass = hass
        self.captured: list[int] | None = None
        self.captured_metadata: dict[str, Any] | None = None
        self.captured_at: float | None = None
        self.window_started_at: float | None = None
        self.owner: str | None = None
        self._unsub_dispatcher: Callable[[], None] | None = None
        self._timeout_handle: Any = None
        self._finished = asyncio.Event()
        self._finished.set()
        self._active_known_ids: set[int] = set()

    @property
    def is_active(self) -> bool:
        """Return whether a learn window is currently open."""
        return self._unsub_dispatcher is not None

    def start(
        self, timeout: int = LEARN_TIMEOUT_DEFAULT, owner: str | None = None
    ) -> bool:
        """Open a learn window that expires after timeout seconds.

        Returns False without disturbing anything when a window is already
        active: windows are serialized so a capture can always be attributed
        to exactly one consumer.
        """
        if self.is_active:
            return False
        self.captured = None
        self.captured_metadata = None
        self.captured_at = None
        self.owner = owner
        # Re-seed the known ids on every window: YAML ids are registered by
        # platform setup, UI ids are read live from the entry options so a
        # device deleted from the UI is teachable again right away.
        self._active_known_ids = get_known_ids(self.hass) | _ui_known_ids(self.hass)
        self._finished = asyncio.Event()
        self._unsub_dispatcher = async_dispatcher_connect(
            self.hass, SIGNAL_RECEIVE_MESSAGE, self._packet_received
        )
        self.window_started_at = self.hass.loop.time()
        self._timeout_handle = self.hass.loop.call_later(timeout, self._on_timeout)
        return True

    def stop(self, owner: str | None = None) -> None:
        """Close the learn window without discarding a capture already made.

        When owner is given, only a window owned by that owner is closed; a
        window owned by someone else is left running.
        """
        if owner is not None and self.owner != owner:
            return
        if self._timeout_handle is not None:
            self._timeout_handle.cancel()
            self._timeout_handle = None
        if self._unsub_dispatcher is not None:
            self._unsub_dispatcher()
            self._unsub_dispatcher = None
        self.owner = None
        if not self._finished.is_set():
            self._finished.set()

    async def wait(self) -> None:
        """Wait until the current learn window closes."""
        await self._finished.wait()

    def _on_timeout(self) -> None:
        """Expire the learn window once the configured delay elapses."""
        self.stop()

    @callback
    def _packet_received(self, packet: Any) -> None:
        """Capture the first sender absent from the known-id registry."""
        identity = extract_radio_identity(packet)
        if identity is None:
            return
        sender_int = combine_hex(list(identity.sender_id))
        if sender_int in self._active_known_ids:
            return
        self.captured = list(identity.sender_id)
        self.captured_metadata = safe_metadata(radio=identity)
        self.captured_at = self.hass.loop.time()
        self.stop()
        self.hass.bus.async_fire(
            EVENT_DEVICE_LEARNED,
            {
                "id": self.captured,
                "hex": to_hex_string(self.captured),
                **{
                    key: value
                    for key, value in self.captured_metadata.items()
                    if key != "sender_id"
                },
            },
        )

    def capture_is_fresh_for(self, window_started_at: float | None) -> bool:
        """Return True when the capture happened during the caller's own window."""
        return (
            self.captured is not None
            and self.captured_at is not None
            and window_started_at is not None
            and self.captured_at >= window_started_at
        )


async def _async_handle_learn_service(call: ServiceCall) -> None:
    """Start a learn window from the enocean_custom.learn service."""
    manager = get_learn_manager(call.hass)
    if not manager.start(call.data["timeout"], owner="service"):
        raise HomeAssistantError(
            "An EnOcean teach-in window is already in progress; try again once it ends."
        )


def async_register_learn_service(hass: HomeAssistant) -> None:
    """Register the learn service once, safe to call again after a reload."""
    if hass.services.has_service(DOMAIN, SERVICE_LEARN):
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_LEARN,
        _async_handle_learn_service,
        schema=SERVICE_LEARN_SCHEMA,
    )

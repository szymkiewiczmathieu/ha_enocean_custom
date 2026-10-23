"""Locate the entry-owned runtime objects without a global mirror.

Home Assistant's ``runtime-data`` rule keeps the serial owner on
``ConfigEntry.runtime_data`` only; ``hass.data`` no longer mirrors it.
Entities created from the config entry and the historical YAML platforms both
need the dongle, so this module resolves the single EnOcean entry on demand.
It must stay cheap and importable from the executor: switch commands run there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .const import DOMAIN

if TYPE_CHECKING:
    from .dongle import EnOceanDongle
    from .inbox import DeviceInbox


def get_gateway(hass: Any) -> EnOceanDongle | None:
    """Return the dongle owned by the EnOcean config entry, or None when unloaded."""
    config_entries = getattr(hass, "config_entries", None)
    if config_entries is None:
        return None
    for entry in config_entries.async_entries(DOMAIN):
        gateway = getattr(entry, "runtime_data", None)
        if gateway is not None:
            return gateway
    return None


def get_inbox(hass: Any) -> DeviceInbox | None:
    """Return the passive radio inbox attached to the loaded dongle, if any."""
    gateway = get_gateway(hass)
    if gateway is None:
        return None
    return getattr(gateway, "inbox", None)

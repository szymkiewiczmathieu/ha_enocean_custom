"""One trivial test to validate the pytest-homeassistant-custom-component wiring."""

from __future__ import annotations

from homeassistant.core import HomeAssistant


async def test_hass_fixture_boots(hass: HomeAssistant) -> None:
    """The plugin's hass fixture must come up before anything else is tried."""
    assert hass.is_running or not hass.is_stopping

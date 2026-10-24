"""Config flow, exercised through the real flow manager (M9).

The stdlib unittest suite already covers config_flow.py's pure helpers and
a hand-rolled flow-step walk without importing Home Assistant's full
config_entries machinery; this file instead drives the exact same code
through ``hass.config_entries.flow``, catching anything that only breaks
when the real flow manager (step dispatch, unique_id handling, entry
persistence) is involved.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.enocean_custom import (
    config_flow,  # noqa: F401  (binds the submodule for patch())
)
from custom_components.enocean_custom.const import DOMAIN


async def test_manual_path_creates_the_single_entry(hass: HomeAssistant) -> None:
    """No USB device detected -> straight to the manual path -> one entry."""
    with (
        patch(
            "custom_components.enocean_custom.config_flow.dongle.detect",
            return_value=[],
        ),
        patch(
            "custom_components.enocean_custom.config_flow.dongle.validate_path",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "manual"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: "/dev/ttyUSB0"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE] == "/dev/ttyUSB0"
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data[CONF_DEVICE] == "/dev/ttyUSB0"


async def test_an_invalid_path_is_refused_without_creating_an_entry(
    hass: HomeAssistant,
) -> None:
    """validate_path refusing a path must surface as a form error, not a crash."""
    with (
        patch(
            "custom_components.enocean_custom.config_flow.dongle.detect",
            return_value=[],
        ),
        patch(
            "custom_components.enocean_custom.config_flow.dongle.validate_path",
            return_value=False,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: "/dev/not-a-dongle"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_DEVICE: "invalid_dongle_path"}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_a_second_instance_is_refused(hass: HomeAssistant) -> None:
    """single_config_entry: a second gateway must never be offered."""
    with (
        patch(
            "custom_components.enocean_custom.config_flow.dongle.detect",
            return_value=[],
        ),
        patch(
            "custom_components.enocean_custom.config_flow.dongle.validate_path",
            return_value=True,
        ),
    ):
        first = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        await hass.config_entries.flow.async_configure(
            first["flow_id"], {CONF_DEVICE: "/dev/ttyUSB0"}
        )

        second = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "single_instance_allowed"

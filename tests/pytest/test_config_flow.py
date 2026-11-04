"""Config flow, exercised through the real flow manager (M9).

The stdlib unittest suite already covers config_flow.py's pure helpers and
a hand-rolled flow-step walk without importing Home Assistant's full
config_entries machinery; this file instead drives the exact same code
through ``hass.config_entries.flow``, catching anything that only breaks
when the real flow manager (step dispatch, unique_id handling, entry
persistence) is involved.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.usb import UsbServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_custom import (
    config_flow,
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


async def test_detected_path_can_be_replaced_by_a_manual_one(
    hass: HomeAssistant,
) -> None:
    with (
        patch.object(
            config_flow.dongle, "detect", return_value=["/dev/serial/by-id/detected"]
        ),
        patch.object(config_flow.dongle, "validate_path", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "detect"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_DEVICE: config_flow.EnOceanFlowHandler.MANUAL_PATH_VALUE},
        )
        assert result["step_id"] == "manual"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: "/dev/ttyUSB-manual"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE] == "/dev/ttyUSB-manual"


async def test_detected_path_is_used_directly(hass: HomeAssistant) -> None:
    with (
        patch.object(
            config_flow.dongle, "detect", return_value=["/dev/serial/by-id/detected"]
        ),
        patch.object(config_flow.dongle, "validate_path", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: "/dev/serial/by-id/detected"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE] == "/dev/serial/by-id/detected"


async def test_usb_discovery_requires_confirmation_then_uses_the_stable_path(
    hass: HomeAssistant,
) -> None:
    discovery = UsbServiceInfo(
        device="/dev/ttyUSB-raw",
        vid="0403",
        pid="6001",
        serial_number="private-serial",
        manufacturer="EnOcean",
        description="USB 300",
    )
    with (
        patch.object(
            config_flow, "usb_unique_id_from_service_info", return_value="usb-id"
        ),
        patch.object(config_flow, "human_readable_device_name", return_value="USB 300"),
        patch.object(
            config_flow.usb, "get_serial_by_id", return_value="/dev/serial/by-id/stable"
        ),
        patch.object(
            config_flow.dongle, "validate_path", return_value=True
        ) as validate,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USB}, data=discovery
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "usb_confirm"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE] == "/dev/serial/by-id/stable"
    validate.assert_called_once_with("/dev/serial/by-id/stable")


async def test_yaml_import_reports_invalid_path_and_accepts_a_valid_one(
    hass: HomeAssistant,
) -> None:
    with patch.object(
        config_flow.dongle, "validate_path", side_effect=[False, True]
    ) as validate:
        invalid = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_IMPORT},
            data={CONF_DEVICE: "/dev/invalid"},
        )
        assert invalid["type"] is FlowResultType.ABORT
        assert invalid["reason"] == "invalid_dongle_path"
        valid = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_IMPORT},
            data={CONF_DEVICE: "/dev/valid"},
        )
    assert valid["type"] is FlowResultType.CREATE_ENTRY
    assert valid["data"][CONF_DEVICE] == "/dev/valid"
    assert validate.call_count == 2


async def test_user_step_refuses_an_existing_entry_at_the_handler_boundary(
    hass: HomeAssistant,
) -> None:
    flow = config_flow.EnOceanFlowHandler()
    flow.hass = hass
    with patch.object(flow, "_async_current_entries", return_value=[Mock()]):
        result = await flow.async_step_user()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


def _reconfigure_entry(
    hass: HomeAssistant, path: str = "/dev/current"
) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: path})
    entry.add_to_hass(hass)
    return entry


async def _start_reconfigure(hass: HomeAssistant, entry: MockConfigEntry):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )


async def test_reconfigure_same_path_is_a_noop_and_options_flow_is_available(
    hass: HomeAssistant,
) -> None:
    entry = _reconfigure_entry(hass)
    result = await _start_reconfigure(hass, entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: "/dev/current"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DEVICE] == "/dev/current"
    assert isinstance(
        config_flow.EnOceanFlowHandler.async_get_options_flow(entry),
        config_flow.EnOceanOptionsFlow,
    )


async def test_reconfigure_alias_does_not_open_a_competing_serial_reader(
    hass: HomeAssistant,
) -> None:
    entry = _reconfigure_entry(hass)
    with (
        patch.object(config_flow.dongle, "same_device", return_value=True),
        patch.object(config_flow.dongle, "validate_path") as validate,
    ):
        result = await _start_reconfigure(hass, entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: "/dev/serial/by-id/alias"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DEVICE] == "/dev/serial/by-id/alias"
    validate.assert_not_called()


async def test_reconfigure_rejects_an_invalid_new_path_then_accepts_a_valid_one(
    hass: HomeAssistant,
) -> None:
    entry = _reconfigure_entry(hass)
    with (
        patch.object(config_flow.dongle, "same_device", return_value=False),
        patch.object(
            config_flow.dongle, "validate_path", side_effect=[False, True]
        ) as validate,
    ):
        result = await _start_reconfigure(hass, entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: "/dev/new"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {CONF_DEVICE: "invalid_dongle_path"}
        assert entry.data[CONF_DEVICE] == "/dev/current"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: "/dev/new"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DEVICE] == "/dev/new"
    assert validate.call_count == 2

"""Config flow for the EnOcean Custom serial gateway."""

from __future__ import annotations

from typing import Any, override

import voluptuous as vol
from homeassistant.components import usb
from homeassistant.components.usb import (
    human_readable_device_name,
    usb_unique_id_from_service_info,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import ATTR_MANUFACTURER, CONF_DEVICE, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.service_info.usb import UsbServiceInfo

from . import dongle
from .const import (
    DOMAIN,
    ERROR_INVALID_DONGLE_PATH,
    LOGGER,
    MANUFACTURER,
)
from .options_flow import EnOceanOptionsFlow

MANUAL_SCHEMA = vol.Schema({vol.Required(CONF_DEVICE): cv.string})


class EnOceanFlowHandler(ConfigFlow, domain=DOMAIN):
    """Configure the single serial gateway used by this integration."""

    VERSION = 1
    MANUAL_PATH_VALUE = "manual"

    def __init__(self) -> None:
        """Initialize discovery state."""
        self.data: dict[str, Any] = {}

    @override
    async def async_step_usb(self, discovery_info: UsbServiceInfo) -> ConfigFlowResult:
        """Handle Home Assistant USB discovery."""
        unique_id = usb_unique_id_from_service_info(discovery_info)
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(
            updates={CONF_DEVICE: discovery_info.device}
        )

        serial_path = await self.hass.async_add_executor_job(
            usb.get_serial_by_id,
            discovery_info.device,
        )
        self.data[CONF_DEVICE] = serial_path
        self.context["title_placeholders"] = {
            CONF_NAME: human_readable_device_name(
                serial_path,
                discovery_info.serial_number,
                discovery_info.manufacturer,
                discovery_info.description,
                discovery_info.vid,
                discovery_info.pid,
            )
        }
        return await self.async_step_usb_confirm()

    async def async_step_usb_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Ask before claiming a discovered serial device."""
        if user_input is not None:
            return await self.async_step_manual({CONF_DEVICE: self.data[CONF_DEVICE]})
        self._set_confirm_only()
        return self.async_show_form(
            step_id="usb_confirm",
            description_placeholders={
                ATTR_MANUFACTURER: MANUFACTURER,
                CONF_DEVICE: self.data.get(CONF_DEVICE, ""),
            },
        )

    async def async_step_import(
        self,
        import_data: dict[str, Any],
    ) -> ConfigFlowResult:
        """Import the legacy top-level YAML gateway configuration."""
        if not await self.validate_enocean_conf(import_data):
            LOGGER.warning("Cannot import YAML configuration: invalid dongle path")
            return self.async_abort(reason=ERROR_INVALID_DONGLE_PATH)
        return self.create_enocean_entry(import_data)

    @override
    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Start a user flow while enforcing the single-gateway invariant."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return await self.async_step_detect()

    async def async_step_detect(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Offer detected serial paths and a manual fallback."""
        if user_input is not None:
            if user_input[CONF_DEVICE] == self.MANUAL_PATH_VALUE:
                return await self.async_step_manual()
            return await self.async_step_manual(user_input)

        devices = await self.hass.async_add_executor_job(dongle.detect)
        if not devices:
            return await self.async_step_manual()
        devices.append(self.MANUAL_PATH_VALUE)
        return self.async_show_form(
            step_id="detect",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE): SelectSelector(
                        SelectSelectorConfig(
                            options=devices,
                            translation_key="devices",
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_manual(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Validate a manually supplied serial path."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if await self.validate_enocean_conf(user_input):
                return self.create_enocean_entry(user_input)
            errors[CONF_DEVICE] = ERROR_INVALID_DONGLE_PATH

        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(
                MANUAL_SCHEMA,
                user_input,
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Replace the serial path without opening an alias twice."""
        entry = self._get_reconfigure_entry()
        current_path = entry.data[CONF_DEVICE]
        errors: dict[str, str] = {}

        if user_input is not None:
            new_path = user_input[CONF_DEVICE]
            if new_path == current_path:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_DEVICE: new_path},
                    reload_even_if_entry_is_unchanged=False,
                )
            alias_of_current = await self.hass.async_add_executor_job(
                dongle.same_device,
                current_path,
                new_path,
            )
            if alias_of_current or await self.validate_enocean_conf(user_input):
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_DEVICE: new_path},
                )
            errors[CONF_DEVICE] = ERROR_INVALID_DONGLE_PATH

        schema = vol.Schema(
            {vol.Required(CONF_DEVICE, default=current_path): cv.string}
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )

    async def validate_enocean_conf(self, user_input: dict[str, Any]) -> bool:
        """Probe a path in the executor; the probe always closes its descriptor."""
        return await self.hass.async_add_executor_job(
            dongle.validate_path,
            user_input[CONF_DEVICE],
        )

    def create_enocean_entry(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        """Persist a validated gateway path."""
        return self.async_create_entry(title=MANUFACTURER, data=user_input)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> EnOceanOptionsFlow:
        """Return the device-management options flow."""
        return EnOceanOptionsFlow()

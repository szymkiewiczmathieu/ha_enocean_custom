"""Config flows for the ENOcean integration."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_DEVICE

from . import dongle
from .const import DOMAIN, ERROR_INVALID_DONGLE_PATH, LOGGER


class EnOceanFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the enOcean config flows."""

    VERSION = 1
    MANUAL_PATH_VALUE = "Custom path"

    def __init__(self) -> None:
        """Initialize the EnOcean config flow."""
        self.dongle_path = None
        self.discovery_info = None

    async def async_step_import(self, data=None):
        """Import a yaml configuration."""

        if not await self.validate_enocean_conf(data):
            LOGGER.warning("Cannot import yaml configuration: invalid dongle path")
            return self.async_abort(reason="invalid_dongle_path")

        return self.create_enocean_entry(data)

    async def async_step_user(self, user_input=None):
        """Handle an EnOcean config flow start."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        return await self.async_step_detect()

    async def async_step_detect(self, user_input=None):
        """Propose a list of detected dongles."""
        errors = {}
        if user_input is not None:
            if user_input[CONF_DEVICE] == self.MANUAL_PATH_VALUE:
                return await self.async_step_manual(None)
            if await self.validate_enocean_conf(user_input):
                return self.create_enocean_entry(user_input)
            errors = {CONF_DEVICE: ERROR_INVALID_DONGLE_PATH}

        bridges = await self.hass.async_add_executor_job(dongle.detect)
        if len(bridges) == 0:
            return await self.async_step_manual(user_input)

        bridges.append(self.MANUAL_PATH_VALUE)
        return self.async_show_form(
            step_id="detect",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE): vol.In(bridges)}),
            errors=errors,
        )

    async def async_step_manual(self, user_input=None):
        """Request manual USB dongle path."""
        default_value = None
        errors = {}
        if user_input is not None:
            if await self.validate_enocean_conf(user_input):
                return self.create_enocean_entry(user_input)
            default_value = user_input[CONF_DEVICE]
            errors = {CONF_DEVICE: ERROR_INVALID_DONGLE_PATH}

        device_field = (
            vol.Required(CONF_DEVICE, default=default_value)
            if default_value
            else vol.Required(CONF_DEVICE)
        )
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({device_field: str}),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None):
        """Allow changing the serial path without deleting the config entry."""
        entry = self._get_reconfigure_entry()
        current_path = entry.data[CONF_DEVICE]
        errors = {}
        if user_input is not None:
            new_path = user_input[CONF_DEVICE]
            if new_path == current_path:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_DEVICE: new_path},
                    reload_even_if_entry_is_unchanged=False,
                )
            is_same_device = await self.hass.async_add_executor_job(
                dongle.same_device, current_path, new_path
            )
            if is_same_device or await self.validate_enocean_conf(user_input):
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_DEVICE: new_path}
                )
            errors[CONF_DEVICE] = ERROR_INVALID_DONGLE_PATH

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVICE, default=current_path): str}
            ),
            errors=errors,
        )

    async def validate_enocean_conf(self, user_input) -> bool:
        """Return True if the user_input contains a valid dongle path."""
        dongle_path = user_input[CONF_DEVICE]
        path_is_valid = await self.hass.async_add_executor_job(
            dongle.validate_path, dongle_path
        )
        return path_is_valid

    def create_enocean_entry(self, user_input):
        """Create an entry for the provided configuration."""
        return self.async_create_entry(title="EnOcean", data=user_input)

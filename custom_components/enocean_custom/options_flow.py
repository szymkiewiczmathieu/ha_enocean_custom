"""Options flow: add and remove EnOcean devices without editing YAML."""

from __future__ import annotations

import re
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.binary_sensor import DEVICE_CLASSES_SCHEMA
from homeassistant.config_entries import OptionsFlow
from homeassistant.const import CONF_DEVICE_CLASS, CONF_NAME, CONF_PLATFORM
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import DOMAIN, LEARN_TIMEOUT_DEFAULT, UI_DEVICE_PLATFORMS
from .enocean_library.utils import combine_hex, to_hex_string
from .learn import get_learn_manager
from .light import CONF_SENDER_ID
from .schema import CONF_UI_DEVICES, valid_ui_devices
from .switch import CONF_CHANNEL, CONF_SWITCH_TYPE, SWITCH_TYPES

_FLOW_OWNER = "options_flow"
_HEX_BYTE = re.compile(r"^[0-9A-Fa-f]{2}$")


def _unique_id_for(device: dict[str, Any]) -> str:
    """Return the unique_id the matching YAML platform would have produced."""
    identifier = combine_hex(device["id"])
    if device["platform"] == "binary_sensor":
        return f"{identifier}-{device['device_class']}"
    if device["platform"] == "switch":
        return f"{identifier}-{device['channel']}"
    return f"{identifier}"


def _parse_sender_id(value: Any) -> list[int] | None:
    """Parse a sender id typed as 'AA:BB:CC:DD' (or space separated) hex bytes."""
    if not isinstance(value, str):
        return None
    parts = re.split(r"[: ]+", value.strip())
    if len(parts) != 4 or not all(_HEX_BYTE.match(part) for part in parts):
        return None
    return [int(part, 16) for part in parts]


def _exact_int(value: Any) -> int | None:
    """Return value as an exact integer, or None when it is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


class EnOceanOptionsFlow(OptionsFlow):
    """Add or remove EnOcean devices entirely from the UI."""

    def __init__(self) -> None:
        """Initialize flow-local state for the current session."""
        self._captured_id: list[int] | None = None
        self._window_started_at: float | None = None
        self._pending_platform: str | None = None
        self._pending_name: str | None = None
        self._manage_index: int | None = None

    @callback
    def async_remove(self) -> None:
        """Stop this flow's learn window when the flow is discarded.

        Ownership-scoped: a window started by the learn *service* is left
        running, so discarding the UI flow can never silently eat the next
        physical press meant for a service consumer.
        """
        get_learn_manager(self.hass).stop(owner=_FLOW_OWNER)

    async def async_step_init(self, user_input=None):
        """Offer to add a new device or manage existing UI devices."""
        return self.async_show_menu(step_id="init", menu_options=["learn", "manage"])

    async def async_step_learn(self, user_input=None):
        """Wait for the next unknown EnOcean sender."""
        manager = get_learn_manager(self.hass)
        if manager.capture_is_fresh_for(self._window_started_at):
            self._captured_id = manager.captured
            manager.captured = None
            manager.captured_at = None
            self._window_started_at = None
            return self.async_show_progress_done(next_step_id="device_form")
        if self._window_started_at is not None:
            # Our own window expired or was closed without a capture.
            self._window_started_at = None
            return self.async_show_progress_done(next_step_id="learn_timeout")
        if not manager.start(LEARN_TIMEOUT_DEFAULT, owner=_FLOW_OWNER):
            return self.async_abort(reason="learn_in_progress")
        self._window_started_at = manager.window_started_at
        task = self.hass.async_create_task(manager.wait())
        return self.async_show_progress(
            step_id="learn",
            progress_action="waiting_for_device",
            progress_task=task,
        )

    async def async_step_learn_timeout(self, user_input=None):
        """Abort after no unknown sender was heard before the timeout."""
        return self.async_abort(reason="learn_timeout")

    def _show_device_form(self, errors: dict[str, str] | None = None):
        """Render the platform/name form, pre-filled after a rejected submit."""
        if self._pending_platform is not None:
            schema = {
                vol.Required(CONF_PLATFORM, default=self._pending_platform): vol.In(
                    UI_DEVICE_PLATFORMS
                ),
                vol.Required(CONF_NAME, default=self._pending_name): cv.string,
            }
        else:
            schema = {
                vol.Required(CONF_PLATFORM): vol.In(UI_DEVICE_PLATFORMS),
                vol.Required(CONF_NAME): cv.string,
            }
        return self.async_show_form(
            step_id="device_form",
            data_schema=vol.Schema(schema),
            description_placeholders={"captured_id": to_hex_string(self._captured_id)},
            errors=errors or {},
        )

    async def async_step_device_form(self, user_input=None):
        """Ask for the platform and name of the captured device."""
        if user_input is not None:
            self._pending_platform = user_input[CONF_PLATFORM]
            self._pending_name = user_input[CONF_NAME]
            return await self.async_step_device_details()
        return self._show_device_form()

    def _device_details_schema(self, platform: str) -> vol.Schema:
        """Build the platform form with websocket-serializable selectors only.

        Custom voluptuous functions crash the WS/REST flow handler with a
        ValueError (HTTP 500); native selectors are always serializable.
        """
        schema_dict: dict[Any, Any] = {}
        if platform == "binary_sensor":
            schema_dict[vol.Optional(CONF_DEVICE_CLASS)] = DEVICE_CLASSES_SCHEMA
        elif platform == "switch":
            schema_dict[vol.Optional(CONF_CHANNEL, default=0)] = (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=255, mode=selector.NumberSelectorMode.BOX
                    )
                )
            )
            schema_dict[vol.Required(CONF_SWITCH_TYPE, default="default")] = vol.In(
                SWITCH_TYPES
            )
        else:
            schema_dict[vol.Required(CONF_SENDER_ID)] = selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            )
        return vol.Schema(schema_dict)

    def _show_details_form(self, platform: str, errors: dict[str, str] | None = None):
        """Render the platform-specific details form."""
        return self.async_show_form(
            step_id="device_details",
            data_schema=self._device_details_schema(platform),
            description_placeholders={
                "captured_id": to_hex_string(self._captured_id),
                "name": self._pending_name or "",
            },
            errors=errors or {},
        )

    async def async_step_device_details(self, user_input=None):
        """Collect platform-specific fields and persist the new device."""
        platform = self._pending_platform
        if platform is None:
            return self.async_abort(reason="device_form_missing")

        if user_input is None:
            return self._show_details_form(platform)

        if platform == "switch":
            channel = _exact_int(user_input.get(CONF_CHANNEL, 0))
            if channel is None or not 0 <= channel <= 255:
                return self._show_details_form(
                    platform, errors={CONF_CHANNEL: "invalid_channel"}
                )
            if user_input.get(CONF_SWITCH_TYPE) == "RPS" and channel not in (0, 1):
                return self._show_details_form(
                    platform, errors={CONF_CHANNEL: "invalid_channel_rps"}
                )
        else:
            channel = 0

        sender_id: list[int] | None = None
        if platform == "light":
            sender_id = _parse_sender_id(user_input.get(CONF_SENDER_ID))
            if sender_id is None:
                return self._show_details_form(
                    platform, errors={CONF_SENDER_ID: "invalid_sender_id"}
                )

        device = {
            "id": self._captured_id,
            "platform": platform,
            "name": self._pending_name,
            "device_class": user_input.get(CONF_DEVICE_CLASS),
            "channel": channel,
            "switch_type": user_input.get(CONF_SWITCH_TYPE),
            "sender_id": sender_id,
        }
        unique_id = _unique_id_for(device)
        registry = er.async_get(self.hass)
        existing_devices = valid_ui_devices(
            self.config_entry.options.get(CONF_UI_DEVICES, [])
        )
        collides = registry.async_get_entity_id(
            platform, DOMAIN, unique_id
        ) is not None or any(
            _unique_id_for(existing) == unique_id for existing in existing_devices
        )
        if collides:
            return self._show_device_form(errors={"base": "unique_id_exists"})

        updated_options = dict(self.config_entry.options)
        updated_options[CONF_UI_DEVICES] = [
            *self.config_entry.options.get(CONF_UI_DEVICES, []),
            device,
        ]
        return self.async_create_entry(title="", data=updated_options)

    async def async_step_manage(self, user_input=None):
        """List UI devices and let the user pick one to remove."""
        raw_devices = list(self.config_entry.options.get(CONF_UI_DEVICES, []))
        # Filter hand-edited malformed rows out of the list, but keep mapping
        # choices to their RAW index so deletion always pops the right row.
        choices = {}
        for index, raw in enumerate(raw_devices):
            valid = valid_ui_devices([raw])
            if not valid:
                continue
            device = valid[0]
            choices[str(index)] = (
                f"{device['name']} ({device['platform']}, "
                f"{to_hex_string(device['id'])})"
            )
        if not choices:
            return self.async_abort(reason="no_ui_devices")
        if user_input is not None:
            self._manage_index = int(user_input["device"])
            return await self.async_step_delete_confirm()
        return self.async_show_form(
            step_id="manage",
            data_schema=vol.Schema({vol.Required("device"): vol.In(choices)}),
        )

    async def async_step_delete_confirm(self, user_input=None):
        """Confirm, then remove a UI device from options and the registry.

        Deletion only ever targets the registry row whose domain, platform and
        unique_id match exactly; teach-in re-offers the freed EnOcean ID on
        the next learn window because known ids are re-seeded from options.
        """
        raw_devices = list(self.config_entry.options.get(CONF_UI_DEVICES, []))
        index = self._manage_index
        if index is None or index >= len(raw_devices):
            return self.async_abort(reason="no_ui_devices")
        valid = valid_ui_devices([raw_devices[index]])
        if not valid:
            return self.async_abort(reason="no_ui_devices")
        device = valid[0]

        if user_input is None:
            return self.async_show_form(
                step_id="delete_confirm",
                data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
                description_placeholders={
                    "name": device["name"],
                    "captured_id": to_hex_string(device["id"]),
                    "platform": device["platform"],
                },
            )
        if not user_input["confirm"]:
            return self.async_abort(reason="delete_cancelled")

        unique_id = _unique_id_for(device)
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(device["platform"], DOMAIN, unique_id)
        if entity_id is not None:
            registry.async_remove(entity_id)

        raw_devices.pop(index)
        updated_options = dict(self.config_entry.options)
        updated_options[CONF_UI_DEVICES] = raw_devices
        return self.async_create_entry(title="", data=updated_options)

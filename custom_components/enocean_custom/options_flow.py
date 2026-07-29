"""Options flow: add and remove EnOcean devices without editing YAML."""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from math import isfinite
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import OptionsFlow
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_ENTITY_ID,
    CONF_NAME,
    CONF_PLATFORM,
    SERVICE_TOGGLE,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .climate import (
    CONF_COMMAND_FREQUENCY,
    CONF_DEVICE_TYPE,
    CONF_PI_CONTROL_KP,
    CONF_PI_CONTROL_TN,
    CONF_SENDER_ID_SWITCH,
    CONF_SENSOR_ENTITY_ID,
    CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION,
    CONF_SENSOR_TARGET_TEMP_RANGE,
    CONF_SENSOR_TARGET_TEMP_TOLERANCE,
    CONF_TARGET_TEMP_BASE,
    CONF_TARGET_TEMP_NIGHT_REDUCTION,
    DEVICE_SUPPORTED_LIST,
)
from .const import (
    DOMAIN,
    LEARN_TIMEOUT_DEFAULT,
    SERVICE_SEND_TEACH_IN,
    SIGNAL_RECEIVE_MESSAGE,
    UI_DEVICE_PLATFORMS,
)
from .enocean_library.protocol.d2 import parse_d2_01_actuator_status
from .enocean_library.utils import combine_hex, to_hex_string
from .learn import get_known_ids, get_learn_manager
from .light import CONF_SENDER_ID
from .schema import (
    CONF_UI_DEVICES,
    UI_CLIMATE_DEVICE_TYPES,
    UI_DEVICE_SCHEMA,
    exact_finite_int,
    valid_ui_devices,
)
from .sensor import (
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_RANGE_FROM,
    CONF_RANGE_TO,
    SENSOR_TYPES,
)
from .switch import CONF_CHANNEL, CONF_SWITCH_TYPE, SWITCH_TYPES

_FLOW_OWNER = "options_flow"
_HEX_BYTE = re.compile(r"^[0-9A-Fa-f]{2}$")
_HEX_12 = re.compile(r"^[0-9A-Fa-f]{12}$")
_STRICT_TYPED_ID = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){3}$")
_LABEL_CHARSET = re.compile(r"^[0-9A-Za-z+]+$")
_CONF_QR_CODE = "qr_code"
_CONF_ACTUATOR_TYPE = "actuator_type"
_CONF_FAILURE_ACTION = "failure_action"
_ACTUATOR_RELAY = "relay_rps"
_ACTUATOR_DIMMER = "dimmer_4bs"
_FAILURE_RETRY = "retry"
_FAILURE_KEEP = "keep"
_FAILURE_DELETE = "delete"

PAIRING_TIMEOUT = 120.0
PAIRING_RELAY_INTERVAL = 4.0
PAIRING_DIMMER_INTERVAL = 5.0
PAIRING_DIMMER_ATTEMPTS = 3


def _unique_id_for(device: dict[str, Any]) -> str:
    """Return the unique_id the matching YAML platform would have produced."""
    identifier = combine_hex(device["id"])
    if device["platform"] == "binary_sensor":
        return f"{identifier}-{device['device_class']}"
    if device["platform"] == "switch":
        return f"{identifier}-{device['channel']}"
    if device["platform"] == "climate":
        return f"{identifier}-0"
    if device["platform"] == "sensor":
        return f"{identifier}-{device['device_class']}"
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


def _finite_float(value: Any) -> float | None:
    """Return a finite float, or None when conversion is unsafe."""
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


def _parse_commissioning_code(value: Any) -> list[int] | None:
    """Extract a 32-bit EURID from an Alliance product label or typed ID.

    Product ID and Standardized Labeling Specification 1.8, sections 4.1 and
    4.3, defines an ANSI MH10.8.2 label with mandatory 30S (12-hex EURID) and
    1P (12-hex Product ID) containers. This integration supports 32-bit radio
    IDs only, so a genuine 48-bit EURID is rejected instead of truncated.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    # Strict typed fallback: exactly four colon-separated hex bytes. The
    # tolerant sender-id parser stays reserved for the sender_id form field;
    # a commissioning path must never guess from a malformed string.
    if _STRICT_TYPED_ID.fullmatch(value):
        return [int(value[offset : offset + 2], 16) for offset in range(0, 12, 3)]
    # Label payloads are '+'-separated alphanumeric MH10.8.2 containers:
    # anything else (spaces, NULs, unicode) is not a product label.
    if not _LABEL_CHARSET.fullmatch(value):
        return None
    containers = value.split("+")
    eurids = [item[3:] for item in containers if item.startswith("30S")]
    product_ids = [item[2:] for item in containers if item.startswith("1P")]
    if (
        len(eurids) != 1
        or len(product_ids) != 1
        or not _HEX_12.fullmatch(eurids[0])
        or not _HEX_12.fullmatch(product_ids[0])
        or eurids[0][:4] != "0000"
    ):
        return None
    try:
        return [
            exact_finite_int(int(eurids[0][offset : offset + 2], 16))
            for offset in range(4, 12, 2)
        ]
    except vol.Invalid:
        return None


def _qr_code_schema() -> vol.Schema:
    """Return the websocket-serializable commissioning-code form schema."""
    return vol.Schema(
        {
            vol.Required(_CONF_QR_CODE): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            )
        }
    )


def _pair_actuator_type_schema() -> vol.Schema:
    """Return the websocket-serializable actuator name/type schema."""
    return vol.Schema(
        {
            vol.Required(CONF_NAME): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Required(_CONF_ACTUATOR_TYPE): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[_ACTUATOR_RELAY, _ACTUATOR_DIMMER],
                    translation_key="actuator_type",
                )
            ),
        }
    )


def _pairing_failure_schema() -> vol.Schema:
    """Return the websocket-serializable timeout action schema."""
    return vol.Schema(
        {
            vol.Required(_CONF_FAILURE_ACTION): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        _FAILURE_RETRY,
                        _FAILURE_KEEP,
                        _FAILURE_DELETE,
                    ],
                    translation_key="pairing_failure_action",
                )
            )
        }
    )


class EnOceanOptionsFlow(OptionsFlow):
    """Add or remove EnOcean devices entirely from the UI."""

    def __init__(self) -> None:
        """Initialize flow-local state for the current session."""
        self._captured_id: list[int] | None = None
        self._window_started_at: float | None = None
        self._pending_platform: str | None = None
        self._pending_name: str | None = None
        self._manage_index: int | None = None
        self._pairing_actuator_type: str | None = None
        self._pairing_device: dict[str, Any] | None = None
        self._pairing_task: asyncio.Task[None] | None = None
        self._pairing_outcome: str | None = None
        self._pairing_status_event: asyncio.Event | None = None
        self._pairing_unsubscribe = None

    @callback
    def async_remove(self) -> None:
        """Stop this flow's learn window and pairing loop when discarded.

        Ownership-scoped: a window started by the learn *service* is left
        running, so discarding the UI flow can never silently eat the next
        physical press meant for a service consumer.
        """
        get_learn_manager(self.hass).stop(owner=_FLOW_OWNER)
        self._stop_pairing_loop()

    async def async_step_init(self, user_input=None):
        """Offer to add a new device or manage existing UI devices."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["learn", "qr_code", "pair_actuator", "manage"],
        )

    async def async_step_pair_actuator(self, user_input=None):
        """Identify an actuator from its QR label or strict typed radio ID."""
        errors = {}
        if user_input is not None:
            if parsed := _parse_commissioning_code(user_input.get(_CONF_QR_CODE)):
                self._captured_id = parsed
                return await self.async_step_pair_actuator_type()
            errors[_CONF_QR_CODE] = "invalid_qr_code"
        return self.async_show_form(
            step_id="pair_actuator",
            data_schema=_qr_code_schema(),
            errors=errors,
        )

    async def async_step_pair_actuator_type(self, user_input=None):
        """Collect the friendly name and supported actuator family."""
        if self._captured_id is None:
            return self.async_abort(reason="device_form_missing")
        if user_input is not None:
            self._pending_name = user_input[CONF_NAME]
            self._pairing_actuator_type = user_input[_CONF_ACTUATOR_TYPE]
            return await self.async_step_pair_actuator_details()
        return self.async_show_form(
            step_id="pair_actuator_type",
            data_schema=_pair_actuator_type_schema(),
            description_placeholders={"captured_id": to_hex_string(self._captured_id)},
        )

    def _pair_actuator_details_schema(self) -> vol.Schema:
        """Build the serializable details schema for the chosen actuator."""
        if self._pairing_actuator_type == _ACTUATOR_RELAY:
            return vol.Schema(
                {
                    vol.Required(CONF_CHANNEL, default=0): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    )
                }
            )
        return vol.Schema(
            {
                vol.Required(CONF_SENDER_ID): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Optional(CONF_CHANNEL, default=0): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=31,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )

    def _show_pair_actuator_details(self, errors: dict[str, str] | None = None):
        """Render the pairing details form."""
        return self.async_show_form(
            step_id="pair_actuator_details",
            data_schema=self._pair_actuator_details_schema(),
            description_placeholders={
                "captured_id": to_hex_string(self._captured_id),
                "name": self._pending_name or "",
            },
            errors=errors or {},
        )

    async def async_step_pair_actuator_details(self, user_input=None):
        """Validate, persist, and reload the entity used during pairing."""
        if (
            self._captured_id is None
            or self._pending_name is None
            or self._pairing_actuator_type not in (_ACTUATOR_RELAY, _ACTUATOR_DIMMER)
        ):
            return self.async_abort(reason="device_form_missing")
        if user_input is None:
            return self._show_pair_actuator_details()

        maximum = 1 if self._pairing_actuator_type == _ACTUATOR_RELAY else 31
        channel = _exact_int(user_input.get(CONF_CHANNEL, 0))
        if channel is None or not 0 <= channel <= maximum:
            return self._show_pair_actuator_details(
                errors={CONF_CHANNEL: "invalid_channel"}
            )

        sender_id = None
        platform = "switch"
        switch_type = "RPS"
        if self._pairing_actuator_type == _ACTUATOR_DIMMER:
            platform = "light"
            switch_type = None
            sender_id = _parse_sender_id(user_input.get(CONF_SENDER_ID))
            if sender_id is None:
                return self._show_pair_actuator_details(
                    errors={CONF_SENDER_ID: "invalid_sender_id"}
                )

        try:
            device = UI_DEVICE_SCHEMA(
                {
                    "id": self._captured_id,
                    "platform": platform,
                    "name": self._pending_name,
                    "channel": channel,
                    "switch_type": switch_type,
                    "sender_id": sender_id,
                }
            )
        except vol.Invalid:
            return self._show_pair_actuator_details(
                errors={"base": "invalid_device_details"}
            )

        updated_options = self._options_with_added_device(device)
        if updated_options is None:
            return self._show_pair_actuator_details(errors={"base": "unique_id_exists"})
        self._pairing_device = device
        self.hass.config_entries.async_update_entry(
            self.config_entry, options=updated_options
        )
        return await self.async_step_pair_actuator_instructions()

    async def async_step_pair_actuator_instructions(self, user_input=None):
        """Tell the user how to put the actuator into pairing mode."""
        if self._pairing_device is None:
            return self.async_abort(reason="device_form_missing")
        if user_input is not None:
            return await self.async_step_pair_actuator_progress()
        return self.async_show_form(
            step_id="pair_actuator_instructions",
            data_schema=vol.Schema({}),
            description_placeholders={
                "timeout": str(round(PAIRING_TIMEOUT)),
            },
        )

    async def async_step_pair_actuator_progress(self, user_input=None):
        """Drive the bounded pairing loop and advance when it completes."""
        if self._pairing_device is None:
            return self.async_abort(reason="device_form_missing")
        if self._pairing_task is None:
            self._pairing_outcome = None
            self._pairing_task = self.hass.async_create_task(
                self._run_pairing_loop(),
                "EnOcean guided actuator pairing",
            )
            return self.async_show_progress(
                step_id="pair_actuator_progress",
                progress_action="pairing_actuator",
                progress_task=self._pairing_task,
                description_placeholders={
                    "timeout": str(round(PAIRING_TIMEOUT)),
                },
            )
        if not self._pairing_task.done():
            return self.async_show_progress(
                step_id="pair_actuator_progress",
                progress_action="pairing_actuator",
                progress_task=self._pairing_task,
                description_placeholders={
                    "timeout": str(round(PAIRING_TIMEOUT)),
                },
            )
        if (
            self._pairing_outcome == "success"
            and self._pairing_device_still_persisted()
        ):
            next_step = (
                "pair_relay_success"
                if self._pairing_actuator_type == _ACTUATOR_RELAY
                else "pair_dimmer_success"
            )
        else:
            # A success whose device vanished meanwhile (concurrent delete) is
            # reported as a failure, never as a stale success (review P2-02).
            next_step = "pair_actuator_failure"
        return self.async_show_progress_done(next_step_id=next_step)

    async def _run_pairing_loop(self) -> None:
        """Toggle/send teach-in until confirmed, completed, or timed out."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PAIRING_TIMEOUT
        try:
            if self._pairing_actuator_type == _ACTUATOR_RELAY:
                self._pairing_status_event = asyncio.Event()

                @callback
                def _status_received(packet) -> None:
                    if self._is_matching_pairing_status(packet):
                        self._pairing_status_event.set()

                self._pairing_unsubscribe = async_dispatcher_connect(
                    self.hass, SIGNAL_RECEIVE_MESSAGE, _status_received
                )
                await self._run_relay_pairing(deadline)
            else:
                await self._run_dimmer_pairing(deadline)
        finally:
            if self._pairing_unsubscribe is not None:
                self._pairing_unsubscribe()
                self._pairing_unsubscribe = None
            self._pairing_status_event = None

    async def _run_relay_pairing(self, deadline: float) -> None:
        """Toggle the persisted RPS entity until a D2-01 status arrives."""
        while self._pairing_status_event is not None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                self._pairing_outcome = "timeout"
                return
            if self._pairing_status_event.is_set():
                self._pairing_outcome = "success"
                return
            # Review finding P2-01: the deadline also bounds the service call
            # itself. Cancelling the await cannot kill an executor handler
            # already running, so one last in-flight command may still
            # complete — but no further command is issued afterwards.
            try:
                await asyncio.wait_for(
                    self._async_call_pairing_service(SWITCH_DOMAIN, SERVICE_TOGGLE),
                    timeout=remaining,
                )
            except TimeoutError:
                self._pairing_outcome = "timeout"
                return
            remaining = deadline - asyncio.get_running_loop().time()
            try:
                await asyncio.wait_for(
                    self._pairing_status_event.wait(),
                    timeout=min(PAIRING_RELAY_INTERVAL, max(remaining, 0.0)),
                )
            except TimeoutError:
                continue
            self._pairing_outcome = "success"
            return

    @callback
    def _is_matching_pairing_status(self, packet) -> bool:
        """Return whether a packet confirms this relay, ignoring malformed data."""
        if (
            self._pairing_status_event is None
            or self._pairing_device is None
            or getattr(packet, "sender_int", None)
            != combine_hex(self._pairing_device["id"])
        ):
            return False
        try:
            status = parse_d2_01_actuator_status(getattr(packet, "data", []))
        except (IndexError, TypeError, ValueError):
            return False
        if status is None:
            return False
        # Review finding P1-01: a valid status for the OTHER channel of a
        # multi-gang actuator does not prove THIS channel learned the command.
        if status.channel != self._pairing_device["channel"]:
            return False
        # Review finding P2-02: a concurrent options flow may have deleted the
        # device while we wait; a stale success must never be reported.
        return self._pairing_device_still_persisted()

    def _pairing_device_still_persisted(self) -> bool:
        """Return whether the pairing device identity is still in the options."""
        if self._pairing_device is None:
            return False
        unique_id = _unique_id_for(self._pairing_device)
        devices = self.config_entry.options.get(CONF_UI_DEVICES, [])
        return any(_unique_id_for(existing) == unique_id for existing in devices)

    async def _run_dimmer_pairing(self, deadline: float) -> None:
        """Send the existing A5-38-08 entity service a bounded number of times."""
        sent = 0
        while sent < PAIRING_DIMMER_ATTEMPTS:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                self._pairing_outcome = "timeout"
                return
            try:
                accepted = await asyncio.wait_for(
                    self._async_call_pairing_service(DOMAIN, SERVICE_SEND_TEACH_IN),
                    timeout=remaining,
                )
            except TimeoutError:
                self._pairing_outcome = "timeout"
                return
            if accepted:
                sent += 1
                if sent >= PAIRING_DIMMER_ATTEMPTS:
                    self._pairing_outcome = "success"
                    return
            await asyncio.sleep(min(PAIRING_DIMMER_INTERVAL, max(remaining, 0.0)))
        self._pairing_outcome = "success"

    async def _async_call_pairing_service(self, domain: str, service: str) -> bool:
        """Call an existing entity command after resolving its registry row."""
        device = self._pairing_device
        if device is None:
            return False
        entity_id = er.async_get(self.hass).async_get_entity_id(
            device["platform"], DOMAIN, _unique_id_for(device)
        )
        if entity_id is None:
            return False
        try:
            await self.hass.services.async_call(
                domain,
                service,
                {CONF_ENTITY_ID: entity_id},
                blocking=True,
            )
        except HomeAssistantError:
            return False
        return True

    def _finish_pairing(self):
        """Finish an options flow whose device was already persisted."""
        return self.async_create_entry(title="", data=dict(self.config_entry.options))

    async def async_step_pair_relay_success(self, user_input=None):
        """Report confirmed D2-01 relay feedback."""
        if user_input is not None:
            return self._finish_pairing()
        return self.async_show_form(
            step_id="pair_relay_success", data_schema=vol.Schema({})
        )

    async def async_step_pair_dimmer_success(self, user_input=None):
        """Report sent teach-in telegrams without claiming radio confirmation."""
        if user_input is not None:
            return self._finish_pairing()
        return self.async_show_form(
            step_id="pair_dimmer_success",
            data_schema=vol.Schema({}),
            description_placeholders={
                "attempts": str(PAIRING_DIMMER_ATTEMPTS),
            },
        )

    async def async_step_pair_actuator_failure(self, user_input=None):
        """Offer retry, keep, or deletion after the pairing timeout."""
        if self._pairing_device is None:
            return self.async_abort(reason="device_form_missing")
        if user_input is None:
            return self.async_show_form(
                step_id="pair_actuator_failure",
                data_schema=_pairing_failure_schema(),
            )

        action = user_input[_CONF_FAILURE_ACTION]
        if action == _FAILURE_RETRY:
            self._reset_pairing_run()
            return await self.async_step_pair_actuator_instructions()
        if action == _FAILURE_KEEP:
            return self._finish_pairing()

        updated_options, error = self._options_without_pairing_device()
        if error is not None:
            return self.async_abort(reason=error)
        self.hass.config_entries.async_update_entry(
            self.config_entry, options=updated_options
        )
        self._pairing_device = None
        return self.async_create_entry(title="", data=updated_options)

    @callback
    def _stop_pairing_loop(self) -> None:
        """Cancel the pairing task and remove its dispatcher listener."""
        if self._pairing_task is not None and not self._pairing_task.done():
            self._pairing_task.cancel()
        if self._pairing_unsubscribe is not None:
            self._pairing_unsubscribe()
            self._pairing_unsubscribe = None
        self._pairing_status_event = None

    def _reset_pairing_run(self) -> None:
        """Reset completed run state before a retry."""
        self._stop_pairing_loop()
        self._pairing_task = None
        self._pairing_outcome = None

    async def async_step_qr_code(self, user_input=None):
        """Capture an EnOcean ID from a commissioning QR payload or typed ID."""
        errors = {}
        if user_input is not None:
            if parsed := _parse_commissioning_code(user_input.get(_CONF_QR_CODE)):
                self._captured_id = parsed
                return await self.async_step_device_form()
            errors[_CONF_QR_CODE] = "invalid_qr_code"
        return self.async_show_form(
            step_id="qr_code",
            data_schema=_qr_code_schema(),
            errors=errors,
        )

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
                vol.Required(
                    CONF_PLATFORM, default=self._pending_platform
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=list(UI_DEVICE_PLATFORMS))
                ),
                vol.Required(
                    CONF_NAME, default=self._pending_name
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
            }
        else:
            schema = {
                vol.Required(CONF_PLATFORM): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=list(UI_DEVICE_PLATFORMS))
                ),
                vol.Required(CONF_NAME): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
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
            schema_dict[vol.Optional(CONF_DEVICE_CLASS)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[item.value for item in BinarySensorDeviceClass]
                )
            )
        elif platform == "switch":
            schema_dict[vol.Optional(CONF_CHANNEL, default=0)] = (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=255, mode=selector.NumberSelectorMode.BOX
                    )
                )
            )
            schema_dict[vol.Required(CONF_SWITCH_TYPE, default="default")] = (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(options=list(SWITCH_TYPES))
                )
            )
        elif platform == "light":
            schema_dict[vol.Required(CONF_SENDER_ID)] = selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            )
            # D2-01-12 multi-gang actuators report every channel; pick which
            # one drives this light (I/O channel is 5 bits wide).
            schema_dict[vol.Optional("channel", default=0)] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=31,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
        elif platform == "sensor":
            schema_dict[vol.Optional(CONF_DEVICE_CLASS, default="powersensor")] = (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(options=list(SENSOR_TYPES))
                )
            )
            for field, default, minimum, maximum in (
                (CONF_MAX_TEMP, 40, -100, 100),
                (CONF_MIN_TEMP, 0, -100, 100),
                (CONF_RANGE_FROM, 255, 0, 255),
                (CONF_RANGE_TO, 0, 0, 255),
            ):
                schema_dict[vol.Optional(field, default=default)] = (
                    selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=minimum,
                            max=maximum,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    )
                )
        elif platform == "climate":
            schema_dict[vol.Required(CONF_SENDER_ID_SWITCH)] = selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            )
            schema_dict[vol.Required(CONF_DEVICE_TYPE)] = selector.SelectSelector(
                # Review finding P2-02: only offer what UI_DEVICE_SCHEMA can
                # actually persist (A5-20-04 stays YAML-only for now).
                selector.SelectSelectorConfig(options=list(UI_CLIMATE_DEVICE_TYPES))
            )
            schema_dict[vol.Required(CONF_SENSOR_ENTITY_ID)] = selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            )
            for field, default, minimum, maximum, step in (
                (CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION, 8.0, 0, 20, 0.1),
                (CONF_SENSOR_TARGET_TEMP_RANGE, 5, 0, 20, 1),
                (CONF_SENSOR_TARGET_TEMP_TOLERANCE, 0.5, 0, 10, 0.1),
                (CONF_TARGET_TEMP_BASE, 21.0, 5, 35, 0.1),
                (CONF_TARGET_TEMP_NIGHT_REDUCTION, 4.0, 0, 20, 0.1),
                (CONF_PI_CONTROL_KP, 5.0, 0.001, 100, 0.001),
                (CONF_PI_CONTROL_TN, 240.0, 0.001, 1440, 0.001),
            ):
                schema_dict[vol.Optional(field, default=default)] = (
                    selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=minimum,
                            max=maximum,
                            step=step,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    )
                )
            schema_dict[vol.Optional(CONF_COMMAND_FREQUENCY, default="00:17:00")] = (
                selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                )
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
        elif platform == "light":
            # D2-01-12 I/O channel is 5 bits wide (0-31).
            channel = _exact_int(user_input.get(CONF_CHANNEL, 0))
            if channel is None or not 0 <= channel <= 31:
                return self._show_details_form(
                    platform, errors={CONF_CHANNEL: "invalid_channel"}
                )
        else:
            channel = 0

        sender_id: list[int] | None = None
        id_switch: list[int] | None = None
        if platform == "light":
            sender_id = _parse_sender_id(user_input.get(CONF_SENDER_ID))
            if sender_id is None:
                return self._show_details_form(
                    platform, errors={CONF_SENDER_ID: "invalid_sender_id"}
                )
        elif platform == "climate":
            id_switch = _parse_sender_id(user_input.get(CONF_SENDER_ID_SWITCH))
            if id_switch is None:
                return self._show_details_form(
                    platform, errors={CONF_SENDER_ID_SWITCH: "invalid_sender_id"}
                )

        if platform == "sensor":
            for field, minimum, maximum in (
                (CONF_MAX_TEMP, -100, 100),
                (CONF_MIN_TEMP, -100, 100),
                (CONF_RANGE_FROM, 0, 255),
                (CONF_RANGE_TO, 0, 255),
            ):
                value = _exact_int(user_input.get(field))
                if value is None or not minimum <= value <= maximum:
                    return self._show_details_form(
                        platform, errors={field: "invalid_exact_integer"}
                    )
                user_input[field] = value
            if (
                user_input.get(CONF_DEVICE_CLASS) == "temperature"
                and user_input[CONF_MIN_TEMP] >= user_input[CONF_MAX_TEMP]
            ):
                return self._show_details_form(
                    platform, errors={"base": "invalid_temperature_range"}
                )
            if (
                user_input.get(CONF_DEVICE_CLASS) == "temperature"
                and user_input[CONF_RANGE_FROM] == user_input[CONF_RANGE_TO]
            ):
                return self._show_details_form(
                    platform, errors={"base": "invalid_raw_range"}
                )

        if platform == "climate":
            for field, minimum, maximum in (
                (CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION, 0, 20),
                (CONF_SENSOR_TARGET_TEMP_TOLERANCE, 0, 10),
                (CONF_TARGET_TEMP_BASE, 5, 35),
                (CONF_TARGET_TEMP_NIGHT_REDUCTION, 0, 20),
                (CONF_PI_CONTROL_KP, 0.001, 100),
                (CONF_PI_CONTROL_TN, 0.001, 1440),
            ):
                value = _finite_float(user_input.get(field))
                if value is None or not minimum <= value <= maximum:
                    return self._show_details_form(
                        platform, errors={field: "invalid_number"}
                    )
                user_input[field] = value
            target_range = _exact_int(user_input.get(CONF_SENSOR_TARGET_TEMP_RANGE))
            if target_range is None or not 0 <= target_range <= 20:
                return self._show_details_form(
                    platform,
                    errors={CONF_SENSOR_TARGET_TEMP_RANGE: "invalid_exact_integer"},
                )
            user_input[CONF_SENSOR_TARGET_TEMP_RANGE] = target_range
            try:
                command_frequency = cv.positive_time_period(
                    user_input.get(CONF_COMMAND_FREQUENCY)
                )
            except vol.Invalid:
                command_frequency = timedelta(0)
            if command_frequency < timedelta(seconds=1):
                return self._show_details_form(
                    platform,
                    errors={CONF_COMMAND_FREQUENCY: "invalid_command_frequency"},
                )

        device = {
            "id": self._captured_id,
            "platform": platform,
            "name": self._pending_name,
            "device_class": user_input.get(CONF_DEVICE_CLASS),
            "channel": channel,
            "switch_type": user_input.get(CONF_SWITCH_TYPE),
            "sender_id": sender_id,
            "id_switch": id_switch,
            "device_type": user_input.get(CONF_DEVICE_TYPE),
            "sensor_entity_id": user_input.get(CONF_SENSOR_ENTITY_ID),
            "temperature_frost_protection": user_input.get(
                CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION, 8.0
            ),
            "sensor_target_temperature_range": user_input.get(
                CONF_SENSOR_TARGET_TEMP_RANGE, 5
            ),
            "sensor_target_temperature_update_tolerance": user_input.get(
                CONF_SENSOR_TARGET_TEMP_TOLERANCE, 0.5
            ),
            "target_temperature_base_value": user_input.get(
                CONF_TARGET_TEMP_BASE, 21.0
            ),
            "target_temperature_reduction_night": user_input.get(
                CONF_TARGET_TEMP_NIGHT_REDUCTION, 4.0
            ),
            "command_frequency": user_input.get(CONF_COMMAND_FREQUENCY, "00:17:00"),
            "pi_control_Kp": user_input.get(CONF_PI_CONTROL_KP, 5.0),
            "pi_control_Tn": user_input.get(CONF_PI_CONTROL_TN, 240.0),
            "max_temp": user_input.get(CONF_MAX_TEMP, 40),
            "min_temp": user_input.get(CONF_MIN_TEMP, 0),
            "range_from": user_input.get(CONF_RANGE_FROM, 255),
            "range_to": user_input.get(CONF_RANGE_TO, 0),
        }
        try:
            device = UI_DEVICE_SCHEMA(device)
        except vol.Invalid:
            return self._show_details_form(
                platform, errors={"base": "invalid_device_details"}
            )
        updated_options = self._options_with_added_device(device)
        if updated_options is None:
            return self._show_device_form(errors={"base": "unique_id_exists"})

        return self.async_create_entry(title="", data=updated_options)

    def _options_with_added_device(
        self, device: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Return options containing a device, or None on identity collision."""
        unique_id = _unique_id_for(device)
        registry = er.async_get(self.hass)
        existing_devices = valid_ui_devices(
            self.config_entry.options.get(CONF_UI_DEVICES, [])
        )
        if registry.async_get_entity_id(
            device["platform"], DOMAIN, unique_id
        ) is not None or any(
            _unique_id_for(existing) == unique_id for existing in existing_devices
        ):
            return None
        updated_options = dict(self.config_entry.options)
        updated_options[CONF_UI_DEVICES] = [
            *self.config_entry.options.get(CONF_UI_DEVICES, []),
            device,
        ]
        return updated_options

    def _options_without_pairing_device(
        self,
    ) -> tuple[dict[str, Any], str | None]:
        """Find this flow's persisted pairing device and use the manage path."""
        device = self._pairing_device
        raw_devices = list(self.config_entry.options.get(CONF_UI_DEVICES, []))
        if device is None:
            return dict(self.config_entry.options), "no_ui_devices"
        unique_id = _unique_id_for(device)
        index = next(
            (
                index
                for index, raw in enumerate(raw_devices)
                if (valid := valid_ui_devices([raw]))
                and valid[0]["platform"] == device["platform"]
                and _unique_id_for(valid[0]) == unique_id
            ),
            None,
        )
        if index is None:
            return dict(self.config_entry.options), "no_ui_devices"
        return self._options_without_ui_device_at(index)

    def _options_without_ui_device_at(
        self, index: int
    ) -> tuple[dict[str, Any], str | None]:
        """Remove one valid UI device using the protected shared delete path."""
        raw_devices = list(self.config_entry.options.get(CONF_UI_DEVICES, []))
        if index < 0 or index >= len(raw_devices):
            return dict(self.config_entry.options), "no_ui_devices"
        valid = valid_ui_devices([raw_devices[index]])
        if not valid:
            return dict(self.config_entry.options), "no_ui_devices"
        device = valid[0]
        if combine_hex(device["id"]) in get_known_ids(self.hass):
            return dict(self.config_entry.options), "yaml_managed"

        unique_id = _unique_id_for(device)
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(device["platform"], DOMAIN, unique_id)
        if entity_id is not None:
            registry.async_remove(entity_id)

        raw_devices.pop(index)
        updated_options = dict(self.config_entry.options)
        updated_options[CONF_UI_DEVICES] = raw_devices
        return updated_options, None

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
        updated_options, error = self._options_without_ui_device_at(index)
        if error is not None:
            return self.async_abort(reason=error)
        return self.async_create_entry(title="", data=updated_options)

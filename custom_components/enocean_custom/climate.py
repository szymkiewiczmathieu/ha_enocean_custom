"""Climate control for EnOcean heating actuators."""

from __future__ import annotations

import asyncio
import inspect
import math
import random
from collections.abc import Callable
from datetime import timedelta
from time import monotonic
from typing import Any, override

import voluptuous as vol
from homeassistant.components.climate import (
    ATTR_PRESET_MODE,
    ATTR_TEMPERATURE,
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_NONE,
    PRESET_SLEEP,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate import (
    PLATFORM_SCHEMA as CLIMATE_PLATFORM_SCHEMA,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ID,
    CONF_NAME,
    EVENT_HOMEASSISTANT_START,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
    UnitOfTemperature,
)
from homeassistant.core import CoreState, Event, HomeAssistant, State
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_point_in_time,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.reload import async_setup_reload_service
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from .const import DOMAIN, LOGGER
from .device import EnOceanEntity, build_radio_optional
from .enocean_library.protocol.constants import RORG
from .enocean_library.utils import combine_hex
from .learn import register_known_id
from .schema import CONF_UI_DEVICES, ENOCEAN_ID, exact_finite_int, valid_ui_devices

PROFILE_SRC_D08 = "SRC-D08"
PROFILE_A5_20_04 = "A5-20-04"
DEVICE_SUPPORTED_LIST = [PROFILE_SRC_D08, PROFILE_A5_20_04]

DEFAULT_NAME: str = "EnOcean Climate"
(
    CONF_DEVICE_TYPE,
    CONF_SENDER_ID_SWITCH,
    CONF_SENSOR_ENTITY_ID,
    CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION,
    CONF_SENSOR_TARGET_TEMP_RANGE,
    CONF_SENSOR_TARGET_TEMP_TOLERANCE,
    CONF_TARGET_TEMP_BASE,
    CONF_TARGET_TEMP_NIGHT_REDUCTION,
    CONF_COMMAND_FREQUENCY,
    CONF_PI_CONTROL_KP,
    CONF_PI_CONTROL_TN,
) = (
    "device_type",
    "id_switch",
    "sensor_entity_id",
    "temperature_frost_protection",
    "sensor_target_temperature_range",
    "sensor_target_temperature_update_tolerance",
    "target_temperature_base_value",
    "target_temperature_reduction_night",
    "command_frequency",
    "pi_control_Kp",
    "pi_control_Tn",
)

(
    ATTR_PI_CONTROL_OUTPUT,
    ATTR_PI_CONTROL_UNIT,
    ATTR_TEMPERATURE_COMFORT,
    ATTR_TEMPERATURE_SLEEP,
    ATTR_TEMPERATURE_AWAY,
) = (
    "PI_control_output",
    "PI_control_unit",
    "temperature_comfort",
    "temperature_sleep",
    "temperature_away",
)
ATTR_VALVE_POSITION = "valve_position"
ATTR_VALVE_FAILURE = "valve_failure"
_SENSOR_ATTR_SETPOINT = "SetPoint"
_SENSOR_ATTR_SLIDESWITCH = "SlideSwitch"


def _finite_float(value: object) -> float:
    """Coerce a finite floating-point configuration value."""
    # Review finding P1-H1: YAML `yes`/`true` coerce to 1.0 through
    # float(True) — a boolean is never a temperature or a gain.
    if isinstance(value, bool):
        raise vol.Invalid("value must be a number, not a boolean")
    result = float(value)
    if not math.isfinite(result):
        raise vol.Invalid("value must be finite")
    return result


def _finite_time_period(value: object) -> timedelta:
    """Coerce a positive time period, rejecting booleans and `.inf` YAML."""
    if isinstance(value, bool):
        raise vol.Invalid("value must be a time period, not a boolean")
    try:
        return cv.positive_time_period(value)
    except (OverflowError, ValueError, TypeError) as err:
        raise vol.Invalid("value must be a valid time period") from err


FINITE_FLOAT = vol.All(_finite_float)
FINITE_INT = exact_finite_int

PLATFORM_SCHEMA = CLIMATE_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_ID): ENOCEAN_ID,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Required(CONF_SENDER_ID_SWITCH): ENOCEAN_ID,
        vol.Required(CONF_DEVICE_TYPE): vol.In(DEVICE_SUPPORTED_LIST),
        vol.Required(CONF_SENSOR_ENTITY_ID): cv.entity_id,
        vol.Optional(CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION, default=8.0): vol.All(
            FINITE_FLOAT,
            vol.Range(min=0, max=20),
        ),
        vol.Optional(CONF_SENSOR_TARGET_TEMP_RANGE, default=5): vol.All(
            FINITE_INT,
            vol.Range(min=0, max=20),
        ),
        vol.Optional(CONF_SENSOR_TARGET_TEMP_TOLERANCE, default=0.5): vol.All(
            FINITE_FLOAT,
            vol.Range(min=0, max=10),
        ),
        vol.Optional(CONF_TARGET_TEMP_BASE, default=21.0): vol.All(
            FINITE_FLOAT,
            vol.Range(min=5, max=35),
        ),
        vol.Optional(CONF_TARGET_TEMP_NIGHT_REDUCTION, default=4.0): vol.All(
            FINITE_FLOAT,
            vol.Range(min=0, max=20),
        ),
        vol.Optional(CONF_COMMAND_FREQUENCY, default="00:17:00"): vol.All(
            _finite_time_period,
            vol.Range(min=timedelta(seconds=1)),
        ),
        vol.Optional(CONF_PI_CONTROL_KP, default=5.0): vol.All(
            FINITE_FLOAT,
            vol.Range(min=0.001, max=100),
        ),
        vol.Optional(CONF_PI_CONTROL_TN, default=240.0): vol.All(
            FINITE_FLOAT,
            vol.Range(min=0.001, max=1440),
        ),
    }
)


def generate_unique_id(dev_id: list[int], channel: int) -> str:
    """Return the legacy-compatible channel identity."""
    return f"{combine_hex(dev_id)}-{channel}"


def _migrate_to_new_unique_id(
    hass: HomeAssistant,
    dev_id: list[int],
    channel: int,
) -> None:
    """Upgrade the historical channel-less climate registry identity."""
    registry = er.async_get(hass)
    old_identity = str(combine_hex(dev_id))
    entity_id = registry.async_get_entity_id(Platform.CLIMATE, DOMAIN, old_identity)
    if entity_id is None:
        return
    try:
        registry.async_update_entity(
            entity_id,
            new_unique_id=generate_unique_id(dev_id, channel),
        )
    except ValueError:
        LOGGER.warning(
            "Skipped EnOcean climate identity migration because the target "
            "identity already exists"
        )
    else:
        LOGGER.debug("Migrated EnOcean climate to a channel-aware identity")


def _entity_from_config(config: ConfigType) -> EnOceanClimate:
    """Construct one climate entity from validated YAML-compatible data."""
    return EnOceanClimate(
        config[CONF_ID],
        config.get(CONF_NAME, DEFAULT_NAME),
        config.get(
            CONF_SENDER_ID_SWITCH,
            config.get("sender_id_switch", []),
        ),
        config[CONF_SENSOR_ENTITY_ID],
        config.get(CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION, 8.0),
        config.get(CONF_SENSOR_TARGET_TEMP_RANGE, 5),
        config.get(CONF_SENSOR_TARGET_TEMP_TOLERANCE, 0.5),
        config.get(CONF_TARGET_TEMP_BASE, 21.0),
        config.get(CONF_TARGET_TEMP_NIGHT_REDUCTION, 4.0),
        config.get(CONF_COMMAND_FREQUENCY, timedelta(minutes=17)),
        config.get(CONF_PI_CONTROL_KP, 5.0),
        config.get(CONF_PI_CONTROL_TN, 240.0),
        device_type=config.get(CONF_DEVICE_TYPE, PROFILE_SRC_D08),
    )


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Create a YAML-configured heating controller."""
    climate_platforms = [Platform.CLIMATE]
    await async_setup_reload_service(hass, DOMAIN, climate_platforms)
    device_id = config[CONF_ID]
    _migrate_to_new_unique_id(hass, device_id, 0)
    register_known_id(hass, device_id)
    add_entities([_entity_from_config(config)])
    _register_climate_services()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create heating controllers stored in the entry options."""
    entities: list[EnOceanClimate] = []
    for row in valid_ui_devices(entry.options.get(CONF_UI_DEVICES, [])):
        if row["platform"] != "climate":
            continue
        config = PLATFORM_SCHEMA(
            {
                "platform": DOMAIN,
                CONF_ID: row["id"],
                CONF_NAME: row["name"],
                CONF_SENDER_ID_SWITCH: row["id_switch"],
                CONF_DEVICE_TYPE: row["device_type"],
                CONF_SENSOR_ENTITY_ID: row["sensor_entity_id"],
                CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION: row[
                    CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION
                ],
                CONF_SENSOR_TARGET_TEMP_RANGE: row[CONF_SENSOR_TARGET_TEMP_RANGE],
                CONF_SENSOR_TARGET_TEMP_TOLERANCE: row[
                    CONF_SENSOR_TARGET_TEMP_TOLERANCE
                ],
                CONF_TARGET_TEMP_BASE: row[CONF_TARGET_TEMP_BASE],
                CONF_TARGET_TEMP_NIGHT_REDUCTION: row[CONF_TARGET_TEMP_NIGHT_REDUCTION],
                CONF_COMMAND_FREQUENCY: row[CONF_COMMAND_FREQUENCY],
                CONF_PI_CONTROL_KP: row[CONF_PI_CONTROL_KP],
                CONF_PI_CONTROL_TN: row[CONF_PI_CONTROL_TN],
            }
        )
        _migrate_to_new_unique_id(hass, config[CONF_ID], 0)
        entities.append(_entity_from_config(config))
    async_add_entities(entities)
    _register_climate_services()


def _register_climate_services() -> None:
    """Register both historical entity-scoped teach-in services."""
    try:
        platform = entity_platform.async_get_current_platform()
    except RuntimeError:
        return
    platform.async_register_entity_service(
        "climate_teach_in_actor",
        {},
        "teach_in_actor",
    )
    platform.async_register_entity_service(
        "climate_teach_in_actor_switch",
        {},
        "teach_in_actor_switch",
    )


class EnOceanClimate(EnOceanEntity, ClimateEntity, RestoreEntity):  # A5/F6 control
    """Control a legacy SRC-D08 or an A5-20-04 radiator valve."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    _CONTROL_STATE_ATTRIBUTES = (
        "_attr_hvac_mode",
        "_attr_preset_mode",
        "_attr_target_temp",
        "_attr_target_temp_comfort",
        "_attr_target_temp_sleep",
        "_attr_target_temp_away",
        "_attr_current_temperature",
        "_sensor_target_temp",
        "_attr_pi_control_output",
        "_pi_control_integrator_state",
        "_pi_control_error",
        "_pi_control_update_time",
    )

    def __init__(
        self,
        dev_id: list[int],
        dev_name: str,
        control_sender_id: list[int],
        temperature_entity_id: str,
        frost_temperature: float,
        panel_target_span: int,
        panel_update_tolerance: float,
        base_temperature: float,
        night_reduction: float,
        refresh_interval: timedelta,
        proportional_gain: float,
        integral_time: float,
        *,
        device_type: str = PROFILE_SRC_D08,
    ) -> None:
        """Initialize controller state without assuming an actuator response."""
        super().__init__(list(dev_id), dev_name)
        self._attr_name = dev_name
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        self._attr_hvac_modes = [HVACMode("heat"), HVACMode("off")]
        self._attr_preset_modes = [
            PRESET_BOOST,
            PRESET_COMFORT,
            PRESET_SLEEP,
            PRESET_AWAY,
        ]
        self._attr_hvac_mode: HVACMode | None = None
        self._attr_preset_mode: str | None = PRESET_NONE
        self._attr_current_temperature: float | None = None
        self._attr_target_temp: float | None = None
        self._attr_target_temp_comfort: float | None = None
        self._attr_target_temp_sleep: float | None = None
        self._attr_target_temp_away: float | None = None
        self._attr_pi_control_output: float | None = None
        self._attr_unique_id = generate_unique_id(dev_id, channel=0)

        self._profile = device_type
        self._sender_id = list(dev_id)
        self._sender_id_switch = list(control_sender_id)
        self.sensor_entity_id = temperature_entity_id
        self._target_temp_base = base_temperature
        self._target_temp_frost_protection = frost_temperature
        self._sensor_target_temp: float | None = None
        self._sensor_target_temp_range = panel_target_span
        self._sensor_target_temp_tolerance = panel_update_tolerance
        self._target_temp_reduction_night = night_reduction
        self._command_frequency = refresh_interval
        self._pi_control_Kp = proportional_gain
        self._pi_control_Tn = integral_time
        self._pi_control_error = 0.0
        self._pi_control_update_time = monotonic()
        self._pi_control_integrator_state: float | None = None
        self._control_lock = asyncio.Lock()
        self._attr_valve_position: int | None = None
        self._attr_valve_failure: int | None = None

    async def _async_create_timer(self, _time=None) -> None:
        """Install the periodic refresh and bind its cancellation to the entity."""
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_periodic_control,
                self._command_frequency,
            )
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Restore control state and subscribe to the external thermometer."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self.sensor_entity_id],
                self._async_handle_sensor_change,
            )
        )

        schedule_random = random.Random(self._attr_unique_id)
        delay = schedule_random.uniform(0, self._command_frequency.total_seconds())
        self.async_on_remove(
            async_track_point_in_time(
                self.hass,
                self._async_create_timer,
                dt_util.now() + timedelta(seconds=delay),
            )
        )

        await self._async_restore_control_state()

        async def startup_update(*_: Any) -> None:
            sensor_state: State | None = self.hass.states.get(self.sensor_entity_id)
            if sensor_state is None or sensor_state.state in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            ):
                return
            await self._async_commit_control_change(
                lambda: self._async_get_sensor_update(sensor_state)
            )

        if self.hass.state is CoreState.running:
            await startup_update()
        else:
            self.async_on_remove(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_START,
                    startup_update,
                )
            )

    async def _async_restore_control_state(self) -> None:
        """Restore safe finite values, filling absent state with profile defaults."""
        defaults = {
            "_attr_target_temp": self._target_temp_base,
            "_attr_target_temp_comfort": self._target_temp_base,
            "_attr_target_temp_sleep": self._target_temp_base - 5,
            "_attr_target_temp_away": self._target_temp_base - 5,
            "_attr_pi_control_output": 0.0,
        }
        previous = await self.async_get_last_state()
        if previous is None:
            for attribute, default in defaults.items():
                setattr(self, attribute, default)
            self._attr_preset_mode = str(PRESET_COMFORT)
            self._attr_hvac_mode = HVACMode("off")
            self._pi_control_integrator_state = 0.0
            return

        state_attributes = {
            "_attr_target_temp": ATTR_TEMPERATURE,
            "_attr_target_temp_comfort": ATTR_TEMPERATURE_COMFORT,
            "_attr_target_temp_sleep": ATTR_TEMPERATURE_SLEEP,
            "_attr_target_temp_away": ATTR_TEMPERATURE_AWAY,
            "_attr_pi_control_output": ATTR_PI_CONTROL_OUTPUT,
        }
        for attribute, state_key in state_attributes.items():
            restored = self._restored_float(previous.attributes.get(state_key))
            setattr(
                self,
                attribute,
                restored if restored is not None else defaults[attribute],
            )

        if previous.state in (HVACMode.HEAT, HVACMode.OFF):
            self._attr_hvac_mode = HVACMode(previous.state)
        else:
            self._attr_hvac_mode = HVACMode("off")
        restored_preset = previous.attributes.get(ATTR_PRESET_MODE)
        self._attr_preset_mode = (
            restored_preset
            if restored_preset in self._attr_preset_modes
            else PRESET_COMFORT
        )
        self._pi_control_integrator_state = float(self._attr_pi_control_output or 0)

    @staticmethod
    def _restored_float(value: Any) -> float | None:
        """Return a finite restored float or None."""
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose PI diagnostics, preset temperatures, and valve feedback."""
        output = self._attr_pi_control_output
        return {
            ATTR_PI_CONTROL_OUTPUT: round(output) if output is not None else None,
            ATTR_PI_CONTROL_UNIT: "%",
            ATTR_TEMPERATURE_COMFORT: getattr(self, "_attr_target_temp_comfort", None),
            ATTR_TEMPERATURE_SLEEP: getattr(self, "_attr_target_temp_sleep", None),
            ATTR_TEMPERATURE_AWAY: getattr(self, "_attr_target_temp_away", None),
            ATTR_VALVE_POSITION: self._attr_valve_position,
            ATTR_VALVE_FAILURE: self._attr_valve_failure,
        }

    @property
    def hvac_action(self) -> HVACAction:
        """Describe whether the controller currently requests heat."""
        if self._attr_hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if (self._attr_pi_control_output or 0) < 5:
            return HVACAction.IDLE
        return HVACAction.HEATING

    @property
    def target_temperature(self) -> float | None:
        """Expose frost protection while the entity is off."""
        if self._attr_hvac_mode == HVACMode.OFF:
            off_target = self._target_temp_frost_protection
            return off_target
        visible_target = self._attr_target_temp
        return visible_target

    @property
    def min_temp(self) -> float:
        """Return the mode-dependent lower target bound."""
        if self._attr_hvac_mode == HVACMode.OFF:
            frost_limit = self._target_temp_frost_protection
            return frost_limit
        if self._attr_preset_mode == PRESET_BOOST:
            boost_limit = self.max_temp
            return boost_limit
        return self._target_temp_base - 10

    @property
    def max_temp(self) -> float:
        """Return the mode-dependent upper target bound."""
        if self._attr_hvac_mode == HVACMode.OFF:
            off_maximum = self._target_temp_frost_protection
            return off_maximum
        return self._target_temp_base + 10

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Stage an HVAC mode until every serial response is RET_OK."""
        if hvac_mode not in (HVACMode.HEAT, HVACMode.OFF):
            LOGGER.error("Unrecognized HVAC mode: %s", hvac_mode)
            return
        await self._async_commit_control_change(
            lambda: setattr(self, "_attr_hvac_mode", hvac_mode)
        )

    @override
    async def async_turn_on(self) -> None:
        """Enable heating."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    @override
    async def async_turn_off(self) -> None:
        """Disable heating."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Stage a new target temperature until the command is acknowledged."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None or self._attr_hvac_mode == HVACMode.OFF:
            return
        await self._async_commit_control_change(
            lambda: setattr(self, "_attr_target_temp", temperature)
        )

    async def _async_handle_sensor_change(
        self,
        event: Event[EventStateChangedData],
    ) -> None:
        """Run control after a valid external temperature update."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            return
        await self._async_commit_control_change(
            lambda: self._async_get_sensor_update(new_state)
        )

    async def _async_get_sensor_update(self, state: State) -> bool:
        """Validate and incorporate external temperature/panel data."""
        try:
            current = float(state.state)
            if not math.isfinite(current):
                raise ValueError("sensor state is not finite")
            self._attr_current_temperature = current

            if self._profile == PROFILE_A5_20_04:
                return True

            raw_setpoint = state.attributes[_SENSOR_ATTR_SETPOINT]
            slide_switch = state.attributes[_SENSOR_ATTR_SLIDESWITCH]
            requested = (
                raw_setpoint * 2 / 255 - 1
            ) * self._sensor_target_temp_range + self._target_temp_base
            if not slide_switch:
                requested = max(
                    requested - self._target_temp_reduction_night,
                    self.min_temp,
                )

            if not isinstance(self._sensor_target_temp, (int, float)):
                self._sensor_target_temp = requested
            elif (
                abs(self._sensor_target_temp - requested)
                > self._sensor_target_temp_tolerance
            ):
                self._sensor_target_temp = requested
                self._attr_hvac_mode = HVACMode("heat")
                self._attr_target_temp = requested
                self._attr_preset_mode = (
                    PRESET_COMFORT if slide_switch else PRESET_SLEEP
                )
        except (KeyError, TypeError, ValueError) as err:
            LOGGER.error("Unable to update climate from its sensor: %s", err)
            return False
        return True

    async def _async_periodic_control(self, event_time=None) -> None:
        """Serialize a keep-alive refresh with user-initiated transactions."""
        await self._async_commit_control_change(
            lambda: None,
            event_time=event_time,
        )

    @override
    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Stage a preset transition and preserve the previous preset target."""
        if preset_mode not in self._attr_preset_modes:
            raise ValueError(
                f"Unsupported preset_mode {preset_mode}; expected "
                f"one of {self._attr_preset_modes}"
            )
        if (
            self._attr_hvac_mode != HVACMode.HEAT
            or preset_mode == self._attr_preset_mode
        ):
            return

        def apply_preset() -> None:
            saved_targets = {
                PRESET_COMFORT: "_attr_target_temp_comfort",
                PRESET_SLEEP: "_attr_target_temp_sleep",
                PRESET_AWAY: "_attr_target_temp_away",
            }
            if (old_target := saved_targets.get(self._attr_preset_mode)) is not None:
                setattr(self, old_target, self._attr_target_temp)
            self._attr_preset_mode = preset_mode
            if preset_mode in (PRESET_BOOST,):
                self._attr_target_temp = float(self.max_temp)
            else:
                self._attr_target_temp = getattr(self, saved_targets[preset_mode])

        await self._async_commit_control_change(apply_preset)

    def _control_state(self) -> dict[str, Any]:
        """Snapshot all mutable state touched by a control transaction."""
        return {
            attribute: getattr(self, attribute)
            for attribute in self._CONTROL_STATE_ATTRIBUTES
        }

    def _restore_control_state(self, state: dict[str, Any]) -> None:
        """Replace mutable control state from a transaction snapshot."""
        for attribute, value in state.items():
            setattr(self, attribute, value)

    async def _async_commit_control_change(
        self,
        apply_change: Callable[[], Any],
        *,
        event_time=None,
    ) -> None:
        """Commit staged state only if every queued ESP3 command returns OK."""
        async with self._control_lock:
            previous_state = self._control_state()
            change_result = apply_change()
            if inspect.isawaitable(change_result):
                change_result = await change_result
            if change_result is False:
                self._restore_control_state(previous_state)
                self.async_write_ha_state()
                return

            responses: list[bool] = []
            expected_responses = 0
            all_locally_queued = False
            sending_finished = False
            finalized = False
            candidate_state: dict[str, Any] | None = None
            completion = asyncio.get_running_loop().create_future()

            def finish_when_ready() -> None:
                nonlocal finalized
                if (
                    finalized
                    or not sending_finished
                    or len(responses) < expected_responses
                ):
                    return
                finalized = True
                if (
                    all_locally_queued
                    and all(responses[:expected_responses])
                    and candidate_state is not None
                ):
                    self._restore_control_state(candidate_state)
                self.async_write_ha_state()
                if not completion.done():
                    completion.set_result(None)

            def response_received(accepted: bool) -> None:
                if finalized:
                    return
                responses.append(accepted)
                finish_when_ready()

            send_results = await self._async_control_heating(
                event_time=event_time,
                response_callback=response_received,
            )
            candidate_state = self._control_state()
            self._restore_control_state(previous_state)
            expected_responses = sum(send_results)
            all_locally_queued = bool(send_results) and all(send_results)
            sending_finished = True

            if expected_responses == 0 or not all_locally_queued:
                finalized = True
                self.async_write_ha_state()
                return

            finish_when_ready()
            if completion.done():
                return
            try:
                await completion
            except asyncio.CancelledError:
                finalized = True
                raise

    async def _async_control_heating(
        self,
        event_time=None,
        response_callback: Callable[[bool], None] | None = None,
    ) -> list[bool]:
        """Calculate PI output and queue the profile-specific actor commands."""
        if response_callback is None:
            await self._async_commit_control_change(
                lambda: None,
                event_time=event_time,
            )
            return []

        if self._attr_current_temperature is None:
            if self._attr_hvac_mode == HVACMode.OFF:
                LOGGER.warning("Temperature unavailable; closing %s", self.dev_name)
                if getattr(self, "_profile", PROFILE_SRC_D08) == PROFILE_A5_20_04:
                    close_payload = self._a5_20_04_payload(
                        0,
                        self._target_temp_frost_protection,
                    )
                    return [
                        self._send_a5_20_04(
                            close_payload,
                            response_callback=response_callback,
                        )
                    ]
                return [
                    self.sendPacket(
                        [0x00],
                        response_callback=response_callback,
                    )
                ]
            LOGGER.debug(
                "Skipping control for %s because temperature is unavailable",
                self.dev_name,
            )
            return []

        target = (
            self._target_temp_frost_protection
            if self._attr_hvac_mode == HVACMode.OFF
            else self._attr_target_temp
        )
        self._update_pi_controller(target)

        if self._profile == PROFILE_A5_20_04:
            position = (
                0
                if self._attr_hvac_mode == HVACMode.OFF
                else round(self._attr_pi_control_output or 0)
            )
            payload = self._a5_20_04_payload(position, target)
            return [
                self._send_a5_20_04(
                    payload,
                    response_callback=response_callback,
                )
            ]

        setpoint = round((target - self._target_temp_base) * 12.75 + 127.5)
        setpoint = min(max(setpoint, 0), 255)
        protocol_temperature = 255 - round(6.375 * self._attr_current_temperature)
        protocol_temperature = min(max(protocol_temperature, 0), 255)
        thermostat_sent = self.sendPacket(
            [0x00, setpoint, protocol_temperature, 0x09],
            response_callback=response_callback,
        )
        rocker_value = 0x00 if self._attr_hvac_mode == HVACMode.OFF else 0x10
        rocker_sent = self.sendPacket(
            [rocker_value],
            response_callback=response_callback,
        )
        return [thermostat_sent, rocker_sent]

    def _update_pi_controller(self, target: float) -> None:
        """Advance a bounded PI controller with anti-wind-up."""
        now = monotonic()
        elapsed_minutes = max(0.0, now - self._pi_control_update_time) / 60
        self._pi_control_update_time = now
        if not isinstance(self._attr_pi_control_output, (int, float)):
            self._attr_pi_control_output = 0.0
        if not isinstance(self._pi_control_integrator_state, (int, float)):
            self._pi_control_integrator_state = 0.0

        if 0 < self._attr_pi_control_output < 100:
            self._pi_control_integrator_state += (
                elapsed_minutes * self._pi_control_Kp * self._pi_control_error
            )
        self._pi_control_error = target - self._attr_current_temperature
        raw_output = self._pi_control_Kp * (
            self._pi_control_error
            + self._pi_control_integrator_state / self._pi_control_Tn
        )
        self._attr_pi_control_output = min(max(raw_output, 0.0), 100.0)

    def _a5_20_04_payload(self, position: int, target: float) -> list[int]:
        """Encode A5-20-04 direction-2 DB3..DB0."""
        target_raw = round((min(max(target, 10), 30) - 10) * 255 / 20)
        wake_up = self._wake_up_cycle_code(self._command_frequency)
        return [min(max(position, 0), 100), target_raw, wake_up, 0x08]

    @staticmethod
    def _wake_up_cycle_code(interval: timedelta) -> int:
        """Map a refresh interval to the nearest A5-20-04 wake-up code."""
        # EEP 2.6.8 A5-20-04, WUC table: code 0 = 10 s, 1 = 60 s,
        # 2..49 = 90..1500 s in 30 s steps, 50..63 = 3..42 h in 3 h steps.
        # Review finding P1-01: pick the globally nearest legal duration —
        # branchy shortcuts made codes 1 and 49 unreachable at boundaries
        # (26 minutes must not become a 3-hour wake-up).
        seconds = interval.total_seconds()
        durations = (
            [10.0, 60.0]
            + [30.0 * (code + 1) for code in range(2, 50)]
            + [3600.0 * hours for hours in range(3, 43, 3)]
        )
        return min(range(64), key=lambda code: abs(durations[code] - seconds))

    def teach_in_actor(self):
        """Teach the configured thermostat/valve actor."""
        if self._profile == PROFILE_A5_20_04:
            # 4BS bidirectional variation 3 response: A5-20-04, manufacturer 0,
            # EEP accepted, sender stored, response, and LRN=0.
            self._send_a5_20_04([0x80, 0x20, 0x00, 0xF0])
            return
        legacy_teach_in = [0x40, 0x30, 0x02, 0x86]
        self.sendPacket(legacy_teach_in)

    def teach_in_actor_switch(self):
        """Teach the legacy SRC-D08 rocker input."""
        if self._profile == PROFILE_SRC_D08:
            rocker_teach_in = [0x70]
            self.sendPacket(rocker_teach_in)

    def _send_a5_20_04(
        self,
        payload: list[int],
        response_callback: Callable[[bool], None] | None = None,
    ) -> bool:
        """Send one addressed A5-20-04 frame from the controller identity."""
        command = [
            RORG.BS4,
            *payload,
            *self._sender_id_switch,
            0x00,
        ]
        return self.send_command(
            command,
            build_radio_optional(self.dev_id),
            0x01,
            response_callback=response_callback,
        )

    def sendPacket(
        self,
        data: list[int],
        response_callback: Callable[[bool], None] | None = None,
    ) -> bool:
        """Encode the historical SRC-D08 panel or rocker telegram."""
        data_length = len(data)
        if data_length == 1:
            command = [
                RORG.RPS,
                data[0],
                *self._sender_id_switch,
                0x30,
            ]
        else:
            command = [
                RORG.BS4,
                *data,
                *self._sender_id,
                0x00,
            ]
        return self.send_command(
            command,
            build_radio_optional(),
            0x01,
            response_callback=response_callback,
        )

    @override
    def value_changed(self, packet) -> None:
        """Decode A5-20-04 valve position, temperature, and failure feedback."""
        if (
            self._profile != PROFILE_A5_20_04
            or packet.rorg != RORG.BS4
            or len(packet.data) < 5
        ):
            return
        db3, db2, db1, db0 = packet.data[1:5]
        self._attr_valve_position = min(db3, 100)
        if db0 & 0x01:
            self._attr_valve_failure = db1
        else:
            self._attr_valve_failure = None
            self._attr_current_temperature = round(10 + db1 * 20 / 255, 1)
        if db0 & 0x02:
            self._attr_target_temp = round(10 + db2 * 20 / 255, 1)
        self.schedule_update_ha_state()

"""Smoke-test the integration against the pinned Home Assistant runtime."""

from __future__ import annotations

import asyncio
import json
import sys
from collections import deque
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import voluptuous as vol
import yaml
from homeassistant.components.climate.const import HVACMode
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_ID,
    CONF_NAME,
    CONF_PLATFORM,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.enocean_custom import (
    binary_sensor,
    climate,
    dongle,
    light,
    sensor,
    switch,
)
from custom_components.enocean_custom.const import DATA_ENOCEAN, ENOCEAN_DONGLE
from custom_components.enocean_custom.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.enocean_custom.enocean_library.communicators import (
    serialcommunicator,
)
from custom_components.enocean_custom.enocean_library.communicators.communicator import (
    Communicator,
)
from custom_components.enocean_custom.enocean_library.protocol import crc8
from custom_components.enocean_custom.enocean_library.protocol.constants import (
    PACKET,
    PARSE_RESULT,
    RETURN_CODE,
)
from custom_components.enocean_custom.enocean_library.protocol.packet import (
    Packet,
    ResponsePacket,
)


async def _assert_off_without_sensor_sends_switch_off() -> None:
    """OFF must always reach the actor, even before a sensor value exists."""
    entity = object.__new__(climate.EnOceanClimate)
    entity.dev_name = "test"
    entity._attr_hvac_mode = HVACMode.OFF
    entity._attr_preset_mode = None
    entity._attr_target_temp = None
    entity._attr_current_temperature = None
    packets: list[list[int]] = []

    def record_packet(data: list[int], response_callback=None) -> bool:
        packets.append(data)
        return True

    entity.sendPacket = record_packet  # type: ignore[method-assign]

    await entity._async_control_heating(response_callback=lambda _accepted: None)

    if packets != [[0x00]]:
        raise AssertionError(f"OFF without sensor sent unexpected packets: {packets}")


def _new_transactional_climate():
    """Build only the state required by the transaction helper."""
    entity = object.__new__(climate.EnOceanClimate)
    entity._attr_hvac_mode = HVACMode.HEAT
    entity._attr_preset_mode = "comfort"
    entity._attr_target_temp = 20.0
    entity._attr_target_temp_comfort = 20.0
    entity._attr_target_temp_sleep = 18.0
    entity._attr_target_temp_away = 16.0
    entity._attr_current_temperature = 19.0
    entity._sensor_target_temp = None
    entity._attr_pi_control_output = 25.0
    entity._pi_control_integrator_state = 1.0
    entity._pi_control_error = 0.5
    entity._pi_control_update_time = 100.0
    entity._control_lock = asyncio.Lock()
    return entity


async def _assert_climate_response_transactions() -> None:
    """Climate state must remain staged until every ESP3 response is OK."""

    async def exercise(send_results: list[bool], responses: list[bool]):
        entity = _new_transactional_climate()
        callbacks = []
        writes = []

        async def control(event_time=None, response_callback=None):
            if event_time is not None:
                raise AssertionError("transaction callback was passed as event_time")
            callback = response_callback
            callbacks.extend([callback] * sum(send_results))
            return send_results

        entity._async_control_heating = control  # type: ignore[method-assign]
        entity.async_write_ha_state = (  # type: ignore[method-assign]
            lambda: writes.append(True)
        )
        transaction = asyncio.create_task(
            entity._async_commit_control_change(
                lambda: setattr(entity, "_attr_hvac_mode", HVACMode.OFF)
            )
        )
        await asyncio.sleep(0)
        if entity._attr_hvac_mode != HVACMode.HEAT:
            raise AssertionError("Climate exposed queued state before ESP3 responses")
        for callback, accepted in zip(callbacks, responses, strict=True):
            callback(accepted)
        await transaction
        return entity, writes

    accepted, _ = await exercise([True, True], [True, True])
    if accepted._attr_hvac_mode != HVACMode.OFF:
        raise AssertionError("Climate did not commit after two RET_OK responses")

    rejected, _ = await exercise([True, True], [True, False])
    if rejected._attr_hvac_mode != HVACMode.HEAT:
        raise AssertionError("Climate retained state after an ESP3 rejection")

    partial, _ = await exercise([True, False], [True])
    if partial._attr_hvac_mode != HVACMode.HEAT:
        raise AssertionError("Climate retained state after a local partial rejection")

    local_rejection, _ = await exercise([False, False], [])
    if local_rejection._attr_hvac_mode != HVACMode.HEAT:
        raise AssertionError("Climate retained state after local queue rejection")


async def _assert_real_climate_send_chain() -> None:
    """Exercise async setter -> controller -> sendPacket -> dongle callbacks."""

    class CapturingDongle:
        def __init__(self):
            self.packets = []
            self.callbacks = []

        def send(self, packet, response_callback=None):
            self.packets.append(packet)
            self.callbacks.append(response_callback)
            return True

    radio_id = [0x01, 0x23, 0x45, 0x67]
    radio_switch_id = [0x89, 0xAB, 0xCD, 0xEF]
    entity = climate.EnOceanClimate(
        radio_id,
        "transaction-chain",
        radio_switch_id,
        "sensor.test_temperature",
        8.0,
        5,
        0.5,
        21.0,
        4.0,
        timedelta(minutes=17),
        5.0,
        240.0,
    )
    entity._attr_hvac_mode = HVACMode.HEAT
    entity._attr_preset_mode = "comfort"
    entity._attr_target_temp = 20.0
    entity._attr_target_temp_comfort = 20.0
    entity._attr_target_temp_sleep = 18.0
    entity._attr_target_temp_away = 16.0
    entity._attr_current_temperature = 19.0
    entity._attr_pi_control_output = 25.0
    entity._pi_control_integrator_state = 1.0
    entity._pi_control_error = 0.5
    writes = []
    entity.async_write_ha_state = lambda: writes.append(True)  # type: ignore[method-assign]
    fake_dongle = CapturingDongle()
    entity.hass = SimpleNamespace(data={DATA_ENOCEAN: {ENOCEAN_DONGLE: fake_dongle}})

    off_transaction = asyncio.create_task(entity.async_set_hvac_mode(HVACMode.OFF))
    await asyncio.sleep(0)
    if entity._attr_hvac_mode != HVACMode.HEAT:
        raise AssertionError("real climate chain exposed OFF before ESP3 responses")
    if len(fake_dongle.packets) != 2 or any(
        callback is None for callback in fake_dongle.callbacks
    ):
        raise AssertionError("real climate chain did not propagate both callbacks")
    fake_dongle.callbacks[0](True)
    if entity._attr_hvac_mode != HVACMode.HEAT:
        raise AssertionError("real climate chain committed after only one response")

    periodic_transaction = asyncio.create_task(
        entity._async_periodic_control(event_time=object())
    )
    await asyncio.sleep(0)
    if len(fake_dongle.packets) != 2:
        raise AssertionError("periodic climate refresh bypassed the pending OFF lock")

    fake_dongle.callbacks[1](True)
    await off_transaction
    if entity._attr_hvac_mode != HVACMode.OFF:
        raise AssertionError("real climate chain did not commit after both RET_OK")

    await asyncio.sleep(0)
    if len(fake_dongle.packets) != 4:
        raise AssertionError(
            "serialized periodic climate refresh did not queue one batch"
        )
    periodic_switch_values = [
        packet.data[1]
        for packet in fake_dongle.packets[2:]
        if packet.data and packet.data[0] == 0xF6
    ]
    if periodic_switch_values != [0x00]:
        raise AssertionError(
            "periodic climate refresh contradicted the committed OFF transaction"
        )
    for callback in fake_dongle.callbacks[2:]:
        callback(True)
    await periodic_transaction

    concurrent = entity
    concurrent._attr_hvac_mode = HVACMode.HEAT
    concurrent._attr_target_temp = 20.0
    concurrent_dongle = CapturingDongle()
    concurrent.hass = SimpleNamespace(
        data={DATA_ENOCEAN: {ENOCEAN_DONGLE: concurrent_dongle}}
    )
    off_task = asyncio.create_task(concurrent.async_set_hvac_mode(HVACMode.OFF))
    await asyncio.sleep(0)
    temperature_task = asyncio.create_task(
        concurrent.async_set_temperature(temperature=23.0)
    )
    await asyncio.sleep(0)
    if len(concurrent_dongle.packets) != 2:
        raise AssertionError("concurrent climate setters bypassed transaction ordering")
    for callback in concurrent_dongle.callbacks[:2]:
        callback(True)
    await off_task
    await asyncio.sleep(0)
    if len(concurrent_dongle.packets) != 4:
        raise AssertionError("queued climate setter did not run after the first commit")
    for callback in concurrent_dongle.callbacks[2:]:
        callback(True)
    await temperature_task
    if (
        concurrent._attr_hvac_mode != HVACMode.OFF
        or concurrent._attr_target_temp != 23.0
    ):
        raise AssertionError("concurrent climate setters committed stale visible state")


async def _assert_legacy_send_only_light_identity_is_preserved() -> None:
    """Migrate the legacy ID without colliding in HA's real registry."""
    with TemporaryDirectory() as config_dir:
        hass = HomeAssistant(config_dir)
        try:
            await ar.async_load(hass, load_empty=True)
            dr.async_setup(hass)
            await dr.async_load(hass, load_empty=True)
            await er.async_load(hass, load_empty=True)
            registry = er.async_get(hass)
            legacy = registry.async_get_or_create(
                "light",
                "enocean_custom",
                "0",
                suggested_object_id="legacy_send_only",
            )
            captured = []
            for name, sender_id in (
                ("legacy-send-only", [1, 2, 3, 4]),
                ("second-send-only", [5, 6, 7, 8]),
            ):
                await light.async_setup_platform(
                    hass,
                    {
                        CONF_ID: [],
                        CONF_NAME: name,
                        light.CONF_SENDER_ID: sender_id,
                    },
                    captured.extend,
                )

            unique_ids = [entity.unique_id for entity in captured]
            if len(unique_ids) != 2 or len(set(unique_ids)) != 2 or "0" in unique_ids:
                raise AssertionError(
                    "send-only light identities collided after migration"
                )
            if (
                registry.async_get_entity_id("light", "enocean_custom", unique_ids[0])
                != legacy.entity_id
                or registry.async_get_entity_id("light", "enocean_custom", "0")
                is not None
            ):
                raise AssertionError(
                    "legacy light entity_id was not preserved during migration"
                )
        finally:
            await hass.async_stop(force=True)


def _assert_ute_does_not_corrupt_following_frame() -> None:
    """Ensure UTE handling consumes neither the next sync byte nor its frame."""
    received = []
    communicator = Communicator(callback=received.append)
    ute_frame = bytearray(
        [
            0x55,
            0x00,
            0x0D,
            0x07,
            0x01,
            0xFD,
            0xD4,
            0xA0,
            0xFF,
            0x3E,
            0x00,
            0x01,
            0x01,
            0xD2,
            0x01,
            0x94,
            0xE3,
            0xB9,
            0x00,
            0x01,
            0xFF,
            0xFF,
            0xFF,
            0xFF,
            0x40,
            0x00,
            0xAB,
        ]
    )
    response_frame = bytearray(Packet(PACKET.RESPONSE, data=[RETURN_CODE.OK]).build())
    communicator._buffer = list(ute_frame + response_frame)
    communicator.parse()
    if communicator._buffer or len(received) != 2:
        raise AssertionError(
            "UTE packet corrupted or prevented parsing of the following RESPONSE"
        )


def _assert_corrupt_header_resyncs_following_frame() -> None:
    """A bogus declared length must not strand the next valid ESP3 frame."""
    received = []
    communicator = Communicator(callback=received.append)
    header_fields = [0xFF, 0xFF, 0x00, int(PACKET.RADIO_ERP1)]
    bad_crc = (crc8.calc(header_fields) + 1) & 0xFF
    valid_response = Packet(PACKET.RESPONSE, data=[RETURN_CODE.OK]).build()
    communicator._buffer = [0x55, *header_fields, bad_crc, *valid_response]

    communicator.parse()

    if communicator._buffer or len(received) != 1:
        raise AssertionError("corrupt ESP3 header stranded the following valid frame")


def _assert_incomplete_frames_preserve_following_frame() -> None:
    """Valid following frames must escape giant and truncated predecessors."""
    valid_response = Packet(PACKET.RESPONSE, data=[RETURN_CODE.OK]).build()

    giant_fields = [0xFF, 0xFF, 0x00, int(PACKET.RADIO_ERP1)]
    giant = Communicator(callback=lambda packet: giant_received.append(packet))
    giant_received = []
    giant._buffer = [
        0x55,
        *giant_fields,
        crc8.calc(giant_fields),
        *valid_response,
    ]
    giant.parse()
    if giant._buffer or len(giant_received) != 1:
        raise AssertionError("valid-CRC giant header poisoned the following frame")

    truncated = Packet(PACKET.COMMON_COMMAND, data=[0x08]).build()[:-1]
    trunc_received = []
    trunc = Communicator(callback=trunc_received.append)
    trunc._buffer = [*truncated, *valid_response]
    trunc.parse()
    if trunc._buffer or len(trunc_received) != 1:
        raise AssertionError("truncated frame consumed the following frame")

    # A valid inner ESP3 byte sequence may legally occur inside the payload of
    # a still-incomplete generic outer packet. It is not evidence that the
    # outer frame is corrupt and must never trigger eager resynchronization.
    nested_response = list(valid_response)
    outer = list(
        Packet(
            PACKET.EVENT,
            data=[0x01, *nested_response, *([0xA5] * 12)],
        ).build()
    )
    outer_prefix = outer[:-5]
    status, remaining, parsed = Packet.parse_msg(outer_prefix)
    if (
        status != PARSE_RESULT.INCOMPLETE
        or remaining != outer_prefix
        or parsed is not None
    ):
        raise AssertionError(
            "incomplete outer ESP3 frame was discarded for a nested frame"
        )


def _assert_generic_frame_timeout_resyncs_following_frame() -> None:
    """A stalled generic frame must release a complete following ESP3 frame."""
    valid_response = list(Packet(PACKET.RESPONSE, data=[RETURN_CODE.OK]).build())
    giant_fields = [0xFF, 0xFF, 0x00, int(PACKET.EVENT)]
    stream = bytes([0x55, *giant_fields, crc8.calc(giant_fields), *valid_response])

    class TimedOutSerial:
        def __init__(self, *_args, **_kwargs):
            self._chunks = deque([stream])
            self.closed = False

        def read(self, _size):
            return self._chunks.popleft() if self._chunks else b""

        @staticmethod
        def write(data):
            return len(data)

        @staticmethod
        def cancel_read():
            return None

        @staticmethod
        def cancel_write():
            return None

        def close(self):
            self.closed = True

    received = []
    received_event = Event()

    def receive(packet):
        received.append(packet)
        received_event.set()

    original_serial = serialcommunicator.serial.Serial
    serialcommunicator.serial.Serial = TimedOutSerial
    worker = None
    try:
        worker = serialcommunicator.SerialCommunicator("redacted", callback=receive)
        worker.FRAME_ASSEMBLY_TIMEOUT_SECONDS = 0.01
        worker.start()
        if not received_event.wait(1):
            raise AssertionError("generic ESP3 timeout stranded the following frame")
        worker.stop()
        worker.join(1)
        if worker.is_alive() or worker._buffer or len(received) != 1:
            raise AssertionError("generic ESP3 timeout did not resynchronize cleanly")
    finally:
        if worker is not None and worker.is_alive():
            worker.stop()
            worker.join(1)
        serialcommunicator.serial.Serial = original_serial


def _assert_migration_logs_redact_hardware_identifiers() -> None:
    """Registry collision logs must not expose EnOcean hardware identities."""
    sentinel = [0x11, 0x22, 0x33, 0x44]
    rendered_identifier = str(int.from_bytes(bytes(sentinel), "big"))

    class CollisionRegistry:
        @staticmethod
        def async_get_entity_id(*_args):
            return "domain.existing"

        @staticmethod
        def async_update_entity(*_args, **_kwargs):
            raise ValueError("collision")

    def assert_module_redacts(module: Any) -> None:
        original_get = module.er.async_get
        original_logger = module.LOGGER
        logger = Mock()
        module.er.async_get = lambda _hass: CollisionRegistry()
        module.LOGGER = logger
        try:
            module._migrate_to_new_unique_id(object(), sentinel, 0)
            rendered = " ".join(str(part) for part in logger.mock_calls)
            if rendered_identifier in rendered:
                raise AssertionError("migration log exposed a hardware identifier")
        finally:
            module.er.async_get = original_get
            module.LOGGER = original_logger

    for module in (switch, climate):
        assert_module_redacts(module)


def _assert_transmit_queue_is_bounded() -> None:
    """Local producers must be rejected before memory growth becomes unbounded."""
    communicator = Communicator()
    packet = Packet(PACKET.COMMON_COMMAND, data=[0x08])
    accepted = [
        communicator.send(packet)
        for _ in range(communicator.TRANSMIT_QUEUE_MAXSIZE + 1)
    ]
    if (
        not all(accepted[:-1])
        or accepted[-1]
        or communicator.transmit.qsize() != communicator.TRANSMIT_QUEUE_MAXSIZE
    ):
        raise AssertionError("EnOcean transmit queue did not enforce its hard limit")


def _assert_base_id_retry_paths() -> None:
    """Refusal and timeout must release the asynchronous Base-ID latch."""
    ute_communicator = Communicator()
    ute_communicator._pending_ute_packets.append(object())
    if not ute_communicator.request_base_id():
        raise AssertionError("UTE Base-ID request was rejected")
    ute_first_request = ute_communicator.transmit.get_nowait()
    ute_communicator._packet_written(ute_first_request)
    ute_refusal = object.__new__(ResponsePacket)
    ute_refusal.response = RETURN_CODE.WRONG_PARAM
    ute_refusal.response_data = []
    ute_communicator._response_received(ute_refusal)
    if not ute_communicator._base_id_requested or ute_communicator.transmit.empty():
        raise AssertionError("UTE Base-ID refusal did not queue its bounded retry")
    ute_retry_request = ute_communicator.transmit.get_nowait()
    ute_communicator._packet_written(ute_retry_request)
    ute_communicator._response_received(ute_refusal)
    if ute_communicator._base_id_requested or not ute_communicator.transmit.empty():
        raise AssertionError("UTE Base-ID retries were not bounded")

    communicator = Communicator()
    if not communicator.request_base_id():
        raise AssertionError("initial Base-ID request was rejected")
    first_request = communicator.transmit.get_nowait()
    communicator._packet_written(first_request)
    refusal = object.__new__(ResponsePacket)
    refusal.response = RETURN_CODE.WRONG_PARAM
    refusal.response_data = []
    communicator._response_received(refusal)
    if communicator._base_id_requested:
        raise AssertionError("refused Base-ID request remained latched")
    if not communicator.request_base_id():
        raise AssertionError("Base-ID retry after refusal was rejected")
    retry_request = communicator.transmit.get_nowait()
    if retry_request is first_request:
        raise AssertionError("Base-ID retry reused stale packet ownership")
    communicator._packet_written(retry_request)
    communicator._response_timed_out(retry_request)
    if communicator._base_id_requested:
        raise AssertionError("timed-out Base-ID request remained latched")
    if not communicator.request_base_id() or communicator.transmit.empty():
        raise AssertionError("Base-ID retry after timeout was not queued")


def main() -> None:
    """Import the integration and verify dangerous climate inputs are rejected."""
    base = {
        CONF_PLATFORM: "enocean_custom",
        CONF_ID: [0x0F, 0x53, 0xD6, 0x83],
        CONF_NAME: "test",
        climate.CONF_SENDER_ID_SWITCH: [0x12, 0x34, 0x56, 0x78],
        climate.CONF_DEVICE_TYPE: "SRC-D08",
        climate.CONF_SENSOR_ENTITY_ID: "sensor.test_temperature",
    }
    validated = climate.PLATFORM_SCHEMA(base)
    if validated[CONF_ID] != base[CONF_ID]:
        raise AssertionError("Valid EnOcean ID was changed by schema validation")

    component_root = ROOT / "custom_components/enocean_custom"
    service_schema = yaml.safe_load((component_root / "services.yaml").read_text())
    service_strings = json.loads((component_root / "strings.json").read_text())[
        "services"
    ]
    for service_name, schema_data in service_schema.items():
        for field in ("name", "description"):
            if schema_data[field] != service_strings[service_name][field]:
                raise AssertionError(
                    f"services.yaml and strings.json disagree for {service_name}.{field}"
                )

    invalid_cases = [
        {**base, CONF_ID: [1, 2, 3]},
        {**base, climate.CONF_PI_CONTROL_TN: 0},
        {**base, climate.CONF_COMMAND_FREQUENCY: "00:00:00"},
    ]
    finite_fields = (
        climate.CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION,
        climate.CONF_SENSOR_TARGET_TEMP_RANGE,
        climate.CONF_SENSOR_TARGET_TEMP_TOLERANCE,
        climate.CONF_TARGET_TEMP_BASE,
        climate.CONF_TARGET_TEMP_NIGHT_REDUCTION,
        climate.CONF_PI_CONTROL_KP,
        climate.CONF_PI_CONTROL_TN,
    )
    invalid_cases.extend(
        {**base, field: value}
        for field in finite_fields
        for value in (
            "inf",
            "-inf",
            "nan",
            float("inf"),
            float("-inf"),
            float("nan"),
        )
    )
    invalid_cases.extend(
        {**base, climate.CONF_SENSOR_TARGET_TEMP_RANGE: value}
        for value in (True, False, 5.9, "5.9")
    )
    out_of_range_values = {
        climate.CONF_SENSOR_TARGET_TEMP_FROST_PROTECTION: 21,
        climate.CONF_SENSOR_TARGET_TEMP_RANGE: 21,
        climate.CONF_SENSOR_TARGET_TEMP_TOLERANCE: 11,
        climate.CONF_TARGET_TEMP_BASE: 36,
        climate.CONF_TARGET_TEMP_NIGHT_REDUCTION: 21,
        climate.CONF_PI_CONTROL_KP: 101,
        climate.CONF_PI_CONTROL_TN: 1441,
    }
    invalid_cases.extend(
        {**base, field: value} for field, value in out_of_range_values.items()
    )
    for config in invalid_cases:
        try:
            climate.PLATFORM_SCHEMA(config)
        except vol.Invalid:
            continue
        raise AssertionError(f"Unsafe climate config accepted: {config}")

    valid_id = [0x01, 0x23, 0x45, 0xFF]
    invalid_ids = (
        [0x01, 0x02, 0x03],
        [0x01, 0x02, 0x03, 0x100],
        [-1, 2, 3, 4],
        [True, 2, 3, 4],
        [1.0, 2, 3, 4],
        [1.9, 2, 3, 4],
        [-0.1, 2, 3, 4],
        [float("inf"), 2, 3, 4],
    )
    invalid_platform_configs = []
    for invalid_id in invalid_ids:
        invalid_platform_configs.extend(
            (
                (
                    switch.PLATFORM_SCHEMA,
                    {CONF_PLATFORM: "enocean_custom", CONF_ID: invalid_id},
                ),
                (
                    binary_sensor.PLATFORM_SCHEMA,
                    {CONF_PLATFORM: "enocean_custom", CONF_ID: invalid_id},
                ),
                (
                    sensor.PLATFORM_SCHEMA,
                    {CONF_PLATFORM: "enocean_custom", CONF_ID: invalid_id},
                ),
                (
                    light.PLATFORM_SCHEMA,
                    {
                        CONF_PLATFORM: "enocean_custom",
                        light.CONF_SENDER_ID: invalid_id,
                    },
                ),
                (
                    light.PLATFORM_SCHEMA,
                    {
                        CONF_PLATFORM: "enocean_custom",
                        light.CONF_SENDER_ID: valid_id,
                        CONF_ID: invalid_id,
                    },
                ),
            )
        )
    invalid_platform_configs.extend(
        (
            (
                sensor.PLATFORM_SCHEMA,
                {
                    CONF_PLATFORM: "enocean_custom",
                    CONF_ID: valid_id,
                    CONF_DEVICE_CLASS: "not-a-real-sensor",
                },
            ),
            (
                sensor.PLATFORM_SCHEMA,
                {
                    CONF_PLATFORM: "enocean_custom",
                    CONF_ID: valid_id,
                    CONF_DEVICE_CLASS: sensor.SENSOR_TYPE_TEMPERATURE,
                    sensor.CONF_RANGE_FROM: 0,
                    sensor.CONF_RANGE_TO: 0,
                },
            ),
            (
                switch.PLATFORM_SCHEMA,
                {
                    CONF_PLATFORM: "enocean_custom",
                    CONF_ID: valid_id,
                    switch.CONF_SWITCH_TYPE: "RPS",
                    switch.CONF_CHANNEL: 2,
                },
            ),
        )
    )
    invalid_platform_configs.extend(
        (
            switch.PLATFORM_SCHEMA,
            {
                CONF_PLATFORM: "enocean_custom",
                CONF_ID: valid_id,
                switch.CONF_CHANNEL: invalid_value,
            },
        )
        for invalid_value in (True, 255.9, -0.1, float("inf"), float("nan"))
    )
    invalid_platform_configs.extend(
        (
            sensor.PLATFORM_SCHEMA,
            {
                CONF_PLATFORM: "enocean_custom",
                CONF_ID: valid_id,
                CONF_DEVICE_CLASS: sensor.SENSOR_TYPE_TEMPERATURE,
                field: invalid_value,
            },
        )
        for field in (
            sensor.CONF_MIN_TEMP,
            sensor.CONF_MAX_TEMP,
            sensor.CONF_RANGE_FROM,
            sensor.CONF_RANGE_TO,
        )
        for invalid_value in (True, 1.9, -0.1, float("inf"), float("nan"))
    )
    for schema, config in invalid_platform_configs:
        try:
            schema(config)
        except vol.Invalid:
            continue
        raise AssertionError(f"Unsafe platform config accepted: {config}")

    malformed_packets = (
        (
            binary_sensor.EnOceanBinarySensor(valid_id, "binary", None),
            SimpleNamespace(rorg=0xF6, data=[0xF6]),
        ),
        (
            light.EnOceanLight(valid_id, valid_id, "light"),
            SimpleNamespace(rorg=0xA5, data=[0xA5]),
        ),
        (
            switch.EnOceanSwitch(valid_id, "switch", 0, "default"),
            SimpleNamespace(rorg=0xA5, data=[]),
        ),
        (
            sensor.EnOceanTemperatureSensor(
                valid_id,
                "temperature",
                sensor.SENSOR_DESC_TEMPERATURE,
                scale_min=0,
                scale_max=40,
                range_from=255,
                range_to=0,
            ),
            SimpleNamespace(rorg=0xA5, data=[0xA5]),
        ),
        (
            sensor.EnOceanHumiditySensor(
                valid_id, "humidity", sensor.SENSOR_DESC_HUMIDITY
            ),
            SimpleNamespace(rorg=0xA5, data=[0xA5]),
        ),
        (
            sensor.EnOceanWindowHandle(
                valid_id, "handle", sensor.SENSOR_DESC_WINDOWHANDLE
            ),
            SimpleNamespace(rorg=0xF6, data=[0xF6]),
        ),
        (
            sensor.EnOceanShutterContact(
                valid_id, "contact", sensor.SENSOR_DESC_SHUTTERCONTACT
            ),
            SimpleNamespace(rorg=0xD5, data=[0xD5]),
        ),
    )
    for entity, packet in malformed_packets:
        entity.value_changed(packet)

    light_entity = light.EnOceanLight(valid_id, valid_id, "light")
    sent_commands: list[tuple[list[int], list[int], int]] = []
    light_responses = []

    def accept_command(
        data: list[int],
        optional: list[int],
        packet_type: int,
        response_callback=None,
    ) -> bool:
        sent_commands.append((data, optional, packet_type))
        light_responses.append(response_callback)
        return True

    light_entity.send_command = accept_command  # type: ignore[method-assign]
    light_entity.schedule_update_ha_state = lambda: None  # type: ignore[method-assign]
    light_entity.async_write_ha_state = lambda: None  # type: ignore[method-assign]
    light_entity.turn_on(brightness=255)
    if light_entity.is_on is not None or light_entity.brightness != 50:
        raise AssertionError("Queued light command changed state before ESP3 response")
    if sent_commands[0][0][2] != 100:
        raise AssertionError(f"Full HA brightness did not map to 100%: {sent_commands}")
    expected_targeted_optional = [0x03, *valid_id, 0xFF, 0x00]
    if sent_commands[0][1] != expected_targeted_optional:
        raise AssertionError(
            f"Light ESP3 optional data is invalid: {sent_commands[0][1]}"
        )
    light_responses[0](False)
    if light_entity.is_on is not None or light_entity.brightness != 50:
        raise AssertionError("Rejected ESP3 response changed light state")
    light_entity.turn_on(brightness=255)
    light_responses[1](True)
    if light_entity.is_on is not True or light_entity.brightness != 255:
        raise AssertionError("RET_OK did not apply queued light state")
    light_entity.value_changed(SimpleNamespace(rorg=0xA5, data=[0xA5, 0x02, 100]))
    if light_entity.brightness != 255:
        raise AssertionError(
            f"100% EnOcean brightness did not map to HA 255: {light_entity.brightness}"
        )

    rejected_light = light.EnOceanLight(valid_id, valid_id, "rejected")
    rejected_light.send_command = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
    rejected_light.turn_on(brightness=255)
    if rejected_light.is_on:
        raise AssertionError("Light became optimistic-on after a rejected command")

    rejected_switch = switch.EnOceanSwitch(valid_id, "rejected", 0, "default")
    rejected_switch.send_command = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
    rejected_switch.turn_on()
    if rejected_switch.is_on:
        raise AssertionError("Switch became optimistic-on after a rejected command")

    rps_switch = switch.EnOceanSwitch(valid_id, "rps", 0, "RPS")
    rps_responses = []

    def accept_rps(_data, _optional, _packet_type, response_callback=None) -> bool:
        rps_responses.append(response_callback)
        return True

    rps_switch.send_command = accept_rps  # type: ignore[method-assign]
    rps_switch.async_write_ha_state = lambda: None  # type: ignore[method-assign]
    rps_switch.turn_on()
    rps_responses[0](True)
    if rps_switch.is_on is not None:
        raise AssertionError("RPS state changed before release response")
    rps_responses[1](False)
    if rps_switch.is_on is not None:
        raise AssertionError("RPS state changed after one rejected response")
    rps_switch.turn_on()
    rps_responses[2](True)
    rps_responses[3](True)
    if rps_switch.is_on is not True:
        raise AssertionError("RPS state did not change after two RET_OK responses")

    class PowerPacket:
        def __init__(self, watts: int) -> None:
            self.data = [0xA5, 0, 0, 0, 0]
            self.watts = watts
            self.parsed = {}

        def parse_eep(self, _func: int, _type: int) -> None:
            self.parsed = {
                "DT": {"raw_value": 1},
                "MR": {"raw_value": self.watts},
                "DIV": {"raw_value": 0},
            }

    feedback_switch = switch.EnOceanSwitch(valid_id, "feedback", 0, "default")
    feedback_switch.schedule_update_ha_state = lambda: None  # type: ignore[method-assign]
    feedback_switch.value_changed(PowerPacket(2))
    if feedback_switch.is_on is not True:
        raise AssertionError("Power feedback above threshold did not turn switch on")
    feedback_switch.value_changed(PowerPacket(0))
    if feedback_switch.is_on is not False:
        raise AssertionError("Zero-watt feedback did not turn switch off")

    send_only_light = light.EnOceanLight(valid_id, [], "send-only")
    if send_only_light.unique_id == "0":
        raise AssertionError("Send-only light retained the colliding unique_id 0")
    receiver_light = light.EnOceanLight(valid_id, [1, 2, 3, 4], "receiver")
    if receiver_light.unique_id == send_only_light.unique_id:
        raise AssertionError(
            "Receive-capable light did not preserve its receiver-based identity"
        )
    if send_only_light.is_on is not None or rejected_switch.is_on is not None:
        raise AssertionError(
            "Actuator state was not unknown before feedback or command"
        )

    broadcast_packets: list[tuple[list[int], list[int], int]] = []
    send_only_light.send_command = (  # type: ignore[method-assign]
        lambda data, optional, packet_type, response_callback=None: (
            broadcast_packets.append((data, optional, packet_type)) or True
        )
    )
    send_only_light.turn_on(brightness=1)
    expected_broadcast_optional = [0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00]
    if broadcast_packets[0][1] != expected_broadcast_optional:
        raise AssertionError(
            f"Broadcast ESP3 optional data is invalid: {broadcast_packets[0][1]}"
        )

    climate_packets: list[tuple[list[int], list[int], int]] = []
    climate_entity = object.__new__(climate.EnOceanClimate)
    climate_entity.dev_name = "climate-envelope"
    climate_entity._sender_id_switch = valid_id
    climate_entity._sender_id = valid_id
    climate_entity.send_command = (  # type: ignore[method-assign]
        lambda data, optional, packet_type, response_callback=None: (
            climate_packets.append((data, optional, packet_type)) or True
        )
    )
    climate_entity.sendPacket([0x00])
    climate_entity.sendPacket([0x00, 0x01, 0x02, 0x03])
    if any(packet[1] != expected_broadcast_optional for packet in climate_packets):
        raise AssertionError(
            f"Climate ESP3 optional data is invalid: {climate_packets}"
        )

    response_probe = object.__new__(dongle.EnOceanDongle)
    response_probe._response_packets = 0
    response_probe._response_errors = 0
    response_probe._last_response_at = None
    response_probe._last_response_code = None
    response_probe._last_error = None
    response_probe._response_lock = Lock()
    response_results = []
    internal_packet = object()
    entity_packet = object()
    response_probe._queued_response_callbacks = {
        id(entity_packet): response_results.append
    }
    response_probe._pending_response_callbacks = deque()
    response_probe.hass = SimpleNamespace(
        loop=SimpleNamespace(
            call_soon_threadsafe=lambda callback, *args: callback(*args)
        )
    )
    response_probe._packet_written(internal_packet)
    response_probe._packet_written(entity_packet)
    ok_response = object.__new__(ResponsePacket)
    ok_response.response = RETURN_CODE.OK
    response_probe.callback(ok_response)
    if response_results:
        raise AssertionError("Internal ESP3 response stole an entity callback")
    response_packet = object.__new__(ResponsePacket)
    response_packet.response = RETURN_CODE.WRONG_PARAM
    response_probe.callback(response_packet)
    if (
        response_probe._response_packets != 2
        or response_probe._response_errors != 1
        or response_probe._last_response_code != "WRONG_PARAM"
        or response_results != [False]
    ):
        raise AssertionError("ESP3 rejection was not correlated and recorded")

    private_path = "/dev/serial/by-id/private-test-path"
    diagnostics_entry = SimpleNamespace(
        data={"device": private_path},
        runtime_data=SimpleNamespace(diagnostics=lambda: {"health": "ok"}),
    )
    diagnostic = asyncio.run(
        async_get_config_entry_diagnostics(None, diagnostics_entry)  # type: ignore[arg-type]
    )
    if private_path in repr(diagnostic):
        raise AssertionError("Diagnostics leaked the configured serial path")
    if diagnostic["dongle"] != {"health": "ok"}:
        raise AssertionError(f"Dongle diagnostics were lost: {diagnostic}")

    _assert_ute_does_not_corrupt_following_frame()
    _assert_corrupt_header_resyncs_following_frame()
    _assert_incomplete_frames_preserve_following_frame()
    _assert_generic_frame_timeout_resyncs_following_frame()
    _assert_migration_logs_redact_hardware_identifiers()
    _assert_transmit_queue_is_bounded()
    _assert_base_id_retry_paths()
    asyncio.run(_assert_off_without_sensor_sends_switch_off())
    asyncio.run(_assert_climate_response_transactions())
    asyncio.run(_assert_real_climate_send_chain())
    asyncio.run(_assert_legacy_send_only_light_identity_is_preserved())
    print(
        "HA_2026_7_3_SMOKE_OK "
        f"invalid_cases_rejected={len(invalid_cases)} "
        f"platform_invalid_cases_rejected={len(invalid_platform_configs)} "
        f"malformed_packets_dropped={len(malformed_packets)} "
        "brightness_mapping=exact rejected_commands=no_state_change "
        "esp3_optional=valid esp3_rejections=correlated actuator_initial=unknown "
        "ute_resync=valid diagnostics=redacted off_without_sensor=sent "
        "climate_responses=transactional climate_send_chain=real base_id_retry=valid"
    )


if __name__ == "__main__":
    main()

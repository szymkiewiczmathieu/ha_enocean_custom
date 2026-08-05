"""Smoke-test the integration against the pinned Home Assistant runtime."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import deque
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread, get_ident
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import Mock

import voluptuous as vol
import yaml
from homeassistant.components.climate.const import HVACMode
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_ID,
    CONF_NAME,
    CONF_PLATFORM,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.enocean_custom import (
    binary_sensor,
    climate,
    dongle,
    learn,
    light,
    options_flow,
    sensor,
    switch,
)
from custom_components.enocean_custom.const import (
    DATA_ENOCEAN,
    DOMAIN,
    ENOCEAN_DONGLE,
    EVENT_DEVICE_LEARNED,
    SERVICE_LEARN,
    SIGNAL_DONGLE_STATUS,
    SIGNAL_RECEIVE_MESSAGE,
)
from custom_components.enocean_custom.device import EnOceanEntity
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
    RORG,
)
from custom_components.enocean_custom.enocean_library.protocol.packet import (
    Packet,
    ResponsePacket,
)
from custom_components.enocean_custom.schema import CONF_UI_DEVICES

STRINGS = json.loads(
    (ROOT / "custom_components/enocean_custom/strings.json").read_text(encoding="utf-8")
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


async def _assert_dongle_status_signal_writes_state_from_worker_thread() -> None:
    """Run the dongle status target on the event loop, not the executor.

    Regression: a plain sync dispatcher target runs in the executor, where
    ``async_write_ha_state`` raises and the entity stays stuck in its previous
    availability forever. The status target must run on the event loop.
    """
    import threading

    from homeassistant.helpers.dispatcher import (
        async_dispatcher_connect,
        dispatcher_send,
    )

    if not getattr(EnOceanEntity._dongle_status_changed, "_hass_callback", False):
        raise AssertionError("dongle status target is not marked as a loop callback")
    if not getattr(EnOceanEntity._message_received_callback, "_hass_callback", False):
        raise AssertionError("packet target is not marked as a loop callback")

    with TemporaryDirectory() as config_dir:
        hass = HomeAssistant(config_dir)
        try:
            loop_thread = threading.get_ident()
            calls: list[tuple[bool, int]] = []
            entity = EnOceanEntity([1, 2, 3, 4], "status probe")
            entity.hass = hass
            entity.async_write_ha_state = (  # type: ignore[method-assign]
                lambda: calls.append((entity.available, threading.get_ident()))
            )
            async_dispatcher_connect(
                hass, SIGNAL_DONGLE_STATUS, entity._dongle_status_changed
            )

            fired = Event()

            def flip() -> None:
                dispatcher_send(hass, SIGNAL_DONGLE_STATUS, False)
                fired.set()

            worker = Thread(target=flip)
            worker.start()
            async with asyncio.timeout(5):
                while not fired.is_set():
                    await asyncio.sleep(0.01)
            await hass.async_block_till_done()
            worker.join(timeout=5)
            if not calls:
                raise AssertionError(
                    "status signal from a worker thread was never delivered"
                )
            if any(thread_id != loop_thread for _, thread_id in calls):
                raise AssertionError("status target ran off the event loop thread")
            if calls[-1][0] is not False:
                raise AssertionError("availability was not mirrored to False")
        finally:
            await hass.async_stop(force=True)


async def _assert_d2_status_parse_dispatch_and_entities() -> None:
    """Exercise D2 status via real ESP3 parse, dispatcher, switch, and light."""
    with TemporaryDirectory() as config_dir:
        hass = HomeAssistant(config_dir)
        try:
            sender = [0x01, 0x02, 0x03, 0x04]
            writes: list[int] = []
            entities = [
                switch.EnOceanSwitch(sender, "D2 channel 0", 0, "default"),
                switch.EnOceanSwitch(sender, "D2 channel 1", 1, "default"),
                switch.EnOceanSwitch(sender, "RPS regression", 0, "RPS"),
                light.EnOceanLight([0x05, 0x06, 0x07, 0x08], sender, "D2 light"),
            ]
            for entity in entities:
                entity.hass = hass
                entity.async_write_ha_state = (  # type: ignore[method-assign]
                    lambda: writes.append(get_ident())
                )
                await entity.async_added_to_hass()

            def parsed_status(packet_sender: list[int], channel: int, output: int):
                wire = Packet(
                    PACKET.RADIO_ERP1,
                    data=[
                        RORG.VLD,
                        0xC4,
                        channel,
                        output,
                        *packet_sender,
                        0x00,
                    ],
                    optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
                ).build()
                status, remaining, packet = Packet.parse_msg(bytearray(wire))
                if (
                    status != PARSE_RESULT.OK
                    or remaining
                    or packet is None
                    or len(packet.data) != 9
                ):
                    raise AssertionError("D2 VLD variable-length frame was not parsed")
                return packet

            async_dispatcher_send(
                hass, SIGNAL_RECEIVE_MESSAGE, parsed_status(sender, 0, 50)
            )
            await hass.async_block_till_done()
            channel_0, channel_1, rps, d2_light = entities
            if (
                channel_0.is_on is not True
                or channel_1.is_on is not None
                # RPS entities now consume D2 status on their own channel.
                or rps.is_on is not True
                or d2_light.is_on is not True
                or d2_light.brightness != 128
            ):
                raise AssertionError("D2 channel 0 feedback did not update entities")
            if channel_0.extra_state_attributes.get("d2_output_value") != 50:
                raise AssertionError("D2 state attributes did not expose output value")

            async_dispatcher_send(
                hass, SIGNAL_RECEIVE_MESSAGE, parsed_status(sender, 1, 0)
            )
            async_dispatcher_send(
                hass,
                SIGNAL_RECEIVE_MESSAGE,
                parsed_status([0x10, 0x20, 0x30, 0x40], 0, 100),
            )
            malformed_wire = Packet(
                PACKET.RADIO_ERP1,
                data=[RORG.VLD, 0x04, 0x00, *sender, 0x00],
                optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
            ).build()
            status, _, malformed = Packet.parse_msg(bytearray(malformed_wire))
            if status != PARSE_RESULT.OK or malformed is None:
                raise AssertionError(
                    "Malformed D2 radio envelope could not be exercised"
                )
            async_dispatcher_send(hass, SIGNAL_RECEIVE_MESSAGE, malformed)
            await hass.async_block_till_done()

            if (
                channel_0.is_on is not True
                or channel_1.is_on is not False
                # The RPS entity ignores the channel-1 packet (no leak).
                or rps.is_on is not True
                # Regression (review finding): the channel-1 packet must NOT
                # touch the channel-0 light — it keeps the channel-0 state.
                or d2_light.is_on is not True
                or d2_light.brightness != 128
            ):
                raise AssertionError(
                    "D2 channel 1, unknown sender, malformed, or RPS isolation failed"
                )
            if not writes or any(thread_id != get_ident() for thread_id in writes):
                raise AssertionError("D2 state feedback wrote outside HA's event loop")
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


def _assert_unsolicited_ute_is_never_acknowledged() -> None:
    """The runtime worker must never answer a UTE it did not solicit."""
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

    received: list[Any] = []
    communicator = Communicator(callback=received.append)
    communicator._buffer = list(ute_frame)
    communicator.parse()
    if len(received) != 1 or communicator.transmit.qsize():
        raise AssertionError("an unsolicited UTE teach-in was acknowledged")
    if communicator._pending_ute_packets or communicator._base_id_requested:
        raise AssertionError(
            "an unsolicited UTE teach-in was queued for a later answer"
        )

    # A UTE arriving after the Base ID is already known must be just as silent.
    known_base_id = Communicator(callback=[].append)
    known_base_id.base_id = [0xFF, 0x87, 0xCA, 0x00]
    known_base_id._buffer = list(ute_frame)
    known_base_id.parse()
    if known_base_id.transmit.qsize():
        raise AssertionError("a known Base ID re-enabled automatic UTE acknowledgement")

    # The worker the integration actually starts carries the same policy.
    original_serial = serialcommunicator.serial.Serial
    serialcommunicator.serial.Serial = lambda *args, **kwargs: Mock()
    try:
        runtime_dongle = dongle.EnOceanDongle(None, "/dev/test-ute-policy")
    finally:
        serialcommunicator.serial.Serial = original_serial
    if runtime_dongle._communicator.teach_in:
        raise AssertionError("the runtime dongle enabled automatic UTE acknowledgement")


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
    # Deferred UTE handling only ever runs inside an explicitly opened
    # teach-in session, so the session flag is what this path is exercised on.
    ute_communicator = Communicator(teach_in=True)
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


def _new_test_config_entry() -> ConfigEntry:
    """Build a minimal real ConfigEntry for teach-in harness tests."""
    return ConfigEntry(
        data={"device": "/dev/test-teachin"},
        discovery_keys=MappingProxyType({}),
        domain=DOMAIN,
        minor_version=1,
        options={},
        source="user",
        subentries_data=None,
        title="EnOcean",
        unique_id=None,
        version=1,
    )


def _bind_options_flow(hass: HomeAssistant, entry: ConfigEntry) -> Any:
    """Bind a real OptionsFlow instance to hass/entry without the flow manager."""
    flow = options_flow.EnOceanOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id
    return flow


async def _assert_ui_devices_create_entities_with_yaml_compatible_unique_ids() -> None:
    """options["ui_devices"] must create entities cohabiting with YAML ones."""
    with TemporaryDirectory() as config_dir:
        hass = HomeAssistant(config_dir)
        try:
            await ar.async_load(hass, load_empty=True)
            dr.async_setup(hass)
            await dr.async_load(hass, load_empty=True)
            await er.async_load(hass, load_empty=True)
            ui_devices = [
                {
                    "id": [0x10, 0x20, 0x30, 0x40],
                    "platform": "binary_sensor",
                    "name": "UI door",
                    "device_class": "door",
                    "channel": 0,
                    "sender_id": None,
                },
                {
                    "id": [0x10, 0x20, 0x30, 0x40],
                    "platform": "switch",
                    "name": "UI relay",
                    "device_class": None,
                    "channel": 3,
                    "sender_id": None,
                },
                {
                    "id": [0x50, 0x60, 0x70, 0x80],
                    "platform": "light",
                    "name": "UI lamp",
                    "device_class": None,
                    "channel": 0,
                    "sender_id": [0x01, 0x02, 0x03, 0x04],
                },
                {
                    "id": [0x60, 0x61, 0x62, 0x63],
                    "platform": "sensor",
                    "name": "UI temperature",
                    "device_class": "temperature",
                    "min_temp": -20,
                    "max_temp": 60,
                    "range_from": 255,
                    "range_to": 0,
                },
                {
                    "id": [0x70, 0x71, 0x72, 0x73],
                    "platform": "climate",
                    "name": "UI heating",
                    "id_switch": [0x74, 0x75, 0x76, 0x77],
                    "device_type": "SRC-D08",
                    "sensor_entity_id": "sensor.room_temperature",
                },
            ]
            entry_stub = SimpleNamespace(options={CONF_UI_DEVICES: ui_devices})

            binary_sensor_entities: list[Any] = []
            await binary_sensor.async_setup_entry(
                hass, entry_stub, binary_sensor_entities.extend
            )
            switch_entities: list[Any] = []
            await switch.async_setup_entry(hass, entry_stub, switch_entities.extend)
            light_entities: list[Any] = []
            await light.async_setup_entry(hass, entry_stub, light_entities.extend)
            sensor_entities: list[Any] = []
            await sensor.async_setup_entry(hass, entry_stub, sensor_entities.extend)
            climate_entities: list[Any] = []
            await climate.async_setup_entry(hass, entry_stub, climate_entities.extend)

            door_id = (0x10 << 24) | (0x20 << 16) | (0x30 << 8) | 0x40
            lamp_id = (0x50 << 24) | (0x60 << 16) | (0x70 << 8) | 0x80
            if binary_sensor_entities[0].unique_id != f"{door_id}-door":
                raise AssertionError(
                    "UI binary_sensor unique_id diverged from YAML formula"
                )
            if switch_entities[0].unique_id != f"{door_id}-3":
                raise AssertionError("UI switch unique_id diverged from YAML formula")
            if light_entities[0].unique_id != f"{lamp_id}":
                raise AssertionError("UI light unique_id diverged from YAML formula")
            sensor_id = (0x60 << 24) | (0x61 << 16) | (0x62 << 8) | 0x63
            climate_id = (0x70 << 24) | (0x71 << 16) | (0x72 << 8) | 0x73
            if sensor_entities[0].unique_id != f"{sensor_id}-temperature":
                raise AssertionError("UI sensor unique_id diverged from YAML formula")
            if climate_entities[0].unique_id != f"{climate_id}-0":
                raise AssertionError("UI climate unique_id diverged from YAML formula")

            if learn.get_known_ids(hass):
                raise AssertionError("UI setup polluted the YAML-managed ID registry")

            # A YAML-configured binary_sensor on the very same platform must
            # keep working (cohabitation, not replacement).
            yaml_entities: list[Any] = []
            binary_sensor.setup_platform(
                hass,
                {
                    CONF_ID: [0x90, 0xA0, 0xB0, 0xC0],
                    CONF_NAME: "YAML window",
                    CONF_DEVICE_CLASS: "window",
                },
                yaml_entities.extend,
            )
            if len(yaml_entities) != 1 or len(binary_sensor_entities) != 1:
                raise AssertionError(
                    "YAML and UI binary_sensor entities did not cohabit"
                )
            yaml_id = (0x90 << 24) | (0xA0 << 16) | (0xB0 << 8) | 0xC0
            if yaml_id not in learn.get_known_ids(hass):
                raise AssertionError("YAML setup_platform did not register a known id")
        finally:
            await hass.async_stop(force=True)


async def _assert_options_flow_add_and_delete_device() -> None:
    """Drive the real options flow: learn, collision refusal, and deletion."""
    with TemporaryDirectory() as config_dir:
        hass = HomeAssistant(config_dir)
        try:
            await ar.async_load(hass, load_empty=True)
            dr.async_setup(hass)
            await dr.async_load(hass, load_empty=True)
            await er.async_load(hass, load_empty=True)
            registry = er.async_get(hass)

            entry = _new_test_config_entry()
            # A bare temp config dir has no custom_components tree for the
            # loader to discover, so ConfigEntries.async_add (which calls
            # async_setup) is not usable here. The options flow only needs
            # the entry to be resolvable via async_get_known_entry.
            hass.config_entries = ConfigEntries(hass, {})
            hass.config_entries._entries[entry.entry_id] = entry

            # -- learn step times out without a capture -> the flow aborts.
            timeout_flow = _bind_options_flow(hass, entry)
            first = await timeout_flow.async_step_learn(None)
            if first["type"] != FlowResultType.SHOW_PROGRESS:
                raise AssertionError("learn step did not show progress on first entry")
            learn.get_learn_manager(hass).stop()
            first["progress_task"].cancel()
            timed_out = await timeout_flow.async_step_learn(None)
            if (
                timed_out["type"] != FlowResultType.SHOW_PROGRESS_DONE
                or timed_out["step_id"] != "learn_timeout"
            ):
                raise AssertionError(
                    f"learn step did not report a timeout: {timed_out}"
                )
            timeout_result = await timeout_flow.async_step_learn_timeout(None)
            if (
                timeout_result["type"] != FlowResultType.ABORT
                or timeout_result["reason"] != "learn_timeout"
            ):
                raise AssertionError("learn timeout did not abort the flow")

            # -- learn step captures an unknown sender -> device_form -> device_details.
            add_flow = _bind_options_flow(hass, entry)
            captured_id = [0x11, 0x22, 0x33, 0x44]
            await add_flow.async_step_learn(None)
            manager = learn.get_learn_manager(hass)
            # A real dispatcher packet, exactly as the serial thread would send.
            async_dispatcher_send(
                hass,
                SIGNAL_RECEIVE_MESSAGE,
                SimpleNamespace(sender_int=0x11223344),
            )
            await hass.async_block_till_done()
            if manager.captured != captured_id:
                raise AssertionError(
                    "dispatcher packet was not captured by the learn window"
                )
            done = await add_flow.async_step_learn(None)
            if (
                done["type"] != FlowResultType.SHOW_PROGRESS_DONE
                or done["step_id"] != "device_form"
            ):
                raise AssertionError(
                    f"captured sender did not advance to device_form: {done}"
                )

            form = await add_flow.async_step_device_form(None)
            if form["type"] != FlowResultType.FORM or form["step_id"] != "device_form":
                raise AssertionError("device_form did not render a form")

            # -- QR commissioning rejects malformed input and pre-fills the
            # existing device form only after a valid 32-bit EURID is parsed.
            qr_flow = _bind_options_flow(hass, entry)
            invalid_qr = await qr_flow.async_step_qr_code({"qr_code": "not-a-code"})
            if invalid_qr["type"] != FlowResultType.FORM or invalid_qr.get(
                "errors"
            ) != {"qr_code": "invalid_qr_code"}:
                raise AssertionError(f"invalid QR was accepted: {invalid_qr}")
            valid_qr = await qr_flow.async_step_qr_code(
                {"qr_code": "30S0000A1B2C3D4+1P001B0000789A"}
            )
            if (
                valid_qr["type"] != FlowResultType.FORM
                or valid_qr["step_id"] != "device_form"
                or qr_flow._captured_id != [0xA1, 0xB2, 0xC3, 0xD4]
            ):
                raise AssertionError(
                    f"valid QR did not pre-fill device form: {valid_qr}"
                )

            details_step = await add_flow.async_step_device_form(
                {"platform": "binary_sensor", "name": "UI teach-in door"}
            )
            if (
                details_step["type"] != FlowResultType.FORM
                or details_step["step_id"] != "device_details"
            ):
                raise AssertionError("device_form did not advance to device_details")

            created = await add_flow.async_step_device_details({"device_class": "door"})
            if created["type"] != FlowResultType.CREATE_ENTRY:
                raise AssertionError(
                    f"device_details did not create an entry: {created}"
                )
            hass.config_entries.async_update_entry(entry, options=created["data"])

            devices = entry.options[CONF_UI_DEVICES]
            if len(devices) != 1 or devices[0]["id"] != captured_id:
                raise AssertionError("added UI device was not persisted to options")
            expected_unique_id = options_flow._unique_id_for(devices[0])

            # -- Simulate the reload that would create the real entity, then
            # confirm a second add attempt for the same identity is refused
            # without merging or overwriting anything.
            registry.async_get_or_create("binary_sensor", DOMAIN, expected_unique_id)
            collide_flow = _bind_options_flow(hass, entry)
            collide_flow._captured_id = captured_id
            collide_flow._pending_platform = "binary_sensor"
            collide_flow._pending_name = "duplicate"
            collided = await collide_flow.async_step_device_details(
                {"device_class": "door"}
            )
            if collided["type"] != FlowResultType.FORM or collided.get("errors") != {
                "base": "unique_id_exists"
            }:
                raise AssertionError(f"colliding device was not refused: {collided}")
            if len(entry.options[CONF_UI_DEVICES]) != 1:
                raise AssertionError("a colliding submission mutated persisted options")

            # -- A registry row belonging to a different platform must never
            # be touched by deleting our UI device.
            foreign_entity = registry.async_get_or_create(
                "sensor", "other_integration", "foreign-unique-id"
            )

            manage_flow = _bind_options_flow(hass, entry)
            manage = await manage_flow.async_step_manage(None)
            if manage["type"] != FlowResultType.FORM or manage["step_id"] != "manage":
                raise AssertionError("manage step did not list the UI device")
            confirm = await manage_flow.async_step_manage({"device": "0"})
            if (
                confirm["type"] != FlowResultType.FORM
                or confirm["step_id"] != "delete_confirm"
            ):
                raise AssertionError(
                    "manage selection did not advance to delete_confirm"
                )

            learn.register_known_id(hass, captured_id)
            refused_delete = await manage_flow.async_step_delete_confirm(
                {"confirm": True}
            )
            if (
                refused_delete["type"] != FlowResultType.ABORT
                or refused_delete["reason"] != "yaml_managed"
                or len(entry.options[CONF_UI_DEVICES]) != 1
                or registry.async_get_entity_id(
                    "binary_sensor", DOMAIN, expected_unique_id
                )
                is None
            ):
                raise AssertionError(
                    f"YAML-managed UI deletion was not refused: {refused_delete}"
                )
            learn.get_known_ids(hass).remove(
                (0x11 << 24) | (0x22 << 16) | (0x33 << 8) | 0x44
            )
            deleted = await manage_flow.async_step_delete_confirm({"confirm": True})
            if deleted["type"] != FlowResultType.CREATE_ENTRY:
                raise AssertionError(
                    f"delete_confirm did not persist removal: {deleted}"
                )
            hass.config_entries.async_update_entry(entry, options=deleted["data"])

            if entry.options[CONF_UI_DEVICES]:
                raise AssertionError("deleted UI device is still present in options")
            if (
                registry.async_get_entity_id(
                    "binary_sensor", DOMAIN, expected_unique_id
                )
                is not None
            ):
                raise AssertionError(
                    "deleted UI device's entity registry row was not removed"
                )
            if (
                registry.async_get_entity_id(
                    "sensor", "other_integration", "foreign-unique-id"
                )
                != foreign_entity.entity_id
            ):
                raise AssertionError(
                    "deletion touched a registry row from a different platform"
                )

            # -- Switch details: RPS requires channel 0/1, valid RPS persists.
            rps_flow = _bind_options_flow(hass, entry)
            rps_flow._captured_id = [0x21, 0x22, 0x23, 0x24]
            rps_flow._pending_platform = "switch"
            rps_flow._pending_name = "UI RPS switch"
            bad = await rps_flow.async_step_device_details(
                {"channel": 2, "switch_type": "RPS"}
            )
            if bad["type"] != FlowResultType.FORM or bad.get("errors") != {
                "channel": "invalid_channel_rps"
            }:
                raise AssertionError(f"invalid RPS channel was accepted: {bad}")
            good = await rps_flow.async_step_device_details(
                {"channel": 1, "switch_type": "RPS"}
            )
            if good["type"] != FlowResultType.CREATE_ENTRY:
                raise AssertionError(f"valid RPS switch was refused: {good}")
            rps_device = good["data"][CONF_UI_DEVICES][-1]
            if rps_device["switch_type"] != "RPS" or rps_device["channel"] != 1:
                raise AssertionError("RPS switch_type was not persisted")

            # -- Light details: sender_id text must parse as 4 hex bytes.
            light_flow = _bind_options_flow(hass, entry)
            light_flow._captured_id = [0x31, 0x32, 0x33, 0x34]
            light_flow._pending_platform = "light"
            light_flow._pending_name = "UI light"
            bad_sender = await light_flow.async_step_device_details(
                {"sender_id": "not-an-id"}
            )
            if bad_sender["type"] != FlowResultType.FORM or bad_sender.get(
                "errors"
            ) != {"sender_id": "invalid_sender_id"}:
                raise AssertionError(f"invalid sender_id was accepted: {bad_sender}")
            good_sender = await light_flow.async_step_device_details(
                {"sender_id": "05:9F:89:34"}
            )
            if good_sender["type"] != FlowResultType.CREATE_ENTRY:
                raise AssertionError(f"valid sender_id was refused: {good_sender}")
            light_device = good_sender["data"][CONF_UI_DEVICES][-1]
            if light_device["sender_id"] != [0x05, 0x9F, 0x89, 0x34]:
                raise AssertionError("sender_id text was not parsed to 4 bytes")

            # -- Sensor details preserve the YAML field names/defaults.
            sensor_flow = _bind_options_flow(hass, entry)
            sensor_flow._captured_id = [0x41, 0x42, 0x43, 0x44]
            sensor_flow._pending_platform = "sensor"
            sensor_flow._pending_name = "UI temperature"
            sensor_input = sensor_flow._device_details_schema("sensor")(
                {"device_class": "temperature"}
            )
            good_sensor = await sensor_flow.async_step_device_details(sensor_input)
            sensor_device = good_sensor["data"][CONF_UI_DEVICES][-1]
            if (
                good_sensor["type"] != FlowResultType.CREATE_ENTRY
                or sensor_device["device_class"] != "temperature"
                or sensor_device["min_temp"] != 0
                or sensor_device["max_temp"] != 40
                or sensor_device["range_from"] != 255
                or sensor_device["range_to"] != 0
            ):
                raise AssertionError(
                    f"sensor YAML-compatible defaults were not persisted: {good_sensor}"
                )

            # -- Climate details preserve all YAML fields and parse id_switch.
            climate_flow = _bind_options_flow(hass, entry)
            climate_flow._captured_id = [0x51, 0x52, 0x53, 0x54]
            climate_flow._pending_platform = "climate"
            climate_flow._pending_name = "UI heating"
            climate_input = climate_flow._device_details_schema("climate")(
                {
                    "id_switch": "55:56:57:58",
                    "device_type": "SRC-D08",
                    "sensor_entity_id": "sensor.room_temperature",
                }
            )
            good_climate = await climate_flow.async_step_device_details(climate_input)
            climate_device = good_climate["data"][CONF_UI_DEVICES][-1]
            if (
                good_climate["type"] != FlowResultType.CREATE_ENTRY
                or climate_device["id_switch"] != [0x55, 0x56, 0x57, 0x58]
                or climate_device["command_frequency"] != "00:17:00"
                or climate_device["pi_control_Kp"] != 5.0
                or climate_device["pi_control_Tn"] != 240.0
            ):
                raise AssertionError(
                    f"climate YAML-compatible defaults were not persisted: {good_climate}"
                )

            # -- The deleted device's id is teachable again immediately (K1).
            manager = learn.get_learn_manager(hass)
            if not manager.start(60, owner="service"):
                raise AssertionError("learn window did not reopen after deletion")
            async_dispatcher_send(
                hass,
                SIGNAL_RECEIVE_MESSAGE,
                SimpleNamespace(sender_int=0x11223344),
            )
            await hass.async_block_till_done()
            if manager.captured != captured_id:
                raise AssertionError("deleted UI device id was not teachable again")
        finally:
            await hass.async_stop(force=True)


async def _assert_learn_service_starts_window_and_registers_once() -> None:
    """The learn service must start a window and survive reload without a duplicate."""
    with TemporaryDirectory() as config_dir:
        hass = HomeAssistant(config_dir)
        try:
            learn.async_register_learn_service(hass)
            learn.async_register_learn_service(hass)  # simulate a second reload
            if not hass.services.has_service(DOMAIN, SERVICE_LEARN):
                raise AssertionError("learn service was not registered")

            events: list[dict[str, Any]] = []
            hass.bus.async_listen(
                EVENT_DEVICE_LEARNED, lambda event: events.append(event.data)
            )
            await hass.services.async_call(
                DOMAIN, SERVICE_LEARN, {"timeout": 20}, blocking=True
            )
            manager = learn.get_learn_manager(hass)
            if not manager.is_active:
                raise AssertionError("learn service did not open a learn window")

            # A second window while one is active is refused loudly.
            refused = False
            try:
                await hass.services.async_call(
                    DOMAIN, SERVICE_LEARN, {"timeout": 20}, blocking=True
                )
            except HomeAssistantError:
                refused = True
            if not refused:
                raise AssertionError("concurrent learn service call was not refused")

            # An ordinary RPS telegram declares no profile: the event must say
            # so instead of guessing, and must not carry the raw packet.
            packet = SimpleNamespace(sender_int=0xAABBCCDD, rorg=RORG.RPS)
            async_dispatcher_send(hass, SIGNAL_RECEIVE_MESSAGE, packet)
            await hass.async_block_till_done()

            if events != [
                {
                    "id": [0xAA, 0xBB, 0xCC, 0xDD],
                    "hex": "AA:BB:CC:DD",
                    "evidence": "profile_unknown",
                    "support": "unknown",
                }
            ]:
                raise AssertionError(f"unexpected teach-in event payload: {events}")
            if manager.captured_metadata["sender_id"] != [0xAA, 0xBB, 0xCC, 0xDD]:
                raise AssertionError("captured metadata lost its sender identity")
        finally:
            await hass.async_stop(force=True)


async def _assert_device_intelligence_flow_and_device_registry() -> None:
    """QR identification fills translated placeholders and groups the registry."""
    with TemporaryDirectory() as config_dir:
        hass = HomeAssistant(config_dir)
        try:
            await ar.async_load(hass, load_empty=True)
            dr.async_setup(hass)
            await dr.async_load(hass, load_empty=True)
            await er.async_load(hass, load_empty=True)

            entry = _new_test_config_entry()
            hass.config_entries = ConfigEntries(hass, {})
            hass.config_entries._entries[entry.entry_id] = entry

            # -- A cataloged Alliance label identifies the product; the raw
            # payload and its security containers never survive parsing.
            flow = _bind_options_flow(hass, entry)
            # The gateway variant is the Cositherm whose DDF declares a
            # single transmitted D2-34-10; 002D00000004 transmits GP
            # B0-00-00 and only receives A5-10 profiles.
            label = "30S000010203040+1P002D0000000A+10ZSECRETPAYLOAD"
            result = await flow.async_step_qr_code({"qr_code": label})
            if result["step_id"] != "device_form":
                raise AssertionError(f"cataloged label was refused: {result}")
            placeholders = result["description_placeholders"]
            declared = set(
                re.findall(
                    r"\{([a-z_]+)\}",
                    STRINGS["options"]["step"]["device_form"]["description"],
                )
            )
            if declared - set(placeholders):
                raise AssertionError(
                    f"device_form placeholders are missing: {declared - set(placeholders)}"
                )
            if (
                placeholders["radio_manufacturer"] != "Afriso"
                or placeholders["radio_model"] != "Cositherm 2-Channel"
                or placeholders["radio_eep"] != "D2-34-10"
                or placeholders["radio_support"] != "unsupported"
            ):
                raise AssertionError(f"unexpected radio card: {placeholders}")
            if "SECRETPAYLOAD" in repr(flow._radio_metadata) or "10Z" in repr(
                flow._radio_metadata
            ):
                raise AssertionError("QR security container survived parsing")
            # D2-34-10 has no decoder here, so no platform may be pre-selected.
            for key in result["data_schema"].schema:
                if key == CONF_PLATFORM and key.default is not vol.UNDEFINED:
                    raise AssertionError("an unsupported profile was pre-selected")
            # The Product ID already declares the profile: no manual EEP field
            # is offered, and forging one into the submission changes nothing.
            if "manual_eep" in [str(key) for key in result["data_schema"].schema]:
                raise AssertionError("a declared profile was offered for manual entry")

            details = await flow.async_step_device_form(
                {
                    "platform": "switch",
                    "name": "Cositherm relay",
                    "manual_eep": "F6-02-01",
                }
            )
            created = await flow.async_step_device_details({"channel": 0})
            if created["type"] != FlowResultType.CREATE_ENTRY:
                raise AssertionError(f"device_details did not create: {details}")
            row = created["data"][CONF_UI_DEVICES][0]
            metadata = row["radio_metadata"]
            if metadata["product_id"] != "002D0000000A":
                raise AssertionError("the Product ID was not persisted")
            if (
                metadata["eep"] != "D2-34-10"
                or metadata["eep_source"] != "product_declared"
                or metadata["evidence"] != "assisted"
            ):
                raise AssertionError(f"a forged manual EEP overwrote proof: {metadata}")
            for forgeable in ("manufacturer", "model", "model_id"):
                if forgeable in metadata:
                    raise AssertionError(
                        f"forgeable {forgeable} string was persisted to options"
                    )
            hass.config_entries.async_update_entry(entry, options=created["data"])

            # -- Two entities of one sender share a single registry device, and
            # the model comes from the catalog rather than from stored text.
            device_registry = dr.async_get(hass)
            entities = [
                EnOceanEntity(row["id"], row["name"]).set_radio_metadata(metadata),
                EnOceanEntity(row["id"], "Second channel").set_radio_metadata(metadata),
            ]
            identifiers = {
                tuple(sorted(entity.device_info["identifiers"])) for entity in entities
            }
            if len(identifiers) != 1:
                raise AssertionError("entities of one sender were not grouped")
            device = device_registry.async_get_or_create(
                config_entry_id=entry.entry_id, **entities[0].device_info
            )
            if (
                device.manufacturer != "Afriso"
                or device.model != "Cositherm 2-Channel"
                or device.model_id != "002D0000000A"
            ):
                raise AssertionError(f"registry product identity is wrong: {device}")

            # -- A YAML entity has no metadata: grouped, but never mislabeled.
            yaml_entity = EnOceanEntity([0x01, 0x02, 0x03, 0x04], "YAML rocker")
            yaml_info = yaml_entity.device_info
            if yaml_info["identifiers"] != {(DOMAIN, "01020304")}:
                raise AssertionError("YAML entity was not grouped by sender")
            if any(key in yaml_info for key in ("manufacturer", "model", "model_id")):
                raise AssertionError("YAML entity was given an invented model")
        finally:
            await hass.async_stop(force=True)


async def _assert_learn_windows_are_owned_and_serialized() -> None:
    """Concurrent windows are refused and foreign owners cannot close a window."""
    with TemporaryDirectory() as config_dir:
        hass = HomeAssistant(config_dir)
        try:
            manager = learn.get_learn_manager(hass)
            if not manager.start(60, owner="service"):
                raise AssertionError("first learn window did not start")
            if manager.start(60, owner="options_flow"):
                raise AssertionError("a concurrent learn window was allowed")
            manager.stop(owner="options_flow")
            if not manager.is_active:
                raise AssertionError("a foreign owner closed the service window")
            manager.stop(owner="service")
            if manager.is_active:
                raise AssertionError("owner stop did not close the window")
            manager.stop()  # unconditional close (unload path)
        finally:
            await hass.async_stop(force=True)


def _assert_options_schemas_are_ws_serializable() -> None:
    """The P0 regression: every options schema must survive the WS handler."""
    import homeassistant.helpers.config_validation as cv
    import voluptuous_serialize

    flow = options_flow.EnOceanOptionsFlow()
    schemas = [
        options_flow._qr_code_schema(),
        options_flow._pair_actuator_type_schema(),
        options_flow._pairing_failure_schema(),
        vol.Schema({}),  # instructions and both terminal success forms
    ]
    schemas.extend(
        flow._device_details_schema(platform)
        for platform in ("binary_sensor", "switch", "light", "climate", "sensor")
    )
    flow._pairing_actuator_type = "relay_rps"
    schemas.append(flow._pair_actuator_details_schema())
    flow._pairing_actuator_type = "dimmer_4bs"
    schemas.append(flow._pair_actuator_details_schema())
    # device_form in its three shapes: fresh, refilled after a rejected submit,
    # and without the manual EEP field because a profile is already declared.
    schemas.append(flow._device_form_schema())
    flow._pending_platform = "switch"
    flow._pending_name = "Named device"
    flow._pending_manual_eep = "A5-12-01"
    schemas.append(flow._device_form_schema())
    flow._radio_metadata = {"eep": "A5-12-01", "eep_source": "radio_declared"}
    schemas.append(flow._device_form_schema())
    for schema in schemas:
        try:
            voluptuous_serialize.convert(schema, custom_serializer=cv.custom_serializer)
        except ValueError as err:
            raise AssertionError(
                f"new options schema is not WS-serializable: {err}"
            ) from err


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
    _assert_unsolicited_ute_is_never_acknowledged()
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
    asyncio.run(_assert_dongle_status_signal_writes_state_from_worker_thread())
    asyncio.run(_assert_d2_status_parse_dispatch_and_entities())
    asyncio.run(_assert_ui_devices_create_entities_with_yaml_compatible_unique_ids())
    asyncio.run(_assert_options_flow_add_and_delete_device())
    asyncio.run(_assert_learn_service_starts_window_and_registers_once())
    asyncio.run(_assert_device_intelligence_flow_and_device_registry())
    asyncio.run(_assert_learn_windows_are_owned_and_serialized())
    _assert_options_schemas_are_ws_serializable()
    print(
        "HA_2026_7_3_SMOKE_OK "
        f"invalid_cases_rejected={len(invalid_cases)} "
        f"platform_invalid_cases_rejected={len(invalid_platform_configs)} "
        f"malformed_packets_dropped={len(malformed_packets)} "
        "brightness_mapping=exact rejected_commands=no_state_change "
        "esp3_optional=valid esp3_rejections=correlated actuator_initial=unknown "
        "ute_resync=valid ute_auto_ack=disabled "
        "diagnostics=redacted off_without_sensor=sent "
        "climate_responses=transactional climate_send_chain=real base_id_retry=valid "
        "teach_in=captured options_flow=add_collide_delete "
        "learn_service=idempotent_registration "
        "learn_windows=owned_serialized options_schemas=ws_serializable "
        "deleted_id=teachable_again "
        "d2_status=parse_dispatch_switch_light d2_channels=0_1 "
        "d2_unknown_sender=ignored d2_malformed=ignored d2_rps=synced_no_leak "
        "d2_loop_write=safe "
        "device_intelligence=catalog_only device_registry=grouped_by_sender"
    )


if __name__ == "__main__":
    main()

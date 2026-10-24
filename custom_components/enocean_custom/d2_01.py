"""D2-01 actuator queries: status (CMD 0x3) and measurement (CMD 0x6).

Everything here is sourced from the EnOcean Equipment Profiles specification
2.6.7 (May 20, 2017), family D2-01 "Electronic switches and dimmers with
Energy Measurement and Local Control", pages 131-146:

* the capability matrix of TYPEs 0x00..0x14 (pages 131-132) decides which
  profiles report a measurement on query and whether that is energy, power
  or both; nothing is inferred from a product name or from traffic;
* CMD 0x3 "Actuator Status Query" (page 135): two data bytes, the command in
  the low nibble of the first, the I/O channel (0x00..0x1D, 0x1E = all output
  channels, 0x1F = input channel) in the low five bits of the second. "An
  Actuator Status Response message shall be received within a maximum of
  300ms from the time of transmission of this message. In case no such
  response is received within this time frame the action shall be treated as
  completed without result.";
* CMD 0x4 "Actuator Status Response" (pages 135-136): sent by the actuator
  "within a maximum of 50ms from the time of reception" of the query;
  decoded by ``protocol/d2.py``;
* CMD 0x6 "Actuator Measurement Query" (page 137): same two-byte layout with
  the ``qu`` bit (offset 10, 0 = energy, 1 = power) above the channel, and the
  same 300 ms response window; CMD 0x7 (pages 137-138) is decoded by the
  existing power/energy sensors.

CMD 0x2 "Actuator Set Local" (pages 133-134) is deliberately not implemented:
it rewrites every local setting at once (taught-in devices enable, over
current behaviour, local control, dim timers, day/night, power failure
detection, default state) with no "keep" value, and the family defines no
read-back for most of them ("Response Timing: None"); CMD 0x5 "Actuator Set
Measurement" (page 136) likewise changes the device's autonomous reporting
with no read-back. Only what the actuator itself reports in CMD 0x4 is
exposed, read-only.

Radio discipline: a query is transmitted only at start-up when the operator
enabled ``d2_01_query_on_start`` (off by default) or when the per-actuator
"Refresh state" button is pressed. Queries are serialized, one ESP3 command in
flight, at least 250 ms apart, at most one per device and channel per pass,
and never retried.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DATA_ENOCEAN, LOGGER, SIGNAL_RECEIVE_MESSAGE
from .enocean_library.protocol.constants import PACKET, RORG
from .enocean_library.protocol.d2 import parse_d2_01_actuator_status
from .enocean_library.protocol.packet import Packet as ESP3Packet
from .enocean_library.utils import combine_hex
from .entity import build_radio_optional, get_gateway_base_id
from .identity import SENSOR_DEFAULT_DEVICE_CLASS

# Entry option: query every D2-01 actuator once after start-up. Off by default:
# enabling it is the only way this integration ever transmits on its own.
CONF_D2_01_QUERY_ON_START: Final = "d2_01_query_on_start"

DATA_D2_01_RUNNERS: Final = "d2_01_runners"

CMD_STATUS_QUERY: Final = 0x03
CMD_STATUS_RESPONSE: Final = 0x04
CMD_MEASUREMENT_QUERY: Final = 0x06
CMD_MEASUREMENT_RESPONSE: Final = 0x07
# CMD 0x6 offset 10 ("qu": 0 = query energy, 1 = query power), EEP 2.6.7 p.137.
MEASUREMENT_QUERY_POWER_BIT: Final = 0x20
IO_CHANNEL_MASK: Final = 0x1F

# EEP 2.6.7 p.135 (CMD 0x3) and p.137 (CMD 0x6): the response "shall be
# received within a maximum of 300ms from the time of transmission".
RESPONSE_WINDOW_SECONDS: Final = 0.300
# Minimum spacing between two transmissions of one pass (duty-cycle courtesy).
MIN_SPACING_SECONDS: Final = 0.250
# Upper bound on the wait for the dongle's ESP3 result of one query; the
# serial worker itself stops the transport after 1 s without a response.
TRANSPORT_TIMEOUT_SECONDS: Final = 2.0
# How long the start-up pass waits for the dongle Base ID before giving up.
BASE_ID_WAIT_SECONDS: Final = 20.0

# EEP 2.6.7, D2-01 capability table, pages 131-132. Each set lists the TYPEs
# whose column carries an "X" on the named row; only the rows this module
# needs are transcribed. Every TYPE 0x00..0x14 supports CMD 0x1/0x3/0x4
# (pages 132-136 define them for the family; the per-type command tables on
# pages 144 and 146 mark 0x3 and 0x4 for 0x0C and 0x0D..0x12).
D2_01_TYPES: Final = frozenset(range(0x15))
ENERGY_MEASUREMENT_TYPES: Final = frozenset(
    {0x00, 0x02, 0x04, 0x05, 0x06, 0x08, 0x09, 0x0B, 0x0C, 0x0E, 0x10}
)
POWER_MEASUREMENT_TYPES: Final = frozenset({0x04, 0x05, 0x08, 0x09, 0x0B, 0x0C, 0x0E})
MEASUREMENT_REPORT_ON_QUERY_TYPES: Final = frozenset(
    {0x00, 0x02, 0x04, 0x05, 0x06, 0x08, 0x09, 0x0B, 0x0C, 0x0E, 0x10}
)
LOCAL_CONTROL_TYPES: Final = D2_01_TYPES - {0x06, 0x07}
POWER_FAILURE_DETECTION_TYPES: Final = frozenset({0x0A, 0x0B})
OUTPUT_CHANNELS_BY_TYPE: Final[Mapping[int, int]] = {
    **dict.fromkeys(range(0x10), 1),
    0x10: 2,
    0x11: 2,
    0x12: 2,
    0x13: 4,
    0x14: 8,
}


def d2_01_type(eep: Any) -> int | None:
    """Return the TYPE of a D2-01 EEP string, or None for anything else."""
    if not isinstance(eep, str):
        return None
    parts = eep.upper().split("-")
    if len(parts) != 3 or parts[0] != "D2" or parts[1] != "01":
        return None
    try:
        type_ = int(parts[2], 16)
    except ValueError:
        return None
    return type_ if type_ in D2_01_TYPES else None


def supports_status_query(eep: Any) -> bool:
    """Return whether CMD 0x3 applies: every D2-01 TYPE, nothing else."""
    return d2_01_type(eep) is not None


def measurement_queries(eep: Any) -> tuple[bool, bool]:
    """Return ``(energy, power)``: which CMD 0x6 queries the profile answers.

    Both require "Measurement report on query" for the TYPE; energy and power
    follow their own rows of the capability table. A profile not in the table
    (an undeclared row, a non-D2-01 EEP) answers neither.
    """
    type_ = d2_01_type(eep)
    if type_ is None or type_ not in MEASUREMENT_REPORT_ON_QUERY_TYPES:
        return False, False
    return type_ in ENERGY_MEASUREMENT_TYPES, type_ in POWER_MEASUREMENT_TYPES


@dataclass(frozen=True, slots=True)
class D201Target:
    """One actuator channel to query, and which queries it is entitled to."""

    dev_id: tuple[int, ...]
    channel: int
    name: str
    eep: str | None
    query_energy: bool = False
    query_power: bool = False

    @property
    def sender(self) -> int:
        """Return the actuator's identifier as an integer."""
        return combine_hex(list(self.dev_id))


def d2_01_targets(rows: Iterable[Mapping[str, Any]]) -> list[D201Target]:
    """Return the D2-01 actuator channels of a group of rows, deduplicated.

    A ``switch`` row with ``switch_type: default`` is driven with D2-01 CMD
    0x1 and is therefore queried with CMD 0x3. Rows emulating a rocker
    (``RPS``, including every Ubiwizz UBID1507C profile row) are not D2
    actuators from this integration's point of view and are never queried.
    Measurement queries additionally require a declared D2-01 TYPE that
    reports on query *and* a power/energy sensor row on the same sender to
    receive the answer; otherwise the answer would reach nothing.
    """
    rows = list(rows)
    metering_senders = {
        combine_hex(row["id"])
        for row in rows
        if row.get("platform") == "sensor"
        and row.get("device_class") == SENSOR_DEFAULT_DEVICE_CLASS
    }
    targets: list[D201Target] = []
    seen: set[tuple[int, int]] = set()
    for row in rows:
        if row.get("platform") != "switch" or (row.get("switch_type") or "default") != (
            "default"
        ):
            continue
        key = (combine_hex(row["id"]), int(row.get("channel", 0)))
        if key in seen:
            continue
        seen.add(key)
        eep = (row.get("radio_metadata") or {}).get("eep")
        energy, power = measurement_queries(eep)
        metering = key[0] in metering_senders
        targets.append(
            D201Target(
                dev_id=tuple(row["id"]),
                channel=key[1],
                name=str(row.get("name", "")),
                eep=eep if isinstance(eep, str) else None,
                query_energy=energy and metering,
                query_power=power and metering,
            )
        )
    return targets


def build_status_query(
    sender_id: list[int], destination: list[int], channel: int
) -> ESP3Packet:
    """Return the ERP1 frame of a CMD 0x3 Actuator Status Query (EEP 2.6.7 p.135)."""
    return ESP3Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.VLD, CMD_STATUS_QUERY, channel & IO_CHANNEL_MASK, *sender_id, 0x00],
        optional=build_radio_optional(destination),
    )


def build_measurement_query(
    sender_id: list[int], destination: list[int], channel: int, *, power: bool
) -> ESP3Packet:
    """Return the ERP1 frame of a CMD 0x6 Actuator Measurement Query (p.137)."""
    qu = MEASUREMENT_QUERY_POWER_BIT if power else 0x00
    return ESP3Packet(
        PACKET.RADIO_ERP1,
        data=[
            RORG.VLD,
            CMD_MEASUREMENT_QUERY,
            qu | (channel & IO_CHANNEL_MASK),
            *sender_id,
            0x00,
        ],
        optional=build_radio_optional(destination),
    )


def is_matching_response(packet: Any, target: D201Target, command: int) -> bool:
    """Return whether a radio packet answers a query of ``command`` for the target."""
    if getattr(packet, "sender_int", None) != target.sender:
        return False
    data = getattr(packet, "data", None)
    if not isinstance(data, (list, tuple)) or len(data) < 3 or data[0] != RORG.VLD:
        return False
    if command == CMD_STATUS_QUERY:
        status = parse_d2_01_actuator_status(list(data))
        return status is not None and status.channel == target.channel
    if command == CMD_MEASUREMENT_QUERY:
        # CMD 0x7, EEP 2.6.7 pp.137-138: command in the low nibble of the
        # first data byte, I/O channel in the low five bits of the second.
        return (
            data[1] & 0x0F == CMD_MEASUREMENT_RESPONSE
            and data[2] & IO_CHANNEL_MASK == target.channel
        )
    return False


@dataclass(slots=True)
class D201QueryReport:
    """What one pass did, for logs and diagnostics; never identifiers."""

    sent: int = 0
    answered: int = 0
    unanswered: int = 0
    rejected: int = 0
    skipped_no_base_id: int = 0
    outcomes: list[str] = field(default_factory=list)


class D201QueryRunner:
    """Serialize D2-01 queries for one config entry.

    One pass is a list of targets; for each, a status query and, where the
    profile and the configured sensors justify it, an energy and/or a power
    measurement query. Every transmission waits for the dongle's ESP3 result,
    then for the actuator's answer up to the specification's 300 ms window
    counted from the physical write, and keeps at least 250 ms between two
    writes. Nothing is retried; a pass runs once.
    """

    def __init__(self, hass: HomeAssistant, gateway: Any) -> None:
        """Bind to the dongle of one entry."""
        self.hass = hass
        self.gateway = gateway
        self._lock = asyncio.Lock()
        self._last_write_at: float | None = None

    async def async_run(
        self, targets: Iterable[D201Target], *, reason: str
    ) -> D201QueryReport:
        """Run one pass over the targets, one query at a time."""
        report = D201QueryReport()
        async with self._lock:
            for target in targets:
                sender_id = get_gateway_base_id(self.gateway)
                if sender_id is None:
                    report.skipped_no_base_id += 1
                    report.outcomes.append("no_base_id")
                    continue
                destination = list(target.dev_id)
                commands = [
                    (
                        CMD_STATUS_QUERY,
                        build_status_query(sender_id, destination, target.channel),
                    )
                ]
                if target.query_energy:
                    commands.append(
                        (
                            CMD_MEASUREMENT_QUERY,
                            build_measurement_query(
                                sender_id, destination, target.channel, power=False
                            ),
                        )
                    )
                if target.query_power:
                    commands.append(
                        (
                            CMD_MEASUREMENT_QUERY,
                            build_measurement_query(
                                sender_id, destination, target.channel, power=True
                            ),
                        )
                    )
                for command, packet in commands:
                    outcome = await self._async_send_and_wait(target, command, packet)
                    report.outcomes.append(outcome)
                    if outcome == "rejected":
                        report.rejected += 1
                        continue
                    report.sent += 1
                    if outcome == "answered":
                        report.answered += 1
                    else:
                        report.unanswered += 1
        LOGGER.debug(
            "D2-01 %s pass: %d sent, %d answered, %d unanswered, %d rejected, "
            "%d skipped (Base ID)",
            reason,
            report.sent,
            report.answered,
            report.unanswered,
            report.rejected,
            report.skipped_no_base_id,
        )
        return report

    async def _async_send_and_wait(
        self, target: D201Target, command: int, packet: ESP3Packet
    ) -> str:
        """Transmit one query and wait for its answer within the EEP window."""
        loop = asyncio.get_running_loop()
        await self._async_respect_spacing(loop)
        accepted: asyncio.Future[bool] = loop.create_future()
        written: asyncio.Future[float] = loop.create_future()
        answered = asyncio.Event()

        @callback
        def _esp3_result(ok: bool) -> None:
            if not accepted.done():
                accepted.set_result(bool(ok))

        @callback
        def _written() -> None:
            if not written.done():
                written.set_result(loop.time())

        @callback
        def _radio_received(received: Any) -> None:
            if is_matching_response(received, target, command):
                answered.set()

        unsubscribe = async_dispatcher_connect(
            self.hass, SIGNAL_RECEIVE_MESSAGE, _radio_received
        )
        try:
            queued = self.gateway.send(
                packet, response_callback=_esp3_result, written_callback=_written
            )
            if not queued:
                return "rejected"
            try:
                ok = await asyncio.wait_for(accepted, TRANSPORT_TIMEOUT_SECONDS)
            except TimeoutError:
                ok = False
            self._last_write_at = written.result() if written.done() else loop.time()
            if not ok:
                return "rejected"
            # The 300 ms window runs from the physical write (EEP 2.6.7
            # p.135/p.137 "from the time of transmission").
            deadline = self._last_write_at + RESPONSE_WINDOW_SECONDS
            remaining = deadline - loop.time()
            if remaining > 0 and not answered.is_set():
                try:
                    await asyncio.wait_for(answered.wait(), remaining)
                except TimeoutError:
                    pass
            return "answered" if answered.is_set() else "unanswered"
        finally:
            unsubscribe()

    async def _async_respect_spacing(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._last_write_at is None:
            return
        wait = self._last_write_at + MIN_SPACING_SECONDS - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)


def get_d2_01_runner(
    hass: HomeAssistant, entry_id: str, gateway: Any
) -> D201QueryRunner:
    """Return the runner of one entry, creating it on first use."""
    data = hass.data.setdefault(DATA_ENOCEAN, {})
    runners = data.setdefault(DATA_D2_01_RUNNERS, {})
    runner = runners.get(entry_id)
    if runner is None or runner.gateway is not gateway:
        runner = D201QueryRunner(hass, gateway)
        runners[entry_id] = runner
    return runner


def drop_d2_01_runner(hass: HomeAssistant, entry_id: str) -> None:
    """Forget the runner of an entry being unloaded."""
    runners = hass.data.get(DATA_ENOCEAN, {}).get(DATA_D2_01_RUNNERS, {})
    runners.pop(entry_id, None)


async def async_startup_pass(
    hass: HomeAssistant, gateway: Any, entry_id: str, targets: list[D201Target]
) -> D201QueryReport | None:
    """Query every target once after start-up, when the operator enabled it.

    Waits (bounded) for the dongle's Base ID, which the start-up
    ``CO_RD_IDBASE`` read provides shortly after the worker starts; without
    it no D2 frame can be addressed and the pass is skipped with a log line.
    """
    if not targets:
        return None
    deadline = time.monotonic() + BASE_ID_WAIT_SECONDS
    while get_gateway_base_id(gateway) is None:
        if time.monotonic() >= deadline:
            LOGGER.warning(
                "D2-01 start-up state query skipped: the dongle Base ID was not "
                "resolved within %d s",
                int(BASE_ID_WAIT_SECONDS),
            )
            return None
        await asyncio.sleep(0.5)
    runner = get_d2_01_runner(hass, entry_id, gateway)
    report = await runner.async_run(targets, reason="start-up")
    LOGGER.info(
        "D2-01 start-up state query: %d actuator channel(s), %d query(ies) sent, "
        "%d answered",
        len(targets),
        report.sent,
        report.answered,
    )
    return report

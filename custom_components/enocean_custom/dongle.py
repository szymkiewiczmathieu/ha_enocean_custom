"""Representation of an EnOcean dongle."""

from __future__ import annotations

import glob
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from os.path import samefile
from threading import Lock
from time import monotonic
from typing import Any

import serial
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send, dispatcher_send

from .const import (
    DOMAIN,
    ISSUE_SERIAL_STOPPED,
    SIGNAL_DONGLE_STATUS,
    SIGNAL_GATEWAY_UPDATE,
    SIGNAL_RECEIVE_MESSAGE,
)
from .enocean_library.communicators import SerialCommunicator
from .enocean_library.protocol.constants import RETURN_CODE
from .enocean_library.protocol.packet import RadioPacket, ResponsePacket
from .inbox import DeviceInbox, packet_observation

_LOGGER = logging.getLogger(__name__)

# ESP3 common commands addressed to the dongle itself. They travel on the
# serial link only: none of them transmits anything on the radio.
CO_RD_VERSION = 0x03
CO_WR_REPEATER = 0x09
CO_RD_REPEATER = 0x0A


@dataclass(frozen=True, slots=True)
class GatewayInfo:
    """What the dongle reports about itself (ESP3 CO_RD_VERSION, 2.5.5).

    Versions are rendered as the four dotted bytes of the response
    (main.beta.alpha.build). ``chip_id`` is the module's own radio identifier
    and is treated like every other EnOcean identifier: shown in the device
    registry, never exported in diagnostics.
    """

    app_version: str
    api_version: str
    chip_id: str
    chip_version: str
    app_description: str


@dataclass(frozen=True, slots=True)
class RepeaterState:
    """The dongle's own repeater setting (ESP3 CO_RD_REPEATER, 2.5.12)."""

    enabled: bool
    level: int
    # 0 = off, 1 = on, 2 = selective (filtered), per ESP3 V1.58.
    raw_enable: int


def parse_version_response(packet: Any) -> GatewayInfo | None:
    """Decode a CO_RD_VERSION response; None for a refusal or an odd length."""
    if packet is None or getattr(packet, "response", None) != RETURN_CODE.OK:
        return None
    data = list(getattr(packet, "response_data", []) or [])
    if len(data) != 32 or any(
        isinstance(byte, bool) or not isinstance(byte, int) or not 0 <= byte <= 0xFF
        for byte in data
    ):
        return None
    description = bytes(data[16:32]).split(b"\x00", 1)[0]
    return GatewayInfo(
        app_version=".".join(str(byte) for byte in data[0:4]),
        api_version=".".join(str(byte) for byte in data[4:8]),
        chip_id="".join(f"{byte:02X}" for byte in data[8:12]),
        chip_version=".".join(str(byte) for byte in data[12:16]),
        app_description=description.decode("ascii", errors="replace").strip(),
    )


def parse_repeater_response(packet: Any) -> RepeaterState | None:
    """Decode a CO_RD_REPEATER response; None for a refusal or an odd length."""
    if packet is None or getattr(packet, "response", None) != RETURN_CODE.OK:
        return None
    data = list(getattr(packet, "response_data", []) or [])
    if len(data) != 2 or any(
        isinstance(byte, bool) or not isinstance(byte, int) for byte in data
    ):
        return None
    enable, level = data
    if enable not in (0, 1, 2) or level not in (0, 1, 2):
        return None
    return RepeaterState(enabled=enable != 0, level=level, raw_enable=enable)


class EnOceanDongle:
    """Own the serial worker and expose its health to Home Assistant."""

    # The start-up CO_RD_IDBASE read can be refused or lost. Rather than
    # blocking every D2/UTE command until a reload, a command that needs the
    # Base ID re-issues the read on demand: at most BASE_ID_RETRY_LIMIT times
    # per worker, with a growing delay between attempts and never while a
    # read is still pending.
    BASE_ID_RETRY_LIMIT = 3
    BASE_ID_RETRY_BACKOFF_SECONDS = (5.0, 30.0, 120.0)

    def __init__(self, hass, serial_path: str, entry_id: str | None = None) -> None:
        """Initialize the EnOcean dongle and open its serial descriptor."""
        self.serial_path = serial_path
        self.identifier = "configured EnOcean dongle"
        self.hass = hass
        # The owning config entry; gateway entities and ``via_device`` links
        # are keyed on it. None only in tests that build a bare dongle.
        self.entry_id = entry_id
        self._gateway_info: GatewayInfo | None = None
        self._repeater: RepeaterState | None = None
        self._available = False
        self._stopping = False
        self._base_id_retry_count = 0
        self._base_id_retry_not_before = 0.0
        self._base_id_retry_lock = Lock()
        self._started_at: datetime | None = None
        self._last_packet_at: datetime | None = None
        self._last_error: str | None = None
        self._received_packets = 0
        self._transmit_attempts = 0
        self._transmit_queued = 0
        self._transmit_rejected = 0
        self._response_packets = 0
        self._response_errors = 0
        self._last_response_at: datetime | None = None
        self._last_response_code: str | None = None
        self._response_lock = Lock()
        self._inbox: DeviceInbox | None = None
        self._queued_response_callbacks: dict[
            int, tuple[Callable[[bool], None] | None, Callable[[], None] | None]
        ] = {}
        self._pending_response_callbacks: deque[Callable[[bool], None] | None] = deque()
        # teach_in=False is explicit and load-bearing: the runtime worker must
        # never acknowledge a UTE teach-in on its own. Answering one is an
        # unsolicited radio transmission that pairs whatever device asks,
        # outside any operator-opened window. Pairing stays operator-driven
        # through the existing teach-in services and the guided flows, which
        # only ever transmit on an explicit user action.
        self._communicator = SerialCommunicator(
            port=serial_path,
            callback=self.callback,
            stopped_callback=self._communicator_stopped,
            written_callback=self._packet_written,
            teach_in=False,
        )

    @property
    def available(self) -> bool:
        """Return whether the serial worker can currently accept traffic."""
        return self._available and not self._stopping and self._communicator.is_alive()

    @property
    def base_id(self) -> list[int] | None:
        """Return the already-resolved radio sender ID without blocking HA."""
        return self._communicator.current_base_id

    @property
    def gateway_info(self) -> GatewayInfo | None:
        """Return the firmware/chip facts reported by CO_RD_VERSION, if any."""
        return self._gateway_info

    @property
    def repeater(self) -> RepeaterState | None:
        """Return the dongle's own repeater setting as last read back."""
        return self._repeater

    @property
    def received_radio_packets(self) -> int:
        """Return how many valid radio telegrams the worker has dispatched."""
        return self._received_packets

    @property
    def last_packet_at(self) -> datetime | None:
        """Return when the last valid radio telegram was dispatched."""
        return self._last_packet_at

    def request_version(self) -> bool:
        """Queue CO_RD_VERSION; the answer updates ``gateway_info`` (no radio)."""
        return self._communicator.request_common_command(
            [CO_RD_VERSION], self._version_response
        )

    def request_repeater(self) -> bool:
        """Queue CO_RD_REPEATER; the answer updates ``repeater`` (no radio)."""
        return self._communicator.request_common_command(
            [CO_RD_REPEATER], self._repeater_response
        )

    def _version_response(self, packet: Any) -> None:
        """Store the version facts (worker thread) and notify the event loop."""
        info = parse_version_response(packet)
        if info is None:
            _LOGGER.debug("The dongle did not report a usable CO_RD_VERSION answer")
            return
        self._gateway_info = info
        self._notify_gateway_update()

    def _repeater_response(self, packet: Any) -> None:
        """Store the repeater read-back (worker thread) and notify the loop."""
        state = parse_repeater_response(packet)
        if state is None:
            _LOGGER.debug("The dongle did not report a usable CO_RD_REPEATER answer")
            return
        self._repeater = state
        self._notify_gateway_update()

    def _notify_gateway_update(self) -> None:
        """Publish a gateway fact change on Home Assistant's event loop."""
        try:
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, SIGNAL_GATEWAY_UPDATE
            )
        except RuntimeError:
            _LOGGER.debug("Home Assistant loop closed before gateway update")

    def retry_base_id(self) -> bool:
        """Re-request the Base ID for a command that needs it, within bounds.

        Returns True when a new CO_RD_IDBASE command was queued. Safe from the
        event loop (flows) and from the executor (switch commands). Nothing is
        sent while the Base ID is known, the worker is unavailable, a read is
        still pending, the retry budget is spent, or the backoff has not
        elapsed; the caller still skips its current command.
        """
        if self.base_id is not None or not self.available:
            return False
        with self._base_id_retry_lock:
            now = monotonic()
            if (
                self._communicator.base_id_request_pending
                or self._base_id_retry_count >= self.BASE_ID_RETRY_LIMIT
                or now < self._base_id_retry_not_before
            ):
                return False
            attempt = self._base_id_retry_count
            self._base_id_retry_count += 1
            backoff = self.BASE_ID_RETRY_BACKOFF_SECONDS[
                min(attempt, len(self.BASE_ID_RETRY_BACKOFF_SECONDS) - 1)
            ]
            self._base_id_retry_not_before = now + backoff
        queued = self._communicator.request_base_id()
        _LOGGER.warning(
            "Dongle Base ID is still unknown; %s (attempt %d of %d)",
            "re-requesting it" if queued else "its re-request was not queued",
            attempt + 1,
            self.BASE_ID_RETRY_LIMIT,
        )
        return queued

    async def async_setup(self) -> None:
        """Start the serial worker and clear any stale repair issue."""
        self._started_at = datetime.now(UTC)
        self._available = True
        self._communicator.start()
        self._communicator.request_base_id()
        # Both are dongle-local ESP3 reads: nothing is transmitted on the radio.
        self.request_version()
        self.request_repeater()
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_SERIAL_STOPPED)
        dispatcher_send(self.hass, SIGNAL_DONGLE_STATUS, self.available)

    def set_inbox(self, inbox: DeviceInbox | None) -> None:
        """Attach the passive registry owned by the current config entry."""
        self._inbox = inbox

    @property
    def inbox(self) -> DeviceInbox | None:
        """Return the passive registry attached by the config entry, if any."""
        return self._inbox

    async def async_unload(self) -> bool:
        """Stop the sole serial owner before allowing config-entry reload."""
        self._stopping = True
        self._available = False
        dispatcher_send(self.hass, SIGNAL_DONGLE_STATUS, False)
        self._communicator.stop()
        # Keep the join off the event loop and bounded. If it fails, unloading
        # must fail too so Home Assistant cannot create a competing reader.
        await self.hass.async_add_executor_job(self._communicator.join, 1)
        if self._communicator.is_alive():
            self._last_error = "Serial worker did not stop within 1 second"
            self._stopping = False
            _LOGGER.warning(self._last_error)
            self._notify_status()
            return False
        return True

    def send(
        self,
        command: Any,
        response_callback: Callable[[bool], None] | None = None,
        written_callback: Callable[[], None] | None = None,
    ) -> bool:
        """Queue one command and report wire/ESP3 lifecycle callbacks.

        ``written_callback`` runs on Home Assistant's event loop only after the
        serial worker has physically written this exact packet. It lets a radio
        protocol start a receive deadline from transmission, not queueing.
        ``response_callback`` remains coupled to the following ESP3 response.
        """
        self._transmit_attempts += 1
        if not self.available:
            self._transmit_rejected += 1
            return False
        try:
            command.build()
        except (AttributeError, TypeError, ValueError):
            self._transmit_rejected += 1
            _LOGGER.exception("Rejecting invalid EnOcean command before queueing")
            return False
        with self._response_lock:
            self._queued_response_callbacks[id(command)] = (
                response_callback,
                written_callback,
            )
        accepted = self._communicator.send(command)
        if accepted:
            self._transmit_queued += 1
        else:
            with self._response_lock:
                self._queued_response_callbacks.pop(id(command), None)
            self._transmit_rejected += 1
        return accepted

    def _packet_written(self, packet: Any) -> None:
        """Register response ownership and publish the physical write once."""
        with self._response_lock:
            callbacks = self._queued_response_callbacks.pop(id(packet), None)
            if callable(callbacks):
                response_callback, written_callback = callbacks, None
            else:
                response_callback, written_callback = callbacks or (None, None)
            self._pending_response_callbacks.append(response_callback)
        if written_callback is not None:
            try:
                self.hass.loop.call_soon_threadsafe(written_callback)
            except RuntimeError:
                _LOGGER.debug("Home Assistant loop closed before write notification")

    def callback(self, packet: Any) -> None:
        """Dispatch a valid radio packet from the worker thread."""
        if isinstance(packet, ResponsePacket):
            self._response_packets += 1
            self._last_response_at = datetime.now(UTC)
            try:
                response = RETURN_CODE(packet.response)
                self._last_response_code = response.name
            except ValueError:
                response = None
                self._last_response_code = f"UNKNOWN_{packet.response}"
            if response != RETURN_CODE.OK:
                self._response_errors += 1
                self._last_error = f"ESP3 command response: {self._last_response_code}"
                _LOGGER.warning(self._last_error)
            with self._response_lock:
                response_callback = (
                    self._pending_response_callbacks.popleft()
                    if self._pending_response_callbacks
                    else None
                )
            if response_callback is not None:
                try:
                    self.hass.loop.call_soon_threadsafe(
                        response_callback, response == RETURN_CODE.OK
                    )
                except RuntimeError:
                    _LOGGER.debug(
                        "Home Assistant loop closed before ESP3 response notification"
                    )
            return
        if not isinstance(packet, RadioPacket):
            return
        self._received_packets += 1
        self._last_packet_at = datetime.now(UTC)
        _LOGGER.debug("Received EnOcean packet type=%s", packet.packet_type)
        if self._inbox is not None and (observation := packet_observation(packet)):
            try:
                self.hass.loop.call_soon_threadsafe(self._inbox.observe, observation)
            except RuntimeError:
                _LOGGER.debug("Home Assistant loop closed before inbox update")
        try:
            dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, packet)
        except RuntimeError:
            _LOGGER.debug("Home Assistant loop closed before packet dispatch")

    def _communicator_stopped(self, error: str | None) -> None:
        """Record worker termination and hand notification to HA's event loop."""
        self._available = False
        if error:
            self._last_error = error.replace(self.serial_path, "[REDACTED]")
        elif not self._stopping:
            self._last_error = "Serial worker exited unexpectedly"
        with self._response_lock:
            callbacks = [
                response_callback
                for response_callback, _written_callback in (
                    *self._queued_response_callbacks.values(),
                )
                if response_callback is not None
            ]
            callbacks.extend(
                response_callback
                for response_callback in self._pending_response_callbacks
                if response_callback is not None
            )
            self._queued_response_callbacks.clear()
            self._pending_response_callbacks.clear()
        try:
            for response_callback in callbacks:
                self.hass.loop.call_soon_threadsafe(response_callback, False)
            self.hass.loop.call_soon_threadsafe(self._notify_status)
        except RuntimeError:
            _LOGGER.debug("Home Assistant loop closed before serial stop notification")

    def _notify_status(self) -> None:
        """Publish availability and create a Repair for unexpected termination."""
        dispatcher_send(self.hass, SIGNAL_DONGLE_STATUS, self.available)
        if self.available or self._stopping:
            return
        reason = self._last_error or "Serial worker is not running"
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            ISSUE_SERIAL_STOPPED,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_SERIAL_STOPPED,
            translation_placeholders={"device": self.identifier, "reason": reason},
        )

    def diagnostics(self) -> dict[str, Any]:
        """Return privacy-safe runtime diagnostics for the config entry."""
        transmit_queue = getattr(self._communicator, "transmit", None)
        queue_depth = None
        if transmit_queue is not None:
            try:
                queue_depth = transmit_queue.qsize()
            except (AttributeError, NotImplementedError, OSError):
                _LOGGER.debug("EnOcean transmit queue depth is unavailable")
        thread_name = self._communicator.name.replace(self.serial_path, "[REDACTED]")
        with self._response_lock:
            pending_responses = len(self._pending_response_callbacks)
        # Read defensively: lifecycle tests build the dongle without __init__.
        info = getattr(self, "_gateway_info", None)
        repeater = getattr(self, "_repeater", None)
        return {
            "serial_device": self.identifier,
            "available": self.available,
            "thread_alive": self._communicator.is_alive(),
            "thread_name": thread_name,
            "stopping": self._stopping,
            "base_id_resolved": self.base_id is not None,
            "base_id_retries": self._base_id_retry_count,
            # The chip ID is an EnOcean identifier and stays out of exports.
            "gateway_info": (
                {
                    "app_version": info.app_version,
                    "api_version": info.api_version,
                    "chip_version": info.chip_version,
                    "app_description": info.app_description,
                }
                if info is not None
                else None
            ),
            "repeater": (
                {"enabled": repeater.enabled, "level": repeater.level}
                if repeater is not None
                else None
            ),
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_packet_at": (
                self._last_packet_at.isoformat() if self._last_packet_at else None
            ),
            "last_error": self._last_error,
            "received_radio_packets": self._received_packets,
            "transmit_attempts": self._transmit_attempts,
            "transmit_queued": self._transmit_queued,
            "transmit_rejected": self._transmit_rejected,
            "transmit_queue_depth": queue_depth,
            "response_packets": self._response_packets,
            "response_errors": self._response_errors,
            "last_response_at": (
                self._last_response_at.isoformat() if self._last_response_at else None
            ),
            "last_response_code": self._last_response_code,
            "pending_responses": pending_responses,
        }


def detect() -> list[str]:
    """Return candidate paths for USB EnOcean dongles."""
    globs_to_test = ["/dev/tty*FTOA2PV*", "/dev/serial/by-id/*EnOcean*"]
    found_paths: list[str] = []
    for current_glob in globs_to_test:
        found_paths.extend(glob.glob(current_glob))
    return sorted(set(found_paths))


def same_device(first_path: str, second_path: str) -> bool:
    """Return whether two serial paths resolve to the same device node."""
    try:
        return samefile(first_path, second_path)
    except OSError:
        return False


def validate_path(path: str) -> bool:
    """Return whether the path can be opened as a serial port."""
    communicator = None
    try:
        communicator = SerialCommunicator(port=path)
        return True
    except serial.SerialException:
        _LOGGER.warning("The configured EnOcean dongle path is invalid")
        return False
    finally:
        if communicator is not None:
            communicator.close()

"""Representation of an EnOcean dongle."""

from __future__ import annotations

import glob
import logging
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from os.path import samefile
from threading import Lock
from typing import Any

import serial
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import dispatcher_send

from .const import (
    DOMAIN,
    ISSUE_SERIAL_STOPPED,
    SIGNAL_DONGLE_STATUS,
    SIGNAL_RECEIVE_MESSAGE,
)
from .enocean_library.communicators import SerialCommunicator
from .enocean_library.protocol.constants import RETURN_CODE
from .enocean_library.protocol.packet import RadioPacket, ResponsePacket
from .inbox import DeviceInbox, packet_observation

_LOGGER = logging.getLogger(__name__)


class EnOceanDongle:
    """Own the serial worker and expose its health to Home Assistant."""

    def __init__(self, hass, serial_path: str) -> None:
        """Initialize the EnOcean dongle and open its serial descriptor."""
        self.serial_path = serial_path
        self.identifier = "configured EnOcean dongle"
        self.hass = hass
        self._available = False
        self._stopping = False
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
        self._queued_response_callbacks: dict[int, Callable[[bool], None] | None] = {}
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

    async def async_setup(self) -> None:
        """Start the serial worker and clear any stale repair issue."""
        self._started_at = datetime.now(UTC)
        self._available = True
        self._communicator.start()
        self._communicator.request_base_id()
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_SERIAL_STOPPED)
        dispatcher_send(self.hass, SIGNAL_DONGLE_STATUS, self.available)

    def set_inbox(self, inbox: DeviceInbox | None) -> None:
        """Attach the passive registry owned by the current config entry."""
        self._inbox = inbox

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
    ) -> bool:
        """Queue one command and account for acceptance or rejection."""
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
            self._queued_response_callbacks[id(command)] = response_callback
        accepted = self._communicator.send(command)
        if accepted:
            self._transmit_queued += 1
        else:
            with self._response_lock:
                self._queued_response_callbacks.pop(id(command), None)
            self._transmit_rejected += 1
        return accepted

    def _packet_written(self, packet: Any) -> None:
        """Register response ownership in exact serial-write order."""
        with self._response_lock:
            response_callback = self._queued_response_callbacks.pop(id(packet), None)
            self._pending_response_callbacks.append(response_callback)

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
        dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, packet)

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
                for response_callback in (
                    *self._queued_response_callbacks.values(),
                    *self._pending_response_callbacks,
                )
                if response_callback is not None
            ]
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
        queue_depth = transmit_queue.qsize() if transmit_queue is not None else None
        thread_name = self._communicator.name.replace(self.serial_path, "[REDACTED]")
        with self._response_lock:
            pending_responses = len(self._pending_response_callbacks)
        return {
            "serial_device": self.identifier,
            "available": self.available,
            "thread_alive": self._communicator.is_alive(),
            "thread_name": thread_name,
            "stopping": self._stopping,
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

import logging
import threading
import time

import serial

from .communicator import Communicator


class SerialCommunicator(Communicator):
    """Serial port communicator class for EnOcean radio"""

    logger = logging.getLogger(__name__)
    RESPONSE_TIMEOUT_SECONDS = 1.0
    FRAME_ASSEMBLY_TIMEOUT_SECONDS = 0.5

    def __init__(
        self,
        port="/dev/ttyAMA0",
        callback=None,
        stopped_callback=None,
        written_callback=None,
        teach_in=False,
    ):
        # teach_in stays opt-in: a worker started for normal operation must
        # never acknowledge a UTE telegram it did not solicit.
        super().__init__(callback, teach_in=teach_in)
        self.name = "EnOceanSerialCommunicator"
        self.daemon = True
        # Linearization gate shared by send(), stop() and the physical write.
        # RLock is required because terminal-error paths can call stop() from
        # the worker itself.
        self._send_lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._stopped_callback = stopped_callback
        self._written_callback = written_callback
        self._terminal_error = None
        self._response_pending_packet = None
        self._response_deadline = None
        self._last_buffer_progress_at = None
        # Initialize serial port
        self.__ser = serial.Serial(port, 57600, timeout=0.1, write_timeout=0.5)

    def stop(self):
        """Stop the worker and actively interrupt pending serial I/O."""
        # Publish shutdown intent before cancellation.  A writer that has not
        # crossed the linearization gate must then abandon its packet.  A
        # writer already holding the gate is logically before stop(); the
        # cancellation below interrupts its bounded pyserial write.
        self._stop_requested.set()
        for method_name in ("cancel_read", "cancel_write"):
            cancel_io = getattr(self.__ser, method_name, None)
            if cancel_io is None:
                continue
            try:
                cancel_io()
            except (OSError, serial.SerialException, NotImplementedError):
                self.logger.warning("Unable to cancel serial %s", method_name)
        with self._send_lock:
            super().stop()

    def send(self, packet):
        """Queue a packet only while the serial worker is accepting work."""
        with self._send_lock:
            if self._stop_requested.is_set() or self._stop_flag.is_set():
                self.logger.warning("Dropping EnOcean packet after worker stop")
                return False
            return super().send(packet)

    def _packet_written(self, packet):
        """Start one bounded ESP3 response window for this physical write."""
        super()._packet_written(packet)
        self._response_pending_packet = packet
        self._response_deadline = time.monotonic() + self.RESPONSE_TIMEOUT_SECONDS
        if self._written_callback is not None:
            self._written_callback(packet)

    def _response_received(self, packet):
        """Release the sole in-flight command before dispatching its response."""
        self._response_pending_packet = None
        self._response_deadline = None
        super()._response_received(packet)

    def _stop_on_response_timeout(self):
        """Degrade instead of writing another command behind a missing response."""
        if (
            self._response_pending_packet is None
            or self._response_deadline is None
            or time.monotonic() < self._response_deadline
        ):
            return False
        packet = self._response_pending_packet
        self._response_pending_packet = None
        self._response_deadline = None
        self._response_timed_out(packet)
        self._terminal_error = "ESP3 response timeout; transport stopped"
        self.logger.error(self._terminal_error)
        self.stop()
        return True

    def run(self):
        self.logger.info("SerialCommunicator started")
        try:
            while not self._stop_flag.is_set():
                # ESP3 has no command identifier in RESPONSE packets. Keep exactly
                # one physical command in flight so a missing/corrupt response can
                # stop the transport before ownership is shifted to a later command.
                if self._response_pending_packet is None:
                    packet = self._get_from_send_queue()
                    if packet:
                        try:
                            payload = bytearray(packet.build())
                            # Resolve the bound method before entering the gate.
                            # This closes the adversarial lookup/check race: if
                            # stop() wins during attribute resolution, the
                            # packet is abandoned before serial.write is called.
                            serial_write = self.__ser.write
                            with self._send_lock:
                                if (
                                    self._stop_requested.is_set()
                                    or self._stop_flag.is_set()
                                ):
                                    break
                                written = serial_write(payload)
                                if self._stop_requested.is_set():
                                    # A cancelled pyserial write may return a
                                    # short count. During an intentional stop,
                                    # that is a clean shutdown, not a terminal
                                    # transport fault.
                                    break
                                if written != len(payload):
                                    self._terminal_error = (
                                        "SerialException: incomplete serial write"
                                    )
                                    self.logger.error("Incomplete serial write")
                                    self.stop()
                                    break
                                self._packet_written(packet)
                        except (TypeError, ValueError):
                            self.logger.exception("Invalid EnOcean packet; dropping it")
                        except serial.SerialException as err:
                            self._terminal_error = (
                                f"{type(err).__name__}: serial write failed"
                            )
                            self.logger.error("Serial port exception while writing")
                            self.stop()
                            break

                if self._stop_flag.is_set():
                    continue

                # Read chars from serial port as hex numbers.
                try:
                    chunk = bytearray(self.__ser.read(16))
                    if chunk:
                        self._buffer.extend(chunk)
                        self._last_buffer_progress_at = time.monotonic()
                    elif (
                        self._buffer
                        and self._last_buffer_progress_at is not None
                        and time.monotonic() - self._last_buffer_progress_at
                        >= self.FRAME_ASSEMBLY_TIMEOUT_SECONDS
                    ):
                        self.logger.error(
                            "Incomplete ESP3 frame timed out; discarding one byte"
                        )
                        del self._buffer[0]
                        self._last_buffer_progress_at = (
                            time.monotonic() if self._buffer else None
                        )
                except serial.SerialException as err:
                    self._terminal_error = f"{type(err).__name__}: serial read failed"
                    self.logger.error(
                        "Serial port exception! (device disconnected or multiple access on port?)"
                    )
                    self.stop()
                buffer_before_parse = bytes(self._buffer)
                try:
                    self.parse()
                except (IndexError, KeyError, TypeError, ValueError):
                    self.logger.exception(
                        "Malformed EnOcean frame; discarding one byte to resynchronize"
                    )
                    if self._buffer and bytes(self._buffer) == buffer_before_parse:
                        del self._buffer[0]
                except Exception:
                    self.logger.exception(
                        "Unexpected exception while parsing; resynchronizing"
                    )
                    if self._buffer and bytes(self._buffer) == buffer_before_parse:
                        del self._buffer[0]
                if not self._buffer:
                    self._last_buffer_progress_at = None
                self._stop_on_response_timeout()
                time.sleep(0)
        finally:
            self.close()
            self.logger.info("SerialCommunicator stopped")
            if self._stopped_callback is not None:
                try:
                    self._stopped_callback(self._terminal_error)
                except Exception:
                    self.logger.exception("Exception in serial stop callback")

    def close(self):
        """Close the serial descriptor. Safe to call more than once."""
        try:
            self.__ser.close()
        except serial.SerialException:
            self.logger.warning("Unable to close serial port")

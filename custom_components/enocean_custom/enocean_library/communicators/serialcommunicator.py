import logging
import threading
import time

import serial

from .communicator import Communicator


class SerialCommunicator(Communicator):
    ''' Serial port communicator class for EnOcean radio '''
    logger = logging.getLogger(__name__)

    def __init__(self, port='/dev/ttyAMA0', callback=None):
        super().__init__(callback)
        self.name = f"EnOceanSerialCommunicator[{port}]"
        self.daemon = True
        self._send_lock = threading.Lock()
        # Initialize serial port
        self.__ser = serial.Serial(
            port, 57600, timeout=0.1, write_timeout=0.5
        )

    def stop(self):
        """Stop the worker and actively interrupt pending serial I/O."""
        with self._send_lock:
            super().stop()
        for method_name in ("cancel_read", "cancel_write"):
            cancel_io = getattr(self.__ser, method_name, None)
            if cancel_io is None:
                continue
            try:
                cancel_io()
            except (OSError, serial.SerialException, NotImplementedError):
                self.logger.exception("Unable to %s", method_name)

    def send(self, packet):
        """Queue a packet only while the serial worker is accepting work."""
        with self._send_lock:
            if self._stop_flag.is_set():
                self.logger.warning("Dropping EnOcean packet after worker stop")
                return False
            super().send(packet)
            return True

    def run(self):
        self.logger.info('SerialCommunicator started')
        try:
            while not self._stop_flag.is_set():
                # If there are messages in the transmit queue, send them.
                while True:
                    if self._stop_flag.is_set():
                        break
                    packet = self._get_from_send_queue()
                    if not packet:
                        break
                    try:
                        payload = bytearray(packet.build())
                        if self._stop_flag.is_set():
                            break
                        self.__ser.write(payload)
                    except (TypeError, ValueError):
                        self.logger.exception("Invalid EnOcean packet; dropping it")
                    except serial.SerialException:
                        self.logger.exception('Serial port exception while writing')
                        self.stop()
                        break

                if self._stop_flag.is_set():
                    continue

                # Read chars from serial port as hex numbers.
                try:
                    self._buffer.extend(bytearray(self.__ser.read(16)))
                except serial.SerialException:
                    self.logger.exception(
                        'Serial port exception! (device disconnected or multiple access on port?)'
                    )
                    self.stop()
                try:
                    self.parse()
                except Exception:
                    self.logger.exception('Exception occurred while parsing')
                time.sleep(0)
        finally:
            self.close()
            self.logger.info('SerialCommunicator stopped')

    def close(self):
        '''Close the serial descriptor. Safe to call more than once.'''
        try:
            self.__ser.close()
        except serial.SerialException:
            self.logger.exception('Exception while closing serial port')

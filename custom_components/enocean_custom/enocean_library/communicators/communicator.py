import logging
import queue
import threading
from collections import deque
from datetime import UTC, datetime

from ..protocol.constants import PACKET, PARSE_RESULT, RETURN_CODE
from ..protocol.packet import Packet, ResponsePacket, UTETeachInPacket


class Communicator(threading.Thread):
    """
    Communicator base-class for EnOcean.
    Not to be used directly, only serves as base class for SerialCommunicator etc.
    """

    logger = logging.getLogger(__name__)
    BASE_ID_MAX_AUTO_RETRIES = 1
    TRANSMIT_QUEUE_MAXSIZE = 256

    def __init__(self, callback=None, teach_in=False):
        super().__init__()
        # Create an event to stop the thread
        self._stop_flag = threading.Event()
        # Input buffer
        self._buffer = []
        # Setup packet queues
        self.transmit = queue.Queue(maxsize=self.TRANSMIT_QUEUE_MAXSIZE)
        self.receive = queue.Queue()
        # Set the callback method
        self.__callback = callback
        # Internal variable for the Base ID of the module.
        self._base_id = None
        self._base_id_requested = False
        self._base_id_request_packet = None
        self._base_id_in_flight = False
        self._base_id_retry_count = 0
        self._pending_ute_packets = deque(maxlen=8)
        # Acknowledging a UTE teach-in is an unsolicited radio transmission:
        # it answers whichever device asks, whether or not an operator opened
        # a pairing session. It therefore defaults to off and must be enabled
        # explicitly, for the duration of an explicitly opened session only.
        self.teach_in = teach_in

    def _get_from_send_queue(self):
        """Get message from send queue, if one exists"""
        try:
            packet = self.transmit.get(block=False)
            self.logger.debug("Sending EnOcean packet type=%s", packet.packet_type)
            return packet
        except queue.Empty:
            pass
        return None

    def send(self, packet):
        if not isinstance(packet, Packet):
            self.logger.error("Object to send must be an instance of Packet")
            return False
        try:
            self.transmit.put_nowait(packet)
            return True
        except queue.Full:
            self.logger.error("EnOcean transmit queue is full; rejecting packet")
            return False

    def request_base_id(self, *, retry=False):
        """Request the transmitter Base ID without blocking the reader thread."""
        if self._base_id is not None or self._base_id_requested:
            return True
        if not retry:
            self._base_id_retry_count = 0
        packet = Packet(PACKET.COMMON_COMMAND, data=[0x08])
        self._base_id_requested = True
        self._base_id_request_packet = packet
        if self.send(packet):
            return True
        self._reset_base_id_request()
        return False

    def _reset_base_id_request(self):
        """Clear Base-ID ownership so a refused request can be retried."""
        self._base_id_requested = False
        self._base_id_request_packet = None
        self._base_id_in_flight = False

    def _packet_written(self, packet):
        """Record when the exact queued Base-ID command reaches the wire."""
        if packet is self._base_id_request_packet:
            self._base_id_in_flight = True

    def _response_received(self, packet):
        """Resolve Base-ID ownership from the response to its physical write."""
        if not self._base_id_in_flight:
            return
        if packet.response == RETURN_CODE.OK and len(packet.response_data) == 4:
            self._base_id = packet.response_data
        self._reset_base_id_request()
        if not self.teach_in:
            # A teach-in session can close while the Base-ID command is still
            # in flight. Answering afterwards would be exactly the unsolicited
            # transmission the session bounds, so the deferred teach-ins are
            # dropped instead of being acknowledged late.
            self._pending_ute_packets.clear()
        if self._base_id is None:
            if (
                self._pending_ute_packets
                and self._base_id_retry_count < self.BASE_ID_MAX_AUTO_RETRIES
                and not self._stop_flag.is_set()
            ):
                self._base_id_retry_count += 1
                self.logger.warning("Retrying Base ID request after dongle refusal.")
                self.request_base_id(retry=True)
            return
        self._base_id_retry_count = 0
        while self._pending_ute_packets:
            ute_packet = self._pending_ute_packets.popleft()
            response_packet = ute_packet.create_response_packet(self._base_id)
            self.logger.info("Sending deferred response to UTE teach-in.")
            self.send(response_packet)

    def _response_timed_out(self, packet):
        """Release a timed-out Base-ID latch before the transport degrades."""
        if packet is self._base_id_request_packet:
            self._reset_base_id_request()

    def stop(self):
        self._stop_flag.set()
        self._reset_base_id_request()
        self._base_id_retry_count = 0
        # A stopped worker owns no teach-in session any more.
        self._pending_ute_packets.clear()

    def parse(self):
        """Parses messages and puts them to receive queue"""
        # Loop while we get new messages
        while True:
            status, self._buffer, packet = Packet.parse_msg(self._buffer)
            # If message is incomplete -> break the loop
            if status == PARSE_RESULT.INCOMPLETE:
                return status

            # If message is OK, add it to receive queue or send to the callback method
            if status == PARSE_RESULT.OK and packet:
                packet.received = datetime.now(UTC)

                if isinstance(packet, ResponsePacket):
                    self._response_received(packet)

                # Outside an explicitly opened teach-in session the telegram is
                # still parsed and dispatched, but never answered: passive
                # observation costs no radio transmission.
                if isinstance(packet, UTETeachInPacket) and self.teach_in:
                    if self._base_id is None:
                        self.logger.warning(
                            "Cannot respond to UTE teach-in before Base ID is known."
                        )
                        self._pending_ute_packets.append(packet)
                        self.request_base_id()
                    else:
                        response_packet = packet.create_response_packet(self._base_id)
                        self.logger.info("Sending response to UTE teach-in.")
                        self.send(response_packet)

                if self.__callback is None:
                    self.receive.put(packet)
                else:
                    self.__callback(packet)
                self.logger.debug("Received EnOcean packet type=%s", packet.packet_type)

    @property
    def base_id(self):
        """Fetches Base ID from the transmitter, if required. Otherwise returns the currently set Base ID."""
        # If base id is already set, return it.
        if self._base_id is not None:
            return self._base_id

        # Send COMMON_COMMAND 0x08, CO_RD_IDBASE request to the module
        self.request_base_id()
        # Loop over 10 times, to make sure we catch the response.
        # Thanks to timeout, shouldn't take more than a second.
        # Unfortunately, all other messages received during this time are ignored.
        for _ in range(10):
            try:
                packet = self.receive.get(block=True, timeout=0.1)
                # We're only interested in responses to the request in question.
                if (
                    isinstance(packet, ResponsePacket)
                    and packet.response == RETURN_CODE.OK
                    and len(packet.response_data) == 4
                ):
                    # Base ID is set in the response data.
                    self._base_id = packet.response_data
                    # Put packet back to the Queue, so the user can also react to it if required...
                    self.receive.put(packet)
                    break
                # Put other packets back to the Queue.
                self.receive.put(packet)
            except queue.Empty:
                continue
        # Return the current Base ID (might be None).
        return self._base_id

    @base_id.setter
    def base_id(self, base_id):
        """Sets the Base ID manually, only for testing purposes."""
        self._base_id = base_id

"""ESP3 framing diagnostics derived from the vendored library's own log records.

The vendored parser and serial worker report every framing event through
their loggers and nowhere else. This module turns those records into counters
for the config entry diagnostics without modifying the library:

* a serial read that ends inside the six-byte ESP3 header is logged by the
  parser as ``Message incomplete, buffered_bytes=N`` at WARNING. It is ordinary
  read fragmentation: the bytes stay buffered and the next read completes the
  frame. It is counted, kept available as a DEBUG line, and no longer reaches
  the warning log;
* real faults -- header or data CRC mismatch, an impossible ERP1 length, a
  frame whose bytes stop arriving -- are counted and left untouched, at the
  level the library chose. A fragment that never completes still surfaces
  through the worker's own ``Incomplete ESP3 frame timed out`` error.

Matching is done on the exact log templates, so a library update that rewords
one simply stops being counted (a test pins every template to the vendored
sources). The filter only ever inspects records of the two library loggers.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from .enocean_library.communicators.serialcommunicator import SerialCommunicator
from .enocean_library.protocol.packet import Packet

_LOGGER = logging.getLogger(__name__)

PARTIAL_HEADER_READS: Final = "partial_header_reads"

# Exact vendored log template -> diagnostic counter.
WATCHED_TEMPLATES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "Message incomplete, buffered_bytes=%s": PARTIAL_HEADER_READS,
        "Header CRC error!": "header_crc_errors",
        "Data CRC error!": "data_crc_errors",
        "Invalid ESP3 RADIO_ERP1 frame length!": "invalid_frame_lengths",
        "Incomplete ESP3 frame timed out; discarding one byte": "frame_timeouts",
    }
)


def _library_loggers() -> tuple[logging.Logger, ...]:
    """Return the loggers of the vendored parser and serial worker."""
    return (Packet.logger, SerialCommunicator.logger)


class Esp3FramingMonitor(logging.Filter):
    """Count ESP3 framing events; demote the one that is not a fault.

    ``filter`` runs on the serial worker thread, inside the library's logging
    call. It never raises, never blocks and never touches the serial link.
    """

    def __init__(self) -> None:
        """Start every counter at zero."""
        super().__init__()
        self._counts = dict.fromkeys(WATCHED_TEMPLATES.values(), 0)

    def filter(self, record: logging.LogRecord) -> bool:
        """Count a watched record; drop only the partial-header notice."""
        template = record.msg
        counter = WATCHED_TEMPLATES.get(template) if type(template) is str else None
        if counter is None:
            return True
        self._counts[counter] += 1
        if counter != PARTIAL_HEADER_READS:
            return True
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "Serial read ended inside an ESP3 header (%s byte(s) buffered); "
                "waiting for the rest of the frame",
                record.args[0] if record.args else "?",
            )
        return False

    def attach(self) -> None:
        """Install the filter on the library loggers (idempotent)."""
        for logger in _library_loggers():
            if self not in logger.filters:
                logger.addFilter(self)

    def detach(self) -> None:
        """Remove the filter from the library loggers (idempotent)."""
        for logger in _library_loggers():
            logger.removeFilter(self)

    def snapshot(self) -> dict[str, int]:
        """Return the counters since the dongle was created; no identifier."""
        return dict(self._counts)

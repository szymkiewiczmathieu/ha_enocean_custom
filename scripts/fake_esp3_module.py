#!/usr/bin/env python3
"""Small ESP3 module emulator for hardware-free integration tests.

Linux/macOS pseudo-terminal usage::

    python3 scripts/fake_esp3_module.py --link /tmp/enocean-fake

The command prints the slave path. Type an injection command followed by Enter,
for example ``rps 10 058F2233 30`` or ``d2-05 60 058F2233``. Use ``--listen
0`` to expose the same emulator over an ephemeral TCP port (the printed socket
URL can be used as the integration's serial path). Only loopback listening is
allowed.

Protocol reference: EnOcean Serial Protocol 3 v1.58, sections 1.7 (frame
layout), 2.1 (RADIO_ERP1), 2.2 (RESPONSE), 2.5.5 (CO_RD_VERSION), 2.5.10
(CO_RD_IDBASE), and 2.5.11/2.5.12 (repeater write/read). This implementation
was written for this repository's test harness and has no runtime dependencies.
"""

from __future__ import annotations

import argparse
import os
import pty
import select
import socket
import sys
import tty
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Final

SYNC: Final = 0x55
RADIO_ERP1: Final = 0x01
RESPONSE: Final = 0x02
COMMON_COMMAND: Final = 0x05
CO_RD_VERSION: Final = 0x03
CO_WR_REPEATER: Final = 0x09
CO_RD_REPEATER: Final = 0x0A
CO_RD_IDBASE: Final = 0x08
RET_OK: Final = 0x00
RET_NOT_SUPPORTED: Final = 0x02
RORG_RPS: Final = 0xF6
RORG_1BS: Final = 0xD5
RORG_4BS: Final = 0xA5
RORG_VLD: Final = 0xD2
BROADCAST: Final = b"\xff\xff\xff\xff"

# CO_RD_VERSION response data (ESP3 2.5.5): app/API/chip ID/chip version/text.
VERSION_DATA: Final = (
    bytes((2, 15, 0, 0, 2, 6, 3, 0))
    + bytes.fromhex("0F0E0D0C")
    + bytes((2, 15, 0, 0))
    + b"FAKE ESP3".ljust(16, b"\x00")
)


def crc8(data: bytes | bytearray) -> int:
    """Return the ESP3 CRC-8 (polynomial 0x07, initial value zero)."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def encode_frame(packet_type: int, data: bytes, optional: bytes = b"") -> bytes:
    """Encode one complete ESP3 frame, including both checksums."""
    header = bytes(
        (
            (len(data) >> 8) & 0xFF,
            len(data) & 0xFF,
            len(optional),
            packet_type,
        )
    )
    body = data + optional
    return (
        bytes((SYNC,)) + header + bytes((crc8(header),)) + body + bytes((crc8(body),))
    )


@dataclass(frozen=True, slots=True)
class ESP3Frame:
    """One parsed ESP3 frame."""

    packet_type: int
    data: bytes
    optional: bytes


class ESP3StreamParser:
    """Incrementally split a serial byte stream into CRC-valid ESP3 frames."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[ESP3Frame]:
        """Consume bytes, resynchronizing after malformed frames."""
        self._buffer.extend(chunk)
        frames: list[ESP3Frame] = []
        while True:
            start = self._buffer.find(SYNC)
            if start < 0:
                self._buffer.clear()
                return frames
            if start:
                del self._buffer[:start]
            if len(self._buffer) < 6:
                return frames
            header = bytes(self._buffer[1:5])
            if crc8(header) != self._buffer[5]:
                del self._buffer[0]
                continue
            data_length = int.from_bytes(header[:2], "big")
            optional_length = header[2]
            total = 7 + data_length + optional_length
            if len(self._buffer) < total:
                return frames
            body = bytes(self._buffer[6 : total - 1])
            if crc8(body) != self._buffer[total - 1]:
                del self._buffer[0]
                continue
            frames.append(
                ESP3Frame(
                    header[3],
                    body[:data_length],
                    body[data_length:],
                )
            )
            del self._buffer[:total]


@dataclass(frozen=True, slots=True)
class CapturedRadio:
    """An outbound ERP1 telegram captured from the real communicator."""

    rorg: int
    payload: tuple[int, ...]
    sender: tuple[int, ...]
    destination: tuple[int, ...]
    status: int


class ESP3EmulatorCore:
    """Protocol state shared by the PTY and TCP transports."""

    def __init__(self, base_id: bytes = bytes.fromhex("FF87CA00")) -> None:
        if len(base_id) != 4:
            raise ValueError("base_id must contain exactly four bytes")
        self.base_id = base_id
        self.repeater_enable = 0
        self.repeater_level = 1
        self.sent_radio: list[CapturedRadio] = []

    def handle(self, frame: ESP3Frame) -> bytes | None:
        """Answer one request exactly as the module interface requires."""
        if frame.packet_type == COMMON_COMMAND and frame.data:
            return self._common_command(frame.data)
        if frame.packet_type == RADIO_ERP1:
            return self._radio_from_host(frame)
        return encode_frame(RESPONSE, bytes((RET_NOT_SUPPORTED,)))

    def _common_command(self, data: bytes) -> bytes:
        command = data[0]
        if command == CO_RD_VERSION:
            return encode_frame(RESPONSE, bytes((RET_OK,)) + VERSION_DATA)
        if command == CO_RD_IDBASE:
            # One optional byte reports the finite number of Base-ID writes left.
            return encode_frame(
                RESPONSE,
                bytes((RET_OK,)) + self.base_id,
                bytes((10,)),
            )
        if command == CO_WR_REPEATER and len(data) >= 3:
            self.repeater_enable = data[1]
            self.repeater_level = data[2] if data[1] else 1
            return encode_frame(RESPONSE, bytes((RET_OK,)))
        if command == CO_RD_REPEATER:
            return encode_frame(
                RESPONSE,
                bytes((RET_OK, self.repeater_enable, self.repeater_level)),
            )
        return encode_frame(RESPONSE, bytes((RET_NOT_SUPPORTED,)))

    def _radio_from_host(self, frame: ESP3Frame) -> bytes:
        if len(frame.data) >= 6 and len(frame.optional) >= 7:
            self.sent_radio.append(
                CapturedRadio(
                    rorg=frame.data[0],
                    payload=tuple(frame.data[1:-5]),
                    sender=tuple(frame.data[-5:-1]),
                    destination=tuple(frame.optional[1:5]),
                    status=frame.data[-1],
                )
            )
        return encode_frame(RESPONSE, bytes((RET_OK,)))


def radio_frame(
    rorg: int,
    payload: bytes | list[int] | tuple[int, ...],
    sender: bytes | list[int] | tuple[int, ...],
    *,
    status: int = 0,
    destination: bytes = BROADCAST,
    dbm: int = 0x40,
) -> bytes:
    """Build an inbound ERP1 frame (sub-telegram 1, seven optional bytes)."""
    data = bytes((rorg,)) + bytes(payload) + bytes(sender) + bytes((status,))
    optional = bytes((1,)) + destination + bytes((dbm, 0))
    return encode_frame(RADIO_ERP1, data, optional)


def d2_01_status_frame(
    sender: bytes | tuple[int, ...], channel: int, output: int
) -> bytes:
    """Build a D2-01 CMD 0x4 status response (EEP 2.6.7 pp. 135-136)."""
    return radio_frame(RORG_VLD, (0x04, channel & 0x1F, output), sender)


def a5_20_04_status_frame(
    sender: bytes | tuple[int, ...],
    position: int,
    temperature_raw: int = 128,
    target_raw: int = 128,
    failure: int | None = None,
) -> bytes:
    """Build an A5-20-04 direction-1 valve status telegram."""
    if failure is None:
        # LRN plus current-temperature and target-temperature valid flags.
        db0 = 0x0E
        db1 = temperature_raw
    else:
        # Failure flag set; DB1 carries the failure code, not a temperature.
        db0 = 0x0F
        db1 = failure
    return radio_frame(RORG_4BS, (position, target_raw, db1, db0), sender)


def d2_05_status_frame(
    sender: bytes | tuple[int, ...],
    position: int,
    channel: int = 0,
    angle: int = 0x7F,
    locking: int = 0,
) -> bytes:
    """Build a D2-05-00 CMD 0x4 reply (EEP 2.6.7 pp. 158-159)."""
    payload = (
        position & 0x7F,
        angle & 0x7F,
        locking & 0x07,
        ((channel & 0x0F) << 4) | 0x04,
    )
    return radio_frame(RORG_VLD, payload, sender)


class InjectionMixin:
    """Named RPS/4BS/VLD injections used by tests and by the CLI."""

    core: ESP3EmulatorCore

    def _write(self, frame: bytes) -> None:
        raise NotImplementedError

    def inject_radio(
        self,
        rorg: int,
        payload: bytes | list[int] | tuple[int, ...],
        sender: bytes | list[int] | tuple[int, ...],
        *,
        status: int = 0,
    ) -> None:
        """Inject one arbitrary RPS/1BS/4BS/VLD radio telegram."""
        self._write(radio_frame(rorg, payload, sender, status=status))

    def inject_rps(
        self,
        value: int,
        sender: bytes | list[int] | tuple[int, ...],
        *,
        status: int = 0x30,
    ) -> None:
        self.inject_radio(RORG_RPS, (value,), sender, status=status)

    def inject_1bs(
        self,
        value: int,
        sender: bytes | list[int] | tuple[int, ...],
        *,
        status: int = 0,
    ) -> None:
        self.inject_radio(RORG_1BS, (value,), sender, status=status)

    def inject_4bs(
        self,
        payload: bytes | list[int] | tuple[int, ...],
        sender: bytes | list[int] | tuple[int, ...],
        *,
        status: int = 0,
    ) -> None:
        if len(payload) != 4:
            raise ValueError("a 4BS payload has exactly four data bytes")
        self.inject_radio(RORG_4BS, payload, sender, status=status)

    def inject_vld(
        self,
        payload: bytes | list[int] | tuple[int, ...],
        sender: bytes | list[int] | tuple[int, ...],
        *,
        status: int = 0,
    ) -> None:
        self.inject_radio(RORG_VLD, payload, sender, status=status)

    def inject_d2_01_status(
        self,
        sender: bytes | list[int] | tuple[int, ...],
        channel: int,
        output: int,
    ) -> None:
        self._write(d2_01_status_frame(sender, channel, output))

    def inject_a5_20_04(
        self,
        sender: bytes | list[int] | tuple[int, ...],
        position: int,
        temperature_raw: int = 128,
        target_raw: int = 128,
        failure: int | None = None,
    ) -> None:
        self._write(
            a5_20_04_status_frame(
                sender, position, temperature_raw, target_raw, failure
            )
        )

    def inject_d2_05_00_status(
        self,
        sender: bytes | list[int] | tuple[int, ...],
        position: int,
        channel: int = 0,
        angle: int = 0x7F,
        locking: int = 0,
    ) -> None:
        self._write(d2_05_status_frame(sender, position, channel, angle, locking))


class FakeESP3Module(InjectionMixin):
    """Run the emulator on a raw pseudo-terminal."""

    def __init__(
        self, base_id: bytes | tuple[int, ...] = bytes.fromhex("FF87CA00")
    ) -> None:
        self.core = ESP3EmulatorCore(bytes(base_id))
        self._master, self._slave = pty.openpty()
        tty.setraw(self._slave)
        self.port = os.ttyname(self._slave)
        self._parser = ESP3StreamParser()
        self._stop = Event()
        self._write_lock = Lock()
        self._thread = Thread(target=self._run, name="fake-esp3-pty", daemon=True)
        self._thread.start()

    @property
    def sent_radio(self) -> list[CapturedRadio]:
        return self.core.sent_radio

    def _write(self, frame: bytes) -> None:
        with self._write_lock:
            if not self._stop.is_set():
                os.write(self._master, frame)

    def _run(self) -> None:
        while not self._stop.is_set():
            ready, _, _ = select.select([self._master], [], [], 0.05)
            if not ready:
                continue
            try:
                chunk = os.read(self._master, 4096)
            except OSError:
                return
            for frame in self._parser.feed(chunk):
                if (response := self.core.handle(frame)) is not None:
                    self._write(response)

    def close(self) -> None:
        """Stop the reader and close both PTY descriptors, idempotently."""
        if self._stop.is_set():
            return
        self._stop.set()
        self._thread.join(1)
        for descriptor in (self._master, self._slave):
            try:
                os.close(descriptor)
            except OSError:
                pass


class FakeESP3TCPServer(InjectionMixin):
    """Expose one emulated module on a loopback socket for reconnect tests."""

    def __init__(
        self,
        base_id: bytes | tuple[int, ...] = bytes.fromhex("FF87CA00"),
        port: int = 0,
    ) -> None:
        self.core = ESP3EmulatorCore(bytes(base_id))
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", port))
        self._listener.listen(2)
        self._listener.settimeout(0.05)
        self.host, self.port = self._listener.getsockname()
        self.url = f"socket://{self.host}:{self.port}"
        self.connection_count = 0
        self._client: socket.socket | None = None
        self._client_lock = Lock()
        self._stop = Event()
        self._thread = Thread(target=self._run, name="fake-esp3-tcp", daemon=True)
        self._thread.start()

    @property
    def sent_radio(self) -> list[CapturedRadio]:
        return self.core.sent_radio

    def _write(self, frame: bytes) -> None:
        with self._client_lock:
            client = self._client
            if client is not None:
                try:
                    client.sendall(frame)
                except OSError:
                    pass

    def drop_client(self) -> None:
        """Simulate a remote dongle disappearing without stopping its listener."""
        with self._client_lock:
            client, self._client = self._client, None
        if client is not None:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                client, _address = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            client.settimeout(0.05)
            with self._client_lock:
                self._client = client
            self.connection_count += 1
            parser = ESP3StreamParser()
            try:
                while not self._stop.is_set():
                    try:
                        chunk = client.recv(4096)
                    except TimeoutError:
                        continue
                    if not chunk:
                        break
                    for frame in parser.feed(chunk):
                        if (response := self.core.handle(frame)) is not None:
                            client.sendall(response)
            except OSError:
                pass
            finally:
                with self._client_lock:
                    if self._client is client:
                        self._client = None
                client.close()

    def close(self) -> None:
        """Stop accepting clients and close the listener and active connection."""
        if self._stop.is_set():
            return
        self._stop.set()
        self.drop_client()
        self._listener.close()
        self._thread.join(1)


def _id(text: str) -> bytes:
    cleaned = text.replace(":", "").replace("-", "")
    if len(cleaned) != 8:
        raise ValueError("an EnOcean ID needs exactly four hexadecimal bytes")
    return bytes.fromhex(cleaned)


def _inject_command(module: InjectionMixin, line: str) -> None:
    fields = line.split()
    if not fields:
        return
    command = fields[0].lower()
    if command == "rps" and len(fields) in (3, 4):
        module.inject_rps(
            int(fields[1], 16),
            _id(fields[2]),
            status=int(fields[3], 16) if len(fields) == 4 else 0x30,
        )
    elif command == "1bs" and len(fields) == 3:
        module.inject_1bs(int(fields[1], 16), _id(fields[2]))
    elif command == "4bs" and len(fields) == 3:
        module.inject_4bs(bytes.fromhex(fields[1]), _id(fields[2]))
    elif command == "vld" and len(fields) == 3:
        module.inject_vld(bytes.fromhex(fields[1]), _id(fields[2]))
    elif command == "d2-01" and len(fields) == 4:
        module.inject_d2_01_status(_id(fields[3]), int(fields[1], 0), int(fields[2], 0))
    elif command == "a5-20-04" and len(fields) == 3:
        module.inject_a5_20_04(_id(fields[2]), int(fields[1], 0))
    elif command == "d2-05" and len(fields) == 3:
        module.inject_d2_05_00_status(_id(fields[2]), int(fields[1], 0))
    else:
        raise ValueError(
            "use: rps BB ID [SS], 1bs BB ID, 4bs BBBBBBBB ID, vld HEX ID, "
            "d2-01 CHANNEL OUTPUT ID, a5-20-04 POSITION ID, d2-05 POSITION ID"
        )


def main() -> int:
    """Run the interactive emulator."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-id", default="FF87CA00")
    parser.add_argument("--link", help="optional symbolic link to the PTY")
    parser.add_argument(
        "--listen",
        metavar="PORT",
        type=int,
        help="serve loopback TCP on this port instead of a PTY (0 = ephemeral)",
    )
    args = parser.parse_args()
    module: FakeESP3Module | FakeESP3TCPServer
    if args.listen is None:
        module = FakeESP3Module(_id(args.base_id))
        print(f"Fake ESP3 module on {module.port}", flush=True)
        if args.link:
            link = Path(args.link)
            if link.is_symlink():
                link.unlink()
            link.symlink_to(module.port)
            print(f"Also available at {link}", flush=True)
    else:
        module = FakeESP3TCPServer(_id(args.base_id), args.listen)
        print(f"Fake ESP3 module on {module.url}", flush=True)
    print(
        "Injection commands: rps, 1bs, 4bs, vld, d2-01, a5-20-04, d2-05; quit",
        flush=True,
    )
    try:
        for line in sys.stdin:
            if line.strip().lower() in ("quit", "exit"):
                break
            try:
                _inject_command(module, line)
            except (ValueError, OSError) as err:
                print(err, file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        module.close()
        if args.link and Path(args.link).is_symlink():
            Path(args.link).unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

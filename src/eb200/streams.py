"""UDP stream receivers for EB200 datagrams (audio first)."""

from __future__ import annotations

import array
import socket
import threading

from . import binary
from .errors import EB200Protocol

# Sequence deltas larger than half the 16-bit space are treated as a
# receiver-side restart (e.g. after re-registering or changing the AF mode),
# not as packet loss.
RESTART_DELTA = 0x8000


class AudioReceiver:
    """Consumes EB200 UDP AUDIO datagrams and keeps continuity statistics.

    Sequence gaps are healed with silence so consumers keep a continuous
    sample stream; counters let callers monitor link quality. The receiver
    owns one socket; a daemon thread fills an internal buffer that is
    drained through :meth:`read`.
    """

    def __init__(
        self,
        bind_ip: str = "0.0.0.0",
        socket_buffer: int = 4 * 1024 * 1024,
        recv_timeout: float = 0.5,
    ) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, socket_buffer)
        self._sock.bind((bind_ip, 0))
        self._sock.settimeout(recv_timeout)
        self._lock = threading.Lock()
        self._chunks: list[array.array[int]] = []
        self._packets = 0
        self._lost = 0
        self._duplicates = 0
        self._resets = 0
        self._parse_errors = 0
        self._samples = 0
        self._dropped_buffered = 0
        self._last_seq: int | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._sock.getsockname()[1]

    def start(self) -> None:
        if self._thread is not None:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="eb200-audio", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._sock.close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def stats(self) -> dict[str, int]:
        with self._lock:
            buffered = sum(len(c) for c in self._chunks)
            return {
                "packets": self._packets,
                "lost": self._lost,
                "duplicates": self._duplicates,
                "resets": self._resets,
                "parse_errors": self._parse_errors,
                "buffered_samples": buffered,
                "samples": self._samples,
                "dropped_buffered": self._dropped_buffered,
            }

    def read(self, max_samples: int = 100_000) -> array.array[int]:
        """Move buffered samples into the caller, capped at max_samples."""
        out: array.array[int] = array.array("h")
        with self._lock:
            while self._chunks and len(out) < max_samples:
                chunk = self._chunks.pop(0)
                room = max_samples - len(out)
                if len(chunk) <= room:
                    out.extend(chunk)
                else:
                    out.extend(chunk[:room])
                    self._chunks.insert(0, chunk[room:])
            self._dropped_buffered = 0
        return out

    # -- internals -----------------------------------------------------------

    def _loop(self) -> None:
        while self._running:
            try:
                data = self._sock.recv(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                datagram = binary.parse_audio(data)
            except EB200Protocol:
                with self._lock:
                    self._parse_errors += 1
                continue
            self._account(datagram)

    def _account(self, datagram: binary.AudioDatagram) -> None:
        samples = array.array("h", datagram.samples)
        silence = 0
        with self._lock:
            self._packets += 1
            seq = datagram.header.sequence
            if self._last_seq is not None:
                delta = (seq - self._last_seq) & 0xFFFF
                if delta == 0:
                    self._duplicates += 1
                    self._packets -= 1  # a duplicate is counted once
                    return
                if delta > RESTART_DELTA:
                    # Receiver restarted its sequence counter (e.g. when the
                    # AF mode is applied): realign instead of counting loss.
                    self._resets += 1
                elif delta > 1:
                    missing = delta - 1
                    self._lost += missing
                    # Healed with silence: one chunk worth per lost packet.
                    silence = missing * len(samples)
                    self._chunks.append(array.array("h", bytes(2 * silence)))
            self._last_seq = seq
            self._chunks.append(samples)
            self._samples += silence + len(samples)

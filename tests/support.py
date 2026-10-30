"""Test doubles: fake instrument server, datagram builder, wait helper."""

from __future__ import annotations

import socket
import struct
import threading
import time

from eb200 import binary


def make_audio_datagram(
    sequence: int,
    samples: list[int],
    trace_type: int = 0x77,
    flags: int | None = None,
) -> bytes:
    """Build a wire-format AUDIO datagram exactly as captured from fw 2.80."""
    if flags is None:
        flags = binary.FLAG_AUDIO
    body = struct.pack(">HBBI", len(samples), trace_type, 0, flags)
    payload = struct.pack(f">{len(samples)}h", *samples)
    header = struct.pack(">IHHHHI", binary.UDP_MAGIC, 0x27, 2, sequence, 0, 0)
    ga = struct.pack(">HH", binary.GA_AUDIO, len(body) + len(payload))
    return header + ga + body + payload


class _Kill:
    """Response value: close the connection instead of replying."""


KILL = _Kill()


class Delayed:
    """Response value: reply after ``seconds``."""

    def __init__(self, seconds: float, data: bytes) -> None:
        self.seconds = seconds
        self.data = data


class FakeEB200Server:
    """Minimal TCP stand-in for the EB200 SCPI port.

    Response values in ``responses`` (keyed by whitespace-collapsed upper
    case command text): plain bytes, :data:`KILL`, or :class:`Delayed`.
    Commands starting with ``ECHO `` are always echoed back, which lets
    concurrency tests prove request/response pairing. A query without a
    mapping is never answered — exactly like the real receiver for unknown
    commands.
    """

    def __init__(self) -> None:
        self.responses: dict[str, bytes | Delayed | _Kill] = {}
        self.received: list[str] = []
        self.connection_count = 0
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self._running = False
        self._accept_thread: threading.Thread | None = None
        self._conns: set[socket.socket] = set()
        self._lock = threading.Lock()

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def port(self) -> int:
        return self._listener.getsockname()[1]

    def start(self) -> None:
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def stop(self) -> None:
        self._running = False
        try:
            self._listener.close()
        except OSError:
            pass
        self.kill_connections()
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=2.0)

    def kill_connections(self) -> None:
        with self._lock:
            conns = list(self._conns)
        for conn in conns:
            try:
                conn.close()
            except OSError:
                pass

    def add_responses(self, values: dict[str, bytes]) -> None:
        self.responses.update(values)

    def _accept_loop(self) -> None:
        # Non-blocking accept: a close() from another thread does not wake
        # up a thread blocked in accept() on Linux, which would keep the
        # listening socket alive and accept stray reconnects.
        self._listener.setblocking(False)
        while self._running:
            try:
                conn, _addr = self._listener.accept()
            except BlockingIOError:
                time.sleep(0.02)
                continue
            except OSError:
                break
            with self._lock:
                self.connection_count += 1
                self._conns.add(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        buf = b""
        while True:
            try:
                data = conn.recv(4096)
            except OSError:
                break
            if not data:
                break
            buf += data
            while b"\n" in buf and buf:
                line, buf = buf.split(b"\n", 1)
                cmd = line.decode("ascii", "replace").strip()
                if not cmd:
                    continue
                with self._lock:
                    self.received.append(cmd)
                if not self._reply(conn, cmd):
                    with self._lock:
                        self._conns.discard(conn)
                    return
        with self._lock:
            self._conns.discard(conn)

    @staticmethod
    def _send(conn: socket.socket, data: bytes) -> bool:
        """Send bytes; report whether the socket is still usable."""
        try:
            conn.sendall(data)
        except OSError:
            return False
        return True

    def _reply(self, conn: socket.socket, cmd: str) -> bool:
        """Answer one command; return False when the socket is closed."""
        if cmd.startswith("ECHO "):
            return self._send(conn, (cmd + "\r\n").encode("ascii"))
        key = " ".join(cmd.split()).upper()
        with self._lock:
            value = self.responses.get(key)
        if value is None:
            if key == "SYST:ERR?":
                return self._send(conn, b'0,"No error"\r\n')
            return True
        if isinstance(value, Delayed):
            time.sleep(value.seconds)
            return self._send(conn, value.data)
        if value is KILL:
            try:
                conn.close()
            except OSError:
                pass
            return False
        return self._send(conn, value)


def wait_for(predicate, timeout: float = 2.0, poll: float = 0.02) -> bool:
    """Poll ``predicate`` until true or the deadline passes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()

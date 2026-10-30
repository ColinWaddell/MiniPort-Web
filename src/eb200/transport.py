"""Thread-safe TCP/SCPI transport for the EB200.

One worker thread owns the socket and processes a queue of requests, so
SCPI request/response ordering can never be interleaved by concurrent
callers. The receiver answers invalid commands with *nothing*, so
per-request timeouts are part of the API; a timed-out response may still be
in flight later, which is why the transport drops the link after a timeout
instead of re-syncing in-band (docs/protocol-notes.md).
"""

from __future__ import annotations

import queue
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType

from . import framing
from .errors import (
    EB200Connection,
    EB200Protocol,
    EB200Timeout,
    SCPIError,
    parse_syst_error,
)

DEFAULT_PORT = 5555


class _Result:
    """Single-shot handoff of one request outcome to its caller."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._value: bytes | None = None
        self._error: BaseException | None = None

    def set_value(self, value: bytes) -> None:
        self._value = value
        self._event.set()

    def set_error(self, error: BaseException) -> None:
        self._error = error
        self._event.set()

    def wait(self) -> bytes:
        self._event.wait()
        if self._error is not None:
            raise self._error
        return self._value or b""


@dataclass
class _Request:
    text: str
    expect_reply: bool
    result: _Result
    timeout: float


class TCPTransport:
    """Serialized SCPI request/response transport with reconnection."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        timeout: float = 3.0,
        connect_timeout: float = 5.0,
        reconnect_attempts: int = 5,
        reconnect_delay: float = 0.5,
        on_reconnect: Callable[[], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.on_reconnect = on_reconnect
        self.local_ip = ""
        self._timeout = timeout
        self._connect_timeout = connect_timeout
        self._reconnect_attempts = reconnect_attempts
        self._reconnect_delay = reconnect_delay
        self._queue: queue.SimpleQueue[_Request | object] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._had_socket = False
        self._running = False
        self._worker: threading.Thread | None = None
        self._stop_sentinel = object()

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._sock = self._open_socket()
            self._had_socket = True
            self._running = True
        self._worker = threading.Thread(
            target=self._work_loop, name="eb200-transport", daemon=True
        )
        self._worker.start()

    def close(self) -> None:
        self._running = False
        self._queue.put(self._stop_sentinel)
        with self._lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._worker is not None:
            self._worker.join(timeout=self._connect_timeout + 2.0)

    def __enter__(self) -> TCPTransport:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _open_socket(self) -> socket.socket:
        try:
            sock = socket.create_connection(
                (self.host, self.port), timeout=self._connect_timeout
            )
        except OSError as exc:
            raise EB200Connection(
                f"cannot connect to {self.host}:{self.port}: {exc}"
            ) from exc
        sock.settimeout(self._timeout)
        self.local_ip = sock.getsockname()[0]
        return sock

    def _close_socket(self) -> None:
        with self._lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    # -- public API ---------------------------------------------------------

    def query(self, cmd: str, timeout: float | None = None) -> bytes:
        """Send a query and return the exact response bytes."""
        result = _Result()
        self._queue.put(_Request(cmd, True, result, timeout or self._timeout))
        return result.wait()

    def command(self, cmd: str) -> None:
        """Send a set command (the receiver does not answer these)."""
        result = _Result()
        self._queue.put(_Request(cmd, False, result, self._timeout))
        result.wait()

    def query_errors(self, limit: int = 32) -> list[SCPIError]:
        """Drain SYST:ERR? until it reports 'No error'."""
        errors: list[SCPIError] = []
        for _ in range(limit):
            text = framing.decode_text(self.query("SYST:ERR?"))
            parsed = parse_syst_error(text)
            if parsed is None:
                break
            errors.append(SCPIError(code=parsed[0], message=parsed[1]))
        return errors

    @property
    def is_connected(self) -> bool:
        """Approximate link health: a socket object currently exists."""
        return self._sock is not None

    # -- worker -------------------------------------------------------------

    def _work_loop(self) -> None:
        while self._running:
            try:
                item = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if item is self._stop_sentinel:
                break
            try:
                self._handle(item)  # type: ignore[arg-type]
            except Exception as exc:  # noqa: BLE001 - settle the future
                item.result.set_error(EB200Protocol(f"internal error: {exc}"))  # type: ignore[union-attr]
        self._drain()

    def _drain(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if item is self._stop_sentinel:
                continue
            item.result.set_error(EB200Connection("transport closed"))  # type: ignore[union-attr]

    def _handle(self, req: _Request) -> None:
        last_attempts = self._reconnect_attempts
        for attempt in range(last_attempts + 1):
            if not self._running:
                req.result.set_error(EB200Connection("transport closed"))
                return
            try:
                sock = self._ensure_socket()
            except EB200Connection:
                if attempt == last_attempts:
                    req.result.set_error(
                        EB200Connection(
                            f"cannot (re)connect to {self.host}:{self.port}"
                        )
                    )
                    return
                time.sleep(self._reconnect_delay)
                continue
            try:
                sock.settimeout(req.timeout)
                sock.sendall((req.text + "\n").encode("ascii"))
                if not req.expect_reply:
                    req.result.set_value(b"")
                    return
                data = framing.read_response(sock, req.timeout)
                sock.settimeout(self._timeout)
                req.result.set_value(data)
                return
            except TimeoutError:
                self._close_socket()
                req.result.set_error(
                    EB200Timeout(f"no response to {req.text!r} within {req.timeout}s")
                )
                return
            except (OSError, EB200Protocol) as exc:
                # Connection dropped (or produced framing garbage that
                # suggests a broken stream): drop the link and retry with
                # a fresh one.
                self._close_socket()
                if attempt == last_attempts:
                    req.result.set_error(EB200Connection(f"connection lost: {exc}"))
                    return
                time.sleep(self._reconnect_delay)
        req.result.set_error(EB200Connection("connection lost, retries exhausted"))

    def _ensure_socket(self) -> socket.socket:
        with self._lock:
            if self._sock is None:
                self._sock = self._open_socket()
                was_away = self._had_socket
                self._had_socket = True
                reopened = was_away
            else:
                return self._sock
        if reopened and self.on_reconnect is not None:
            try:
                self.on_reconnect()
            except Exception:  # noqa: BLE001 - callbacks must not kill the worker
                pass
        return self._sock

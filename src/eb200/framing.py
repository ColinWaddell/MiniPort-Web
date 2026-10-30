"""SCPI response framing for the EB200 TCP link.

Observed behaviour (docs/protocol-notes.md): responses end with CRLF,
multi-line responses separate their lines with a bare LF, binary data
arrives as '#'-prefixed definite-length blocks, and there is no response at
all for invalid commands — so every read must tolerate timeouts.
"""

from __future__ import annotations

import socket
import time

from .errors import EB200Protocol

LINE_TERMINATOR = b"\r\n"


def decode_text(data: bytes) -> str:
    """Decode a raw response as text, dropping the line terminator."""
    return data.rstrip(b"\r\n").decode("ascii", "replace")


def iter_lines(data: bytes) -> list[str]:
    """Split a multi-line response (CRLF-terminated, LF-separated)."""
    return decode_text(data).splitlines()


def parse_block(data: bytes) -> bytes:
    """Extract the payload of a complete definite-length block.

    Accepts ``#<d><digits><payload>['\\r\\n']`` and indefinite blocks;
    raises EB200Protocol on any deviation.
    """
    if len(data) < 2 or not data.startswith(b"#"):
        raise EB200Protocol("not a definite-length block")
    if not (48 <= data[1] <= 57):
        raise EB200Protocol(f"bad block header: {data[:2]!r}")
    count_digits = int(chr(data[1]))
    if count_digits == 0:
        end = data.find(b"\n")
        if end == -1:
            raise EB200Protocol("unterminated indefinite-length block")
        return bytes(data[2:end])
    header_len = 2 + count_digits
    if len(data) < header_len:
        raise EB200Protocol("truncated block header")
    length = int(data[2:header_len])
    end = header_len + length
    if len(data) < end:
        raise EB200Protocol("truncated definite-length block")
    return bytes(data[header_len:end])


def read_response(
    sock: socket.socket, timeout: float, idle_timeout: float = 0.5
) -> bytes:
    """Read one response, auto-detecting line vs block framing.

    Lines keep their terminator (raw fidelity); blocks return their payload
    without the '#' header. A partial line after the idle timeout is
    returned as-is; a partial block raises TimeoutError (data integrity).
    """
    sock.settimeout(timeout)
    first = sock.recv(4096)
    if not first:
        raise EB200Protocol("connection closed before a response arrived")
    if first.startswith(b"#"):
        return _read_block(sock, first, idle_timeout)
    return _read_line(sock, first)


def _more(sock: socket.socket, buf: bytearray) -> None:
    chunk = sock.recv(4096)
    if not chunk:
        raise EB200Protocol("connection closed mid-response")
    buf += chunk


def _read_line(sock: socket.socket, first: bytes) -> bytes:
    buf = bytearray(first)
    while not buf.endswith(LINE_TERMINATOR):
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            return bytes(buf)
        if not chunk:
            raise EB200Protocol("connection closed mid-response")
        buf += chunk
    return bytes(buf)


def _read_block(sock: socket.socket, first: bytes, idle_timeout: float) -> bytes:
    buf = bytearray(first)
    if len(buf) < 2:
        _more(sock, buf)
    if not (48 <= buf[1] <= 57):
        raise EB200Protocol(f"bad block header: {bytes(buf[:2])!r}")
    count_digits = int(chr(buf[1]))
    if count_digits == 0:
        while not buf.endswith(b"\n"):
            _more(sock, buf)
        return bytes(buf[2:-1])
    header_len = 2 + count_digits
    while len(buf) < header_len:
        _more(sock, buf)
    length = int(buf[2:header_len])
    end = header_len + length
    while len(buf) < end:
        _more(sock, buf)
    payload = bytes(buf[header_len:end])
    sock.settimeout(idle_timeout)
    tail = bytes(buf[end:])
    try:
        tail += sock.recv(4096)
    except TimeoutError:
        pass
    if tail.endswith(LINE_TERMINATOR):
        tail = tail[:-2]
    if tail:
        raise EB200Protocol(f"unexpected trailing data after block: {tail!r}")
    return payload


def idle_deadline(seconds: float) -> float:
    """Convenience for callers that use :func:`time.monotonic` deadlines."""
    return time.monotonic() + seconds

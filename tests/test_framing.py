"""Framing tests driven through real socketpairs."""

import socket

import pytest

from eb200 import framing
from eb200.errors import EB200Protocol


def _pair() -> tuple[socket.socket, socket.socket]:
    """(device-side socket, reader socket)."""
    left, right = socket.socketpair()
    return left, right


def test_decode_text_strips_terminator():
    assert framing.decode_text(b"-18.6\r\n") == "-18.6"


def test_iter_lines_splits_multiline():
    assert framing.iter_lines(b"DEF\n001\n002\n003\r\n") == [
        "DEF",
        "001",
        "002",
        "003",
    ]


def test_read_line():
    sender, reader = _pair()
    sender.sendall(b"-18.6\r\n")
    assert framing.read_response(reader, timeout=1.0) == b"-18.6\r\n"


def test_read_line_chunked_bytes():
    sender, reader = _pair()
    for byte in b"DEF\n001\n002\n003\r\n":
        sender.sendall(bytes([byte]))
    assert framing.read_response(reader, timeout=1.0) == b"DEF\n001\n002\n003\r\n"


def test_read_line_partial_after_idle():
    sender, reader = _pair()
    sender.sendall(b"PART")
    assert framing.read_response(reader, timeout=0.05, idle_timeout=0.05) == b"PART"


def test_read_line_timeout_when_silent():
    _sender, reader = _pair()
    with pytest.raises(TimeoutError):
        framing.read_response(reader, timeout=0.05)


def test_read_line_closed_connection_raises_protocol():
    sender, reader = _pair()
    sender.close()
    with pytest.raises(EB200Protocol):
        framing.read_response(reader, timeout=1.0)


def test_read_definite_block():
    sender, reader = _pair()
    sender.sendall(b"#202AB\r\n")
    assert framing.read_response(reader, timeout=1.0) == b"AB"


def test_read_block_without_terminator_is_tolerated():
    sender, reader = _pair()
    sender.sendall(b"#202AB")
    assert framing.read_response(reader, timeout=1.0) == b"AB"


def test_read_block_extra_trailing_data_raises():
    sender, reader = _pair()
    sender.sendall(b"#2002AB\r\nXX")
    with pytest.raises(EB200Protocol):
        framing.read_response(reader, timeout=1.0)


def test_read_indefinite_block():
    sender, reader = _pair()
    sender.sendall(b"#0AB\n")
    assert framing.read_response(reader, timeout=1.0) == b"AB"


def test_truncated_block_times_out():
    sender, reader = _pair()
    sender.sendall(b"#29999AB")
    with pytest.raises(TimeoutError):
        framing.read_response(reader, timeout=0.05)


def test_parse_block_from_complete_bytes():
    assert framing.parse_block(b"#202AB\r\n") == b"AB"
    assert framing.parse_block(b"#0AB\n") == b"AB"


def test_parse_block_rejects_garbage():
    with pytest.raises(EB200Protocol):
        framing.parse_block(b"-18.6\r\n")
    with pytest.raises(EB200Protocol):
        framing.parse_block(b"#2x")

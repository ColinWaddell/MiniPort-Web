"""Binary decoder tests against datagrams built to the captured layout."""

import struct

import pytest
from support import make_audio_datagram

from eb200 import binary
from eb200.errors import EB200Protocol


def test_parse_audio_datagram_fields():
    data = make_audio_datagram(164, [10, -20, 30])
    datagram = binary.parse_audio(data)
    assert datagram.header.magic == binary.UDP_MAGIC
    assert datagram.header.version_minor == 0x27
    assert datagram.header.version_major == 2
    assert datagram.header.sequence == 164
    assert datagram.header.reserved == 0
    assert datagram.generic_tag == binary.GA_AUDIO
    assert datagram.samples == (10, -20, 30)
    assert datagram.value_count == 3
    assert datagram.selector_flags == binary.FLAG_AUDIO


def test_parse_rejects_bad_magic():
    with pytest.raises(EB200Protocol):
        binary.parse_audio(b"\x00" * 64)


def test_parse_rejects_non_audio_tag():
    data = bytearray(make_audio_datagram(1, [1, 2]))
    struct.pack_into(">H", data, 16, binary.GA_IF)  # IF tag in an audio parse
    with pytest.raises(EB200Protocol):
        binary.parse_audio(bytes(data))


def test_parse_rejects_truncated_payload():
    data = make_audio_datagram(1, [1, 2, 3])[:-3]
    with pytest.raises(EB200Protocol):
        binary.parse_audio(data)


def test_parse_rejects_short_datagram():
    with pytest.raises(EB200Protocol):
        binary.parse_audio(b"\x00\x0e\xb2\x00")


def test_sequence_losses():
    assert binary.sequence_losses([1, 2, 3]) == 0
    assert binary.sequence_losses([1, 2, 5]) == 2
    assert binary.sequence_losses([65534, 1]) == 2
    assert binary.sequence_losses([7]) == 0


def test_parse_packed_level():
    assert binary.parse_packed_level(b"\xffP") == pytest.approx(-17.6)
    assert binary.parse_packed_level(b"\x00\xc8") == pytest.approx(20.0)
    with pytest.raises(EB200Protocol):
        binary.parse_packed_level(b"\xff")

"""Decoders for EB200 packed responses and UDP datagrams."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .errors import EB200Protocol

UDP_MAGIC = 0x000EB200

GA_AUDIO = 401  # 0x0191, observed on every audio datagram
GA_IF = 901  # 0x0385, IF trace class per runds_connector (not offered on fw 2.80)

FLAG_AUDIO = 0x00040000  # selectorFlags observed on audio datagrams
FLAG_IF_MASK = 0xA0000000  # allowed selectorFlags mask per runds_connector
FLAG_IF_OPTIONAL_HEADER = 0x80000000

# The receiver's own datagram fields; trace_type byte 0x77 observed on this
# unit's audio (semantics unknown, likely a trace-class marker).
HEADER_SIZE = 16
GENERIC_ATTRIBUTE_SIZE = 4
AUDIO_ATTRIBUTE_SIZE = 8


@dataclass(frozen=True)
class DatagramHeader:
    magic: int
    version_minor: int
    version_major: int
    sequence: int
    reserved: int
    declared_size: int


@dataclass(frozen=True)
class AudioDatagram:
    header: DatagramHeader
    generic_tag: int
    generic_length: int
    value_count: int
    trace_type: int
    optional_header_length: int
    selector_flags: int
    samples: tuple[int, ...]


_HEADER = struct.Struct(">IHHHHI")
_GENERIC_ATTRIBUTE = struct.Struct(">HH")
_AUDIO_ATTRIBUTE = struct.Struct(">HBBI")


def parse_datagram_header(data: bytes) -> DatagramHeader:
    """Decode the 16-byte UDP datagram header, validating the magic."""
    if len(data) < HEADER_SIZE:
        raise EB200Protocol(f"datagram shorter than header ({len(data)}B)")
    magic, vmin, vmaj, seq, res, size = _HEADER.unpack_from(data)
    if magic != UDP_MAGIC:
        raise EB200Protocol(f"bad datagram magic 0x{magic:08X}")
    return DatagramHeader(magic, vmin, vmaj, seq, res, size)


def parse_audio(data: bytes) -> AudioDatagram:
    """Decode an AUDIO-class UDP datagram (GenericAttribute tag 401).

    Layout verified live on fw 2.80: header(16) + GA(4) + audio attribute
    (8) + optional header (opt_len bytes) + big-endian signed 16-bit
    samples.
    """
    header = parse_datagram_header(data)
    minimum = HEADER_SIZE + GENERIC_ATTRIBUTE_SIZE + AUDIO_ATTRIBUTE_SIZE
    if len(data) < minimum:
        raise EB200Protocol(f"audio datagram too short ({len(data)}B)")
    tag, ga_len = _GENERIC_ATTRIBUTE.unpack_from(data, HEADER_SIZE)
    if tag != GA_AUDIO:
        raise EB200Protocol(f"unexpected generic attribute tag {tag}")
    base = HEADER_SIZE + GENERIC_ATTRIBUTE_SIZE
    values, trace_type, opt_len, flags = _AUDIO_ATTRIBUTE.unpack_from(data, base)
    start = base + AUDIO_ATTRIBUTE_SIZE + opt_len
    available = len(data) - start
    if available < values * 2:
        raise EB200Protocol(
            f"truncated audio payload: {values} samples declared, "
            f"{available // 2} present"
        )
    samples = struct.unpack(f">{values}h", data[start : start + values * 2])
    return AudioDatagram(
        header=header,
        generic_tag=tag,
        generic_length=ga_len,
        value_count=values,
        trace_type=trace_type,
        optional_header_length=opt_len,
        selector_flags=flags,
        samples=samples,
    )


def sequence_losses(sequences: list[int]) -> int:
    """Count missing packets in a sequence list, wrap-aware (16-bit)."""
    losses = 0
    for earlier, later in zip(sequences, sequences[1:], strict=False):
        delta = (later - earlier) & 0xFFFF
        if delta > 1:
            losses += delta - 1
    return losses


def parse_packed_level(data: bytes) -> float:
    """Decode the PACKed scalar level reply (2 bytes BE, 1/10 dBµV)."""
    if len(data) != 2:
        raise EB200Protocol(f"packed level must be 2 bytes, got {len(data)}")
    return struct.unpack(">h", data)[0] / 10

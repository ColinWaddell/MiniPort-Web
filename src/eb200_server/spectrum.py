"""SpectrumService: continuous sweep cycles streamed as binary frames.

Runs one documented sweep cycle after another through the device facade
(see capability-report.md: the trace-buffer path is the working scan data
channel on this unit) and decodes each result into a wire frame:

    ``SPW1`` header (little-endian) + int16 levels (dBµV × 10) with
    an optional peak-hold array. -32768 marks "no level" (NaN).

Frames carry the frequency grid implicitly (start + index × step), so the
payload stays minimal and no full-dataset retransmission is needed.
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
import time
from dataclasses import dataclass

from eb200.device import EB200
from eb200.errors import EB200Error

log = logging.getLogger(__name__)

FRAME_MAGIC = b"SPW1"
FRAME_VERSION = 1
FLAG_PEAK = 0x01
NO_LEVEL = -32768
LEVEL_SCALE = 10
MAX_POINTS = 2001

_HEADER = struct.Struct("<4sBBIddd")


@dataclass(frozen=True)
class SpectrumParams:
    start_hz: int
    stop_hz: int
    step_hz: int

    def validate(self) -> None:
        if self.stop_hz <= self.start_hz:
            raise ValueError("stop_hz must be above start_hz")
        if self.step_hz <= 0:
            raise ValueError("step_hz must be positive")
        points = (self.stop_hz - self.start_hz) // self.step_hz + 1
        if points > MAX_POINTS:
            raise ValueError(
                f"{points} points exceeds the {MAX_POINTS} point limit; "
                "widen the step or narrow the span"
            )

    @property
    def points(self) -> int:
        return (self.stop_hz - self.start_hz) // self.step_hz + 1


def _scale(level: float) -> int:
    if level is None or math.isnan(level):
        return NO_LEVEL
    return max(-32767, min(32767, int(round(level * LEVEL_SCALE))))


def _unscale(value: int) -> float | None:
    if value == NO_LEVEL:
        return None
    return value / LEVEL_SCALE


def encode_frame(
    timestamp: float,
    params: SpectrumParams,
    levels: list[float | None],
    peak: list[float | None] | None = None,
) -> bytes:
    flags = FLAG_PEAK if peak is not None else 0
    header = _HEADER.pack(
        FRAME_MAGIC,
        FRAME_VERSION,
        flags,
        params.points,
        timestamp,
        float(params.start_hz),
        float(params.step_hz),
    )
    body = struct.pack(f"<{len(levels)}h", *(_scale(lv) for lv in levels))
    if peak is not None:
        body += struct.pack(f"<{len(peak)}h", *(_scale(lv) for lv in peak))
    return header + body


def decode_frame(frame: bytes) -> dict:
    """Decode a frame back into fields (tests and diagnostics)."""
    magic, version, flags, points, ts, start, step = _HEADER.unpack_from(frame, 0)
    if magic != FRAME_MAGIC:
        raise ValueError("bad frame magic")
    offset = _HEADER.size
    raw = struct.unpack_from(f"<{points}h", frame, offset)
    offset += 2 * points
    levels = [_unscale(v) for v in raw]
    peak = None
    if flags & FLAG_PEAK:
        raw = struct.unpack_from(f"<{points}h", frame, offset)
        peak = [_unscale(v) for v in raw]
    if version != FRAME_VERSION:
        log.warning("unexpected frame version %s", version)
    return {
        "timestamp": ts,
        "start_hz": int(start),
        "step_hz": int(step),
        "levels": levels,
        "peak": peak,
    }


class SpectrumService:
    """Runs continuous sweep cycles and emits frames + a peak-hold trace.

    Holds ``receiver_lock`` for the whole session so sweep cycles cannot
    interleave with other device operations.
    """

    def __init__(
        self,
        device: EB200,
        receiver_lock: asyncio.Lock,
        history_size: int = 600,
    ) -> None:
        self._device = device
        self._receiver_lock = receiver_lock
        self._task: asyncio.Task | None = None
        self._stop_requested = asyncio.Event()
        self.params: SpectrumParams | None = None
        self.peak: list[float | None] | None = None
        self.running = False
        self.cycle_count = 0
        self.last_error: str | None = None
        self._history: list[tuple[float, list[float | None]]] = []
        self._history_size = history_size
        self._on_frame = None

    async def start(self, params: SpectrumParams) -> None:
        if self.running:
            raise ValueError("spectrum already running")
        params.validate()
        self.params = params
        self.peak = None
        self._history.clear()
        self.cycle_count = 0
        self.last_error = None
        self._stop_requested.clear()
        self._task = asyncio.create_task(self._run(), name="eb200-spectrum")
        await self._wait_running()

    async def stop(self) -> None:
        self._stop_requested.set()
        if self._task is not None:
            await asyncio.wait([self._task], timeout=30.0)
        self.running = False

    async def reset_peak(self) -> None:
        self.peak = None

    async def _wait_running(self) -> None:
        deadline = time.monotonic() + 5.0
        while not self.running and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        if not self.running:
            raise RuntimeError("spectrum service failed to start")

    async def _run(self) -> None:
        params = self.params
        if params is None:
            return
        self.running = True
        await self._receiver_lock.acquire()
        try:
            while not self._stop_requested.is_set():
                started = time.monotonic()
                try:
                    result = await asyncio.to_thread(
                        self._device.scan_sweep,
                        params.start_hz,
                        params.stop_hz,
                        params.step_hz,
                        timeout=15.0,
                    )
                except EB200Error as exc:
                    self.last_error = str(exc)
                    log.warning("spectrum cycle failed: %s", exc)
                    await asyncio.sleep(1.0)
                    continue
                levels: list[float | None] = list(result.levels)
                while len(levels) < params.points:
                    levels.append(None)
                self._merge_peak(levels)
                timestamp = time.time()
                self._history.append((timestamp, levels))
                if len(self._history) > self._history_size:
                    self._history.pop(0)
                self.cycle_count += 1
                self.last_error = "; ".join(result.restore_errors) or None
                if self._on_frame is not None:
                    await self._on_frame(
                        encode_frame(timestamp, params, levels, self.peak)
                    )
                # keep the loop responsive to stop requests
                elapsed = time.monotonic() - started
                await asyncio.sleep(max(0.0, 0.05 - elapsed))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - service must not die silently
            log.exception("spectrum service crashed")
            raise
        finally:
            self._receiver_lock.release()
            self.running = False

    def set_frame_sink(self, sink) -> None:
        self._on_frame = sink

    def _merge_peak(self, levels: list[float | None]) -> None:
        if self.peak is None or len(self.peak) != len(levels):
            self.peak = list(levels)
            return
        peak = self.peak
        for index, level in enumerate(levels):
            if level is None:
                continue
            current = peak[index]
            if current is None or level > current:
                peak[index] = level

    def history(self) -> list[tuple[float, list[float | None]]]:
        return list(self._history)

    def current_levels(self) -> list[float | None] | None:
        return self._history[-1][1] if self._history else None

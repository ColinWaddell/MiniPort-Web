"""AudioService: browser-facing UDP audio streaming and WAV recording.

Start sequence mirrors the weakmon-proven order: remote AF mode first,
then the undocumented AUDIO tag registration to this host. The sample
rate is measured from the wire (the documented table disagrees with
observations; see docs/capability-report.md) and pushed to browsers so
playback speed always matches the stream.

All device-touching phases (:meth:`prepare`, :meth:`request_stop`,
:meth:`teardown`) are synchronous so the manager can run them in worker
threads; only the pump coroutine lives on the event loop.

Recordings buffer PCM in memory and are written as 16-bit mono WAV at
record-stop time, when the measured rate is best known.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from eb200.device import EB200
from eb200.streams import AudioReceiver

log = logging.getLogger(__name__)

# Fallback for the WAV header before the first wire measurement exists;
# phase 5's estimator measures ~8 kHz, matching the documented table.
DEFAULT_WIRE_RATE = 8000
MAX_RECORDING_BYTES = 64 * 1024 * 1024

WAV_HEADER_SIZE = 44


class WavStreamWriter:
    """Streaming 16-bit PCM WAV writer; the rate is patched at finish."""

    def __init__(self, path: Path, sample_rate: int, channels: int = 1) -> None:
        self._path = path
        self._channels = channels
        self._frames = 0
        self._frame_rate = sample_rate
        self._fh = path.open("wb")
        self._fh.write(b"\x00" * WAV_HEADER_SIZE)  # patched at finish()

    def write(self, pcm: bytes) -> None:
        self._fh.write(pcm)
        self._frames += len(pcm) // (2 * self._channels)

    def finish(self, frame_rate: int) -> int:
        framerate = int(frame_rate)
        data_size = self._frames * 2 * self._channels
        byte_rate = framerate * 2 * self._channels
        header = (
            b"RIFF"
            + (36 + data_size).to_bytes(4, "little")
            + b"WAVEfmt "
            + (16).to_bytes(4, "little")
            + (1).to_bytes(2, "little")
            + self._channels.to_bytes(2, "little")
            + framerate.to_bytes(4, "little")
            + byte_rate.to_bytes(4, "little")
            + (2 * self._channels).to_bytes(2, "little")
            + (16).to_bytes(2, "little")
            + b"data"
            + data_size.to_bytes(4, "little")
        )
        self._fh.flush()
        self._fh.close()
        with self._path.open("r+b") as patcher:
            patcher.seek(0)
            patcher.write(header)
        self._frame_rate = framerate
        return self._frames

    def close_provisional(self) -> None:
        """Write the header with the provisional rate (crash safety)."""
        try:
            self.finish(self._frame_rate)
        except Exception:  # noqa: BLE001
            self._fh.close()


class AudioError(RuntimeError):
    """Audio service misuse (start without stream, etc.)."""


class AudioService:
    def __init__(
        self,
        device: EB200,
        recordings_dir: Path,
        *,
        af_mode: int = 10,
        pump_interval: float = 0.04,
        store=None,
    ) -> None:
        self._device = device
        self._recordings_dir = recordings_dir
        self._af_mode = af_mode
        self._pump_interval = pump_interval
        self._store = store
        self._rx: AudioReceiver | None = None
        self._task: asyncio.Task | None = None
        self._stop_requested = asyncio.Event()
        self._running = False
        self._owner_id: str | None = None
        self._af_mode_before: int = 0
        self._ip: str = ""
        self._audio_sink = None
        self._last_announced_rate: float | None = None
        self._recording = False
        self._recording_name: str | None = None
        self._recording_started = 0.0
        self._rec_bytes = 0
        self._rec_trigger = "manual"
        self._rec_job_id: int | None = None
        self._writer: WavStreamWriter | None = None
        recordings_dir.mkdir(parents=True, exist_ok=True)

    # -- lifecycle (sync phases for thread offloading) --------------------------

    def prepare(self, client_id: str, ip: str) -> None:
        """Engage the receiver's audio path and start the UDP receiver."""
        if self._running:
            raise ValueError("audio stream already running")
        self._ip = ip
        self._af_mode_before = self._device.remote_af_mode()
        # weakmon order: AF mode first, then the tag registration.
        self._device.set_remote_af_mode(self._af_mode)
        rx = AudioReceiver()
        rx.start()
        try:
            self._device.udp_register(ip, rx.port, "AUDIO")
        except Exception:
            rx.stop()
            self._device.set_remote_af_mode(self._af_mode_before)
            raise
        self._rx = rx
        self._owner_id = client_id
        self._rec_bytes = 0
        self._recording_name = None

    def begin_pump(self) -> None:
        """Start the pump task (must run on the event loop)."""
        if self._running or self._rx is None:
            raise AudioError("cannot start the pump without prepare()")
        self._stop_requested.clear()
        self._running = True
        self._task = asyncio.get_running_loop().create_task(
            self._pump(), name="eb200-audio-pump"
        )

    def request_stop(self) -> None:
        """Signal the pump to exit (thread-safe)."""
        self._stop_requested.set()

    def teardown(self) -> None:
        """Stop the receiver and restore the device state."""
        time.sleep(0.15)  # let the pump observe the stop flag and drain
        if self._recording:
            if self._writer is not None:
                self._writer.close_provisional()
            self._recording = False
            if self._store is not None:
                self._store.update_recording(
                    self._recording_name or "",
                    bytes_count=self._rec_bytes + 44,
                    duration=max(0.0, time.time() - self._recording_started),
                    rate_hz=int(self._last_announced_rate or DEFAULT_WIRE_RATE),
                )
            self._recording_name = None
            self._writer = None
        if self._rx is not None:
            self._rx.stop()
            self._rx = None
        try:
            self._device.udp_clear(self._ip)
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask errors
            log.warning("audio tag cleanup failed: %s", exc)
        try:
            self._device.set_remote_af_mode(self._af_mode_before)
        except Exception as exc:  # noqa: BLE001
            log.warning("remote AF mode restore failed: %s", exc)
        self._running = False
        self._owner_id = None

    # -- async conveniences (tests) -----------------------------------------------

    async def start(self, client_id: str, ip: str) -> dict:
        self.prepare(client_id, ip)
        self.begin_pump()
        await asyncio.sleep(self._pump_interval)
        return self.status()

    async def stop(self) -> dict:
        self.request_stop()
        await asyncio.sleep(0.15)
        self.teardown()
        await self._notify_config()
        return self.status()

    # -- pump ----------------------------------------------------------------------

    def set_sink(self, sink) -> None:
        """Async callable receiving int16-LE PCM bytes or text frames."""
        self._audio_sink = sink

    async def _pump(self) -> None:
        try:
            rx = self.require_running()
            while not self._stop_requested.is_set():
                samples = rx.read(8192)
                if samples:
                    payload = samples.tobytes()  # native little-endian int16
                    if self._recording:
                        self._rec_bytes += len(payload)
                        if self._writer is not None:
                            self._writer.write(payload)
                        if self._rec_bytes >= MAX_RECORDING_BYTES:
                            log.warning(
                                "recording hit the %d byte cap; stopping",
                                MAX_RECORDING_BYTES,
                            )
                            self.record_stop()
                    if self._audio_sink is not None:
                        await self._audio_sink(payload)
                rate = rx.effective_rate()
                if rate is not None and rate > 0:
                    if (
                        self._last_announced_rate is None
                        or abs(rate - self._last_announced_rate) > 1.0
                    ):
                        self._last_announced_rate = rate
                        await self._announce_rate(rate)
                await asyncio.sleep(self._pump_interval)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the pump must not die silently
            log.exception("audio pump crashed")
            self._running = False

    async def _announce_rate(self, rate: float) -> None:
        await self._audio_sink(f'{{"type":"audio-rate","rate":{rate:.1f}}}'.encode())

    async def _notify_config(self) -> None:
        if self._audio_sink is not None:
            await self._audio_sink(
                f'{{"type":"audio-config","running":{str(self.running).lower()},'
                f'"rate":{self._last_announced_rate or 0:.1f}}}'.encode()
            )

    # -- recordings -----------------------------------------------------------------

    def record_start(
        self,
        client_id: str = "server",
        *,
        trigger: str = "manual",
        job_id: int | None = None,
    ) -> dict:
        self.require_running()  # guard: the stream must be running
        if self._recording:
            raise AudioError("recording already running")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            frequency = self._device.frequency()
            demod = self._device.demodulation()
        except Exception:  # noqa: BLE001 - naming may fail without device
            frequency, demod = 0, "UNK"
        name = f"{stamp}_{frequency}{demod}.wav"
        path = self._recordings_dir / name
        self._writer = WavStreamWriter(
            path,
            int(self._last_announced_rate or DEFAULT_WIRE_RATE),
        )
        self._recording_name = name
        self._recording_started = time.time()
        self._rec_bytes = 0
        self._rec_trigger = trigger
        self._rec_job_id = job_id
        self._recording = True
        if self._store is not None:
            self._store.add_recording(name, str(path), trigger=trigger, job_id=job_id)
        return {"recording": True, "name": name}

    def record_stop(self) -> dict:
        if not self._recording:
            raise AudioError("no recording running")
        rate = self._last_announced_rate
        seconds = 0.0
        framerate = int(round(rate)) if rate else int(DEFAULT_WIRE_RATE)
        frames = 0
        if self._writer is not None:
            frames = self._writer.finish(framerate)
        duration = time.time() - self._recording_started
        if rate:
            seconds = frames / rate
        self._recording = False
        self._recording_name = None
        self._rec_bytes = 0
        if self._store is not None:
            self._store.update_recording(
                self._recording.name if False else self._recording_name or "",
                bytes_count=frames * 2 + 44,
                duration=round(max(seconds, duration * 0.0), 2) or round(duration, 2),
                rate_hz=framerate,
            )
        writer, self._writer = self._writer, None
        self._rec_trigger = "manual"
        self._rec_job_id = None
        return {
            "recording": False,
            "name": writer._path.name if writer else "",
            "samples": frames,
            "seconds": round(seconds or duration, 2),
            "rate_hz": framerate,
        }

    def list_recordings(self) -> list[dict]:
        if self._store is None:
            return []
        rows = self._store.recordings()
        return [
            {
                "name": row["name"],
                "bytes": row["bytes"],
                "duration_s": row["duration_s"],
                "rate_hz": row["rate_hz"],
                "ts": row["ts"],
                "trigger": row["trigger"],
            }
            for row in rows
        ]

    def recording_path(self, name: str) -> Path:
        if "/" in name or "\\" in name or ".." in name:
            raise ValueError("invalid recording name")
        path = self._recordings_dir / name
        if not path.is_file():
            raise FileNotFoundError(name)
        return path

    def delete_recording(self, name: str) -> bool:
        path = self.recording_path(name)
        path.unlink()
        if self._store is not None:
            return self._store.delete_recording_row(name)
        return True

    # -- status ---------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    def require_running(self) -> AudioReceiver:
        if self._rx is None or not self._running:
            raise AudioError("audio stream is not running")
        return self._rx

    @property
    def rx_port(self) -> int:
        """UDP port of the active receiver socket (0 when idle)."""
        return self._rx.port if self._rx is not None else 0

    def status(self) -> dict:
        if self._rx is None or not self._running:
            return {"running": False, "recording": self._recording}
        stats = self._rx.stats()
        rate = self._rx.effective_rate()
        return {
            "running": True,
            "rate_hz": round(rate) if rate else None,
            "packets": stats["packets"],
            "lost": stats["lost"],
            "duplicates": stats["duplicates"],
            "resets": stats["resets"],
            "parse_errors": stats["parse_errors"],
            "buffered_samples": stats["buffered_samples"],
            "samples": stats["samples"],
            "recording": self._recording,
            "recording_name": self._recording_name,
        }

"""ReceiverManager: single owner of the EB200 connection and its state.

The manager owns the one allowed receiver session. It polls receiver state
on a timer and broadcasts a snapshot to every connected browser, so
polling cost does not scale with client count. Control operations are
serialised through a single lock; ownership of the controls can be
acquired explicitly (advisory) or is adopted on first use, preventing
competing control operations between browsers.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from pathlib import Path

from eb200.device import EB200, ScanResult
from eb200.errors import EB200Error
from eb200.transport import DEFAULT_PORT, TCPTransport
from eb200_server.audio import AudioService
from eb200_server.automation import LevelLogger, Scheduler, TriggerRecorder
from eb200_server.db import Store
from eb200_server.presets import csv_export
from eb200_server.spectrum import SpectrumParams, SpectrumService

log = logging.getLogger(__name__)

CONTROL_TIMEOUT_S = 120.0

# Static capability description served to clients; derived from the
# facade's documented constants.
CAPABILITIES = {
    "frequency_min_hz": 10_000,
    "frequency_max_hz": 3_000_000_000,
    "bandwidth_min_hz": 150,
    "bandwidth_max_hz": 150_000,
    "demodulation_modes": ["AM", "FM", "CW", "USB", "LSB"],
    "frequency_modes": ["CW", "FIXed", "SWEep"],
    "gain_min_dbuv": -30.0,
    "gain_max_dbuv": 110.0,
    "gain_modes": ["FIXed", "MGC", "AUTO", "AGC"],
    "sweep_step_max_hz": 10_000_000,
    "udp_trace_tags": ["AUDIO", "IFPAN", "FSCAN", "MSCAN", "CW", "LIST"],
    "scan_supported": True,
}


class ControlBusy(RuntimeError):
    """Another client currently owns the receiver controls."""


# kind -> device setter signature; values validated by the caller layer.
_SETTERS = {
    "frequency": lambda d, v: d.set_frequency(int(v)),
    "demod": lambda d, v: d.set_demodulation(v),
    "bandwidth": lambda d, v: d.set_bandwidth(int(v)),
    "afc": lambda d, v: d.set_afc(bool(v)),
    "att_on": lambda d, v: d.set_attenuator(bool(v)),
    "att_auto": lambda d, v: d.set_attenuator_auto(bool(v)),
    "gain": lambda d, v: d.set_gain(float(v)),
    "gain_mode": lambda d, v: d.set_gain_mode(v),
    "squelch": lambda d, v: d.set_squelch(bool(v)),
    "squelch_threshold": lambda d, v: d.set_squelch_threshold(float(v)),
    "volume": lambda d, v: d.set_af_volume(float(v)),
    "freq_mode": lambda d, v: d.set_frequency_mode(v),
}


def result_to_dict(result: ScanResult) -> dict:
    return {
        "levels": [None if math.isnan(lv) else round(lv, 1) for lv in result.levels],
        "frequencies_hz": list(result.frequencies),
        "restore_errors": list(result.restore_errors),
    }


class ReceiverManager:
    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        *,
        poll_interval: float = 0.3,
        transport: TCPTransport | None = None,
        data_dir: Path | None = None,
        settings: dict | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._poll_interval = poll_interval
        self._own_transport = transport is None
        self._transport = transport or TCPTransport(host, port, timeout=2.5)
        self._device = EB200(self._transport, registry_settle=0.3)
        self._receiver_lock = asyncio.Lock()
        self._clients: dict[object, str] = {}
        self._spectrum_clients: dict[object, str] = {}
        self._audio_clients: dict[object, str] = {}
        self._owner_id: str | None = None
        self._owner_seen = 0.0
        self._snapshot: dict | None = None
        self._scanning = False
        self._last_connected = False
        self._spectrum: SpectrumService | None = None
        self._data_dir = data_dir or Path(__file__).resolve().parents[2] / "data"
        self.settings = {
            "level_retention_days": int(
                os.environ.get("EB200_LEVEL_RETENTION_DAYS", "7")
            ),
            "sweep_retention_days": int(
                os.environ.get("EB200_SWEEP_RETENTION_DAYS", "30")
            ),
            "recording_retention_days": int(
                os.environ.get("EB200_RECORDING_RETENTION_DAYS", "30")
            ),
        }
        self._store = Store(self._data_dir / "miniport.db")
        self._audio = AudioService(
            self._device,
            self._data_dir / "recordings",
            store=self._store,
        )
        self._level_logger = LevelLogger(self._store)
        self._trigger = TriggerRecorder(self._store)
        self._scheduler = Scheduler(self._store, self, self._data_dir / "sweeps")
        self._device_info: dict | None = None
        self._poll_task: asyncio.Task | None = None
        self._info_task: asyncio.Task | None = None
        self._stopped = False
        if settings:
            self.settings.update(settings)

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        if self._own_transport:
            await asyncio.to_thread(self._transport.start)
        self._poll_task = asyncio.create_task(self._poll_loop(), name="eb200-poll")
        self._info_task = asyncio.create_task(
            self._load_device_info(), name="eb200-info"
        )
        self._scheduler.start()

    async def aclose(self) -> None:
        self._stopped = True
        await self._scheduler.aclose()
        if self._audio is not None and self._audio.running:
            await self._audio.stop()
        if self._spectrum is not None:
            await self._spectrum.stop()
        for task in (self._poll_task, self._info_task):
            if task is not None:
                task.cancel()
        if self._own_transport:
            await asyncio.to_thread(self._transport.close)

    @property
    def device_info(self) -> dict | None:
        return self._device_info

    def last_snapshot(self) -> dict | None:
        return self._snapshot

    # -- client registry ------------------------------------------------------

    def websocket_join(self, ws: object, client_id: str) -> None:
        self._clients[ws] = client_id

    def websocket_leave(self, ws: object) -> None:
        client_id = self._clients.pop(ws, None)
        if client_id is not None and self._owner_id == client_id:
            self._owner_id = None

    def spectrum_join(self, ws: object, client_id: str) -> None:
        self._spectrum_clients[ws] = client_id

    def spectrum_leave(self, ws: object) -> None:
        self._spectrum_clients.pop(ws, None)

    def audio_join(self, ws: object, client_id: str) -> None:
        self._audio_clients[ws] = client_id

    def audio_leave(self, ws: object) -> None:
        self._audio_clients.pop(ws, None)

    async def _audio_frame(self, payload: bytes) -> None:
        for ws in list(self._audio_clients):
            if payload.startswith(b'{"type"'):
                sent = await self._safe_send(ws, ws.send_text, payload.decode("ascii"))
            else:
                sent = await self._safe_send(ws, ws.send_bytes, payload)
            if not sent:
                self._audio_clients.pop(ws, None)

    # -- audio streaming -----------------------------------------------------------

    async def audio_start(self, client_id: str) -> dict:
        self._ensure_control(client_id)
        self._spectrum_guard()
        ip = self._transport.local_ip
        async with self._receiver_lock:
            await asyncio.to_thread(self._audio.prepare, client_id, ip)
        self._audio.set_sink(self._audio_frame)
        self._audio.begin_pump()
        return self._audio.status()

    async def audio_stop(self, client_id: str) -> dict:
        self._ensure_control(client_id)
        self._audio.request_stop()
        async with self._receiver_lock:
            await asyncio.to_thread(self._audio.teardown)
        return self._audio.status()

    async def trigger_recording(
        self,
        duration: float,
        *,
        job_id: int | None = None,
        trigger: str = "auto",
    ) -> dict:
        """Conduct a bounded recording, engaging the audio stream if idle."""
        loop = asyncio.get_running_loop()
        ip = self._transport.local_ip
        engaged = False
        if not self._audio.running:
            self._spectrum_guard()
            async with self._receiver_lock:
                await loop.run_in_executor(
                    None, self._audio.prepare, "server:automation", ip
                )
            self._audio.set_sink(self._audio_frame)
            self._audio.begin_pump()
            engaged = True
        try:
            await loop.run_in_executor(
                None,
                lambda: self._audio.record_start(trigger=trigger, job_id=job_id),
            )
            await asyncio.sleep(duration)
            return await loop.run_in_executor(None, self._audio.record_stop)
        finally:
            if engaged:
                self._audio.request_stop()
                async with self._receiver_lock:
                    await loop.run_in_executor(None, self._audio.teardown)

    async def audio_record(self, client_id: str, action: str) -> dict:
        self._ensure_control(client_id)
        if action == "start":
            return await asyncio.to_thread(self._audio.record_start, client_id)
        if action == "stop":
            return await asyncio.to_thread(self._audio.record_stop)
        raise ValueError(f"unknown audio record action {action!r}")

    def audio_status(self) -> dict:
        return self._audio.status()

    def audio_recordings(self) -> list[dict]:
        return self._audio.list_recordings()

    @property
    def store(self) -> Store:
        return self._store

    def audio_recording_path(self, name: str) -> Path:
        return self._audio.recording_path(name)

    def audio_recording_delete(self, client_id: str, name: str) -> bool:
        self._ensure_control(client_id)
        return self._audio.delete_recording(name)

    def note_activity(self, ws_or_client: object) -> None:
        """Refresh the control-ownership TTL (heartbeat or set command)."""
        client_id = self._clients.get(ws_or_client, ws_or_client)
        if isinstance(client_id, str) and self._owner_id == client_id:
            self._owner_seen = time.monotonic()

    # -- control ownership ------------------------------------------------------

    def acquire_control(self, client_id: str) -> None:
        if self._owner_id in (None, client_id):
            self._owner_id = client_id
            self._owner_seen = time.monotonic()
            return
        if time.monotonic() - self._owner_seen > CONTROL_TIMEOUT_S:
            log.info(
                "control ownership of %s expired; taken by %s",
                self._owner_id,
                client_id,
            )
            self._owner_id = client_id
            self._owner_seen = time.monotonic()
            return
        raise ControlBusy(
            f"receiver controls held by another client ({self._owner_id})"
        )

    def release_control(self, client_id: str) -> None:
        if self._owner_id == client_id:
            self._owner_id = None

    def _ensure_control(self, client_id: str) -> None:
        if self._owner_id is None:
            self._owner_id = client_id
            self._owner_seen = time.monotonic()
            return
        if self._owner_id == client_id:
            self._owner_seen = time.monotonic()
            return
        if time.monotonic() - self._owner_seen > CONTROL_TIMEOUT_S:
            self._owner_id = client_id
            self._owner_seen = time.monotonic()
            return
        raise ControlBusy(
            f"receiver controls held by another client ({self._owner_id})"
        )

    # -- operations -------------------------------------------------------------

    async def set_parameter(self, client_id: str, kind: str, value: object) -> None:
        try:
            setter = _SETTERS[kind]
        except KeyError:
            raise ValueError(f"unknown parameter {kind!r}") from None
        self._spectrum_guard()
        async with self._receiver_lock:
            self._ensure_control(client_id)
            await asyncio.to_thread(setter, self._device, value)

    async def scan(
        self,
        client_id: str,
        start_hz: int,
        stop_hz: int,
        step_hz: int,
        *,
        timeout: float = 30.0,
    ) -> dict:
        self._spectrum_guard()
        async with self._receiver_lock:
            self._ensure_control(client_id)
            self._scanning = True
            try:
                result = await asyncio.to_thread(
                    self._device.scan_sweep,
                    start_hz,
                    stop_hz,
                    step_hz,
                    timeout=timeout,
                )
            finally:
                self._scanning = False
        return result_to_dict(result)

    # -- manual reconnect ----------------------------------------------------------

    async def force_reconnect(self) -> dict:
        """Drop any stale link, reconnect once and re-identify, bounded.

        Backs the browser's "attempt connection" button: after a receiver
        power cycle a half-dead socket object satisfies ``is_connected``,
        so the link is reset first to guarantee a real end-to-end
        connect. Refuses while the spectrum sweep holds the receiver; it
        deliberately does **not** take the receiver lock — single SCPI
        requests are serialised inside the transport, and waiting on the
        lock would add a stalled poll cycle's worth of latency (up to
        ~10 s with the receiver unresponsive) to the button press.
        """
        if self._busy():
            raise ControlBusy("receiver busy: spectrum scan is running")
        await asyncio.to_thread(self._transport.reset)
        connected = await asyncio.to_thread(self._transport.reconnect_now, 3.0)
        if connected:
            try:
                self._device_info = await asyncio.to_thread(self._load_info_sync)
            except (EB200Error, OSError):
                pass  # identification is best-effort; the poll loop retries
        self._last_connected = connected
        return {"connected": connected, "device": self._device_info}

    # -- spectrum (continuous sweeps) ----------------------------------------------

    def _busy(self) -> bool:
        if self._scanning:
            return True
        return self._spectrum is not None and self._spectrum.running

    def _spectrum_guard(self) -> None:
        if self._spectrum is not None and self._spectrum.running:
            raise ControlBusy("receiver busy: spectrum scan is running")

    def spectrum_status(self) -> dict:
        service = self._spectrum
        if service is None or service.params is None:
            return {"running": False}
        return {
            "running": service.running,
            "start_hz": service.params.start_hz,
            "stop_hz": service.params.stop_hz,
            "step_hz": service.params.step_hz,
            "points": service.params.points,
            "cycles": service.cycle_count,
            "last_error": service.last_error,
        }

    async def spectrum_start(self, client_id: str, params: SpectrumParams) -> dict:
        self._ensure_control(client_id)
        if self._spectrum is None or not self._spectrum.running:
            self._spectrum = SpectrumService(self._device, self._receiver_lock)
            self._spectrum.set_frame_sink(self._spectrum_frame)
        try:
            await self._spectrum.start(params)
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        return self.spectrum_status()

    async def spectrum_stop(self, client_id: str) -> dict:
        self._ensure_control(client_id)
        if self._spectrum is not None:
            await self._spectrum.stop()
        return self.spectrum_status()

    async def spectrum_reset_peak(self, client_id: str) -> dict:
        self._ensure_control(client_id)
        if self._spectrum is not None:
            await self._spectrum.reset_peak()
        return self.spectrum_status()

    def spectrum_csv(self) -> str:
        service = self._spectrum
        if service is None or service.params is None:
            raise ValueError("spectrum has not run yet")
        frequencies = [
            service.params.start_hz + index * service.params.step_hz
            for index in range(service.params.points)
        ]
        return csv_export(frequencies, service.history())

    async def _spectrum_frame(self, frame: bytes) -> None:
        for ws in list(self._spectrum_clients):
            if not await self._safe_send(ws, ws.send_bytes, frame):
                self._spectrum_clients.pop(ws, None)

    def scheduler_run(self, job: dict) -> dict:
        return self._scheduler.run_job_now(job)

    def level_logger(self) -> LevelLogger:
        return self._level_logger

    def trigger_recorder(self) -> TriggerRecorder:
        return self._trigger

    # -- presets (SQLite-backed; phase 6) ----------------------------------------

    def presets(self, kind: str) -> list[dict]:
        return self._store.presets(kind)

    def preset_add(self, client_id: str, kind: str, name: str, params: dict) -> dict:
        self._ensure_control(client_id)
        if kind == "spectrum":
            SpectrumParams(
                int(params["start_hz"]), int(params["stop_hz"]), int(params["step_hz"])
            ).validate()
        return self._store.add_preset(kind, name, params)

    def preset_delete(self, client_id: str, preset_id: int) -> bool:
        self._ensure_control(client_id)
        return self._store.delete_preset(preset_id)

    async def preset_apply(self, client_id: str, preset_id: int) -> dict:
        kind_rows = self._store.presets("receiver")
        preset = next((p for p in kind_rows if p["id"] == preset_id), None)
        if preset is None:
            raise ValueError(f"receiver preset {preset_id} not found")
        params = preset["params"]
        apply_sequence = [
            ("frequency", int(params["frequency_hz"])),
            ("demod", str(params.get("demod", "AM"))),
            ("bandwidth", int(params.get("bandwidth_hz", 15_000))),
        ]
        self._spectrum_guard()
        async with self._receiver_lock:
            self._ensure_control(client_id)
            for kind, value in apply_sequence:
                setter = _SETTERS[kind]
                await asyncio.to_thread(setter, self._device, value)
        return {"applied": True, "preset_id": preset_id}

    # -- polling / broadcast ------------------------------------------------------

    def _sample_state(self) -> dict:
        device = self._device
        return {
            "freq_hz": device.frequency(),
            "demod": device.demodulation(),
            "freq_mode": device.frequency_mode(),
            "bandwidth_hz": device.bandwidth(),
            "afc": device.afc(),
            "att_on": device.attenuator(),
            "att_auto": device.attenuator_auto(),
            "gain_db": device.gain(),
            "gain_mode": device.gain_mode(),
            "squelch": device.squelch(),
            "squelch_threshold_dbuv": device.squelch_threshold(),
            "volume": device.af_volume(),
            "level_dbuv": device.level(),
            "sweeping": device.sweeping(),
            "remote_af_mode": device.remote_af_mode(),
        }

    async def poll_once(self, *, offline: bool = False) -> dict:
        sampling_allowed = not self._busy() and not offline
        error = None
        state = None
        if sampling_allowed:
            try:
                async with self._receiver_lock:
                    state = await asyncio.to_thread(self._sample_state)
                self._last_connected = True
            except (EB200Error, OSError) as exc:
                error = str(exc)
                self._last_connected = False
                log.warning("receiver poll failed: %s", exc)
        if state is not None:
            now = time.time()
            self._level_logger.ingest(now, state["freq_hz"], state["level_dbuv"])
            await self._trigger.ingest(now, state["level_dbuv"])
        # While busy (spectrum running) the last known connectivity stands;
        # sampling would race the sweep task.
        snapshot = {
            "type": "state",
            "ts": time.time(),
            "connected": self._last_connected,
            "scanning": self._busy(),
            "spectrum": self.spectrum_status(),
            "audio": self.audio_status(),
            "error": error,
            "device": self._device_info,
            "state": state or (self._snapshot or {}).get("state"),
        }
        self._snapshot = snapshot
        await self._broadcast(snapshot)
        return snapshot

    async def _broadcast(self, message: dict) -> None:
        for ws in list(self._clients):
            if not await self._safe_send(ws, ws.send_json, message):
                self._clients.pop(ws, None)

    async def _safe_send(self, ws: object, send, *args) -> bool:
        """Send with a hard timeout; dead peers must never stall producers.

        The close() attempt is bounded too: starlette's close performs a
        graceful close-frame handshake, which can hang forever on a
        half-dead TCP socket (e.g. a crashed browser process) — and a hang
        here would stop every broadcast to every client.
        """
        try:
            await asyncio.wait_for(send(*args), timeout=2.0)
            return True
        except Exception:  # noqa: BLE001 - prune on failure or hang
            try:
                await asyncio.wait_for(ws.close(), timeout=1.0)
            except Exception:  # noqa: BLE001 - give up; the socket is dead
                pass
            return False

    async def _poll_loop(self) -> None:
        while not self._stopped:
            try:
                if not self._transport.is_connected:
                    connected = await asyncio.to_thread(self._transport.reconnect_now)
                    if not connected:
                        await self.poll_once(offline=True)
                        await asyncio.sleep(self._poll_interval)
                        continue
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the loop alive
                log.exception("poll loop error")
            await asyncio.sleep(self._poll_interval)

    async def _load_device_info(self) -> None:
        while not self._stopped:
            try:
                async with self._receiver_lock:
                    info = await asyncio.to_thread(self._load_info_sync)
                self._device_info = info
                return
            except (EB200Error, OSError) as exc:
                log.info("device identification pending: %s", exc)
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                raise

    def _load_info_sync(self) -> dict:
        info = self._device.identify()
        try:
            options = self._device.option_codes()
        except EB200Error:
            options = []
        return {
            "model": info.model,
            "serial": info.serial,
            "firmware": info.firmware,
            "options": options,
            "host": self._host,
            "port": self._port,
        }

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
import time
from pathlib import Path

from eb200.device import EB200, ScanResult
from eb200.errors import EB200Error
from eb200.transport import DEFAULT_PORT, TCPTransport
from eb200_server.presets import PresetStore, csv_export
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
        self._owner_id: str | None = None
        self._owner_seen = 0.0
        self._snapshot: dict | None = None
        self._scanning = False
        self._last_connected = False
        self._spectrum: SpectrumService | None = None
        self._preset_store = PresetStore(
            Path(__file__).resolve().parents[2] / "data" / "scan-presets.json"
        )
        self._device_info: dict | None = None
        self._poll_task: asyncio.Task | None = None
        self._info_task: asyncio.Task | None = None
        self._stopped = False

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        if self._own_transport:
            await asyncio.to_thread(self._transport.start)
        self._poll_task = asyncio.create_task(self._poll_loop(), name="eb200-poll")
        self._info_task = asyncio.create_task(
            self._load_device_info(), name="eb200-info"
        )

    async def aclose(self) -> None:
        self._stopped = True
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
            try:
                await ws.send_bytes(frame)
            except Exception:  # noqa: BLE001 - prune dead sockets lazily
                self._spectrum_clients.pop(ws, None)

    def preset_list(self) -> list[dict]:
        return self._preset_store.list()

    def preset_add(self, client_id: str, name: str, params: SpectrumParams) -> dict:
        self._ensure_control(client_id)
        params.validate()
        return self._preset_store.add(
            name, params.start_hz, params.stop_hz, params.step_hz
        )

    def preset_delete(self, client_id: str, preset_id: int) -> bool:
        self._ensure_control(client_id)
        return self._preset_store.delete(preset_id)

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
        }

    async def poll_once(self) -> dict:
        sampling_allowed = not self._busy()
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
        # While busy (spectrum running) the last known connectivity stands;
        # sampling would race the sweep task.
        snapshot = {
            "type": "state",
            "ts": time.time(),
            "connected": self._last_connected,
            "scanning": self._busy(),
            "spectrum": self.spectrum_status(),
            "error": error,
            "device": self._device_info,
            "state": state or (self._snapshot or {}).get("state"),
        }
        self._snapshot = snapshot
        await self._broadcast(snapshot)
        return snapshot

    async def _broadcast(self, message: dict) -> None:
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001 - dead clients are pruned lazily
                self._clients.pop(ws, None)

    async def _poll_loop(self) -> None:
        while not self._stopped:
            try:
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

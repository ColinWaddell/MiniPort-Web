"""FastAPI application: REST control endpoints and the live-state socket.

Browser clients never talk to the EB200; every request goes through the
:class:`~eb200_server.manager.ReceiverManager` that owns the single
receiver session. Control requests carry an ``X-Eb200-Client`` header
identifying the browser; ownership prevents competing control operations.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from eb200.errors import EB200Error
from eb200_server.audio import AudioError
from eb200_server.manager import CAPABILITIES, ControlBusy, ReceiverManager
from eb200_server.spectrum import SpectrumParams, decode_frame

log = logging.getLogger(__name__)


class ControlRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)


class FrequencyRequest(BaseModel):
    hz: int = Field(ge=0, le=3_000_000_000)


class BandwidthRequest(BaseModel):
    hz: int = Field(ge=0, le=1_000_000)


class ModeRequest(BaseModel):
    mode: str = Field(min_length=1, max_length=16)


class GainRequest(BaseModel):
    db: float = Field(ge=-40.0, le=120.0)


class SwitchRequest(BaseModel):
    enabled: bool


class AttenuatorRequest(BaseModel):
    enabled: bool | None = None
    auto: bool | None = None


class ThresholdRequest(BaseModel):
    dbuv: float = Field(ge=-200.0, le=200.0)


class VolumeRequest(BaseModel):
    value: float = Field(ge=0.0, le=1.0)


class ScanRequest(BaseModel):
    start_hz: int = Field(ge=0)
    stop_hz: int = Field(ge=0)
    step_hz: int = Field(gt=0)
    timeout: float = Field(default=30.0, gt=0.0, le=120.0)


class SpectrumStartRequest(BaseModel):
    start_hz: int = Field(ge=0)
    stop_hz: int = Field(ge=0)
    step_hz: int = Field(gt=0)


class PresetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    start_hz: int = Field(ge=0)
    stop_hz: int = Field(ge=0)
    step_hz: int = Field(gt=0)


class ReceiverPresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    frequency_hz: int | None = Field(default=None, ge=0)
    demod: str | None = Field(default=None, min_length=1, max_length=8)
    bandwidth_hz: int | None = Field(default=None, ge=0)


class JobCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kind: str = Field(pattern="^(sweep|audio-record)$")
    params: dict
    interval_s: int | None = Field(default=None, ge=5)
    daily: str | None = Field(default=None, pattern="^([01][0-9]|2[0-3]):[0-5][0-9]$")


class LevelLogRequest(BaseModel):
    enabled: bool | None = None
    period_s: float | None = Field(default=None, ge=0.5, le=3600)


class TriggerRequest(BaseModel):
    armed: bool | None = None
    threshold_dbuv: float | None = None
    duration_s: float | None = Field(default=None, ge=1, le=600)
    cooldown_s: float | None = Field(default=None, ge=0, le=3600)


def _require_header(header_value: str | None) -> None:
    if not header_value:
        raise HTTPException(400, "X-Eb200-Client header required")


def _iso(timestamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(timestamp))


def _attachment(body: str, filename: str) -> Response:
    return Response(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _control_call(coro) -> dict:
    """Shared error mapping for control-ish calls."""
    try:
        return await coro
    except ControlBusy as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except EB200Error as exc:
        raise HTTPException(502, f"receiver reported: {exc}") from None
    except OSError as exc:
        raise HTTPException(503, f"receiver error: {exc}") from None


async def _apply_set(
    manager: ReceiverManager,
    client_header: str | None,
    kind: str,
    value: object,
) -> dict:
    if not client_header:
        raise HTTPException(400, "X-Eb200-Client header required")
    try:
        await manager.set_parameter(client_header, kind, value)
    except ControlBusy as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except EB200Error as exc:
        raise HTTPException(502, f"receiver reported: {exc}") from None
    except OSError as exc:
        raise HTTPException(503, f"receiver error: {exc}") from None
    return {"ok": True}


def create_app(manager: ReceiverManager, *, owned_lifespan: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if owned_lifespan:
            await manager.start()
        try:
            yield
        finally:
            if owned_lifespan:
                await manager.aclose()

    app = FastAPI(title="MiniPort-Web", lifespan=lifespan)

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/device")
    async def device_info() -> dict:
        return {"device": manager.device_info, "capabilities": CAPABILITIES}

    @app.post("/api/reconnect")
    async def device_reconnect() -> dict:
        try:
            return await manager.force_reconnect()
        except ControlBusy as exc:
            raise HTTPException(409, str(exc)) from None
        except EB200Error as exc:
            raise HTTPException(502, f"receiver reported: {exc}") from None
        except OSError as exc:
            raise HTTPException(503, f"receiver error: {exc}") from None

    @app.get("/api/state")
    async def state() -> dict:
        return await manager.poll_once()

    @app.post("/api/control/acquire")
    async def control_acquire(request: ControlRequest) -> dict:
        try:
            manager.acquire_control(request.client_id)
        except ControlBusy as exc:
            raise HTTPException(409, str(exc)) from None
        return {"controlled": True, "client_id": request.client_id}

    @app.post("/api/control/release")
    async def control_release(request: ControlRequest) -> dict:
        manager.release_control(request.client_id)
        return {"controlled": False}

    @app.post("/api/frequency")
    async def set_frequency(
        request: FrequencyRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        return await _apply_set(manager, x_eb200_client, "frequency", request.hz)

    @app.post("/api/demod")
    async def set_demod(
        request: ModeRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        return await _apply_set(manager, x_eb200_client, "demod", request.mode)

    @app.post("/api/bandwidth")
    async def set_bandwidth(
        request: BandwidthRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        return await _apply_set(manager, x_eb200_client, "bandwidth", request.hz)

    @app.post("/api/gain")
    async def set_gain(
        request: GainRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        return await _apply_set(manager, x_eb200_client, "gain", request.db)

    @app.post("/api/gain-mode")
    async def set_gain_mode(
        request: ModeRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        return await _apply_set(manager, x_eb200_client, "gain_mode", request.mode)

    @app.post("/api/squelch")
    async def set_squelch(
        request: SwitchRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        return await _apply_set(manager, x_eb200_client, "squelch", request.enabled)

    @app.post("/api/afc")
    async def set_afc(
        request: SwitchRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        return await _apply_set(manager, x_eb200_client, "afc", request.enabled)

    @app.post("/api/squelch-threshold")
    async def set_squelch_threshold(
        request: ThresholdRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        return await _apply_set(
            manager, x_eb200_client, "squelch_threshold", request.dbuv
        )

    @app.post("/api/volume")
    async def set_volume(
        request: VolumeRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        return await _apply_set(manager, x_eb200_client, "volume", request.value)

    @app.post("/api/operation")
    async def set_operation(
        request: ModeRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        return await _apply_set(manager, x_eb200_client, "freq_mode", request.mode)

    @app.post("/api/attenuator")
    async def set_attenuator(
        request: AttenuatorRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        if not x_eb200_client:
            raise HTTPException(400, "X-Eb200-Client header required")
        try:
            if request.auto is not None:
                await manager.set_parameter(x_eb200_client, "att_auto", request.auto)
            if request.enabled is not None:
                await manager.set_parameter(x_eb200_client, "att_on", request.enabled)
        except ControlBusy as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except EB200Error as exc:
            raise HTTPException(502, f"receiver reported: {exc}") from None
        except OSError as exc:
            raise HTTPException(503, f"receiver error: {exc}") from None
        return {"ok": True}

    @app.post("/api/scan")
    async def run_scan(
        request: ScanRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        if not x_eb200_client:
            raise HTTPException(400, "X-Eb200-Client header required")
        try:
            return await manager.scan(
                x_eb200_client,
                request.start_hz,
                request.stop_hz,
                request.step_hz,
                timeout=request.timeout,
            )
        except ControlBusy as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except EB200Error as exc:
            raise HTTPException(502, f"receiver reported: {exc}") from None
        except OSError as exc:
            raise HTTPException(503, f"receiver error: {exc}") from None

    @app.websocket("/ws/state")
    async def state_socket(ws: WebSocket, client_id: str = ""):
        await ws.accept()
        manager.websocket_join(ws, client_id)
        last = manager.last_snapshot()
        if last is not None:
            await ws.send_json(last)
        try:
            while True:
                message = await ws.receive_json()
                if message.get("type") == "hb":
                    manager.note_activity(ws)
                    await manager.poll_once()
        except WebSocketDisconnect:
            pass
        finally:
            manager.websocket_leave(ws)

    @app.post("/api/spectrum/start")
    async def spectrum_start(
        request: SpectrumStartRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        if not x_eb200_client:
            raise HTTPException(400, "X-Eb200-Client header required")
        params = SpectrumParams(request.start_hz, request.stop_hz, request.step_hz)
        return await _control_call(manager.spectrum_start(x_eb200_client, params))

    @app.post("/api/spectrum/stop")
    async def spectrum_stop(x_eb200_client: str = Header(None)) -> dict:
        if not x_eb200_client:
            raise HTTPException(400, "X-Eb200-Client header required")
        return await _control_call(manager.spectrum_stop(x_eb200_client))

    @app.post("/api/spectrum/reset-peak")
    async def spectrum_reset_peak(x_eb200_client: str = Header(None)) -> dict:
        if not x_eb200_client:
            raise HTTPException(400, "X-Eb200-Client header required")
        return await _control_call(manager.spectrum_reset_peak(x_eb200_client))

    @app.get("/api/spectrum/export.csv")
    async def spectrum_export_csv() -> Response:
        try:
            content = manager.spectrum_csv()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return Response(
            content,
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="spectrum.csv"'},
        )

    @app.get("/api/spectrum/presets")
    async def preset_list() -> dict:
        return {"presets": manager.presets("spectrum")}

    @app.post("/api/spectrum/presets")
    async def preset_create(
        request: PresetCreateRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        if not x_eb200_client:
            raise HTTPException(400, "X-Eb200-Client header required")
        params = {
            "start_hz": request.start_hz,
            "stop_hz": request.stop_hz,
            "step_hz": request.step_hz,
        }
        try:
            result = manager.preset_add(
                x_eb200_client, "spectrum", request.name, params
            )
        except ControlBusy as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"preset": result}

    @app.delete("/api/spectrum/presets/{preset_id}")
    async def preset_delete(preset_id: int, x_eb200_client: str = Header(None)) -> dict:
        if not x_eb200_client:
            raise HTTPException(400, "X-Eb200-Client header required")
        try:
            deleted = manager.preset_delete(x_eb200_client, preset_id)
        except ControlBusy as exc:
            raise HTTPException(409, str(exc)) from None
        if not deleted:
            raise HTTPException(404, f"preset {preset_id} not found")
        return {"deleted": True}

    # -- automation (phase 6) ------------------------------------------------------

    @app.get("/api/presets")
    async def receiver_presets() -> dict:
        return {"presets": manager.presets("receiver")}

    @app.post("/api/presets")
    async def receiver_preset_create(
        request: ReceiverPresetRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        _require_header(x_eb200_client)
        state = (manager.last_snapshot() or {}).get("state") or {}
        params = {
            "frequency_hz": request.frequency_hz or state.get("freq_hz", 27_260_000),
            "demod": request.demod or state.get("demod", "AM"),
            "bandwidth_hz": request.bandwidth_hz or state.get("bandwidth_hz", 15_000),
        }
        try:
            preset = manager.preset_add(
                x_eb200_client, "receiver", request.name, params
            )
        except ControlBusy as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"preset": preset}

    @app.post("/api/presets/{preset_id}/apply")
    async def receiver_preset_apply(
        preset_id: int, x_eb200_client: str = Header(None)
    ) -> dict:
        _require_header(x_eb200_client)
        return await _control_call(manager.preset_apply(x_eb200_client, preset_id))

    @app.delete("/api/presets/{preset_id}")
    async def receiver_preset_delete(
        preset_id: int, x_eb200_client: str = Header(None)
    ) -> dict:
        _require_header(x_eb200_client)
        try:
            deleted = manager.preset_delete(x_eb200_client, preset_id)
        except ControlBusy as exc:
            raise HTTPException(409, str(exc)) from None
        if not deleted:
            raise HTTPException(404, f"preset {preset_id} not found")
        return {"deleted": True}

    @app.get("/api/jobs")
    async def job_list() -> dict:
        return {"jobs": manager.store.jobs(), "runs": manager.store.recent_runs(30)}

    @app.post("/api/jobs")
    async def job_create(
        request: JobCreateRequest, x_eb200_client: str = Header(None)
    ) -> dict:
        _require_header(x_eb200_client)
        try:
            return {
                "job": manager.store.add_job(
                    request.name,
                    request.kind,
                    request.params,
                    interval_s=request.interval_s,
                    daily=request.daily,
                )
            }
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/jobs/{job_id}/enable")
    async def job_enable(job_id: int, x_eb200_client: str = Header(None)) -> dict:
        _require_header(x_eb200_client)
        if not manager.store.set_job_enabled(job_id, True):
            raise HTTPException(404, f"job {job_id} not found")
        return {"enabled": True}

    @app.post("/api/jobs/{job_id}/disable")
    async def job_disable(job_id: int, x_eb200_client: str = Header(None)) -> dict:
        _require_header(x_eb200_client)
        if not manager.store.set_job_enabled(job_id, False):
            raise HTTPException(404, f"job {job_id} not found")
        return {"enabled": False}

    @app.post("/api/jobs/{job_id}/run")
    async def job_run_now(job_id: int, x_eb200_client: str = Header(None)) -> dict:
        _require_header(x_eb200_client)
        job = next((j for j in manager.store.jobs() if j["id"] == job_id), None)
        if job is None:
            raise HTTPException(404, f"job {job_id} not found")
        return {"result": await manager.scheduler_run(job)}

    @app.delete("/api/jobs/{job_id}")
    async def job_delete(job_id: int, x_eb200_client: str = Header(None)) -> dict:
        _require_header(x_eb200_client)
        if not manager.store.delete_job(job_id):
            raise HTTPException(404, f"job {job_id} not found")
        return {"deleted": True}

    @app.get("/api/job-runs")
    async def job_runs(limit: int = 50) -> dict:
        return {"runs": manager.store.recent_runs(min(limit, 500))}

    @app.get("/api/level-log")
    async def level_log_status() -> dict:
        return manager.level_logger().status()

    @app.post("/api/level-log")
    async def level_log_configure(request: LevelLogRequest) -> dict:
        try:
            return manager.level_logger().configure(
                enabled=request.enabled, period_s=request.period_s
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/level-log/export.csv")
    async def level_log_export(since: float = 0.0) -> Response:
        rows = manager.store.levels_since(since)
        body = "timestamp_iso,frequency_hz,level_dbuv\n" + "\n".join(
            _iso(r["ts"]) + f",{r['frequency_hz']},{r['level_dbuv']:.1f}" for r in rows
        )
        return _attachment(body + "\n", "level-log.csv")

    @app.get("/api/level-log/export.json")
    async def level_log_export_json(since: float = 0.0) -> dict:
        return {"samples": manager.store.levels_since(since)}

    @app.get("/api/sweeps")
    async def sweep_list(limit: int = 100) -> dict:
        return {"sweeps": manager.store.sweeps(min(limit, 500))}

    @app.get("/api/sweeps/{sweep_id}/export.csv")
    async def sweep_export_csv(sweep_id: int) -> Response:
        sweep = manager.store.get_sweep(sweep_id)
        if sweep is None:
            raise HTTPException(404, "sweep not found")
        frequencies = [
            sweep["start_hz"] + index * sweep["step_hz"]
            for index in range(sweep["points"])
        ]
        levels = decode_frame(Path(sweep["path"]).read_bytes())["levels"]
        stamp = _iso(sweep["ts"])
        rows = ["timestamp_iso,frequency_hz,level_dbuv"]
        for freq, level in zip(frequencies, levels, strict=False):
            level_text = f"{level:.1f}" if level is not None else ""
            rows.append(f"{stamp},{freq},{level_text}")
        return _attachment("\n".join(rows) + "\n", f"sweep-{sweep_id}.csv")

    @app.delete("/api/sweeps/{sweep_id}")
    async def sweep_delete(sweep_id: int, x_eb200_client: str = Header(None)) -> dict:
        _require_header(x_eb200_client)
        deleted = manager.store.delete_sweep(sweep_id, remove_file=True)
        if not deleted:
            raise HTTPException(404, "sweep not found")
        return {"deleted": True}

    @app.get("/api/trigger")
    async def trigger_status() -> dict:
        return manager.trigger_recorder().status()

    @app.post("/api/trigger")
    async def trigger_configure(request: TriggerRequest) -> dict:
        try:
            return manager.trigger_recorder().configure(
                armed=request.armed,
                threshold_dbuv=request.threshold_dbuv,
                duration_s=request.duration_s,
                cooldown_s=request.cooldown_s,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.websocket("/ws/spectrum")
    async def spectrum_socket(ws: WebSocket, client_id: str = ""):
        await ws.accept()
        manager.spectrum_join(ws, client_id)
        await ws.send_json({"type": "spectrum-config", **manager.spectrum_status()})
        try:
            while True:
                message = await ws.receive_json()
                if message.get("type") == "hb":
                    manager.note_activity(ws)
        except WebSocketDisconnect:
            pass
        finally:
            manager.spectrum_leave(ws)

    @app.post("/api/audio/start")
    async def audio_start(x_eb200_client: str = Header(None)) -> dict:
        if not x_eb200_client:
            raise HTTPException(400, "X-Eb200-Client header required")
        return await _control_call(manager.audio_start(x_eb200_client))

    @app.post("/api/audio/stop")
    async def audio_stop(x_eb200_client: str = Header(None)) -> dict:
        if not x_eb200_client:
            raise HTTPException(400, "X-Eb200-Client header required")
        return await _control_call(manager.audio_stop(x_eb200_client))

    @app.get("/api/audio/stats")
    async def audio_stats() -> dict:
        return manager.audio_status()

    @app.post("/api/audio/record/{action}")
    async def audio_record(action: str, x_eb200_client: str = Header(None)) -> dict:
        if not x_eb200_client:
            raise HTTPException(400, "X-Eb200-Client header required")
        try:
            return await manager.audio_record(x_eb200_client, action)
        except ControlBusy as exc:
            raise HTTPException(409, str(exc)) from None
        except AudioError as exc:
            raise HTTPException(400, str(exc)) from None
        except EB200Error as exc:
            raise HTTPException(502, f"receiver reported: {exc}") from None
        except OSError as exc:
            raise HTTPException(503, f"receiver error: {exc}") from None

    @app.get("/api/audio/recordings")
    async def audio_recordings() -> dict:
        return {"recordings": manager.audio_recordings()}

    @app.get("/api/audio/recordings/{name}")
    async def audio_recording_download(name: str) -> Response:
        try:
            path = manager.audio_recording_path(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except FileNotFoundError:
            raise HTTPException(404, "recording not found") from None
        return FileResponse(path, media_type="audio/wav", filename=path.name)

    @app.delete("/api/audio/recordings/{name}")
    async def audio_recording_delete(
        name: str, x_eb200_client: str = Header(None)
    ) -> dict:
        if not x_eb200_client:
            raise HTTPException(400, "X-Eb200-Client header required")
        try:
            deleted = manager.audio_recording_delete(x_eb200_client, name)
        except ControlBusy as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except FileNotFoundError:
            raise HTTPException(404, "recording not found") from None
        return {"deleted": deleted}

    @app.websocket("/ws/audio")
    async def audio_socket(ws: WebSocket, client_id: str = ""):
        await ws.accept()
        manager.audio_join(ws, client_id)
        await ws.send_json({"type": "audio-config", **manager.audio_status()})
        try:
            while True:
                message = await ws.receive_json()
                if message.get("type") == "hb":
                    manager.note_activity(ws)
        except WebSocketDisconnect:
            pass
        finally:
            manager.audio_leave(ws)

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    else:
        log.info("no frontend build at %s; API only", dist)


__all__ = ["create_app"]

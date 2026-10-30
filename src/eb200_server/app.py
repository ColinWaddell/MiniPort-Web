"""FastAPI application: REST control endpoints and the live-state socket.

Browser clients never talk to the EB200; every request goes through the
:class:`~eb200_server.manager.ReceiverManager` that owns the single
receiver session. Control requests carry an ``X-Eb200-Client`` header
identifying the browser; ownership prevents competing control operations.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from eb200.errors import EB200Error
from eb200_server.manager import CAPABILITIES, ControlBusy, ReceiverManager

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

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    else:
        log.info("no frontend build at %s; API only", dist)


__all__ = ["create_app"]

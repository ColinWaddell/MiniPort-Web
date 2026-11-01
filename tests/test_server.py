"""Server tests with the stateful simulated instrument."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from eb200.sim import StatefulTransport
from eb200_server.app import create_app
from eb200_server.manager import ControlBusy, ReceiverManager

CLIENT = "ui-test"
HEADERS = {"X-Eb200-Client": CLIENT}


@pytest.fixture
def api():
    manager = ReceiverManager(
        "10.0.0.240", transport=StatefulTransport(), poll_interval=0.05
    )
    app = create_app(manager)
    with TestClient(app) as test_client:
        test_client.app_state_manager = manager  # convenience for direct tests
        yield test_client


def _acquire(api, client_id=CLIENT):
    assert (
        api.post("/api/control/acquire", json={"client_id": client_id}).status_code
        == 200
    )


def test_device_info(api):
    data = api.get("/api/device").json()
    assert data["device"]["model"] == "EB200"
    assert data["device"]["firmware"].startswith("V02.80")
    assert data["capabilities"]["demodulation_modes"][0] == "AM"
    assert data["capabilities"]["scan_supported"] is True


def test_state_snapshot(api):
    state = api.get("/api/state").json()
    assert state["connected"] is True
    assert state["error"] is None
    assert state["state"]["freq_hz"] == 27_260_000
    assert state["state"]["demod"] == "AM"
    assert state["state"]["level_dbuv"] == -18.3


def test_frequency_set_roundtrip(api):
    _acquire(api)
    response = api.post("/api/frequency", json={"hz": 100_000_000}, headers=HEADERS)
    assert response.status_code == 200
    assert api.get("/api/state").json()["state"]["freq_hz"] == 100_000_000


def test_state_changes_across_controls(api):
    _acquire(api)
    api.post("/api/demod", json={"mode": "FM"}, headers=HEADERS)
    api.post("/api/bandwidth", json={"hz": 9_000}, headers=HEADERS)
    api.post("/api/gain", json={"db": 60}, headers=HEADERS)
    api.post("/api/gain-mode", json={"mode": "FIXed"}, headers=HEADERS)
    api.post("/api/squelch", json={"enabled": False}, headers=HEADERS)
    api.post("/api/squelch-threshold", json={"dbuv": -20}, headers=HEADERS)
    api.post("/api/volume", json={"value": 0.5}, headers=HEADERS)
    api.post("/api/attenuator", json={"enabled": True, "auto": False}, headers=HEADERS)
    state = api.get("/api/state").json()["state"]
    assert state["demod"] == "FM"
    assert state["bandwidth_hz"] == 9_000
    assert state["gain_db"] == 60
    assert state["gain_mode"] == "FIXed"
    assert state["squelch"] is False
    assert state["squelch_threshold_dbuv"] == -20
    assert state["volume"] == 0.5
    assert state["att_on"] is True
    assert state["att_auto"] is False


def test_ownership_blocks_second_client(api):
    _acquire(api, "first")
    blocked = api.post(
        "/api/frequency", json={"hz": 1_000_000}, headers={"X-Eb200-Client": "second"}
    )
    assert blocked.status_code == 409
    api.post("/api/control/release", json={"client_id": "first"})
    allowed = api.post(
        "/api/frequency", json={"hz": 1_000_000}, headers={"X-Eb200-Client": "second"}
    )
    assert allowed.status_code == 200


def test_ownership_adopts_first_user(api):
    response = api.post(
        "/api/frequency", json={"hz": 50_000_000}, headers={"X-Eb200-Client": "solo"}
    )
    assert response.status_code == 200


def test_reconnect_endpoint(api):
    transport = api.app_state_manager._transport
    positive = api.post("/api/reconnect")
    assert positive.status_code == 200
    data = positive.json()
    assert data["connected"] is True
    assert data["device"]["model"] == "EB200"

    # A stale link (receiver powered off/on) is dropped, then re-established.
    transport.disconnect()
    data = api.post("/api/reconnect").json()
    assert data["connected"] is True
    assert api.get("/api/state").json()["connected"] is True


def test_reconnect_refused_while_busy(api):
    _acquire(api)
    started = api.post(
        "/api/spectrum/start",
        json={"start_hz": 26_800_000, "stop_hz": 27_800_000, "step_hz": 25_000},
        headers=HEADERS,
    )
    assert started.status_code == 200
    try:
        busy = api.post("/api/reconnect")
        assert busy.status_code == 409
    finally:
        api.post("/api/spectrum/stop", headers=HEADERS)
    assert api.post("/api/reconnect").status_code == 200


def test_missing_client_header(api):
    assert api.post("/api/frequency", json={"hz": 1_000}).status_code == 400


def test_invalid_values_map_to_400(api):
    _acquire(api)
    assert (
        api.post("/api/frequency", json={"hz": 1}, headers=HEADERS).status_code == 400
    )
    assert (
        api.post("/api/bandwidth", json={"hz": 1_000_000}, headers=HEADERS).status_code
        == 400
    )
    assert (
        api.post("/api/demod", json={"mode": "XYZ"}, headers=HEADERS).status_code == 400
    )
    # Out-of-range values are rejected by the pydantic request model (422)
    # or by the device facade (400); both are client errors.
    assert api.post("/api/gain", json={"db": 999}, headers=HEADERS).status_code in (
        400,
        422,
    )


def test_scan_error_maps_to_502():
    from eb200.errors import EB200Timeout

    transport = StatefulTransport()
    manager = ReceiverManager("10.0.0.240", transport=transport, poll_interval=0.05)
    app = create_app(manager)
    with TestClient(app, raise_server_exceptions=False) as api:
        assert (
            api.post("/api/control/acquire", json={"client_id": CLIENT}).status_code
            == 200
        )
        transport.inject_fault(EB200Timeout("forced failure"))
        # scan_sweep tolerates faults while recording state, so enough are
        # injected to reach the configure/measure phase.
        for _ in range(12):
            transport.inject_fault(EB200Timeout("forced failure"))
        response = api.post(
            "/api/scan",
            json={"start_hz": 26_800_000, "stop_hz": 27_800_000, "step_hz": 25_000},
            headers=HEADERS,
        )
        assert response.status_code == 502


def test_websocket_state_stream(api):
    with api.websocket_connect("/ws/state?client_id=ws-test") as ws:
        first = ws.receive_json()
        assert first["type"] == "state"
        assert first["state"]["freq_hz"] == 27_260_000
        ws.send_json({"type": "hb"})
        next_message = ws.receive_json()
        assert next_message["type"] == "state"


def test_control_ownership_expiry():
    manager = ReceiverManager("10.0.0.240", transport=StatefulTransport())
    manager.acquire_control("stale")
    manager._owner_seen -= 10_000  # simulate an expired session
    manager.acquire_control("fresh")  # TTL expiry allows takeover
    manager.release_control("fresh")
    assert manager._owner_id is None
    manager.acquire_control("a")
    try:
        manager.acquire_control("b")
    except ControlBusy:
        pass
    else:
        pytest.fail("expected ControlBusy for a fresh second client")


def test_stalled_websocket_is_pruned(tmp_path):
    """A wedge peer must not stall broadcasts (hardened _safe_send)."""

    manager = ReceiverManager(
        "10.0.0.240",
        transport=StatefulTransport(),
        poll_interval=0.05,
        data_dir=tmp_path,
    )

    class WedgeWS:
        async def send_json(self, message):
            await asyncio.sleep(10)

        async def close(self):
            pass

    async def scenario():
        wedge = WedgeWS()
        manager.websocket_join(wedge, "wedge")
        started = time.monotonic()
        await manager.poll_once()  # broadcast hits the wedge and prunes
        elapsed = time.monotonic() - started
        assert "wedge" not in manager._clients, "stalled websocket must be pruned"
        assert elapsed < 4, f"send timeout took too long: {elapsed}"

    asyncio.run(scenario())

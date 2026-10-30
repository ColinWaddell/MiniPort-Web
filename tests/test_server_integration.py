"""Opt-in live tests of the REST control surface (pytest -m integration).

Every implemented endpoint is exercised against the physical EB200 with
write-own-value discipline (settings are set to their current value only,
except the one-cycle sweep). The flock guarantees the web server is not
running against the instrument during the test.
"""

import fcntl
import os
import socket as socket_module
import time

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

HOST = os.environ.get("EB200_HOST", "10.0.0.240")
PORT = int(os.environ.get("EB200_PORT", "5555"))
LOCK_PATH = "/tmp/eb200-integration.lock"
CLIENT = "pytest-live"

GAIN_READBACK = {"FIX": "FIXed", "F": "FIXed"}


@pytest.fixture(scope="module")
def live_api():
    probe = socket_module.socket()
    probe.settimeout(2.0)
    try:
        probe.connect((HOST, PORT))
    except OSError:
        probe.close()
        pytest.skip(f"EB200 not reachable at {HOST}:{PORT}")

    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        pytest.skip("another session holds the EB200 integration lock")

    from eb200_server.app import create_app
    from eb200_server.manager import ReceiverManager

    manager = ReceiverManager(HOST, PORT, poll_interval=0.3)
    app = create_app(manager)
    with TestClient(app, raise_server_exceptions=False) as api:
        yield api
    # fixture teardown: lifespan stops the manager


def _headers():
    return {"X-Eb200-Client": CLIENT}


def _own_value(api, key):
    state = api.get("/api/state").json()["state"]
    return state[key]


def test_live_device_and_health(live_api):
    assert live_api.get("/api/health").json() == {"status": "ok"}
    deadline = time.monotonic() + 10.0
    device = None
    while time.monotonic() < deadline:
        device = live_api.get("/api/device").json()["device"]
        if device is not None:
            break
        time.sleep(0.5)
    assert device is not None and device["model"] == "EB200"
    assert live_api.get("/api/device").json()["capabilities"]["scan_supported"] is True


def test_live_control_matrix(live_api):
    live_api.post("/api/control/acquire", json={"client_id": CLIENT})

    cases = [
        ("/api/frequency", {"hz": _own_value(live_api, "freq_hz")}),
        ("/api/demod", {"mode": _own_value(live_api, "demod")}),
        ("/api/bandwidth", {"hz": _own_value(live_api, "bandwidth_hz")}),
        ("/api/gain", {"db": _own_value(live_api, "gain_db")}),
        (
            "/api/gain-mode",
            {
                "mode": GAIN_READBACK.get(
                    _own_value(live_api, "gain_mode"),
                    _own_value(live_api, "gain_mode"),
                )
            },
        ),
        ("/api/squelch", {"enabled": _own_value(live_api, "squelch")}),
        (
            "/api/squelch-threshold",
            {"dbuv": _own_value(live_api, "squelch_threshold_dbuv")},
        ),
        ("/api/volume", {"value": _own_value(live_api, "volume")}),
        (
            "/api/attenuator",
            {"auto": _own_value(live_api, "att_auto"), "enabled": None},
        ),
        ("/api/afc", {"enabled": _own_value(live_api, "afc")}),
        ("/api/operation", {"mode": _own_value(live_api, "freq_mode")}),
    ]
    before = live_api.get("/api/state").json()["state"]
    for path, body in cases:
        response = live_api.post(path, json=body, headers=_headers())
        assert response.status_code == 200, (
            f"{path} -> {response.status_code} {response.text}"
        )
    after = live_api.get("/api/state").json()["state"]

    for key in before:
        if key == "level_dbuv":
            continue  # a live measurement, expected to wobble
        assert before[key] == after[key], (
            f"state {key} drifted: {before[key]} != {after[key]}"
        )


def test_live_scan_endpoint(live_api):
    response = live_api.post(
        "/api/scan",
        json={"start_hz": 26_800_000, "stop_hz": 27_800_000, "step_hz": 25_000},
        headers=_headers(),
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["levels"]) == 41
    assert data["frequencies_hz"][0] == 26_800_000
    assert data["frequencies_hz"][-1] == 27_800_000
    assert data["restore_errors"] == []
    assert all(level is not None for level in data["levels"])
    assert all(-120 <= level <= 40 for level in data["levels"])
    assert live_api.get("/api/state").json()["state"]["freq_mode"] in ("CW", "FIX")


def test_live_second_client_lockout(live_api):
    live_api.post("/api/control/acquire", json={"client_id": "owner-a"})
    blocked = live_api.post("/api/control/acquire", json={"client_id": "owner-b"})
    assert blocked.status_code == 409
    live_api.post("/api/control/release", json={"client_id": "owner-a"})

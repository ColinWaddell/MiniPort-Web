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
def live_api(tmp_path_factory):
    store_dir = tmp_path_factory.mktemp("live-data")
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

    manager = ReceiverManager(
        HOST,
        PORT,
        poll_interval=0.3,
        data_dir=store_dir,
    )
    import faulthandler

    app = create_app(manager)
    with TestClient(app, raise_server_exceptions=False) as api:
        faulthandler.dump_traceback_later(
            70, repeat=True, file=open("/tmp/opencode/teardown-stacks.txt", "w")
        )
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


def test_live_spectrum_cycle(live_api):
    """Continuous sweep cycles + CSV export against the physical receiver."""
    live_api.post("/api/control/acquire", json={"client_id": CLIENT})
    start = live_api.post(
        "/api/spectrum/start",
        json={"start_hz": 27_000_000, "stop_hz": 27_200_000, "step_hz": 5_000},
        headers=_headers(),
    )
    assert start.status_code == 200
    deadline = time.monotonic() + 20.0
    cycles = 0
    while time.monotonic() < deadline:
        spectrum = live_api.get("/api/state").json()["spectrum"]
        cycles = spectrum.get("cycles", 0)
        if cycles >= 2:
            break
        time.sleep(0.5)
    assert cycles >= 2, "spectrum did not produce two cycles in time"

    stop = live_api.post("/api/spectrum/stop", headers=_headers())
    assert stop.status_code == 200
    assert stop.json()["running"] is False

    export = live_api.get("/api/spectrum/export.csv")
    assert export.status_code == 200
    lines = export.text.strip().splitlines()
    assert lines[0].startswith("timestamp_iso,27000000")
    assert len(lines) >= 3

    state = live_api.get("/api/state").json()["state"]
    assert state["freq_mode"] in ("CW", "FIX")


def test_live_presets(live_api):
    created = live_api.post(
        "/api/spectrum/presets",
        json={
            "name": "phase4 test band",
            "start_hz": 27_000_000,
            "stop_hz": 27_200_000,
            "step_hz": 5_000,
        },
        headers=_headers(),
    )
    assert created.status_code == 200
    preset = created.json()["preset"]
    assert (
        live_api.delete(
            f"/api/spectrum/presets/{preset['id']}", headers=_headers()
        ).status_code
        == 200
    )
    assert (
        live_api.delete(
            f"/api/spectrum/presets/{preset['id']}", headers=_headers()
        ).status_code
        == 404
    )


def test_live_audio_stream_and_recording(live_api):
    """UDP audio stream + WAV recording against the physical receiver.

    Requires the temporary ufw rule for UDP from the receiver while it
    runs (docs/protocol-notes.md, host-side note).
    """
    live_api.post("/api/control/acquire", json={"client_id": CLIENT})
    start = live_api.post("/api/audio/start", headers=_headers())
    assert start.status_code == 200
    assert start.json()["running"] is True

    deadline = time.monotonic() + 20.0
    packets = 0
    while time.monotonic() < deadline:
        packets = live_api.get("/api/audio/stats").json().get("packets", 0)
        if packets >= 20:
            break
        time.sleep(0.5)
    stats = live_api.get("/api/audio/stats").json()
    assert packets >= 20, "no audio packets; check the ufw rule"
    rate = stats.get("rate_hz")
    assert rate is None or 7000 <= rate <= 16000

    started = live_api.post("/api/audio/record/start", headers=_headers())
    assert started.status_code == 200
    time.sleep(1.8)
    stopped = live_api.post("/api/audio/record/stop", headers=_headers())
    assert stopped.status_code == 200
    name = stopped.json()["name"]
    assert stopped.json()["samples"] > 100

    download = live_api.get(f"/api/audio/recordings/{name}")
    assert download.status_code == 200
    wav = download.content
    assert wav[:4] == b"RIFF" and len(wav) > 2000

    # Cleanup: delete the recording, restore receiver state.
    deleted = live_api.delete(f"/api/audio/recordings/{name}", headers=_headers())
    assert deleted.status_code == 200
    finished = live_api.post("/api/audio/stop", headers=_headers())
    assert finished.status_code == 200
    assert finished.json()["running"] is False
    state = live_api.get("/api/state").json()["state"]
    assert state["remote_af_mode"] == 0


def test_live_presets_apply(live_api):
    """Receiver preset create/apply/delete against the physical receiver."""
    live_api.post("/api/control/acquire", json={"client_id": CLIENT})
    state = live_api.get("/api/state").json()["state"]
    created = live_api.post(
        "/api/presets",
        json={
            "name": "phase6 spot",
            "frequency_hz": state["freq_hz"],
            "demod": state["demod"],
            "bandwidth_hz": state["bandwidth_hz"],
        },
        headers=_headers(),
    )
    assert created.status_code == 200
    preset_id = created.json()["preset"]["id"]

    # detune, then let the preset return the receiver
    detune = live_api.post(
        "/api/frequency", json={"hz": 27_500_000}, headers=_headers()
    )
    assert detune.status_code == 200
    assert live_api.get("/api/state").json()["state"]["freq_hz"] == 27_500_000
    applied = live_api.post(f"/api/presets/{preset_id}/apply", headers=_headers())
    assert applied.status_code == 200
    assert live_api.get("/api/state").json()["state"]["freq_hz"] == state["freq_hz"]
    assert (
        live_api.delete(f"/api/presets/{preset_id}", headers=_headers()).status_code
        == 200
    )


def test_live_level_log_and_sweep_job(live_api):
    """Level logging + a manual sweep job run + export, live."""
    live_api.post("/api/control/acquire", json={"client_id": CLIENT})
    assert live_api.post("/api/level-log", json={"enabled": True}).status_code == 200
    started = live_api.get("/api/level-log").json()["rows"]

    job = live_api.post(
        "/api/jobs",
        json={
            "name": "live monitored band",
            "kind": "sweep",
            "params": {
                "start_hz": 27_000_000,
                "stop_hz": 27_200_000,
                "step_hz": 5_000,
            },
            "interval_s": 600,
        },
        headers=_headers(),
    )
    assert job.status_code == 200
    job_id = job.json()["job"]["id"]

    manual = live_api.post(f"/api/jobs/{job_id}/run", headers=_headers())
    assert manual.status_code == 200
    sweep = live_api.get("/api/sweeps").json()["sweeps"][0]
    export = live_api.get(f"/api/sweeps/{sweep['id']}/export.csv")
    assert export.status_code == 200
    assert export.text.startswith("timestamp_iso,frequency_hz,level_dbuv")
    assert len(export.text.splitlines()) == sweep["points"] + 1

    time.sleep(5.5)  # level logger period (5 s)
    rows = live_api.get("/api/level-log").json()
    assert rows["rows"] >= started + 1

    csv = live_api.get("/api/level-log/export.csv")
    assert csv.status_code == 200
    live_json = live_api.get("/api/level-log/export.json")
    assert live_json.status_code == 200 and live_json.json()["samples"]

    assert live_api.post("/api/level-log", json={"enabled": False}).status_code == 200
    assert (
        live_api.post(f"/api/jobs/{job_id}/disable", headers=_headers()).status_code
        == 200
    )

"""SQLite store and automation service tests (phase 6)."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from eb200.sim import StatefulTransport
from eb200_server.app import create_app
from eb200_server.automation import LevelLogger, Scheduler, TriggerRecorder
from eb200_server.db import Store
from eb200_server.manager import ReceiverManager
from eb200_server.spectrum import decode_frame

CLIENT = "automation-test"
HEADERS = {"X-Eb200-Client": CLIENT}


# -- store ------------------------------------------------------------------


def test_store_presets_roundtrip(tmp_path):
    store = Store(tmp_path / "test.db")
    one = store.add_preset(
        "receiver", "2m band", {"frequency_hz": 145_500_000, "demod": "FM"}
    )
    assert one["id"] == 1
    store.add_preset(
        "spectrum", "FM band", {"start_hz": 87.5e6, "stop_hz": 108e6, "step_hz": 1e5}
    )
    assert len(store.presets("receiver")) == 1
    assert len(store.presets("spectrum")) == 1
    assert len(store.presets(None)) == 2
    assert store.delete_preset(1) is True


def test_store_job_due_times(tmp_path):
    store = Store(tmp_path / "test.db")
    job = store.add_job(
        "bw sweep",
        "sweep",
        {"start_hz": 27e6, "stop_hz": 27.1e6, "step_hz": 5e3},
        interval_s=10,
    )
    assert store.due_jobs(now=0) != []
    store.mark_job_run(job["id"], "ok")
    last_run = store.jobs()[0]["last_run"]
    assert store.due_jobs(now=last_run + 5) == []
    assert store.due_jobs(now=last_run + 10) != []

    job_id = job["id"]
    store.set_job_enabled(job_id, False)
    assert store.due_jobs(now=10**9) == []
    store.set_job_enabled(job_id, True)

    daily = store.add_job(
        "morning record", "audio-record", {"duration_s": 60}, daily="09:30"
    )
    at_0830 = time.mktime(time.strptime("2026-10-30 08:30:00", "%Y-%m-%d %H:%M:%S"))
    at_1000 = time.mktime(time.strptime("2026-10-30 10:00:00", "%Y-%m-%d %H:%M:%S"))
    assert store.due_jobs(now=at_0830) == []
    assert [j["id"] for j in store.due_jobs(now=at_1000)] == [daily["id"]]


def test_store_level_log_and_retention(tmp_path):
    store = Store(tmp_path / "test.db")
    now = time.time()
    store.log_levels(
        [
            (now - 100_000, 27e6, -18.1),  # older than a day: retention target
            (now - 5, 27e6, -17.4),
            (now, 27e6, -17.1),
        ]
    )
    assert store.level_log_count() == 3
    recent = store.levels_since(now - 6)
    assert len(recent) == 2
    removed = store.retain_levels(days=1)
    assert removed == 1
    assert store.level_log_count() == 2


def test_store_sweeps_with_files(tmp_path):
    store = Store(tmp_path / "test.db")
    sweep_file = tmp_path / "sweep-1.bin"
    sweep_file.write_bytes(b"SPW1-frame")
    store.add_sweep(time.time(), 27e6, 27.1e6, 5e3, 21, str(sweep_file))
    assert len(store.sweeps()) == 1
    sweep = store.sweeps()[0]
    assert store.delete_sweep(sweep["id"], remove_file=True) is True
    assert not sweep_file.exists()


def test_store_recordings_rows(tmp_path):
    store = Store(tmp_path / "test.db")
    path = tmp_path / "rec.wav"
    path.write_bytes(b"RIFF")
    assert store.add_recording("rec.wav", str(path)) == 1
    store.update_recording("rec.wav", bytes_count=100, duration=6.1, rate_hz=8000)
    row = store.get_recording("rec.wav")
    assert row["bytes"] == 100 and row["duration_s"] == 6.1
    assert store.delete_recording_row("rec.wav", remove_file=True) is True
    assert not path.exists()


def test_store_settings(tmp_path):
    store = Store(tmp_path / "test.db")
    assert store.get_setting("missing", "fallback") == "fallback"
    store.set_setting("key", "1")
    store.set_setting("key", "2")
    assert store.get_setting("key", "") == "2"


# -- services ------------------------------------------------------------------


def test_level_logger_period_and_disable(tmp_path):
    store = Store(tmp_path / "test.db")
    logger = LevelLogger(store, period_s=5.0)
    logger.configure(enabled=True)
    base = time.time()
    logger.ingest(base, 27_260_000, -18.3)
    logger.ingest(base + 2, 27_260_000, -18.1)  # inside the period
    logger.ingest(base + 6, 27_260_000, -18.0)
    assert store.level_log_count() == 2
    logger.configure(enabled=False)
    logger.ingest(base + 20, 27_260_000, -18.0)
    logger.ingest(base + 40, 27_260_000, -18.0)
    assert store.level_log_count() == 2  # disabled: no more rows


async def _trigger_scenario(store):
    calls: list[float] = []
    trigger = TriggerRecorder(store)

    async def action(duration: float) -> dict:
        calls.append(duration)
        return {"name": "auto.wav"}

    trigger.set_action(action)
    trigger.configure(armed=True, threshold_dbuv=-25, duration_s=10, cooldown_s=30)
    await trigger.ingest(1000.0, -19.0)  # above threshold → fires
    await trigger.ingest(1010.0, -10.0)  # cooldown holds
    await trigger.ingest(2000.0, -30.0)  # below threshold
    assert calls == [10.0]


def test_trigger_recorder_threshold_and_cooldown(tmp_path):
    store = Store(tmp_path / "test.db")
    asyncio.run(_trigger_scenario(store))
    assert store.recent_runs()[0]["status"] == "ok"


async def _job_scenarios(tmp_path):
    transport = StatefulTransport()
    manager = ReceiverManager(
        "10.0.0.240", transport=transport, poll_interval=0.05, data_dir=tmp_path
    )
    store = manager.store
    sweep = store.add_job(
        "monitor band",
        "sweep",
        {"start_hz": 27_000_000, "stop_hz": 27_100_000, "step_hz": 5_000},
        interval_s=600,
    )
    audio = store.add_job(
        "record 0.3s", "audio-record", {"duration_s": 0.3}, interval_s=600
    )
    scheduler = Scheduler(store, manager, tmp_path / "sweeps")
    try:
        await scheduler.run_job_now(store.jobs()[0])
        await scheduler.run_job_now(
            next(j for j in store.jobs() if j["id"] == audio["id"])
        )
        statuses = {run["job_id"]: run["status"] for run in store.recent_runs()}
        assert statuses[sweep["id"]] == "ok"
        assert statuses[audio["id"]] == "ok"
        sweeps = store.sweeps()
        assert len(sweeps) == 1
        decoded = decode_frame(
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: __import__("pathlib").Path(sweeps[0]["path"]).read_bytes()
            )
        )
        assert len(decoded["levels"]) == 21
        # audio job engaged and cleanly tore down the stream
        assert manager.audio_status()["running"] is False
        assert device_state(manager).remote_af_mode() == 0
    finally:
        await manager.aclose()


def device_state(manager):
    return manager._device


def test_scheduler_jobs(tmp_path):
    asyncio.run(_job_scenarios(tmp_path))


# -- REST surface ----------------------------------------------------------------


def _api(tmp_path):
    manager = ReceiverManager(
        "10.0.0.240",
        transport=StatefulTransport(),
        poll_interval=0.05,
        data_dir=tmp_path,
    )
    app = create_app(manager)
    with TestClient(app) as api:
        yield api


@pytest.fixture
def api(tmp_path):
    yield from _api(tmp_path)


def test_rest_presets_apply(api):
    assert (
        api.post("/api/control/acquire", json={"client_id": CLIENT}).status_code == 200
    )
    created = api.post(
        "/api/presets",
        json={
            "name": "test tune",
            "frequency_hz": 145_500_000,
            "demod": "FM",
            "bandwidth_hz": 15_000,
        },
        headers=HEADERS,
    )
    assert created.status_code == 200
    apply_result = api.post("/api/presets/1/apply", headers=HEADERS)
    assert apply_result.status_code == 200
    state = api.get("/api/state").json()["state"]
    assert state["freq_hz"] == 145_500_000
    assert state["demod"] == "FM"
    assert (
        api.post(
            "/api/frequency", json={"hz": 100_000_000}, headers=HEADERS
        ).status_code
        == 200
    )


def test_rest_jobs_and_level_log(api):
    api.post("/api/control/acquire", json={"client_id": CLIENT})
    job = api.post(
        "/api/jobs",
        json={
            "name": "test sweep",
            "kind": "sweep",
            "params": {"start_hz": 27e6, "stop_hz": 27.1e6, "step_hz": 5e3},
            "interval_s": 600,
        },
        headers=HEADERS,
    )
    assert job.status_code == 200
    job_id = job.json()["job"]["id"]
    assert api.post(f"/api/jobs/{job_id}/disable", headers=HEADERS).status_code == 200
    assert api.post(f"/api/jobs/{job_id}/enable", headers=HEADERS).status_code == 200
    assert api.get("/api/jobs").json()["jobs"][0]["kind"] == "sweep"

    level_config = api.post("/api/level-log", json={"enabled": True, "period_s": 1})
    assert level_config.status_code == 200
    time.sleep(1.1)
    assert api.get("/api/level-log").json()["enabled"] is True
    csv = api.get("/api/level-log/export.csv")
    assert csv.status_code == 200 and csv.text.startswith("timestamp_iso")
    export_json = api.get("/api/level-log/export.json")
    assert len(export_json.json()["samples"]) >= 1

    manual_run = api.post(f"/api/jobs/{job_id}/run", headers=HEADERS)
    assert manual_run.status_code == 200
    assert api.get("/api/sweeps").json()["sweeps"]
    assert api.get("/api/sweeps/1/export.csv").status_code == 200
    assert api.post("/api/jobs/999/run", headers=HEADERS).status_code == 404
    # hygiene: leave the store without enabled jobs
    assert api.post(f"/api/jobs/{job_id}/disable", headers=HEADERS).status_code == 200


def test_rest_trigger_config(api):
    configured = api.post(
        "/api/trigger",
        json={"armed": True, "threshold_dbuv": -25, "duration_s": 5, "cooldown_s": 10},
    )
    assert configured.status_code == 200
    body = configured.json()
    assert body["armed"] is True and body["threshold_dbuv"] == -25
    assert api.post("/api/trigger", json={"armed": False}).json()["armed"] is False

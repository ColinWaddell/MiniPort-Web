"""Automation services: level logger, trigger recorder and the scheduler.

All three are long-running pieces that work independently of browser
connections. The level logger and trigger recorder hook into the
manager's poll loop (no extra receiver traffic); the scheduler runs due
jobs from the SQLite store and enforces retention. Device-touching work
runs through the manager (receiver lock, ownership as the server's own
client).
"""

from __future__ import annotations

import asyncio
import logging
import time

from eb200_server.db import Store
from eb200_server.spectrum import SpectrumParams, encode_frame

log = logging.getLogger(__name__)


class LevelLogger:
    """Persists periodic level samples taken from the poll loop."""

    def __init__(self, store: Store, period_s: float = 5.0) -> None:
        self._store = store
        self._period_s = period_s
        self._last_write = 0.0

    # -- settings-backed configuration ----------------------------------------

    @property
    def enabled(self) -> bool:
        return self._store.get_setting("level_log_enabled", "0") == "1"

    @property
    def period_s(self) -> float:
        value = float(self._store.get_setting("level_log_period", str(self._period_s)))
        return max(0.5, value)

    def configure(
        self, *, enabled: bool | None = None, period_s: float | None = None
    ) -> dict:
        if enabled is not None:
            self._store.set_setting("level_log_enabled", "1" if enabled else "0")
        if period_s is not None:
            if not 0.5 <= period_s <= 3600:
                raise ValueError("level log period must be 0.5-3600 s")
            self._store.set_setting("level_log_period", f"{period_s:g}")
        return self.status()

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "period_s": self.period_s,
            "rows": self._store.level_log_count(),
        }

    def ingest(self, timestamp: float, frequency_hz: int, level_dbuv: float) -> None:
        if not self.enabled:
            return
        if timestamp - self._last_write < self.period_s:
            return
        self._last_write = timestamp
        self._store.log_levels([(timestamp, frequency_hz, level_dbuv)])


class TriggerRecorder:
    """Automatic audio recording when the current level crosses a threshold.

    Configuration lives in the settings table. The manager calls
    :meth:`ingest` with every poll sample; when the trigger fires it asks
    the manager to conduct a triggered audio recording for the configured
    duration, then observes the cooldown.
    """

    def __init__(self, store: Store, trigger_action=None) -> None:
        self._store = store
        self._last_triggered = 0.0
        self._trigger_action = trigger_action  # manager coroutine setter

    def set_action(self, action) -> None:
        self._trigger_action = action

    @property
    def armed(self) -> bool:
        return self._store.get_setting("trigger_armed", "0") == "1"

    def configure(
        self,
        *,
        armed: bool | None = None,
        threshold_dbuv: float | None = None,
        duration_s: float | None = None,
        cooldown_s: float | None = None,
    ) -> dict:
        if armed is not None:
            self._store.set_setting("trigger_armed", "1" if armed else "0")
        if threshold_dbuv is not None:
            self._store.set_setting("trigger_threshold_dbuv", f"{threshold_dbuv:g}")
        if duration_s is not None:
            if not 1 <= duration_s <= 600:
                raise ValueError("trigger duration must be 1-600 s")
            self._store.set_setting("trigger_duration_s", f"{duration_s:g}")
        if cooldown_s is not None:
            if not 0 <= cooldown_s <= 3600:
                raise ValueError("trigger cooldown must be 0-3600 s")
            self._store.set_setting("trigger_cooldown_s", f"{cooldown_s:g}")
        return self.status()

    def status(self) -> dict:
        return {
            "armed": self.armed,
            "threshold_dbuv": float(
                self._store.get_setting("trigger_threshold_dbuv", "-30")
            ),
            "duration_s": float(self._store.get_setting("trigger_duration_s", "30")),
            "cooldown_s": float(self._store.get_setting("trigger_cooldown_s", "60")),
            "last_triggered": self._last_triggered,
        }

    async def ingest(self, timestamp: float, level_dbuv: float) -> None:
        if not self.armed or self._trigger_action is None:
            return
        if timestamp - self._last_triggered < self.status()["cooldown_s"]:
            return
        if level_dbuv is None or level_dbuv < self.status()["threshold_dbuv"]:
            return
        self._last_triggered = timestamp
        try:
            result = await self._trigger_action(
                float(self._store.get_setting("trigger_duration_s", "30"))
            )
            self._store.record_run(
                0,
                "ok",
                f"triggered recording {result.get('name', '')}",
            )
        except Exception as exc:  # noqa: BLE001 - never kill polls
            log.warning("triggered recording failed: %s", exc)
            self._store.record_run(0, "error", str(exc))


class Scheduler:
    """Runs due jobs and enforces retention, independent of any browser."""

    def __init__(
        self, store: Store, manager, sweeps_dir, interval_s: float = 20.0
    ) -> None:
        self._store = store
        self._manager = manager
        self._sweeps_dir = sweeps_dir
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None
        self._last_retention = time.time()
        sweeps_dir.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(
            self._run(), name="eb200-scheduler"
        )

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:  # noqa: PERF203
                pass

    async def _run(self) -> None:
        while True:
            try:
                for job in self._store.due_jobs():
                    await self.dispatch(job)
                if time.time() - self._last_retention > 3600:
                    self._last_retention = time.time()
                    removed_levels = self._store.retain_levels(
                        float(self._manager.settings.get("level_retention_days", 7))
                    )
                    removed_sweeps = self._store.retain_sweeps(
                        float(self._manager.settings.get("sweep_retention_days", 30))
                    )
                    removed_recordings = self._store.retain_recordings(
                        float(
                            self._manager.settings.get("recording_retention_days", 30)
                        )
                    )
                    if removed_levels or removed_sweeps or removed_recordings:
                        log.info(
                            "retention removed %s levels, %s sweeps, %s recordings",
                            removed_levels,
                            removed_sweeps,
                            removed_recordings,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - scheduler keeps running
                log.exception("scheduler error")
            await asyncio.sleep(self._interval_s)

    async def run_job_now(self, job: dict) -> dict:
        await self.dispatch(job)
        runs = self._store.recent_runs(limit=5)
        return {"done": True, "recent": runs}

    async def dispatch(self, job: dict) -> dict:
        kind = job["kind"]
        params = job["params"]
        try:
            if kind == "sweep":
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: self._manager._device.scan_sweep(
                        int(params["start_hz"]),
                        int(params["stop_hz"]),
                        int(params["step_hz"]),
                        timeout=30.0,
                    ),
                )
                self._store_sweep(result.levels, result.frequencies, params, job["id"])
                self._store.record_run(job["id"], "ok", f"{len(result.levels)} points")
            elif kind == "audio-record":
                recording = await self._manager.trigger_recording(
                    float(params.get("duration_s", 30)),
                    job_id=job["id"],
                    trigger="job",
                )
                self._store.record_run(
                    job["id"],
                    "ok",
                    f"recorded {recording.get('name', '')}",
                )
            else:
                self._store.record_run(job["id"], "error", f"unknown kind {kind!r}")
        except Exception as exc:  # noqa: BLE001 - failures are logged, not raised
            self._store.record_run(job["id"], "error", str(exc))
            log.warning("job %s (%s) failed: %s", job["name"], kind, exc)
        return {"job_id": job["id"], "kind": kind}

    def _store_sweep(self, levels, frequencies, params, job_id) -> int:
        timestamp = time.time()
        payload = encode_frame(
            timestamp,
            SpectrumParams(
                int(params["start_hz"]),
                int(params["stop_hz"]),
                int(params["step_hz"]),
            ),
            list(levels),
            None,
        )
        path = self._sweeps_dir / f"sweep-{int(timestamp * 1000)}.bin"
        path.write_bytes(payload)
        return self._store.add_sweep(
            timestamp,
            int(params["start_hz"]),
            int(params["stop_hz"]),
            int(params["step_hz"]),
            len(frequencies),
            str(path),
        )

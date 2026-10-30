"""SQLite store for presets, scheduled jobs, level history, sweeps and
recordings. Configuration/metadata only — the actual measurement payload
and audio files live on disk, referenced from here. WAL mode keeps
writes cheap on frequently-rewritten state."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    params TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    params TEXT NOT NULL,
    interval_s INTEGER,
    daily TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run REAL
);
CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    status TEXT NOT NULL,
    message TEXT
);
CREATE INDEX IF NOT EXISTS job_runs_job ON job_runs(job_id, id);
CREATE TABLE IF NOT EXISTS level_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    frequency_hz INTEGER NOT NULL,
    level_dbuv REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS level_log_ts ON level_log(ts);
CREATE INDEX IF NOT EXISTS level_log_freq ON level_log(frequency_hz);
CREATE TABLE IF NOT EXISTS sweeps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    start_hz INTEGER NOT NULL,
    stop_hz INTEGER NOT NULL,
    step_hz INTEGER NOT NULL,
    points INTEGER NOT NULL,
    path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    name TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    bytes INTEGER NOT NULL DEFAULT 0,
    duration_s REAL NOT NULL DEFAULT 0,
    rate_hz INTEGER NOT NULL DEFAULT 0,
    trigger TEXT NOT NULL DEFAULT 'manual',
    job_id INTEGER
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> float:
    return time.time()


class Store:
    """Thread-safe SQLite access; one connection, serialized writes."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _insert(self, sql: str, params: tuple = ()) -> int:
        """Statement that inserts a row; returns the new row id."""
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor.lastrowid

    def _execute(self, sql: str, params: tuple = ()) -> int:
        """Other statements; returns the number of affected rows."""
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor.rowcount

    # -- presets ---------------------------------------------------------------

    def presets(self, kind: str | None = None) -> list[dict]:
        rows = (
            self._query("SELECT * FROM presets WHERE kind=? ORDER BY id", (kind,))
            if kind is not None
            else self._query("SELECT * FROM presets ORDER BY id")
        )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "params": json.loads(row["params"]),
            }
            for row in rows
        ]

    def add_preset(self, kind: str, name: str, params: dict) -> dict:
        preset_id = self._insert(
            "INSERT INTO presets (name, kind, params) VALUES (?, ?, ?)",
            (name, kind, json.dumps(params)),
        )
        return {"id": preset_id, "name": name, "kind": kind, "params": params}

    def delete_preset(self, preset_id: int) -> bool:
        return self._execute("DELETE FROM presets WHERE id = ?", (preset_id,)) > 0

    # -- scheduled jobs ----------------------------------------------------------

    def jobs(self) -> list[dict]:
        rows = self._query("SELECT * FROM jobs ORDER BY id")
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "params": json.loads(row["params"]),
                "interval_s": row["interval_s"],
                "daily": row["daily"],
                "enabled": bool(row["enabled"]),
                "last_run": row["last_run"],
            }
            for row in rows
        ]

    def add_job(
        self,
        name: str,
        kind: str,
        params: dict,
        *,
        interval_s: int | None = None,
        daily: str | None = None,
    ) -> dict:
        if bool(interval_s) == bool(daily):
            raise ValueError("exactly one of interval_s or daily is required")
        if interval_s is not None and interval_s < 5:
            raise ValueError("interval_s must be at least 5")
        job_id = self._insert(
            "INSERT INTO jobs (name, kind, params, interval_s, daily) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, kind, json.dumps(params), interval_s, daily),
        )
        return {
            "id": job_id,
            "name": name,
            "kind": kind,
            "params": params,
            "interval_s": interval_s,
            "daily": daily,
            "enabled": True,
            "last_run": None,
        }

    def set_job_enabled(self, job_id: int, enabled: bool) -> bool:
        return (
            self._execute(
                "UPDATE jobs SET enabled = ? WHERE id = ?", (int(enabled), job_id)
            )
            > 0
        )

    def delete_job(self, job_id: int) -> bool:
        self._execute("DELETE FROM job_runs WHERE job_id = ?", (job_id,))
        return self._execute("DELETE FROM jobs WHERE id = ?", (job_id,)) > 0

    def mark_job_run(self, job_id: int, status: str, message: str = "") -> None:
        self._execute("UPDATE jobs SET last_run = ? WHERE id = ?", (_now(), job_id))
        self._insert(
            "INSERT INTO job_runs (job_id, ts, status, message) VALUES (?, ?, ?, ?)",
            (job_id, _now(), status, message[:500]),
        )

    def due_jobs(self, now: float | None = None) -> list[dict]:
        now = now if now is not None else _now()
        due: list[dict] = []
        for job in self.jobs():
            if not job["enabled"]:
                continue
            if job["interval_s"] is not None:
                if (
                    job["last_run"] is None
                    or now - job["last_run"] >= job["interval_s"]
                ):
                    due.append(job)
            elif job["daily"] is not None:
                scheduled = _daily_timestamp(job["daily"], now)
                if scheduled <= now and (
                    job["last_run"] is None or job["last_run"] < scheduled
                ):
                    due.append(job)
        return due

    def record_run(self, job_id: int, status: str, message: str = "") -> None:
        self.mark_job_run(job_id, status, message)

    def recent_runs(self, limit: int = 50) -> list[dict]:
        rows = self._query("SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,))
        return [
            {
                "job_id": r["job_id"],
                "ts": r["ts"],
                "status": r["status"],
                "message": r["message"],
            }
            for r in rows
        ]

    # -- level log ---------------------------------------------------------------

    def log_levels(self, samples: list[tuple[float, int, float]]) -> int:
        if not samples:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT INTO level_log (ts, frequency_hz, level_dbuv) VALUES (?, ?, ?)",
                [(ts, freq, level) for ts, freq, level in samples],
            )
            self._conn.commit()
        return len(samples)

    def levels_since(self, since: float, limit: int = 50_000) -> list[dict]:
        rows = self._query(
            "SELECT ts, frequency_hz, level_dbuv FROM level_log "
            "WHERE ts > ? ORDER BY id LIMIT ?",
            (since, limit),
        )
        return [
            {
                "ts": r["ts"],
                "frequency_hz": r["frequency_hz"],
                "level_dbuv": r["level_dbuv"],
            }
            for r in rows
        ]

    def retain_levels(self, days: float) -> int:
        cutoff = _now() - days * 86400
        return self._execute("DELETE FROM level_log WHERE ts < ?", (cutoff,))

    def level_log_count(self) -> int:
        return self._query("SELECT COUNT(*) AS n FROM level_log")[0]["n"]

    # -- sweep history ---------------------------------------------------------------

    def add_sweep(
        self,
        ts: float,
        start_hz: int,
        stop_hz: int,
        step_hz: int,
        points: int,
        path: str,
    ) -> int:
        return self._insert(
            "INSERT INTO sweeps (ts, start_hz, stop_hz, step_hz, points, path) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, start_hz, stop_hz, step_hz, points, path),
        )

    def sweeps(self, limit: int = 100) -> list[dict]:
        rows = self._query("SELECT * FROM sweeps ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def get_sweep(self, sweep_id: int) -> dict | None:
        rows = self._query("SELECT * FROM sweeps WHERE id = ?", (sweep_id,))
        return dict(rows[0]) if rows else None

    def delete_sweep(self, sweep_id: int, *, remove_file: bool = False) -> bool:
        rows = self._query("SELECT path FROM sweeps WHERE id = ?", (sweep_id,))
        deleted = self._execute("DELETE FROM sweeps WHERE id = ?", (sweep_id,)) > 0
        if deleted and remove_file and rows:
            try:
                Path(rows[0]["path"]).unlink(missing_ok=True)
            except OSError:
                pass
        return deleted

    def retain_sweeps(self, days: float) -> int:
        cutoff = _now() - days * 86400
        rows = self._query("SELECT id FROM sweeps WHERE ts < ?", (cutoff,))
        for row in rows:
            self.delete_sweep(row["id"], remove_file=True)
        return len(rows)

    # -- recordings ----------------------------------------------------------------

    def add_recording(
        self, name: str, path: str, trigger: str = "manual", job_id: int | None = None
    ) -> int:
        return self._insert(
            "INSERT OR IGNORE INTO recordings (ts, name, path, trigger, job_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (_now(), name, path, trigger, job_id),
        )

    def update_recording(
        self, name: str, *, bytes_count: int, duration: float, rate_hz: int
    ) -> None:
        self._execute(
            "UPDATE recordings SET bytes = ?, duration_s = ?, rate_hz = ? "
            "WHERE name = ?",
            (bytes_count, duration, rate_hz, name),
        )

    def recordings(self, limit: int = 200) -> list[dict]:
        rows = self._query(
            "SELECT * FROM recordings ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    def get_recording(self, name: str) -> dict | None:
        rows = self._query("SELECT * FROM recordings WHERE name = ?", (name,))
        return dict(rows[0]) if rows else None

    def delete_recording_row(self, name: str, *, remove_file: bool = False) -> bool:
        rows = self._query("SELECT path FROM recordings WHERE name = ?", (name,))
        deleted = self._execute("DELETE FROM recordings WHERE name = ?", (name,)) > 0
        if deleted and remove_file and rows:
            try:
                Path(rows[0]["path"]).unlink(missing_ok=True)
            except OSError:
                pass
        return deleted

    def retain_recordings(self, days: float) -> int:
        cutoff = _now() - days * 86400
        rows = self._query(
            "SELECT name FROM recordings WHERE ts < ? AND trigger != 'manual'",
            (cutoff,),
        )
        for row in rows:
            self.delete_recording_row(row["name"], remove_file=True)
        return len(rows)

    # -- settings ------------------------------------------------------------------

    def get_setting(self, key: str, default: str) -> str:
        rows = self._query("SELECT value FROM settings WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def set_setting(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def _daily_timestamp(daily: str, now: float) -> float:
    """Timestamp of today's HH:MM schedule (before now)."""
    hour, minute = (int(part) for part in daily.split(":"))
    local = time.strftime("%Y-%m-%d 00:00:00", time.localtime(now))
    base = time.mktime(time.strptime(local, "%Y-%m-%d %H:%M:%S"))
    return base + hour * 3600 + minute * 60

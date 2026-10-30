"""JSON-file store for scan presets and spectrum CSV export.

An interim store for phase 4; a database arrives with the unattended
operation phase.
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path


class PresetStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._entries = []
            return
        with self._path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        self._entries = [
            {
                "id": int(entry["id"]),
                "name": str(entry["name"]),
                "start_hz": int(entry["start_hz"]),
                "stop_hz": int(entry["stop_hz"]),
                "step_hz": int(entry["step_hz"]),
            }
            for entry in data.get("presets", [])
        ]

    def _save(self) -> None:
        with self._path.open("w", encoding="utf-8") as fh:
            json.dump({"presets": self._entries}, fh, indent=2)

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(entry) for entry in self._entries]

    def add(self, name: str, start_hz: int, stop_hz: int, step_hz: int) -> dict:
        with self._lock:
            next_id = max((entry["id"] for entry in self._entries), default=0) + 1
            entry = {
                "id": next_id,
                "name": name,
                "start_hz": start_hz,
                "stop_hz": stop_hz,
                "step_hz": step_hz,
            }
            self._entries.append(entry)
            self._save()
            return dict(entry)

    def delete(self, preset_id: int) -> bool:
        with self._lock:
            before = len(self._entries)
            self._entries = [
                entry for entry in self._entries if entry["id"] != preset_id
            ]
            changed = len(self._entries) != before
            if changed:
                self._save()
            return changed


def csv_export(
    frequencies_hz: list[int],
    history: list[tuple[float, list[float | None]]],
) -> str:
    """Export the waterfall history as CSV (levels in dBµV, gaps empty)."""
    header = "timestamp_iso," + ",".join(str(freq) for freq in frequencies_hz)
    rows = [header]
    for timestamp, levels in history:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(timestamp))
        cells = [stamp] + [
            "" if level is None or math.isnan(level) else f"{level:.1f}"
            for level in levels
        ]
        rows.append(",".join(cells))
    return "\n".join(rows) + "\n"

"""Server configuration: defaults < JSON config file < environment < CLI.

Every value can be overridden; precedence from weakest to strongest:
:class:`ServerConfig` defaults, the JSON file (--config/…), environment
variables (EB200_*), and explicit CLI arguments.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

_ENV_MAP: dict[str, tuple[str, type]] = {
    "EB200_WEB_HOST": ("web_host", str),
    "EB200_WEB_PORT": ("web_port", int),
    "EB200_HOST": ("receiver_host", str),
    "EB200_PORT": ("receiver_port", int),
    "EB200_POLL_INTERVAL": ("poll_interval", float),
    "EB200_DATA_DIR": ("data_dir", str),
    "EB200_LOG_LEVEL": ("log_level", str),
    "EB200_LOG_FORMAT": ("log_format", str),
    "EB200_LEVEL_RETENTION_DAYS": ("level_retention_days", int),
    "EB200_SWEEP_RETENTION_DAYS": ("sweep_retention_days", int),
    "EB200_RECORDING_RETENTION_DAYS": ("recording_retention_days", int),
}

_DAILY_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


@dataclass
class ServerConfig:
    web_host: str = "127.0.0.1"  # per the brief: never expose publicly by default
    web_port: int = 8000
    receiver_host: str = "10.0.0.240"
    receiver_port: int = 5555
    poll_interval: float = 0.3
    data_dir: str = ""  # empty = repository/default data directory
    log_level: str = "INFO"
    log_format: str = "text"  # "json" for structured logging
    level_retention_days: int = 7
    sweep_retention_days: int = 30
    recording_retention_days: int = 30

    def data_path(self, base: Path) -> Path:
        return Path(self.data_dir) if self.data_dir else base


def load_config(
    config_file: str | None = None,
    overrides: dict | None = None,
) -> ServerConfig:
    values: dict[str, object] = {}

    if config_file:
        path = Path(config_file)
        if not path.is_file():
            raise FileNotFoundError(f"config file not found: {config_file}")
        file_values = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(file_values, dict):
            raise ValueError(f"config file must contain a JSON object: {config_file}")
        known = ServerConfig().__dict__
        for key, value in file_values.items():
            if key not in known:
                raise ValueError(f"unknown config key {key!r} in {config_file}")
            values[key] = value

    for env_key, (key, cast) in _ENV_MAP.items():
        if env_key in os.environ:
            values[key] = cast(os.environ[env_key])

    if overrides:
        values.update({k: v for k, v in overrides.items() if v is not None})

    config = ServerConfig(**values)  # type: ignore[arg-type]
    validate(config)
    return config


def validate(config: ServerConfig) -> None:
    errors: list[str] = []
    if not 1 <= config.web_port <= 65535:
        errors.append(f"web_port {config.web_port} outside 1-65535")
    if not 1 <= config.receiver_port <= 65535:
        errors.append(f"receiver_port {config.receiver_port} outside 1-65535")
    if not 0.1 <= config.poll_interval <= 5.0:
        errors.append(f"poll_interval {config.poll_interval} outside 0.1-5.0")
    if config.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        errors.append(f"log_level {config.log_level!r} not in DEBUG/INFO/WARNING/ERROR")
    if config.log_format not in {"text", "json"}:
        errors.append(f"log_format {config.log_format!r} not in text/json")
    if errors:
        raise ValueError("; ".join(errors))

"""Configuration layer tests (defaults < file < env < CLI)."""

import json
from pathlib import Path

import pytest

from eb200_server.__main__ import setup_logging
from eb200_server.config import ServerConfig, load_config


def test_defaults_are_lan_safe():
    config = ServerConfig()
    assert config.web_host == "127.0.0.1"
    assert config.receiver_host == "10.0.0.240"
    assert config.receiver_port == 5555
    assert config.log_format == "text"


def test_config_file_overrides_defaults(tmp_path: Path):
    file = tmp_path / "config.json"
    file.write_text(json.dumps({"web_port": 8330, "log_format": "json"}))
    config = load_config(str(file))
    assert config.web_port == 8330
    assert config.log_format == "json"
    assert config.receiver_host == "10.0.0.240"


def test_environment_beats_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    file = tmp_path / "config.json"
    file.write_text(json.dumps({"web_port": 8330}))
    monkeypatch.setenv("EB200_WEB_PORT", "9001")
    config = load_config(str(file))
    assert config.web_port == 9001
    monkeypatch.setenv("EB200_LEVEL_RETENTION_DAYS", "3")
    config = load_config(str(file))
    assert config.level_retention_days == 3


def test_cli_overrides_beat_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    file = tmp_path / "config.json"
    file.write_text(json.dumps({"web_port": 8330}))
    monkeypatch.setenv("EB200_WEB_PORT", "9001")
    config = load_config(str(file), overrides={"web_port": 9002})
    assert config.web_port == 9002


def test_unknown_file_keys_rejected(tmp_path: Path):
    file = tmp_path / "config.json"
    file.write_text(json.dumps({"web_port": 8330, "rogue_key": True}))
    with pytest.raises(ValueError, match="rogue_key"):
        load_config(str(file))


def test_missing_file_rejected():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/miniport.json")


@pytest.mark.parametrize(
    "field, value",
    [
        ("web_port", 0),
        ("web_port", 70000),
        ("poll_interval", 0.01),
        ("log_level", "LOUD"),
        ("log_format", "xml"),
    ],
)
def test_validation_rejects_out_of_range(field: str, value):
    with pytest.raises(ValueError):
        load_config(overrides={field: value})


def test_json_log_formatter():
    import logging

    config = load_config(overrides={"log_format": "json", "log_level": "INFO"})
    setup_logging(config)
    handler = logging.getLogger().handlers[0]
    line = handler.format(
        logging.LogRecord(
            "miniport.test", logging.WARNING, __file__, 1, "watch %s", ("out",), None
        )
    )
    payload = json.loads(line)
    assert payload["level"] == "WARNING"
    assert payload["message"] == "watch out"
    assert payload["logger"] == "miniport.test"

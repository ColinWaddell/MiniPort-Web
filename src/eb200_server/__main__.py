"""Command line entry point: ``eb200-server`` / ``python -m eb200_server``.

Configuration precedence: defaults < --config JSON file < EB200_* environment
variables < explicit CLI arguments.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from eb200_server.config import ServerConfig, load_config

DEFAULT_WEB_HOST = "127.0.0.1"  # per project brief: bind localhost by default
DEFAULT_WEB_PORT = 8000


class JsonFormatter(logging.Formatter):
    """One JSON object per line; used for --log-format json."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def setup_logging(config: ServerConfig) -> None:
    handler = logging.StreamHandler(sys.stderr)
    if config.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))
    logging.captureWarnings(True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eb200-server", description="MiniPort-Web receiver server"
    )
    parser.add_argument(
        "--config",
        default=None,
        help="JSON config file (weakest, below environment and CLI flags)",
    )
    parser.add_argument("--host", help="web bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="web port (default 8000)")
    parser.add_argument("--receiver-host", help="EB200 address (default 10.0.0.240)")
    parser.add_argument("--receiver-port", type=int)
    parser.add_argument(
        "--data-dir", help="data directory (SQLite, recordings, sweeps)"
    )
    parser.add_argument("--poll-interval", type=float)
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-format", choices=["text", "json"])
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(
            args.config,
            overrides={
                "web_host": args.host,
                "web_port": args.port,
                "receiver_host": args.receiver_host,
                "receiver_port": args.receiver_port,
                "data_dir": args.data_dir,
                "poll_interval": args.poll_interval,
                "log_level": args.log_level,
                "log_format": args.log_format,
            },
        )
    except (ValueError, OSError) as exc:
        parser.exit(2, f"eb200-server: {exc}\n")

    setup_logging(config)
    from pathlib import Path

    import uvicorn

    from eb200_server.app import create_app
    from eb200_server.manager import ReceiverManager

    manager = ReceiverManager(
        config.receiver_host,
        config.receiver_port,
        poll_interval=config.poll_interval,
        data_dir=Path(config.data_dir) if config.data_dir else None,
    )
    app = create_app(manager)
    uvicorn.run(
        app,
        host=config.web_host,
        port=config.web_port,
        log_level=config.log_level.lower(),
    )


if __name__ == "__main__":
    main()

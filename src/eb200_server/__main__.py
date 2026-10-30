"""Command line entry point: ``python -m eb200_server``."""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn

from eb200_server.app import create_app
from eb200_server.manager import ReceiverManager

DEFAULT_WEB_HOST = "127.0.0.1"  # per project brief: bind localhost by default
DEFAULT_WEB_PORT = 8000


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="eb200-server", description="MiniPort-Web receiver server"
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("EB200_WEB_HOST", DEFAULT_WEB_HOST),
        help="web interface bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("EB200_WEB_PORT", DEFAULT_WEB_PORT)),
    )
    parser.add_argument(
        "--receiver-host",
        default=os.environ.get("EB200_HOST", "10.0.0.240"),
    )
    parser.add_argument(
        "--receiver-port",
        type=int,
        default=int(os.environ.get("EB200_PORT", "5555")),
    )
    parser.add_argument("--poll-interval", type=float, default=0.3)
    parser.add_argument(
        "--log-level", default=os.environ.get("EB200_LOG_LEVEL", "INFO")
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    manager = ReceiverManager(
        args.receiver_host,
        args.receiver_port,
        poll_interval=args.poll_interval,
    )
    app = create_app(manager)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()

"""CLI smoke tests against a fake instrument server."""

import pytest
from support import FakeEB200Server

from eb200.cli import build_parser, cmd_raw, main


@pytest.fixture
def server():
    fake = FakeEB200Server()
    fake.add_responses(
        {
            "*IDN?": b"ROHDE&SCHWARZ,EB200,108.063/003,V02.80-4052.4654.00\r\n",
            "FREQ?": b"27260000\r\n",
        }
    )
    fake.start()
    yield fake
    fake.stop()


def test_raw_guard_refuses_set_commands():
    args = build_parser().parse_args(["raw", "FREQ 1000"])
    with pytest.raises(SystemExit) as excinfo:
        cmd_raw(None, args)  # guard fires before any link use
    assert excinfo.value.code == 2


def test_raw_guard_refuses_reset():
    args = build_parser().parse_args(["raw", "--unsafe", "*RST"])
    with pytest.raises(SystemExit) as excinfo:
        cmd_raw(None, args)
    assert excinfo.value.code == 2


def test_main_idn(server, capsys):
    main(["--host", server.host, "--port", str(server.port), "idn"])
    assert "ROHDE&SCHWARZ" in capsys.readouterr().out


def test_main_rx(server, capsys):
    main(["--host", server.host, "--port", str(server.port), "rx"])
    out = capsys.readouterr().out
    assert b"27260000" in out.encode() or "27260000" in out


def test_main_raw_query(server, capsys):
    main(["--host", server.host, "--port", str(server.port), "raw", "FREQ?"])
    assert "27260000" in capsys.readouterr().out

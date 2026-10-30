"""Transport tests against a fake instrument TCP server."""

import threading
import time

import pytest
from support import KILL, Delayed, FakeEB200Server, wait_for

from eb200 import transport as transport_module
from eb200.errors import EB200Connection, EB200Timeout

IDN = b"ROHDE&SCHWARZ,EB200,108.063/003,V02.80-4052.4654.00\r\n"


@pytest.fixture
def server():
    fake = FakeEB200Server()
    fake.add_responses({"*IDN?": IDN})
    fake.start()
    yield fake
    fake.stop()


def make_transport(fake: FakeEB200Server, **kwargs):
    defaults = {
        "timeout": 0.5,
        "reconnect_delay": 0.05,
        "reconnect_attempts": 2,
    }
    defaults.update(kwargs)
    tr = transport_module.TCPTransport(fake.host, fake.port, **defaults)
    tr.start()
    return tr


def test_query_roundtrip(server):
    with make_transport(server) as tr:
        assert tr.query("*IDN?") == IDN


def test_command_reaches_the_server(server):
    with make_transport(server) as tr:
        tr.command("OUTP:SQU ON")
    assert wait_command(server, "OUTP:SQU ON")


def wait_command(server: FakeEB200Server, cmd: str, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cmd in server.received:
            return True
        time.sleep(0.02)
    return False


def test_concurrent_queries_keep_pairing(server):
    tr = make_transport(server)
    results: dict[str, bytes] = {}
    errors: list[Exception] = []

    def worker(tid: int) -> None:
        for n in range(25):
            text = f"ECHO {tid}-{n}"
            try:
                results[text] = tr.query(text)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    tr.close()

    assert errors == []
    assert len(results) == 100
    for text, data in results.items():
        assert data.rstrip(b"\r\n").decode("ascii") == text


def test_timeout_then_recovery_on_next_query(server):
    server.add_responses({"SLOW?": Delayed(2.0, b"late\r\n")})
    tr = make_transport(server)
    try:
        with pytest.raises(EB200Timeout):
            tr.query("SLOW?")
        # Timeout policy drops the link; the next query opens a fresh one.
        assert tr.query("*IDN?") == IDN
        assert server.connection_count == 2
    finally:
        tr.close()


def test_reconnect_after_connection_kill(server):
    hits: list[int] = []
    tr = make_transport(server, on_reconnect=lambda: hits.append(1))
    try:
        assert wait_for(lambda: server.connection_count >= 1)
        server.kill_connections()
        assert tr.query("*IDN?") == IDN
        assert hits == [1]
    finally:
        tr.close()


def test_reconnect_failure_reports_connection_error(server):
    tr = make_transport(server)
    assert wait_for(lambda: server.connection_count >= 1)
    server.kill_connections()
    server.stop()
    with pytest.raises(EB200Connection):
        tr.query("*IDN?")


def test_close_settles_pending_requests(server):
    server.add_responses({"HANG?": Delayed(2.0, b"x\r\n")})
    tr = make_transport(server, timeout=1.5)
    outcome: dict[str, BaseException] = {}

    def query() -> None:
        try:
            tr.query("HANG?")
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc

    thread = threading.Thread(target=query)
    thread.start()
    time.sleep(0.2)
    tr.close()
    thread.join(3.0)

    assert isinstance(outcome.get("error"), EB200Connection)


def test_kill_value_drops_connection(server):
    server.add_responses({"BAD?": KILL})
    tr = make_transport(server)
    try:
        with pytest.raises((EB200Connection, EB200Timeout)):
            tr.query("BAD?")
        assert tr.query("*IDN?") == IDN
    finally:
        tr.close()

"""Opt-in tests against the physical EB200 (run with 'pytest -m integration').

Discipline: a filesystem lock guarantees no other session (or the web
server) is talking to the receiver; everything is read-only or
read-modify-restore of the receiver's own current value; no *RST is ever
sent. The UDP audio smoke additionally needs the temporary ufw rule from
docs/protocol-notes.md while it runs (set EB200_AUDIO_SMOKE=1 to include it).

Environment: EB200_HOST (default 10.0.0.240), EB200_PORT (default 5555).
"""

import fcntl
import os
import socket as socket_module
import time

import pytest

pytestmark = pytest.mark.integration

HOST = os.environ.get("EB200_HOST", "10.0.0.240")
PORT = int(os.environ.get("EB200_PORT", "5555"))

BASELINE_REGISTRY = "DEF\n001\n002\n003"
LOCK_PATH = "/tmp/eb200-integration.lock"


@pytest.fixture(scope="module")
def link():
    probe = socket_module.socket()
    probe.settimeout(2.0)
    try:
        probe.connect((HOST, PORT))
    except OSError:
        probe.close()
        pytest.skip(f"EB200 not reachable at {HOST}:{PORT}")

    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        pytest.skip("another session holds the EB200 integration lock")

    from eb200.transport import TCPTransport

    transport = TCPTransport(HOST, PORT, timeout=2.5)
    transport.start()
    yield transport
    transport.close()
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()


@pytest.fixture
def receiver(link):
    from eb200.device import EB200

    return EB200(link, registry_settle=0.3)


def test_identity(receiver):
    info = receiver.identify()
    assert info.model == "EB200"
    assert info.firmware.startswith("V02.80")


def test_read_only_rx_state(receiver):
    frequency = receiver.frequency()
    mode = receiver.frequency_mode()
    bandwidth = receiver.bandwidth()
    level = receiver.level()
    assert 10_000 <= frequency <= 3_000_000_000
    assert isinstance(mode, str) and mode != ""
    assert 150 <= bandwidth <= 150_000
    assert -80 <= level <= 120  # dBµV sanity window


def test_read_modify_restore_squelch_threshold(receiver):
    original = receiver.squelch_threshold()
    receiver.set_squelch_threshold(original)
    assert receiver.squelch_threshold() == original


def test_read_modify_restore_volume(receiver):
    original = receiver.af_volume()
    receiver.set_af_volume(original)
    assert receiver.af_volume() == pytest.approx(original, abs=0.02)


def test_udp_registry_idle(receiver):
    assert receiver.udp_registry() == BASELINE_REGISTRY


def test_audio_smoke(receiver, link):
    """Register AUDIO, enable remote AF mode 10, expect datagrams."""
    if os.environ.get("EB200_AUDIO_SMOKE") != "1":
        pytest.skip("set EB200_AUDIO_SMOKE=1 to exercise the UDP audio path")
    from eb200.streams import AudioReceiver

    assert receiver.udp_registry() == BASELINE_REGISTRY

    audio = AudioReceiver()
    audio.start()
    ip = link.local_ip
    orig_mode = receiver.remote_af_mode()
    try:
        # Recover from leftovers of earlier aborted runs, then assert clean.
        receiver.udp_clear(ip)
        assert receiver.udp_registry() == BASELINE_REGISTRY
        receiver.udp_register(ip, audio.port, "AUDIO")
        receiver.set_remote_af_mode(10)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and audio.stats()["packets"] < 1:
            audio.read(8000)
            time.sleep(0.05)
        stats = audio.stats()
        assert stats["packets"] > 0, (
            "no audio datagrams received - check the ufw rule "
            "(docs/protocol-notes.md, host-side note)"
        )
        # A 3 s WLAN capture may drop a packet; gross loss is a failure.
        assert stats["parse_errors"] == 0
        assert stats["lost"] <= stats["packets"] // 2
        samples = audio.read(8192)
        assert len(samples) > 0
    finally:
        try:
            receiver.set_remote_af_mode(orig_mode)
        finally:
            try:
                receiver.udp_clear(ip)
            finally:
                audio.stop()

    assert receiver.remote_af_mode() == orig_mode
    assert receiver.udp_registry() == BASELINE_REGISTRY

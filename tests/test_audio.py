"""Audio service tests: simulated registry, PCM pump, WAV recording."""

import asyncio
import socket as socketlib
import time
import wave

import pytest
from support import make_audio_datagram

from eb200.device import EB200
from eb200.sim import StatefulTransport
from eb200_server.audio import AudioError, AudioService
from eb200_server.db import Store


async def wait_for_async(predicate, timeout: float = 2.0, poll: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(poll)
    return predicate()


def test_stateful_registry_matches_live_semantics():
    transport = StatefulTransport()
    device = EB200(transport, registry_settle=0.0)
    device.udp_register("10.0.0.35", 50000, "AUDIO")
    assert ("10.0.0.35", 50000) in device.udp_entries()
    device.udp_unregister("10.0.0.35", 50000, "AUDIO")
    assert device.udp_registry() == "DEF\n001\n002\n003"
    # Registry state survives repeated register/unregister round trips.
    device.udp_register("10.0.0.35", 50001, "IFPAN")
    assert ("10.0.0.35", 50001) in device.udp_entries()


def test_audio_service_stream_record_restore(tmp_path):
    transport = StatefulTransport()
    device = EB200(transport, registry_settle=0.0)
    sink_frames: list[bytes] = []
    store = Store(tmp_path / "test.db")
    service = AudioService(
        device, tmp_path / "recordings", pump_interval=0.01, store=store
    )

    async def sink(payload: bytes) -> None:
        sink_frames.append(payload)

    async def feed(sequence: int) -> None:
        sender = socketlib.socket(socketlib.AF_INET, socketlib.SOCK_DGRAM)
        try:
            sender.sendto(
                make_audio_datagram(sequence, [100 + sequence, 200 + sequence]),
                ("127.0.0.1", service.rx_port),
            )
        finally:
            sender.close()

    async def scenario() -> None:
        service.set_sink(sink)
        status = await service.start("tester", ip="10.0.0.35")
        assert status["running"] is True
        assert device.remote_af_mode() == 10  # engaged for the stream

        await feed(1)
        await asyncio.sleep(0.05)
        await feed(2)
        assert await wait_for_async(lambda: len(sink_frames) >= 2), (
            "pcm frames must reach the sink"
        )
        # PCM bytes are native little-endian int16 of the sent samples.
        assert sink_frames[0] == (100 + 1).to_bytes(2, "little") + (200 + 1).to_bytes(
            2, "little"
        )
        service.record_start("tester")
        for sequence in range(3, 10):
            await feed(sequence)
            await asyncio.sleep(0.01)
        okSamples = await wait_for_async(lambda: service.status()["samples"] >= 18)
        assert okSamples, "all fed samples must be accounted for"

        def buffered_zero():
            current = service.status()
            if current["running"]:
                assert "buffered_samples" in current, f"bad running status: {current}"
                return current["buffered_samples"] == 0
            raise AssertionError(f"stream went idle during drain wait: {current}")

        okBuf = await wait_for_async(buffered_zero)
        assert okBuf, "the pump must drain the buffer before record_stop"
        stopped = service.record_stop()
        assert stopped["recording"] is False
        assert stopped["samples"] == 14  # frames 3..9, two samples each

        await service.stop()

    asyncio.run(scenario())

    # WAV file exists and validates through the stdlib reader.
    recordings = service.list_recordings()
    assert len(recordings) == 1
    path = service.recording_path(recordings[0]["name"])
    with wave.open(str(path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getnframes() == 14

    # State fully restored after stop.
    assert device.remote_af_mode() == 0
    assert device.udp_registry() == "DEF\n001\n002\n003"
    assert service.running is False


def test_audio_service_guards(tmp_path):
    device = EB200(StatefulTransport(), registry_settle=0.0)
    service = AudioService(device, tmp_path / "recordings")

    async def scenario() -> None:
        with pytest.raises(AudioError):
            await service.record_start("tester")
        with pytest.raises(AudioError):
            await service.record_stop()
        with pytest.raises(ValueError):
            service.recording_path("../escape.wav")

    asyncio.run(scenario())


def test_audio_without_stream_has_no_rate(tmp_path):
    device = EB200(StatefulTransport(), registry_settle=0.0)
    service = AudioService(device, tmp_path / "recordings")
    assert service.status() == {"running": False, "recording": False}

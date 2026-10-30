"""Spectrum service, frame codec, CSV export and preset store tests."""

import asyncio
import math

import pytest

from eb200.device import EB200
from eb200.sim import StatefulTransport
from eb200_server.presets import PresetStore, csv_export
from eb200_server.spectrum import (
    SpectrumParams,
    SpectrumService,
    decode_frame,
    encode_frame,
)

BAND = SpectrumParams(27_000_000, 27_200_000, 5_000)  # 41 points


def test_params_validation():
    with pytest.raises(ValueError):
        SpectrumParams(27_100_000, 27_000_000, 5_000).validate()
    with pytest.raises(ValueError):
        SpectrumParams(27_000_000, 27_100_000, 0).validate()
    with pytest.raises(ValueError):
        SpectrumParams(27_000_000, 37_006_000, 5_000).validate()  # 2002 > limit
    SpectrumParams(27_000_000, 37_000_000, 5_000).validate()  # exactly 2001 ok
    SpectrumParams(27_000_000, 27_200_000, 5_000).validate()


def test_frame_codec_roundtrip():
    levels = [-18.3, None, math.nan, -95.5]
    frame = encode_frame(
        123.5, BAND, levels + [None] * (BAND.points - len(levels)), [None] * BAND.points
    )

    decoded = decode_frame(frame)
    assert decoded["start_hz"] == 27_000_000
    assert decoded["step_hz"] == 5_000
    assert decoded["levels"][0] == pytest.approx(-18.3)
    assert decoded["levels"][1] is None  # None and NaN both become NO_LEVEL
    assert decoded["levels"][3] == pytest.approx(-95.5)
    assert decoded["peak"] is not None


def test_frame_without_peak():
    frame = encode_frame(1.0, BAND, [-20.0] * BAND.points, None)
    decoded = decode_frame(frame)
    assert decoded["peak"] is None


def test_service_runs_cycles_and_tracks_peak():
    transport = StatefulTransport()
    device = EB200(transport, registry_settle=0.0)
    frames: list[bytes] = []
    service = SpectrumService(device, asyncio.Lock())

    async def sink(frame: bytes) -> None:
        frames.append(frame)

    async def scenario() -> None:
        service.set_frame_sink(sink)
        await service.start(BAND)
        await asyncio.sleep(1.2)
        await service.stop()
        assert not service.running

    asyncio.run(scenario())

    assert service.cycle_count >= 2
    assert len(frames) == service.cycle_count
    for frame in frames:
        decoded = decode_frame(frame)
        assert len(decoded["levels"]) == BAND.points
        assert decoded["levels"][0] is not None
        assert decoded["peak"] is not None
    # A strong synthetic carrier sits in the centre of the band.
    decoded = decode_frame(frames[-1])
    centre = BAND.points // 2
    peak_level = decoded["peak"][centre]
    assert peak_level is not None and peak_level > 0
    # After stop, the simulated receiver is back in tune mode.
    assert device.frequency_mode() == "CW"


def test_csv_export_shape():
    frequencies = [26_800_000, 26_825_000]
    history = [(123.0, [-18.1, None]), (124.0, [-17.5, -16.9])]
    text = csv_export(frequencies, history)
    lines = text.strip().splitlines()
    assert lines[0] == "timestamp_iso,26800000,26825000"
    assert len(lines) == 3
    assert lines[1].endswith(",-18.1,")
    assert lines[2].endswith(",-17.5,-16.9")


def test_preset_store_roundtrip(tmp_path):
    store = PresetStore(tmp_path / "presets.json")
    first = store.add("Broadcast band", 87_500_000, 108_000_000, 100_000)
    assert first["id"] == 1
    second = store.add("Short wave", 5_800_000, 15_000_000, 5_000)
    assert second["id"] == 2
    assert len(store.list()) == 2

    # Reload from disk to prove persistence.
    reloaded = PresetStore(tmp_path / "presets.json")
    assert [p["name"] for p in reloaded.list()] == ["Broadcast band", "Short wave"]

    assert reloaded.delete(1) is True
    assert reloaded.delete(1) is False
    assert [p["id"] for p in reloaded.list()] == [2]


def test_no_level_sentinel():
    """None encodes to NO_LEVEL; absurd levels clamp inside int16 range."""
    levels = [9_999.0] + [None] * (BAND.points - 1)
    decoded = decode_frame(encode_frame(0.0, BAND, levels, None))
    assert decoded["levels"][0] == pytest.approx(3276.7)
    for value in decoded["levels"][1:]:
        assert value is None


def test_service_rejection_when_running():
    transport = StatefulTransport()
    device = EB200(transport, registry_settle=0.0)
    service = SpectrumService(device, asyncio.Lock())

    async def scenario() -> None:
        await service.start(BAND)
        await asyncio.sleep(0.3)
        with pytest.raises(ValueError):
            await service.start(BAND)
        await service.stop()
        # start again after stop must work
        await service.start(BAND)
        await asyncio.sleep(0.4)
        await service.stop()

    asyncio.run(scenario())
    assert service.cycle_count >= 1

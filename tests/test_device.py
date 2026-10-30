"""Device facade tests against the simulated transport."""

import math

import pytest

from eb200.device import EB200
from eb200.errors import EB200Protocol, EB200Timeout, SCPIError


def test_identify(device: EB200):
    info = device.identify()
    assert info.manufacturer == "ROHDE&SCHWARZ"
    assert info.model == "EB200"
    assert info.serial == "108.063/003"
    assert info.firmware.startswith("V02.80")


def test_option_codes(device: EB200):
    assert device.option_codes() == ["ER", "F1"]


def test_getters_match_captured_profile(device: EB200):
    assert device.frequency() == 27260000
    assert device.frequency_mode() == "CW"
    assert device.bandwidth() == 15000
    assert device.bandwidth_range() == (150, 150_000)
    assert device.demodulation() == "AM"
    assert device.afc() is False
    assert device.attenuator() is False
    assert device.attenuator_auto() is False
    assert device.squelch() is True
    assert device.squelch_threshold() == -30.0
    assert device.af_volume() == 0.0
    assert device.remote_af_mode() == 0
    assert device.level() == pytest.approx(-18.3)


def test_trace_catalogue(device: EB200):
    assert device.trace_catalogue() == [
        "MTRACE",
        "ITRACE",
        "IFPAN",
        "SSTART",
        "SSTOP",
        "UDP",
    ]
    assert device.trace_points("MTRACE") == 0


def test_setters_build_expected_commands(device: EB200, sim):
    device.set_frequency(100_000_000)
    device.set_bandwidth(9_000)
    device.set_frequency_mode("SWEep")
    device.set_demodulation("FM")
    device.set_afc(True)
    device.set_attenuator(True)
    device.set_attenuator_auto(False)
    device.set_squelch(False)
    device.set_squelch_threshold(-25.5)
    device.set_af_volume(0.5)
    device.set_af_balance(-0.25)
    device.set_remote_af_mode(10)
    sent = sim.queries_sent()
    assert "FREQ 100000000" in sent
    assert "BAND 9000" in sent
    assert "FREQ:MODE SWEep" in sent
    assert "DEM FM" in sent
    assert "FREQ:AFC ON" in sent
    assert "INP:ATT:STAT ON" in sent
    assert "INP:ATT:AUTO OFF" in sent
    assert "OUTP:SQU OFF" in sent
    assert "OUTP:SQU:THR -25.5" in sent
    assert "SYST:AUD:VOL 0.50" in sent
    assert "SYST:AUD:BAL -0.25" in sent
    assert "SYST:AUD:REM:MODE 10" in sent


def test_setter_validation(device: EB200):
    with pytest.raises(ValueError):
        device.set_frequency(9_999)
    with pytest.raises(ValueError):
        device.set_frequency(3_000_000_001)
    with pytest.raises(ValueError):
        device.set_bandwidth(149)
    with pytest.raises(ValueError):
        device.set_remote_af_mode(14)
    with pytest.raises(ValueError):
        device.set_frequency_mode("NOPE")
    with pytest.raises(ValueError):
        device.set_demodulation("XYZ")
    with pytest.raises(ValueError):
        device.set_af_volume(1.5)
    with pytest.raises(ValueError):
        device.set_af_balance(0.6)
    with pytest.raises(ValueError):
        device.udp_register("10.0.0.35", 40000, "NOT-A-TAG")


def test_strict_setter_raises_scpi_error(device: EB200, sim):
    sim.queue_responses(
        "SYST:ERR?",
        [b'1,"Device dependent error;Not installed"\r\n'],
    )
    with pytest.raises(SCPIError) as excinfo:
        device.set_frequency_mode("DSCan")
    assert excinfo.value.code == 1
    assert "Not installed" in excinfo.value.message


def test_non_strict_setter_ignores_queued_errors(sim):
    receiver = EB200(sim, strict=False)
    sim.queue_responses("SYST:ERR?", [b'1,"boom"\r\n'])
    receiver.set_frequency(50_000_000)


def test_udp_register_and_unregister_commands(sim):
    receiver = EB200(sim, registry_settle=0.0)
    sim.set_response("TRACE:UDP?", b'001 "10.0.0.35", 50000, AUD\n002\n003\r\n')
    receiver.udp_register("10.0.0.35", 50000, "AUDIO")
    sim.set_response("TRACE:UDP?", b"DEF\n001\n002\n003\r\n")
    receiver.udp_unregister("10.0.0.35", 50000, "AUDIO")
    sent = sim.queries_sent()
    assert 'TRACE:UDP:TAG:ON "10.0.0.35",50000,AUDIO' in sent
    assert 'TRACE:UDP:TAG:OFF "10.0.0.35",50000,AUDIO' in sent
    assert 'TRACE:UDP:DELETE "10.0.0.35",50000' in sent


def test_udp_clear_deletes_each_entry_once(sim):
    receiver = EB200(sim, registry_settle=0.0)
    sim.queue_responses(
        "TRACE:UDP?",
        [b'001 "10.0.0.35", 50000, AUD\n002\n003\r\n', b"DEF\n001\n002\n003\r\n"],
    )
    removed = receiver.udp_clear("10.0.0.35")
    assert removed == 1
    assert 'TRACE:UDP:DELETE "10.0.0.35",50000' in sim.queries_sent()


def test_gain_getters_and_commands(device: EB200, sim):
    assert device.gain() == 50.0
    assert device.gain_mode() == "AUTO"
    device.set_gain(70)
    device.set_gain_mode("FIXed")
    device.set_gain_mode("auto")  # case-insensitive canonicalisation
    sent = sim.queries_sent()
    assert "GCON 70" in sent
    assert "GCON:MODE FIXed" in sent
    assert "GCON:MODE AUTO" in sent


def test_gain_validation(device: EB200):
    with pytest.raises(ValueError):
        device.set_gain(200)
    with pytest.raises(ValueError):
        device.set_gain(-31)
    with pytest.raises(ValueError):
        device.set_gain_mode("XYZ")


def test_sweep_config_getters_and_commands(device: EB200, sim):
    assert device.sweep_start() == 20_000_000
    assert device.sweep_stop() == 100_000_000
    assert device.sweep_step() == 100_000
    assert device.sweep_count() == math.inf
    assert device.sweep_dwell() == 0.0
    assert device.sweep_direction() == "UP"

    device.set_sweep_start(26_800_000)
    device.set_sweep_stop(27_800_000)
    device.set_sweep_step(25_000)
    device.set_sweep_count(1)
    device.set_sweep_count(math.inf)
    device.set_sweep_dwell(0.25)
    device.set_sweep_direction("down")
    sent = sim.queries_sent()
    assert "FREQ:STARt 26800000" in sent
    assert "FREQ:STOP 27800000" in sent
    assert "SWEep:STEP 25000" in sent
    assert "SWEep:COUNt 1" in sent
    assert "SWEep:COUNt INFinity" in sent
    assert "SWEep:DWELl 0.250" in sent
    assert "SWEep:DIRection DOWN" in sent


def test_sweep_config_validation(device: EB200):
    with pytest.raises(ValueError):
        device.set_sweep_step(0)
    with pytest.raises(ValueError):
        device.set_sweep_count(0)
    with pytest.raises(ValueError):
        device.set_sweep_dwell(11.0)
    with pytest.raises(ValueError):
        device.set_sweep_direction("SIDEWAYS")


def test_scan_sweep_end_to_end(sim):
    receiver = EB200(sim, registry_settle=0.0)
    sim.queue_responses("TRACe:POINts? MTRACE", [b"0\r\n", b"23\r\n", b"50\r\n"])
    sim.set_response("TRACe:DATA? MTRACE", b"-17.3,-18.1,9.9E37\r\n")
    sim.set_response("TRACe:DATA? ITRACE", b"0,26800000,1,26825000,2,26825000\r\n")

    result = receiver.scan_sweep(26_800_000, 27_800_000, 25_000, timeout=5.0)

    assert result.levels == pytest.approx((-17.3, -18.1))
    assert result.frequencies == (26_800_000, 26_825_000)
    assert result.restore_errors == ()

    sent = sim.queries_sent()
    for cmd in (
        "TRACE:FEED:CONTrol MTRACE,ALWayS",
        "TRACE:FEED:CONTrol ITRACE,ALWayS",
        "FREQ:STARt 26800000",
        "FREQ:STOP 27800000",
        "SWEep:STEP 25000",
        "SWEep:COUNt 1",
        "FREQ:MODE SWEep",
        "INIT",
        "ABORt",
    ):
        assert cmd in sent
    for restored in (
        "FREQ:MODE CW",
        "FREQ 27260000",
        "FREQ:STARt 20000000",
        "FREQ:STOP 100000000",
        "SWEep:STEP 100000",
        "SWEep:COUNt INFinity",
        "TRACE:FEED:CONTrol MTRACE,NEVer",
        "TRACE:FEED:CONTrol ITRACE,NEVer",
    ):
        assert restored in sent


def test_scan_sweep_rejects_bad_band(sim):
    receiver = EB200(sim, registry_settle=0.0)
    with pytest.raises(ValueError):
        receiver.scan_sweep(27_800_000, 26_800_000, 25_000)
    with pytest.raises(ValueError):
        receiver.scan_sweep(26_800_000, 27_800_000, 0)
    assert sim.queries_sent() == []  # no instrument traffic on validation error


def test_scan_sweep_timeout(sim):
    receiver = EB200(sim, registry_settle=0.0)
    with pytest.raises(EB200Timeout):
        receiver.scan_sweep(26_800_000, 27_800_000, 25_000, timeout=0.7)


def test_udp_register_verifies_registry_state(sim):
    receiver = EB200(sim, registry_settle=0.01)
    # Registry stays at baseline, so the awaited verification must fail.
    with pytest.raises(EB200Protocol):
        receiver.udp_register("10.0.0.35", 50000, "AUDIO")

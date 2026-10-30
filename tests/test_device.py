"""Device facade tests against the simulated transport."""

import pytest

from eb200.device import EB200
from eb200.errors import EB200Protocol, SCPIError


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


def test_udp_register_verifies_registry_state(sim):
    receiver = EB200(sim, registry_settle=0.01)
    # Registry stays at baseline, so the awaited verification must fail.
    with pytest.raises(EB200Protocol):
        receiver.udp_register("10.0.0.35", 50000, "AUDIO")

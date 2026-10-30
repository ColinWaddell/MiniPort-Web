"""Simulated transport tests, including replay of committed fixtures."""

from pathlib import Path

import pytest

from eb200.errors import EB200Connection, EB200Protocol, EB200Timeout
from eb200.sim import SimulatedTransport

SWEEP_FIXTURE = Path("tests/fixtures/capture/20261030-161332-sweep.jsonl")


def test_default_profile_replays_live_values(sim: SimulatedTransport):
    assert sim.query("*IDN?").startswith(b"ROHDE&SCHWARZ,EB200")
    assert sim.query("FREQ?") == b"27260000\r\n"
    assert sim.query("BAND?") == b"15000\r\n"


def test_unknown_command_raises_protocol(sim: SimulatedTransport):
    with pytest.raises(EB200Protocol):
        sim.query("INPut?")


def test_case_insensitive_matching():
    sim = SimulatedTransport(responses={"freq?": b"27260000\r\n"})
    assert sim.query("FREQ?") == b"27260000\r\n"


def test_queue_responses_fifo(sim: SimulatedTransport):
    sim.queue_responses(
        "SYST:ERR?",
        [b'1,"Device dependent error;Not installed"\r\n'],
    )
    errors = sim.query_errors()
    assert len(errors) == 1
    assert errors[0].code == 1
    # Queue exhausted, the static default takes over again.
    assert sim.query_errors() == []


def test_none_value_simulates_timeout():
    sim = SimulatedTransport(responses={"X?": None})
    with pytest.raises(EB200Timeout):
        sim.query("X?")


def test_injected_fault_raises(sim: SimulatedTransport):
    sim.inject_fault(EB200Connection("forced drop"))
    with pytest.raises(EB200Connection):
        sim.query("FREQ?")
    # The fault is consumed; the next query succeeds.
    assert sim.query("FREQ?") == b"27260000\r\n"


def test_disconnect_reconnect_cycle(sim: SimulatedTransport):
    sim.disconnect()
    with pytest.raises(EB200Connection):
        sim.query("FREQ?")
    sim.reconnect()
    assert sim.query("FREQ?") == b"27260000\r\n"


def test_from_transcript_replays_capture():
    sim = SimulatedTransport.from_transcript(SWEEP_FIXTURE)
    assert sim.query("*IDN?").startswith(b"ROHDE&SCHWARZ,EB200")
    assert sim.query("FREQ?") == b"27260000\r\n"
    assert sim.query("SYST:VERS?") == b"1996.0\r\n"


def test_from_transcript_marks_unanswered_queries():
    sim = SimulatedTransport.from_transcript(SWEEP_FIXTURE)
    # INPut? timed out against the receiver and therefore never got an
    # answer in the transcript: the simulated transport raises, matching
    # live behaviour.
    with pytest.raises(EB200Protocol):
        sim.query("INPut?")


def test_requests_are_recorded(sim: SimulatedTransport):
    sim.query("FREQ?")
    sim.command("OUTP:SQU ON")
    assert sim.queries_sent() == ["FREQ?", "OUTP:SQU ON"]

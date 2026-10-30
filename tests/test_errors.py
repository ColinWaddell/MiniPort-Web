"""Error taxonomy + SYST:ERR? parsing tests."""

import pytest

from eb200.errors import EB200Protocol, SCPIError, parse_syst_error


def test_parses_error_line():
    assert parse_syst_error('-113,"Undefined header;SYST:IF:REM:MODE?"') == (
        -113,
        "Undefined header;SYST:IF:REM:MODE?",
    )


def test_no_error_gives_none():
    assert parse_syst_error('0,"No error"') is None
    assert parse_syst_error("") is None


def test_unparseable_line_raises():
    with pytest.raises(EB200Protocol):
        parse_syst_error("garbage without quotes")


def test_scpi_error_fields():
    error = SCPIError(1, "Device dependent error", "FREQ:MODE DSCan")
    assert error.code == 1
    assert error.message == "Device dependent error"
    assert error.command == "FREQ:MODE DSCan"
    assert str(error).startswith("1:")

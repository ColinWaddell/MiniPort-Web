"""Error taxonomy for the eb200 package."""

from __future__ import annotations

import re


class EB200Error(Exception):
    """Base class for every error raised by the eb200 package."""


class EB200Timeout(EB200Error):
    """A query produced no answer within the allotted time."""


class EB200Connection(EB200Error):
    """The TCP link failed (and, where applicable, reconnecting did too)."""


class EB200Protocol(EB200Error):
    """The receiver produced something this driver cannot parse."""


class SCPIError(EB200Error):
    """The receiver reported an error through its SCPI error queue."""

    def __init__(self, code: int, message: str, command: str = "") -> None:
        detail = f"{code}: {message}"
        if command:
            detail += f" ({command})"
        super().__init__(detail)
        self.code = code
        self.message = message
        self.command = command


_ERROR_LINE = re.compile(r'^(-?\d+),"(.*)"$')


def parse_syst_error(text: str) -> tuple[int, str] | None:
    """Parse one SYST:ERR? reply, e.g. ``-113,"Undefined header;CMD?"``.

    Returns the (code, message) pair for real errors, or None for the
    ``0,"No error"`` terminator or an empty line. Raises EB200Protocol for
    unparseable content.
    """
    line = text.strip()
    if not line:
        return None
    match = _ERROR_LINE.match(line)
    if match is None:
        raise EB200Protocol(f"unparseable SYST:ERR? reply: {text!r}")
    code = int(match.group(1))
    if code == 0:
        return None
    return code, match.group(2)

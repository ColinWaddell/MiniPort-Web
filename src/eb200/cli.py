"""Read-only command line probe for a Rohde & Schwarz EB200 receiver.

The probe talks TCP/SCPI straight to the instrument and records every
exchange as raw bytes. It is intentionally self-contained: the ``eb200``
driver core is added in the next phase, after which this module is reworked
onto that API.

Every subcommand is read-only by default. ``raw`` refuses commands that do
not end in ``?`` (and any instrument reset/self-test) unless ``--unsafe`` is
given explicitly.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import socket
import sys
import time
from pathlib import Path

from . import experiments

DEFAULT_HOST = "10.0.0.240"
DEFAULT_PORT = 5555
CONNECT_TIMEOUT_S = 5.0
RESPONSE_TIMEOUT_S = 2.0
SWEEP_PAUSE_S = 0.15
DEFAULT_CAPTURE_DIR = Path("tests/fixtures/capture")

FORBIDDEN_PREFIXES = ("*RST", "*TST")
FORBIDDEN_HINT = "refusing non-query command; use --unsafe to override"

# (label, query). All entries are queries; unknown commands are recorded
# together with the resulting SCPI error so the sweep doubles as evidence.
SWEEP_COMMANDS: tuple[tuple[str, str], ...] = (
    ("identity", "*IDN?"),
    ("options", "*OPT?"),
    ("status-byte", "*STB?"),
    ("ist-flags", "*IST?"),
    ("scpi-version", "SYST:VERS?"),
    ("trace-catalogue", "TRACe:CATalog?"),
    ("trace-points-mtrace", "TRACe:POINts? MTRACE"),
    ("trace-points-itrace", "TRACe:POINts? ITRACE"),
    ("trace-points-ifpan", "TRACe:POINts? IFPAN"),
    ("udp-trace-list", "TRACE:UDP?"),
    ("format-data", "FORMat:DATA?"),
    ("frequency-mode", "FREQ:MODE?"),
    ("frequency", "FREQ?"),
    ("frequency-afc", "FREQ:AFC?"),
    ("bandwidth", "BAND?"),
    ("bandwidth-min", "BAND? MINimum"),
    ("bandwidth-max", "BAND? MAXimum"),
    ("demodulation-long", "DEMODULATION?"),
    ("demodulation-short", "DEM?"),
    ("attenuator-auto", "INP:ATT:AUTO?"),
    ("attenuator-auto-long", "INPut:ATTenuation:AUTO?"),
    ("attenuator-state", "INP:ATT:STAT?"),
    ("input", "INPut?"),
    ("squelch", "OUTP:SQU?"),
    ("squelch-long", "OUTPut:SQUelch:STATe?"),
    ("squelch-threshold", "OUTP:SQU:THR?"),
    ("squelch-control", "OUTP:SQU:CONT?"),
    ("volume", "SYST:AUD:VOL?"),
    ("balance", "SYST:AUD:BAL?"),
    ("remote-af-mode", "SYST:AUD:REM:MODE?"),
    ("level", 'SENS:DATA? "VOLT:AC"'),
    ("display-enable", "DISP:ENAB?"),
    ("operation-condition", "STATus:OPERation:CONDition?"),
    ("operation-event", "STATus:OPERation?"),
    ("sweeping-condition", "STATus:OPERation:SWEeping:CONDition?"),
    ("questionable-condition", "STATus:QUEStionable:CONDition?"),
    ("trace-condition", "STATus:TRACe:CONDition?"),
)

RX_QUERIES: tuple[tuple[str, str], ...] = (
    ("frequency", "FREQ?"),
    ("frequency mode", "FREQ:MODE?"),
    ("frequency afc", "FREQ:AFC?"),
    ("bandwidth", "BAND?"),
    ("demodulation", "DEMODULATION?"),
    ("attenuator auto", "INP:ATT:AUTO?"),
    ("attenuator state", "INP:ATT:STAT?"),
    ("squelch", "OUTP:SQU?"),
    ("squelch threshold", "OUTP:SQU:THR?"),
    ("volume", "SYST:AUD:VOL?"),
    ("remote af mode", "SYST:AUD:REM:MODE?"),
    ("signal level", 'SENS:DATA? "VOLT:AC"'),
)


class Transcript:
    """Append-only JSONL log of raw command/response bytes."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh = path.open("a", encoding="utf-8", newline="\n")

    @classmethod
    def create(cls, directory: Path, tag: str) -> Transcript:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = directory / f"{stamp}-{tag}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(path)

    @property
    def path(self) -> Path:
        return self._path

    def log(
        self,
        kind: str,
        cmd: str | None = None,
        data: bytes = b"",
        note: str | None = None,
    ) -> None:
        entry: dict[str, str] = {
            "t": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
            "kind": kind,
            "data_b64": base64.b64encode(data).decode("ascii"),
        }
        if cmd is not None:
            entry["cmd"] = cmd
        if note is not None:
            entry["note"] = note
        self._fh.write(json.dumps(entry) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class ProbeLink:
    """A single EB200 TCP connection that logs its own traffic."""

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = RESPONSE_TIMEOUT_S,
        transcript: Transcript | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._transcript = transcript
        self._sock: socket.socket = socket.create_connection(
            (host, port), timeout=CONNECT_TIMEOUT_S
        )
        self._sock.settimeout(timeout)

    @property
    def transcript(self) -> Transcript | None:
        return self._transcript

    @property
    def local_ip(self) -> str:
        return self._sock.getsockname()[0]

    def close(self) -> None:
        self._sock.close()

    def send_command(self, cmd: str) -> None:
        payload = (cmd + "\n").encode("ascii")
        if self._transcript is not None:
            self._transcript.log("tx", cmd, payload)
        self._sock.sendall(payload)

    def read_response(self) -> bytes:
        self._sock.settimeout(self._timeout)
        try:
            first = self._sock.recv(4096)
        except TimeoutError:
            raise TimeoutError(f"no response within {self._timeout}s") from None
        if not first:
            raise ConnectionError("connection closed by receiver")
        if first.startswith(b"#"):
            return self._read_block(first)
        return self._read_line(first)

    def _read_line(self, first: bytes) -> bytes:
        buf = bytearray(first)
        while True:
            if buf.endswith(b"\r\n"):
                return bytes(buf)
            try:
                chunk = self._sock.recv(4096)
            except TimeoutError:
                return bytes(buf)
            if not chunk:
                raise ConnectionError("connection closed by receiver")
            buf += chunk

    def _read_block(self, first: bytes) -> bytes:
        """Read a definite-length block response (#<digits><len><payload>)."""
        buf = bytearray(first)
        if len(buf) < 2:
            buf += self._recv_more()
        count_digits = int(chr(buf[1]))
        if count_digits == 0:
            # Indefinite-length block: read until newline terminator.
            while not buf.endswith(b"\n"):
                buf += self._recv_more()
            return bytes(buf[2:-1])
        want = 2 + count_digits
        while len(buf) < want:
            buf += self._recv_more()
        length = int(buf[2:want])
        end = want + length
        while len(buf) < end:
            buf += self._recv_more()
        return bytes(buf[want:end])

    def _recv_more(self) -> bytes:
        try:
            chunk = self._sock.recv(4096)
        except TimeoutError:
            raise TimeoutError("response ended mid-block") from None
        if not chunk:
            raise ConnectionError("connection closed mid-response")
        return chunk

    def query(self, cmd: str) -> bytes:
        self.send_command(cmd)
        data = self.read_response()
        if self._transcript is not None:
            self._transcript.log("rx", None, data)
        return data

    def issue(self, cmd: str, wait: float = 0.25) -> bytes:
        """Send a set command and briefly collect any unexpected reply."""
        self.send_command(cmd)
        try:
            data = self.read_response()
        except TimeoutError:
            data = b""
        if self._transcript is not None:
            self._transcript.log("rx", None, data, note="issue")
        return data

    def query_errors(self, limit: int = 32) -> list[str]:
        """Drain the SCPI error queue until it reports 'No error'."""
        messages: list[str] = []
        for _ in range(limit):
            data = self.query("SYST:ERR?")
            text = data.decode("ascii", "replace").rstrip()
            messages.append(text)
            if not text or text.startswith("0,"):
                break
        return messages

    def query_errors_clean(self) -> list[str]:
        """Return only queued errors, ignoring the trailing 'No error'."""
        return [m for m in self.query_errors() if m and not m.startswith("0,")]


def run_sweep(link: ProbeLink, pause: float = SWEEP_PAUSE_S) -> None:
    transcript = link.transcript
    for label, cmd in SWEEP_COMMANDS:
        try:
            data = link.query(cmd)
            shown: object = data
        except TimeoutError:
            data = b""
            shown = b"<timeout>"
        errors = link.query_errors_clean()
        print(f"{cmd:36} -> {shown!r}")
        for err in errors:
            print(f"{'':36}    error: {err}")
        if transcript is not None:
            transcript.log(
                "sweep",
                cmd,
                data,
                note=json.dumps({"label": label, "errors": errors}),
            )
        time.sleep(pause)


def _print_labelled(rows: list[tuple[str, bytes]]) -> None:
    for label, data in rows:
        print(f"{label:20} = {data!r}")


def cmd_idn(link: ProbeLink, _args: argparse.Namespace) -> None:
    print(link.query("*IDN?").decode("ascii", "replace"))


def cmd_options(link: ProbeLink, _args: argparse.Namespace) -> None:
    print(link.query("*OPT?").decode("ascii", "replace"))


def cmd_rx(link: ProbeLink, _args: argparse.Namespace) -> None:
    rows = []
    for label, cmd in RX_QUERIES:
        try:
            rows.append((label, link.query(cmd)))
        except TimeoutError:
            rows.append((label, b"<timeout>"))
        link.query_errors_clean()
    _print_labelled(rows)


def cmd_traces(link: ProbeLink, _args: argparse.Namespace) -> None:
    catalog = link.query("TRACe:CATalog?").decode("ascii", "replace")
    print(f"catalog: {catalog}")
    for name in catalog.replace('"', "").strip().split(","):
        name = name.strip()
        if not name:
            continue
        try:
            print(f"points {name:10}: {link.query(f'TRACe:POINts? {name}')!r}")
        except TimeoutError:
            print(f"points {name:10}: <timeout>")


def cmd_udp(link: ProbeLink, _args: argparse.Namespace) -> None:
    print(link.query("TRACE:UDP?").decode("ascii", "replace"))


def cmd_level(link: ProbeLink, _args: argparse.Namespace) -> None:
    print(link.query('SENS:DATA? "VOLT:AC"').decode("ascii", "replace"))


def cmd_errors(link: ProbeLink, _args: argparse.Namespace) -> None:
    for msg in link.query_errors():
        print(msg)


def cmd_raw(args: argparse.Namespace, link: ProbeLink) -> None:
    cmd = args.cmd.strip()
    lowered = cmd.lower()
    if any(lowered.startswith(p.lower()) for p in FORBIDDEN_PREFIXES):
        print(
            f"refusing {cmd!r}: instrument reset/self-test forbidden",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if not lowered.endswith("?") and not args.unsafe:
        print(FORBIDDEN_HINT, file=sys.stderr)
        raise SystemExit(2)
    data = link.query(cmd)
    print(repr(data))
    errors = link.query_errors_clean()
    for err in errors:
        print(f"error: {err}")


def cmd_sweep(link: ProbeLink, args: argparse.Namespace) -> None:
    if link.transcript is None:
        link._transcript = Transcript.create(args.capture_dir, "sweep")
        print(f"transcript: {link.transcript.path}")
    run_sweep(link)


def cmd_exp(link: ProbeLink, args: argparse.Namespace) -> None:
    if link.transcript is None:
        link._transcript = Transcript.create(args.capture_dir, "exp")
        print(f"transcript: {link.transcript.path}")
    experiments.run(link, args.name, args.duration)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eb200-probe",
        description=("Read-only discovery probe for a Rohde & Schwarz EB200 receiver"),
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=RESPONSE_TIMEOUT_S)
    parser.add_argument("--transcript", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("idn", help="print *IDN?").set_defaults(func=cmd_idn)
    sub.add_parser("options", help="print *OPT?").set_defaults(func=cmd_options)
    sub.add_parser("rx", help="print receiver state").set_defaults(func=cmd_rx)
    sub.add_parser("traces", help="print trace catalogue and points").set_defaults(
        func=cmd_traces
    )
    sub.add_parser("udp", help="print TRACE:UDP? list").set_defaults(func=cmd_udp)
    sub.add_parser("level", help="print S-meter value").set_defaults(func=cmd_level)
    sub.add_parser("errors", help="drain SCPI error queue").set_defaults(
        func=cmd_errors
    )

    raw = sub.add_parser("raw", help="send one command, print raw response")
    raw.add_argument("cmd")
    raw.add_argument("--unsafe", action="store_true")
    raw.set_defaults(func=cmd_raw)

    sweep = sub.add_parser("sweep", help="scripted read-only discovery sweep")
    sweep.add_argument("--capture-dir", type=Path, default=DEFAULT_CAPTURE_DIR)
    sweep.set_defaults(func=cmd_sweep)

    exp = sub.add_parser("exp", help="run a guarded experiment")
    exp.add_argument("name", choices=experiments.EXPERIMENTS)
    exp.add_argument("--duration", type=float, default=3.0)
    exp.add_argument("--capture-dir", type=Path, default=DEFAULT_CAPTURE_DIR)
    exp.set_defaults(func=cmd_exp)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    transcript = None
    if args.transcript is not None:
        args.transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript = Transcript(args.transcript)
    link = ProbeLink(args.host, args.port, args.timeout, transcript)
    try:
        args.func(link, args)
    finally:
        link.close()
        if transcript is not None:
            transcript.close()


if __name__ == "__main__":
    main()

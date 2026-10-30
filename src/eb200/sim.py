"""Scripted in-memory transport standing in for a physical EB200."""

from __future__ import annotations

import base64
import json
import math
import threading
from pathlib import Path

from . import framing
from .errors import (
    EB200Connection,
    EB200Protocol,
    EB200Timeout,
    SCPIError,
    parse_syst_error,
)

DEFAULT_ERRORS_RESPONSE = b'0,"No error"\r\n'

# Values captured live from the EB200 at 10.0.0.240 (firmware 2.80);
# see tests/fixtures/capture/20261030-161332-sweep.jsonl.
DEFAULT_RESPONSES: dict[str, bytes] = {
    "*IDN?": b"ROHDE&SCHWARZ,EB200,108.063/003,V02.80-4052.4654.00\r\n",
    "*OPT?": b"0,0,0,ER,0,0,F1,0\r\n",
    "*STB?": b"1\r\n",
    "*IST?": b"0\r\n",
    "SYST:VERS?": b"1996.0\r\n",
    "SYST:ERR?": DEFAULT_ERRORS_RESPONSE,
    "FREQ?": b"27260000\r\n",
    "FREQ:MODE?": b"CW\r\n",
    "FREQ:AFC?": b"0\r\n",
    "FREQ:STARt?": b"20000000\r\n",
    "FREQ:STOP?": b"100000000\r\n",
    "BAND?": b"15000\r\n",
    "BAND? MINimum": b"150\r\n",
    "BAND? MAXimum": b"150000\r\n",
    "DEM?": b"AM\r\n",
    "INP:ATT:AUTO?": b"0\r\n",
    "INP:ATT:STAT?": b"0\r\n",
    "GCON?": b"50\r\n",
    "GCON:MODE?": b"AUTO\r\n",
    "OUTP:SQU?": b"1\r\n",
    "OUTP:SQU:THR?": b"-30\r\n",
    "OUTP:SQU:CONT?": b"NONE\r\n",
    "SYST:AUD:VOL?": b"0.00\r\n",
    "SYST:AUD:BAL?": b"0.00\r\n",
    "SYST:AUD:REM:MODE?": b"0\r\n",
    "SWEep:STEP?": b"100000\r\n",
    "SWEep:DWELl?": b"0.000\r\n",
    "SWEep:HOLD:TIME?": b"0.000\r\n",
    "SWEep:COUNt?": b"9.9E37\r\n",
    "SWEep:DIRection?": b"UP\r\n",
    "STATus:OPERation:SWEeping:CONDition?": b"0\r\n",
    'SENS:DATA? "VOLT:AC"': b"-18.3\r\n",
    "TRACe:CATalog?": b'"MTRACE", "ITRACE", "IFPAN", "SSTART", "SSTOP", "UDP"\r\n',
    "TRACe:POINts? MTRACE": b"0\r\n",
    "TRACe:POINts? ITRACE": b"0\r\n",
    "TRACe:POINts? IFPAN": b"0\r\n",
    "DISP:ENAB?": b"1\r\n",
    "FORMat:DATA?": b"ASC\r\n",
    "TRACE:UDP?": b"DEF\n001\n002\n003\r\n",
    "STATus:OPERation:CONDition?": b"0\r\n",
}


class SimulatedTransport:
    """Replays captured responses with injectable faults.

    Matching is case-insensitive on whitespace-collapsed commands. Response
    values may be ``bytes``, an exception instance (raised when asked for) or
    ``None`` (simulates a response timeout). ``queue_responses`` feeds FIFO
    lists per command and takes precedence over the static map while
    non-empty.
    """

    def __init__(
        self,
        responses: dict[str, bytes] | None = None,
        default: bytes | None = None,
    ) -> None:
        self.requests: list[str] = []
        self._lock = threading.Lock()
        self._responses = {self._key(k): v for k, v in (responses or {}).items()}
        self._default = default
        self._queued: dict[str, list[bytes | Exception | None]] = {}
        self._faults: list[Exception] = []
        self.connected = True
        self.closed = False

    @staticmethod
    def _key(cmd: str) -> str:
        return " ".join(cmd.split()).upper()

    # -- configuration ------------------------------------------------------

    def set_response(self, cmd: str, value: bytes | None) -> None:
        with self._lock:
            self._responses[self._key(cmd)] = value

    def queue_responses(self, cmd: str, values: list[bytes | Exception | None]) -> None:
        with self._lock:
            self._queued.setdefault(self._key(cmd), []).extend(values)

    def inject_fault(self, error: Exception) -> None:
        with self._lock:
            self._faults.append(error)

    # -- simulated link state ----------------------------------------------

    def disconnect(self) -> None:
        self.connected = False

    def reconnect(self) -> None:
        self.connected = True

    # -- transport API ------------------------------------------------------

    def start(self) -> None:  # noqa: D102 - parity with TCPTransport
        return

    def close(self) -> None:  # noqa: D102 - parity with TCPTransport
        self.closed = True
        self.connected = False

    @property
    def is_connected(self) -> bool:
        """Link-state parity with TCPTransport."""
        return self.connected

    def reconnect_now(self, connect_timeout: float = 1.5) -> bool:
        """Bounded reconnect parity with TCPTransport (simulated link)."""
        del connect_timeout
        self.connected = True
        return True

    def query(self, cmd: str, timeout: float | None = None) -> bytes:
        self._record(cmd, timeout)
        self._raise_fault_if_any()
        if not self.connected:
            raise EB200Connection("simulated device is disconnected")
        value = self._response_for(cmd)
        if value is None:
            raise EB200Timeout(f"simulated timeout for {cmd!r}")
        if isinstance(value, Exception):
            raise value
        return value

    def command(self, cmd: str) -> None:
        self._record(cmd, None)
        self._raise_fault_if_any()
        if not self.connected:
            raise EB200Connection("simulated device is disconnected")

    def query_errors(self, limit: int = 32) -> list[SCPIError]:
        errors: list[SCPIError] = []
        for _ in range(limit):
            text = framing.decode_text(self.query("SYST:ERR?"))
            parsed = parse_syst_error(text)
            if parsed is None:
                break
            errors.append(SCPIError(code=parsed[0], message=parsed[1]))
        return errors

    # -- introspection ------------------------------------------------------

    def queries_sent(self) -> list[str]:
        return list(self.requests)

    # -- internals ----------------------------------------------------------

    def _record(self, cmd: str, timeout: float | None) -> None:
        with self._lock:
            self.requests.append(cmd if timeout is None else f"{cmd} (t={timeout})")

    def _raise_fault_if_any(self) -> None:
        with self._lock:
            if self._faults:
                raise self._faults.pop(0)

    def _response_for(self, cmd: str) -> bytes | Exception | None:
        key = self._key(cmd)
        with self._lock:
            pending = self._queued.get(key)
            if pending:
                return pending.pop(0)
            if key in self._responses:
                return self._responses[key]
        if self._default is None:
            raise EB200Protocol(f"no scripted response for {cmd!r}")
        return self._default

    # -- constructors -------------------------------------------------------

    @classmethod
    def with_defaults(cls) -> SimulatedTransport:
        return cls(responses=DEFAULT_RESPONSES)

    @classmethod
    def from_transcript(cls, path: str | Path) -> SimulatedTransport:
        """Build responses from a probe JSONL transcript (tx/rx pairs).

        Later answers to the same command win. Entries without a paired
        response (timeouts) are skipped; the generated transport therefore
        raises for those commands, matching the live behaviour.
        """
        responses: dict[str, bytes] = {}
        pending: str | None = None
        with Path(path).open("r", encoding="utf-8") as fh:
            for line in fh:
                entry = json.loads(line)
                kind = entry.get("kind")
                if kind == "tx" and entry.get("cmd"):
                    pending = entry["cmd"]
                    continue
                if kind != "rx" or pending is None:
                    pending = pending if kind == "tx" else None
                    continue
                data = base64.b64decode(entry.get("data_b64", ""))
                responses[pending] = data
                pending = None
        return cls(responses=responses)


def _sweep_level(start: int, step: int, index: int, steps: int) -> float:
    """Deterministic synthetic spectrum: noise floor plus a centre peak."""
    frequency = start + step * index
    jitter = ((frequency // max(step, 1)) * 7919) % 31 / 10.0
    level = -19.0 + jitter
    distance = abs(index - (steps - 1) / 2)
    if distance < 3:
        level += 35.0 - distance * 8.0  # a strong carrier in the band centre
    return level


class StatefulTransport(SimulatedTransport):
    """Simulated transport with a small receiver state machine (tests only).

    Set commands mutate in-memory state; queries read from it, so panel and
    server tests can observe the effects of control operations. Unknown
    queries raise loudly instead of returning fake data.
    """

    def __init__(self) -> None:
        super().__init__(responses={}, default=None)
        self.local_ip = "127.0.0.1"  # transport parity
        self._state_lock = threading.Lock()
        self._state: dict[str, float | int | str | bool] = {
            "frequency": 27_260_000,
            "bandwidth": 15_000,
            "demod": "AM",
            "freq_mode": "CW",
            "afc": False,
            "att_on": False,
            "att_auto": False,
            "gain": 50.0,
            "gain_mode": "AUTO",
            "squelch": True,
            "squelch_threshold": -30.0,
            "volume": 0.0,
            "remote_af_mode": 0,
            "level_dbuv": -18.3,
            "sweeping": False,
            "sweep_start": 20_000_000,
            "sweep_stop": 100_000_000,
            "sweep_step": 100_000,
            "sweep_count": math.inf,
        }
        # One-cycle sweep emulation: buffers served once, then consumed.
        self._pending_points = 0
        self._armed_trace: tuple[list[float], list[int]] | None = None
        # UDP trace registry emulation: up to three destination slots.
        self._registry: list[dict[str, str | int]] = []

    def _arm_sweep(self) -> None:
        """Fill the trace buffers for a one-cycle sweep, like the device."""
        with self._state_lock:
            state = {**self._state}
        if state["sweep_count"] != 1:
            return
        start = int(state["sweep_start"])
        stop = int(state["sweep_stop"])
        step = int(state["sweep_step"])
        if stop <= start or step <= 0:
            return
        steps = (stop - start) // step + 1
        levels = [_sweep_level(start, step, index, steps) for index in range(steps)]
        frequencies = [start + index * step for index in range(steps)]
        self._armed_trace = (levels, frequencies)
        self._pending_points = steps + 1  # levels + 9.9E37 terminator

    # -- transport API --------------------------------------------------------

    def command(self, cmd: str) -> None:
        self._raise_fault_if_any()
        with self._lock:
            self.requests.append(cmd)
        key = self._key(cmd)
        if key == "ABORT":
            with self._state_lock:
                self._state["sweeping"] = False
            return
        if key == "INIT":
            with self._state_lock:
                mode = str(self._state["freq_mode"])
                count = self._state["sweep_count"]
                self._state["sweeping"] = True
            if mode.upper().startswith("SWE") and count == 1:
                self._arm_sweep()
            return
        parts = cmd.strip().split(" ", 1)
        if len(parts) != 2:
            return
        head = " ".join(parts[0].split()).upper()
        value = parts[1].strip()
        if head.startswith("TRACE:UDP"):
            self._registry_command(head, value)
            return
        with self._state_lock:
            self._mutate(head, value)

    def _registry_command(self, head: str, value: str) -> None:
        import re as _re

        entry_match = _re.match(r'"([^"]+)",\s*(\d+)(?:,\s*(.+))?', value)
        if entry_match is None:
            return
        ip, port, tag = (
            entry_match.group(1),
            int(entry_match.group(2)),
            entry_match.group(3),
        )
        with self._state_lock:
            if head.endswith("TAG:ON"):
                self._registry = [
                    entry
                    for entry in self._registry
                    if not (entry["ip"] == ip and entry["port"] == port)
                ]
                self._registry.append(
                    {"ip": ip, "port": port, "tag": (tag or "").strip()}
                )
            elif head.endswith("TAG:OFF"):
                for entry in self._registry:
                    if entry["ip"] == ip and entry["port"] == port:
                        entry["tag"] = ""
            elif head.endswith("DELETE"):
                self._registry = [
                    entry
                    for entry in self._registry
                    if not (entry["ip"] == ip and entry["port"] == port)
                ]

    def _mutate(self, head: str, value: str) -> None:
        state = self._state
        boolean = value.strip().upper() in {"ON", "1"}
        if head == "FREQ":
            state["frequency"] = int(float(value))
        elif head == "BAND":
            state["bandwidth"] = int(float(value))
        elif head == "DEM":
            state["demod"] = value.upper()
        elif head == "FREQ:MODE":
            state["freq_mode"] = value
        elif head == "FREQ:AFC":
            state["afc"] = boolean
        elif head == "FREQ:START":
            state["sweep_start"] = int(float(value))
        elif head == "FREQ:STOP":
            state["sweep_stop"] = int(float(value))
        elif head == "INP:ATT:STAT":
            state["att_on"] = boolean
        elif head == "INP:ATT:AUTO":
            state["att_auto"] = boolean
        elif head == "OUTP:SQU":
            state["squelch"] = boolean
        elif head == "OUTP:SQU:THR":
            state["squelch_threshold"] = float(value)
        elif head == "GCON":
            state["gain"] = float(value)
        elif head == "GCON:MODE":
            state["gain_mode"] = (
                "FIXed" if value.upper().startswith("F") else value.upper()
            )
        elif head == "SYST:AUD:VOL":
            state["volume"] = float(value)
        elif head == "SYST:AUD:REM:MODE":
            state["remote_af_mode"] = int(float(value))
        elif head == "SWEep:STEP".upper():
            state["sweep_step"] = int(float(value))
        elif head == "SWEep:DWELl".upper():
            state["sweep_dwell"] = float(value)
        elif head == "SWEep:COUNt".upper():
            if "INF" in value.upper():
                state["sweep_count"] = math.inf
            else:
                state["sweep_count"] = float(value)
        else:  # unknown set command: tolerated like the instrument's silence
            return

    def query(self, cmd: str, timeout: float | None = None) -> bytes:
        self._raise_fault_if_any()
        key = self._key(cmd)
        if key == "TRACe:POINts? MTRACE".upper():
            with self._lock:
                self.requests.append(cmd)
            with self._state_lock:
                pending = self._pending_points
            return f"{pending}\r\n".encode()
        if key == "TRACE:UDP?":
            with self._lock:
                self.requests.append(cmd)
            with self._state_lock:
                entries = list(self._registry)
            lines = ["DEF", "001", "002", "003"]
            for index, entry in enumerate(entries[:3]):
                tag = str(entry.get("tag") or "")
                lines[1 + index] = (
                    f'{1 + index:03d} "{entry["ip"]}", {entry["port"]}, {tag[:3]}'
                )
            return ("\n".join(lines) + "\r\n").encode("ascii", "replace")
        if key in ("TRACe:DATA? MTRACE".upper(), "TRACe:DATA? ITRACE".upper()):
            with self._lock:
                self.requests.append(cmd)
            with self._state_lock:
                trace = self._armed_trace
                pending = self._pending_points
            if trace is None:
                raise EB200Protocol("stateful sim has no armed sweep trace")
            if key.endswith("MTRACE?".upper()) or "MTRACE" in key:
                if pending == 0:
                    raise EB200Protocol("stateful sim trace already consumed")
                levels, _frequencies = trace
                with self._state_lock:
                    self._pending_points = 0  # reading consumes the buffer
                payload = ",".join(f"{level:.1f}" for level in levels) + ",9.9E37"
                return f"{payload}\r\n".encode()
            # ITRACE: index/frequency pairs + terminator pair
            levels, frequencies = trace
            pairs = ",".join(
                f"{index},{freq}" for index, freq in enumerate(frequencies)
            )
            return f"{pairs},9.9E37,9.9E37\r\n".encode()
        with self._lock:
            self.requests.append(cmd)
        with self._state_lock:
            state = {**self._state}
        answer = self._answer(key, state)
        if answer is None:
            raise EB200Protocol(f"stateful sim has no answer for {cmd!r}")
        return f"{answer}\r\n".encode("ascii", "replace")

    # -- state machine --------------------------------------------------------

    @staticmethod
    def _answer(key: str, s: dict) -> str | None:
        if key == "*IDN?":
            return "ROHDE&SCHWARZ,EB200,108.063/003,V02.80-4052.4654.00"
        if key == "*OPT?":
            return "0,0,0,ER,0,0,F1,0"
        if key == "SYST:VERS?":
            return "1996.0"
        if key == "SYST:ERR?":
            return '0,"No error"'
        if key == "TRACE:CATALOG?":
            return '"MTRACE", "ITRACE", "IFPAN", "SSTART", "SSTOP", "UDP"'
        if key == "TRACe:POINts? MTRACE".upper():
            return "0"
        if key == "TRACE:UDP?":
            return "DEF\n001\n002\n003"
        if key == "STATUS:OPERATION:SWEEPING:CONDITION?":
            return "8" if s["sweeping"] else "0"
        if key == 'SENS:DATA? "VOLT:AC"':
            return f"{float(s['level_dbuv']):.1f}"
        boolean = lambda name: "1" if s[name] else "0"  # noqa: E731
        simple = {
            "FREQ?": f"{int(s['frequency'])}",
            "FREQ:MODE?": str(s["freq_mode"]),
            "FREQ:AFC?": boolean("afc"),
            "FREQ:START?": f"{int(s['sweep_start'])}",
            "FREQ:STOP?": f"{int(s['sweep_stop'])}",
            "BAND?": f"{int(s['bandwidth'])}",
            "BAND? MINIMUM": "150",
            "BAND? MAXIMUM": "150000",
            "DEM?": str(s["demod"]),
            "DEMODULATION?": str(s["demod"]),
            "SWEep:STEP?".upper(): f"{int(s['sweep_step'])}",
            "SWEep:COUNt?".upper(): (
                "9.9E37" if s["sweep_count"] == math.inf else f"{s['sweep_count']:.0f}"
            ),
            "SWEep:DWELl?".upper(): f"{float(s.get('sweep_dwell', 0.0)):.3f}",
            "SWEep:DIRection?".upper(): "UP",
            "INP:ATT:STAT?": boolean("att_on"),
            "INP:ATT:AUTO?": boolean("att_auto"),
            "GCON?": f"{float(s['gain']):g}",
            "GCON:MODE?": str(s["gain_mode"]),
            "OUTP:SQU?": boolean("squelch"),
            "OUTP:SQU:THR?": f"{float(s['squelch_threshold']):g}",
            "OUTP:SQU:CONT?": "NONE",
            "SYST:AUD:VOL?": f"{float(s['volume']):.2f}",
            "SYST:AUD:REM:MODE?": f"{int(s['remote_af_mode'])}",
            "DISP:ENAB?": "1",
        }
        return simple.get(key)

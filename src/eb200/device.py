"""Typed facade over the EB200 SCPI surface."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from . import framing
from .errors import EB200Protocol, SCPIError
from .transport import TCPTransport

# Documented receiver frequency range (10 kHz - 3 GHz).
FREQUENCY_MIN_HZ = 10_000
FREQUENCY_MAX_HZ = 3_000_000_000
# Live BAND? MINimum / MAXimum on fw 2.80.
BANDWIDTH_MIN_HZ = 150
BANDWIDTH_MAX_HZ = 150_000

# Documented SENSe:FREQuency:MODE set (availability depends on options).
FREQUENCY_MODES = ("CW", "FIXed", "SWEep", "MSCan", "DSCan", "FASTlevcw", "LIST")
# Demodulation set per the R&S Hamlib backend and weakmon; AM read-verified
# live. WFM is deliberately absent (not part of the documented DEM set here).
DEMODULATION_MODES = ("AM", "FM", "CW", "USB", "LSB")
# Documented SYSTem:AUDio:REMote:MODe table (0 = off, 13 = GSM 6.10).
REMOTE_AF_MODES = range(0, 14)
# UDP trace tags accepted by TRACE:UDP:TAG:ON on fw 2.80 (live-probed).
UDP_TRACE_TAGS = ("AUDIO", "IFPAN", "FSCAN", "MSCAN", "CW", "LIST")

_REGISTRY_ENTRY = re.compile(r'"(\d{1,3}(?:\.\d{1,3}){3})",\s*(\d+)')


@dataclass(frozen=True)
class DeviceInfo:
    raw: str
    manufacturer: str
    model: str
    serial: str
    firmware: str


def _text(data: bytes) -> str:
    return framing.decode_text(data).strip()


def _parse_int(data: bytes) -> int:
    return int(_text(data))


def _parse_float(data: bytes) -> float:
    return float(_text(data))


def _parse_bool(data: bytes) -> bool:
    return _text(data).upper() in {"1", "ON"}


def _on_off(value: bool) -> str:
    return "ON" if value else "OFF"


class EB200:
    """Receiver control facade. Set-commands are error-checked in strict mode."""

    def __init__(
        self,
        transport: TCPTransport,
        *,
        strict: bool = True,
        registry_settle: float = 0.3,
    ) -> None:
        self._t = transport
        self._strict = strict
        self._registry_settle = registry_settle

    # -- device -------------------------------------------------------------

    def identify(self) -> DeviceInfo:
        raw = _text(self._t.query("*IDN?"))
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 4 or parts[0] == "":
            raise EB200Protocol(f"unexpected *IDN? reply: {raw!r}")
        return DeviceInfo(
            raw=raw,
            manufacturer=parts[0],
            model=parts[1],
            serial=parts[2],
            firmware=parts[3],
        )

    def option_codes(self) -> list[str]:
        text = _text(self._t.query("*OPT?"))
        return [p for p in (t.strip() for t in text.split(",")) if p != "0"]

    def errors(self) -> list[SCPIError]:
        return self._t.query_errors()

    # -- frequency and demodulation ------------------------------------------

    def frequency(self) -> int:
        return _parse_int(self._t.query("FREQ?"))

    def set_frequency(self, hz: int) -> None:
        if not FREQUENCY_MIN_HZ <= hz <= FREQUENCY_MAX_HZ:
            raise ValueError(
                f"frequency {hz} Hz outside {FREQUENCY_MIN_HZ}-{FREQUENCY_MAX_HZ}"
            )
        self._set(f"FREQ {int(hz)}")

    def frequency_mode(self) -> str:
        return _text(self._t.query("FREQ:MODE?"))

    def set_frequency_mode(self, mode: str) -> None:
        if mode not in FREQUENCY_MODES:
            raise ValueError(f"unknown frequency mode {mode!r}")
        self._set(f"FREQ:MODE {mode}")

    def bandwidth(self) -> int:
        return _parse_int(self._t.query("BAND?"))

    def set_bandwidth(self, hz: int) -> None:
        if not BANDWIDTH_MIN_HZ <= hz <= BANDWIDTH_MAX_HZ:
            raise ValueError(
                f"bandwidth {hz} Hz outside {BANDWIDTH_MIN_HZ}-{BANDWIDTH_MAX_HZ}"
            )
        self._set(f"BAND {int(hz)}")

    def bandwidth_range(self) -> tuple[int, int]:
        low = _parse_int(self._t.query("BAND? MINimum"))
        high = _parse_int(self._t.query("BAND? MAXimum"))
        return low, high

    def demodulation(self) -> str:
        return _text(self._t.query("DEM?"))

    def set_demodulation(self, mode: str) -> None:
        if mode.upper() not in DEMODULATION_MODES:
            raise ValueError(f"unknown demodulation {mode!r}")
        self._set(f"DEM {mode}")

    def afc(self) -> bool:
        return _parse_bool(self._t.query("FREQ:AFC?"))

    def set_afc(self, enabled: bool) -> None:
        self._set(f"FREQ:AFC {_on_off(enabled)}")

    # -- signal path ---------------------------------------------------------

    def attenuator(self) -> bool:
        return _parse_bool(self._t.query("INP:ATT:STAT?"))

    def set_attenuator(self, enabled: bool) -> None:
        self._set(f"INP:ATT:STAT {_on_off(enabled)}")

    def attenuator_auto(self) -> bool:
        return _parse_bool(self._t.query("INP:ATT:AUTO?"))

    def set_attenuator_auto(self, enabled: bool) -> None:
        self._set(f"INP:ATT:AUTO {_on_off(enabled)}")

    def level(self) -> float:
        return _parse_float(self._t.query('SENS:DATA? "VOLT:AC"'))

    # -- squelch ---------------------------------------------------------------

    def squelch(self) -> bool:
        return _parse_bool(self._t.query("OUTP:SQU?"))

    def set_squelch(self, enabled: bool) -> None:
        self._set(f"OUTP:SQU {_on_off(enabled)}")

    def squelch_threshold(self) -> float:
        return _parse_float(self._t.query("OUTP:SQU:THR?"))

    def set_squelch_threshold(self, dbuv: float) -> None:
        self._set(f"OUTP:SQU:THR {dbuv:g}")

    # -- audio -------------------------------------------------------------------

    def af_volume(self) -> float:
        return _parse_float(self._t.query("SYST:AUD:VOL?"))

    def set_af_volume(self, volume: float) -> None:
        if not 0.0 <= volume <= 1.0:
            raise ValueError(f"volume {volume} outside 0.0-1.0")
        self._set(f"SYST:AUD:VOL {volume:.2f}")

    def af_balance(self) -> float:
        return _parse_float(self._t.query("SYST:AUD:BAL?"))

    def set_af_balance(self, balance: float) -> None:
        if not -0.5 <= balance <= 0.5:
            raise ValueError(f"balance {balance} outside -0.5-0.5")
        self._set(f"SYST:AUD:BAL {balance:.2f}")

    def remote_af_mode(self) -> int:
        return _parse_int(self._t.query("SYST:AUD:REM:MODE?"))

    def set_remote_af_mode(self, mode: int) -> None:
        if mode not in REMOTE_AF_MODES:
            raise ValueError(f"remote AF mode {mode} outside 0-13")
        self._set(f"SYST:AUD:REM:MODE {mode}")

    # -- traces ------------------------------------------------------------------

    def trace_catalogue(self) -> list[str]:
        text = _text(self._t.query("TRACe:CATalog?"))
        return re.findall(r'"([^"]+)"', text)

    def trace_points(self, name: str) -> int:
        return _parse_int(self._t.query(f"TRACe:POINts? {name}"))

    def trace_data(self, name: str) -> bytes:
        return self._t.query(f"TRACe:DATA? {name}")

    # -- undocumented UDP trace registry ----------------------------------------
    #
    # Behaviour established live on fw 2.80 (see docs/protocol-notes.md):
    # the registry holds up to three destination entries; TAG:ON registers
    # one, TAG:OFF detaches the tag but *leaves (or creates) an entry*,
    # and DELETE removes one entry per call. Registry changes apply
    # asynchronously, so every check runs after a settle delay.

    def udp_registry(self) -> str:
        return _text(self._t.query("TRACE:UDP?"))

    def udp_entries(self, ip: str | None = None) -> list[tuple[str, int]]:
        """Registered destinations as (ip, port) pairs, optionally filtered."""
        entries = [
            (host, int(port))
            for host, port in _REGISTRY_ENTRY.findall(self.udp_registry())
        ]
        if ip is None:
            return entries
        return [entry for entry in entries if entry[0] == ip]

    def udp_register(
        self, ip: str, port: int, tag: str, *, settle: float | None = None
    ) -> None:
        if tag not in UDP_TRACE_TAGS:
            raise ValueError(f"tag {tag!r} not in known UDP tags {UDP_TRACE_TAGS}")
        self._set(f'TRACE:UDP:TAG:ON "{ip}",{port},{tag}')
        self._await_registry(ip, port, present=True, settle=settle)

    def udp_unregister(
        self, ip: str, port: int, tag: str | None = None, *, settle: float | None = None
    ) -> None:
        """Detach the tag (if given) and delete the entry for ip:port.

        The initial DELETE always runs; when a settle time is configured,
        verification repeats DELETEs until every entry for the pair is gone
        (the receiver removes one entry per call).
        """
        if tag is not None:
            if tag not in UDP_TRACE_TAGS:
                raise ValueError(f"tag {tag!r} not in known UDP tags {UDP_TRACE_TAGS}")
            self._set(f'TRACE:UDP:TAG:OFF "{ip}",{port},{tag}')
        self._set(f'TRACE:UDP:DELETE "{ip}",{port}')
        self._await_registry(ip, port, present=False, settle=settle)

    def udp_clear(self, ip: str | None = None, *, settle: float | None = None) -> int:
        """Delete registry entries (all, or only those aimed at ip).

        Returns the number of DELETE commands issued. Raises EB200Protocol
        when entries survive every attempt.
        """
        removed = 0
        for _round in range(8):
            targets = self.udp_entries(ip)
            if not targets:
                return removed
            for host, port in dict.fromkeys(targets):
                self._t.command(f'TRACE:UDP:DELETE "{host}",{port}')
                self._t.query_errors()  # tolerate 'nothing to delete'
                removed += 1
            settle_s = self._registry_settle if settle is None else settle
            if settle_s > 0:
                time.sleep(settle_s)
        raise EB200Protocol(
            f"UDP registry entries for {ip or 'any host'} did not clear"
        )

    def _registry_contains(self, ip: str, port: int) -> bool:
        return (
            re.search(rf'"{re.escape(ip)}"\s*,\s*{port}\b', self.udp_registry())
            is not None
        )

    def _await_registry(
        self, ip: str, port: int, *, present: bool, settle: float | None
    ) -> None:
        settle = self._registry_settle if settle is None else settle
        if settle <= 0:
            return
        time.sleep(settle)
        for _round in range(6):
            if self._registry_contains(ip, port) == present:
                return
            if not present:
                # DELETE removes one entry per call; repeat as needed.
                self._t.command(f'TRACE:UDP:DELETE "{ip}",{port}')
                self._t.query_errors()  # tolerate 'nothing to delete'
            time.sleep(settle)
        state = "appear" if present else "clear"
        raise EB200Protocol(f"UDP registry entry for {ip}:{port} did not {state}")

    # -- internals ---------------------------------------------------------------

    def _set(self, cmd: str) -> None:
        self._t.command(cmd)
        if not self._strict:
            return
        errors = self._t.query_errors()
        if errors:
            raise errors[0]

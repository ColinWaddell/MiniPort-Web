"""Guarded live experiments against the EB200.

Each experiment follows the same discipline: record the receiver's current
state, perform the (reversible) test, restore the recorded state, then
re-verify it by query and drain the SCPI error queue. Every exchange is
written to the transcript, so the result doubles as a regression fixture.

The `TRACE:UDP:*` commands exercised here are undocumented in the manual but
known to work on EB200 firmware from community implementations (weakmon,
runds_connector); whether they behave the same on this unit is exactly what
the experiments answer. Note that registration entries have proven sticky:
`_clean_udp_tags` removes them again and verifies the baseline list.
"""

from __future__ import annotations

import re
import socket
import struct
import time

AUDIO_MODE_8K = 10  # 8 kHz, 16 bit, 1 channel (SYSTem:AUDio:REMote:MODe table)

UDP_BASELINE = "DEF\n001\n002\n003"
UDP_ENTRY_RE = re.compile(r'"(\d{1,3}(?:\.\d{1,3}){3})",\s*(\d+)')
TRACE_NAMES = ("AUDIO", "IF")

EXPERIMENTS = (
    "trace-format",
    "audio-udp",
    "if-udp",
    "tag-names",
    "mode-probe",
    "sweep-fscan",
    "trace-buffer",
)


def run(link, name: str, duration: float) -> None:
    if name == "trace-format":
        trace_format(link)
    elif name == "audio-udp":
        audio_udp(link, duration)
    elif name == "if-udp":
        if_udp(link, duration)
    elif name == "tag-names":
        tag_names(link)
    elif name == "mode-probe":
        mode_probe(link)
    elif name == "sweep-fscan":
        sweep_fscan(link, duration)
    elif name == "trace-buffer":
        trace_buffer(link, duration)
    else:  # pragma: no cover - argparse restricts choices
        raise ValueError(f"unknown experiment {name!r}")


# --------------------------------------------------------------------------- #
# UDP trace registry helpers
# --------------------------------------------------------------------------- #


def _udp_list(link) -> str:
    return link.query("TRACE:UDP?").decode("ascii", "replace")


def _udp_at_baseline(link) -> bool:
    return _udp_list(link).strip() == UDP_BASELINE


def _clean_udp_tags(link) -> bool:
    """Remove UDP trace registrations until the baseline list returns.

    TAG:ON/OFF/DELETE have no documented syntax and the receiver applies
    changes asynchronously (an immediate re-query still shows the old
    list), so a short settle delay follows every command. Plain
    'TRACE:UDP:DELETE "ip",port' appears to be the effective command;
    the other variants are kept as fallbacks. Returns True when the
    TRACE:UDP? list matches the no-entry baseline again.
    """
    if _udp_at_baseline(link):
        print("    UDP trace list already at baseline")
        return True
    print(f"    UDP trace list before cleanup: {_udp_list(link)!r}")
    addrs = set(UDP_ENTRY_RE.findall(_udp_list(link)))
    cmds: list[str] = []
    for ip, port in addrs:
        cmds.append(f'TRACE:UDP:DELETE "{ip}",{port}')
        for trace in TRACE_NAMES:
            cmds.append(f'TRACE:UDP:TAG:OFF "{ip}",{port},{trace}')
        cmds.append(f'TRACE:UDP:DELETE "{ip}",{port}')
    for slot in (1, 2, 3):
        cmds.append(f"TRACE:UDP:DELETE {slot:03d}")
    for cmd in cmds:
        link.issue(cmd)
        errors = link.query_errors_clean()
        print(f"    cleanup {cmd} -> {'ok' if not errors else errors[0]}")
        time.sleep(0.2)
        if _udp_at_baseline(link):
            break
    time.sleep(0.2)
    restored = _udp_at_baseline(link)
    print(f"    UDP trace list after cleanup: {_udp_list(link)!r}")
    return restored


# --------------------------------------------------------------------------- #
# generic experiment helpers
# --------------------------------------------------------------------------- #


def _report(datagram: bytes) -> str:
    if len(datagram) < 20:
        return f"short packet len={len(datagram)} {datagram.hex()}"
    magic, vmin, vmaj, seq, _res, size = struct.unpack(">IHHHHI", datagram[:16])
    ga_tag, ga_len = struct.unpack(">HH", datagram[16:20])
    text = (
        f"magic=0x{magic:08X} ver={vmin}.{vmaj} seq={seq} size={size} "
        f"ga={ga_tag}(len={ga_len})"
    )
    if ga_tag in (401, 901) and len(datagram) >= 28:
        values, _bits, opt_len, flags = struct.unpack(">HBBI", datagram[20:28])
        text += f" attr: values={values} opt={opt_len} flags=0x{flags:X}"
    return text


def _collect_udp(sock: socket.socket, duration: float, link) -> list[bytes]:
    """Read UDP datagrams for ``duration`` seconds, logging each one."""
    datagrams: list[bytes] = []
    end = time.monotonic() + duration
    sock.settimeout(0.25)
    while True:
        if time.monotonic() >= end:
            break
        try:
            data = sock.recv(65535)
        except TimeoutError:
            continue
        except OSError as exc:
            print(f"    udp socket closed: {exc}")
            break
        datagrams.append(data)
        if link.transcript is not None:
            link.transcript.log("rx-udp", None, data)
    return datagrams


def _print_capture(datagrams: list[bytes]) -> None:
    print(f"    datagrams received: {len(datagrams)}")
    sizes = sorted({len(d) for d in datagrams})
    if len(sizes) <= 5:
        print(f"    sizes: {sizes}")
    for dg in datagrams[:2]:
        print(f"    first bytes {dg.hex()[:120]}")
        print(f"    header: {_report(dg)}")
    seqs = [int.from_bytes(dg[8:10], "big") for dg in datagrams if len(dg) >= 10]
    losses = sum(
        1 for a, b in zip(seqs, seqs[1:], strict=False) if ((b - a) & 0xFFFF) > 1
    )
    if len(seqs) > 1:
        print(f"    seq first/last: {seqs[0]} .. {seqs[-1]}, gaps: {losses}")


def _register_trace(link, ip: str, port: int, trace: str) -> str | None:
    candidates = (
        f'TRACE:UDP:TAG:ON "{ip}",{port},{trace}',
        f'TRACE:UDP:DEFAULT:TAG:ON "{ip}", {port}, {trace}',
        f'TRACE:UDP:DEFAULT:TAG:ON "{ip}", {port}, {trace}',
    )
    for cmd in candidates:
        link.issue(cmd)
        errors = link.query_errors_clean()
        if errors:
            print(f"    {cmd} -> rejected: {errors[0]}")
            continue
        print(f"    registered with: {cmd}")
        return cmd
    return None


def _verify_restored(link, expectations: list[tuple[str, str]]) -> None:
    for cmd, expected_note in expectations:
        data = link.query(cmd).decode("ascii", "replace").strip()
        print(f"    restored check {cmd}: {data!r} (expected {expected_note!r})")


# --------------------------------------------------------------------------- #
# experiments
# --------------------------------------------------------------------------- #


def trace_format(link) -> None:
    """Compare SENS:DATA? and TRACe:DATA? responses under FORMat:DATA."""
    transcript = link.transcript
    orig = link.query("FORMat:DATA?")
    orig_text = orig.decode("ascii", "replace").strip()
    print(f"initial FORMat:DATA : {orig!r}")

    samples: list[tuple[str, str]] = []
    try:
        for fmt in ("PACKed",):
            link.issue(f"FORMat:DATA {fmt}")
            errors = link.query_errors_clean()
            if errors:
                print(f"  FORMat:DATA {fmt!r} rejected: {errors[0]}")
            for label, cmd in (
                ("level", 'SENS:DATA? "VOLT:AC"'),
                ("level-again", 'SENS:DATA? "VOLT:AC"'),
                ("mtrace", "TRACe:DATA? MTRACE"),
                ("itrace", "TRACe:DATA? ITRACE"),
            ):
                try:
                    data = link.query(cmd)
                except TimeoutError:
                    data = b"<timeout>"
                errors = link.query_errors_clean()
                print(f"  [{fmt:>7}] {label:12} -> {data!r}  errors={errors or '-'}")
                if transcript is not None:
                    transcript.log(
                        "experiment",
                        cmd,
                        data,
                        note=str({"format": fmt, "errors": errors}),
                    )
                samples.append((f"{fmt}/{label}", data.hex()))
        print(f"  hex summary: { {k: v[:64] for k, v in samples} }")
    finally:
        link.issue(f"FORMat:DATA {orig_text or 'ASC'}")
        errors = link.query_errors_clean()
        if errors:
            print(f"  restore {orig_text!r} rejected: {errors[0]}; retrying with 'ASC'")
            link.issue("FORMat:DATA ASC")
            link.query_errors_clean()
        _verify_restored(link, [("FORMat:DATA?", orig_text or "ASC")])


def audio_udp(link, duration: float) -> None:
    """Enable remote AF, capture UDP audio datagrams, then restore state.

    Command order and the tag variants follow weakmon (mode 10 first, then
    tag registration). A squelch-off window (squelch can gate AF) and the
    DEFAULT tag variant are tried if the first window is silent.
    """
    orig_udp = _udp_list(link)
    orig_mode = link.query("SYST:AUD:REM:MODE?").decode("ascii", "replace").strip()
    orig_squelch = link.query("OUTP:SQU?").decode("ascii", "replace").strip()
    print(f"initial TRACE:UDP? : {orig_udp!r}")
    print(f"initial SYST:AUD:REM:MODE? : {orig_mode}")
    print(f"initial OUTP:SQU? : {orig_squelch}")
    _clean_udp_tags(link)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    ip = link.local_ip
    print(f"waiting for audio on {ip}:{port}")

    registered: str | None = None
    squelch_restored = False
    try:
        # weakmon order: audio mode first, then the tag registration.
        link.issue(f"SYST:AUD:REM:MODE {AUDIO_MODE_8K}")
        errors = link.query_errors_clean()
        print(f"    SYST:AUD:REM:MODE 10 -> {'accepted' if not errors else errors}")
        registered = _register_trace(link, ip, port, "AUDIO")
        trace_status = link.query("STATus:TRACe:CONDition?")
        print(f"    STATus:TRACe:CONDition? -> {trace_status!r}")
        datagrams = _collect_udp(sock, duration, link)
        print("    - window 1: mode 10 + TAG:ON AUDIO -")
        _print_capture(datagrams)

        if not datagrams and orig_squelch == "1":
            # Squelch may gate the AF output; briefly force it open.
            link.issue("OUTP:SQU OFF")
            errors = link.query_errors_clean()
            print(f"    OUTP:SQU OFF -> {'accepted' if not errors else errors}")
            datagrams = _collect_udp(sock, duration, link)
            print("    - window 2: squelch forced off -")
            _print_capture(datagrams)
            link.issue(f"OUTP:SQU {orig_squelch}")
            link.query_errors_clean()
            squelch_restored = True

        if not datagrams:
            # Last variant: the DEFAULT TAG path used by weakmon.
            link.issue(f'TRACE:UDP:DEFAULT:TAG:ON "{ip}", {port}, AUDIO')
            errors = link.query_errors_clean()
            print(
                "    TRACE:UDP:DEFAULT:TAG:ON -> "
                f"{'accepted' if not errors else errors}"
            )
            registered = registered or "DEFAULT-variant"
            datagrams = _collect_udp(sock, duration, link)
            print("    - window 3: DEFAULT tag variant -")
            _print_capture(datagrams)
    finally:
        sock.close()
        if not squelch_restored:
            link.issue(f"OUTP:SQU {orig_squelch}")
            link.query_errors_clean()
        link.issue(f"SYST:AUD:REM:MODE {orig_mode or '0'}")
        link.query_errors_clean()
        try:
            _clean_udp_tags(link)
        except TimeoutError:
            print("    cleanup: query timed out; re-check TRACE:UDP? manually")
        _verify_restored(
            link,
            [
                ("TRACE:UDP?", UDP_BASELINE),
                ("SYST:AUD:REM:MODE?", orig_mode or "0"),
                ("OUTP:SQU?", orig_squelch or "1"),
            ],
        )
        link.query_errors_clean()
        print("    cleanup done")


def if_udp(link, duration: float) -> None:
    """Attempt to enable the undocumented UDP IF trace (spectrum data)."""
    orig_udp = _udp_list(link)
    orig_mode = link.query("FREQ:MODE?").decode("ascii", "replace").strip()
    print(f"initial TRACE:UDP? : {orig_udp!r}")
    print(f"initial FREQ:MODE? : {orig_mode}")
    _clean_udp_tags(link)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    ip = link.local_ip
    print(f"waiting for IF data on {ip}:{port}")

    try:
        _register_trace(link, ip, port, "IF")
        # runds sends the PRIVATE flag as a separate command.
        link.issue(f'TRACE:UDP:FLAG:ON "{ip}",{port},"PRIVATE"')
        errors = link.query_errors_clean()
        print(f"    FLAG:ON PRIVATE -> {'accepted' if not errors else errors}")
        datagrams = _collect_udp(sock, duration, link)
        print("    - window 1: IF tag in CW mode -")
        _print_capture(datagrams)
        if not datagrams:
            link.issue("SYST:IF:REM:MODE SHORT")
            errors = link.query_errors_clean()
            print(
                f"    SYST:IF:REM:MODE SHORT -> {'accepted' if not errors else errors}"
            )
            datagrams = _collect_udp(sock, duration, link)
            print("    - window 2: after SYST:IF:REM:MODE SHORT -")
            _print_capture(datagrams)
    finally:
        sock.close()
        try:
            _clean_udp_tags(link)
        except TimeoutError:
            print("    cleanup: query timed out; re-check TRACE:UDP? manually")
        _verify_restored(
            link, [("TRACE:UDP?", UDP_BASELINE), ("FREQ:MODE?", orig_mode)]
        )
        link.query_errors_clean()
        print("    cleanup done")


def tag_names(link) -> None:
    """Probe which trace names TRACE:UDP:TAG:ON accepts (one at a time).

    Each accepted name is cleaned up again immediately. If a tag produces
    datagrams on its own (no audio mode needed), they are captured.
    """
    _clean_udp_tags(link)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    ip = link.local_ip
    accepted: list[str] = []
    try:
        for name in (
            "IFPAN",
            "FS",
            "CM",
            "LEVEL",
            "MEAS",
            "FSCAN",
            "MSCAN",
            "CW",
            "LIST",
            "TRACE",
            "UDP",
        ):
            cmd = f'TRACE:UDP:TAG:ON "{ip}",{port},{name}'
            link.issue(cmd)
            errors = link.query_errors_clean()
            if errors:
                print(f"  {name:8} rejected: {errors[0]}")
                continue
            accepted.append(name)
            datagrams = _collect_udp(sock, 1.5, link)
            print(f"  {name:8} ACCEPTED, datagrams={len(datagrams)}")
            if datagrams:
                _print_capture(datagrams)
            link.issue(f'TRACE:UDP:TAG:OFF "{ip}",{port},{name}')
            link.query_errors_clean()
            link.issue(f'TRACE:UDP:DELETE "{ip}",{port}')
            link.query_errors_clean()
            if not _udp_at_baseline(link):
                print(f"  {name:8} did not clean up; running full cleanup")
                _clean_udp_tags(link)
            print(f"  {name:8} done;")
    finally:
        sock.close()
        _clean_udp_tags(link)
        print(f"  accepted tag names: {accepted}")
        _verify_restored(link, [("TRACE:UDP?", UDP_BASELINE)])


def sweep_fscan(link, duration: float) -> None:
    """Configure a small FSCAN sweep and capture the FSCAN UDP datagrams.

    Exercises FREQ:STARt/STOP, SWEep:STEP/DWELl, FREQ:MODE SWEep and the
    undocumented FSCAN trace tag; everything is restored afterwards (mode,
    sweep band, step, registry).
    """
    start_state: dict[str, bytes | None] = {}
    for key, cmd in (
        ("mode", "FREQ:MODE?"),
        ("frequency", "FREQ?"),
        ("start", "FREQ:STARt?"),
        ("stop", "FREQ:STOP?"),
        ("sweep-step", "SWEep:STEP?"),
        ("sweep-dwell", "SWEep:DWELl?"),
        ("sweep-hold", "SWEep:HOLD:TIME?"),
        ("sweep-count", "SWEep:COUNt?"),
        ("sweep-direction", "SWEep:DIRection?"),
        ("squelch-threshold", "OUTP:SQU:THR?"),
        ("gain", "GCON?"),
        ("gain-mode", "GCON:MODE?"),
    ):
        try:
            start_state[key] = link.query(cmd)
        except Exception as exc:  # noqa: BLE001 - unknown command is data
            start_state[key] = None
            link.query_errors_clean()
            print(f"  {key}: query failed: {exc}")
        else:
            print(f"  {key}: {start_state[key]!r}")
    link.query_errors_clean()
    _clean_udp_tags(link)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    ip = link.local_ip
    print(f"waiting for FSCAN data on {ip}:{port}")

    def set_checked(cmd: str) -> None:
        link.issue(cmd)
        errors = link.query_errors_clean()
        if errors:
            print(f"    {cmd} rejected: {errors[0]}")

    try:
        orig_display = None
        try:
            orig_display = link.query("DISP:MENU?")
        except Exception:  # noqa: BLE001 - query may not exist
            link.query_errors_clean()
        print(f"  DISP:MENU? -> {orig_display!r}")

        def window(label: str, seconds: float) -> int:
            datagrams = _collect_udp(sock, seconds, link)
            print(f"    - {label}: {len(datagrams)} datagrams in {seconds}s -")
            if datagrams:
                print(f"    first: {_report(datagrams[0])}")
                print(f"    bytes 16..64: {datagrams[0][16:64].hex()}")
            return len(datagrams)

        # 1. Sweep first, tag registered afterwards (weakmon ordering).
        set_checked("FREQ:STARt 26800000")
        set_checked("FREQ:STOP 27800000")
        set_checked("SWEep:STEP 25000")
        set_checked("FREQ:MODE SWEep")
        _register_trace(link, ip, port, "FSCAN")
        set_checked("INIT")
        window("sweep + tag + INIT", duration)

        # 2. Raise the squelch threshold so only strong signals hold.
        set_checked("OUTP:SQU:THR -10")
        set_checked("INIT")
        window("squelch threshold -10 dBuV", duration)

        # 3. Real signals: the FM broadcast band.
        set_checked("FREQ:STARt 87500000")
        set_checked("FREQ:STOP 108000000")
        set_checked("SWEep:STEP 100000")
        set_checked("INIT")
        window("FM broadcast band", duration)

        # 4. Dwell-hold semantics (hold criterion met every step).
        set_checked("SWEep:DWELl 0.200")
        set_checked("INIT")
        window("dwell 200 ms", duration)

        # 5. FLAG variant without quotes (runds used a quoted PRIVATE).
        set_checked(f'TRACE:UDP:FLAG:ON "{ip}",{port},PRIVATE')
        set_checked("INIT")
        window("FLAG:ON PRIVATE unquoted", duration)
        link.issue(f'TRACE:UDP:FLAG:OFF "{ip}",{port},PRIVATE')
        link.query_errors_clean()
    finally:
        sock.close()
        link.issue("ABORt")
        link.query_errors_clean()
        if orig_display is not None:
            orig_menu = orig_display.decode("ascii", "replace").strip().strip('"')
            link.issue(f"DISP:MENU {orig_menu}")
            link.query_errors_clean()
        for key, prefix in (
            ("frequency", "FREQ"),
            ("squelch-threshold", "OUTP:SQU:THR"),
            ("mode", "FREQ:MODE"),
            ("start", "FREQ:STARt"),
            ("stop", "FREQ:STOP"),
            ("sweep-step", "SWEep:STEP"),
            ("sweep-dwell", "SWEep:DWELl"),
        ):
            recorded = start_state[key]
            if recorded is not None:
                link.issue(f"{prefix} {recorded.decode('ascii', 'replace').strip()}")
        link.query_errors_clean()
        try:
            _clean_udp_tags(link)
        except TimeoutError:
            print("    cleanup: query timed out; re-check TRACE:UDP? manually")
        checks = []
        for key, cmd in (
            ("mode", "FREQ:MODE?"),
            ("frequency", "FREQ?"),
            ("start", "FREQ:STARt?"),
            ("stop", "FREQ:STOP?"),
            ("sweep-step", "SWEep:STEP?"),
            ("sweep-dwell", "SWEep:DWELl?"),
            ("sweep-count", "SWEep:COUNt?"),
            ("squelch-threshold", "OUTP:SQU:THR?"),
        ):
            recorded = start_state[key]
            note = (
                "unknown at start"
                if recorded is None
                else recorded.decode("ascii", "replace").strip()
            )
            checks.append((cmd, note))
        _verify_restored(link, checks)
        link.query_errors_clean()
        print("    cleanup done")


def trace_buffer(link, duration: float = 12.0) -> None:
    """Run a one-cycle FSCAN sweep and read MTRACE/ITRACE (documented path).

    Enables TRACE:FEED:CONTrol for both buffers, sweeps 26.8-27.8 MHz in
    25 kHz steps once, then reads the trace buffers; everything restored.
    """
    start_state: dict[str, bytes | None] = {}
    for key, cmd in (
        ("mode", "FREQ:MODE?"),
        ("frequency", "FREQ?"),
        ("start", "FREQ:STARt?"),
        ("stop", "FREQ:STOP?"),
        ("sweep-step", "SWEep:STEP?"),
        ("sweep-count", "SWEep:COUNt?"),
        ("format", "FORMat:DATA?"),
        ("points-mtrace", "TRACe:POINts? MTRACE"),
        ("points-itrace", "TRACe:POINts? ITRACE"),
    ):
        try:
            start_state[key] = link.query(cmd)
        except Exception as exc:  # noqa: BLE001
            start_state[key] = None
            link.query_errors_clean()
            print(f"  {key}: query failed: {exc}")
        else:
            print(f"  {key}: {start_state[key]!r}")
    link.query_errors_clean()

    def set_checked(cmd: str) -> None:
        link.issue(cmd)
        errors = link.query_errors_clean()
        if errors:
            print(f"    {cmd} rejected: {errors[0]}")

    try:
        set_checked("TRACE:FEED:CONTrol MTRACE,ALWayS")
        set_checked("TRACE:FEED:CONTrol ITRACE,ALWayS")
        set_checked("FREQ:STARt 26800000")
        set_checked("FREQ:STOP 27800000")
        set_checked("SWEep:STEP 25000")
        set_checked("SWEep:COUNt 1")
        set_checked("FREQ:MODE SWEep")
        set_checked("INIT")
        time.sleep(0.5)
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            points = (
                link.query("TRACe:POINts? MTRACE").decode("ascii", "replace").strip()
            )
            sweeping = (
                link.query("STATus:OPERation:SWEeping:CONDition?")
                .decode("ascii", "replace")
                .strip()
            )
            print(f"    points={points} sweeping={sweeping}")
            if points != "0" and sweeping == "0":
                break
            time.sleep(0.5)
        mtrace = link.query("TRACe:DATA? MTRACE")
        itrace = link.query("TRACe:DATA? ITRACE")
        print("    MTRACE:", repr(mtrace[:400]))
        print("    ITRACE:", repr(itrace[:400]))
        print("    MTRACE points:", repr(link.query("TRACe:POINts? MTRACE")))
        print("    ITRACE points:", repr(link.query("TRACe:POINts? ITRACE")))
        set_checked("FORMat:DATA PACKed")
        packed = link.query("TRACe:DATA? MTRACE")
        print(
            "    PACKed MTRACE:", repr(packed[:200]), "... total", len(packed), "bytes"
        )
    finally:
        link.issue("ABORt")
        link.query_errors_clean()
        for key, prefix in (
            ("mode", "FREQ:MODE"),
            ("frequency", "FREQ"),
            ("start", "FREQ:STARt"),
            ("stop", "FREQ:STOP"),
            ("sweep-step", "SWEep:STEP"),
            ("sweep-count", "SWEep:COUNt"),
            ("format", "FORMat:DATA"),
        ):
            recorded = start_state[key]
            if recorded is not None:
                value = recorded.decode("ascii", "replace").strip()
                if key == "sweep-count" and "E37" in value:
                    value = "INFinity"
                link.issue(f"{prefix} {value}")
        # FEED control: try to return both buffers to NEVer.
        for trace in ("MTRACE", "ITRACE"):
            link.issue(f"TRACE:FEED:CONTrol {trace},NEVer")
        link.query_errors_clean()
        checks = []
        for key, cmd in (
            ("mode", "FREQ:MODE?"),
            ("frequency", "FREQ?"),
            ("start", "FREQ:STARt?"),
            ("stop", "FREQ:STOP?"),
            ("sweep-step", "SWEep:STEP?"),
            ("sweep-count", "SWEep:COUNt?"),
            ("format", "FORMat:DATA?"),
            ("points-mtrace", "TRACe:POINts? MTRACE"),
            ("points-itrace", "TRACe:POINts? ITRACE"),
        ):
            recorded = start_state[key]
            note = (
                "unknown at start"
                if recorded is None
                else recorded.decode("ascii", "replace").strip()
            )
            checks.append((cmd, note))
        _verify_restored(link, checks)
        link.query_errors_clean()
        print("    cleanup done")


def mode_probe(link) -> None:
    transcript = link.transcript
    orig = link.query("FREQ:MODE?").decode("ascii", "replace").strip()
    catalog = link.query("TRACe:CATalog?")
    print(f"initial FREQ:MODE? : {orig}")
    print(f"initial TRACe:CATalog? : {catalog!r}")

    try:
        for mode in ("FIXed", "SWEep", "MSCan", "DSCan", "FASTlevcw", "LIST"):
            link.issue(f"FREQ:MODE {mode}")
            errors = link.query_errors_clean()
            try:
                now = link.query("FREQ:MODE?").decode("ascii", "replace").strip()
            except TimeoutError:
                now = "<timeout>"
            print(f"  set {mode:10} -> accepted={not errors} now={now!r}")
            if errors:
                print(f"{'':15}errors={errors}")
            if transcript is not None:
                transcript.log(
                    "experiment",
                    f"FREQ:MODE {mode}",
                    now.encode(),
                    note=str(errors),
                )
            # Return to the recorded baseline after every probe.
            link.issue(f"FREQ:MODE {orig}")
            link.query_errors_clean()
    finally:
        _verify_restored(link, [("FREQ:MODE?", orig)])
        link.query_errors_clean()
        print("    cleanup done")

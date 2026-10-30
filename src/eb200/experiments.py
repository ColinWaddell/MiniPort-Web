"""Guarded live experiments against the EB200.

Each experiment follows the same discipline: record the receiver's current
state, perform the (reversible) test, restore the recorded state, then
re-verify it by query and drain the SCPI error queue. Every exchange is
written to the transcript, so the result doubles as a regression fixture.

The commands exercised here include undocumented ones (`TRACE:UDP:*`) that
are known to work on EB200 firmware from community implementations; whether
they behave the same on this unit is precisely what the experiments answer.
"""

from __future__ import annotations

import socket
import struct
import time

AUDIO_MODE_8K = 10  # 8 kHz, 16 bit, 1 channel, per SYSTem:AUDio:REMote:MODe table

EXPERIMENTS = ("trace-format", "audio-udp", "if-udp", "mode-probe")


def run(link, name: str, duration: float) -> None:
    if name == "trace-format":
        trace_format(link)
    elif name == "audio-udp":
        audio_udp(link, duration)
    elif name == "if-udp":
        if_udp(link, duration)
    elif name == "mode-probe":
        mode_probe(link)
    else:  # pragma: no cover - argparse restricts choices
        raise ValueError(f"unknown experiment {name!r}")


# --------------------------------------------------------------------------- #
# helpers
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
    seqs: list[int] = []
    for dg in datagrams:
        if len(dg) >= 10:
            seqs.append(int.from_bytes(dg[8:10], "big"))
    losses = sum(
        1 for a, b in zip(seqs, seqs[1:], strict=False) if ((b - a) & 0xFFFF) > 1
    )
    if len(seqs) > 1:
        print(f"    seq first/last: {seqs[0]} .. {seqs[-1]}, gaps: {losses}")


def _register_trace(link, ip: str, port: int, trace: str) -> str | None:
    candidates = (
        f'TRACE:UDP:TAG:ON "{ip}",{port},{trace}',
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


def _unregister_trace(
    link, ip: str, port: int, trace: str, registered: str | None
) -> None:
    """Stop and delete the UDP trace registration (best effort, all variants)."""
    if registered is not None and "DEFAULT" not in registered:
        link.issue(f'TRACE:UDP:TAG:OFF "{ip}",{port},{trace}')
        link.query_errors_clean()
    if registered is not None:
        link.issue(f'TRACE:UDP:DELETE "{ip}",{port}')
        link.query_errors_clean()


def _verify_restored(link, expectations: list[tuple[str, str]]) -> None:
    for cmd, expected_note in expectations:
        data = link.query(cmd)
        print(f"    restored check {cmd}: {data!r} ({expected_note})")


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
        for fmt in ("REAL,32", "INT,16"):
            link.issue(f"FORMat:DATA {fmt}")
            errors = link.query_errors_clean()
            if errors:
                print(f"  FORMat:DATA {fmt!r} rejected: {errors[0]}")
            for label, cmd in (
                ("level", 'SENS:DATA? "VOLT:AC"'),
                ("mtrace", "TRACe:DATA? MTRACE"),
                ("mtrace-quoted", 'TRACe:DATA? "MTRACE"'),
            ):
                try:
                    data = link.query(cmd)
                except TimeoutError:
                    data = b"<timeout>"
                errors = link.query_errors_clean()
                note = {"format": fmt, "errors": errors}
                print(f"  [{fmt:>8}] {label:14} -> {data!r}  errors={errors or '-'}")
                if transcript is not None:
                    transcript.log("experiment", cmd, data, note=str(note))
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
    """Enable remote AF, capture UDP audio datagrams, then restore state."""
    orig_udp = link.query("TRACE:UDP?")
    orig_mode = link.query("SYST:AUD:REM:MODE?").decode("ascii", "replace").strip()
    print(f"initial TRACE:UDP? : {orig_udp!r}")
    print(f"initial SYST:AUD:REM:MODE? : {orig_mode}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    ip = link.local_ip
    print(f"waiting for audio on {ip}:{port}")

    registered: str | None = None
    try:
        registered = _register_trace(link, ip, port, "AUDIO")
        link.issue(f"SYST:AUD:REM:MODE {AUDIO_MODE_8K}")
        errors = link.query_errors_clean()
        print(f"    SYST:AUD:REM:MODE 10 -> {'accepted' if not errors else errors}")
        datagrams = _collect_udp(sock, duration, link)
        _print_capture(datagrams)
    finally:
        sock.close()
        # Restore audio mode first so the stream stops, then drop the tag.
        link.issue(f"SYST:AUD:REM:MODE {orig_mode or '0'}")
        link.query_errors_clean()
        _unregister_trace(link, ip, port, "AUDIO", registered)
        _verify_restored(
            link,
            [
                ("TRACE:UDP?", orig_udp.decode("ascii", "replace")),
                ("SYST:AUD:REM:MODE?", orig_mode),
            ],
        )
        link.query_errors_clean()
        print("    cleanup done")


def if_udp(link, duration: float) -> None:
    """Attempt to enable the undocumented UDP IF trace (spectrum data)."""
    orig_udp = link.query("TRACE:UDP?")
    print(f"initial TRACE:UDP? : {orig_udp!r}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    ip = link.local_ip
    print(f"waiting for IF data on {ip}:{port}")

    registered: str | None = None
    try:
        registered = _register_trace(link, ip, port, "IF")
        # runds sends the PRIVATE flag in a separate command.
        link.issue(f'TRACE:UDP:FLAG:ON "{ip}",{port},"PRIVATE"')
        errors = link.query_errors_clean()
        print(f"    FLAG:ON PRIVATE -> {'accepted' if not errors else errors}")
        datagrams = _collect_udp(sock, duration, link)
        print("    - before SYST:IF:REM:MODE -")
        _print_capture(datagrams)
        if not datagrams:
            link.issue("SYST:IF:REM:MODE SHORT")
            errors = link.query_errors_clean()
            print(
                f"    SYST:IF:REM:MODE SHORT -> {'accepted' if not errors else errors}"
            )
            datagrams = _collect_udp(sock, duration, link)
            print("    - after SYST:IF:REM:MODE SHORT -")
            _print_capture(datagrams)
    finally:
        sock.close()
        _unregister_trace(link, ip, port, "IF", registered)
        _verify_restored(link, [("TRACE:UDP?", orig_udp.decode("ascii", "replace"))])
        link.query_errors_clean()
        print("    cleanup done")


def mode_probe(link) -> None:
    """Probe SENSe:FREQuency:MODE availability, restoring CW after each test."""
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

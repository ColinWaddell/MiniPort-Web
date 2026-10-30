# Driver architecture proposal (phase 2 design)

This is the design the phase 2 implementation of the `eb200` package follows.
It reflects what discovery established about the receiver (see
`capability-report.md`, `protocol-notes.md`, `scpi-catalogue.md`).

## Goals

- A standalone, dependency-free (stdlib only) Python package usable from
  scripts, CLI tools and the later web server alike.
- Correct-by-construction request/response pairing over a TCP link whose
  device silently drops invalid commands.
- Streaming receivers (UDP audio/trace datagrams) isolated from control
  traffic.
- Everything testable without the physical instrument, with integration
  tests opt-in against the real receiver.

## Layering

```
┌─────────────────────────────────────────────────────────────┐
│  application (phase 3+: FastAPI server, CLI tools)          │
├─────────────────────────────────────────────────────────────┤
│  device    EB200 facade: typed getters/setters, validation  │
├─────────────────────────────────────────────────────────────┤
│  streams   UDP receivers: audio + trace datagrams (threads) │
├─────────────────────────────────────────────────────────────┤
│  binary    datagram/block parsing (framing-independent)     │
├─────────────────────────────────────────────────────────────┤
│  transport TCP/SCPI: framing, serialized command queue,     │
│            timeouts, reconnection                           │
└─────────────────────────────────────────────────────────────┘
```

- **transport** owns the socket and guarantees one outstanding SCPI request
  at a time. It exposes `query(cmd) -> bytes` (expect `\r\n`-terminated
  answer), `command(cmd)` (fire-and-forget, optionally followed by an error
  check), `read_block()` support for `#`-block responses, per-command
  timeouts and a `query_errors()` drain helper.
- **binary** is pure parsing: definite-length blocks (`#<n><len><bytes>`),
  UDP header (magic/sequence), GenericAttribute (391/401/901 …),
  TraceAttribute, and the AUDIO sample payload. Fail-soft policy on unknown
  tags: count and skip, never crash a stream.
- **streams** run a daemon thread per UDP socket: `AudioReceiver` (seq
  tracking, loss counter, sample provider with drop-in silence gaps) and
  later `TraceReceiver` for FSCAN/MSCAN/CW/LIST/IFPAN payloads once those
  modes are exercised live. Registration (`TRACE:UDP:TAG:ON/…DELETE`) with
  knowledge of the ~0.2 s registry-settle delay lives in the device facade.
- **device** (`EB200`) is the public API: `identify()`, `option_codes()`,
  frequency/bandwidth/demodulation/attenuation/squelch/AF get/set, `level()`,
  `trace_catalogue()`, UDP registry helpers. Setters issue the documented
  command, then (optionally, `strict=True` by default) drain `SYST:ERR?` and
  raise `SCPIError` on failure.

## Concurrency model

- One worker thread owns the TCP socket (worker + `queue.SimpleQueue`);
  every request carries a future-like handoff. Callers block on their own
  request only — no interleaved request/response corruption is possible.
- Reconnection: on socket error/hang, close, reconnect with a bounded
  backoff (attempts, delay), then fail pending requests with
  `EB200Connection`. An `on_reconnect` hook lets the device layer re-read
  state; the receiver is never `*RST`-ed by the driver.
- UDP receiver threads are purely consumers; they never touch the TCP link.
  Registry changes are routed through the serialized control path.
- Phase 3's FastAPI server adapts this sync core with
  `asyncio.to_thread`; the package itself stays sync and thread-safe.

## Error taxonomy

```
EB200Error                      base
├── EB200Timeout                no/timely response on socket
├── EB200Connection             socket down, reconnect exhausted
├── EB200Protocol               unparseable reply (magazine framing bugs)
└── SCPIError(code, message)    receiver's error queue content
```

`SCPIError` also drives the instrument-safety helpers: `read_modify_restore`
context manager that snapshots a set of queries and restores them in
`finally` (including re-query verification), used by integration tests and
the experiments CLI.

## Module map (phase 2)

```
src/eb200/
├── __init__.py
├── errors.py        EB200Error taxonomy, SCPIError parsing ("code,"text"" lines)
├── framing.py       line/block response readers, SYST:ERR? text parsing
├── binary.py        definite-block + UDP datagram decoders, seq/loss stats
├── transport.py     TCPTransport: worker thread, queue, reconnect, timeouts
├── device.py        EB200 facade + dataclasses (DeviceInfo, LevelSample…)
├── streams.py       AudioReceiver (datagram consumer thread), later TraceReceiver
├── sim.py           SimulatedTransport: fixture-driven responses + fault injection
└── cli.py           eb200-probe + eb200-stream tools on the package API
```

## Test strategy

- **Unit (default pytest run):** `sim.py` replays captured responses; framing
  tests feed responses in single-byte chunks through real socketpairs;
  concurrency tests hammer the transport through many threads and assert
  request/response pairing; reconnect tests run against a local fake
  instrument server (`tests/support.py`) that can drop connections
  mid-session.
- **Integration (opt-in, `pytest -m integration`):** guarded by `EB200_HOST`
  (default 10.0.0.240) and an `flock` lockfile so the server and tests can
  never address the receiver simultaneously. Read-only sweeps, read-modify-
  restore round-trips (squelch threshold to its own value, volume to its own
  value …), one audio-capture smoke test; the ufw note from
  `protocol-notes.md` applies while UDP tests run.
- Fixtures come from the committed `tests/fixtures/capture/*.jsonl`
  transcripts, converted via `SimulatedTransport.from_transcript()`.

## Notable protocol facts the design must respect

1. No response at all for invalid commands → timeouts are part of the API.
2. Registry mutations apply asynchronously (~0.2 s) → settle-and-recheck.
3. Read-back short forms (`SWE`) differ from set forms (`SWEep`).
4. Trace names unquoted in queries, quoted in the catalogue.
5. `TRACE:UDP:DELETE` takes exactly two arguments; `TAG:OFF` precedes it.
6. Never send `*RST`/`*TST`; state restoration is always read-back-verified.
7. The audio sample rate needs phase 5 validation before playback code
   locks in (documented 8 kHz vs measured ≈ 13.3 kHz discrepancy).
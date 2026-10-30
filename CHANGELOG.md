# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.3.0] - 2026-10-30

### Added

- FastAPI application server (`eb200_server`): the single owner of the
  receiver session, polling live state and broadcasting WebSocket snapshots
  to any number of browsers. Control ops are serialised; browser ownership
  of the controls (adopt-on-first-use or explicit acquire, 120 s TTL)
  prevents competing control operations (second client → HTTP 409).
- REST surface: `/api/state`, `/api/device` (device info + capabilities),
  frequency/demod/bandwidth/gain/gain-mode/attenuator/squelch/
  squelch-threshold/volume/afc/operation control endpoints, one-cycle
  `/api/scan`, health. Errors map to 400/409/502/503. Binds localhost by
  default (`eb200-server --host … --receiver-host …`).
- React + TypeScript + Vite control panel (`web/`): instrument-style dark
  panel with frequency display/entry and step buttons, live S-meter,
  demodulator + IF bandwidth, gain and RF attenuator, squelch, receiver AF
  volume, single-sweep scan panel with canvas level trace, connection and
  control-ownership badges, reconnect with backoff, responsive tablet
  layout. The built bundle is served by FastAPI.
- `StatefulTransport` in `eb200.sim`: stateful set/query simulator used by
  13 server tests (incl. ownership lockout and WebSocket stream) and the
  rest of the suite.
- Gated live REST integration tests (`tests/test_server_integration.py`):
  every control endpoint written own-value discipline plus one real sweep
  and the ownership lockout, against the physical EB200.

### Verified

- Browser E2E (Playwright) against the live receiver: frequency stepping,
  gain mode/MGC, squelch toggle, panel-driven scan, control lockout with a
  second client, WS state flow, and the mobile layout.

## [0.2.0] - 2026-10-30

### Added

- Gain control on the `EB200` facade (`gain`, `set_gain`, `gain_mode`,
  `set_gain_mode`) with documented -30…110 dBµV range validation.
- Sweep configuration (`sweep_start/stop/step`, count with INFinity
  handling, dwell, direction), measuring-state control (`initiate`,
  `abort`, `sweeping`) and `trace_feed_control`.
- `EB200.scan_sweep(start, stop, step)`: one documented FSCAN sweep cycle
  read back from the MTRACE/ITRACE trace buffers as per-step levels
  (dBµV, no-data as NaN) and step frequencies, with best-effort receiver
  state restore surfaced in `ScanResult.restore_errors`.
- Live experiment `sweep-fscan` (UDP FSCAN tag trigger matrix - tag accepted
  but no datagrams on this unit) and `trace-buffer` (documented trace-buffer
  scan path, captured responses).
- Unit tests for all new commands plus a simulated scan round-trip and
  timeout path; integration tests for gain read-modify-restore and one live
  sweep.

### Verified

- Live: `TRACe:DATA? MTRACE` returns per-step levels with a `9.9E37`
  terminator and consuming the buffer clears it; `ITRACE` returns
  step-index/frequency pairs ending in a `9.9E37,9.9E37` terminator pair;
  sweep band/step/dwell parameters are `-221 Settings conflict` locked
  while a scan runs.

## [0.1.0] - 2026-10-30

### Added

- `eb200-core` package layer: TCP/SCPI transport with a serialized command
  queue, per-request timeouts, timeout-policy link resets and bounded
  reconnection; definite-length block and UDP datagram decoders; the `EB200`
  device facade (identity, receiver state, level, traces, UDP registry
  helpers); `AudioReceiver` UDP stream consumer with sequence-gap healing
  and restart detection; `SimulatedTransport` replaying captured responses
  with fault injection.
- `eb200-probe` command line tool: read-only discovery commands (`idn`,
  `options`, `rx`, `traces`, `udp`, `level`, `errors`, `raw`, `sweep`), the
  guarded experiments (`exp`) and an `audio` listener that restores receiver
  state afterwards.
- Test suite: 64 unit tests (simulated responses, fake instrument server,
  socketpair framing tests), 5 integration tests against the live receiver
  (opt-in, `pytest -m integration`, lockfile-guarded) plus an UDP audio
  smoke test (`EB200_AUDIO_SMOKE=1`).
- Discovery documentation: capability report, SCPI command catalogue,
  protocol notes and driver architecture proposal.
- Guarded live experiments against the receiver (trace output formats, UDP
  audio streaming, UDP IF streaming attempt, frequency-mode availability,
  UDP tag-name enumeration).
- Captured EB200 responses as regression fixtures (JSONL transcripts).
- Project scaffold (pyproject, MIT license, ruff/pytest configuration).
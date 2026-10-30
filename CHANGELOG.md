# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
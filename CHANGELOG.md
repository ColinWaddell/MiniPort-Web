# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
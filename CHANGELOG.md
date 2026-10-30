# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.9.0] - 2026-10-30

### Added

- Waterfall auto-normalizing palette (option "AUTO WATERFALL"): the colour
  range self-normalizes over a rolling ~20 s percentile window (per-frame
  5th/95th percentiles of received levels), so faint signals pop out of
  the noise floor. Band changes reset the statistics window.
- SENSITIVITY slider (0–100) tightening/loosening the percentile window
  around the median level; AUTO state + slider persist in localStorage.
- In MANUAL the waterfall behaves exactly as before and the DISPLAY dBµV
  inputs are editable; in AUTO they are read-only and show the live
  computed range (`WaterfallNormalizer` in `web/src/lib/spectrum.ts`;
  client-only, no server changes).

### Fixed

- WebSocket broadcasts can no longer be frozen by a wedged client: the
  prune path's `close()` is now bounded by a 1 s timeout — starlette's
  graceful close could hang forever on a half-dead TCP socket (a crashed
  browser process), which had stalled the sweep-frame broadcast loop for
  all clients. Verified live: a wedged state-socket client (12 s of not
  reading) left a healthy client untouched (36 messages at ~3 Hz, max gap
  0.4 s).

## [0.8.1] - 2026-10-30

### Fixed

- Native browser tooltips no longer compete with the in-interface help
  bubbles: the hover-bubble affordance no longer carries a `title`
  attribute (screen-reader text kept via aria-label). Verified on the LAN
  origin: zero native tooltips, our bubble alone on hover.

## [0.8.0] - 2026-10-30

### Added

- In-interface help, written for this receiver: dotted-underlined jargon
  words (MGC, RF ATTENUATOR, AFC, THRESHOLD, OPERATION, IF BANDWIDTH,
  DEMODULATOR, SPAN/STEP chips, DISPLAY dBµV range, SIGNAL LEVEL, panel
  headings …) with hover bubbles explaining the control, its unit and
  range, and quirks verified on the instrument.
- A `?` button in the header opens a full reference sheet: one scrollable
  card grouped per panel (every control explained, touch-friendly),
  closable with Esc/outside click.
- 10 px spacing between the waterfall and the buttons beneath it.

### Verified

- Browser E2E on both origins (localhost and the LAN IP / non-secure
  context): hover bubbles appear, the reference sheet opens and closes,
  waterfall spacing = 10 px, no page errors.

## [0.7.2] - 2026-10-30

### Fixed

- Spectrum band changes that cross the previously configured band no longer
  fail with `-221 Settings conflict;Stop freq lower than start freq`: the
  driver widens the band first and narrows afterwards (both when
  configuring a sweep and when restoring afterwards). Found through first
  real-user testing at the 90-110 MHz band; verified live (201-point cycle,
  no error) and with a command-ordering regression test.

### Added

- LIVE SPECTRUM / WATERFALL display controls: freeze/resume drawing,
  clear-waterfall, peak-line and waterfall visibility toggles, PNG export,
  span chips (0.5/1/5/10 MHz) and step chips (1/5/12.5/25/100 kHz) that
  centre the band on the receiver's current tune, plus aria labels for the
  sweep inputs. All are display-level (freeze/clear/toggles/PNG work
  without control ownership); band/step changes stay control-gated.

## [0.7.0] - 2026-10-30

### Added

- `eb200_server.config`: layered configuration (defaults < JSON `--config`
  file < `EB200_*` environment variables < CLI flags) with validation and
  retention values; `packaging/miniport-web.json.example` as the starting
  file.
- Structured logging: `--log-format json` emits one JSON object per record
  (verified live).
- `packaging/miniport-web.service`: hardened systemd unit
  (`Restart=always`, `NoNewPrivileges`, `ProtectSystem=full`, read-only
  home, writable data dir) + documentation in `docs/deployment.md`
  (install, config, systemd enable, verification checklist, updates).
- `Dockerfile` (multi-stage node→python) and `.dockerignore` — provided,
  but this host has no Docker engine: unverified, to be exercised on a
  Docker-capable machine.
- **Boot without the receiver**: the transport worker now exists even when
  the first connect fails (lazily reconnecting), so the web server boots
  with the instrument down and recovers automatically; a bounded send
  timeout (`_safe_send`) keeps producers alive when a browser stalls.
  Verified by a new end-to-end test ("boots offline and recovers when the
  receiver returns") whose earlier failure (a permanent test hang) was
  traced via the default executor's stack dump.
- Retention env wiring (`EB200_LEVEL/sweep/RECORDING_RETENTION_DAYS`).

## [0.6.0] - 2026-10-30

### Added

- SQLite store (`src/eb200_server/db.py`, WAL): presets (receiver +
  spectrum), scheduled jobs + run log, level-log samples, sweep history
  (payloads as files referenced by rows), recordings metadata, settings.
  Affected-row/insert semantics split to keep rowcounts truthful.
- Automation services (`src/eb200_server/automation.py`):
  - `LevelLogger`: periodic level samples from the poll hook (no extra
    receiver traffic), configurable period, settings-backed.
  - `TriggerRecorder`: automatic WAV recording when the level crosses a
    configured threshold (armed/cooldown/duration settings-backed).
  - `Scheduler`: background job runner (sweep cycles stored to `data/sweeps/`,
    audio recordings) with `interval` or `daily` schedules, run-log and
    hourly retention (`level`/`sweep`/`recording` days) for unattended
    operation.
- Disk-streaming WAV recordings (`WavStreamWriter`): header written at
  start, rate patched at finish; DB rows carry size/duration/rate/trigger;
  crash-safe provisional patching.
- REST automation surface: `/api/presets` (frequency presets + apply),
  unified `/api/spectrum/presets` (now DB-backed), `/api/jobs`
  (create/enable/disable/run-now/delete + `/api/job-runs`),
  `/api/level-log` (configure/status + CSV/JSON export),
  `/api/sweeps` (+ per-sweep CSV), `/api/trigger` (arm/status).
- Frontend: `AutomationPanel` (trigger, level logging, scheduled jobs with
  run-now/delete/enable, sweep history export) and frequency presets in
  the tuning panel; recordings list now shows duration and trigger.
- A fresh test data dir everywhere (`ReceiverManager(data_dir=...)`), with
  live integration fixtures isolated from the repo store.

## [0.5.0] - 2026-10-30

### Added

- `AudioService` (`src/eb200_server/audio.py`): owns the UDP audio
  receiver, engages the undocumented remote-AF path (`SYST:AUD:REM:MODE`
  + `TRACE:UDP:TAG:ON …,AUDIO` in the weakmon-proven order) and restores
  the receiver state on stop; measures the stream's wire rate with a
  segment-based estimator (longest contiguous arrivals win) and serves
  WAV recordings (16-bit mono, buffered, capped at 64 MB).
- REST: `/api/audio/start|stop`, `/api/audio/stats`,
  `/api/audio/record/{start|stop}`, recording list/download/delete.
- New WebSocket `/ws/audio`: text config/rate messages plus binary
  chunks of native int16-LE PCM.
- Frontend `AudioPanel`: LISTEN/STOP with Web Audio playback
  (AudioBuffer scheduling against the AudioContext clock, ~250 ms jitter
  buffer, underrun re-anchoring, long-queue chunk dropping), browser
  listen volume, live stream stats (rate/packets/loss), recording
  controls with download and delete, and a firewall hint when connected
  but packetless.
- `AudioReceiver.effective_rate()`: wire-rate measurement that resolves
  the phase-1 rate discrepancy — measured ≈ 7,957 Hz for mode 10,
  matching the documented 8 kHz (the earlier 1.67× figure was a probe
  artefact; docs corrected).
- Simulated UDP trace registry inside `StatefulTransport` (TAG:ON/OFF/
  DELETE semantics as measured live), enabling end-to-end audio service
  tests without hardware.
- Dead-WebSocket hardening: every broadcast send runs with a hard 2 s
  timeout and prunes stuck peers, so a crashed browser can no longer
  stall the audio/spectrum/state producers (found via the first live
  audio E2E run, which showed a frozen pump with 97% packet loss).

### Verified

- Browser E2E against the live receiver: LISTEN flows PCM at the
  measured ~7,957 Hz with 0% loss, RECORD writes a downloadable RIFF
  WAV, STOP restores remote-AF mode 0 and the registry baseline.
- Gated live tests for the audio stream, recording download and state
  restore.

## [0.4.0] - 2026-10-30

### Added

- `SpectrumService` (`src/eb200_server/spectrum.py`): continuous
  documented sweep cycles over the trace-buffer path, per-bin peak hold,
  frame history, and an efficient binary WebSocket protocol (`SPW1`:
  little-endian header + int16 levels/peak in 1/10 dBµV, NaN sentinel) so
  payload size scales with the grid, not with JSON overhead.
- REST: `/api/spectrum/start|stop|reset-peak`, `/api/spectrum/export.csv`
  (waterfall history as CSV: timestamp × frequency grid), preset store
  (`/api/spectrum/presets` GET/POST/DELETE, JSON file in `data/`, gitignored)
  with validation (≤ 2001 points).
- New WebSocket `/ws/spectrum` streaming binary frames to any number of
  viewers; spectrum runs hold the receiver lock and block competing
  control ops with a 409 guard; state snapshots carry `spectrum` status and
  keep last-known connectivity while sweeping.
- `SpectrumPanel` in the frontend: canvas spectrum with grid, axis labels,
  trace, dashed peak-hold line and peak marker; canvas waterfall with
  four-stop palette and adjustable display range; start/stop, peak reset,
  preset save/load/delete and CSV export. Frequency header offsets, water-
  fall row model and the STOP button availability were fixed during live
  E2E.
- `StatefulTransport` now emulates one-cycle sweeps (deterministic
  synthetic band with a centre carrier), enabling end-to-end spectrum
  service tests without hardware; fault injection reaches it too.
- Gated live tests: spectrum cycles, CSV shape, preset lifecycle.

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
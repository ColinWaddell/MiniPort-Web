# MiniPort-Web

Modern web-based control software for the Rohde & Schwarz EB200 Miniport
receiver. The project recovers the receiver's remote-control and measurement
capabilities behind a browser interface running on Linux.

Status: Phase 4 complete — receiver discovery, tested driver library, web
application, and a live spectrum/waterfall panel driven by continuous
documented sweep cycles (binary WebSocket streaming, peak hold, presets,
CSV export). No IF-panorama/Digi-Scan on this hardware; the spectrum uses
the trace-buffer path.

## Repository layout

- `src/eb200/` — standalone Python driver package (no web framework dependency)
- `src/eb200_server/` — FastAPI application server (owns the receiver session)
- `web/` — React + TypeScript + Vite control panel (built bundle served by the server)
- `docs/` — receiver capability report, SCPI catalogue, protocol notes
- `tests/` — unit tests (simulated responses), server tests, opt-in
  integration tests against the physical receiver

## Quickstart

```bash
python3 -m venv env
env/bin/pip install -e '.[dev,server]'
env/bin/eb200-probe idn
env/bin/pytest                      # unit suite against simulated responses
env/bin/pytest -m integration       # opt-in suite against the live receiver
```

### Web application

```bash
env/bin/pip install -e '.[dev,server]'
cd web && npm install && npm run build && cd ..
env/bin/eb200-server
```

This binds `127.0.0.1:8000` by default (`--host`, `--port`,
`--receiver-host` override it) and serves the built panel at `/`. The
browser talks only to this server; the server owns the single EB200
session, polls state (~3 Hz) and broadcasts it over `/ws/state`. Control
requests carry the `X-Eb200-Client` header; control ownership is
adopt-on-first-use (or explicit `/api/control/acquire`) with a 120 s TTL,
so two browsers cannot fight over the receiver (second client gets 409).
`eb200-probe audio --duration 3` briefly listens to the UDP audio stream
and restores receiver state afterwards.

The probe defaults to `10.0.0.240:5555` and can be redirected with `--host`
and `--port`. Integration tests use the `EB200_HOST`/`EB200_PORT` environment
variables.

## Documentation

- `docs/capability-report.md` — what this receiver actually supports (firmware 2.80)
- `docs/scpi-catalogue.md` — every tested command with its captured response
- `docs/protocol-notes.md` — TCP/UDP framing details and observed quirks
- `docs/driver-architecture.md` — proposed design for the `eb200` driver

## License

MIT — see `LICENSE`.
# MiniPort-Web

Modern web-based control software for the Rohde & Schwarz EB200 Miniport
receiver. The project recovers the receiver's remote-control and measurement
capabilities behind a browser interface running on Linux.

Status: Phase 2 complete — receiver discovery and a tested driver library
(`eb200`) covering control, gain, level polling and one-cycle sweep
acquisition. No web interface yet.

## Repository layout

- `src/eb200/` — standalone Python driver package (no web framework dependency)
- `docs/` — receiver capability report, SCPI catalogue and protocol notes
- `tests/` — unit tests (simulated responses) plus opt-in integration tests
  against the physical receiver

## Quickstart

```bash
python3 -m venv env
env/bin/pip install -e '.[dev]'
env/bin/eb200-probe idn
env/bin/eb200-probe audio --duration 3   # brief UDP audio listen (restores state)
env/bin/pytest          # unit suite against simulated responses
env/bin/pytest -m integration   # opt-in suite against the live receiver
```

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
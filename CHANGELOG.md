# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-10-30

### Added

- Project scaffold for the `eb200` Python package.
- `eb200-probe` command line tool for read-only discovery of the EB200:
  identity, options, receiver state, traces, UDP trace list and a scripted
  read-only sweep, with raw-byte JSONL transcripts.
- Guarded live experiments against the receiver (trace output formats, UDP
  audio streaming, UDP IF streaming attempt, frequency-mode availability).
- Discovery documentation: capability report, SCPI command catalogue,
  protocol notes and driver architecture proposal.
- Captured EB200 responses as regression fixtures.
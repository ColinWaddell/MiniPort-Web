# EB200 communication protocol notes

Everything here was observed against the live receiver
(`10.0.0.240:5555`, firmware 2.80) unless marked *documented*.

## TCP / SCPI transport

- Port TCP **5555** (*documented*: LAN remote is an option of the manual
  version, but this unit speaks it on the shipped port).
- Commands are ASCII terminated with **`\n`**; responses are terminated with
  **`\r\n`**.
- Multi-line responses exist and separate their lines with a bare `\n`
  (observed on `TRACE:UDP?`). Treat `\r\n` as the response terminator, not
  `\n`.
- **Set commands produce no response on the socket.** Nothing is echoed.
- **Invalid/unknown commands also produce no socket response.** The error is
  pushed to the SCPI error queue and only surfaces through `SYST:ERR?`
  (e.g. `-113,"Undefined header;SYST:IF:REM:MODE?"` appeared only when the
  error queue was queried afterwards). Consequence: every query needs a
  read timeout, and after a timeout the connection may still deliver a late
  response — prefer discarding the link rather than re-syncing in-band.
- Error message grammar: `<code>,"<text>[;<command echo]"`. Codes observed:
  `-102` Syntax error, `-103` Invalid separator, `-113` Undefined header,
  `-141` Invalid character data, `-151` Invalid string data, `-158` String
  data not allowed, `1` Device dependent error (`Not installed`).
- One TCP connection at a time is the safe model (single remote client per
  the device model; not tested with two concurrent connections).
- `SYST:ERR?` replies terminate with `0,"No error"` — drain in a loop.

## Response value encodings

- `FREQ?`, `BAND?` etc: plain integers, no unit and no `+`/`,` decorations.
- Level `SENS:DATA? "VOLT:AC"`: under `FORMat:DATA ASC` a signed decimal
  (`-18.3`, dBµV); under `FORMat:DATA PACKed` **two raw bytes, big-endian,
  1/10 dBµV** (e.g. `FF 50` = −176 → −17.6 dBµV), with **no** `#` block
  header for scalar results.
- Traces (`TRACe:DATA? <name>`) are only non-empty once measurement results
  are stored (standby: `9.91E37` no-data marker under ASC; `'0'` under
  PACKed). Trace names must be unquoted; the catalogue replies with quoted
  names.
- R&S no-data marker: `9.91E37` (= SCPI single-precision ±infinity-ish
  representation, *documented*).

## UDP datagram protocol (Annex F territory)

Only the AUDIO datagram class has been captured end to end; the IF/scan
classes follow the same envelope per the community implementations
(weakmon, runds_connector) and Annex F's TOC (Header, GenericAttribute,
TraceAttribute, per-mode trace classes).

16-byte header, big-endian:

| offset | size | field | observed (AUDIO) |
|---|---|---|---|
| 0 | 4 | magic | `0x000EB200` |
| 4 | 2 | "version" field | `0x0027` (39) |
| 6 | 2 | "version" field | `0x0002` (2) |
| 8 | 2 | sequence number | increment +1 per datagram (164, 165, …); 16-bit wrap expected |
| 10 | 2 | reserved | `0x0000` |
| 12 | 4 | data length field | `0x00000000` (not populated for AUDIO on this firmware; runds checks it against size) |

GenericAttribute at offset 16: tag U16 + length U16.
- tag 401 (0x0191) = AUDIO (verified)
- tag 901 (0x0385) = IF trace (per runds source; not reproducible on this
  unit because tag `IF` is rejected by `TRACE:UDP:TAG:ON`)

TraceAttribute at offset 20 (AUDIO): values U16 + two bytes + selectorFlags
U32:

| field | observed |
|---|---|
| values | `247` (number of 16-bit samples following) |
| byte 22 | `0x77` |
| byte 23 | `0x00` |
| selectorFlags | `0x00040000` |

Samples start at offset 28, big-endian signed 16-bit, 247 per datagram.

Observed AUDIO throughput at `SYST:AUD:REM:MODE 10` (documented: 8 kHz,
16 bit, 1ch, 16 kbyte/s): 54 datagrams/s × 247 samples ≈ **13,340 samples/s**
(≈ 27 kbyte/s payload) — 1.67× the documented rate. The true playback rate
still needs an audible/frequency-correlation check in phase 5.

## TRACE:UDP registry (undocumented subsystem)

- `TRACE:UDP?` lists the registry: baseline is three empty slots
  (`DEF\n001\n002\n003\r\n`). A registered entry appears inline in a slot
  line, e.g. `001 "10.0.0.35", 49521, AUD` (display truncates the tag).
- Register: `TRACE:UDP:TAG:ON "<ip>",<port>,<TAG>`. Valid tags on fw 2.80:
  `AUDIO`, `IFPAN`, `FSCAN`, `MSCAN`, `CW`, `LIST`. Anything else → `-141`.
  The weakmon-era `TRACE:UDP:DEFAULT:TAG:ON` form does **not** exist here
  (`-113`/`-141`).
- Deregister: `TRACE:UDP:TAG:OFF "<ip>",<port>,<TAG>` then
  `TRACE:UDP:DELETE "<ip>",<port>`. `DELETE` takes exactly two arguments
  (`-103` with a third).
- Registry mutation applies **asynchronously** (~0.2 s): immediately after
  `DELETE`, `TRACE:UDP?` may still show the old list. Allow a settle delay
  before verifying.
- The receiver only starts sending once the corresponding measuring mode is
  active. AUDIO additionally requires `SYST:AUD:REM:MODE > 0`. FSCAN/MSCAN/
  CW/LIST/IFPAN tags were silent during standby and still need to be
  exercised from their active modes (phase 4).

## Host-side note (this workstation)

`mrpinchy` runs ufw with default-deny inbound, so the receiver's UDP
datagrams are dropped unless a temporary rule exists:

```bash
sudo ufw allow from 10.0.0.240 to any proto udp comment eb200-discovery-temp
sudo ufw delete allow from 10.0.0.240 to any proto udp
```

Any receiver-streaming integration test needs this rule while it runs.

## Manual completeness caveat

The circulating EB200 manual PDFs (rigpix and mirrors, rev
`4052.2039.32-07`, 232 pages) stop at chapter 5 — Annexes A–J, including
Annex F *Datagram Communication*, are missing everywhere (confirmed on the
R&S groups.io EB200 thread). This matters: Annex F is the authoritative
specification for everything in the second half of this page. Interim
authority: community implementations + live capture. A candidate
corroborating source is the R&S EM510 manual, which ships the same
"Annex F: Datagram Communication" for the family.
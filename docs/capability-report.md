# EB200 Miniport capability report

Receiver under test: Rohde & Schwarz EB200 Miniport, serial `108.063/003`,
firmware `V02.80-4052.4654.00`, reachable at `10.0.0.240`, SCPI port TCP 5555.

All findings below were established live on 2026-10-30 through read-only
queries and guarded experiments (record → test → restore → re-verify). Raw
transcripts are in `tests/fixtures/capture/`. Statements sourced from the
manual only are marked *documented*; everything else was observed on the
device.

## Identification

- `*IDN?` → `ROHDE&SCHWARZ,EB200,108.063/003,V02.80-4052.4654.00`
  (firmware 2.80 confirmed)
- `SYST:VERS?` → `1996.0` (SCPI 1996.0 conformance, documented-standard query)

## Installed options

`*OPT?` → `0,0,0,ER,0,0,F1,0`

The manual (rev 01.00) explains the codes `SU` (IF panorama module),
`BP` (battery pack), `DS` (Digi-Scan software), `ER` (Expansion RAM),
`CM` (Coverage Measurement), `FS` (Field Strength Measurement). The
fw-2.80 response has a different shape than the manual example (eight slots
vs six) and contains the undocumented code `F1`.

Live behaviour narrows it down:

| Feature | Manual/option | Live result |
|---|---|---|
| Frequency modes | CW, FIXed, SWEep, MSCan | `FREQ:MODE SWEep`/`MSCan` accepted |
| Digi-Scan | option EB200DS | `FREQ:MODE DSCan` → `1,"Device dependent error;Not installed"` |
| FASTLEVCW measuring | option | "Not installed" |
| LIST scan | option | "Not installed" |
| IF-PAN | option EB200SU | `IFPAN` trace name responds, `TRACe:POINts? IFPAN` = 0, `IFPAN` UDP tag accepted but silent |

Conclusion: this unit has neither the Digi-Scan (DS) option nor a working
IF-panorama hardware path; **frequency scanning is available through SWEep
and MSCan**, which is the relevant path for the web application. The `F1`
slot code remains unexplained (candidates: a fw-2.80-era code; possibly the
LAN-remote capability). The `ER` slot maps to Expansion RAM per manual.

## Verified receiver control state

Read via the sweep (transcript `20261030-161332-sweep.jsonl`):

| Aspect | Query | Value at time of test |
|---|---|---|
| Frequency | `FREQ?` | `27260000` Hz (plain integer) |
| Bandwidth | `BAND?` | `15000` Hz; range query `BAND? MINimum` / `MAXimum` → `150` / `150000` |
| Demodulation | `DEM?` / `DEMODULATION?` | `AM` |
| Frequency mode | `FREQ:MODE?` | `CW` |
| AFC | `FREQ:AFC?` | `0` |
| Attenuator auto | `INP:ATT:AUTO?` (long form works too) | `0` |
| Attenuator state | `INP:ATT:STAT?` | `0` |
| Squelch | `OUTP:SQU?` | `1` (enabled) |
| Squelch threshold | `OUTP:SQU:THR?` | `-30` (dBµV) |
| Squelch control | `OUTP:SQU:CONT?` | `NONE` |
| AF volume / balance | `SYST:AUD:VOL?` / `SYST:AUD:BAL?` | `0.00` / `0.00` |
| Remote AF mode | `SYST:AUD:REM:MODE?` | `0` (off) |
| Signal level | `SENS:DATA? "VOLT:AC"` | fluctuating `-18.0` … `-19.2` dBµV (noise floor) |
| Display enable | `DISP:ENAB?` | `1` |
| Status registers | `STATus:…:CONDition?` family | all `0` (idle) |

## Measurement and streaming capabilities

### Remote AF over UDP (verified working)

Sequence: `SYST:AUD:REM:MODE 10` (8 kHz/16 bit/1ch per documented table),
then `TRACE:UDP:TAG:ON "<ip>",<port>,AUDIO`. Result (transcript
`20261030-161547-exp.jsonl`): 162 datagrams in 3 s, strictly contiguous
sequence numbers 164–325 (no loss), GA tag 401, TraceAttribute
`values=247, byte2=0x77, byte3=0x00, flags=0x40000`, samples big-endian
int16 from byte 28 (see `protocol-notes.md`).

Caveat: the measured payload rate was ≈ 13,340 samples/s, ≈ 1.67× the
documented 8 kHz. The playback rate must be validated by ear/correlation
in phase 5 before trusting either number.

### UDP trace destinations (TRACE:UDP registry, undocumented)

- `TRACE:UDP?` lists up to three destination slots; baseline `DEF\n001\n002\n003`.
- Register: `TRACE:UDP:TAG:ON "<ip>",<port>,<TAG>`. Valid tags on this
  firmware: `IFPAN`, `FSCAN`, `MSCAN`, `CW`, `LIST`, `AUDIO` (probed one by
  one, each cleaned up again — transcript `20261030-161742-exp.jsonl`).
  Note there is no DS-related tag: consistent with Digi-Scan not installed.
- **Only AUDIO actually streams** — exhaustively verified on 2026-10-30 that
  FSCAN/MSCAN/CW/LIST/IFPAN tags produce no datagrams: with the sweep
  running (SWEeping condition non-zero), with `INIT`, `INIT:CONM`, finite
  `SWEep:COUNt INFinity→2`, squelch thresholds, dwell variants, quoted and
  unquoted `FLAG:ON PRIVATE` variants, and `DISP:MENU` (whose remote values
  are only DEFAULT/IFPAN/LEVEL/TONE/FREQ per the manual). The IF trace path
  that runds uses (`SYST:IF:REM:MODE`) does not exist here. Spectrum via
  UDP was not achievable on this unit in phase 2; sweep data comes through
  the documented trace buffers instead (see below).
- The weakmon-era `…:DEFAULT:…` command forms used by weakmon are
  also undefined on 2.80.
- Deregistration is a repeated `TRACE:UDP:DELETE` (one entry per call);
  `TAG:OFF` only detaches a tag and actually *creates* a destination entry —
  full details in `protocol-notes.md`.
- Deregistration is asynchronous as well (~0.2 s); verification must wait.

### Sweep acquisition through the trace buffers (documented path, verified)

One sweep cycle over `FREQ:STARt 26800000 … FREQ:STOP 27800000`,
`SWEep:STEP 25000` (41 steps):

1. `TRACE:FEED:CONTrol MTRACE,ALWayS` and `…ITRACE,ALWayS`.
2. `SWEep:COUNt 1`, `FREQ:MODE SWEep`, `INIT` (INIT clears the buffers).
3. `TRACe:POINts? MTRACE` reaches **42** (41 steps + terminator) within a
   second for dwell 0; the sweep completes on its own.
4. `TRACe:DATA? MTRACE` → levels per step in dBµV, trailing `9.91E37`
   no-data terminator; `TRACe:DATA? ITRACE` → step-index/frequency pairs;
   reading consumes both buffers (points back to 0).
5. Sweep bandwidth parameters cannot be changed while a scan runs
   (`-221,"Settings conflict;Not during scan"`); ABORt first.

The library implements this as `EB200.scan_sweep(start, stop, step)` with
best-effort state restore reported in `ScanResult.restore_errors`.

### Trace subsystem (documented, verified idle)

- `TRACe:CATalog?` → `"MTRACE", "ITRACE", "IFPAN", "SSTART", "SSTOP", "UDP"`
- `TRACe:POINts?` → 0 for all (buffers empty in standby)
- `TRACe:DATA? MTRACE` → `9.91E37` under ASC (no data marker); unquoted names
  required (`-158` for quoted names)
- `FORMat:DATA` accepts `ASC` and `PACKed` (`REAL,32`/`INT,16` reject with
  `-141`). Under PACKed the scalar `SENS:DATA? "VOLT:AC"` answer is 2 raw
  bytes, big-endian, 1/10 dBµV (e.g. `ff50` = −17.6 dBµV) — no block header.

## Discrepancies and unknowns

1. Annexes A–J are missing from every circulating copy of the manual
   (rigpix's ends at chapter 5; Annex F holds the authoritative datagram
   spec). Confirmed by the R&S groups.io thread "Annex missing". Community
   implementations and live captures stand in as ground truth.
2. `*OPT?` slot 7 code `F1` is undocumented; slot shape differs from the
   manual's six-slot example.
3. `TRACE:UDP:*` commands are undocumented in the manual but present and
   functional (AUDIO verified end to end).
4. Audio payload rate ≈ 1.67× the documented 8 kHz for mode 10.
5. `FREQ:MODE FIXed` is accepted but reads back `CW`.
6. Read-back values use short forms (`SWE`, `MSC`) while the set syntax uses
   long forms (`SWEep`, `MSCan`).
7. Invalid commands are not answered on the TCP socket at all; their errors
   surface only in the `SYST:ERR?` queue. Clients must rely on timeouts.
8. The `TRACE:UDP?` listing wraps entries with spaces (`"ip", port, `);
   slots appear to be reused round-robin (first registration went to slot
   002, later ones to 001).

## Instrument state after discovery

Frequency, mode, bandwidth, RF and audio settings unchanged from before the
session; remote AF mode restored to 0; UDP registry at baseline
(`DEF\n001\n002\n003`); error queue drained. No `*RST` or non-volatile
reset was issued at any point.
# EB200 SCPI command catalogue (firmware V02.80)

Status legend:

- **live-ok** — executed against the receiver, response captured
- **live-error** — executed, receiver rejected (response recorded)
- **documented** — in the manual chapter 4, not yet exercised live

Raw responses for every command below are in `tests/fixtures/capture/`.

## Identity and system

| Command | Status | Observed response / notes |
|---|---|---|
| `*IDN?` | live-ok | `ROHDE&SCHWARZ,EB200,108.063/003,V02.80-4052.4654.00` |
| `*OPT?` | live-ok | `0,0,0,ER,0,0,F1,0` — codes: manual explains SU/BP/DS/ER/CM/FS; `F1` undocumented |
| `SYST:VERS?` | live-ok | `1996.0` (SCPI 1996.0) |
| `SYST:ERR?` | live-ok | `0,"No error"` or `code,"text;command"`; queue drained by repeating |
| `*STB?` | live-ok | `1` (status byte; MSC bit per status system) |
| `*IST?` | live-ok | `0` |
| `*RST` | forbidden | resets receiver — never sent during discovery |

## Frequency, mode, signal path

| Command | Status | Observed response / notes |
|---|---|---|
| `FREQ?` | live-ok | `27260000` (Hz, plain integer) |
| `FREQ? <val>` | live-ok | set accepted in the same connection |
| `FREQ:MODE?` | live-ok | `CW`; read-back uses short forms (`SWE`, `MSC`) |
| `FREQ:MODE SWEep` / `MSCan` | live-ok | accepted, read-back `SWE` / `MSC` |
| `FREQ:MODE FIXed` | live-ok | accepted but read-back stays `CW` (quirk) |
| `FREQ:MODE DSCan` / `FASTlevcw` / `LIST` | live-error | `1,"Device dependent error;Not installed"` |
| `FREQ:AFC?` | live-ok | `0` |
| `BAND?` | live-ok | `15000` (Hz) |
| `BAND? MINimum` / `MAXimum` | live-ok | `150` / `150000` |
| `DEM?` / `DEMODULATION?` | live-ok | `AM` (short and long forms both accepted) |
| `INP:ATT:AUTO?` | live-ok | `0`; long form `INPut:ATTenuation:AUTO?` also live-ok |
| `INP:ATT:STAT?` | live-ok | `0` (documented: `INPut:ATTenuation:STATe`) |
| `INPut?` | live-error | `-102,"Syntax error;INPUT?"` — root INPut needs a suffix |

## Output, squelch, audio

| Command | Status | Observed response / notes |
|---|---|---|
| `OUTP:SQU?` | live-ok | `1` (documented: `OUTPut:SQUelch`) |
| `OUTP:SQU:THR?` | live-ok | `-30` (dBµV) |
| `OUTP:SQU:CONT?` | live-ok | `NONE` |
| `SYST:AUD:VOL?` | live-ok | `0.00` |
| `SYST:AUD:BAL?` | live-ok | `0.00` |
| `SYST:AUD:REM:MODE?` | live-ok | `0` (table 0–13 documented) |
| `SYST:AUD:REM:MODE 10` | live-ok | accepted; starts 8 kHz/16-bit/1ch UDP stream when paired with a tag |

## Level measurement

| Command | Status | Observed response / notes |
|---|---|---|
| `SENS:DATA? "VOLT:AC"` | live-ok | ASCII `-18.3` (dBµV) under FORMat:DATA ASC; under PACKed: 2 raw bytes big-endian 1/10 dBµV, no block header |
| `GCON?` | live-ok | `50` (MGC gain dBµV; documented: -30 max sensitivity … 110 min) |
| `GCON:MODE?` | live-ok | `AUTO` (read-back uses short forms: `FIX`, `AUTO`) |
| `DISP:ENAB?` | live-ok | `1` (Hamlib "lock" equivalent) |

## Sweep / scan configuration and acquisition

| Command | Status | Observed response / notes |
|---|---|---|
| `FREQ:STARt?` / `FREQ:STOP?` | live-ok | sweep band frequency scan (defaults 20 MHz / 100 MHz) |
| `SWEep:STEP?` | live-ok | `100000` Hz (sweep step, not the tuning STEP!) |
| `SWEep:DWELl?`, `SWEep:HOLD:TIME?` | live-ok | `0.000` / `0.000` |
| `SWEep:COUNt?` | live-ok | `9.9E37` (documented "infinite" marker) |
| `SWEep:DIRection?` | live-ok | `UP` |
| `GCON?` / `GCON:MODE?` | live-ok | gain MGC value (dBµV) and mode; see level table |
| `TRACE:FEED:CONTrol MTRACE,ALWayS` | live-ok | enable storage of measured values in MTRACE |
| `TRACE:FEED:CONTrol ITRACE,ALWayS` | live-ok | enable storage of step index + frequency in ITRACE |
| `FREQ:MODE SWEep` + `INIT` | live-ok | starts the sweep; SWEeping condition becomes non-zero |
| `ABORt` | live-ok | stops the active scan (no reply, per manual) |
| `FREQ:STARt <v>` while scanning | live-error | `-221,"Settings conflict;Not during scan"` — ABORt first |
| `TRACe:POINts? MTRACE` during sweep | live-ok | grows with steps; `42` = 41 steps + terminator |
| `TRACe:DATA? MTRACE` after a full cycle | live-ok | level list per step, dBµV, trailing `9.9E37`terminator; **reading it consumes the buffer** (points → 0) |
| `TRACe:DATA? ITRACE` after a full cycle | live-ok | index,frequency pairs `0,26800000,1,26825000,…,40,27800000`, terminator pair `9.9E37,9.9E37` |
| `TRACe:DATA? MTRACE` under PACKed | live-ok | returns ASCII `0` (unusable) — read traces in ASC |
| `STATus:OPERation:SWEeping:CONDition?` | live-ok | `0` idle in CW; `8` while a sweep is configured/running; `10` seen after re-INIT |

## Undocumented UDP trace registry

## Trace subsystem

| Command | Status | Observed response / notes |
|---|---|---|
| `TRACe:CATalog?` | live-ok | `"MTRACE", "ITRACE", "IFPAN", "SSTART", "SSTOP", "UDP"` |
| `TRACe:POINts? MTRACE` etc. | live-ok | `0` (all buffers idle) |
| `TRACe:DATA? MTRACE` | live-ok | `9.91E37` under ASC (no-data marker); `'0'` with PACKed |
| `TRACe:DATA? "MTRACE"` | live-error | `-158,"String data not allowed"` — names must be unquoted |
| `FORMat:DATA?` | live-ok | `ASC` |
| `FORMat:DATA PACKed` | live-ok | accepted; switches scalar/trace outputs to packed |
| `FORMat:DATA REAL,32` / `INT,16` | live-error | `-141,"Invalid character data"` — REAL/INT forms not implemented |

## Undocumented UDP trace registry

| Command | Status | Observed response / notes |
|---|---|---|
| `TRACE:UDP?` | live-ok | `DEF\n001\n002\n003` baseline; registered slots show `"ip", port, TAG` |
| `TRACE:UDP:TAG:ON "<ip>",<port>,AUDIO` | live-ok | accepted; audio flows once `SYST:AUD:REM:MODE` > 0 |
| `…TAG:ON …,<IFPAN/FSCAN/MSCAN/CW/LIST>` | live-ok | accepted (no datagrams while receiver idle) |
| `…TAG:ON …,IF` | live-error | `-141,"Invalid character data"` — not a tag on 2.80 |
| `…TAG:ON …,FS/CM/LEVEL/MEAS` | live-error | `-141` |
| `…:DEFAULT:TAG:ON …` | live-error | `-141`/`-113` — weakmon-era DEFAULT forms not supported |
| `TRACE:UDP:FLAG:ON "<ip>",<port>,"PRIVATE"` | live-error | `-151,"Invalid string data"` (runds variant unsupported) |
| `TRACE:UDP:TAG:OFF "<ip>",<port>,<TAG>` | live-ok | accepted; detaches tag |
| `TRACE:UDP:DELETE "<ip>",<port>` | live-ok | removes slot; exactly two args (`-103` with third) |
| `SYST:IF:REM:MODE SHORT` | live-error | `-113,"Undefined header"` — runds IF path not present on 2.80 |

## Status system (documented, verified idle)

| Command | Status | Observed response |
|---|---|---|
| `STATus:OPERation?` / `:CONDition?` | live-ok | `0` |
| `STATus:OPERation:SWEeping:CONDition?` | live-ok | `0` |
| `STATus:QUEStionable:CONDition?` | live-ok | `0` |
| `STATus:TRACe:CONDition?` | live-ok | `0` (also `0` while an audio stream was active) |

## Known not-yet-exercised (for later phases)

Documented in manual chapter 4, not yet tested live: `INITiate[:IMMediate]`,
`SENSe:FUNCtion 'ON',…` sensor enablement, `FREQuency:STARt/STOP`,
`SWEep:*` sweep configuration (`STEep`, `DWELl`, `HOLD`, `COUNt`, …),
`SUPPress` lists (`TRACe SSTART/SSTOP`), `SYSTem:AUDio:*` sets, memory
(`MEMory`), display commands, `*TST?` (deliberately not executed: it
triggers a module self-test).
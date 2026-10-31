import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { AudioStreamer, type StreamerStatus } from "../lib/audio-streamer";
import {
  AfAutoRange,
  AfDsp,
  TRACE_DB_MAX,
  TRACE_DB_MIN,
  type AfSnapshot,
} from "../lib/af-dsp";
import { levelToColor } from "../lib/spectrum";
import type { AudioStatus, Recording } from "../lib/types";
import { HelpTip } from "./help";

type AfTraceView = "spectrum" | "scope";
type AfScrollView = "spectrogram" | "wavelet" | "off";

interface Props {
  connected: boolean;
  ownsControl: boolean;
  status: AudioStatus;
}

const AF_BG = "#0a0e14";
const AF_GRID = "#223041";
const AF_LABEL = "#8a94a3";
const DEFAULT_AF_RANGE = { min: -70, max: 0 };

export function AudioPanel({ connected, ownsControl, status }: Props) {
  const streamerRef = useRef<AudioStreamer | null>(null);
  const dspRef = useRef<AfDsp | null>(null);
  if (dspRef.current === null) dspRef.current = new AfDsp();
  const [streamer, setStreamer] = useState<StreamerStatus>({
    ws: "down",
    rateHz: 0,
    playing: false,
    packets: 0,
    lost: 0,
  });
  const [listening, setListening] = useState(false);
  const [volume, setVolume] = useState(0.8);
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [afTrace, setAfTrace] = useState<AfTraceView>(() =>
    localStorage.getItem("miniport-af-trace") === "scope" ? "scope" : "spectrum",
  );
  const [afScroll, setAfScroll] = useState<AfScrollView>(() => {
    const stored = localStorage.getItem("miniport-af-scroll");
    return stored === "wavelet" || stored === "off" ? stored : "spectrogram";
  });
  const [afSensitivity, setAfSensitivity] = useState(() => {
    const stored = Number(localStorage.getItem("miniport-af-sens"));
    return Number.isFinite(stored) && stored >= 0 && stored <= 100 ? stored : 50;
  });
  const [afSnap, setAfSnap] = useState<AfSnapshot | null>(null);

  const afTraceCanvas = useRef<HTMLCanvasElement | null>(null);
  const afScrollCanvas = useRef<HTMLCanvasElement | null>(null);
  const listeningRef = useRef(false);
  const afTraceRef = useRef(afTrace);
  const afScrollRef = useRef(afScroll);
  const afSensRef = useRef(afSensitivity);
  const specRangeRef = useRef(new AfAutoRange());
  const waveRangeRef = useRef(new AfAutoRange());
  const lastAfRateRef = useRef(0);

  useEffect(() => {
    listeningRef.current = listening;
  }, [listening]);
  useEffect(() => {
    afTraceRef.current = afTrace;
    try {
      localStorage.setItem("miniport-af-trace", afTrace);
    } catch {
      /* private mode */
    }
  }, [afTrace]);
  useEffect(() => {
    afScrollRef.current = afScroll;
    try {
      localStorage.setItem("miniport-af-scroll", afScroll);
    } catch {
      /* private mode */
    }
  }, [afScroll]);
  useEffect(() => {
    afSensRef.current = afSensitivity;
    try {
      localStorage.setItem("miniport-af-sens", String(afSensitivity));
    } catch {
      /* private mode */
    }
  }, [afSensitivity]);

  useEffect(() => {
    const streamerInstance = new AudioStreamer();
    streamerInstance.onStatus = (next) => setStreamer(next);
    streamerInstance.onSamples = (samples, rateHz) =>
      dspRef.current?.feed(samples, rateHz);
    streamerRef.current = streamerInstance;
    return () => streamerInstance.dispose();
  }, []);

  useEffect(() => {
    streamerRef.current?.ingestServerStatus(status);
  }, [status]);

  // AF DSP draw tick at ~10 Hz, matching the spectrum panel's cadence
  const tickAf = useCallback(() => {
    const dsp = dspRef.current;
    const traceCanvas = afTraceCanvas.current;
    if (!dsp || !traceCanvas) return;
    const snap = dsp.snapshot();
    if (snap.rateHz !== lastAfRateRef.current) {
      // wire-rate change: old pixel history has the wrong frequency scale
      lastAfRateRef.current = snap.rateHz;
      specRangeRef.current.reset();
      waveRangeRef.current.reset();
      clearCanvas(traceCanvas);
      if (afScrollCanvas.current) clearCanvas(afScrollCanvas.current);
    }
    setAfSnap(snap);
    if (afTraceRef.current === "spectrum") {
      const row = dsp.spectrumRow();
      if (row) drawAfSpectrum(traceCanvas, row, dsp.spectrumHz());
    } else {
      const win = dsp.scopeTrace();
      if (win) drawAfScope(traceCanvas, win, snap.rateHz);
    }
    const scrollCanvas = afScrollCanvas.current;
    if (!scrollCanvas || afScrollRef.current === "off") return;
    if (afScrollRef.current === "spectrogram") {
      const row = dsp.spectrogramRow();
      if (row) {
        specRangeRef.current.push(row);
        const range =
          specRangeRef.current.range(afSensRef.current) ?? DEFAULT_AF_RANGE;
        appendAfRow(scrollCanvas, row, range.min, range.max);
      }
    } else {
      const row = dsp.waveletRow();
      if (row) {
        waveRangeRef.current.push(row);
        const range =
          waveRangeRef.current.range(afSensRef.current) ?? DEFAULT_AF_RANGE;
        appendAfRow(scrollCanvas, row, range.min, range.max);
      }
    }
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (listeningRef.current) tickAf();
      else setAfSnap(null);
    }, 100);
    return () => window.clearInterval(timer);
  }, [tickAf]);

  const clearAfCanvases = () => {
    if (afTraceCanvas.current) {
      clearCanvas(afTraceCanvas.current);
    }
    if (afScrollCanvas.current) {
      clearCanvas(afScrollCanvas.current);
    }
  };

  const refreshRecordings = () => {
    api
      .recordings()
      .then((data) => setRecordings(data))
      .catch(() => setRecordings([]));
  };
  useEffect(() => {
    refreshRecordings();
    const timer = window.setInterval(refreshRecordings, 5000);
    return () => window.clearInterval(timer);
  }, []);

  const toggleListen = async () => {
    const streamerInstance = streamerRef.current!;
    if (!listening) {
      try {
        await streamerInstance.start();
        await api.audioStart();
        setListening(true);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    } else {
      try {
        if (status.recording) await api.record("stop");
        await api.audioStop();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
      streamerInstance.stop();
      setListening(false);
      refreshRecordings();
    }
  };

  const toggleRecording = async () => {
    try {
      if (!status.recording) {
        await api.record("start");
      } else {
        await api.record("stop");
        refreshRecordings();
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const controllable = connected && ownsControl;
  const lostPct =
    streamer.packets > 0
      ? ((streamer.lost / streamer.packets) * 100).toFixed(1)
      : "0.0";
  const waveletAxis =
    dspRef.current.waveletHz() ?? { low: 50, high: 2000 };
  const waveletTicks = [50, 100, 200, 500, 1000, 2000].filter(
    (hz) => hz <= waveletAxis.high,
  );

  return (
    <div className="panel-body">
      <h2>
        <HelpTip term="listen">REMOTE AUDIO</HelpTip>{" "}
        {status.running ? <span className="scan-flag">· STREAMING</span> : null}
        {status.recording ? <span className="rec-flag"> · REC</span> : null}
      </h2>
      <div className="field-row">
        <button
          className={`switch ${listening ? "on" : ""}`}
          disabled={!controllable}
          onClick={toggleListen}
          aria-pressed={listening}
        >
          {listening ? "STOP LISTENING" : "LISTEN"}
        </button>
        <button
          className={`switch ${status.recording ? "on rec" : ""}`}
          disabled={!controllable || !status.running}
          onClick={toggleRecording}
        >
          {status.recording ? "STOP REC" : "RECORD"}
        </button>
        {listening ? (
          <span className="metric">
            rate {streamer.rateHz} Hz · {streamer.packets} pkt ·{" "}
            {lostPct}% lost
          </span>
        ) : (
          <span className="metric">
            {status.rate_hz && status.running
              ? `rate ${status.rate_hz} Hz`
              : "not streaming"}
          </span>
        )}
      </div>
      <label className="slider">
        LISTEN VOLUME (browser)
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={volume}
          disabled={!listening}
          onChange={(ev) => {
            const value = Number.parseFloat(ev.target.value);
            setVolume(value);
            streamerRef.current?.setVolume(value);
          }}
        />
      </label>
      {listening && streamer.packets === 0 ? (
        <div className="banner">
          <span>
            connected but no audio packets — the host firewall needs a UDP
            allow rule for the receiver (docs/protocol-notes.md)
          </span>
        </div>
      ) : null}
      {listening ? (
        <>
          <h2>
            <HelpTip term="af_dsp">AF DSP DISPLAY</HelpTip>
            {dspRef.current.spectrumHz().high > 0 ? (
              <span className="metric">
                {" · AF "}
                {(dspRef.current.spectrumHz().high / 1000).toFixed(1)} kHz BAND
              </span>
            ) : null}
          </h2>
          <div className="field-row">
            <span className="chips-label">
              <HelpTip term="af_trace">TRACE</HelpTip>
            </span>
            <div className="chips">
              <button
                className={`chip ${afTrace === "spectrum" ? "active" : ""}`}
                onClick={() => setAfTrace("spectrum")}
              >
                SPECTRUM
              </button>
              <button
                className={`chip ${afTrace === "scope" ? "active" : ""}`}
                onClick={() => setAfTrace("scope")}
              >
                SCOPE
              </button>
            </div>
            <span className="chips-label">
              <HelpTip term="af_waterfall">WATERFALL</HelpTip>
            </span>
            <div className="chips">
              <button
                className={`chip ${afScroll === "spectrogram" ? "active" : ""}`}
                onClick={() => setAfScroll("spectrogram")}
              >
                SPECTROGRAM
              </button>
              <button
                className={`chip ${afScroll === "wavelet" ? "active" : ""}`}
                onClick={() => setAfScroll("wavelet")}
              >
                WAVELET
              </button>
              <button
                className={`chip ${afScroll === "off" ? "active" : ""}`}
                onClick={() => setAfScroll("off")}
              >
                OFF
              </button>
            </div>
            <label className="slider">
              <HelpTip term="af_sensitivity">SENSITIVITY</HelpTip>
              <input
                type="range"
                min={0}
                max={100}
                step={1}
                value={afSensitivity}
                disabled={afScroll === "off"}
                onChange={(ev) =>
                  setAfSensitivity(Number.parseInt(ev.target.value, 10))
                }
              />
            </label>
            <button onClick={clearAfCanvases}>CLEAR</button>
          </div>
          <canvas
            ref={afTraceCanvas}
            width={960}
            height={190}
            className="spectrum-canvas af-trace"
          />
          <canvas
            ref={afScrollCanvas}
            width={960}
            height={150}
            className="spectrum-canvas af-scroll"
            style={{ display: afScroll === "off" ? "none" : "block" }}
          />
          {afScroll === "wavelet" ? (
            <div className="wf-axis">
              {waveletTicks.map((hz) => (
                <span
                  key={hz}
                  style={{
                    left: `${
                      (Math.log2(hz / waveletAxis.low) /
                        Math.log2(waveletAxis.high / waveletAxis.low)) *
                      100
                    }%`,
                  }}
                >
                  {hz >= 1000 ? `${hz / 1000}k` : hz} Hz
                </span>
              ))}
            </div>
          ) : null}
          <div className="af-meter">
            <span className="chips-label">
              <HelpTip term="af_meter">AF POWER</HelpTip>
            </span>
            <div className="af-meter-bar">
              <div
                className="af-meter-fill"
                style={{ width: `${dbMeterPct(afSnap?.rmsDb)}%` }}
              />
            </div>
            <span className="metric af-db">
              {(afSnap?.rmsDb ?? -100).toFixed(1)} dBFS RMS
            </span>
            <div className="af-meter-bar">
              <div
                className="af-meter-fill peak"
                style={{ width: `${dbMeterPct(afSnap?.peakDb)}%` }}
              />
            </div>
            <span className="metric af-db">
              {(afSnap?.peakDb ?? -100).toFixed(1)} PEAK
            </span>
          </div>
          <span className="metric">
            <HelpTip term="af_decoders">TONE DECODERS</HelpTip>
            {" · DTMF "}
            <span className="af-decoder-value">
              {afSnap?.dtmf ? afSnap.dtmf.split("").join(" ") : "—"}
            </span>
            {" · CTCSS "}
            <span className="af-decoder-value">
              {afSnap?.ctcssHz ? afSnap.ctcssHz.toFixed(1) : "—"}
            </span>
            {afSnap?.cwHz ? (
              <span>
                {" · CW "}
                <span className="af-decoder-value">
                  {Math.round(afSnap.cwHz)} Hz
                </span>
              </span>
            ) : null}
          </span>
        </>
      ) : null}
      {error ? (
        <div className="banner">
          <span>⚠ {error}</span>
        </div>
      ) : null}
      <h2><HelpTip term="recordings">RECORDINGS</HelpTip></h2>
      {recordings.length === 0 ? (
        <div className="scan-empty">No recordings yet.</div>
      ) : (
        <ul className="recordings">
          {recordings.map((recording) => (
            <li key={recording.name}>
              <a href={`api/audio/recordings/${recording.name}`} download>
                {recording.name}
              </a>
              <span className="metric">
                {" "}
                ({Math.round(recording.bytes / 1024)} kB
                {recording.duration_s
                  ? ` · ${recording.duration_s.toFixed(1)} s`
                  : ""}
                {recording.trigger && recording.trigger !== "manual"
                  ? ` · ${recording.trigger}`
                  : ""}
                )
              </span>
              <button
                className="link"
                disabled={!controllable}
                onClick={async () => {
                  await api.deleteRecording(recording.name).catch(() => undefined);
                  refreshRecordings();
                }}
              >
                delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function dbMeterPct(db: number | undefined): number {
  if (db === undefined) return 0;
  const pct = ((db + 60) / 60) * 100;
  return Math.min(Math.max(pct, 0), 100);
}

function clearCanvas(canvas: HTMLCanvasElement) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.fillStyle = AF_BG;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
}

function drawAfSpectrum(
  canvas: HTMLCanvasElement,
  row: Float32Array,
  hz: { low: number; high: number },
) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const { width, height } = canvas;
  ctx.fillStyle = AF_BG;
  ctx.fillRect(0, 0, width, height);
  const padLeft = 40;
  const padBottom = 16;
  const plotWidth = width - padLeft - 8;
  const plotHeight = height - padBottom - 6;
  const span = TRACE_DB_MAX - TRACE_DB_MIN;
  const toY = (db: number) =>
    6 + (1 - (db - TRACE_DB_MIN) / span) * plotHeight;

  ctx.strokeStyle = AF_GRID;
  ctx.fillStyle = AF_LABEL;
  ctx.font = "10px ui-monospace, monospace";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 5; i++) {
    const db = TRACE_DB_MAX - (span * i) / 5;
    const y = toY(db);
    ctx.beginPath();
    ctx.moveTo(padLeft, y);
    ctx.lineTo(width - 4, y);
    ctx.stroke();
    ctx.fillText(Math.round(db).toString(), 4, y + 3);
  }
  for (let i = 0; i <= 4; i++) {
    const kHz = ((hz.high * i) / 4) / 1000;
    const x = padLeft + (i / 4) * plotWidth;
    ctx.fillText(`${kHz.toFixed(1)}k`, x - 10, height - 4);
  }

  ctx.strokeStyle = "#4fd1c5";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (let x = 0; x < row.length; x++) {
    const px = padLeft + (x / (row.length - 1)) * plotWidth;
    const y = toY(row[x]);
    if (x === 0) ctx.moveTo(px, y);
    else ctx.lineTo(px, y);
  }
  ctx.stroke();
}

function drawAfScope(
  canvas: HTMLCanvasElement,
  win: Float32Array,
  rateHz: number,
) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const { width, height } = canvas;
  ctx.fillStyle = AF_BG;
  ctx.fillRect(0, 0, width, height);
  const padLeft = 34;
  const plotWidth = width - padLeft - 8;
  const midY = height / 2;
  const scale = (height - 20) / 2;

  ctx.strokeStyle = AF_GRID;
  ctx.fillStyle = AF_LABEL;
  ctx.font = "10px ui-monospace, monospace";
  ctx.lineWidth = 1;
  for (const level of [1, 0.5, 0, -0.5, -1]) {
    const y = midY - level * scale;
    ctx.beginPath();
    ctx.moveTo(padLeft, y);
    ctx.lineTo(width - 4, y);
    ctx.stroke();
    ctx.fillText(level.toFixed(1), 4, y + 3);
  }
  const spanMs = (1000 * win.length) / rateHz;
  for (let i = 0; i <= 4; i++) {
    const x = padLeft + (i / 4) * plotWidth;
    ctx.fillText(`${((spanMs * i) / 4).toFixed(0)}ms`, x - 10, height - 2);
  }

  // per-column min/max keeps fast edges visible when downsampled
  ctx.strokeStyle = "#4fd1c5";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = 0; x < plotWidth; x++) {
    const from = Math.floor((x * win.length) / plotWidth);
    const to = Math.max(
      from + 1,
      Math.floor(((x + 1) * win.length) / plotWidth),
    );
    let min = win[from];
    let max = min;
    for (let i = from + 1; i < to && i < win.length; i++) {
      if (win[i] < min) min = win[i];
      if (win[i] > max) max = win[i];
    }
    const px = padLeft + x;
    ctx.moveTo(px, midY - max * scale);
    ctx.lineTo(px, midY - min * scale + 1);
  }
  ctx.stroke();
}

function appendAfRow(
  canvas: HTMLCanvasElement,
  row: Float32Array,
  dbMin: number,
  dbMax: number,
) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const { width, height } = canvas;
  const rowHeight = 2;
  // shift existing rows down; the newest frame appears at the top
  ctx.drawImage(
    canvas,
    0,
    0,
    width,
    height - rowHeight,
    0,
    rowHeight,
    width,
    height - rowHeight,
  );
  for (let x = 0; x < width; x++) {
    const idx = Math.min(
      Math.floor((x / width) * row.length),
      row.length - 1,
    );
    const [r, g, b] = levelToColor(row[idx], dbMin, dbMax);
    ctx.fillStyle = `rgb(${r},${g},${b})`;
    ctx.fillRect(x, 0, 1, rowHeight);
  }
}
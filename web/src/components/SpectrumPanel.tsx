import { useCallback, useEffect, useRef, useState } from "react";
import { api, clientId } from "../lib/api";
import {
  decodeSpectrumFrame,
  WaterfallNormalizer,
  type SpectrumFrame,
  topPeak,
} from "../lib/spectrum";
import {
  PALETTES,
  WaterfallBuffer,
  WaterfallRenderer,
  fmtClock,
  fmtDuration,
  fmtFreqMhz,
  freqTicks,
  timeInterval,
  type PaletteName,
} from "../lib/waterfall";
import type { Preset, SpectrumStatus } from "../lib/types";
import { HelpTip } from "./help";

interface Props {
  connected: boolean;
  ownsControl: boolean;
  scanning: boolean;
  status: SpectrumStatus;
  tuneHz: number | null;
  thresholdDbuv?: number | null;
  onTune?: (hz: number) => void;
}

const DB_MIN_DEFAULT = -120;
const DB_MAX_DEFAULT = 20;

// Shared instrument-face geometry: the trace, the waterfall and the axis
// strip all plot between the same left gutter and right edge, so tick marks
// line up with data in both displays.
const TRACE_H = 210;
const AXIS_H = 24;
const PAD_LEFT = 48;
const PAD_RIGHT = 4;

const WATERFALL_HEIGHTS = [320, 480, 640];
const ROW_DENSITIES = [1, 2];
const AVG_WINDOWS = [0, 3, 10];

const SPAN_CHIPS: [string, number][] = [
  ["0.5 MHz", 500_000],
  ["1 MHz", 1_000_000],
  ["5 MHz", 5_000_000],
  ["10 MHz", 10_000_000],
];
const STEP_CHIPS: [string, number][] = [
  ["1k", 1_000],
  ["5k", 5_000],
  ["12.5k", 12_500],
  ["25k", 25_000],
  ["100k", 100_000],
];

interface Hover {
  x: number;
  y: number;
  kind: "trace" | "waterfall";
}

interface CadenceInfo {
  spRow: number | null;
  rows: number;
  seconds: number | null;
}

function storedNumber(key: string, fallback: number, valid: number[]): number {
  try {
    const v = Number(localStorage.getItem(key));
    return valid.includes(v) ? v : fallback;
  } catch {
    return fallback;
  }
}

function storedText(key: string, fallback: string, valid: string[]): string {
  try {
    const v = localStorage.getItem(key);
    return v !== null && valid.includes(v) ? v : fallback;
  } catch {
    return fallback;
  }
}

function store(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: in-memory only */
  }
}

/// Prepares a canvas for CSS-px drawing in device pixels.
function fitCanvas(
  canvas: HTMLCanvasElement,
  cssHeight: number,
): CanvasRenderingContext2D | null {
  const cssWidth = canvas.clientWidth;
  if (cssWidth <= 0) return null;
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(cssWidth * dpr);
  const h = Math.round(cssHeight * dpr);
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}

export function SpectrumPanel({
  connected,
  ownsControl,
  scanning,
  status,
  tuneHz,
  thresholdDbuv,
  onTune,
}: Props) {
  const [startMhz, setStartMhz] = useState("27.0000");
  const [stopMhz, setStopMhz] = useState("27.4000");
  const [stepKhz, setStepKhz] = useState("25");
  const [dbMin, setDbMin] = useState(DB_MIN_DEFAULT);
  const [dbMax, setDbMax] = useState(DB_MAX_DEFAULT);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetName, setPresetName] = useState("");
  const [lastPeak, setLastPeak] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [frozen, setFrozen] = useState(false);
  const [showPeak, setShowPeak] = useState(true);
  const [showWaterfall, setShowWaterfall] = useState(true);
  const [autoWaterfall, setAutoWaterfall] = useState(
    () => localStorage.getItem("miniport-wf-auto") !== "0",
  );
  const [sensitivity, setSensitivity] = useState(() => {
    const stored = Number(localStorage.getItem("miniport-wf-sens"));
    return Number.isFinite(stored) && stored >= 0 && stored <= 100 ? stored : 50;
  });
  const [computedRange, setComputedRange] = useState<{ min: number; max: number } | null>(null);
  const [wfHeight, setWfHeight] = useState(() =>
    storedNumber("miniport-wf-height", 320, WATERFALL_HEIGHTS),
  );
  const [rowDensity, setRowDensity] = useState(() =>
    storedNumber("miniport-wf-row", 2, ROW_DENSITIES),
  );
  const paletteNames = Object.keys(PALETTES);
  const [palette, setPalette] = useState<PaletteName>(() =>
    storedText("miniport-wf-palette", "eb200", paletteNames) as PaletteName,
  );
  const [avgWindow, setAvgWindow] = useState(() =>
    storedNumber("miniport-wf-avg", 0, AVG_WINDOWS),
  );
  const [cadence, setCadence] = useState<CadenceInfo>({
    spRow: null,
    rows: 0,
    seconds: null,
  });

  const spectrumCanvas = useRef<HTMLCanvasElement | null>(null);
  const waterfallCanvas = useRef<HTMLCanvasElement | null>(null);
  const axisCanvas = useRef<HTMLCanvasElement | null>(null);
  const frameRef = useRef<SpectrumFrame | null>(null);
  const scaleRef = useRef({ min: DB_MIN_DEFAULT, max: DB_MAX_DEFAULT });
  const frozenRef = useRef(false);
  const showPeakRef = useRef(true);
  const showWaterfallRef = useRef(true);
  const autoRef = useRef(autoWaterfall);
  const sensitivityRef = useRef(sensitivity);
  const normalizerRef = useRef(new WaterfallNormalizer());
  const bandKeyRef = useRef<string | null>(null);
  const bufRef = useRef(new WaterfallBuffer());
  const rendererRef = useRef(new WaterfallRenderer());
  const paletteRef = useRef(palette);
  const heightRef = useRef(wfHeight);
  const densityRef = useRef(rowDensity);
  const avgRef = useRef(avgWindow);
  const avgBufRef = useRef<Float32Array | null>(null);
  const hoverRef = useRef<Hover | null>(null);
  const rangeRef = useRef({ min: DB_MIN_DEFAULT, max: DB_MAX_DEFAULT });
  const tuneRef = useRef(tuneHz);
  const thresholdRef = useRef(thresholdDbuv);
  const running = status.running;
  const controllable = connected && ownsControl && !scanning;
  const canStop = connected && ownsControl && running;

  /// Palette range for painting a new row (and for the legend). While AUTO
  /// normalizer re-adapts after a band change it holds the last computed
  /// range, so the first rows of a new band don't flash at the manual trace
  /// scale.
  const wfRange = useCallback(() => {
    if (autoRef.current) {
      const range = normalizerRef.current.range(sensitivityRef.current);
      if (range) {
        rangeRef.current = range;
        return range;
      }
      return rangeRef.current;
    }
    return scaleRef.current;
  }, []);

  useEffect(() => {
    tuneRef.current = tuneHz;
  }, [tuneHz]);
  useEffect(() => {
    thresholdRef.current = thresholdDbuv;
  }, [thresholdDbuv]);

  /// Exponential moving average of the trace, like a video filter.
  const updateAvg = useCallback((frame: SpectrumFrame) => {
    const n = avgRef.current;
    if (!n || n < 2) {
      avgBufRef.current = null;
      return;
    }
    const levels = frame.levels;
    let buf = avgBufRef.current;
    if (!buf || buf.length !== levels.length) {
      buf = new Float32Array(levels.length);
      for (let i = 0; i < levels.length; i++) {
        buf[i] = levels[i] === null ? Number.NaN : (levels[i] as number);
      }
    } else {
      const alpha = 2 / (n + 1);
      for (let i = 0; i < levels.length; i++) {
        const v = levels[i];
        if (v === null) continue;
        const cur = buf[i];
        buf[i] = Number.isNaN(cur) ? v : cur + (v - cur) * alpha;
      }
    }
    avgBufRef.current = buf;
  }, []);

  const renderAll = useCallback(() => {
    const frame = frameRef.current;
    const spectrum = spectrumCanvas.current;
    const waterfall = waterfallCanvas.current;
    const axis = axisCanvas.current;
    if (!frame || !spectrum || !axis) return;

    if (autoRef.current) {
      const range = normalizerRef.current.range(sensitivityRef.current);
      if (range) {
        const rounded = { min: Math.round(range.min), max: Math.round(range.max) };
        setComputedRange((previous) =>
          previous && previous.min === rounded.min && previous.max === rounded.max
            ? previous
            : rounded,
        );
      }
    }
    const { min: wfMin, max: wfMax } = wfRange();
    rangeRef.current = { min: wfMin, max: wfMax };

    const spanHz = Math.max(frame.stepHz * (frame.points - 1), 1);
    const ticks = freqTicks(frame.startHz, spanHz);
    const cssW = axis.clientWidth;
    const plotW = Math.max(cssW - PAD_LEFT - PAD_RIGHT, 10);

    const traceCtx = fitCanvas(spectrum, TRACE_H);
    if (traceCtx) {
      const trace =
        avgRef.current > 1 && avgBufRef.current ? avgBufRef.current : frame.levels;
      drawSpectrum(traceCtx, cssW, frame, trace, scaleRef.current.min, scaleRef.current.max, {
        padLeft: PAD_LEFT,
        plotW,
        ticks,
        hover: hoverRef.current?.kind === "trace" ? hoverRef.current : null,
        showPeak: showPeakRef.current,
        tuneHz: tuneRef.current,
        thresholdDbuv: thresholdRef.current ?? null,
      });
    }

    if (waterfall && showWaterfallRef.current) {
      const wfCtx = fitCanvas(waterfall, heightRef.current);
      if (wfCtx) {
        drawWaterfall(wfCtx, cssW, heightRef.current, bufRef.current, rendererRef.current, {
          padLeft: PAD_LEFT,
          plotW,
          density: densityRef.current,
          min: wfMin,
          max: wfMax,
          palette: paletteRef.current,
          ticks,
          hover: hoverRef.current?.kind === "waterfall" ? hoverRef.current : null,
          tuneHz: tuneRef.current,
          startHz: frame.startHz,
          spanHz,
        });
      }
    }

    const axisCtx = fitCanvas(axis, AXIS_H);
    if (axisCtx) {
      drawAxis(axisCtx, cssW, frame, { padLeft: PAD_LEFT, plotW, ticks });
    }

    const spRow = bufRef.current.secondsPerRow();
    const rowsVisible = Math.min(
      bufRef.current.length,
      Math.floor(heightRef.current / densityRef.current),
    );
    const seconds = spRow !== null && rowsVisible > 1 ? rowsVisible * spRow : null;
    setCadence((previous) =>
      previous.spRow === spRow && previous.rows === rowsVisible && previous.seconds === seconds
        ? previous
        : { spRow, rows: rowsVisible, seconds },
    );
  }, [wfRange]);

  useEffect(() => {
    frozenRef.current = frozen;
  }, [frozen]);
  useEffect(() => {
    autoRef.current = autoWaterfall;
    store("miniport-wf-auto", autoWaterfall ? "1" : "0");
  }, [autoWaterfall]);
  useEffect(() => {
    sensitivityRef.current = sensitivity;
    store("miniport-wf-sens", String(sensitivity));
  }, [sensitivity]);
  useEffect(() => {
    showPeakRef.current = showPeak;
  }, [showPeak]);
  useEffect(() => {
    showWaterfallRef.current = showWaterfall;
    renderAll();
  }, [showWaterfall, renderAll]);
  useEffect(() => {
    scaleRef.current = { min: dbMin, max: dbMax };
  }, [dbMin, dbMax]);
  // Height/density only change how the buffer maps onto the canvas; the
  // buffer itself survives, so zooming the time axis re-reveals old rows.
  useEffect(() => {
    heightRef.current = wfHeight;
    store("miniport-wf-height", String(wfHeight));
    renderAll();
  }, [wfHeight, renderAll]);
  useEffect(() => {
    densityRef.current = rowDensity;
    store("miniport-wf-row", String(rowDensity));
    renderAll();
  }, [rowDensity, renderAll]);
  // A palette change repaints the whole retained history with the current
  // colour range.
  useEffect(() => {
    paletteRef.current = palette;
    store("miniport-wf-palette", palette);
    const { min, max } = wfRange();
    rendererRef.current.rerender(bufRef.current.all(), min, max, palette);
    renderAll();
  }, [palette, renderAll, wfRange]);
  useEffect(() => {
    avgRef.current = avgWindow;
    store("miniport-wf-avg", String(avgWindow));
  }, [avgWindow]);

  useEffect(() => {
    api
      .presets()
      .then(setPresets)
      .catch(() => setPresets([]));
  }, [running]);

  // spectrum websocket: binary frames straight into the buffer
  useEffect(() => {
    let socket: WebSocket | null = null;
    let disposed = false;
    let retry = 0;
    let timer: number | undefined;

    const connect = () => {
      if (disposed) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const url = `${proto}://${location.host}/ws/spectrum?client_id=${encodeURIComponent(
        clientId(),
      )}`;
      socket = new WebSocket(url);
      socket.binaryType = "arraybuffer";
      socket.onmessage = (ev) => {
        if (!(ev.data instanceof ArrayBuffer)) return;
        const frame = decodeSpectrumFrame(ev.data);
        if (!frame) return;
        const bandKey = `${frame.startHz}/${frame.stepHz}/${frame.points}`;
        if (bandKeyRef.current === null) {
          bandKeyRef.current = bandKey;
        } else if (bandKeyRef.current !== bandKey) {
          // the swept band changed: forget the old level distribution
          bandKeyRef.current = bandKey;
          normalizerRef.current.reset();
          avgBufRef.current = null; // trace average restarts on the new band
        }
        normalizerRef.current.push(frame.levels);
        updateAvg(frame);
        frameRef.current = frame;
        if (!frozenRef.current) {
          // FREEZE is a true snapshot: rows arriving while frozen are dropped
          const { min, max } = wfRange();
          const painted = bufRef.current.push(frame.levels, frame.ts, bandKey);
          for (const row of painted) {
            rendererRef.current.push(row, min, max, paletteRef.current);
          }
        }
        const peak = frame.peak ?? frame.levels;
        const top = topPeak(peak, frame.startHz, frame.stepHz);
        if (top) setLastPeak(`${(top.hz / 1e6).toFixed(4)} MHz · ${top.level.toFixed(1)} dBµV`);
      };
      socket.onclose = () => {
        if (!disposed) {
          retry += 1;
          timer = window.setTimeout(connect, Math.min(500 * retry, 5000));
        }
      };
    };
    connect();
    const draw = window.setInterval(() => {
      if (!frozenRef.current) renderAll();
    }, 100);
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
      window.clearInterval(draw);
      socket?.close();
    };
  }, [renderAll, updateAvg, wfRange]);

  // window resize: re-fit the canvases and redraw from the buffer
  useEffect(() => {
    const targets = [spectrumCanvas.current, waterfallCanvas.current, axisCanvas.current];
    const observer = new ResizeObserver(() => renderAll());
    targets.forEach((target) => target && observer.observe(target));
    return () => observer.disconnect();
  }, [renderAll]);

  const clearWaterfall = () => {
    bufRef.current.clear();
    rendererRef.current.clear();
    renderAll();
  };

  const savePng = () => {
    hoverRef.current = null;
    renderAll(); // drop the transient crosshair from the export
    const spectrum = spectrumCanvas.current;
    const waterfall = showWaterfall ? waterfallCanvas.current : null;
    const axis = axisCanvas.current;
    if (!spectrum || !axis) return;
    const dpr = window.devicePixelRatio || 1;
    const cssW = axis.clientWidth || spectrum.clientWidth;
    const wfH = waterfall ? heightRef.current : 0;
    const captionH = 30;
    const cssH = TRACE_H + wfH + AXIS_H + captionH;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#0a0e14";
    ctx.fillRect(0, 0, cssW, cssH);
    ctx.drawImage(spectrum, 0, 0, cssW, TRACE_H);
    let y = TRACE_H;
    if (waterfall) {
      ctx.drawImage(waterfall, 0, y, cssW, wfH);
      y += wfH;
    }
    ctx.drawImage(axis, 0, y, cssW, AXIS_H);
    y += AXIS_H;
    const frame = frameRef.current;
    const rows = bufRef.current.all().filter((row) => !row.bandChange);
    const timeRange =
      rows.length >= 2 ? `${fmtClock(rows[0].ts)}–${fmtClock(rows[rows.length - 1].ts)}` : "—";
    const caption = frame
      ? `${(frame.startHz / 1e6).toFixed(4)}–${((frame.startHz + frame.stepHz * (frame.points - 1)) / 1e6).toFixed(4)} MHz · step ${
          frame.stepHz < 1e3 ? frame.stepHz.toFixed(1) : (frame.stepHz / 1e3).toFixed(0)
        } kHz · ${timeRange} · colour ${Math.round(rangeRef.current.min)}–${Math.round(
          rangeRef.current.max,
        )} dBµV (${autoRef.current ? "AUTO" : "MANUAL"}) · ${PALETTES[paletteRef.current].label}${
          frozen ? " · FROZEN" : ""
        }`
      : "no frames captured";
    ctx.fillStyle = "#8a94a3";
    ctx.font = "10px ui-monospace, monospace";
    ctx.fillText(caption, 8, y + 19);
    const link = document.createElement("a");
    link.download = `spectrum-${new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)}.png`;
    link.href = canvas.toDataURL("image/png");
    link.click();
  };

  const exportCsv = () => {
    const rows = bufRef.current.all();
    if (!rows.length) return;
    const lines = ["timestamp_s,frequency_hz,level_dbuv"];
    for (const row of rows) {
      if (row.bandChange) continue;
      const [start, step] = row.band.split("/").map(Number);
      for (let i = 0; i < row.levels.length; i++) {
        const level = row.levels[i];
        if (!Number.isFinite(level)) continue;
        lines.push(`${row.ts.toFixed(3)},${Math.round(start + i * step)},${level.toFixed(1)}`);
      }
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const link = document.createElement("a");
    link.download = `waterfall-${new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)}.csv`;
    link.href = URL.createObjectURL(blob);
    link.click();
    URL.revokeObjectURL(link.href);
  };

  const hoverOn = (canvas: HTMLCanvasElement, kind: Hover["kind"]) => (ev: React.PointerEvent) => {
    const rect = canvas.getBoundingClientRect();
    hoverRef.current = { x: ev.clientX - rect.left, y: ev.clientY - rect.top, kind };
    renderAll();
  };
  const hoverOff = () => {
    hoverRef.current = null;
    renderAll();
  };
  const pickFreq = (canvas: HTMLCanvasElement) => (ev: React.MouseEvent) => {
    if (!onTune || !controllable) return;
    const frame = frameRef.current;
    if (!frame) return;
    const rect = canvas.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const plotW = Math.max(rect.width - PAD_LEFT - PAD_RIGHT, 1);
    if (x < PAD_LEFT || x > PAD_LEFT + plotW) return;
    const spanHz = Math.max(frame.stepHz * (frame.points - 1), 1);
    const hz = Math.round(frame.startHz + ((x - PAD_LEFT) / plotW) * spanHz);
    onTune(Math.min(Math.max(hz, frame.startHz), frame.startHz + spanHz));
  };

  const centreOnTune = (span: number) => {
    if (!tuneHz) return;
    setStartMhz(((tuneHz - span / 2) / 1e6).toFixed(4));
    setStopMhz(((tuneHz + span / 2) / 1e6).toFixed(4));
  };

  const start = async () => {
    try {
      await api.spectrumStart(
        Number.parseFloat(startMhz) * 1e6,
        Number.parseFloat(stopMhz) * 1e6,
        Number.parseFloat(stepKhz) * 1e3,
      );
      setApiError(null);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : String(error));
    }
  };

  const savePreset = async () => {
    try {
      await api.presetCreate(
        presetName || `band ${startMhz}–${stopMhz} MHz`,
        Number.parseFloat(startMhz) * 1e6,
        Number.parseFloat(stopMhz) * 1e6,
        Number.parseFloat(stepKhz) * 1e3,
      );
      setPresetName("");
      setPresets(await api.presets());
      setApiError(null);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : String(error));
    }
  };

  const applyPreset = (preset: Preset) => {
    const params = preset.params;
    setStartMhz((Number(params.start_hz) / 1e6).toFixed(4));
    setStopMhz((Number(params.stop_hz) / 1e6).toFixed(4));
    setStepKhz((Number(params.step_hz) / 1e3).toFixed(0));
  };

  const legendRange =
    autoWaterfall && computedRange
      ? computedRange
      : { min: Math.round(dbMin), max: Math.round(dbMax) };
  const legendGradient = `linear-gradient(90deg, ${PALETTES[palette].stops
    .map(([r, g, b], i) => `rgb(${r},${g},${b}) ${(i / (PALETTES[palette].stops.length - 1)) * 100}%`)
    .join(", ")})`;

  return (
    <div className="panel-body">
      <h2>
        <HelpTip term="spectrum">LIVE SPECTRUM / WATERFALL</HelpTip>{" "}
        {running ? (
          <span className="scan-flag">· RUNNING ({status.cycles ?? 0} cycles)</span>
        ) : null}
        {status.last_error ? (
          <span className="scan-flag"> · last cycle error</span>
        ) : null}
      </h2>
      <div className="field-row">
        <label>
          START MHz
          <input value={startMhz} disabled={!controllable || running} onChange={(ev) => setStartMhz(ev.target.value)} inputMode="decimal" />
        </label>
        <label>
          STOP MHz
          <input value={stopMhz} disabled={!controllable || running} onChange={(ev) => setStopMhz(ev.target.value)} inputMode="decimal" />
        </label>
        <label>
          STEP kHz
          <input value={stepKhz} disabled={!controllable || running} onChange={(ev) => setStepKhz(ev.target.value)} inputMode="decimal" />
        </label>
        {running ? (
          <button
            className="run stop"
            disabled={!canStop}
            onClick={() => api.spectrumStop().catch((error) => setApiError(error.message))}
          >
            STOP
          </button>
        ) : (
          <button
            className="run"
            disabled={!controllable}
            onClick={start}
          >
            START SWEEPING
          </button>
        )}
        <button
          disabled={!canStop}
          onClick={() => api.spectrumResetPeak().catch((error) => setApiError(error.message))}
        >
          <HelpTip term="reset_peak">RESET PEAK</HelpTip>
        </button>
        <a className="link" href="api/spectrum/export.csv" download>
          export CSV
        </a>
      </div>
      {apiError ? <ErrorLine text={apiError} /> : null}
      <div className="spectrum-stack">
        <canvas
          ref={spectrumCanvas}
          className="spectrum-canvas"
          onPointerMove={(ev) => {
            const canvas = ev.currentTarget;
            hoverOn(canvas, "trace")(ev);
          }}
          onPointerLeave={hoverOff}
          onClick={(ev) => pickFreq(ev.currentTarget)(ev)}
        />
        <canvas
          ref={waterfallCanvas}
          className="spectrum-canvas waterfall"
          style={{ display: showWaterfall ? undefined : "none", height: `${wfHeight}px` }}
          onPointerMove={(ev) => {
            const canvas = ev.currentTarget;
            hoverOn(canvas, "waterfall")(ev);
          }}
          onPointerLeave={hoverOff}
          onClick={(ev) => pickFreq(ev.currentTarget)(ev)}
        />
        <div className="wf-legend" style={{ display: showWaterfall ? undefined : "none" }}>
          <span className="legend-label">{legendRange.min}</span>
          <div className="legend-bar" style={{ background: legendGradient }} />
          <span className="legend-label">{legendRange.max} dBµV</span>
        </div>
        <canvas ref={axisCanvas} className="spectrum-axis" />
      </div>
      <div className="field-row">
        <button
          className={`switch ${frozen ? "on" : ""}`}
          aria-pressed={frozen}
          onClick={() => setFrozen(!frozen)}
        >
          {frozen ? "FROZEN — RESUME" : "FREEZE"}
        </button>
        <button onClick={clearWaterfall}>CLEAR WATERFALL</button>
        <button
          className={`switch ${showPeak ? "on" : ""}`}
          aria-pressed={showPeak}
          onClick={() => setShowPeak(!showPeak)}
        >
          PEAK LINE {showPeak ? "ON" : "OFF"}
        </button>
        <button
          className={`switch ${showWaterfall ? "on" : ""}`}
          aria-pressed={showWaterfall}
          onClick={() => setShowWaterfall(!showWaterfall)}
        >
          WATERFALL {showWaterfall ? "SHOWN" : "HIDDEN"}
        </button>
        <span className="chips-label"><HelpTip term="avg">AVG</HelpTip></span>
        <div className="chips">
          {AVG_WINDOWS.map((n) => (
            <button
              key={n}
              className={`chip ${avgWindow === n ? "active" : ""}`}
              onClick={() => setAvgWindow(n)}
            >
              {n === 0 ? "OFF" : n}
            </button>
          ))}
        </div>
        <button onClick={exportCsv}>
          <HelpTip term="wf_csv">WATERFALL CSV</HelpTip>
        </button>
        <button onClick={savePng}>SAVE PNG</button>
      </div>
      <div className="field-row">
        <span className="chips-label"><HelpTip term="wf_height">HEIGHT</HelpTip></span>
        <div className="chips">
          {WATERFALL_HEIGHTS.map((h) => (
            <button
              key={h}
              className={`chip ${wfHeight === h ? "active" : ""}`}
              onClick={() => setWfHeight(h)}
            >
              {h} px
            </button>
          ))}
        </div>
        <span className="chips-label"><HelpTip term="wf_density">ROW</HelpTip></span>
        <div className="chips">
          {ROW_DENSITIES.map((d) => (
            <button
              key={d}
              className={`chip ${rowDensity === d ? "active" : ""}`}
              onClick={() => setRowDensity(d)}
            >
              {d} px
            </button>
          ))}
        </div>
        <span className="chips-label"><HelpTip term="wf_palette">PALETTE</HelpTip></span>
        <div className="chips">
          {paletteNames.map((p) => (
            <button
              key={p}
              className={`chip ${palette === p ? "active" : ""}`}
              onClick={() => setPalette(p as PaletteName)}
            >
              {PALETTES[p as PaletteName].label}
            </button>
          ))}
        </div>
        {cadence.seconds !== null ? (
          <span className="metric">history ≈ {fmtDuration(cadence.seconds)}</span>
        ) : null}
      </div>
      <div className="field-row">
        <span className="chips-label"><HelpTip term="span_chips">SPAN</HelpTip></span>
        <div className="chips">
          {SPAN_CHIPS.map(([label, span]) => (
            <button
              key={label}
              className="chip"
              disabled={!controllable || running || !tuneHz}
              onClick={() => centreOnTune(span)}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="chips-label"><HelpTip term="step_chips">STEP</HelpTip></span>
        <div className="chips">
          {STEP_CHIPS.map(([label, hz]) => (
            <button
              key={label}
              className={`chip ${Number(stepKhz) * 1e3 === hz ? "active" : ""}`}
              disabled={!controllable || running}
              onClick={() => setStepKhz((hz / 1e3).toFixed(1).replace(/\.0$/, ""))}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="field-row">
        <button
          className={`switch ${autoWaterfall ? "on" : ""}`}
          aria-pressed={autoWaterfall}
          onClick={() => setAutoWaterfall(!autoWaterfall)}
        >
          AUTO WATERFALL {autoWaterfall ? "ON" : "OFF"}
        </button>
        <label className="slider">
          <HelpTip term="sensitivity">SENSITIVITY</HelpTip>
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={sensitivity}
            disabled={!autoWaterfall}
            onChange={(ev) => setSensitivity(Number.parseInt(ev.target.value, 10))}
          />
        </label>
        <label>
          <HelpTip term="display_range">DISPLAY dBµV MIN</HelpTip>
          <input
            type="number"
            value={autoWaterfall ? (computedRange ? computedRange.min : dbMin) : dbMin}
            disabled={autoWaterfall}
            onChange={(ev) => setDbMin(Number(ev.target.value))}
          />
        </label>
        <label>
          DISPLAY dBµV MAX
          <input
            type="number"
            value={autoWaterfall ? (computedRange ? computedRange.max : dbMax) : dbMax}
            disabled={autoWaterfall}
            onChange={(ev) => setDbMax(Number(ev.target.value))}
          />
        </label>
        <label>
          PRESET
          <select
            value=""
            disabled={presets.length === 0}
            onChange={(ev) => {
              const preset = presets.find((p) => String(p.id) === ev.target.value);
              if (preset) applyPreset(preset);
            }}
          >
            <option value="" disabled>
              {presets.length ? "load preset…" : "no presets yet"}
            </option>
            {presets.map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.name} (
                {(Number(preset.params.start_hz) / 1e6).toFixed(2)}–
                {(Number(preset.params.stop_hz) / 1e6).toFixed(2)})
              </option>
            ))}
          </select>
        </label>
        <label>
          SAVE AS
          <input
            placeholder="preset name"
            value={presetName}
            disabled={!controllable}
            onChange={(ev) => setPresetName(ev.target.value)}
          />
        </label>
        <button disabled={!controllable || running} onClick={savePreset}>
          SAVE PRESET
        </button>
        {presets.length > 0 ? (
          <button
            className="danger"
            disabled={!controllable}
            onClick={async () => {
              const last = presets.at(-1)!;
              await api.presetDelete(last.id).catch(() => undefined);
              setPresets(await api.presets());
            }}
          >
            DELETE LAST
          </button>
        ) : null}
      </div>
      <span className="metric">
        {lastPeak ? `peak: ${lastPeak}` : "no frames yet — start sweeping"}
        {cadence.spRow !== null
          ? ` · row ≈ ${
              cadence.spRow < 1
                ? cadence.spRow.toFixed(2)
                : cadence.spRow < 10
                  ? cadence.spRow.toFixed(1)
                  : cadence.spRow.toFixed(0)
            } s · ${cadence.rows} rows`
          : ""}
      </span>
    </div>
  );
}

function ErrorLine({ text }: { text: string }) {
  return (
    <div className="banner">
      <span>⚠ {text}</span>
    </div>
  );
}

interface DrawOpts {
  padLeft: number;
  plotW: number;
  ticks: { hz: number; label: string }[];
}

function drawSpectrum(
  ctx: CanvasRenderingContext2D,
  cssW: number,
  frame: SpectrumFrame,
  trace: ArrayLike<number | null>,
  dbMin: number,
  dbMax: number,
  opts: DrawOpts & {
    hover: Hover | null;
    showPeak: boolean;
    tuneHz: number | null;
    thresholdDbuv: number | null;
  },
) {
  const { padLeft, plotW, ticks } = opts;
  const height = TRACE_H;
  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cssW, height);

  const span = Math.max(dbMax - dbMin, 1);
  const plotH = height - 8 - 6;
  const toY = (level: number) => 8 + (1 - (level - dbMin) / span) * plotH;

  // horizontal grid + level labels in the gutter
  ctx.strokeStyle = "#223041";
  ctx.fillStyle = "#8a94a3";
  ctx.font = "10px ui-monospace, monospace";
  ctx.lineWidth = 1;
  const dbStep = (dbMax - dbMin) / 5;
  for (let i = 0; i <= 5; i++) {
    const db = dbMax - dbStep * i;
    const y = toY(db);
    ctx.beginPath();
    ctx.moveTo(padLeft, y);
    ctx.lineTo(padLeft + plotW, y);
    ctx.stroke();
    ctx.fillText(db.toFixed(0), 6, y + 3);
  }
  // vertical gridlines aligned with the shared axis strip
  ctx.strokeStyle = "#1a2634";
  for (const tick of ticks) {
    const x = padLeft + ((tick.hz - frame.startHz) / Math.max(frame.stepHz * (frame.points - 1), 1)) * plotW;
    if (x < padLeft || x > padLeft + plotW) continue;
    ctx.beginPath();
    ctx.moveTo(x, 8);
    ctx.lineTo(x, 8 + plotH);
    ctx.stroke();
  }

  // squelch threshold reference line (display only)
  if (
    opts.thresholdDbuv !== null &&
    opts.thresholdDbuv > dbMin &&
    opts.thresholdDbuv < dbMax
  ) {
    const y = toY(opts.thresholdDbuv);
    const top = topPeak(frame.levels, frame.startHz, frame.stepHz);
    const exceeded = top !== null && top.level > opts.thresholdDbuv;
    ctx.strokeStyle = exceeded ? "#f87171" : "rgba(248, 113, 113, 0.5)";
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(padLeft, y);
    ctx.lineTo(padLeft + plotW, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = exceeded ? "#f87171" : "rgba(248, 113, 113, 0.7)";
    ctx.font = "8px ui-monospace, monospace";
    ctx.fillText("SQUELCH", padLeft + plotW - 46, y - 3);
    ctx.font = "10px ui-monospace, monospace";
  }

  // trace + peak-hold
  drawLine(ctx, trace, frame, padLeft, plotW, toY, "#4fd1c5", 1.5);
  if (opts.showPeak && frame.peak) {
    drawLine(ctx, frame.peak, frame, padLeft, plotW, toY, "#63b3ed", 1);
  }

  // tune marker
  drawTuneLine(
    ctx,
    opts.tuneHz,
    frame.startHz,
    Math.max(frame.stepHz * (frame.points - 1), 1),
    padLeft,
    plotW,
    8 + plotH,
  );

  // peak marker
  const top = topPeak(frame.levels, frame.startHz, frame.stepHz);
  if (top) {
    const x = padLeft + ((top.hz - frame.startHz) / (Math.max(frame.stepHz * (frame.points - 1), 1) || 1)) * plotW;
    const y = toY(top.level);
    ctx.strokeStyle = "#f6ad55";
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x, 8);
    ctx.stroke();
    ctx.fillStyle = "#f6ad55";
    ctx.fillText(
      `${(top.hz / 1e6).toFixed(4)} ${top.level.toFixed(1)}`,
      Math.min(x + 4, padLeft + plotW - 90),
      Math.max(y - 4, 12),
    );
  }

  // crosshair
  const hover = opts.hover;
  if (hover && hover.x >= padLeft && hover.x <= padLeft + plotW) {
    const freqSpan = Math.max(frame.stepHz * (frame.points - 1), 1);
    const freq = frame.startHz + ((hover.x - padLeft) / plotW) * freqSpan;
    const bin = Math.min(
      Math.max(Math.round(((hover.x - padLeft) / plotW) * (frame.points - 1)), 0),
      frame.points - 1,
    );
    const level = trace[bin];
    ctx.strokeStyle = "rgba(203, 213, 224, 0.5)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(hover.x, 8);
    ctx.lineTo(hover.x, 8 + plotH);
    ctx.stroke();
    if (level !== null && Number.isFinite(level)) {
      const y = toY(level);
      ctx.beginPath();
      ctx.moveTo(padLeft, y);
      ctx.lineTo(padLeft + plotW, y);
      ctx.stroke();
      ctx.fillStyle = "#4fd1c5";
      ctx.beginPath();
      ctx.arc(hover.x, y, 2.5, 0, Math.PI * 2);
      ctx.fill();
    }
    drawChip(
      ctx,
      hover.x,
      hover.y,
      `${fmtFreqMhz(freq, frame.stepHz)} MHz · ${
        level !== null && Number.isFinite(level) ? `${level.toFixed(1)} dBµV` : "no level"
      } · ${fmtClock(frame.ts)}`,
      cssW,
      height,
    );
  }
}

function drawTuneLine(
  ctx: CanvasRenderingContext2D,
  tuneHz: number | null,
  startHz: number,
  spanHz: number,
  padLeft: number,
  plotW: number,
  plotBottom: number,
): void {
  if (tuneHz === null || tuneHz < startHz || tuneHz > startHz + spanHz) return;
  const x = padLeft + ((tuneHz - startHz) / spanHz) * plotW;
  ctx.strokeStyle = "rgba(246, 224, 94, 0.85)";
  ctx.setLineDash([4, 3]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x, 0);
  ctx.lineTo(x, plotBottom);
  ctx.stroke();
  ctx.setLineDash([]);
}

function drawLine(
  ctx: CanvasRenderingContext2D,
  levels: ArrayLike<number | null>,
  frame: SpectrumFrame,
  padLeft: number,
  plotWidth: number,
  toY: (level: number) => number,
  color: string,
  lineWidth: number,
) {
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.beginPath();
  let started = false;
  for (let i = 0; i < levels.length; i++) {
    const level = levels[i];
    if (level === null || Number.isNaN(level)) {
      started = false;
      continue;
    }
    const x = padLeft + (i / Math.max(frame.points - 1, 1)) * plotWidth;
    const y = toY(level);
    if (!started) {
      ctx.moveTo(x, y);
      started = true;
    } else {
      ctx.lineTo(x, y);
    }
  }
  ctx.stroke();
}

function drawWaterfall(
  ctx: CanvasRenderingContext2D,
  cssW: number,
  cssH: number,
  buf: WaterfallBuffer,
  renderer: WaterfallRenderer,
  opts: DrawOpts & {
    density: number;
    min: number;
    max: number;
    palette: PaletteName;
    hover: Hover | null;
    tuneHz: number | null;
    startHz: number;
    spanHz: number;
  },
) {
  const { padLeft, plotW, density, ticks } = opts;
  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cssW, cssH);
  const rows = buf.all();
  const rowsVisible = Math.min(rows.length, Math.floor(cssH / density));
  const plotH = rowsVisible * density;
  const smooth = rowsVisible > 0 && (rows[rows.length - 1].levels.length || 0) > 900;

  renderer.blit(ctx, padLeft, 0, plotW, rowsVisible, density, smooth);

  // vertical gridlines, shared with the trace above
  ctx.fillStyle = "rgba(180, 200, 220, 0.08)";
  for (const tick of ticks) {
    const x = padLeft + ((tick.hz - opts.startHz) / opts.spanHz) * plotW;
    if (x < padLeft || x > padLeft + plotW) continue;
    ctx.fillRect(x, 0, 1, plotH);
  }

  drawTuneLine(ctx, opts.tuneHz, opts.startHz, opts.spanHz, padLeft, plotW, plotH);

  // time labels in the gutter
  const spRow = buf.secondsPerRow();
  if (rowsVisible >= 2 && spRow !== null && rows.length >= 2) {
    const interval = timeInterval(rowsVisible * spRow);
    let nextTs = rows[rows.length - 1].ts;
    ctx.font = "10px ui-monospace, monospace";
    for (let k = 0; k < rowsVisible; k++) {
      const row = rows[rows.length - 1 - k];
      if (row.ts <= nextTs) {
        const y = k * density + density / 2;
        if (y >= 8 && y <= cssH - 6) {
          ctx.fillStyle = "#8a94a3";
          ctx.fillText(fmtClock(row.ts), 6, y + 3);
          ctx.fillStyle = "#223041";
          ctx.fillRect(padLeft - 4, y, 4, 1);
        }
        nextTs -= interval;
      }
    }
  }

  // crosshair
  const hover = opts.hover;
  if (hover && rowsVisible > 0 && hover.x >= padLeft && hover.x <= padLeft + plotW) {
    const k = Math.min(
      Math.max(Math.floor(hover.y / density), 0),
      rowsVisible - 1,
    );
    const row = rows[rows.length - 1 - k];
    const rowY = k * density + density / 2;
    // frequency maps to the row's own band: rows keep the mapping that was
    // live when they were captured
    const [start, step] = row.band.split("/").map(Number);
    const rowSpan = Math.max(step * (row.levels.length - 1), 1);
    const freq = start + ((hover.x - padLeft) / plotW) * rowSpan;
    const col = renderer.colAt(hover.x, padLeft, plotW);
    const level = row.bandChange
      ? Number.NaN
      : row.levels[renderer.binAt(col, row.levels.length)];
    ctx.strokeStyle = "rgba(203, 213, 224, 0.5)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(hover.x, 0);
    ctx.lineTo(hover.x, plotH);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(padLeft, rowY);
    ctx.lineTo(padLeft + plotW, rowY);
    ctx.stroke();
    const age = Date.now() / 1000 - row.ts;
    const ageText = age < 1.5 ? "now" : age < 120 ? `−${Math.round(age)} s` : `−${Math.round(age / 60)} min`;
    drawChip(
      ctx,
      hover.x,
      hover.y,
      row.bandChange
        ? `band change · ${fmtClock(row.ts)}`
        : `${fmtFreqMhz(freq, step || 1)} MHz · ${
            Number.isFinite(level) ? `${level.toFixed(1)} dBµV` : "no level"
          } · ${fmtClock(row.ts)} (${ageText})`,
      cssW,
      cssH,
    );
  }
}

function drawAxis(
  ctx: CanvasRenderingContext2D,
  cssW: number,
  frame: SpectrumFrame,
  opts: DrawOpts,
) {
  const { padLeft, plotW, ticks } = opts;
  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cssW, AXIS_H);
  ctx.fillStyle = "#223041";
  ctx.fillRect(0, 0, cssW, 1);
  ctx.font = "10px ui-monospace, monospace";
  for (const tick of ticks) {
    const x = padLeft + ((tick.hz - frame.startHz) / Math.max(frame.stepHz * (frame.points - 1), 1)) * plotW;
    if (x < padLeft - 1 || x > padLeft + plotW) continue;
    ctx.fillStyle = "#3a4a5e";
    ctx.fillRect(Math.round(x), 1, 1, 6);
    if (x >= padLeft + 12 && x <= padLeft + plotW - 12) {
      ctx.fillStyle = "#8a94a3";
      ctx.textAlign = "center";
      ctx.fillText(tick.label, x, 19);
      ctx.textAlign = "left";
    }
  }
  ctx.fillStyle = "#5a6678";
  ctx.textAlign = "right";
  ctx.fillText("MHz", cssW - 6, 19);
  ctx.textAlign = "left";
}

function drawChip(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  text: string,
  cssW: number,
  cssH: number,
) {
  ctx.font = "10px ui-monospace, monospace";
  const w = Math.ceil(ctx.measureText(text).width) + 12;
  const h = 18;
  let cx = x + 10;
  if (cx + w > cssW - 4) cx = x - w - 10;
  cx = Math.min(Math.max(cx, 4), Math.max(cssW - w - 4, 4));
  let cy = y + 12;
  if (cy + h > cssH - 4) cy = y - h - 8;
  cy = Math.min(Math.max(cy, 4), Math.max(cssH - h - 4, 4));
  ctx.fillStyle = "rgba(10, 14, 20, 0.92)";
  if (typeof ctx.roundRect === "function") {
    ctx.beginPath();
    ctx.roundRect(cx, cy, w, h, 6);
    ctx.fill();
    ctx.strokeStyle = "#223041";
    ctx.lineWidth = 1;
    ctx.stroke();
  } else {
    ctx.fillRect(cx, cy, w, h);
  }
  ctx.fillStyle = "#d8e0ea";
  ctx.fillText(text, cx + 6, cy + 13);
}
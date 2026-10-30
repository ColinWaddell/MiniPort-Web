import { useCallback, useEffect, useRef, useState } from "react";
import { api, clientId } from "../lib/api";
import {
  decodeSpectrumFrame,
  levelToColor,
  type SpectrumFrame,
  topPeak,
} from "../lib/spectrum";
import type { Preset, SpectrumStatus } from "../lib/types";

interface Props {
  connected: boolean;
  ownsControl: boolean;
  scanning: boolean;
  status: SpectrumStatus;
}

const DB_MIN_DEFAULT = -120;
const DB_MAX_DEFAULT = 20;

export function SpectrumPanel({
  connected,
  ownsControl,
  scanning,
  status,
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

  const spectrumCanvas = useRef<HTMLCanvasElement | null>(null);
  const waterfallCanvas = useRef<HTMLCanvasElement | null>(null);
  const frameRef = useRef<SpectrumFrame | null>(null);
  const scaleRef = useRef({ min: DB_MIN_DEFAULT, max: DB_MAX_DEFAULT });
  const running = status.running;
  const controllable = connected && ownsControl && !scanning;
  const canStop = connected && ownsControl && running;

  useEffect(() => {
    scaleRef.current = { min: dbMin, max: dbMax };
  }, [dbMin, dbMax]);

  useEffect(() => {
    api
      .presets()
      .then(setPresets)
      .catch(() => setPresets([]));
  }, [running]);

  // spectrum websocket: binary frames straight onto the canvases
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
        frameRef.current = frame;
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
  const draw = window.setInterval(drawCanvases, 100);
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
      window.clearInterval(draw);
      socket?.close();
    };
  }, []);

  const drawCanvases = useCallback(() => {
    const frame = frameRef.current;
    const spectrum = spectrumCanvas.current;
    const waterfall = waterfallCanvas.current;
    if (!frame || !spectrum || !waterfall) return;
    const { min, max } = scaleRef.current;
    drawSpectrum(spectrum, frame, min, max);
    appendWaterfall(waterfall, frame, min, max);
  }, []);

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
    setStartMhz((preset.start_hz / 1e6).toFixed(4));
    setStopMhz((preset.stop_hz / 1e6).toFixed(4));
    setStepKhz((preset.step_hz / 1e3).toFixed(0));
  };

  return (
    <div className="panel-body">
      <h2>
        LIVE SPECTRUM / WATERFALL{" "}
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
          resets
        </button>
        <a className="link" href="api/spectrum/export.csv" download>
          export CSV
        </a>
      </div>
      {apiError ? <ErrorLine text={apiError} /> : null}
      <canvas ref={spectrumCanvas} width={960} height={210} className="spectrum-canvas" />
      <canvas ref={waterfallCanvas} width={960} height={160} className="spectrum-canvas waterfall" />
      <div className="field-row">
        <label>
          DISPLAY dBµV MIN
          <input type="number" value={dbMin} onChange={(ev) => setDbMin(Number(ev.target.value))} />
        </label>
        <label>
          DISPLAY dBµV MAX
          <input type="number" value={dbMax} onChange={(ev) => setDbMax(Number(ev.target.value))} />
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
                {preset.name} ({(preset.start_hz / 1e6).toFixed(2)}–{(preset.stop_hz / 1e6).toFixed(2)})
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

function drawSpectrum(
  canvas: HTMLCanvasElement,
  frame: SpectrumFrame,
  dbMin: number,
  dbMax: number,
) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const { width, height } = canvas;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, width, height);

  const padLeft = 44;
  const padBottom = 18;
  const plotWidth = width - padLeft - 8;
  const plotHeight = height - padBottom - 8;
  const span = Math.max(dbMax - dbMin, 1);
  const toY = (level: number) => 8 + (1 - (level - dbMin) / span) * plotHeight;

  // grid + labels
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
    ctx.lineTo(width - 4, y);
    ctx.stroke();
    ctx.fillText(db.toFixed(0), 4, y + 3);
  }
  const freqSpan = frame.stepHz * (frame.points - 1);
  for (let i = 0; i <= 4; i++) {
    const t = i / 4;
    const x = padLeft + t * plotWidth;
    const hz = frame.startHz + t * freqSpan;
    ctx.fillText(`${(hz / 1e6).toFixed(2)}`, x - 12, height - 4);
  }

  // trace + peak-hold
  drawLine(ctx, frame.levels, frame, padLeft, plotWidth, toY, "#4fd1c5", 1.5);
  if (frame.peak) {
    drawLine(ctx, frame.peak, frame, padLeft, plotWidth, toY, "#63b3ed", 1);
  }
  // peak marker
  const top = topPeak(frame.levels, frame.startHz, frame.stepHz);
  if (top) {
    const x = padLeft + ((top.hz - frame.startHz) / (freqSpan || 1)) * plotWidth;
    const y = toY(top.level);
    ctx.strokeStyle = "#f6ad55";
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x, 8);
    ctx.stroke();
    ctx.fillStyle = "#f6ad55";
    ctx.fillText(
      `${(top.hz / 1e6).toFixed(4)} ${top.level.toFixed(1)}`,
      Math.min(x + 4, width - 90),
      Math.max(y - 4, 12),
    );
  }
}

function drawLine(
  ctx: CanvasRenderingContext2D,
  levels: (number | null)[],
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
  levels.forEach((level, index) => {
    if (level === null) {
      started = false;
      return;
    }
    const x =
      padLeft + (index / Math.max(frame.points - 1, 1)) * plotWidth;
    const y = toY(level);
    if (!started) {
      ctx.moveTo(x, y);
      started = true;
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.stroke();
}

function appendWaterfall(
  canvas: HTMLCanvasElement,
  frame: SpectrumFrame,
  dbMin: number,
  dbMax: number,
) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const { width, height } = canvas;
  const rowHeight = 2;
  // shift existing rows down, new frame appears at the top
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
    const bin = Math.min(
      Math.floor((x / width) * frame.points),
      frame.points - 1,
    );
    const [r, g, b] = levelToColor(frame.levels[bin], dbMin, dbMax);
    ctx.fillStyle = `rgb(${r},${g},${b})`;
    ctx.fillRect(x, 0, 1, rowHeight);
  }
}
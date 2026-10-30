import { useEffect, useState } from "react";
import type { ReceiverState, ScanResponse } from "../lib/types";
import { api } from "../lib/api";

interface Props {
  state: ReceiverState;
  controllable: boolean;
  scanning: boolean;
}

export function ScanPanel({ state, controllable, scanning }: Props) {
  const [start, setStart] = useState("");
  const [stop, setStop] = useState("");
  const [step, setStep] = useState("25");
  const [result, setResult] = useState<ScanResponse | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (running || start) return;
    setStart(((state.freq_hz - 250_000) / 1_000_000).toFixed(4));
    setStop(((state.freq_hz + 250_000) / 1_000_000).toFixed(4));
  }, [state.freq_hz, running, start, stop]);

  const run = async () => {
    const startMHz = Number.parseFloat(start);
    const stopMHz = Number.parseFloat(stop);
    const stepKHz = Number.parseFloat(step);
    if (![startMHz, stopMHz, stepKHz].every(Number.isFinite)) return;
    setRunning(true);
    try {
      const response = await api.scan(
        Math.round(startMHz * 1e6),
        Math.round(stopMHz * 1e6),
        Math.round(stepKHz * 1e3),
      );
      setResult(response);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="panel-body">
      <h2>FREQUENCY SCAN (single sweep)</h2>
      <div className="field-row">
        <label>
          START MHz
          <input
            aria-label="scan start MHz"
            value={start}
            disabled={!controllable || running}
            onChange={(ev) => setStart(ev.target.value)}
            inputMode="decimal"
          />
        </label>
        <label>
          STOP MHz
          <input
            aria-label="scan stop MHz"
            value={stop}
            disabled={!controllable || running}
            onChange={(ev) => setStop(ev.target.value)}
            inputMode="decimal"
          />
        </label>
        <label>
          STEP kHz
          <input
            aria-label="scan step kHz"
            value={step}
            disabled={!controllable || running}
            onChange={(ev) => setStep(ev.target.value)}
            inputMode="decimal"
          />
        </label>
        <button
          className="run"
          disabled={!controllable || running || scanning}
          onClick={run}
        >
          {running || scanning ? "SCANNING…" : "RUN SWEEP"}
        </button>
      </div>
      <ResultTrace result={result} />
    </div>
  );
}

function ResultTrace({ result }: { result: ScanResponse | null }) {
  const canvasRef = { current: null as HTMLCanvasElement | null };
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !result || result.frequencies_hz.length === 0) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const { width, height } = canvas;
    ctx.clearRect(0, 0, width, height);
    const levels = result.levels.map((lv) => lv ?? -130);
    const min = Math.min(...levels) - 5;
    const max = Math.max(...levels) + 5;
    const span = Math.max(max - min, 1);
    ctx.strokeStyle = "#4fd1c5";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    levels.forEach((level, index) => {
      const x = (index / (levels.length - 1)) * (width - 8) + 4;
      const y = height - 4 - ((level - min) / span) * (height - 12);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }, [result]);

  if (!result) {
    return <div className="scan-empty">No sweep data yet.</div>;
  }
  return (
    <div className="scan-result">
      <canvas
        ref={(el) => {
          canvasRef.current = el;
        }}
        width={640}
        height={120}
      />
      <span className="metric">
        {result.frequencies_hz.length} steps ·{" "}
        {(result.frequencies_hz[0] / 1e6).toFixed(3)}–
        {(result.frequencies_hz.at(-1)! / 1e6).toFixed(3)} MHz
        {result.restore_errors.length ? ` · restore issues: ${result.restore_errors.length}` : ""}
      </span>
    </div>
  );
}
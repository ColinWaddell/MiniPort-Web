import { useEffect, useState } from "react";
import type { ReceiverState } from "../lib/types";

interface Props {
  state: ReceiverState;
  controllable: boolean;
  minHz: number;
  maxHz: number;
  onSet: (hz: number) => void;
  onOperation: (mode: string) => void;
}

const STEPS: [string, number][] = [
  ["−100k", -100_000],
  ["−10k", -10_000],
  ["−1k", -1_000],
  ["+1k", 1_000],
  ["+10k", 10_000],
  ["+100k", 100_000],
];

function splitHz(hz: number): string {
  const mhz = hz / 1_000_000;
  const [whole, frac = ""] = mhz.toFixed(6).split(".");
  return `${whole}.${frac.slice(0, 3)} ${frac.slice(3, 6)}`.trim();
}

export function FrequencyDisplay({
  state,
  controllable,
  minHz,
  maxHz,
  onSet,
  onOperation,
}: Props) {
  const [entry, setEntry] = useState<string>("");
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (!editing) setEntry(splitHz(state.freq_hz).replace(" ", "."));
    // keep the entry field in sync unless the user is typing
  }, [state.freq_hz, editing]);

  const commit = () => {
    const mhz = Number.parseFloat(entry);
    if (!Number.isFinite(mhz)) return;
    const hz = Math.round(mhz * 1_000_000);
    if (hz < minHz || hz > maxHz) return;
    setEditing(false);
    onSet(hz);
  };

  return (
    <div className="freq">
      <div className="freq-line">
        <span className="freq-label">FREQUENCY MHz</span>
        <input
          className="freq-input"
          value={editing ? entry : splitHz(state.freq_hz)}
          inputMode="decimal"
          disabled={!controllable}
          aria-label="frequency in MHz"
          onFocus={() => setEditing(true)}
          onChange={(ev) => setEntry(ev.target.value)}
          onKeyDown={(ev) => {
            if (ev.key === "Enter") commit();
            if (ev.key === "Escape") setEditing(false);
          }}
        />
      </div>
      <div className="freq-steps">
        {STEPS.map(([label, delta]) => (
          <button
            key={label}
            disabled={!controllable}
            onClick={() => onSet(state.freq_hz + delta)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="freq-meta">
        <label>
          OPERATION{" "}
          <select
            value={state.freq_mode}
            disabled={!controllable}
            onChange={(ev) => onOperation(ev.target.value)}
          >
            <option value="CW">CW (tune)</option>
            <option value="FIXed">FIXed</option>
            <option value="SWEep">SCAN</option>
          </select>
        </label>
        <span className="sweep-flag">{state.sweeping ? "SCAN ACTIVE" : ""}</span>
      </div>
    </div>
  );
}
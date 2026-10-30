import type { Capabilities, ReceiverState } from "../lib/types";

interface Props {
  state: ReceiverState;
  capabilities: Capabilities | null;
  controllable: boolean;
  onGain: (db: number) => void;
  onGainMode: (mode: string) => void;
  onAttenuator: (enabled: boolean | null, auto: boolean | null) => void;
}

const GAIN_READBACK: Record<string, string> = { FIX: "FIXed", F: "FIXed" };

export function GainPanel({
  state,
  capabilities,
  controllable,
  onGain,
  onGainMode,
  onAttenuator,
}: Props) {
  const modes = capabilities?.gain_modes ?? ["FIXed", "MGC", "AUTO", "AGC"];
  const mode = GAIN_READBACK[state.gain_mode] ?? state.gain_mode;
  const manual = mode !== "AUTO" && mode !== "AGC";
  const [minGain, maxGain] = [
    capabilities?.gain_min_dbuv ?? -30,
    capabilities?.gain_max_dbuv ?? 110,
  ];
  return (
    <div className="panel-body">
      <h2>GAIN</h2>
      <div className="field-row">
        <label>
          MODE
          <select
            value={mode}
            disabled={!controllable}
            onChange={(ev) => onGainMode(ev.target.value)}
          >
            {modes.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <span className="metric">{state.gain_db.toFixed(0)} dBµV</span>
      </div>
      <label className="slider">
        MGC
        <input
          type="range"
          min={minGain}
          max={maxGain}
          step={1}
          value={state.gain_db}
          disabled={!controllable || !manual}
          onChange={(ev) => onGain(Number.parseInt(ev.target.value, 10))}
        />
      </label>
      <h2>RF ATTENUATOR</h2>
      <div className="field-row">
        <button
          className={`switch ${state.att_auto ? "on" : ""}`}
          disabled={!controllable}
          onClick={() => onAttenuator(null, !state.att_auto)}
          aria-pressed={state.att_auto}
        >
          AUTO {state.att_auto ? "ON" : "OFF"}
        </button>
        <button
          className={`switch ${state.att_on ? "on" : ""}`}
          disabled={!controllable || state.att_auto}
          onClick={() => onAttenuator(!state.att_on, false)}
          aria-pressed={state.att_on}
        >
          ON {state.att_on ? "IN" : "OUT"}
        </button>
      </div>
    </div>
  );
}
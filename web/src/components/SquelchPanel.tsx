import type { ReceiverState } from "../lib/types";

interface Props {
  state: ReceiverState;
  controllable: boolean;
  onSquelch: (enabled: boolean) => void;
  onThreshold: (dbuv: number) => void;
}

export function SquelchPanel({
  state,
  controllable,
  onSquelch,
  onThreshold,
}: Props) {
  return (
    <div className="panel-body">
      <h2>SQUELCH</h2>
      <div className="field-row">
        <button
          className={`switch ${state.squelch ? "on" : ""}`}
          disabled={!controllable}
          onClick={() => onSquelch(!state.squelch)}
          aria-pressed={state.squelch}
        >
          {state.squelch ? "ON" : "OFF"}
        </button>
        <span className="metric">{state.squelch_threshold_dbuv.toFixed(0)} dBµV</span>
      </div>
      <label className="slider">
        THRESHOLD
        <input
          type="range"
          min={-150}
          max={20}
          step={1}
          value={state.squelch_threshold_dbuv}
          disabled={!controllable}
          onChange={(ev) => onThreshold(Number.parseInt(ev.target.value, 10))}
          onMouseUp={(ev) => onThreshold(Number.parseInt((ev.target as HTMLInputElement).value, 10))}
        />
      </label>
    </div>
  );
}
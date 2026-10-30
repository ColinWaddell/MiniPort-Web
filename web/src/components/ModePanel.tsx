import type { Capabilities, ReceiverState } from "../lib/types";

interface Props {
  state: ReceiverState;
  capabilities: Capabilities | null;
  controllable: boolean;
  onDemod: (mode: string) => void;
  onBandwidth: (hz: number) => void;
  onAfc: (enabled: boolean) => void;
}

const BANDWIDTH_PRESETS = [150, 300, 600, 1_500, 2_100, 6_000, 9_000, 15_000, 30_000, 50_000, 120_000, 150_000];

export function ModePanel({
  state,
  capabilities,
  controllable,
  onDemod,
  onBandwidth,
  onAfc,
}: Props) {
  const demods = capabilities?.demodulation_modes ?? ["AM", "FM", "CW", "USB", "LSB"];
  const maxBand = capabilities?.bandwidth_max_hz ?? 150_000;
  const presets = BANDWIDTH_PRESETS.filter((hz) => hz <= maxBand);
  return (
    <div className="panel-body">
      <h2>DEMODOULATOR</h2>
      <div className="field-row">
        <label>
          MODE
          <select
            value={state.demod}
            disabled={!controllable}
            onChange={(ev) => onDemod(ev.target.value)}
          >
            {demods.map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
        </label>
        <button
          className={`switch ${state.afc ? "on" : ""}`}
          disabled={!controllable}
          onClick={() => onAfc(!state.afc)}
          aria-pressed={state.afc}
        >
          AFC {state.afc ? "ON" : "OFF"}
        </button>
      </div>
      <h2>IF BANDWIDTH</h2>
      <div className="field-row">
        <label>
          BANDWIDTH Hz
          <input
            type="number"
            min={capabilities?.bandwidth_min_hz ?? 150}
            max={maxBand}
            step={1}
            value={state.bandwidth_hz}
            disabled={!controllable}
            onChange={(ev) => {
              const hz = Number.parseInt(ev.target.value, 10);
              if (Number.isFinite(hz) && hz > 0) onBandwidth(hz);
            }}
          />
        </label>
        <div className="chips">
          {presets.map((hz) => (
            <button
              key={hz}
              className={`chip ${state.bandwidth_hz === hz ? "active" : ""}`}
              disabled={!controllable}
              onClick={() => onBandwidth(hz)}
            >
              {hz >= 1000 ? `${hz / 1000}k` : hz}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
import type { ReceiverState } from "../lib/types";
import { HelpTip } from "./help";

interface Props {
  state: ReceiverState;
  controllable: boolean;
  onVolume: (value: number) => void;
}

export function VolumePanel({ state, controllable, onVolume }: Props) {
  return (
    <div className="panel-body">
      <h2><HelpTip term="af_volume">AF VOLUME (receiver loudspeaker)</HelpTip></h2>
      <label className="slider">
        VOLUME
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={state.volume}
          disabled={!controllable}
          onMouseUp={(ev) =>
            onVolume(Number.parseFloat((ev.target as HTMLInputElement).value))
          }
          onChange={(ev) =>
            onVolume(Number.parseFloat((ev.target as HTMLInputElement).value))
          }
        />
      </label>
      <span className="metric">{Math.round(state.volume * 100)} %</span>
    </div>
  );
}
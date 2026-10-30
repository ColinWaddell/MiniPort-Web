import { HelpTip } from "./help";
interface Props {
  levelDbuv: number;
  squelch: boolean;
}

// dBµV range mapped onto the meter; the EB200 noise floor sits around -20.
const MIN = -120;
const MAX = 10;

export function LevelMeter({ levelDbuv, squelch }: Props) {
  const clamped = Math.min(Math.max(levelDbuv, MIN), MAX);
  const fraction = (clamped - MIN) / (MAX - MIN);
  return (
    <div className="meter">
      <div className="meter-head">
        <span className="meter-label"><HelpTip term="level">SIGNAL LEVEL</HelpTip></span>
        <span className="meter-value">{levelDbuv.toFixed(1)} dBµV</span>
        {squelch ? <span className="meter-squelch">SQUELCH ON</span> : null}
      </div>
      <div className="meter-bar">
        <div
          className="meter-fill"
          style={{ width: `${(fraction * 100).toFixed(1)}%` }}
        />
        {[...Array(7)].map((_, index) => (
          <span
            key={index}
            className="meter-tick"
            style={{ left: `${(index / 6) * 100}%` }}
            data-label={`${MIN + ((MAX - MIN) / 6) * index}`}
          />
        ))}
      </div>
    </div>
  );
}
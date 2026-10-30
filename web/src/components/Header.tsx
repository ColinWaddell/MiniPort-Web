import type { DeviceInfo } from "../lib/types";
import type { LinkStatus } from "../lib/useReceiver";
import { HelpButton } from "./help";

interface Props {
  device: DeviceInfo | null;
  ws: LinkStatus;
  connected: boolean;
  ownsControl: boolean;
  controlNote: string | null;
  onTakeControl: () => void;
  onHelp: () => void;
}

export function Header({
  device,
  ws,
  connected,
  ownsControl,
  controlNote,
  onTakeControl,
  onHelp,
}: Props) {
  const linkTone = connected ? "ok" : ws === "down" ? "bad" : "warn";
  const linkText = connected
    ? "receiver online"
    : ws === "down"
      ? "server unreachable"
      : "reconnecting…";
  return (
    <header className="header">
      <div className="ident">
        <span className="ident-model">{device?.model ?? "EB200"}</span>
        <span className="ident-detail">
          {device ? `${device.firmware} · ${device.host}:${device.port}` : "…"}
        </span>
        {device ? (
          <span className="ident-detail">options: {device.options.join(", ") || "none"}</span>
        ) : null}
      </div>
      <div className="status-clusters">
        <span className={`badge badge-${linkTone}`}>
          <span className="led" /> {linkText}
        </span>
        {ownsControl ? (
          <span className="badge badge-ctrl">controls: yours</span>
        ) : (
          <button className="badge badge-idle" onClick={onTakeControl}>
            {controlNote ? "take control (busy)" : "take control"}
          </button>
        )}
        <HelpButton onOpen={onHelp} />
      </div>
    </header>
  );
}
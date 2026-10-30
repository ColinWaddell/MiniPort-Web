import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { AudioStreamer, type StreamerStatus } from "../lib/audio-streamer";
import type { AudioStatus, Recording } from "../lib/types";

interface Props {
  connected: boolean;
  ownsControl: boolean;
  status: AudioStatus;
}

export function AudioPanel({ connected, ownsControl, status }: Props) {
  const streamerRef = useRef<AudioStreamer | null>(null);
  const [streamer, setStreamer] = useState<StreamerStatus>({
    ws: "down",
    rateHz: 0,
    playing: false,
    packets: 0,
    lost: 0,
  });
  const [listening, setListening] = useState(false);
  const [volume, setVolume] = useState(0.8);
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const streamerInstance = new AudioStreamer();
    streamerInstance.onStatus = (next) => setStreamer(next);
    streamerRef.current = streamerInstance;
    return () => streamerInstance.dispose();
  }, []);

  useEffect(() => {
    streamerRef.current?.ingestServerStatus(status);
  }, [status]);

  const refreshRecordings = () => {
    api
      .recordings()
      .then((data) => setRecordings(data))
      .catch(() => setRecordings([]));
  };
  useEffect(() => {
    refreshRecordings();
    const timer = window.setInterval(refreshRecordings, 5000);
    return () => window.clearInterval(timer);
  }, []);

  const toggleListen = async () => {
    const streamerInstance = streamerRef.current!;
    if (!listening) {
      try {
        await streamerInstance.start();
        await api.audioStart();
        setListening(true);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    } else {
      try {
        if (status.recording) await api.record("stop");
        await api.audioStop();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
      streamerInstance.stop();
      setListening(false);
      refreshRecordings();
    }
  };

  const toggleRecording = async () => {
    try {
      if (!status.recording) {
        await api.record("start");
      } else {
        await api.record("stop");
        refreshRecordings();
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const controllable = connected && ownsControl;
  const lostPct =
    streamer.packets > 0
      ? ((streamer.lost / streamer.packets) * 100).toFixed(1)
      : "0.0";

  return (
    <div className="panel-body">
      <h2>
        REMOTE AUDIO {status.running ? <span className="scan-flag">· STREAMING</span> : null}
        {status.recording ? <span className="rec-flag"> · REC</span> : null}
      </h2>
      <div className="field-row">
        <button
          className={`switch ${listening ? "on" : ""}`}
          disabled={!controllable}
          onClick={toggleListen}
          aria-pressed={listening}
        >
          {listening ? "STOP LISTENING" : "LISTEN"}
        </button>
        <button
          className={`switch ${status.recording ? "on rec" : ""}`}
          disabled={!controllable || !status.running}
          onClick={toggleRecording}
        >
          {status.recording ? "STOP REC" : "RECORD"}
        </button>
        {listening ? (
          <span className="metric">
            rate {streamer.rateHz} Hz · {streamer.packets} pkt ·{" "}
            {lostPct}% lost
          </span>
        ) : (
          <span className="metric">
            {status.rate_hz && status.running
              ? `rate ${status.rate_hz} Hz`
              : "not streaming"}
          </span>
        )}
      </div>
      <label className="slider">
        LISTEN VOLUME (browser)
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={volume}
          disabled={!listening}
          onChange={(ev) => {
            const value = Number.parseFloat(ev.target.value);
            setVolume(value);
            streamerRef.current?.setVolume(value);
          }}
        />
      </label>
      {listening && streamer.packets === 0 ? (
        <div className="banner">
          <span>
            connected but no audio packets — the host firewall needs a UDP
            allow rule for the receiver (docs/protocol-notes.md)
          </span>
        </div>
      ) : null}
      {error ? (
        <div className="banner">
          <span>⚠ {error}</span>
        </div>
      ) : null}
      <h2>RECORDINGS</h2>
      {recordings.length === 0 ? (
        <div className="scan-empty">No recordings yet.</div>
      ) : (
        <ul className="recordings">
          {recordings.map((recording) => (
            <li key={recording.name}>
              <a href={`api/audio/recordings/${recording.name}`} download>
                {recording.name}
              </a>
              <span className="metric">
                {" "}
                ({Math.round(recording.bytes / 1024)} kB
                {recording.duration_s
                  ? ` · ${recording.duration_s.toFixed(1)} s`
                  : ""}
                {recording.trigger && recording.trigger !== "manual"
                  ? ` · ${recording.trigger}`
                  : ""}
                )
              </span>
              <button
                className="link"
                disabled={!controllable}
                onClick={async () => {
                  await api.deleteRecording(recording.name).catch(() => undefined);
                  refreshRecordings();
                }}
              >
                delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
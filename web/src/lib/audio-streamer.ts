import { clientId } from "./api";
import type { AudioStatus } from "./types";

/// Browser playback for the /ws/audio PCM stream.
///
/// Chunks (int16 LE) are scheduled on the AudioContext clock with a small
/// jitter buffer; underruns re-anchor the playhead (graceful recovery), a
/// persistently fast queue trims scheduled content. The sample rate comes
/// from the server's wire-rate measurement (the receiver's documented
/// rates disagree with observations; see docs/capability-report.md).

const DEFAULT_RATE = 13373;
const TARGET_LATENCY_S = 0.25;
const MAX_LATENCY_S = 0.6;
const MIN_LEAD_S = 0.05;

export interface StreamerStatus {
  ws: "connecting" | "live" | "down";
  rateHz: number;
  playing: boolean;
  packets: number;
  lost: number;
}

export class AudioStreamer {
  private context: AudioContext | null = null;
  private gain: GainNode | null = null;
  private socket: WebSocket | null = null;
  private scheduledUntil = 0;
  private rateHz = DEFAULT_RATE;
  private closed = false;
  private retry = 0;
  private retryTimer: number | undefined;
  private heartbeat: number | undefined;

  onStatus?: (status: StreamerStatus) => void;
  private status: StreamerStatus = {
    ws: "connecting",
    rateHz: DEFAULT_RATE,
    playing: false,
    packets: 0,
    lost: 0,
  };

  private emit() {
    this.onStatus?.({ ...this.status });
  }

  private patch(patch: Partial<StreamerStatus>) {
    this.status = { ...this.status, ...patch };
    this.emit();
  }

  async start() {
    this.closed = false;
    const AudioContextCtor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    if (!this.context) {
      this.context = new AudioContextCtor();
      this.gain = this.context.createGain();
      this.gain.gain.value = 0.8;
      this.gain.connect(this.context.destination);
    }
    await this.context.resume();
    this.connect();
  }

  stop() {
    this.closed = true;
    if (this.retryTimer !== undefined) window.clearTimeout(this.retryTimer);
    if (this.heartbeat !== undefined) window.clearInterval(this.heartbeat);
    this.socket?.close();
    this.socket = null;
    void this.context?.suspend();
    this.patch({ playing: false, ws: "down" });
  }

  dispose() {
    this.stop();
    void this.context?.close();
    this.context = null;
    this.gain = null;
  }

  setVolume(value: number) {
    if (this.gain) this.gain.gain.value = Math.min(Math.max(value, 0), 1);
  }

  private connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/ws/audio?client_id=${encodeURIComponent(
      clientId(),
    )}`;
    const socket = new WebSocket(url);
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    socket.onopen = () => {
      this.retry = 0;
      this.patch({ ws: "live", playing: true });
      this.heartbeat = window.setInterval(
        () => socket.readyState === 1 && socket.send('{"type":"hb"}'),
        20_000,
      );
    };
    socket.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        const message = JSON.parse(ev.data) as {
          type: string;
          running?: boolean;
          rate?: number;
        };
        if (message.type === "audio-config") {
          this.patch({ playing: Boolean(message.running) });
          if (message.rate && message.rate > 1000) {
            this.rateHz = message.rate;
            this.patch({ rateHz: this.rateHz });
          }
        } else if (message.type === "audio-rate" && message.rate) {
          this.rateHz = message.rate;
          this.patch({ rateHz: this.rateHz });
          this.scheduledUntil = 0; // re-anchor the playhead
        }
        return;
      }
      if (ev.data instanceof ArrayBuffer) this.schedule(ev.data);
    };
    socket.onclose = () => {
      if (this.closed) return;
      this.retry += 1;
      this.patch({ ws: "down", playing: false });
      this.retryTimer = window.setTimeout(
        () => this.connect(),
        Math.min(500 * this.retry, 5000),
      );
    };
  }

  private schedule(chunk: ArrayBuffer) {
    const ctx = this.context;
    const gain = this.gain;
    if (!ctx || !gain || ctx.state !== "running") return;
    const samples = chunk.byteLength / 2;
    if (samples === 0) return;
    const buffer = ctx.createBuffer(1, samples, this.rateHz);
    const channel = buffer.getChannelData(0);
    const view = new DataView(chunk);
    for (let i = 0; i < samples; i++) {
      channel[i] = view.getInt16(i * 2, true) / 32768;
    }
    const now = ctx.currentTime;
    const start = Math.max(this.scheduledUntil, now + MIN_LEAD_S);
    if (start - now > MAX_LATENCY_S) {
      // queue ran long (tab was backgrounded): drop this chunk instead of
      // growing the delay further
      return;
    }
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(gain);
    source.start(start);
    this.scheduledUntil = start + samples / this.rateHz;
    if (this.scheduledUntil > now + TARGET_LATENCY_S * 4) {
      this.scheduledUntil = now + TARGET_LATENCY_S;
    }
  }

  /** Server-provided stream statistics, pushed from state snapshots. */
  ingestServerStatus(audio: AudioStatus) {
    if (audio.rate_hz && audio.rate_hz > 1000) {
      this.rateHz = audio.rate_hz;
    }
    this.patch({
      playing: this.status.playing && Boolean(audio.running),
      packets: audio.packets ?? 0,
      lost: audio.lost ?? 0,
    });
  }
}
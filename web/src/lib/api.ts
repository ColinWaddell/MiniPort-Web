import { ApiError, type Job, type JobRun, type LevelLogStatus, type Preset, type Recording, type ScanResponse, type SweepRow, type TriggerStatus } from "./types";

const CLIENT_ID_KEY = "miniport-client-id";

export function clientId(): string {
  let id = localStorage.getItem(CLIENT_ID_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(CLIENT_ID_KEY, id);
  }
  return id;
}

function headers(): HeadersInit {
  return {
    "X-Eb200-Client": clientId(),
    "Content-Type": "application/json",
  };
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const payload = (await res.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") detail = payload.detail;
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const api = {
  acquireControl: () =>
    post<{ controlled: boolean }>("api/control/acquire", { client_id: clientId() }),
  releaseControl: () =>
    post<{ controlled: boolean }>("api/control/release", { client_id: clientId() }),
  setFrequency: (hz: number) => post("api/frequency", { hz }),
  setDemod: (mode: string) => post("api/demod", { mode }),
  setBandwidth: (hz: number) => post("api/bandwidth", { hz }),
  setAfc: (enabled: boolean) => post("api/afc", { enabled }),
  setAttenuator: (enabled: boolean | null, auto: boolean | null) =>
    post("api/attenuator", { enabled, auto }),
  setGain: (db: number) => post("api/gain", { db }),
  setGainMode: (mode: string) => post("api/gain-mode", { mode }),
  setSquelch: (enabled: boolean) => post("api/squelch", { enabled }),
  setSquelchThreshold: (dbuv: number) => post("api/squelch-threshold", { dbuv }),
  setVolume: (value: number) => post("api/volume", { value }),
  setOperation: (mode: string) => post("api/operation", { mode }),
  scan: (startHz: number, stopHz: number, stepHz: number) =>
    post<ScanResponse>("api/scan", {
      start_hz: startHz,
      stop_hz: stopHz,
      step_hz: stepHz,
      timeout: 60,
    }),
  spectrumStart: (startHz: number, stopHz: number, stepHz: number) =>
    post("api/spectrum/start", {
      start_hz: startHz,
      stop_hz: stopHz,
      step_hz: stepHz,
    }),
  spectrumStop: () => post("api/spectrum/stop", {}),
  spectrumResetPeak: () => post("api/spectrum/reset-peak", {}),
  presets: async () => {
    const res = await fetch("api/spectrum/presets");
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    const data = (await res.json()) as { presets: Preset[] };
    return data.presets;
  },
  presetCreate: (name: string, startHz: number, stopHz: number, stepHz: number) =>
    post<{ preset: Preset }>("api/spectrum/presets", {
      name,
      start_hz: startHz,
      stop_hz: stopHz,
      step_hz: stepHz,
    }),
  presetDelete: async (id: number) => {
    const res = await fetch(`api/spectrum/presets/${id}`, {
      method: "DELETE",
      headers: headers(),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const payload = (await res.json()) as { detail?: unknown };
        if (typeof payload.detail === "string") detail = payload.detail;
      } catch {
        /* keep statusText */
      }
      throw new ApiError(res.status, detail);
    }
    return (await res.json()) as { deleted: boolean };
  },
  audioStart: () => post("api/audio/start", {}),
  audioStop: () => post("api/audio/stop", {}),
  record: (action: "start" | "stop") => post(`api/audio/record/${action}`, {}),
  recordings: async () => {
    const res = await fetch("api/audio/recordings");
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    const data = (await res.json()) as { recordings: Recording[] };
    return data.recordings;
  },
  deleteRecording: async (name: string) => {
    const res = await fetch(`api/audio/recordings/${name}`, {
      method: "DELETE",
      headers: headers(),
    });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return (await res.json()) as { deleted: boolean };
  },
  // phase 6: automation
  frequencyPresets: async () => {
    const res = await fetch("api/presets");
    if (!res.ok) return [];
    const data = (await res.json()) as { presets: Preset[] };
    return data.presets;
  },
  createFrequencyPreset: (name: string, frequency_hz: number, demod: string, bandwidth_hz: number) =>
    post<{ preset: Preset }>("api/presets", {
      name,
      frequency_hz,
      demod,
      bandwidth_hz,
    }),
  applyFrequencyPreset: (id: number) =>
    post<{ applied: boolean }>(`api/presets/${id}/apply`, {}),
  deleteFrequencyPreset: async (id: number) => {
    const res = await fetch(`api/presets/${id}`, { method: "DELETE", headers: headers() });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return (await res.json()) as { deleted: boolean };
  },
  jobs: async () => {
    const res = await fetch("api/jobs");
    if (!res.ok) return { jobs: [], runs: [] };
    return (await res.json()) as { jobs: Job[]; runs: JobRun[] };
  },
  createJob: (body: { name: string; kind: string; params: Record<string, number>; interval_s?: number; daily?: string }) =>
    post<{ job: Job }>("api/jobs", body),
  jobToggle: (id: number, enable: boolean) =>
    post<{ enabled: boolean }>(`api/jobs/${id}/${enable ? "enable" : "disable"}`, {}),
  runJob: (id: number) => post<{ result: unknown }>(`api/jobs/${id}/run`, {}),
  deleteJob: async (id: number) => {
    const res = await fetch(`api/jobs/${id}`, { method: "DELETE", headers: headers() });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return (await res.json()) as { deleted: boolean };
  },
  levelLogStatus: async () => {
    const res = await fetch("api/level-log");
    if (!res.ok) return { enabled: false, period_s: 5, rows: 0 };
    return (await res.json()) as LevelLogStatus;
  },
  configureLevelLog: (enabled: boolean, period_s?: number) =>
    post<LevelLogStatus>("api/level-log", { enabled, period_s }),
  triggerStatus: async () => {
    const res = await fetch("api/trigger");
    if (!res.ok)
      return { armed: false, threshold_dbuv: -30, duration_s: 30, cooldown_s: 60, last_triggered: 0 };
    return (await res.json()) as TriggerStatus;
  },
  configureTrigger: (body: {
    armed?: boolean;
    threshold_dbuv?: number;
    duration_s?: number;
    cooldown_s?: number;
  }) => post<TriggerStatus>("api/trigger", body),
  sweeps: async () => {
    const res = await fetch("api/sweeps");
    if (!res.ok) return [];
    const data = (await res.json()) as { sweeps: SweepRow[] };
    return data.sweeps;
  },
};

export async function fetchCapabilities() {
  const res = await fetch("api/device");
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  const data = (await res.json()) as { capabilities: unknown };
  return data.capabilities;
}
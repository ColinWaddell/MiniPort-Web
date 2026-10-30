import { ApiError, type ScanResponse } from "./types";

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
};

export async function fetchCapabilities() {
  const res = await fetch("api/device");
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  const data = (await res.json()) as { capabilities: unknown };
  return data.capabilities;
}
export interface ReceiverState {
  freq_hz: number;
  demod: string;
  freq_mode: string;
  bandwidth_hz: number;
  afc: boolean;
  att_on: boolean;
  att_auto: boolean;
  gain_db: number;
  gain_mode: string;
  squelch: boolean;
  squelch_threshold_dbuv: number;
  volume: number;
  level_dbuv: number;
  sweeping: boolean;
}

export interface DeviceInfo {
  model: string;
  serial: string;
  firmware: string;
  options: string[];
  host: string;
  port: number;
}

export interface Capabilities {
  frequency_min_hz: number;
  frequency_max_hz: number;
  bandwidth_min_hz: number;
  bandwidth_max_hz: number;
  demodulation_modes: string[];
  frequency_modes: string[];
  gain_min_dbuv: number;
  gain_max_dbuv: number;
  gain_modes: string[];
  sweep_step_max_hz: number;
  udp_trace_tags: string[];
  scan_supported: boolean;
}

export interface ScanResponse {
  levels: (number | null)[];
  frequencies_hz: number[];
  restore_errors: string[];
}

export interface AudioStatus {
  running: boolean;
  rate_hz?: number | null;
  packets?: number;
  lost?: number;
  recording?: boolean;
  recording_name?: string | null;
}

export interface Recording {
  name: string;
  bytes: number;
  duration_s: number;
  rate_hz: number;
  ts: number;
  trigger: string;
}

export interface Preset {
  id: number;
  name: string;
  kind: string;
  params: Record<string, number | string>;
}

export interface Job {
  id: number;
  name: string;
  kind: string;
  params: Record<string, number | string>;
  interval_s: number | null;
  daily: string | null;
  enabled: boolean;
  last_run: number | null;
}

export interface TriggerStatus {
  armed: boolean;
  threshold_dbuv: number;
  duration_s: number;
  cooldown_s: number;
  last_triggered: number;
}

export interface LevelLogStatus {
  enabled: boolean;
  period_s: number;
  rows: number;
}

export interface JobRun {
  job_id: number;
  ts: number;
  status: string;
  message: string;
}

export interface SweepRow {
  id: number;
  ts: number;
  start_hz: number;
  stop_hz: number;
  step_hz: number;
  points: number;
  path: string;
}

export interface Snapshot {
  type: "state";
  ts: number;
  connected: boolean;
  scanning: boolean;
  spectrum: SpectrumStatus;
  audio: AudioStatus;
  error: string | null;
  device: DeviceInfo | null;
  state: ReceiverState | null;
}

export interface SpectrumStatus {
  running: boolean;
  start_hz?: number;
  stop_hz?: number;
  step_hz?: number;
  points?: number;
  cycles?: number;
  last_error?: string | null;
}

export interface Preset {
  id: number;
  name: string;
  kind: string; // 'receiver' | 'spectrum'
  params: Record<string, number | string>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
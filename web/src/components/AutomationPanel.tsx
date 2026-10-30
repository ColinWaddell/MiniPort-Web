import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Job, JobRun, LevelLogStatus, SweepRow, TriggerStatus } from "../lib/types";

interface Props {
  connected: boolean;
  ownsControl: boolean;
}

export function AutomationPanel({ connected, ownsControl }: Props) {
  const [levelLog, setLevelLog] = useState<LevelLogStatus | null>(null);
  const [trigger, setTrigger] = useState<TriggerStatus | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [runs, setRuns] = useState<JobRun[]>([]);
  const [sweeps, setSweeps] = useState<SweepRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [newJob, setNewJob] = useState({
    name: "",
    kind: "sweep",
    startMhz: "27.0",
    stopMhz: "27.4",
    stepKhz: "25",
    intervalMinutes: "10",
  });

  const refresh = useCallback(() => {
    api.levelLogStatus().then(setLevelLog).catch(() => setLevelLog(null));
    api.triggerStatus().then(setTrigger).catch(() => setTrigger(null));
    api.jobs().then((data) => { setJobs(data.jobs); setRuns(data.runs); }).catch(() => {});
    api.sweeps().then(setSweeps).catch(() => setSweeps([]));
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 10_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const run = async (action: () => Promise<unknown>) => {
    try {
      await action();
      setError(null);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      refresh();
    }
  };

  const createJob = () => {
    const minutes = Number.parseFloat(newJob.intervalMinutes);
    const body: {
      name: string;
      kind: string;
      params: Record<string, number>;
      interval_s?: number;
    } = {
      name: newJob.name || `${newJob.kind} job`,
      kind: newJob.kind,
      params:
        newJob.kind === "sweep"
          ? {
              start_hz: Number.parseFloat(newJob.startMhz) * 1e6,
              stop_hz: Number.parseFloat(newJob.stopMhz) * 1e6,
              step_hz: Number.parseFloat(newJob.stepKhz) * 1e3,
            }
          : { duration_s: 30 },
      interval_s: Number.isFinite(minutes) ? Math.max(5, Math.round(minutes * 60)) : 600,
    };
    return run(() => api.createJob(body));
  };

  const triggerRow = trigger ? (
    <>
      <h2>SIGNAL-TRIGGERED RECORDING</h2>
      <div className="field-row">
        <button
          className={`switch ${trigger.armed ? "on" : ""}`}
          disabled={!connected || !ownsControl}
          onClick={() => run(() => api.configureTrigger({ armed: !trigger.armed }))}
          aria-pressed={trigger.armed}
        >
          {trigger.armed ? "TRIGGER ARMED" : "TRIGGER OFF"}
        </button>
        <label>
          THRESHOLD dBµV
          <input
            type="number"
            defaultValue={trigger.threshold_dbuv}
            disabled={!connected || !ownsControl}
            onBlur={(ev) => run(() => api.configureTrigger({ threshold_dbuv: Number(ev.target.value) }))}
          />
        </label>
        <label>
          DURATION s
          <input
            type="number"
            defaultValue={trigger.duration_s}
            disabled={!connected || !ownsControl}
            onBlur={(ev) => run(() => api.configureTrigger({ duration_s: Number(ev.target.value) }))}
          />
        </label>
      </div>
      <span className="metric">
        level ≥ {trigger.threshold_dbuv} dBµV → {trigger.duration_s}s recording ·
        cooldown {trigger.cooldown_s} s (auto-managed WAV, see recordings list)
      </span>
    </>
  ) : null;

  const levelRow = levelLog ? (
    <>
      <h2>LEVEL LOG (SQLite)</h2>
      <div className="field-row">
        <button
          className={`switch ${levelLog.enabled ? "on" : ""}`}
          disabled={!connected || !ownsControl}
          onClick={() => run(() => api.configureLevelLog(!levelLog.enabled))}
          aria-pressed={levelLog.enabled}
        >
          {levelLog.enabled ? "LOGGING" : "LOGGING OFF"}
        </button>
        <label>
          PERIOD s
          <input
            type="number"
            min={0.5}
            defaultValue={levelLog.period_s}
            disabled={!connected || !ownsControl}
            onBlur={(ev) => run(() => api.configureLevelLog(true, Number(ev.target.value)))}
          />
        </label>
        <a className="link" href="api/level-log/export.csv" download>
          export CSV
        </a>
        <a className="link" href="api/level-log/export.json" download>
          JSON
        </a>
      </div>
      <span className="metric">{levelLog.rows} samples stored</span>
    </>
  ) : null;

  const jobsRow = (
    <>
      <h2>SCHEDULED JOBS</h2>
      {jobs.length === 0 ? (
        <div className="scan-empty">No jobs yet. Create one below.</div>
      ) : (
        <ul className="recordings">
          {jobs.map((job) => (
            <li key={job.id}>
              <span className={job.enabled ? "metric" : "rec-flag"}>
                {job.enabled ? "▶ " : "⏸ "}
                {job.name}
              </span>
              <span className="metric">
                {" "}
                {job.kind} ·{" "}
                {job.interval_s
                  ? `every ${job.interval_s}s`
                  : `daily ${job.daily ?? ""}`}{" "}
                ·{" "}
                {job.last_run
                  ? new Date(job.last_run * 1000).toLocaleTimeString()
                  : "never run"}
              </span>
              <button className="link" disabled={!ownsControl}
                onClick={() => run(() => api.runJob(job.id))}>
                run now
              </button>
              <button className="link" disabled={!ownsControl}
                onClick={() => run(() => api.jobToggle(job.id, !job.enabled))}>
                {job.enabled ? "disable" : "enable"}
              </button>
              <button className="link" disabled={!ownsControl}
                onClick={() => run(() => api.deleteJob(job.id))}>
                delete
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="field-row">
        <label>
          JOB NAME
          <input
            value={newJob.name}
            placeholder="optional"
            disabled={!connected || !ownsControl}
            onChange={(ev) => setNewJob({ ...newJob, name: ev.target.value })}
          />
        </label>
        <label>
          KIND
          <select
            value={newJob.kind}
            disabled={!connected || !ownsControl}
            onChange={(ev) => setNewJob({ ...newJob, kind: ev.target.value })}
          >
            <option value="sweep">sweep cycle</option>
            <option value="audio-record">audio recording</option>
          </select>
        </label>
        {newJob.kind === "sweep" ? (
          <>
            <label>
              START MHz
              <input value={newJob.startMhz} disabled={!connected || !ownsControl}
                onChange={(ev) => setNewJob({ ...newJob, startMhz: ev.target.value })} />
            </label>
            <label>
              STOP MHz
              <input value={newJob.stopMhz} disabled={!connected || !ownsControl}
                onChange={(ev) => setNewJob({ ...newJob, stopMhz: ev.target.value })} />
            </label>
            <label>
              STEP kHz
              <input value={newJob.stepKhz} disabled={!connected || !ownsControl}
                onChange={(ev) => setNewJob({ ...newJob, stepKhz: ev.target.value })} />
            </label>
          </>
        ) : null}
        <label>
          EVERY MIN
          <input value={newJob.intervalMinutes} disabled={!connected || !ownsControl}
            onChange={(ev) => setNewJob({ ...newJob, intervalMinutes: ev.target.value })} />
        </label>
        <button className="run" disabled={!connected || !ownsControl} onClick={createJob}>
          CREATE JOB
        </button>
      </div>
      <h2>SWEEP HISTORY (server-side job runs)</h2>
      {sweeps.length === 0 ? (
        <div className="scan-empty">No stored sweeps yet.</div>
      ) : (
        <ul className="recordings">
          {sweeps.slice(0, 10).map((sweep) => (
            <li key={sweep.id}>
              <a href={`api/sweeps/${sweep.id}/export.csv`} download>
                sweep #{sweep.id}
              </a>
              <span className="metric">
                {" "}
                {new Date(sweep.ts * 1000).toLocaleString()} ·{" "}
                {(sweep.start_hz / 1e6).toFixed(2)}–{(sweep.stop_hz / 1e6).toFixed(2)} MHz ·{" "}
                {sweep.points} points
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="field-row">
        <a className="link" href="api/spectrum/export.csv" download>
          export current waterfall CSV
        </a>
        <span className="metric">
          recent runs:{" "}
          {runs.slice(0, 5).map((entry) => `${entry.job_id}:${entry.status}`).join(" ") ||
            "none"}
        </span>
      </div>
    </>
  );

  return (
    <div className="panel-body">
      <h2>AUTOMATION</h2>
      {error ? (
        <div className="banner">
          <span>⚠ {error}</span>
        </div>
      ) : null}
      {triggerRow}
      {levelRow}
      {jobsRow}
    </div>
  );
}
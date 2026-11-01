import { useEffect, useState } from "react";
import { Header } from "./components/Header";
import { HelpSheet } from "./components/help";
import { FrequencyDisplay } from "./components/FrequencyDisplay";
import { LevelMeter } from "./components/LevelMeter";
import { ModePanel } from "./components/ModePanel";
import { GainPanel } from "./components/GainPanel";
import { SquelchPanel } from "./components/SquelchPanel";
import { VolumePanel } from "./components/VolumePanel";
import { ScanPanel } from "./components/ScanPanel";
import { SpectrumPanel } from "./components/SpectrumPanel";
import { AudioPanel } from "./components/AudioPanel";
import { AutomationPanel } from "./components/AutomationPanel";
import { ErrorBanner } from "./components/ErrorBanner";
import { api, clientId, fetchCapabilities } from "./lib/api";
import { useReceiver } from "./lib/useReceiver";
import type { Capabilities } from "./lib/types";

export function App() {
  const { snapshot, ws } = useReceiver();
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [ownsControl, setOwnsControl] = useState(false);
  const [controlNote, setControlNote] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [attempting, setAttempting] = useState(false);

  useEffect(() => {
    fetchCapabilities()
      .then((capabilities_) => setCapabilities(capabilities_ as Capabilities))
      .catch(() => setCapabilities(null));
  }, []);

  useEffect(() => {
    api
      .acquireControl()
      .then(() => {
        setOwnsControl(true);
        setControlNote(null);
      })
      .catch((error: { status: number; message: string }) => {
        setOwnsControl(false);
        if (error.status === 409) setControlNote(error.message);
      });
  }, []);

  const connected = snapshot?.connected ?? false;
  const state = snapshot?.state ?? null;
  const controllable = connected && ownsControl && !snapshot?.scanning;

  const apply = async (action: () => Promise<unknown>) => {
    try {
      await action();
      setApiError(null);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : String(error));
    }
  };

  const takeControl = () => {
    api
      .acquireControl()
      .then(() => {
        setOwnsControl(true);
        setControlNote(null);
      })
      .catch((error: { status: number; message: string }) => {
        if (error.status === 409) setControlNote(error.message);
      });
  };

  const attemptReconnect = () => {
    if (attempting) return;
    setAttempting(true);
    api
      .reconnect()
      .then(() => setApiError(null))
      .catch((error: { status: number; message: string }) => {
        setApiError(error.message);
      })
      .finally(() => setAttempting(false));
  };

  return (
    <div className="shell">
      <Header
        device={snapshot?.device ?? null}
        ws={ws}
        connected={connected}
        ownsControl={ownsControl}
        controlNote={controlNote}
        attempting={attempting}
        onTakeControl={takeControl}
        onReconnect={attemptReconnect}
        onHelp={() => setHelpOpen(true)}
      />
      {helpOpen ? <HelpSheet onClose={() => setHelpOpen(false)} /> : null}
      {snapshot?.error ? (
        <ErrorBanner
          text={snapshot.error}
          onDismiss={() => setApiError(null)}
        />
      ) : null}
      {apiError ? (
        <ErrorBanner text={apiError} onDismiss={() => setApiError(null)} />
      ) : null}
      {snapshot?.scanning ? (
        <div className="notice">
          Sweep running — control is paused until the sweep completes.
        </div>
      ) : null}
      {state ? (
        <main className="panels">
          <section className="panel panel-wide">
            <FrequencyDisplay
              state={state}
              controllable={controllable}
              minHz={capabilities?.frequency_min_hz ?? 10_000}
              maxHz={capabilities?.frequency_max_hz ?? 3_000_000_000}
              onSet={(hz) => apply(() => api.setFrequency(hz))}
              onOperation={(mode) => apply(() => api.setOperation(mode))}
            />
            <LevelMeter levelDbuv={state.level_dbuv} squelch={state.squelch} />
          </section>
          <section className="panel">
            <ModePanel
              state={state}
              capabilities={capabilities}
              controllable={controllable}
              onDemod={(mode) => apply(() => api.setDemod(mode))}
              onBandwidth={(hz) => apply(() => api.setBandwidth(hz))}
              onAfc={(enabled) => apply(() => api.setAfc(enabled))}
            />
          </section>
          <section className="panel">
            <GainPanel
              state={state}
              capabilities={capabilities}
              controllable={controllable}
              onGain={(db) => apply(() => api.setGain(db))}
              onGainMode={(mode) => apply(() => api.setGainMode(mode))}
              onAttenuator={(enabled, auto) =>
                apply(() => api.setAttenuator(enabled, auto))
              }
            />
          </section>
          <section className="panel">
            <SquelchPanel
              state={state}
              controllable={controllable}
              onSquelch={(enabled) => apply(() => api.setSquelch(enabled))}
              onThreshold={(dbuv) =>
                apply(() => api.setSquelchThreshold(dbuv))
              }
            />
          </section>
          <section className="panel">
            <VolumePanel
              state={state}
              controllable={controllable}
              onVolume={(value) => apply(() => api.setVolume(value))}
            />
          </section>
          <section className="panel panel-wide">
            <SpectrumPanel
              connected={connected}
              ownsControl={ownsControl}
              scanning={snapshot?.scanning ?? false}
              status={snapshot?.spectrum ?? { running: false }}
              tuneHz={state?.freq_hz ?? null}
              thresholdDbuv={state?.squelch_threshold_dbuv ?? null}
              onTune={(hz) => apply(() => api.setFrequency(hz))}
            />
          </section>
          <section className="panel panel-wide">
            <AudioPanel
              connected={connected}
              ownsControl={ownsControl}
              status={
                snapshot?.audio ?? {
                  running: false,
                }
              }
            />
          </section>
          <section className="panel panel-wide">
            <AutomationPanel connected={connected} ownsControl={ownsControl} />
          </section>
          <section className="panel panel-wide">
            <ScanPanel
              state={state}
              controllable={controllable}
              scanning={snapshot?.scanning ?? false}
            />
          </section>
        </main>
      ) : (
        <div className="placeholder">
          {connected ? "waiting for receiver state…" : "receiver offline"}
        </div>
      )}
      <footer className="footer">
        client {clientId().slice(0, 8)} · MiniPort-Web 0.10.0
      </footer>
    </div>
  );
}
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

/// In-interface help: dotted-underline hover bubbles + a full reference
/// sheet. Content is written for THIS receiver (firmware 2.80) and the
/// behaviour we verified against it.

export const TIPS: Record<string, string> = {
  frequency:
    "Tuning frequency in MHz. Type a value and press Enter, or nudge with the step buttons.",
  steps: "Tune up or down by that many kHz.",
  operation:
    "CW = tune and listen. FIXed works like CW on this firmware (the receiver reads it back as CW). SCAN = the receiver's own frequency-scan mode.",
  demodulator:
    "Demodulation: AM (broadcast, airband), FM (including broadcast wide-FM with a large bandwidth), CW, USB and LSB sideband.",
  afc: "Automatic Frequency Control — the receiver pulls its tuning onto the centre of a drifting carrier. OFF keeps your exact tuning.",
  bandwidth:
    "Selectivity in Hz, 150 to 150,000. Narrower keeps adjacent signals out; match it to the signal you are listening to.",
  gain: "Manual gain (MGC) in dBµV: −30 = most sensitive, 110 = least sensitive. Applies in the FIXed mode; AGC/AUTO overrides it.",
  attenuator:
    "A 32 dB input pad. AUTO couples it to incoming signal strength; ON pins it in to protect the front end near strong transmitters.",
  squelch:
    "Mutes the receiver's audio until the measured level rises above the threshold, then opens it.",
  threshold:
    "dBµV level the signal must exceed to open the squelch. Around −30 to −35 is about the local noise floor in this room.",
  af_volume:
    "Level of the receiver's own loudspeaker and headphone jack (0–1). Not the browser playback volume — that is LISTEN VOLUME (Web Audio).",
  level: "Live signal level in dBµV, a field-strength scale at the antenna connector. Not a dBm power reading.",
  listen:
    "Browser playback of the receiver's demodulated audio, streamed from the receiver over UDP via this server. If it connects but no packets arrive, the host firewall needs a rule for UDP from the receiver (shown as a hint).",
  listen_volume:
    "Playback volume in your own browser (Web Audio gain node) — independent of the receiver's AF VOLUME.",
  af_dsp:
    "Client-side analysis of the demodulated audio: every sample your browser already receives is processed locally — no server load, no extra traffic. The band shown spans 0 Hz to half the measured wire rate (0–4 kHz at the default remote AF mode).",
  af_trace:
    "SPECTRUM: fast Fourier transform of the last ~0.25 s of audio on a dBFS axis (0 dB = full scale), with a slow peak-decay. SCOPE: rise-triggered oscilloscope view of the raw waveform.",
  af_waterfall:
    "SPECTROGRAM: scrolling colour map of the AF spectrum, time flows downward. WAVELET: same scrolling display but with constant-Q (Morlet wavelet) analysis on a logarithmic frequency axis — CTCSS tones, CW notes and speech pitches appear as cleaner, longer rows than the fixed-resolution FFT can show.",
  af_sensitivity:
    "Tightens (higher) or widens (lower) the AF waterfall's auto colour range around the rolling median; 50 is neutral. Same behaviour as the RF waterfall's slider.",
  af_meter:
    "Power of the audio this browser received since the last tick, in dB relative to full scale (dBFS). RMS follows speech/traffic level; PEAK catches transients.",
  af_decoders:
    "Local tone analysis on the demodulated audio, indicative rather than certified: DTMF = telephone keypad digits (pairs of tones held ~80 ms); CTCSS = the sub-audible squelch tone a transmitter encodes; CW = the pitch of a detected Morse carrier note, a zero-beat aid. No receiver settings are touched.",
  record:
    "Records the demodulated audio to a 16-bit mono WAV file on the server, listed under RECORDINGS for download or deletion.",
  recordings:
    "WAV files written by this server (data/recordings/). Size, duration and the trigger that created each one are stored with them.",
  spectrum:
    "Live sweep over the band set below. The trace is the latest level per bin (dBµV); the dashed line is peak-hold; the waterfall paints levels over time.",
  sweep_band:
    "Band edges in MHz. STOP must stay above START — the driver steps wide first, then narrows, to satisfy the instrument's ordering rule.",
  sweep_step:
    "Bin spacing in kHz. Finer steps give more detail but slower cycles.",
  sweep_control:
    "Starts or stops continuous sweep cycles. While sweeping, receiver control elsewhere pauses (the panel shows the notice).",
  reset_peak:
    "Clears the peak-hold trace (dashed line = highest level seen per bin since the sweep started).",
  waterfall:
    "Scrolling colour map of the levels: time flows downward, colour = level (dark = noise floor, cyan/yellow = strong, red = strongest).",
  span_chips:
    "Recentre the sweep band on the receiver's current tune with that total width.",
  step_chips: "Set the sweep step quickly.",
  display_range:
    "Vertical scale of the spectrum trace (dBµV) — always an absolute, trustworthy level reading. In MANUAL it also sets the waterfall colours; with AUTO WATERFALL on, the inputs instead show the live computed colour range.",
  presets:
    "Named band configurations stored in the server's database; load one to re-fill START/STOP/STEP.",
  trigger:
    "Automatic recording: when the level rises above the threshold the server records a WAV for the set duration, then waits out the cooldown before re-arming.",
  level_log:
    "Periodically stores the receiver level (and tune) into SQLite for export. Retention: level rows default to 7 days.",
  jobs:
    "Recurring tasks the server runs on its own: sweep cycles (stored and exportable) or timed audio recordings. Run-now ignores the schedule.",
  every_min: "Job schedule in minutes between runs.",
  sweeps:
    "Sweep cycles saved by scheduled jobs; each can be exported as CSV (frequency × level).",
  connection:
    "Link state between the server and the EB200. The browser keeps working while it is down; nothing is lost except live data.",
  auto_waterfall:
    "Self-normalizing waterfall: the colour range spans the rolling 5th–95th percentile of received levels, so the noise floor expands and faint signals become visible. The window rebuilds whenever the sweep band changes.",
  sensitivity:
    "Tightens (higher) or widens (lower) the normalizer's window around the median level: higher makes faint signals pop but amplifies noise too; 50 is neutral.",
};

export interface PanelHelp {
  title: string;
  items: { term: string; text: string }[];
}

export const PANEL_HELP: PanelHelp[] = [
  {
    title: "HEADER BADGES",
    items: [
      { term: "connection badge", text: TIPS.connection },
      {
        term: "controls badge",
        text:
          "Control ownership: only one browser may command the receiver. Take control to claim it; the lease expires after 2 minutes of inactivity or a closed tab.",
      },
    ],
  },
  {
    title: "FREQUENCY",
    items: [
      { term: "frequency field", text: TIPS.frequency },
      { term: "step buttons", text: TIPS.steps },
      { term: "OPERATION", text: TIPS.operation },
    ],
  },
  {
    title: "DEMODULATOR / BANDWIDTH",
    items: [
      { term: "MODE", text: TIPS.demodulator },
      { term: "AFC", text: TIPS.afc },
      { term: "IF BANDWIDTH", text: TIPS.bandwidth },
    ],
  },
  {
    title: "GAIN",
    items: [
      { term: "GAIN MODE", text: "AGC/AUTO: the receiver adjusts its own gain. FIXed/MGC: the MGC value below applies." },
      { term: "MGC", text: TIPS.gain },
      { term: "RF ATTENUATOR", text: TIPS.attenuator },
    ],
  },
  {
    title: "SQUELCH",
    items: [
      { term: "SQUELCH", text: TIPS.squelch },
      { term: "THRESHOLD", text: TIPS.threshold },
    ],
  },
  {
    title: "LEVEL",
    items: [{ term: "SIGNAL LEVEL", text: TIPS.level }],
  },
  {
    title: "VOLUME (receiver)",
    items: [{ term: "AF VOLUME", text: TIPS.af_volume }],
  },
  {
    title: "REMOTE AUDIO",
    items: [
      { term: "LISTEN", text: TIPS.listen },
      { term: "LISTEN VOLUME", text: TIPS.listen_volume },
      { term: "AF DSP DISPLAY", text: TIPS.af_dsp },
      { term: "TRACE chips", text: TIPS.af_trace },
      { term: "WATERFALL chips", text: TIPS.af_waterfall },
      { term: "SENSITIVITY (AF)", text: TIPS.af_sensitivity },
      { term: "AF POWER", text: TIPS.af_meter },
      { term: "TONE DECODERS", text: TIPS.af_decoders },
      { term: "RECORD", text: TIPS.record },
      { term: "RECORDINGS", text: TIPS.recordings },
    ],
  },
  {
    title: "LIVE SPECTRUM / WATERFALL",
    items: [
      { term: "trace + peak-hold + waterfall", text: TIPS.spectrum },
      { term: "START/STOP MHz", text: TIPS.sweep_band },
      { term: "STEP kHz", text: TIPS.sweep_step },
      { term: "START/STOP SWEEPING", text: TIPS.sweep_control },
      { term: "RESET PEAK", text: TIPS.reset_peak },
      { term: "WATERFALL", text: TIPS.waterfall },
      { term: "SPAN chips", text: TIPS.span_chips },
      { term: "STEP chips", text: TIPS.step_chips },
      { term: "AUTO WATERFALL", text: TIPS.auto_waterfall },
      { term: "SENSITIVITY", text: TIPS.sensitivity },
      { term: "DISPLAY dBµV range", text: TIPS.display_range },
      { term: "SAVE/CLEAR/FREEZE", text: "Display-only actions: PNG export, waterfall clear, frozen picture — they never touch the receiver." },
      { term: "PRESETS", text: TIPS.presets },
    ],
  },
  {
    title: "AUTOMATION",
    items: [
      { term: "SIGNAL-TRIGGERED RECORDING", text: TIPS.trigger },
      { term: "LEVEL LOG", text: TIPS.level_log },
      { term: "SCHEDULED JOBS", text: TIPS.jobs },
      { term: "EVERY MIN", text: TIPS.every_min },
      { term: "SWEEP HISTORY", text: TIPS.sweeps },
    ],
  },
];

export function HelpTip({ term, children }: { term: string; children: ReactNode }) {
  const tip = TIPS[term];
  if (!tip) {
    return <>{children}</>;
  }
  return (
    <span className="tipword" aria-label={tip}>
      {children}
      <span className="bubble" role="tooltip">
        {tip}
      </span>
    </span>
  );
}

export function HelpButton({ onOpen }: { onOpen: () => void }) {
  return (
    <button className="btn help-btn" onClick={onOpen} aria-label="help reference">
      ?
    </button>
  );
}

export function HelpSheet({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const [open, setOpen] = useState(false);
  useEffect(() => {
    requestAnimationFrame(() => setOpen(true));
  }, []);

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div
        className={`sheet ${open ? "open" : ""}`}
        role="dialog"
        aria-label="reference"
        onClick={(ev) => ev.stopPropagation()}
      >
        <div className="sheet-head">
          <span>REFERENCE — controls and what they do</span>
          <button className="link" onClick={onClose}>
            close ✕
          </button>
        </div>
        <div className="sheet-body">
          <p className="sheet-note">
            Written for this receiver (EB200, firmware 2.80) and what has been
            verified against it. Hover any underlined word in the interface for
            the same information in place.
          </p>
          {PANEL_HELP.map((panel) => (
            <div key={panel.title} className="sheet-group">
              <div className="sheet-title">{panel.title}</div>
              {panel.items.map((item) => (
                <div key={item.term} className="sheet-item">
                  <span className="sheet-term">{item.term}</span>
                  <span>{item.text}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
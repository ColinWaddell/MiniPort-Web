/// Audio-band DSP for the /ws/audio stream (client-side, no React imports).
///
/// Taps the Float32 chunks `AudioStreamer` already decodes for playback and
/// keeps a ~2 s ring buffer at the measured wire rate (~8 kHz → 0–4 kHz AF
/// band). Every transform is sized for that rate, so all of it is cheap
/// (worst view ≈ 0.2 ms/frame). The Morlet scalogram uses direct wavelet
/// correlators (one dot product per scale) instead of an FFT-based CWT —
/// visually equivalent for a scrolling display and ~100× less work.
///
/// Level convention: dBFS, 0 dB = full-scale sine (samples are ±1).

const TAU = Math.PI * 2;

/** FFT length for the AF spectrum, spectrogram and CW peak (samples). */
export const TRACE_SIZE = 2048;
/** Oscilloscope window length (samples); ~128 ms at the 8 kHz wire rate. */
export const SCOPE_SIZE = 1024;
/** Display columns per row (matches the canvases in AudioPanel). */
export const TRACE_COLUMNS = 960;
/** Ring capacity (samples ~ 2 s at the 8 kHz wire rate). */
const RING_CAPACITY = 16384;

/** Peak-decay of the spectrum trace between draw ticks (dB per tick). */
const SPECTRUM_DECAY_DB = 2.0;
/** Number of Morlet scales in the scalogram view (log-spaced). */
const WAVELET_SCALES = 48;
/** Spectrum trace display range (dBFS), used by the AudioPanel trace view. */
export const TRACE_DB_MIN = -90;
export const TRACE_DB_MAX = -5;

/** DTMF: minimum tone separation from the group's second peak (ratio). */
const DTMF_SELECTIVITY = 4.0;
/** DTMF: allowed level difference between the row and column tones (dB). */
const DTMF_TWIST_DB = 12.0;
/** DTMF: minimum tone level for a detection (dBFS). */
const DTMF_FLOOR_DB = -45;
/** DTMF: consecutive windows a pair must hold before the digit is emitted. */
const DTMF_STABLE_WINDOWS = 2;
/** CTCSS: tones further apart than this count as independent channels (Hz). */
const CTCSS_FAR_HZ = 30.0;
/** CTCSS: best tone must exceed the strongest far tone by this ratio. */
const CTCSS_SELECTIVITY = 8.0;
/** CTCSS: minimum tone level (dBFS) and window RMS floor (dBFS). */
const CTCSS_FLOOR_DB = -50;
const CTCSS_RMS_FLOOR_DB = -54;
/** CTCSS: fraction of the recent checks that must agree to display (of 8). */
const CTCSS_HOLD = 5;
/** CW: peak must exceed the band average by this much (dB). */
const CW_SNR_DB = 8.0;
/** CW: zero-beat search band (Hz) and median-smoothing depth. */
const CW_MIN_HZ = 300;
const CW_MAX_HZ = 3000;
const CW_MEDIAN_DEPTH = 5;

function toDb(amplitude: number): number {
  return 20 * Math.log10(amplitude + 1e-10);
}

/** Normalised Goertzel power; ~1.0 for a bin-centred full-scale sine. */
export function goertzelPower(
  samples: Float32Array,
  count: number,
  freqHz: number,
  rateHz: number,
): number {
  const omega = (TAU * freqHz) / rateHz;
  const coeff = 2 * Math.cos(omega);
  let s1 = 0;
  let s2 = 0;
  for (let i = 0; i < count; i++) {
    const s0 = samples[i] + coeff * s1 - s2;
    s2 = s1;
    s1 = s0;
  }
  return s1 * s1 + s2 * s2 - coeff * s1 * s2;
}

/** Goertzel power → dBFS (0 dB = bin-centred full-scale sine). */
export function powerToDb(power: number, count: number): number {
  return 10 * Math.log10((power + 1e-30) / (0.25 * count * count));
}

// ---------------------------------------------------------------------------
// FFT
// ---------------------------------------------------------------------------

/** Iterative radix-2 FFT with a periodic Hann window and dBFS magnitudes. */
export class Fft {
  readonly size: number;
  readonly bins: number;
  private re: Float32Array;
  private im: Float32Array;
  private rev: Uint32Array;
  private cosTable: Float32Array;
  private sinTable: Float32Array;
  private window: Float32Array;

  constructor(size: number) {
    if (size < 4 || (size & (size - 1)) !== 0) {
      throw new RangeError("fft size must be a power of two ≥ 4");
    }
    this.size = size;
    this.bins = size >> 1;
    this.re = new Float32Array(size);
    this.im = new Float32Array(size);
    const bits = Math.round(Math.log2(size));
    this.rev = new Uint32Array(size);
    for (let i = 0; i < size; i++) {
      let reversed = 0;
      for (let b = 0; b < bits; b++) {
        reversed |= ((i >>> b) & 1) << (bits - 1 - b);
      }
      this.rev[i] = reversed;
    }
    this.cosTable = new Float32Array(size >> 1);
    this.sinTable = new Float32Array(size >> 1);
    for (let k = 0; k < size >> 1; k++) {
      this.cosTable[k] = Math.cos((TAU * k) / size);
      this.sinTable[k] = Math.sin((TAU * k) / size);
    }
    // periodic Hann (spectral-analysis convention)
    this.window = new Float32Array(size);
    for (let i = 0; i < size; i++) {
      this.window[i] = 0.5 - 0.5 * Math.cos((TAU * i) / size);
    }
  }

  /** dBFS spectrum of `input` (length size) into `out` (length size/2). */
  runDb(input: Float32Array, out: Float32Array): void {
    const { size: n, re, im, window } = this;
    for (let i = 0; i < n; i++) {
      re[i] = input[i] * window[i];
      im[i] = 0;
    }
    this.transform();
    // |X| of a bin-centred full-scale sine is n/4 (Hann gain 0.5) → amp·4/n
    for (let k = 0; k < this.bins; k++) {
      const amp = (4 * Math.sqrt(re[k] * re[k] + im[k] * im[k])) / n;
      out[k] = toDb(amp);
    }
  }

  private transform(): void {
    const n = this.size;
    const re = this.re;
    const im = this.im;
    for (let i = 0; i < n; i++) {
      const j = this.rev[i];
      if (j > i) {
        let t = re[i];
        re[i] = re[j];
        re[j] = t;
        t = im[i];
        im[i] = im[j];
        im[j] = t;
      }
    }
    for (let blockSize = 2; blockSize <= n; blockSize <<= 1) {
      const half = blockSize >> 1;
      const step = n / blockSize;
      for (let base = 0; base < n; base += blockSize) {
        for (let j = base, k = 0; j < base + half; j++, k += step) {
          const cos = this.cosTable[k];
          const sin = this.sinTable[k];
          const tre = re[j + half] * cos + im[j + half] * sin;
          const tim = im[j + half] * cos - re[j + half] * sin;
          re[j + half] = re[j] - tre;
          im[j + half] = im[j] - tim;
          re[j] += tre;
          im[j] += tim;
        }
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Ring buffer
// ---------------------------------------------------------------------------

/** Fixed-capacity sample ring; `latest()` yields a chronological window. */
export class RingBuffer {
  readonly capacity: number;
  private buf: Float32Array;
  private writeIdx = 0;
  private filled = 0;

  constructor(capacity: number) {
    this.capacity = capacity;
    this.buf = new Float32Array(capacity);
  }

  get count(): number {
    return this.filled;
  }

  append(samples: Float32Array): void {
    const n = samples.length;
    if (n === 0) return;
    if (n >= this.capacity) {
      this.buf.set(samples.subarray(n - this.capacity));
      this.writeIdx = 0;
      this.filled = this.capacity;
      return;
    }
    const tail = this.capacity - this.writeIdx;
    if (n <= tail) {
      this.buf.set(samples, this.writeIdx);
      this.writeIdx += n;
    } else {
      this.buf.set(samples.subarray(0, tail), this.writeIdx);
      this.buf.set(samples.subarray(tail), 0);
      this.writeIdx = n - tail;
    }
    this.writeIdx %= this.capacity;
    this.filled = Math.min(this.filled + n, this.capacity);
  }

  /** Copy the newest `n` samples in chronological order (false if short). */
  latest(n: number, out: Float32Array): boolean {
    if (this.filled < n || n > out.length) return false;
    const start =
      (((this.writeIdx - n) % this.capacity) + this.capacity) % this.capacity;
    const tail = this.capacity - start;
    if (n <= tail) {
      out.set(this.buf.subarray(start, start + n));
    } else {
      out.set(this.buf.subarray(start, this.capacity));
      out.set(this.buf.subarray(0, n - tail), tail);
    }
    return true;
  }

  reset(): void {
    this.writeIdx = 0;
    this.filled = 0;
  }
}

// ---------------------------------------------------------------------------
// Morlet wavelet bank (scalogram)
// ---------------------------------------------------------------------------

const MORLET_W0 = 6;

/** Direct Morlet correlators: |CWT| per scale at the newest instant, in dB. */
export class MorletBank {
  readonly scaleCount: number;
  readonly hzLow: number;
  readonly hzHigh: number;
  /** Centre frequency of each scale (Hz), ascending. */
  readonly freqsHz: number[];
  private cosRows: Float32Array[] = [];
  private sinRows: Float32Array[] = [];
  private norms: number[] = [];
  private maxRowLength = 0;
  private rateHz: number;

  /** Scales span hzLow…hzHigh logarithmically; hzHigh ≤ rate/3 so the
   * shortest correlator still has a usable number of samples. */
  constructor(
    rateHz: number,
    scaleCount = WAVELET_SCALES,
    hzLow = 50,
    hzHigh = Math.min(2000, Math.round((rateHz / 3) * 0.9)),
  ) {
    this.rateHz = rateHz;
    this.scaleCount = scaleCount;
    this.hzLow = hzLow;
    this.hzHigh = Math.max(hzLow * 2, hzHigh);
    const octaves = Math.log2(this.hzHigh / this.hzLow);
    this.freqsHz = [];
    for (let s = 0; s < scaleCount; s++) {
      this.freqsHz.push(this.hzLow * Math.pow(2, (octaves * s) / (scaleCount - 1)));
    }
    this.build();
  }

  private build(): void {
    const cosRows: Float32Array[] = [];
    const sinRows: Float32Array[] = [];
    const norms: number[] = [];
    let maxRow = 0;
    for (const freq of this.freqsHz) {
      // scale in seconds so the correlator's spectral centre is the Morlet
      // lobe ω0, which maps to `freq`: ω0/(scale·rate) rad/sample
      const scale = MORLET_W0 / (TAU * freq);
      const half = Math.min(
        (TRACE_SIZE - 1) >> 1,
        Math.ceil(3 * scale * this.rateHz),
      );
      const length = 2 * half + 1;
      if (length > maxRow) maxRow = length;
      const centre = half; // wavelet centre sample index
      const cos = new Float32Array(length);
      const sin = new Float32Array(length);
      let energy = 0;
      for (let j = 0; j < length; j++) {
        const tau = (j - centre) / (scale * this.rateHz);
        const envelope = Math.exp(-(tau * tau) / 2);
        cos[j] = Math.cos(MORLET_W0 * tau) * envelope;
        sin[j] = Math.sin(MORLET_W0 * tau) * envelope;
        energy += cos[j] * cos[j];
      }
      // normalise so a bin-centred full-scale sine reads ≈ 0 dB
      norms.push(2 / Math.max(energy, 1e-12));
      cosRows.push(cos);
      sinRows.push(sin);
    }
    this.cosRows = cosRows;
    this.sinRows = sinRows;
    this.norms = norms;
    this.maxRowLength = maxRow;
  }

  /** Longest correlator window the caller must supply (samples). */
  get requiredSamples(): number {
    return this.maxRowLength;
  }

  /** dB magnitude per scale, anchored at the centre of `recent`
   * (chronological, length ≥ requiredSamples; usually TRACE_SIZE). */
  rowInto(recent: Float32Array, out: Float32Array): void {
    const available = recent.length;
    for (let s = 0; s < this.scaleCount; s++) {
      const cos = this.cosRows[s];
      const sin = this.sinRows[s];
      const length = Math.min(cos.length, available);
      let re = 0;
      let im = 0;
      const start = available - length;
      for (let j = 0; j < length; j++) {
        const x = recent[start + j];
        re += x * cos[j];
        im += x * sin[j];
      }
      out[s] = toDb(this.norms[s] * Math.sqrt(re * re + im * im));
    }
  }

  /** Fill `out` (display columns) by interpolating the scale row along the
   * logarithmic frequency axis. */
  columnRow(scaleRow: Float32Array, out: Float32Array): void {
    const cols = out.length;
    const last = this.scaleCount - 1;
    for (let x = 0; x < cols; x++) {
      const pos = (x / (cols - 1)) * last;
      const idx = Math.min(Math.floor(pos), last - 1);
      const frac = pos - idx;
      out[x] = scaleRow[idx] + (scaleRow[idx + 1] - scaleRow[idx]) * frac;
    }
  }
}

// ---------------------------------------------------------------------------
// Auto range (port of spectrum.ts WaterfallNormalizer, allocation-free)
// ---------------------------------------------------------------------------

interface PercentileFrame {
  min: number;
  p05: number;
  p50: number;
  p95: number;
  max: number;
}

/** Rolling percentile palette range for the AF waterfall views. */
export class AfAutoRange {
  private frames: PercentileFrame[] = [];
  private capacity = 50;
  private scratch = new Float32Array(TRACE_COLUMNS);

  push(row: Float32Array): void {
    const values = this.scratch.subarray(0, Math.min(row.length, this.scratch.length));
    values.set(row.subarray(0, values.length));
    values.sort();
    if (values.length < 8) return;
    const pick = (p: number) => values[Math.min(values.length - 1, Math.floor(p * values.length))];
    this.frames.push({
      min: values[0],
      p05: pick(0.05),
      p50: pick(0.5),
      p95: pick(0.95),
      max: values[values.length - 1],
    });
    if (this.frames.length > this.capacity) this.frames.shift();
  }

  reset(): void {
    this.frames = [];
  }

  range(sensitivity: number): { min: number; max: number } | null {
    if (this.frames.length < 3) return null;
    const clamp01 = Math.min(Math.max(sensitivity / 100, 0), 1);
    const t = clamp01 * 2 - 1; // -1..1 (50 = neutral)
    const median = (pick: (frame: PercentileFrame) => number) => {
      const values = this.frames.map(pick).sort((a, b) => a - b);
      return values[Math.floor(values.length / 2)];
    };
    const p05 = median((frame) => frame.p05);
    const p50 = median((frame) => frame.p50);
    const p95 = median((frame) => frame.p95);
    const winMin = median((frame) => frame.min);
    const winMax = median((frame) => frame.max);
    const span = Math.max(p95 - p05, 1);
    let min: number;
    let max: number;
    if (t >= 0) {
      min = p05 + (p50 - p05) * t * 0.9;
      max = p95 - (p95 - p50) * t * 0.9;
    } else {
      min = p05 - span * -t * 0.5;
      max = p95 + span * -t * 0.5;
      min = Math.max(min, winMin);
      max = Math.min(max, winMax);
    }
    if (max - min < 1) max = min + 1;
    return { min, max };
  }
}

// ---------------------------------------------------------------------------
// Tone decoders
// ---------------------------------------------------------------------------

/** Lowest tone group of the DTMF keypad (Hz). */
export const DTMF_LOW_HZ = [697, 770, 852, 941];
/** Highest tone group of the DTMF keypad (Hz). */
export const DTMF_HIGH_HZ = [1209, 1336, 1477, 1633];
/** Keypad layout: DTMF_KEYS[lowIndex][highIndex]. */
export const DTMF_KEYS = [
  ["1", "2", "3", "A"],
  ["4", "5", "6", "B"],
  ["7", "8", "9", "C"],
  ["*", "0", "#", "D"],
];

/** Standard 50-tone CTCSS list (Hz), ascending. */
export const CTCSS_TUNES = [
  67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4,
  100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3,
  146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3, 173.8, 177.3, 179.9,
  183.5, 186.2, 189.9, 192.8, 196.6, 199.5, 203.5, 206.5, 210.7, 218.1, 225.7,
  229.1, 233.6, 241.8, 250.3, 254.8,
];

interface ToneHit {
  lowIdx: number;
  highIdx: number;
}

/** DTMF digit detector over the streaming audio (Goertzel pair test). */
export class DtmfDetector {
  private lastRunCount = 0;
  private lastHit: ToneHit | null = null;
  private stableRuns = 0;
  private quietRuns = 0;
  private emitted: ToneHit | null = null;
  private scratch = new Float32Array(1024);

  /** Analyses the newest window when ≥ ~40 ms of new samples arrived.
   * Returns the confirmed digit or null. */
  detect(ring: RingBuffer, rateHz: number): string | null {
    const window = Math.max(256, Math.round(0.04 * rateHz));
    if (ring.count < window) return null;
    if (ring.count - this.lastRunCount < Math.round(window * 0.9)) return null;
    if (!ring.latest(window, this.scratch)) return null;
    this.lastRunCount = ring.count;

    let lowBest = -1;
    let lowBestPower = 0;
    let lowSecond = 0;
    for (let i = 0; i < DTMF_LOW_HZ.length; i++) {
      const power = goertzelPower(this.scratch, window, DTMF_LOW_HZ[i], rateHz);
      if (power > lowBestPower) {
        lowSecond = lowBestPower;
        lowBestPower = power;
        lowBest = i;
      } else if (power > lowSecond) {
        lowSecond = power;
      }
    }
    let highBest = -1;
    let highBestPower = 0;
    let highSecond = 0;
    for (let i = 0; i < DTMF_HIGH_HZ.length; i++) {
      const power = goertzelPower(this.scratch, window, DTMF_HIGH_HZ[i], rateHz);
      if (power > highBestPower) {
        highSecond = highBestPower;
        highBestPower = power;
        highBest = i;
      } else if (power > highSecond) {
        highSecond = power;
      }
    }
    const lowOk = lowBest >= 0 && lowSecond < lowBestPower / DTMF_SELECTIVITY;
    const highOk = highBest >= 0 && highSecond < highBestPower / DTMF_SELECTIVITY;
    const twist = 10 * Math.log10((lowBestPower + 1e-30) / (highBestPower + 1e-30));
    const loud =
      powerToDb(lowBestPower, window) > DTMF_FLOOR_DB &&
      powerToDb(highBestPower, window) > DTMF_FLOOR_DB;
    if (!(lowOk && highOk && Math.abs(twist) <= DTMF_TWIST_DB && loud)) {
      this.stableRuns = 0;
      if (++this.quietRuns > 8) this.emitted = null;
      return null;
    }
    const hit: ToneHit = { lowIdx: lowBest, highIdx: highBest };
    const same =
      this.lastHit !== null &&
      this.lastHit.lowIdx === hit.lowIdx &&
      this.lastHit.highIdx === hit.highIdx;
    this.lastHit = hit;
    this.quietRuns = 0;
    if (!same) {
      this.stableRuns = 1;
      this.emitted = null;
      return null;
    }
    this.stableRuns += 1;
    const repeat =
      this.emitted !== null &&
      this.emitted.lowIdx === hit.lowIdx &&
      this.emitted.highIdx === hit.highIdx;
    if (this.stableRuns >= DTMF_STABLE_WINDOWS && !repeat) {
      this.emitted = hit;
      return DTMF_KEYS[hit.lowIdx][hit.highIdx];
    }
    return null;
  }

  reset(): void {
    this.lastRunCount = 0;
    this.lastHit = null;
    this.stableRuns = 0;
    this.quietRuns = 0;
    this.emitted = null;
  }
}

/** CTCSS sub-audible squelch tone detector (Goertzel sweep + hold). */
export class CtcscDetector {
  private scratch = new Float32Array(TRACE_SIZE);
  private powers = new Float32Array(CTCSS_TUNES.length);
  private decisionRing: (number | null)[] = [];

  /** Returns the held tone reading or null. */
  detect(ring: RingBuffer, rateHz: number): { hz: number; strengthDb: number } | null {
    if (!ring.latest(TRACE_SIZE, this.scratch)) return null;
    let energy = 0;
    for (let i = 0; i < TRACE_SIZE; i++) energy += this.scratch[i] * this.scratch[i];
    const rmsDb = toDb(Math.sqrt(energy / TRACE_SIZE));
    let best = -1;
    let bestPower = 0;
    let farBest = 0;
    if (rmsDb > CTCSS_RMS_FLOOR_DB) {
      // one Goertzel pass over all tones, then cheap scans of the powers
      for (let t = 0; t < CTCSS_TUNES.length; t++) {
        const power = goertzelPower(this.scratch, TRACE_SIZE, CTCSS_TUNES[t], rateHz);
        this.powers[t] = power;
        if (power > bestPower) {
          bestPower = power;
          best = t;
        }
      }
      for (let t = 0; t < CTCSS_TUNES.length; t++) {
        if (Math.abs(CTCSS_TUNES[t] - CTCSS_TUNES[best]) <= CTCSS_FAR_HZ) continue;
        if (this.powers[t] > farBest) farBest = this.powers[t];
      }
    }
    const strengthDb = powerToDb(bestPower, TRACE_SIZE);
    let result: { hz: number; strengthDb: number } | null = null;
    if (
      best >= 0 &&
      strengthDb > CTCSS_FLOOR_DB &&
      bestPower > farBest * CTCSS_SELECTIVITY
    ) {
      result = { hz: CTCSS_TUNES[best], strengthDb };
    }
    this.decisionRing.push(result ? result.hz : null);
    if (this.decisionRing.length > 10) this.decisionRing.shift();
    if (!result) return null;
    const present = this.decisionRing.filter((value) => value !== null);
    if (present.length >= CTCSS_HOLD) {
      const sorted = present.slice().sort((a, b) => (a as number) - (b as number));
      const median = sorted[sorted.length >> 1] as number;
      if (Math.abs(median - result.hz) < 2.0) {
        return { hz: median, strengthDb: result.strengthDb };
      }
    }
    return null;
  }

  reset(): void {
    this.decisionRing = [];
  }
}

/** CW zero-beat aid: strongest steady tone in the 300–3000 Hz band (Hz). */
export class CwTracker {
  private recent: number[] = [];
  private misses = 0;

  detect(spectrumDb: Float32Array, bins: number, rateHz: number): number | null {
    const binHz = rateHz / (2 * bins);
    const from = Math.max(1, Math.ceil(CW_MIN_HZ / binHz));
    const to = Math.min(bins - 1, Math.floor(CW_MAX_HZ / binHz));
    if (to <= from) return null;
    let bestBin = -1;
    let bestDb = -Infinity;
    let sum = 0;
    for (let k = from; k <= to; k++) {
      const value = spectrumDb[k];
      sum += value;
      if (value > bestDb) {
        bestDb = value;
        bestBin = k;
      }
    }
    const average = sum / (to - from + 1);
    if (bestBin < 0 || bestDb - average < CW_SNR_DB) {
      this.misses += 1;
      return null;
    }
    this.misses = 0;
    this.recent.push(bestBin * binHz);
    if (this.recent.length > CW_MEDIAN_DEPTH) this.recent.shift();
    const sorted = this.recent.slice().sort((a, b) => a - b);
    return sorted[sorted.length >> 1];
  }

  reset(): void {
    this.recent = [];
    this.misses = 0;
  }
}

// ---------------------------------------------------------------------------
// Facade
// ---------------------------------------------------------------------------

export interface AfSnapshot {
  rateHz: number;
  rmsDb: number;
  peakDb: number;
  dtmf: string;
  ctcssHz: number | null;
  cwHz: number | null;
}

/** Owns the ring buffer, transforms and decoders for one browser tab. */
export class AfDsp {
  private ring = new RingBuffer(RING_CAPACITY);
  private rateHz = 0;
  private fft = new Fft(TRACE_SIZE);
  private fftWindow = new Float32Array(TRACE_SIZE);
  private spectrumDb = new Float32Array(TRACE_SIZE >> 1);
  private traceDb = new Float32Array(TRACE_SIZE >> 1);
  private traceCol = new Float32Array(TRACE_COLUMNS);
  private traceFresh = false;
  private scopeWindow = new Float32Array(SCOPE_SIZE);
  private wavelets: MorletBank | null = null;
  private waveletScaleRow = new Float32Array(WAVELET_SCALES);
  private meterSum = 0;
  private meterPeak = 0;
  private meterCount = 0;
  private dtmf = new DtmfDetector();
  private ctcss = new CtcscDetector();
  private cw = new CwTracker();
  private dtmfLog: { digit: string; at: number }[] = [];
  private shownCtcssHz: number | null = null;
  private ctcssMisses = 0;
  private shownCwHz: number | null = null;
  private cwMisses = 0;

  /** Feed one decoded playback chunk (~25×/s, ~0.1 ms of work). */
  feed(samples: Float32Array, rateHz: number): void {
    if (samples.length === 0) return;
    // sub-Hz differences (fractional wire measurement vs quantised status)
    // are not a real rate change; a reset here clears the ring faster than
    // it refills and the spectrogram/wavelet displays never recover
    if (Math.abs(rateHz - this.rateHz) > 0.5) this.resetForRate(rateHz);
    this.ring.append(samples);
    for (let i = 0; i < samples.length; i++) {
      const value = samples[i];
      this.meterSum += value * value;
      const abs = value < 0 ? -value : value;
      if (abs > this.meterPeak) this.meterPeak = abs;
    }
    this.meterCount += samples.length;
    const digit = this.dtmf.detect(this.ring, rateHz);
    if (digit !== null) {
      this.dtmfLog.push({ digit, at: Date.now() / 1000 });
      if (this.dtmfLog.length > 16) this.dtmfLog.shift();
    }
  }

  /** Per-draw-tick status (call at ~10 Hz, only while listening). */
  snapshot(): AfSnapshot {
    const rmsDb = this.meterCount
      ? Math.max(toDb(Math.sqrt(this.meterSum / this.meterCount)), -100)
      : -100;
    const peakDb = this.meterCount ? Math.max(toDb(this.meterPeak), -100) : -100;
    this.meterSum = 0;
    this.meterPeak = 0;
    this.meterCount = 0;

    // the trace FFT is computed at most once per draw tick and reused by
    // the spectrum/spectrogram rows and the CW tracker
    this.traceFresh = false;
    if (ringHasWindow(this.ring, TRACE_SIZE)) {
      this.computeTrace();
      const cwHz = this.cw.detect(this.traceDb, this.traceDb.length, this.rateHz);
      if (cwHz !== null) {
        this.shownCwHz = cwHz;
        this.cwMisses = 0;
      } else if (this.shownCwHz !== null && ++this.cwMisses < 15) {
        // hold the pitch reading ~1.5 s through brief dropouts
      } else {
        this.shownCwHz = null;
      }
    } else {
      this.shownCwHz = null;
    }
    const ctcss = this.ctcss.detect(this.ring, this.rateHz);
    if (ctcss) {
      this.shownCtcssHz = ctcss.hz;
      this.ctcssMisses = 0;
    } else if (this.shownCtcssHz !== null && ++this.ctcssMisses < 15) {
      // hold the last reading on the display for ~1.5 s after it fades
    } else {
      this.shownCtcssHz = null;
    }

    const cutoff = Date.now() / 1000 - 60;
    this.dtmfLog = this.dtmfLog.filter((entry) => entry.at >= cutoff);
    return {
      rateHz: this.rateHz,
      rmsDb,
      peakDb,
      dtmf: this.dtmfLog.map((entry) => entry.digit).join(""),
      ctcssHz: this.shownCtcssHz,
      cwHz: this.shownCwHz,
    };
  }

  /** dB-per-column spectrum trace; null until a full window exists. */
  spectrumRow(): Float32Array | null {
    if (!ringHasWindow(this.ring, TRACE_SIZE)) return null;
    if (!this.traceFresh) this.computeTrace();
    this.mapBinsToColumns(this.traceDb);
    return this.traceCol;
  }

  /** Zero-crossing-triggered oscilloscope window; null until enough data. */
  scopeTrace(): Float32Array | null {
    if (!ringHasWindow(this.ring, SCOPE_SIZE)) return null;
    if (!ringLatest(this.ring, SCOPE_SIZE, this.scopeWindow)) return null;
    // trigger on the first rising mid-crossing in the left half so the
    // waveform does not crawl across the screen between frames
    for (let i = 1; i < SCOPE_SIZE / 2; i++) {
      if (this.scopeWindow[i - 1] < 0 && this.scopeWindow[i] >= 0) {
        this.scopeWindow.copyWithin(0, i);
        this.scopeWindow.fill(0, SCOPE_SIZE - i);
        break;
      }
    }
    return this.scopeWindow;
  }

  /** dB-per-column spectrogram row (shares the trace's FFT). */
  spectrogramRow(): Float32Array | null {
    if (!ringHasWindow(this.ring, TRACE_SIZE)) return null;
    if (!this.traceFresh) this.computeTrace();
    this.mapBinsToColumns(this.traceDb);
    return this.traceCol;
  }

  /** dB-per-column wavelet scalogram row; null until ready. */
  waveletRow(): Float32Array | null {
    if (this.wavelets === null) return null;
    if (!ringLatest(this.ring, this.wavelets.requiredSamples, this.fftWindow)) {
      return null;
    }
    this.wavelets.rowInto(this.fftWindow, this.waveletScaleRow);
    this.wavelets.columnRow(this.waveletScaleRow, this.traceCol);
    return this.traceCol;
  }

  /** Display frequency axis of the linear spectrum/spectrogram views (Hz). */
  spectrumHz(): { low: number; high: number } {
    return { low: 0, high: this.rateHz / 2 };
  }

  /** Display frequency axis of the wavelet view (Hz). */
  waveletHz(): { low: number; high: number } | null {
    if (this.wavelets === null) return null;
    return { low: this.wavelets.hzLow, high: this.wavelets.hzHigh };
  }

  private computeTrace(): void {
    ringLatest(this.ring, TRACE_SIZE, this.fftWindow);
    this.fft.runDb(this.fftWindow, this.spectrumDb);
    // peak-decay smoothing gives the trace its classic slow-fall motion
    for (let k = 0; k < this.spectrumDb.length; k++) {
      const value = this.spectrumDb[k];
      const held = Math.max(value, this.traceDb[k] - SPECTRUM_DECAY_DB);
      this.traceDb[k] = Math.max(held, TRACE_DB_MIN);
    }
    this.traceFresh = true;
  }

  private mapBinsToColumns(source: Float32Array): void {
    const bins = source.length;
    for (let x = 0; x < TRACE_COLUMNS; x++) {
      const from = Math.floor((x * bins) / TRACE_COLUMNS);
      const to = Math.max(from + 1, Math.floor(((x + 1) * bins) / TRACE_COLUMNS));
      let peak = -Infinity;
      for (let k = from; k < to && k < bins; k++) {
        if (source[k] > peak) peak = source[k];
      }
      this.traceCol[x] = Number.isFinite(peak) ? peak : TRACE_DB_MIN;
    }
  }

  private resetForRate(rateHz: number): void {
    this.rateHz = rateHz;
    this.ring.reset();
    this.dtmf.reset();
    this.ctcss.reset();
    this.cw.reset();
    this.traceDb.fill(TRACE_DB_MIN);
    this.wavelets = new MorletBank(rateHz);
    this.dtmfLog = [];
    this.shownCtcssHz = null;
    this.ctcssMisses = 0;
    this.shownCwHz = null;
    this.cwMisses = 0;
    // a fresh rate means the old draw-tick state is stale
    this.meterSum = 0;
    this.meterPeak = 0;
    this.meterCount = 0;
  }
}

function ringHasWindow(ring: RingBuffer, size: number): boolean {
  return ring.count >= size;
}

function ringLatest(ring: RingBuffer, size: number, out: Float32Array): boolean {
  return ring.latest(size, out);
}
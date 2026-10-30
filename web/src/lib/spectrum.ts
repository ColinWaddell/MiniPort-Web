/// EB200 spectrum frame codec for the /ws/spectrum socket.
///
/// Frame layout (little-endian, mirrors eb200_server/spectrum.py):
///   <4s B B I d d d>  magic "SPW1" @0, version @4, flags @5,
///                     points u32 @6, timestamp f64 @10,
///                     start_hz f64 @18, step_hz f64 @26  → header 34 bytes
///   <points i16>      levels, dBµV × 10; -32768 = no level
///   <points i16>      peak hold (flag bit 0)

export const FRAME_MAGIC = "SPW1";
export const HEADER_SIZE = 34;
export const NO_LEVEL = -32768;

export interface SpectrumFrame {
  ts: number;
  startHz: number;
  stepHz: number;
  points: number;
  levels: (number | null)[];
  peak: (number | null)[] | null;
}

export function decodeSpectrumFrame(data: ArrayBuffer): SpectrumFrame | null {
  if (data.byteLength < HEADER_SIZE) return null;
  const magic = new TextDecoder().decode(new Uint8Array(data, 0, 4));
  if (magic !== FRAME_MAGIC) return null;
  const view = new DataView(data);
  const version = view.getUint8(4);
  if (version !== 1) return null;
  const flags = view.getUint8(5);
  const points = view.getUint32(6, true);
  const ts = view.getFloat64(10, true);
  const startHz = view.getFloat64(18, true);
  const stepHz = view.getFloat64(26, true);

  let offset = HEADER_SIZE;
  const levels = readLevels(data, offset, points);
  offset += 2 * points;
  const peak = flags & 1 ? readLevels(data, offset, points) : null;
  return { ts, startHz, stepHz, points, levels, peak };
}

function readLevels(data: ArrayBuffer, offset: number, points: number) {
  const raw = new Int16Array(data, offset, points);
  const levels: (number | null)[] = new Array(points);
  for (let i = 0; i < points; i++) {
    levels[i] = raw[i] === NO_LEVEL ? null : raw[i] / 10;
  }
  return levels;
}

export function levelToColor(
  level: number | null,
  min: number,
  max: number,
): [number, number, number] {
  if (level === null) return [18, 20, 26];
  const t = Math.min(Math.max((level - min) / (max - min), 0), 1);
  // dark blue → cyan → yellow → red
  const stops: [number, number, number][] = [
    [10, 12, 60],
    [28, 130, 190],
    [250, 220, 60],
    [245, 90, 60],
  ];
  const pos = t * (stops.length - 1);
  const index = Math.min(Math.floor(pos), stops.length - 2);
  const frac = pos - index;
  const a = stops[index];
  const b = stops[index + 1];
  return [
    Math.round(a[0] + frac * (b[0] - a[0])),
    Math.round(a[1] + frac * (b[1] - a[1])),
    Math.round(a[2] + frac * (b[2] - a[2])),
  ];
}

export function topPeak(
  levels: (number | null)[],
  startHz: number,
  stepHz: number,
): { hz: number; level: number } | null {
  let best: { hz: number; level: number } | null = null;
  levels.forEach((level, index) => {
    if (level === null) return;
    if (best === null || level > best.level) {
      best = { hz: startHz + index * stepHz, level };
    }
  });
  return best;
}

/// Self-normalizing palette range for the waterfall.
///
/// Percentile stretch over a rolling window of recent frames: each frame
/// contributes its 5/50/95th percentiles of level; the palette range comes
/// from the window medians, tightened or widened by the sensitivity value
/// (0–100, 50 = neutral 5/95 stretch). Band changes reset the window so the
/// range re-adapts to the new level distribution instead of mixing bands.
export class WaterfallNormalizer {
  private frames: { min: number; p05: number; p50: number; p95: number; max: number }[] = [];
  private capacity = 60;

  push(levels: (number | null)[]): void {
    const values = levels
      .filter((level): level is number => level !== null)
      .sort((a, b) => a - b);
    if (values.length < 8) return; // too little data for percentiles
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

  /// e.g. a band change: forget the old level distribution entirely.
  reset(): void {
    this.frames = [];
  }

  /// Palette range in dBµV, or null until ~3 frames have arrived.
  range(sensitivity: number): { min: number; max: number } | null {
    if (this.frames.length < 3) return null;
    const clamp01 = Math.min(Math.max(sensitivity / 100, 0), 1);
    const t = clamp01 * 2 - 1; // -1..1 (50 = neutral)

    const median = (pick: (f: Frame) => number) => {
      const values = this.frames.map(pick).sort((a, b) => a - b);
      return values[Math.floor(values.length / 2)];
    };
    const p05 = median((f) => f.p05);
    const p50 = median((f) => f.p50);
    const p95 = median((f) => f.p95);
    const winMin = median((f) => f.min);
    const winMax = median((f) => f.max);

    const span = Math.max(p95 - p05, 1);
    let min: number;
    let max: number;
    if (t >= 0) {
      // tighten toward the median: faint signals pop, noise amplifies
      min = p05 + (p50 - p05) * t * 0.9;
      max = p95 - (p95 - p50) * t * 0.9;
    } else {
      // widen toward the observed extremes: calmer, more headroom
      min = p05 - span * -t * 0.5;
      max = p95 + span * -t * 0.5;
      min = Math.max(min, winMin);
      max = Math.min(max, winMax);
    }
    if (max - min < 1) {
      max = min + 1;
    }
    return { min, max };
  }
}

type Frame = { min: number; p05: number; p50: number; p95: number; max: number };
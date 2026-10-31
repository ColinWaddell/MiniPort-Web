/// Waterfall data model and rendering.
///
/// Rows are exact received frames, not tick-sampled: every frame becomes one
/// row carrying its capture timestamp, and the visible waterfall is always
/// re-rendered from the buffer, so history survives height, density, palette
/// and window-resize changes.

export const WF_MAX_ROWS = 640; // 640 px at 1 px/row density
export const WF_POOLED_COLS = 2048; // render width cap for very long sweeps

const NO_LEVEL = Number.NaN; // buffer representation for "receiver had no level"

export type PaletteName = "eb200" | "gray" | "viridis" | "inferno";

export const PALETTES: Record<
  PaletteName,
  { label: string; stops: [number, number, number][] }
> = {
  eb200: {
    label: "EB200",
    stops: [
      [10, 12, 60],
      [28, 130, 190],
      [250, 220, 60],
      [245, 90, 60],
    ],
  },
  gray: {
    label: "GRAY",
    stops: [
      [18, 18, 18],
      [120, 120, 120],
      [250, 250, 250],
    ],
  },
  viridis: {
    label: "VIRIDIS",
    stops: [
      [68, 1, 84],
      [59, 82, 139],
      [33, 145, 140],
      [94, 201, 98],
      [253, 231, 37],
    ],
  },
  inferno: {
    label: "INFERNO",
    stops: [
      [0, 0, 4],
      [87, 16, 110],
      [188, 55, 84],
      [249, 142, 9],
      [252, 255, 164],
    ],
  },
};

const EMPTY_ROW: Float32Array = new Float32Array(0);

export function levelToColor(
  level: number,
  min: number,
  max: number,
  palette: PaletteName,
): [number, number, number] {
  if (level === NO_LEVEL || !Number.isFinite(level)) return [18, 20, 26];
  const t = Math.min(Math.max((level - min) / (max - min), 0), 1);
  const stops = PALETTES[palette].stops;
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

/// One waterfall row = one received frame (or a synthetic band separator).
export interface WfRow {
  levels: Float32Array; // dBµV per bin, NO_LEVEL where the receiver had none
  ts: number; // capture timestamp from the frame header, seconds
  band: string; // mapping key "startHz/stepHz/points"
  bandChange: boolean; // synthetic separator row marking a band change
}

/// Ring buffer of rows. Capacity rules out any supported geometry
/// (640 rows at 1 px density on a 640 px waterfall).
export class WaterfallBuffer {
  private rows: WfRow[] = [];
  private dts: number[] = []; // recent frame gaps for the cadence estimate
  private capacity = WF_MAX_ROWS;

  get length() {
    return this.rows.length;
  }

  push(levels: (number | null)[], ts: number, band: string): WfRow[] {
    const painted: WfRow[] = [];
    const prev = this.rows.at(-1);
    if (prev && !prev.bandChange && prev.band !== band) {
      // separator row consumes one history slot; it keeps the boundary honest
      const sep: WfRow = {
        levels: EMPTY_ROW,
        ts: prev.ts,
        band: prev.band,
        bandChange: true,
      };
      painted.push(sep);
      this.rows.push(sep);
      this.trim();
    }
    const arr = new Float32Array(levels.length);
    for (let i = 0; i < levels.length; i++) {
      arr[i] = levels[i] === null ? NO_LEVEL : (levels[i] as number);
    }
    if (prev && !prev.bandChange && ts > prev.ts && ts - prev.ts < 60) {
      this.dts.push(ts - prev.ts);
      if (this.dts.length > 24) this.dts.shift();
    }
    const row: WfRow = { levels: arr, ts, band, bandChange: false };
    painted.push(row);
    this.rows.push(row);
    this.trim();
    return painted;
  }

  /// Newest last. Returns the live array — treat as read-only.
  all(): WfRow[] {
    return this.rows;
  }

  private trim(): void {
    while (this.rows.length > this.capacity) this.rows.shift();
  }

  row(index: number): WfRow | null {
    return this.rows[index] ?? null;
  }

  clear(): void {
    this.rows = [];
    this.dts = [];
  }

  /// Median seconds between recent frames, or null before enough data.
  secondsPerRow(): number | null {
    if (this.dts.length < 2) return null;
    const sorted = [...this.dts].sort((a, b) => a - b);
    return sorted[Math.floor(sorted.length / 2)];
  }
}

/// Incremental offscreen renderer: one fixed-width canvas (WF_POOLED_COLS ×
/// WF_MAX_ROWS), shifted down by a whole row (band separators included) each
/// append. A fixed render width means a band change never wipes on-screen
/// history: each row is pooled from its own band's bin count.
/// Row colors are fixed at paint time with the palette range in use then;
/// a full re-render (palette change) repaints all retained rows with the
/// range in use at re-render time.
export class WaterfallRenderer {
  private off = document.createElement("canvas");
  private ctx = this.off.getContext("2d") as CanvasRenderingContext2D | null;
  private poolWidth = 0;
  private rowImg: ImageData | null = null;

  private ensure(): void {
    if (this.rowImg && this.poolWidth > 0) return;
    this.poolWidth = WF_POOLED_COLS;
    this.off.width = this.poolWidth;
    this.off.height = WF_MAX_ROWS;
    this.rowImg = this.ctx?.createImageData(this.poolWidth, 1) ?? null;
  }

  /// Paint a new row at the top; everything else shifts down one row.
  push(row: WfRow, min: number, max: number, palette: PaletteName): void {
    if (!this.ctx || !this.rowImg) return;
    this.ensure();
    const height = this.off.height;
    this.ctx.drawImage(
      this.off,
      0,
      0,
      this.poolWidth,
      height - 1,
      0,
      1,
      this.poolWidth,
      height - 1,
    );
    this.paint(0, row, min, max, palette);
  }

  /// Repaint every retained row at its buffer position (newest at top).
  rerender(
    rows: WfRow[],
    min: number,
    max: number,
    palette: PaletteName,
  ): void {
    if (!this.ctx) return;
    this.ensure();
    this.ctx.clearRect(0, 0, this.poolWidth, WF_MAX_ROWS);
    const newestIndex = rows.length - 1;
    for (let i = newestIndex; i >= 0 && newestIndex - i < WF_MAX_ROWS; i--) {
      this.paint(newestIndex - i, rows[i], min, max, palette);
    }
  }

  private paint(
    y: number,
    row: WfRow,
    min: number,
    max: number,
    palette: PaletteName,
  ): void {
    const ctx = this.ctx;
    const img = this.rowImg;
    if (!ctx || !img) return;
    const data = img.data;
    if (row.bandChange || row.levels.length === 0) {
      for (let c = 0; c < this.poolWidth; c++) {
        const o = c * 4;
        data[o] = 32;
        data[o + 1] = 42;
        data[o + 2] = 58;
        data[o + 3] = 255;
      }
      ctx.putImageData(img, 0, y);
      return;
    }
    const points = row.levels.length;
    const cols = this.poolWidth;
    const stride = points / cols;
    for (let c = 0; c < cols; c++) {
      let level: number;
      if (stride <= 1) {
        // bins are scarcer than columns: repeat the nearest bin
        level = row.levels[Math.min(points - 1, Math.floor((c + 0.5) * stride))];
      } else {
        // pooled columns: max preserves peaks; the buffer stays raw for readout
        const start = Math.floor(c * stride);
        const end = Math.min(points, Math.max(start + 1, Math.ceil((c + 1) * stride)));
        level = NO_LEVEL;
        for (let j = start; j < end; j++) {
          const v = row.levels[j];
          if (Number.isFinite(v) && (level === NO_LEVEL || v > level)) level = v;
        }
      }
      const [r, g, b] = levelToColor(level, min, max, palette);
      const o = c * 4;
      data[o] = r;
      data[o + 1] = g;
      data[o + 2] = b;
      data[o + 3] = 255;
    }
    ctx.putImageData(img, 0, y);
  }

  /// Blit `rowsVisible` rows (newest at top) into the visible waterfall.
  /// `smooth` lets the browser average when the sweep's bins are thinner
  /// than the render columns (nearest-neighbour would flicker thin carriers).
  blit(
    ctx: CanvasRenderingContext2D,
    dx: number,
    dy: number,
    dw: number,
    rowsVisible: number,
    rowHeight: number,
    smooth: boolean,
  ): void {
    if (rowsVisible <= 0 || this.poolWidth <= 0) return;
    ctx.imageSmoothingEnabled = smooth;
    ctx.drawImage(
      this.off,
      0,
      0,
      this.poolWidth,
      rowsVisible,
      dx,
      dy,
      dw,
      rowsVisible * rowHeight,
    );
    ctx.imageSmoothingEnabled = false;
  }

  /// Column in the offscreen for an x position across the plotted rows.
  colAt(x: number, dx: number, dw: number): number {
    const c = Math.floor(((x - dx) / dw) * this.poolWidth);
    return Math.min(Math.max(c, 0), this.poolWidth - 1);
  }

  /// Raw bin index for a pooled column (nearest), for readout.
  binAt(col: number, points: number): number {
    if (points <= 0) return 0;
    const stride = points / this.poolWidth;
    return Math.min(Math.max(Math.round((col + 0.5) * stride - 0.5), 0), points - 1);
  }

  clear(): void {
    if (this.poolWidth > 0) this.ctx?.clearRect(0, 0, this.poolWidth, WF_MAX_ROWS);
  }
}

export const FREQ_TICKS_MAX = 8;

/// Nice frequency ticks spanning [startHz, startHz + spanHz].
export function freqTicks(
  startHz: number,
  spanHz: number,
): { hz: number; label: string }[] {
  const ladder = [
    1e3, 2e3, 5e3, 1e4, 2e4, 5e4, 1e5, 2e5, 5e5, 1e6, 2e6, 5e6, 1e7, 2e7, 5e7,
    1e8,
  ];
  let step = ladder.find((s) => spanHz / s <= FREQ_TICKS_MAX);
  if (step === undefined) {
    step = Math.ceil(spanHz / FREQ_TICKS_MAX / 1e7) * 1e7;
  }
  const dec = step >= 5e7 ? 0 : step >= 1e7 ? 1 : step >= 1e6 ? 1 : step >= 1e5 ? 2 : 3;
  const ticks: { hz: number; label: string }[] = [];
  const epsilon = step * 1e-6;
  for (let hz = Math.ceil(startHz / step) * step; hz <= startHz + spanHz + epsilon; hz += step) {
    ticks.push({ hz, label: (hz / 1e6).toFixed(dec) });
  }
  return ticks;
}

const TIME_TICK_LADDER = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];

export function timeInterval(spanSeconds: number): number {
  return TIME_TICK_LADDER.find((s) => spanSeconds / s <= 6) ?? 3600;
}

export function fmtClock(ts: number): string {
  const d = new Date(ts * 1000);
  const two = (n: number) => String(n).padStart(2, "0");
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}

export function fmtDuration(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 90) {
    const rest = Math.round(seconds % 60);
    return `${minutes} min ${rest ? `${rest} s` : ""}`.trim();
  }
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes % 60} min`;
}

/// Label for a frequency at the resolution the sweep step justifies.
export function fmtFreqMhz(hz: number, stepHz: number): string {
  const dec = stepHz >= 1e6 ? 1 : stepHz >= 1e5 ? 2 : stepHz >= 1e3 ? 3 : 5;
  return (hz / 1e6).toFixed(dec);
}
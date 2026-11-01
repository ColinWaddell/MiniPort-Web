# Engineering notes

Explanatory notes: root causes, design rationale and lessons learned that
are not evident from the source code.

### Morlet scale↔frequency mapping (get the factor of two right)

**Context:** `web/src/lib/af-dsp.ts` — the wavelet scalogram's Morlet
correlators.

**Finding:** Using Torrence & Compo's wavelet-scale formula
`a = (ω0 + √(2 + ω0²))/(2πf)` as the correlator scale puts the transform's
spectral centre at *half* the intended frequency. A 400 Hz tone peaks at a
scale labelled ~843 Hz.

**Reason:** That formula computes the Morlet *Fourier period*
`λ = 4πa/(ω0 + √(2 + ω0²)) ≈ 1.033·a` (a convention including the lobe's
shoulder), not the centre of the analytic lobe. A real-signal correlation
against `ψ(t/a) = e^{iω0τ}e^{−τ²/2}` centres on `ω0/a` rad/s, i.e.
`f = ω0/(2πa)`.

**Action:** For a correlator whose spectral centre must land on `f`, use
`a = ω0/(2πf) ≈ 0.955/f` (seconds) with ω0 = 6. Guard any future wavelet
work with a synthetic-tone peak test (present in the 0.10.0 development
checks: a 400 Hz input must peak within half an octave of 400 Hz).

**Evidence:** The development-time check suite caught the off-by-2× (scale
36 = 843 Hz for a 400 Hz sine); fixed in commit `be89532`, mapping now
`scale = MORLET_W0 / (TAU·freq)` in `web/src/lib/af-dsp.ts`.

### Scrolling scalograms: direct correlators, not FFT-based CWT

**Context:** AF DSP displays added in 0.10.0 (browser-side analysis of the
~8 kHz UDP audio stream).

**Finding:** A scrolling wavelet display consumes one magnitude value per
scale per frame. At that consumption rate, direct dot products against
precomputed wavelets cost ~3–24k multiply-adds per row (48 scales over
50 Hz–2 kHz at the 8 kHz wire rate) — about 100× less than an FFT-based
CWT, which needs one inverse FFT per scale (~3–4 ms).

**Reason:** The FFT-CWT computes the whole time-axis of `|CWT(τ, a)|` per
scale, but a waterfall row only ever displays one instant per scale.
Measured cost with every view running (spectrum, spectrogram, wavelet,
scope, DTMF, CTCSS, meter): ≈ 1 ms per 100 ms tick ≈ 1% of one core
(Node, esbuild-bundled build).

**Action:** Keep the direct correlator design if the scalogram is extended;
prefer it for any per-frame "current value" spectral display at audio
rates. Reach for FFT-based time-frequency analysis only when a full
time–frequency plane per frame is genuinely needed.

**Evidence:** Cost breakdown in the 0.10.0 workpackage (feed-only 0.011 ms
per 40 ms chunk; snapshot+rows ≈ 0.9–1.0 ms per 100 ms tick);
`MorletBank` in `web/src/lib/af-dsp.ts`.
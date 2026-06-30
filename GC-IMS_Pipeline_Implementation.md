# GC-IMS Peak-Finding Pipeline — Implementation Notes

**Version: ver.01 — by Albert Sheng**

**Purpose of this document:** a self-contained, portable record of the working code built for
reading G.A.S. FlavourSpec® GC-IMS `.mea` files, exporting them, and detecting peaks. It is
written so that **any other AI or developer can pick up the project without prior context**.
It documents the file format we reverse-engineered, what each script does, the exact end-to-end
workflow, and — in detail — how peaks are evaluated.

Companion design document: `GC-IMS_Peak_Finding_Workflow.md` (the methodology/architecture blueprint).
This file is the **implementation** counterpart.

- Project root: `J:\GC-IMS-peak`
- Platform: Windows 11, Python 3.14 (`c:\python314\python.exe`)
- Status: raw-data mode working (parse → export → detect). Threshold calibration not yet done.

---

## 0. TL;DR workflow

```powershell
# one-time install (numpy already present)
python -m pip install numpy scipy scikit-image matplotlib

# step 1: parse the .mea, export matrix + CSV + heatmap
python readGAS.py
#   -> results/<name>.csv          (full-res long table: drift, retention, intensity)
#   -> results/<name>.npz          (lossless matrix + axes, ~varies)  <-- feeds step 2
#   -> results/<name>_heatmap.png  (rendered heatmap)

# step 2: detect peaks from the matrix
python peaks.py
#   -> results/<name>_peaks.csv    (peak_id, retention_s, drift_ms, intensity)
#   -> results/<name>_overlay.png  (heatmap with red circles on detected peaks)
#   -> results/<name>_peaks.json   (full per-peak metrics + provenance)
```

Both scripts open a **file-explorer dialog** if you don't pass a path (`readGAS.py` defaults to
`GAS/`, `peaks.py` defaults to `results/`).

---

## 1. Input format: G.A.S. `.mea` (reverse-engineered)

The `.mea` from a G.A.S. **FlavourSpec®** instrument is **not** HDF5 and **not** compressed.
It is:

```
[ ASCII key=value header (~5.5 KB) ][ raw int16 little-endian data block (to EOF) ]
```

### Header
- Plain text, one `key = value` per line, `\n` separated, values often quoted.
- Continues until the first binary (control) byte, then the data block begins.
- Key fields used by the parser:

| Header key | Example | Meaning / use |
|---|---|---|
| `Chunks count` | `8571` | number of retention-time spectra → **rows (RT axis)** |
| `Chunk sample count` | `4500` | drift samples per spectrum → **cols (DT axis)** |
| `Chunk sample rate` | `150 [kHz]` | drift-time digitization rate |
| `Chunk trigger repetition` | `30 [ms]` | IMS spectrum period (= drift window length) |
| `Chunk averages` | `6` | transients averaged per stored chunk |
| `Machine type` | `FlavourSpec®` | metadata |
| `Sample` | `FM-1` | metadata |

### Data block
- `int16` little-endian, **row-major**, shape `(Chunks count, Chunk sample count)`.
- Each row = one retention time's full drift spectrum.
- **Decisive size check** (how we proved dtype/shape):
  `header_size = filesize − (n_rt × n_dt × 2)`. For the reference file:
  `77,144,533 − (8571 × 4500 × 2 = 77,139,000) = 5,533-byte header`. Exact match.

### Axis calibration (derived purely from the header)
- **Drift time (X):** `dt_step_ms = 1 / sample_rate_kHz` → `1/150 = 0.006667 ms` per sample;
  `4500 × 0.006667 ≈ 30 ms` window (matches `Chunk trigger repetition`). Axis = `0 … 29.993 ms`.
- **Retention time (Y):** `rt_step_ms = Chunk averages × Chunk trigger repetition = 6 × 30 = 180 ms`
  per chunk; `8571 × 180 ms ≈ 1542 s ≈ 25.7 min`. Axis = `0 … ~1542 s`.

### Display convention (matches VOCal images and the design doc)
- **X axis = Drift time (DT)**, **Y axis = Retention time (RT)**.
- Heatmaps use `origin="lower"` so retention increases upward.

### Reference file used during development
`GAS/海洋大學 水產養殖系 郭裔培助理教授 鱸魚/260210_103116_FM_1.mea`
(73.6 MB; shape `(8571, 4500)`; intensity `min=-451, max=1444, mean=-18.9, std=58.7`).

> Note: the raw int16 detector signal here is small-magnitude and roughly baseline-centered
> (not the ±32768 full-scale a naive/misaligned read suggests). Correct alignment from
> `header_size` is what yields the realistic values above.

---

## 2. Files in the project

| File | Type | Role |
|---|---|---|
| `gas_utils.py` | shared module | file-picker dialog + path resolution (imported, never run directly) |
| `test_readGAS.py` | script | **probe**: inspect an unknown `.mea`'s structure (no plotting) |
| `readGAS.py` | script | **parse + export**: `.mea` → matrix, CSV, npz, heatmap |
| `peaks.py` | script | **detect**: matrix → peak list, overlay, JSON |
| `results/` | folder | all outputs (auto-created) |
| `GC-IMS_Peak_Finding_Workflow.md` | doc | methodology blueprint (pre-existing) |
| `GC-IMS_Pipeline_Implementation.md` | doc | this file |

Design principle followed throughout (from the blueprint §9/§10): **use library wheels for generic
numeric/image work; write the domain decision logic ourselves.** Generic file reading, smoothing,
flood-fill come from numpy/scipy/scikit-image; prominence and the peak-evaluation logic are ours.

---

## 3. `gas_utils.py` — shared helpers

```python
PROJECT_DIR, GAS_DIR                          # absolute paths
pick_mea_file(title, initialdir, filetypes)   # tkinter file dialog; returns "" on cancel
resolve_input_path(cli_path, title, initialdir, filetypes)
    # if cli_path given -> validate & return; else open dialog; SystemExit on cancel/missing
```

- Uses `tkinter.filedialog`; dialog is forced to front (`-topmost`).
- Defaults: `initialdir=GAS/`, filter `*.mea`. Overridable (peaks.py points it at `results/`,
  filter `*.npz *.mea`).
- If the Python build lacks `tkinter`, it exits with a message telling the user to pass a path.

---

## 4. `test_readGAS.py` — the probe (diagnostic only)

Run this only when handed a **new/unknown** `.mea` to confirm its structure. **Stdlib-only**
(numpy optional). **No plotting, never modifies the file.** Prints to console:

1. File size + raw header hexdump.
2. Compression detection (gzip `1f 8b` / zlib `78 ..` / raw). Auto-decompresses if needed
   (capped at 64 MB read for safety).
3. ASCII header/binary boundary detection + printable-ratio.
4. Parsed `key=value` pairs.
5. Guessed dimension fields (keys containing chunk/point/drift/retention/…).
6. dtype trials (int16/int32/uint16/float32/float64): how many values each yields and whether
   any pair of header dims multiplies to that count (prints ✓ on a match).
7. If numpy present: min/max/mean/std and feasible reshape.

CLI: `python test_readGAS.py [path] [--bytes N] [--dump-header out.txt]`.

> For the reference file this is already done; the format in §1 is the conclusion.

---

## 5. `readGAS.py` — parse + export

**Dependencies:** numpy (required), matplotlib (optional; if missing, heatmap is skipped with a
message and CSV/npz still complete).

### What it does (in order)
1. `resolve_input_path` → pick a `.mea` (dialog defaults to `GAS/`).
2. `read_mea(path)`:
   - reads whole file, `parse_header` extracts the key=value dict,
   - computes `n_rt, n_dt`, validates `header_size = size − n_rt*n_dt*2 ≥ 0`,
   - `np.frombuffer(raw, '<i2', count, offset=header_size).reshape(n_rt, n_dt)`,
   - builds `axes` dict: `drift_ms`, `retention_s`, step sizes, header size.
3. `print_summary` → shape, axis ranges, intensity stats.
4. `export_csv` → always (default long, full resolution).
5. `export_npz` → **default on** (use `--no-npz` to skip).
6. `plot_heatmap` → saves PNG to `results/<name>_heatmap.png` and shows a window
   (unless `--no-show`).

### Outputs (in `results/`, base name = `.mea` name)
- **`<name>.csv`** — default **long/tidy**: header `drift_ms,retention_s,intensity`, one row per
  grid point. Full resolution = `n_rt × n_dt` rows (reference: 38,569,500 rows ≈ 0.7–1 GB).
  Written row-block by row-block via `np.savetxt` so memory stays bounded.
  - `--csv-format wide` → matrix layout (first row = drift times, first col = retention, cells =
    intensity); same points, much smaller file.
  - `--csv-downsample N` → take every Nth point on both axes (N=10 → 1/100 the rows).
- **`<name>.npz`** — `np.savez_compressed` with arrays `intensity` (int16 `(n_rt,n_dt)`),
  `drift_ms`, `retention_s`. **This is the input to `peaks.py`.** Reload:
  ```python
  z = np.load("results/<name>.npz")
  intensity, drift_ms, retention_s = z["intensity"], z["drift_ms"], z["retention_s"]
  ```
- **`<name>_heatmap.png`** — rendered heatmap (X=drift, Y=retention), percentile-clipped contrast.

### Key CLI flags
| Flag | Default | Effect |
|---|---|---|
| `--no-npz` | (npz on) | skip the `.npz` |
| `--csv-format {long,wide}` | long | CSV layout |
| `--csv-downsample N` | 1 | subsample both axes for the CSV |
| `--csv PATH` | `results/<name>.csv` | override CSV path |
| `--cmap NAME` | viridis | colormap (`jet`/`turbo` to mimic VOCal) |
| `--rt-unit {s,min}` | s | retention axis unit |
| `--clip LO HI` | 1 99.5 | percentile contrast clip |
| `--log` | off | `log1p` intensity (tame a dominant RIP) |
| `--save PATH` | `results/<name>_heatmap.png` | heatmap path |
| `--no-show` | show | don't open the blocking window |
| `--figsize WxH` | 8x9 | figure/window size (inches) |
| `--dpi N` | 150 | saved PNG resolution |

### UX details added
- **Logging:** every stage prints `[HH:MM:SS] …` (flushed). The slow CSV write shows a live
  single-line progress bar with % and ETA, so it never looks frozen.
- **Blocking window:** `plt.show()` holds the terminal until the heatmap window is closed
  (use `--no-show` to avoid).
- **Plot text is ASCII-only** (English labels + `_ascii()` filter on metadata) so matplotlib's
  default font never renders missing-glyph boxes.

---

## 6. `peaks.py` — peak detection (the core)

**Dependencies:** numpy, scipy (`gaussian_filter`), scikit-image (`segmentation.flood`),
matplotlib (overlay). Implements workflow steps **[3] seed + [4] measure** — *measure-only*,
i.e. it reports metrics and applies only light, uncalibrated gates. Threshold calibration
(step [5]) is intentionally deferred until a human ground-truth peak list exists.

### Input
- `.npz` from `readGAS.py` (preferred), **or** a `.mea` directly (it calls `readGAS.read_mea`).
- `load_surface(path)` → `(intensity, drift_ms, retention_s, meta)`.

### Pipeline inside `detect_peaks(...)`
1. **Smooth** — `gaussian_filter(intensity, sigma)` (default `sigma=1.0`). Breaks int16 value
   ties / suppresses noise so plateaus don't spawn spurious maxima. `sigma=0` disables.
2. **Floor (sea level)** — `floor = percentile(work, floor_pct)` (default 85th). Only pixels
   above the floor are processed (speed + ignores background). See §7 for why this is safe.
3. **Prominence** — `compute_prominence(work, floor)` union-find persistence (see §7.1).
4. **Prominence gate** — keep peaks with `prominence ≥ max(min_prominence, prom_frac × max_prom)`
   (default `prom_frac=0.02` → 2% of the strongest peak's prominence). Sort by prominence desc.
5. **De-duplicate** — greedy: walking from highest prominence down, drop any peak within
   `min_distance` px (default 3) of an already-kept peak (occupancy grid).
6. **Top-N** (optional) — keep only the N most prominent (`top_n`, default 0 = all).
7. **Measure each surviving peak** — flatness, edge distance, saturation, coordinates (see §7).

### Outputs (in `results/`)
- **`<name>_peaks.csv`** — minimal, human-friendly: `peak_id,retention_s,drift_ms,intensity`.
- **`<name>_overlay.png`** — heatmap with red hollow circles on detected peaks (evidence image;
  resizable via `--figsize`, optional live window via `--show`).
- **`<name>_peaks.json`** — full record: source/machine/sample, timestamp, matrix shape,
  detection params, stats, and a `peaks[]` array where each element has:
  `peak_id, rt_index, dt_index, retention_s, drift_ms, intensity, prominence, flatness,
  flatness_truncated, edge_dist, saturated, rank`.

### Key CLI flags
| Flag | Default | Effect |
|---|---|---|
| `--sigma F` | 1.0 | Gaussian smoothing σ (0 = none) |
| `--floor-pct F` | 85 | floor percentile (lower → more/weaker peaks) |
| `--prom-frac F` | 0.02 | prominence gate as fraction of max prominence (main sensitivity dial) |
| `--min-prominence F` | 0.0 | absolute prominence floor |
| `--min-distance N` | 3 | min pixel spacing between peaks (dedup) |
| `--top-n N` | 0 | keep only N most prominent (0 = all) |
| `--mark-n N` | 0 | overlay marks only top N (0 = all) |
| `--cmap NAME` | viridis | overlay colormap |
| `--figsize WxH` | 8x9 | overlay size (inches) |
| `--dpi N` | 150 | overlay resolution |
| `--show` | off | also open a resizable overlay window |

---

## 7. How peaks are evaluated — itemized

The method is **topographic prominence / persistent homology** on the intensity surface,
plus secondary shape/position metrics. All metrics are **computed by us from the raw matrix**;
none come from the `.mea` file. The numeric *cutoffs* are deliberately not hardcoded — they are
the calibration target.

### 7.1 Prominence (突出度) — primary metric, `compute_prominence()`

**Concept (flooding model):** imagine the intensity map as terrain and lower a water level from
the top. Each new local maximum that emerges is *born* (birth = its height). When two basins meet
at a saddle, the *shorter* peak *dies* (death = saddle height).
`prominence = birth − death = peak height − height of the lowest saddle on the path to a taller peak`.

**Exact algorithm (union-find, 8-connectivity):**
1. Take all pixels with `value > floor`; sort them **high → low**.
2. Maintain a disjoint-set (union-find) over already-processed pixels, with path compression.
   Track per component: `birth_val` (peak height) and `birth_pix` (the maximum's location).
3. For each pixel `p` in descending order, look at its 8 neighbors that are already processed:
   - **No processed neighbor** → `p` is a new local maximum: create a component, `birth=value(p)`.
   - **Exactly one component** → `p` joins it (just a slope pixel).
   - **≥2 components** → they merge at this saddle (`value(p)`): the component(s) with **lower
     birth** die now, each recording `prominence = birth − value(p)`; the highest-birth component
     survives and absorbs the others.
4. Components never merged by the time the sweep ends (including the global maximum) get
   `prominence = birth − floor` (a lower bound).

**Output:** one `(pixel, value, prominence)` per local maximum. Complexity ≈ `O(N log N)`.

**Why the floor mask is valid:** any real peak's relevant saddle (the ridge connecting it to a
taller peak) lies above the background, hence above a low floor. Pixels deep in the baseline only
ever merge "at the sea," so excluding them does not change the prominence of peaks that sit well
above the floor — it only bounds the prominence of peaks near/below the floor (which we don't care
about). This is what makes processing a 38.5 M-pixel image tractable in Python.

**Why prominence (vs. absolute height):**
- Peaks vary wildly in height → prominence is height-independent (an isolated short peak still scores high).
- Two adjacent resolvable peaks have a saddle between them → each keeps its own prominence → counted as two.
- A plateau has no sharp saddle → low, broad prominence → sinks to the bottom of the ranking.
- No peak-shape/width template needed → scale-invariant.

### 7.2 Flatness — `flatness_score()` (workflow §3.2)

Distinguishes a sharp peak from a flat-topped plateau.
1. Crop a window (`win=200` px) around the peak; seed = the peak pixel.
2. `height = value − floor`. Two tolerances: `δ_small = 0.02·height` (top), `δ_large = 0.50·height` (whole).
3. `flood(crop, seed, tolerance=δ)` (scikit-image) grows the connected region whose values are
   within `δ` of the seed → gives a **top region** and a **whole-peak region**.
4. Equivalent radius `r = sqrt(area / π)` for each. `flatness = r_top / r_whole`.
   - Sharp peak → small top region relative to base → **flatness ≈ 0**.
   - Plateau → top region almost as big as base → **flatness ≈ 1**.
5. `flatness_truncated = True` if the whole-region touched the crop edge (radius underestimated →
   value is an estimate; flagged in JSON).

### 7.3 Edge distance — `edge_dist`
`min(r, H−1−r, c, W−1−c)` — distance in pixels from the peak to the nearest matrix border.
Small values flag peaks at the image edge (often artifacts / partially observed). Cheap, exact.

### 7.4 Saturation — `saturated`
`abs(intensity[r,c]) ≥ 32767` (int16 rail). Marks peaks whose true height is unreliable
(clipped). For this dataset values are small, so this rarely triggers, but the flag is kept for
generality / other instruments.

### 7.5 Filtering / ranking (light, uncalibrated)
- **Prominence gate:** `prominence ≥ max(min_prominence, prom_frac × max_prom)`.
- **De-dup:** greedy occupancy grid at `min_distance` px, keeping higher prominence.
- **Top-N:** optional truncation by prominence.
- **`rank`:** 1-based order by prominence after all gates.

> These thresholds are first-pass defaults only. Per the blueprint (§5/§7/§8), the intended
> workflow is to **calibrate** them against an independent human peak list (hit/miss/false-alarm
> scoring), treating the system as a *calibratable instrument* — algorithm fixed, ~5–10
> physically-meaningful thresholds tuned to best match human judgement. That calibration step is
> **not yet implemented** (no ground-truth list yet).

---

## 8. Output schemas (quick reference)

**`<name>_peaks.csv`**
```
peak_id,retention_s,drift_ms,intensity
1,142.20,8.34,1444
...
```

**`<name>_peaks.json`** (abridged)
```json
{
  "source": ".../260210_103116_FM_1.mea",
  "machine": "FlavourSpec®", "sample": "FM-1",
  "detected_at": "2026-06-30T13:40:00",
  "matrix_shape": [8571, 4500],
  "detection_params": {"sigma": 1.0, "floor_pct": 85.0, "prom_frac": 0.02,
                        "min_prominence": 0.0, "min_distance": 3, "top_n": 0},
  "stats": {"floor": ..., "n_raw_maxima": ..., "n_after_prom": ...,
            "n_final": ..., "max_prominence": ..., "prom_threshold": ...},
  "n_peaks": N,
  "peaks": [
    {"peak_id":1,"rt_index":790,"dt_index":1251,"retention_s":142.20,"drift_ms":8.34,
     "intensity":1444,"prominence":...,"flatness":0.08,"flatness_truncated":false,
     "edge_dist":55,"saturated":false,"rank":1}
  ]
}
```

**`<name>.npz`** — keys `intensity (int16 [n_rt,n_dt])`, `drift_ms (float)`, `retention_s (float)`.

---

## 9. Environment / dependencies
- Python 3.14, Windows 11. `numpy` required everywhere; `matplotlib` for any plot; `scipy` +
  `scikit-image` for `peaks.py`.
- Install: `python -m pip install numpy scipy scikit-image matplotlib`
  (use `python -m pip`, not bare `pip`, to hit the interpreter that runs the scripts).
- Verify: `python -c "import numpy, scipy, skimage, matplotlib; print('all OK')"`.
- The PATH warnings pip prints about `*.exe` scripts are harmless (they concern CLI entry points,
  not importable modules).

---

## 10. Known limitations & natural next steps
- **Drift axis is raw ms**, not yet RIP-relative (the `[RIP relative] ≈ 1.00` convention in VOCal).
  Adding it requires locating the RIP band and normalizing — a small calibration step.
- **RIP / tailing masking** (blueprint I4) is not yet applied — the RIP vertical band may be
  detected as peaks. First sanity check on the overlay: is the RIP getting circled?
- **Thresholds are uncalibrated** — defaults only. Needs an independent human peak list to
  calibrate (hit/miss/false-alarm scoring) — the main pending task.
- **Sub-pixel refinement** (blueprint [6], 2D Gaussian centroid) not yet added; coordinates are at
  grid resolution.
- **Deconvolution of co-eluting/fused peaks** (blueprint layer 2, MCR-ALS/PARAFAC2) is out of
  scope here and not implemented.
- Performance: the union-find prominence loop is pure-Python; the floor mask keeps it tractable,
  but very low `--floor-pct` on a 38.5 M-pixel image will be slow.

---

## 11. For an AI picking this up
- Read `GC-IMS_Peak_Finding_Workflow.md` for the *why* (methodology, calibration philosophy,
  A/B modes, two-layer architecture). Read this file for the *what/how* (working code).
- The data is in **raw-data mode** (true intensities), which is strictly more capable than the
  defensive "image mode" the blueprint was written around — colormap inversion, inpainting, etc.
  in the blueprint do **not** apply here.
- The immediate high-value task is **threshold calibration** against a human peak list:
  implement the matching/scoring (rectangular tolerance window per axis, one-to-one assignment,
  hit/miss/false-alarm) and a parameter sweep over the step-[5] cutoffs. Keep detection/metrics
  fixed; only tune the cutoffs. Version the chosen parameters as JSON (instrument-calibration style).

---

*End of document. Reflects the code as built on 2026-06-30.*

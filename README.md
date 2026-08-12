# GC-IMS-PEAK

**Version: 3.1 — by Albert Sheng**

Desktop toolkit for **GC-IMS (Gas Chromatography–Ion Mobility Spectrometry)**
peak detection and compound matching. Reads raw `.mea` files, detects peaks by
topographic prominence, filters them with a pluggable rule engine, and lets you
accept or reject each peak interactively before matching against `.ril` / `.iml`
libraries.

> Version **3.0** — by Albert Sheng

---

## What it does

1. **Parse** a G.A.S. FlavourSpec® `.mea` into an intensity matrix (`readGAS.py`)
2. **Detect** peaks via union-find persistent homology / prominence (`peaks.py`)
3. **Filter** them with rules R001–R006 (`rules.py`)
4. **Select** — click circles on the heatmap or tick the table to keep/drop peaks
5. **Normalise** the axes — drift → relative-to-RIP (x), and retention time →
   **Retention Index** (y) from a batch STD (`calibration.py`, `reference_series.py`)
6. **Identify** — RIP normalisation, K0 conversion, tolerance-window matching
   against GC and IMS libraries (`rip.py`, `dt_convert.py`, `library.py`,
   `match.py`, `identify.py`)

`.mea` files are the original measurements and are **never modified or deleted**
by any part of this project.

### The two axes — which is which

**RI belongs to GC.** It is derived from retention time, and retention time is
chromatography — the *GC* in GC-IMS. The IMS axis is drift time.

| | **GC axis** | **IMS axis** |
|---|---|---|
| Physical quantity | retention time → **RI** (Retention Index) | drift time → `drift_relative` (RIP-relative) or K0 |
| **Display axis** | **y** | **x** |
| Peak fields | `retention_s`, `ri`, `ri_mode` | `drift_ms`, `drift_relative`, `k0_value`, `k0_mode` |
| Library file | `.ril` — **R**etention **I**ndex **L**ibrary | `.iml` — **IM**S **L**ibrary (`Dt[a.u.]` + `DtMode`) |
| Matching (`match.py`) | `match_ri()` / `match_rt()` → `gc_dimension` | `match_drift_rel()` / `match_k0()` → `ims_dimension` |
| Peak-table column | `GC (RI)` | `IMS` |
| Pipeline stage | 4 — `calibration.py` (RT→RI) | 1 — `rip.py`, 2 — `dt_convert.py` |
| Library selection | column-specific (RI depends on the stationary phase) | not column-specific (RIP-relative drift is instrument-level) |

Two things that reliably cause confusion:

- **The display convention is x = drift, y = retention** — the opposite of the
  usual "time on x". This matches VOCal's images and is used consistently in every
  heatmap (with `origin="lower"`, so retention increases upward). Peak coordinates
  in code stay `[rt_index, dt_index]`, i.e. `[row, col]` = `[y, x]`.
- **`.iml` files also carry an `RI` column**, which is why `match_ri()` scans
  `ril_rows + iml_rows`. That is just the IMS library recording a compound's RI as
  well — **RI is still the GC dimension**.

The `GC×IMS` column is a compound that matches on **both** axes (intersected by
CAS). That two-axis agreement is what collapses hundreds of RI-only candidates
down to a few, and it is the most trustworthy identification the pipeline
produces.

### Retention Index (RT→RI, Stage 4)

Selecting a folder silently finds its **STD** run, auto-picks the 6 calibration
peaks (this batch: **C4–C9 2-alkanones**, via the drift-relative homolog
ladder), and fits a `log10(RT)` piecewise-linear curve. Every peak then gets an
RI, and each heatmap's y-axis becomes a **linear Retention Index** axis while the
x-axis stays drift-relative-to-RIP. The calibration is cached per folder and
reused across files. The axis falls back to retention time when no usable STD is
available.

> **⚠ One RI caveat remains.** Compound identity is confirmed (supplier table with
> CAS) and anchors are now assigned from that table's drift values rather than a
> spacing heuristic. Still open: the table's RI may be polar-column data applied to
> this non-polar column, which would shift every RI by a constant (~+300). Peak
> detection and the drift (x) axis are unaffected. Results produced before
> 2026-08-12 carry the old, mis-assigned anchors — **re-run detection**. See
> `ketone_RI_provenance.md`.

---

## Quick start

```bash
python -m pip install -r requirements.txt
python main.py
```

1. **Browse mea folder** → pick a folder of `.mea` files
2. Select a file. If it has been converted before, you are asked whether to
   reuse the existing `.npz` (instant) or re-read the `.mea` (~13 s)
3. **Show Detected Peak Heatmap** → first run takes ~83 s; after that the
   results load from disk
4. Click a circle, or the **On** column, to keep/drop a peak
5. **Rules** → toggle a rule or edit a parameter and watch the circles update

### Command line

```bash
python readGAS.py "path/to/file.mea" --no-show   # → results/<name>.npz + heatmap
python peaks.py   "path/to/file.mea"             # → peaks, maxima cache, backdrop
python peaks.py   results/<name>.npz --bg-only   # rebuild the backdrop only
python identify.py results/<name>_peaks.json     # → _peaks_identified.json
```

---

## How peak selection works

Two mandatory rules run **before** peaks are numbered, and one relative
threshold depends on them:

```
252,629 raw local maxima
   − R004 (RIP band) + R006 (faster than the RIP)   → 101,973 removed
   → prominence ≥ 0.02 × max_prominence             → 31 peaks
   → peak_id 1..31 assigned here (the baseline)
   → optional rules R001/R002/R003/R005 mark peaks, never renumber
```

The order matters. The prominence gate is a *fraction of the strongest peak*,
and the RIP is normally the strongest thing in the image — leaving it in the
candidate set inflated the threshold from 30.9 to 101.0 on the reference file
and silently discarded real analyte peaks. `test/test_select_from_maxima.py`
fails if the mandatory rules are moved back after detection.

A peak rejected by an optional rule is **not removed** — it stays on the canvas
as a grey circle with a grey table row, identical to one you deselected by hand.
Clicking it keeps it anyway, and that override is drawn with a dashed ring.
Your choices persist in `_peaks_state.json`, keyed by matrix coordinates rather
than `peak_id`, which is reassigned whenever the baseline moves.

---

## Project layout

| File | Role |
|------|------|
| `main.py` | Tkinter desktop application |
| `readGAS.py` | `.mea` → intensity matrix, `.npz`, heatmap PNG |
| `peaks.py` | prominence detection, maxima cache, canvas backdrop |
| `rules.py` | rule engine, R001–R006, mandatory-rule enforcement |
| `rip.py` / `dt_convert.py` | RIP normalisation, K0 conversion |
| `calibration.py` | Stage 4 RT→RI: STD anchor selection, log-linear interp, folder resolution + cache |
| `reference_series.py` | pluggable calibration series (ketone / n_alkane / custom) |
| `library.py` / `match.py` / `identify.py` | library readers, matching, integration |
| `peak_with_number.py` | static numbered image for the report (not the canvas) |
| `gas_utils.py` | file-picker helpers |
| `rules_config.json` | per-rule `enabled` + params |
| `test/` | pytest suite (145 tests) |

### Output files (per `.mea`, written to `results/`)

```
<name>.npz                 intensity + axes — the reusable core (~30 MB)
<name>_maxima.npz          all raw local maxima (~2.4 MB) — live rule re-runs
<name>_peaks.json          every baseline peak + rule verdicts + funnel stats
<name>_peaks.csv           compact peak list
<name>_peaks_state.json    your manual keep/drop choices (cannot be regenerated)
<name>_bg.png / _bg.json   circle-free backdrop + plot-area geometry (_bg.json records y_axis: ri|retention_s)
<name>_overlay.png         static image with circles, for VOCal comparison
<name>_heatmap.png         heatmap from readGAS.py
```

Per folder (not per `.mea`): `_folder_calibration.json` caches the resolved
RT→RI calibration so it is reused across every file in the folder.

The full-resolution long-table CSV is **opt-in** (`readGAS.py --write-csv`): it
runs 0.8–1.5 GB per file and nothing in the pipeline reads it, since the `.npz`
holds the same data losslessly at about 1/40 the size.

`results/` and `GAS/` are git-ignored — only `.gitkeep` placeholders are tracked,
so measurement data never gets committed.

---

## Testing

```bash
pytest test/ -q
```

145 tests cover the rule engine and mandatory-rule enforcement, the selection
funnel and its ordering constraint, peak selection state and its coordinate
keying, the state machine, file I/O, peak-table rendering, UI validators, and the
Stage-4 RT→RI calibration (anchor selection, log-linear interp, extrapolate+flag,
pinning, folder resolution/cache, and the linear-RI axis resampling).

---

## Documentation

| Document | Contents |
|---|---|
| `GC-IMS_Identify_Workflow.md` | authoritative spec for stages 1–11 |
| `GC-IMS_Peak_Finding_Workflow.md` | methodology; §5.1 is the as-built flowchart |
| `GC-IMS_Pipeline_Implementation.md` | file formats, CLI flags, output schemas |
| `RT_to_RI_normalization_math.md` | Stage-4 RT→RI interpolation math + checklist |
| `Stage4_code_reference_for_CLI.md` | Stage-4 code skeletons / reference |
| `ketone_RI_provenance.md` | compound identity, where the RI numbers come from, the anchor fix, and §0.0 the old→new retention-axis conversion table |
| ~~`GC-IMS_Matching_Report_v1.pdf`~~ | **superseded (2026-08-12)** — generated before the retention-axis fix, the anchor re-assignment and the RI change. Kept as a record of what was reported on 2026-08-03; do not read its RT/RI as current |
| `UI.md` | UI specification and change log |
| `status.md` | progress tracker and session handoff |
| `Report_Content_Example.md` | what the Batch 8 report should contain |

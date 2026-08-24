# GC-IMS-PEAK

**Version: 3.3 — by Albert Sheng**

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

Selecting a folder silently resolves an RI scale for it and fits a `log10(RT)`
piecewise-linear curve. Every peak then gets an RI, and each heatmap's y-axis
becomes a **linear Retention Index** axis while the x-axis stays
drift-relative-to-RIP. The calibration is cached per folder and reused across
files.

The scale is resolved in four tiers, and which one was used is reported as
`ri_mode` on every peak — a derived value never passes as a measured one:

| tier | source | when |
|---|---|---|
| `batch_own_std` | the folder's own **STD** run, 6 anchors auto-picked from the supplier table's drift values | best; used whenever a usable STD is present |
| `vocal_project_table` | the `RI_Normalization` table inside the folder's own **`.gasprj`** | no STD, but VOCal already calibrated this batch |
| `borrowed_from_registry` | a previous batch on the same instrument + column + method | *currently unreachable — see `status.md` open decision 8* |
| `unavailable` | — | y-axis stays retention time; the GC column falls back to RT matching and **says so** (`GC (RT s)`) |

`.gasprj` ranks above the registry because it is *this* batch's own scale, not
another's. Both rank below the STD, whose anchors are the only ones this project
can name and check.

> **⚠ RI caveats — both still open.**
> - **STD path**: identity is confirmed (supplier table with CAS) and anchors come
>   from that table's drift values, not a spacing heuristic. But the table's RI may
>   be polar-column data applied to this non-polar column, which would shift every
>   RI by a constant (~+300). See `ketone_RI_provenance.md`.
> - **`.gasprj` path**: the file stores only VOCal's *resampled* curve, so which
>   standard produced it — and on which column polarity — is not recoverable. Its
>   short-RT end is VOCal extrapolating below its own first anchor and reaches
>   RI −631, so points below the Kovats floor (methane = 100) are trimmed out of
>   the anchor range; peaks there still get a value, flagged as extrapolated.
>
> Peak detection and the drift (x) axis are unaffected by either. Results produced
> before 2026-08-12 carry the old, mis-assigned anchors — **re-run detection**.

**Library selection follows the RI scale, not the column header.** Matching
compares a peak's RI against a library's RI, so both must be on the same scale —
otherwise a ±5 hit lands on whatever compound's RI in *the other* scale happens
to coincide, which is wrong reliably rather than noisily.
`library.detect_ri_scale_polarity()` votes the calibration's known compounds
against `library_data` on both phase families and picks `.ril` accordingly,
recording header polarity, detected polarity and a `polarity_conflict` flag in
the output. It does **not** choose which RI values are correct — that is a
chemistry question; it only guarantees query and reference share a scale. When
the scale can't be determined (no compound identities, too few probes, or a tie)
it falls back to the header rather than guessing.

---

## Quick start

```bash
python -m pip install -r requirements.txt
python main.py
```

1. **Browse mea folder** → pick a folder of `.mea` files. Samples list first; the
   folder's calibration **STD** is sorted last in grey italic with `· STD`
   appended (identified by its header, not its filename). The status line names
   the RI source once the background resolve finishes.
2. Select a file. If it has been converted before, you are asked whether to
   reuse the existing `.npz` (instant) or re-read the `.mea` (~13 s)
3. **Show Detected Peak Heatmap** → first run takes ~83 s; after that the
   results load from disk
4. Click a circle, or the **On** column, to keep/drop a peak
5. **Rules** → toggle a rule or edit a parameter and watch the circles update
6. **▶** on a table row → the full compound candidate list for that peak

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
| `baseline.py` | opt-in AsLS baseline subtraction along RT (`peaks.py --baseline`) |
| `rules.py` | rule engine, R001–R006, mandatory-rule enforcement |
| `rip.py` / `dt_convert.py` | RIP normalisation, K0 conversion |
| `calibration.py` | Stage 4 RT→RI: STD anchor selection, log-linear interp, folder resolution + cache |
| `reference_series.py` | pluggable calibration series (ketone / n_alkane / custom) |
| `library.py` / `match.py` / `identify.py` | library readers, matching, integration |
| `peak_with_number.py` | static numbered image for the report (not the canvas) |
| `gas_utils.py` | file-picker helpers |
| `rules_config.json` | per-rule `enabled` + params |
| `test/` | pytest suite (238 tests) |

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
RT→RI **and K0** calibrations — both come from the same STD in one pass, so a
batch can never end up with the two resolved from different files. Sidecar
version 2; a version-1 sidecar is treated as stale and recomputed.

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

238 tests cover the rule engine and mandatory-rule enforcement, the selection
funnel and its ordering constraint, peak selection state and its coordinate
keying, the state machine, file I/O, peak-table rendering, UI validators, and the
Stage-4 RT→RI calibration (anchor selection, log-linear interp, extrapolate+flag,
pinning, folder resolution/cache, and the linear-RI axis resampling), plus the
retention-time axis formula and its version marker (`test_rt_axis.py`) and the
AsLS baseline correction (`test_baseline.py`: banded solver against a sparse
reference, peak-height preservation, the measured λ floor, stride invariance).

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

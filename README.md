# GC-IMS-PEAK

Desktop toolkit for **GC-IMS (Gas Chromatography–Ion Mobility Spectrometry)**
peak detection and compound matching. Reads raw `.mea` files, detects peaks by
topographic prominence, filters them with a pluggable rule engine, and lets you
accept or reject each peak interactively before matching against `.ril` / `.iml`
libraries.

> Version **2.1** — by Albert Sheng

---

## What it does

1. **Parse** a G.A.S. FlavourSpec® `.mea` into an intensity matrix (`readGAS.py`)
2. **Detect** peaks via union-find persistent homology / prominence (`peaks.py`)
3. **Filter** them with rules R001–R006 (`rules.py`)
4. **Select** — click circles on the heatmap or tick the table to keep/drop peaks
5. **Identify** — RIP normalisation, K0 conversion, tolerance-window matching
   against GC and IMS libraries (`rip.py`, `dt_convert.py`, `library.py`,
   `match.py`, `identify.py`)

`.mea` files are the original measurements and are **never modified or deleted**
by any part of this project.

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
| `library.py` / `match.py` / `identify.py` | library readers, matching, integration |
| `peak_with_number.py` | static numbered image for the report (not the canvas) |
| `gas_utils.py` | file-picker helpers |
| `rules_config.json` | per-rule `enabled` + params |
| `test/` | pytest suite (91 tests) |

### Output files (per `.mea`, written to `results/`)

```
<name>.npz                 intensity + axes — the reusable core (~30 MB)
<name>_maxima.npz          all raw local maxima (~2.4 MB) — live rule re-runs
<name>_peaks.json          every baseline peak + rule verdicts + funnel stats
<name>_peaks.csv           compact peak list
<name>_peaks_state.json    your manual keep/drop choices (cannot be regenerated)
<name>_bg.png / _bg.json   circle-free backdrop + plot-area geometry
<name>_overlay.png         static image with circles, for VOCal comparison
<name>_heatmap.png         heatmap from readGAS.py
```

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

91 tests cover the rule engine and mandatory-rule enforcement, the selection
funnel and its ordering constraint, peak selection state and its coordinate
keying, the state machine, file I/O, peak-table rendering, and UI validators.

---

## Documentation

| Document | Contents |
|---|---|
| `GC-IMS_Identify_Workflow.md` | authoritative spec for stages 1–11 |
| `GC-IMS_Peak_Finding_Workflow.md` | methodology; §5.1 is the as-built flowchart |
| `GC-IMS_Pipeline_Implementation.md` | file formats, CLI flags, output schemas |
| `UI.md` | UI specification and change log |
| `status.md` | progress tracker and session handoff |
| `Report_Content_Example.md` | what the Batch 8 report should contain |

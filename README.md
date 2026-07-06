# GC-IMS-PEAK

Desktop toolkit for **GC-IMS (Gas Chromatography–Ion Mobility Spectrometry)** peak
detection. Read raw `.mea` files, render heatmaps, detect peaks by topographic
prominence, and inspect the results in a Tkinter desktop UI — including an
**overlay image with numbered peaks** for easy cross-referencing with the table.

> Version **v2** — by Albert Sheng

---

## Features

- **File browser** for a folder of `.mea` files
- **Read** a `.mea` file (`readGAS.py`) → intensity surface + heatmap
- **Detect peaks** (`peaks.py`) → prominence-based peaks, exported as JSON/CSV + overlay
- **Numbered overlay** (`peak_with_number.py`, *new in v2*): red circles **and** the
  peak-id number drawn on each peak, perfectly aligned in matplotlib data
  coordinates. Shown in the main view and the zoom popup.
- **Peak table**: sortable columns (click a header), matrix dimensions and total
  peak count shown in the header
- **Zoom/pan popup** viewer for both heatmap and overlay (click an image)
- **Export** heatmap, overlay, and peaks CSV
- Non-blocking UI (subprocess + threading); single-line status bar

---

## Requirements

```bash
python -m pip install -r requirements.txt
```

- `numpy`, `scipy`, `scikit-image`, `matplotlib` — analysis pipeline
- `pillow` — image display in the desktop UI (`main.py`)
- `tkinter` — ships with standard CPython on Windows (no pip needed)

---

## Quick start

```bash
python main.py
```

1. **Browse Folder** → pick a folder containing `.mea` files
2. Select a file → **Read File** (heatmap appears; the table header shows the
   matrix dimensions, e.g. `8,571 × 4,500 = 38,569,500 points`)
3. **Detect Peaks** → overlay + peak table appear; a moment later the
   **numbered overlay** is rendered and swapped in
4. Click column headers to sort; click an image to open the zoom/pan viewer
5. **Export** heatmap / overlay / peaks CSV as needed

### Command-line use (scripts are independent)

```bash
python readGAS.py  "path/to/file.mea" --no-show      # → results/<name>.npz, _heatmap.png
python peaks.py    "path/to/file.mea"                # → _peaks.json/.csv, _overlay.png
python peak_with_number.py "path/to/file.mea"        # → _overlay_numbered.png
```

---

## Project layout

| File | Role |
|------|------|
| `main.py` | Tkinter desktop application (the UI) |
| `readGAS.py` | Parse `.mea` → intensity surface, save `.npz` + heatmap PNG |
| `peaks.py` | Detect peaks (prominence) → JSON/CSV + overlay PNG |
| `peak_with_number.py` | **v2** — render an overlay with peak-id numbers (display-only) |
| `gas_utils.py` | File-picker helpers |
| `test/` | Pytest suite (54 tests) |
| `UI.md` | UI specification (incl. peak numbering §20 and change log §21) |

### Output files (per `.mea`, written to `results/`)

```
<name>.npz                    intensity + drift/retention axes (from readGAS.py)
<name>_heatmap.png            heatmap image
<name>_peaks.json             full peak data (indices, coords, metrics, params)
<name>_peaks.csv              compact peaks (id, retention_s, drift_ms, intensity)
<name>_overlay.png            heatmap + red circles (from peaks.py)
<name>_overlay_numbered.png   heatmap + red circles + peak-id numbers (v2)
```

> Re-running detection on the same `.mea` **overwrites** that file's outputs in
> place (no versioning). `results/` and `GAS/` contents are git-ignored — only
> `.gitkeep` placeholders are tracked, so the large data never gets committed.

---

## How peak numbering works (v2)

`peaks.py` is intentionally **left unchanged**. Instead, `peak_with_number.py` is a
separate *display-only* helper that:

1. loads the intensity surface from `results/<name>.npz` (reusing
   `peaks.load_surface`) and the peaks from `results/<name>_peaks.json`,
2. draws each peak as a red circle **plus** its `peak_id` number using matplotlib
   `annotate` in **data coordinates** — so the number always sits exactly on its
   circle regardless of axes margins or the `origin="lower"` y-axis orientation,
3. saves `results/<name>_overlay_numbered.png`.

`main.py` invokes it in the background after detection and swaps the numbered
image into both the main canvas and the zoom popup. See
[`UI.md` §20 — Peak Numbering on the Overlay](UI.md) for details.

---

## Testing

```bash
pytest test/ -q
```

54 tests cover the state machine, file operations (JSON/CSV loading + fallback),
peak-table columns/sorting, subprocess/threading, and UI validators.

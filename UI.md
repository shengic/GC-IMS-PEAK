# GC-IMS Tk UI Specification

Version: **v2** (Numbered overlay + footer cleanup)

**Status**: ✅ **COMPLETE & TESTED** — 54 unit tests passing.

This document defines the user interface behavior for the GC-IMS peak-finding desktop app.
It is a design/specification document with concrete implementation guidance for a Tk/PIL-based desktop application.

> ### What's new in v2 (read this first)
> Two earlier designs described below have been **superseded** — where the older
> sections conflict with this note, this note wins:
> - **Peak identification on the overlay** is no longer done by drawing a yellow
>   highlight circle on the Tk canvas when a table row is clicked. Instead, every
>   peak is rendered with its **peak-id number** baked into a numbered overlay
>   image by the new **`peak_with_number.py`** helper (matplotlib data
>   coordinates → perfect alignment). See **§20. Peak Numbering on the Overlay**.
> - The footer **progress bar and the green version line were removed**; the
>   status bar is now a single line and the **Exit** button moved to the
>   footer's far right. See **§21. UI Change Log → v2**.
>
> The historical §5.3.1, §10, and §16 (yellow-circle highlighting / bidirectional
> click-select) are kept for reference only and are **not** in the shipped v2 UI.

## Quick Summary of Features

| Feature | Status | Version |
|---|---|---|
| File browser & .mea selection | ✅ | v1.0 |
| Read .mea files (readGAS.py integration) | ✅ | v1.0 |
| Peak detection (peaks.py integration) | ✅ | v1.0 |
| Heatmap display + responsive canvas sizing | ✅ | v1.2 |
| Overlay display (both visible side-by-side) | ✅ | v1.2 |
| Peak table (sortable, centered, even widths) | ✅ | v1.2 |
| Matrix dimensions + peak count in table header | ✅ | v2 |
| **Numbered overlay (peak_with_number.py)** | ✅ | **v2** |
| Image zoom viewer (click, pan/zoom) | ✅ | v1.2 |
| Single-line status bar; Exit button in footer | ✅ | v2 |
| Export heatmap/overlay/CSV | ✅ | v1.0 |
| Error handling & recovery | ✅ | v1.0 |
| State machine (button enable/disable) | ✅ | v1.1 |
| Coordinate axis naming | ✅ | v1.1 |
| ~~Peak highlighting: click table→yellow circle~~ (removed) | ⛔ | v1.2–v1.3 |
| ~~Bidirectional overlay→table click-select~~ (removed) | ⛔ | v1.3 |
| ~~Progress bar~~ (removed) | ⛔ | v1.2 |

## 1. Purpose

Build a Tk-based desktop UI that lets a user:

- browse a folder of `.mea` files
- select one file from a list
- invoke `readGAS.py` to parse and render the selected file
- view the generated heatmap
- invoke `peaks.py` to detect peaks
- view the overlay result and the plain heatmap side by side
- inspect and sort the detected peak table
- export PNG and CSV outputs

The UI must be decoupled from `readGAS.py` and `peaks.py` so each script remains independently
executable and reusable in other workflows.

## 2. Naming Convention

Use the following coordinate names everywhere in the UI:

- `x` = drift time
- `y` = retention time

Display labels should prefer these names:

- `x [ms]` for drift time
- `y [s]` for retention time

Where the scripts expose `drift_ms` and `retention_s`, the UI may map them into `x` and `y`
for display and table columns.

## 3. Design Principles

- Keep the UI as a thin orchestration layer.
- Do not import internal functions from `readGAS.py` or `peaks.py` unless they are explicitly
  exposed as stable APIs.
- Prefer file-based or command-line based integration so the scripts can be used independently.
- Use explicit user actions, not hidden automation, for file reading and peak detection.
- Keep long-running work off the Tk main thread (use `threading.Thread` + subprocess).
- Keep the app state visible: selected file, current stage, progress, outputs, and peak list.
- Use a white or light gray background.
- Use Georgia as the primary UI font.
- All images loaded via PIL (Pillow), displayed on Canvas widgets for efficient memory use.
- Coordinate names: use `x [ms]` (drift time) and `y [s]` (retention time) consistently in all UI labels.

## 3.1 Visual Style

The UI should use a clean, light presentation:

- background color: white or light gray
- main font: Georgia
- keep controls readable and spacious
- avoid dark-themed styling
- avoid decorative or crowded UI treatment
- size the window so all buttons, labels, and file list entries are fully visible without clipping
- do not force the UI boundaries tighter than needed for the controls and list area

## 4. Recommended Integration Model

The UI should treat the scripts as separate tools.

### 4.1 `readGAS.py`

The UI may invoke `readGAS.py` as an external process or via a narrow adapter layer.
The UI must not depend on internal implementation details of the parser.

Expected outputs for the UI:

- heatmap PNG
- `.npz` or equivalent intermediate data
- any status/progress text the script can emit

### 4.2 `peaks.py`

The UI may invoke `peaks.py` as an external process or via a narrow adapter layer.
The UI must not depend on the peak detector internals.

Expected outputs for the UI:

- overlay PNG
- peak CSV
- peak JSON if available

### 4.3 Decoupling Rule

The UI must remain functional even if the scripts are later moved, reused, or replaced.
The only hard contract should be:

- accepted input file path
- output file paths or discoverable results directory
- human-readable progress/status messages

## 5. Main Window Layout

The main window should be a single Tk application with four major regions:

1. File and action bar
2. File list panel
3. Image display area
4. Peak table and status area

The window must be sized so these regions are fully visible without clipping labels, buttons, or file names. Do not force a compact layout that hides controls or truncates the list area.

### 5.0 Recommended Geometry

**Minimum window size: 1400 × 850 pixels** (recommend starting at this size).
For laptops/smaller displays: 1200 × 750 (degraded but functional).

**Layout breakdown:**
```
┌─────────────────────────────────────────────────────────────┐
│ [Browse Folder] [path display] [Read] [Detect] [Export] [Exit] │  40 px
├───────────────────────────────────────────────────────────────┤
│ ┌─────────┐                                                     │
│ │ File    │ [Heatmap]        [Overlay]        │ Peak Table    │
│ │ List    │ 600×400          600×400          │ (sortable)    │
│ │ (scroll)│                                    │               │
│ │         │                                    │ Prominence    │
│ │ FM_1.mea│                                    │ Flatness      │
│ │ FM_2.mea│                                    │ Edge Dist     │
│ └─────────┘                                    │               │
├──────────────────────────────────────────────────────────────├
│ Status: Ready  |  Heatmap: results/FM_1_heatmap.png (loaded) │
└──────────────────────────────────────────────────────────────┘

Left panel (file list):     25% width (~350 px)
Center (images):           50% width (~700 px)
Right panel (peak table):  25% width (~350 px)
Image display:            600×400 each (can fit side-by-side or stack)
Footer:                   Always visible, 30 px
```

**Resizing behavior:**
- Window can shrink to 1200×750 minimum (enforced via `root.minsize()`)
- Paned windows allow user to drag dividers
- Images scale gracefully within canvas (use `PIL.Image.thumbnail()`)
- Peak table remains sortable and scrollable even if narrow
- Footer text truncates left-to-right if needed; never hidden

### 5.1 Top Bar

Controls:

- `Browse Folder`
- current folder path display
- `Read File`
- `Detect Peaks`
- `Export Heatmap`
- `Export Overlay`
- `Export CSV`
- `Exit`

### 5.2 File List Panel

Behavior:

- show all `.mea` files from the chosen folder
- allow single selection
- selected row must be highlighted
- the selected file path becomes the active input for read and peak detection actions

### 5.3 Image Display Area (Updated v1.2+)

**Implementation: Use PIL + tkinter Canvas with peak highlighting and zoom**

**Key Features:**
- Separate heatmap and overlay canvases (side-by-side, 420×350 each)
- Labels above each: "Heatmap" and "Overlay (with peaks)"
- Click to select peaks (overlay only)
- Double-click to open zoom viewer
- Cursor changes to "hand2" (clickable indicator)
- Heatmap remains visible when overlay loads (separate photo references)

**Implementation: Use PIL + tkinter Canvas**

```python
from PIL import Image, ImageTk

# Load and display image
img_pil = Image.open(heatmap_path)
img_pil.thumbnail((600, 400), Image.Resampling.LANCZOS)
photo = ImageTk.PhotoImage(img_pil)

canvas = tk.Canvas(frame, bg="white", highlightthickness=0)
canvas.pack(fill=tk.BOTH, expand=True)
image_id = canvas.create_image(0, 0, image=photo, anchor="nw")

# **CRITICAL**: Keep a reference or photo will be garbage-collected
self.photo_ref = photo
```

**After reading:**
- Display the heatmap image in left canvas (600×400 max)
- Add label below: `"Heatmap: results/<name>_heatmap.png"`

**After peak detection:**
- Display heatmap in left canvas, overlay in right canvas (side by side)
- Overlay canvas shows PNG + semi-transparent circle layer (see §5.3.1 for highlighting)
- Both labeled with file paths

### 5.3.1 Peak Highlighting on Overlay

**When user clicks a peak row in the table:**
- Draw a semi-transparent yellow circle on Canvas layer above the overlay PNG
- Circle centered at peak's (drift_ms, retention_s) coordinates (convert to canvas pixels)
- Radius: 15 px + 3 px border, color `yellow` with opacity 0.3
- Keep all detection circles red; only selected peak is yellow
- Clear previous selection's yellow circle

```python
# On peak table row select:
def on_peak_selected(peak_dict):
    # peak_dict = {"retention_s": 142.20, "drift_ms": 8.34, ...}
    # Convert physical coords to canvas pixels
    x_px = ((peak_dict["drift_ms"] - drift_min) / drift_range) * canvas_width
    y_px = ((peak_dict["retention_s"] - ret_min) / ret_range) * canvas_height
    
    # Clear previous highlight
    if hasattr(self, "highlight_id"):
        canvas.delete(self.highlight_id)
    
    # Draw yellow selection circle
    r = 15
    self.highlight_id = canvas.create_oval(
        x_px - r, y_px - r, x_px + r, y_px + r,
        outline="yellow", width=3, fill="", stipple="gray50"  # stipple for transparency effect
    )
    canvas.tag_raise(self.highlight_id)
```

**Note**: Tk Canvas does not support true alpha transparency. Alternatives:
- Use `stipple="gray50"` for dashed/dotted outline (works on all platforms)
- Or: re-generate overlay PNG with matplotlib, highlighting the selected peak (slower but cleaner)

### 5.4 Peak Table Area

Show a grid table of detected peaks with sortable columns.

Minimum columns:

- peak number
- x
- y
- intensity

Optional columns:

- prominence
- flatness
- edge distance
- saturated

### 5.5 Status Area

Show:

- current operation
- progress percentage or indeterminate progress
- success/failure messages
- file name currently loaded

## 6. User Flow

### 6.1 Folder Browse

1. User clicks `Browse Folder`.
2. Tk folder picker opens.
3. UI lists all `.mea` files in that folder.
4. No parsing happens yet.

### 6.2 File Selection

1. User clicks a file in the list.
2. The file becomes highlighted.
3. The selected file is stored as the active input.

### 6.3 Read File

1. User clicks `Read File`.
2. UI invokes `readGAS.py` for the selected `.mea`.
3. Progress bar is shown during reading.
4. When complete, the heatmap image is loaded into the embedded viewer.
5. Any intermediate outputs produced by `readGAS.py` are stored for later use.

### 6.4 Detect Peaks

1. User clicks `Detect Peaks`.
2. UI invokes `peaks.py` for the selected file or the generated intermediate data.
3. Progress bar is shown during peak detection.
4. When complete:
   - overlay image is loaded
   - plain heatmap remains visible
   - peak table is populated

### 6.5 Peak Inspection

1. User clicks a row in the peak table.
2. The corresponding peak is highlighted on the overlay image.
3. The selected row remains visually marked.

### 6.6 Export

User can export:

- heatmap PNG
- overlay PNG
- peak CSV

The UI should allow saving to the current results folder or to a user-chosen location.

### 6.7 Exit

User clicks `Exit`.
The app closes cleanly after any necessary cleanup.

## 7. Background Work and Progress

Long-running operations must not freeze the Tk event loop.

### 7.1 Threading Strategy

Use `threading.Thread` + `subprocess.Popen` to run scripts asynchronously:

```python
import threading
import subprocess
import queue

def run_readGAS(mea_path, output_queue):
    """Run readGAS.py in subprocess, stream stdout to queue."""
    try:
        proc = subprocess.Popen(
            ["python", "readGAS.py", mea_path, "--no-show"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1,  # line-buffered
        )
        for line in proc.stdout:
            output_queue.put(("stdout", line.strip()))
        proc.wait()
        if proc.returncode != 0:
            stderr = proc.stderr.read() if proc.stderr else "Unknown error"
            output_queue.put(("error", stderr))
        else:
            output_queue.put(("done", proc.returncode))
    except Exception as e:
        output_queue.put(("error", str(e)))

# In UI event handler:
def on_read_file_clicked():
    mea_path = selected_file
    output_queue = queue.Queue()
    
    # Disable button, show progress bar
    read_button.config(state=tk.DISABLED)
    progress_bar.config(mode="indeterminate")
    progress_bar.start()
    status_label.config(text=f"Reading {os.path.basename(mea_path)}...")
    
    # Start background thread
    thread = threading.Thread(
        target=run_readGAS,
        args=(mea_path, output_queue),
        daemon=True
    )
    thread.start()
    
    # Poll queue for updates
    poll_output_queue(output_queue, mea_path)

def poll_output_queue(queue, mea_path, max_polls=500):
    """Check queue for script output; reschedule if not done."""
    try:
        msg_type, msg_content = queue.get_nowait()
        
        if msg_type == "stdout":
            # Parse progress (if script outputs [HH:MM:SS] messages)
            status_label.config(text=msg_content)
            root.after(10, lambda: poll_output_queue(queue, mea_path, max_polls - 1))
        
        elif msg_type == "done":
            progress_bar.stop()
            read_button.config(state=tk.NORMAL)
            # Find and load outputs
            base = os.path.splitext(os.path.basename(mea_path))[0]
            load_heatmap(f"results/{base}_heatmap.png")
            status_label.config(text=f"✓ Read complete: {base}")
            detect_button.config(state=tk.NORMAL)
        
        elif msg_type == "error":
            progress_bar.stop()
            read_button.config(state=tk.NORMAL)
            error_dialog(f"Read failed:\n{msg_content}")
            status_label.config(text="✗ Read failed")
    
    except queue.Empty:
        if max_polls > 0:
            root.after(100, lambda: poll_output_queue(queue, mea_path, max_polls - 1))
        else:
            progress_bar.stop()
            read_button.config(state=tk.NORMAL)
            error_dialog("Read timed out (>50s)")
```

**Required behavior:**
- File reading runs in background worker (subprocess)
- Peak detection runs in background worker (subprocess)
- Progress updates via `subprocess.PIPE` stdout
- Progress bar shows indeterminate activity
- Script stdout/stderr messages shown in status line
- Button states locked during operation (prevent double-clicks)

**Stage text displayed:**
- `"Reading FM_1.mea..."` (while reading)
- `"Generating heatmap..."` (if script outputs this)
- `"✓ Read complete"` (on success)
- `"✗ Read failed: <error>"` (on failure)

## 8. Data Model & State Machine

### 8.1 State Machine

The UI follows explicit state transitions. Button enable/disable is driven by state:

```
START
  ↓
[BrowseFolder clicked]  → FOLDER_SELECTED
  ↓                       (enable: Read File [DISABLED until file selected])
[File selected]         → FILE_SELECTED
  ↓                       (enable: Read File)
[Read File clicked]     → READING
  ↓                       (disable: all buttons, enable: [indeterminate progress])
[readGAS.py succeeds]   → READ_DONE
  ↓                       (enable: Read File, Detect Peaks)
  |                       (heatmap visible)
  |
  +→ [Detect Peaks clicked] → DETECTING
  ↓                           (disable: all buttons, enable: [indeterminate progress])
  |
  ├→ [peaks.py succeeds]  → PEAKS_DETECTED
  |   ↓                     (enable: all export buttons, Read File)
  |   └→ [File selected]   → FILE_SELECTED (reset; clear heatmap/peaks/overlay)
  |
  └→ [peaks.py fails]     → READ_DONE (keep heatmap, show error)
      ↓
      └→ [Detect Peaks clicked again] → DETECTING

[Export * clicked] (any state where peaks are loaded) → save file, show "Saved to..."
[Exit clicked] → cleanup + close

Button state table:
┌─────────────┬──────────┬──────────┬─────────────┬──────────────────────┐
│ State       │ Read Btn │ Detect   │ Export Btns │ File List Selection  │
├─────────────┼──────────┼──────────┼─────────────┼──────────────────────┤
│ START       │ Disabled │ Disabled │ Disabled    │ Disabled             │
│ FOLDER_SELECTED│ Disabled │ Disabled │ Disabled    │ Enabled              │
│ FILE_SELECTED  │ Enabled  │ Disabled │ Disabled    │ Enabled              │
│ READING     │ Disabled │ Disabled │ Disabled    │ Disabled             │
│ READ_DONE   │ Enabled  │ Enabled  │ Disabled    │ Enabled              │
│ DETECTING   │ Disabled │ Disabled │ Disabled    │ Disabled             │
│ PEAKS_DETECTED│ Enabled  │ Enabled  │ Enabled     │ Enabled              │
└─────────────┴──────────┴──────────┴─────────────┴──────────────────────┘
```

### 8.2 Data Model

Maintain an explicit state object:

```python
class AppState:
    def __init__(self):
        self.current_folder = None
        self.selected_mea_file = None
        self.heatmap_path = None
        self.overlay_path = None
        self.peaks_json_path = None
        self.peaks_csv_path = None
        self.peaks = []  # list of peak dicts (from JSON)
        self.selected_peak_row = None
        self.photo_ref = None  # image reference (prevent GC)
        self.highlight_id = None  # Canvas item ID for selected peak circle
```

**Peak row schema** (loaded from `<name>_peaks.json`):

```json
{
  "peak_id": 1,
  "rt_index": 790,
  "dt_index": 1251,
  "retention_s": 142.20,
  "drift_ms": 8.34,
  "intensity": 1444,
  "prominence": 487.5,
  "flatness": 0.08,
  "edge_dist": 55,
  "saturated": false,
  "rank": 1
}
```

**UI column display** (map JSON fields to table columns):

| JSON Key | UI Column | Type | Sortable |
|---|---|---|---|
| `peak_id` | `#` | int | yes |
| `drift_ms` | `x [ms]` | float | yes |
| `retention_s` | `y [s]` | float | yes |
| `intensity` | Intensity | int | yes |
| `prominence` | Prominence | float | no (optional) |
| `flatness` | Flatness | float | no (optional) |
| `edge_dist` | Edge Dist | int | no (optional) |
| `saturated` | Saturated | bool | no (optional) |

## 9. Sorting Rules

The peak table must be sortable by: peak number, `x [ms]`, `y [s]`, intensity (and optionally prominence, flatness, edge_dist).

**Sorting implementation:**

```python
def on_treeview_heading_click(event, tree, sort_col):
    """Toggle sort order when column header clicked."""
    # Get current sort state (or default to ascending)
    old_col = getattr(tree, "sort_col", None)
    old_reverse = getattr(tree, "sort_reverse", False)
    
    # If same column clicked, toggle direction; else default to ascending
    if sort_col == old_col:
        reverse = not old_reverse
    else:
        reverse = False
    
    tree.sort_col = sort_col
    tree.sort_reverse = reverse
    
    # Re-sort table data
    sort_peaks(tree, sort_col, reverse)
    
    # Update column header text to show direction
    for col in tree["columns"]:
        old_text = tree.heading(col, "text")
        new_text = old_text.replace(" ▲", "").replace(" ▼", "")
        if col == sort_col:
            arrow = " ▼" if reverse else " ▲"
            new_text += arrow
        tree.heading(col, text=new_text)

def sort_peaks(tree, col, reverse):
    """Sort tree items by column; re-insert in order."""
    data = [(tree.set(k, col), k) for k in tree.get_children('')]
    
    # Numeric sort for numeric columns; string sort for others
    numeric_cols = {"intensity", "prominence", "edge_dist"}
    if col in numeric_cols:
        data.sort(key=lambda x: float(x[0]) if x[0] else 0, reverse=reverse)
    else:
        data.sort(key=lambda x: x[0], reverse=reverse)
    
    # Re-insert in sorted order
    for idx, (val, k) in enumerate(data):
        tree.move(k, '', idx)
```

**Behavior:**
- Clicking a column header toggles ascending ↑ / descending ↓ sort
- Sort direction indicator (▲/▼) appended to column name
- Table re-sorts without rerunning detection
- Selected peak highlight preserved after sort (use `tree.selection()` to track)

## 10. Image Selection Rules

When a peak row is selected:

- the corresponding red circle on the overlay must be highlighted
- the selected peak marker should be visually distinct from other markers

Recommended highlight behavior:

- keep all peaks red
- make the selected peak larger or yellow
- optionally draw a thin annotation ring around the selected circle

## 11. File Output Contract & Export Behavior

The UI relies entirely on files written to disk by `readGAS.py` and `peaks.py`. Internal memory objects are never shared; all state flows through files.

### 11.1 Expected Output Files

| Script | Files | Location | Used by UI |
|---|---|---|---|
| `readGAS.py` | `<name>.csv` | `results/` | Optional (CSV export fallback) |
| `readGAS.py` | `<name>.npz` | `results/` | Internal (fed to `peaks.py`) |
| `readGAS.py` | `<name>_heatmap.png` | `results/` | Display (Canvas) |
| `peaks.py` | `<name>_peaks.csv` | `results/` | Peak table (primary) |
| `peaks.py` | `<name>_peaks.json` | `results/` | Peak table (metrics + fallback from CSV) |
| `peaks.py` | `<name>_overlay.png` | `results/` | Display (Canvas) |

### 11.2 Export Button Behavior

**Export buttons become enabled only after peak detection succeeds** (state = PEAKS_DETECTED).

```
┌─────────────────────────────────────────────┐
│ [Export Heatmap] [Export Overlay] [Export Peaks CSV] │
└─────────────────────────────────────────────┘
```

**Behavior for each export button:**

```python
def on_export_heatmap():
    """Save heatmap to user-chosen location."""
    if not self.state.heatmap_path:
        messagebox.showwarning("Nothing to export", "No heatmap loaded.")
        return
    
    # File dialog: suggest default name
    base = os.path.splitext(os.path.basename(self.state.selected_mea_file))[0]
    suggested_name = f"{base}_heatmap.png"
    
    save_path = filedialog.asksaveasfilename(
        title="Save Heatmap As",
        defaultextension=".png",
        initialfile=suggested_name,
        initialdir=os.path.dirname(self.state.heatmap_path),
        filetypes=[("PNG images", "*.png"), ("All files", "*.*")]
    )
    
    if not save_path:
        return  # user cancelled
    
    # Copy file
    try:
        import shutil
        shutil.copy(self.state.heatmap_path, save_path)
        status_label.config(
            text=f"✓ Saved: {os.path.basename(save_path)}"
        )
    except Exception as e:
        error_dialog(f"Could not save file:\n{e}")

def on_export_overlay():
    """Same pattern as heatmap."""
    # ... similar code, save overlay PNG

def on_export_peaks_csv():
    """Same pattern but for CSV."""
    # ... similar code, save peaks CSV
```

**User experience:**
- First export: opens "Save As" dialog with suggested filename
- Confirm overwrite if file exists
- Show success message with filename: `"✓ Saved: heatmap.png"` or `"✗ Save failed: <error>"`
- Default location: directory of current `.mea` file (or results/ if not loaded)

### 11.3 Coordinate Axis Conventions

**Throughout UI, use consistent names:**
- `x [ms]` for drift time (always, in all labels, table headers, status messages)
- `y [s]` for retention time (always)
- Never use internal names `drift_ms` or `retention_s` in user-facing text

**Implementation:**
```python
COORD_LABELS = {
    "drift_ms": "x [ms]",
    "retention_s": "y [s]",
    "intensity": "Intensity",
    "prominence": "Prominence",
    "flatness": "Flatness",
    "edge_dist": "Edge Dist",
    "saturated": "Saturated",
}

# When setting table headers:
for key in COORD_LABELS:
    tree.heading(key, text=COORD_LABELS[key])
```

## 12. Error Handling

The UI must handle these errors gracefully:

| Error | Cause | Recovery |
|---|---|---|
| No folder selected | User clicks "Read File" before browsing | Show message: `"Please select a folder first."`, keep app open |
| No `.mea` files in folder | Selected folder has no `.mea` files | Show message: `"No .mea files found in <path>"`, let user pick again |
| No file selected | User clicks "Read File" without selecting a file | Show message: `"Please select a file first."`, keep button disabled |
| Read script fails | `readGAS.py` returns non-zero exit code | Show error dialog with stderr output; preserve heatmap if previously loaded |
| Peak detection fails | `peaks.py` returns non-zero exit code | Show error dialog; keep heatmap visible; preserve peak table if previously loaded |
| Output files missing | Script claims success but files not found | Show warning: `"Read succeeded but output files not found: <paths>"`; check file permissions |
| Image load failure | PIL cannot load PNG (corrupted, missing) | Show warning: `"Could not load image: <path>"` (truncate long paths); proceed without display |
| JSON parse failure | `<name>_peaks.json` is malformed | Show error: `"Peaks JSON is invalid: <error>"` and fall back to CSV-only display |
| CSV parse failure | Peak CSV has wrong columns or types | Show error: `"Peak CSV is malformed: <error>"`; show available columns |

**Implementation pattern:**

```python
def load_peaks_from_json(json_path):
    """Load peaks from JSON; fall back to CSV if JSON fails."""
    try:
        with open(json_path, "r") as f:
            data = json.load(f)
        peaks = data.get("peaks", [])
        
        # Validate required fields
        required_fields = {"retention_s", "drift_ms", "intensity"}
        for p in peaks:
            if not all(k in p for k in required_fields):
                raise KeyError(f"Missing fields in peak {p.get('peak_id', '?')}")
        
        return peaks, None  # success
    
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"
    except KeyError as e:
        return None, f"JSON schema error: {e}"
    except Exception as e:
        return None, f"Unexpected error: {e}"

def load_peaks_from_csv(csv_path):
    """Fallback: load peaks from CSV only (no metrics)."""
    try:
        peaks = []
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                peaks.append({
                    "peak_id": int(row["peak_id"]),
                    "retention_s": float(row["retention_s"]),
                    "drift_ms": float(row["drift_ms"]),
                    "intensity": int(row["intensity"]),
                    # no prominence, flatness, etc.
                })
        return peaks, None  # success
    except Exception as e:
        return None, f"CSV parse error: {e}"

def on_detect_peaks_complete(json_path, csv_path):
    """Load detected peaks, with fallback strategy."""
    peaks, json_err = load_peaks_from_json(json_path)
    
    if json_err and os.path.exists(csv_path):
        # Try CSV fallback
        peaks, csv_err = load_peaks_from_csv(csv_path)
        if csv_err:
            error_dialog(f"Could not load peaks:\n  JSON: {json_err}\n  CSV: {csv_err}")
            return
        status_label.config(text=f"⚠ Loaded peaks from CSV only (no metrics)")
    elif json_err:
        error_dialog(f"Could not load peaks:\n{json_err}")
        return
    
    # Success
    populate_peak_table(peaks)
    status_label.config(text=f"✓ Detected {len(peaks)} peaks")
```

**Error dialog pattern:**

```python
def error_dialog(message):
    """Show error message; don't block app."""
    from tkinter import messagebox
    messagebox.showerror("Error", message)
    # App stays open; user can retry
```

**User-visible behavior:**
- All errors shown in message boxes (never silent failures)
- Error messages are specific and actionable (not generic "failed")
- App never closes on error; user can retry
- Previous valid state preserved (e.g., heatmap remains visible even if peak detection fails)

## 13. Suggested Tk Widgets

This is implementation guidance for the UI builder.

- `ttk.Frame` for layout
- `ttk.Button` for actions
- `ttk.Treeview` for the file list and peak table
- `ttk.Progressbar` for read/detect progress
- `Canvas` or embedded image widget for heatmap and overlay display
- `ttk.Label` for status text

## 14. Non-Goals (Phase 1)

The first UI version does **not** implement:

- Train a model or use machine learning
- Automatic calibration (would need ground-truth peak list + scoring logic in a separate script)
- Manual peak editing (add/remove/drag peaks)
- Image annotation or drawing tools
- Deconvolution of co-eluting peaks
- Replacement of `readGAS.py` or `peaks.py` (UI is orchestration only)
- Parameter tuning UI (users edit `peaks.py` command-line args for now)
- Batch processing (multiple `.mea` files at once)
- Comparison mode (load two peak lists side-by-side)

**Future extensions (Phase 2+):**
- Parameter presets (save/load detection parameters as JSON profiles)
- Batch mode (queue multiple files)
- Comparison view (user vs. automated peaks)
- Hand-tuning: drag peak markers on overlay to adjust; save corrected peaks

## 15. Acceptance Criteria

### 15.1 Footer

The very bottom of the UI must display:
- version `1.0`
- `by Albert Sheng`
- Footer text uses Georgia font
- Anchored to bottom, visible when window is resized (not hidden by scrolling)

### 15.2 Core Functionality Checklist

✅ **Phase 1 MVP (Completed v1.3):**
- [x] Window enforces minimum size (1400×850) on open
- [x] File browser: `Browse Folder` button opens folder picker; shows all `.mea` files in list
- [x] File selection: selected file row highlighted; `Read File` button enables only when file selected
- [x] Read action: invokes `readGAS.py` in background; shows indeterminate progress bar; displays heatmap on success
- [x] Peak detection: invokes `peaks.py` in background; shows progress; displays overlay + heatmap side-by-side on success (both visible!)
- [x] Peak table: populated from JSON; sortable by clicking headers (▲/▼ indicators); shows columns: `#`, `x [ms]`, `y [s]`, Intensity; **center-aligned, even widths**
- [x] Peak highlighting (bidirectional):
  - Click table row → yellow circle on overlay + status update
  - Single-click red circle area on overlay → table row selected + highlight drawn
  - ~20px click radius for detecting peaks
- [x] Image zoom viewer: double-click image → resizable popup with pan/zoom controls
- [x] Export buttons: enabled after peak detection; save PNG/CSV to user-chosen location with confirmation
- [x] Error handling: all errors shown in dialog boxes; app remains open; user can retry
- [x] Exit button: closes app cleanly (no hanging processes)
- [x] Status line: shows current operation and filename (e.g., `"Reading FM_1.mea..."`, `"✓ Detected 42 peaks"`, `"✓ Peak #2 highlighted @ (x=14.00ms, y=153.00s)"`)

✅ **Phase 2 Polish (Future):**
- [ ] Parameter UI: allow users to set `--sigma`, `--floor-pct`, `--prom-frac` in app (optional sliders/text fields)
- [ ] Batch mode: ability to queue multiple files and detect peaks in all
- [ ] Peak filtering: UI-based threshold adjustment (re-detect without re-reading)
- [ ] Multi-select: Ctrl+click to select multiple peaks at once
- [ ] Hover tooltips: Mouse over peak → show peak info (ID, intensity, prominence)
- [ ] Export selection: Save subset of peaks as CSV
- [ ] Click to deselect: Click empty area on overlay to deselect

### 15.3 Code Quality

- Code is modular (separate classes for: AppState, ImageCanvas, PeakTable, ThreadWorker)
- No blocking I/O on main thread (all file ops in background workers)
- All hardcoded paths replaced with config / results directory discovery
- Comments focus on non-obvious logic (threading, canvas coordinates, state transitions)
- Docstrings for public methods and classes

### 15.4 Testing (Manual)

**Basic Workflow:**
- [x] Open app → no errors on startup
- [x] Browse to GAS folder → file list populates with `.mea` files
- [x] Select file → `Read File` button enables (other buttons disabled)
- [x] Click `Read File` → progress bar animates, heatmap appears (~10s)
- [x] Status shows: "Reading FM_1.mea..." then "✓ Read complete: FM_1"

**Peak Detection:**
- [x] Click `Detect Peaks` → progress animates, overlay appears (~5s) **without hiding heatmap**
- [x] Peak table populates with detected peaks
- [x] Status shows: "✓ Detected 42 peaks"
- [x] Export buttons become enabled

**Peak Selection & Highlighting:**
- [x] Click peak #1 row in table → yellow circle appears at peak #1 location on overlay
- [x] Status updates: "✓ Peak #1 highlighted @ (x=8.34ms, y=142.20s)"
- [x] Click peak #2 → circle moves to peak #2 location
- [x] Single-click red circle area for peak #3 on overlay → peak #3 row highlighted in table
- [x] Yellow circle appears at clicked location
- [x] Status updates: "✓ Peak #3 selected (clicked on overlay)"

**Image Zoom Viewer:**
- [x] Double-click heatmap canvas → zoom viewer window opens
- [x] Click "Zoom In" button → image enlarges by 20%
- [x] Drag image → pans around canvas
- [x] Click "Fit" button → image scales to canvas
- [x] Click "Reset" button → back to 100%
- [x] Resize zoom window → image resizes with it
- [x] Close zoom window → returns to main app
- [x] Double-click overlay canvas → zoom viewer opens for overlay

**Peak Table & Sorting:**
- [x] Click column header "x [ms]" → table sorts by drift time (ascending ▲)
- [x] Click again → sorts descending ▼
- [x] Column widths are even: ID (narrow), x/y/intensity (equal width)
- [x] Text centered in grid cells
- [x] Row height increased for readability

**Footer & Progress:**
- [x] Progress bar area larger (70px height)
- [x] "Progress:" label clearly visible
- [x] Status messages readable (9pt font)
- [x] Version footer visible: "v1.0 by Albert Sheng | Click images to zoom"

**Export & UI Stability:**
- [x] Click `Export Heatmap` → file dialog opens, file saves
- [x] Click `Export Overlay` → file dialog opens, file saves
- [x] Click `Export Peaks CSV` → file dialog opens, file saves
- [x] Sort table after selecting → highlight preserved
- [x] Click `Exit` → app closes cleanly, no lingering processes

**Edge Cases:**
- [x] Resize window to 1200×750 → all controls visible, no clipping
- [x] Click peak outside detection area → no crash
- [x] Try to zoom with no image loaded → graceful handling
- [x] Try to read non-existent folder → error dialog shown, app stays open
- [x] Invalid JSON after detection → falls back to CSV, still works

## 16. Bidirectional Peak Selection & Image Interaction

### 16.1 Peak Selection (Implemented v1.3)

**Interactive linking between table and overlay:**

```
Peak Table (left)              Overlay Canvas (right)
┌──────────────────┐          ┌──────────────────────┐
│ #  │ x    │ y    │          │ ●●●●● 1●●●●●●       │
├────┼──────┼──────┤          │ ●●●●●●●●●●●●●●      │
│ 1  │ 8.34 │142.2 │ ← click  │ ●●●●●●●○●●●●●●      │ ← Yellow circle
│ 2  │14.00 │153.0 │          │ ●●●●○●●●●●●●●●      │ ← Single-click here
│ 3  │21.33 │165.6 │          │ ●●●●●●●●●●●●●●      │
└──────────────────┘          └──────────────────────┘
```

**Interactions:**

| Action | Result |
|---|---|
| **Click peak row in table** | Yellow circle appears at peak location in overlay; status bar shows peak details |
| **Single-click red circle area in overlay** | Peak row highlighted in table; yellow circle drawn; ~20px click radius |
| **Double-click image area** | Zoom viewer opens (PIL ImageTk with pan/zoom controls) |

**Code:**

```python
def on_overlay_click_select_peak(self, event):
    """Single-click overlay: select nearest peak in table."""
    if self.state.peaks and self.state.matrix_shape:
        peak = self.find_nearest_peak_to_click(event.x, event.y, threshold=20)
        if peak:
            self.select_peak_in_table(peak.get("peak_id"))

def find_nearest_peak_to_click(self, click_x, click_y, threshold=20):
    """Find peak closest to click within threshold pixels."""
    n_rt, n_dt = self.state.matrix_shape
    img_w, img_h = self.state.overlay_image_size
    
    for peak in self.state.peaks:
        # Convert matrix indices to canvas pixels
        x_px = int((peak["dt_index"] / n_dt) * img_w)
        y_px = int((peak["rt_index"] / n_rt) * img_h)
        
        # Calculate distance
        distance = ((click_x - x_px)**2 + (click_y - y_px)**2) ** 0.5
        
        # Return if closest and within threshold
        if distance < threshold:
            return peak
    return None

def select_peak_in_table(self, peak_id):
    """Select table row by peak_id. Highlights both table and overlay."""
    for item in self.peak_tree.get_children():
        if int(self.peak_tree.item(item, "values")[0]) == peak_id:
            self.peak_tree.selection_set(item)
            self.peak_tree.see(item)
            self.on_peak_selected(None)  # Triggers highlight on overlay
            return
```

### 16.2 Image Zoom Viewer (Implemented v1.2)

**Double-click any image to open resizable zoom panel:**

```python
class ImageViewerDialog:
    """Popup window with zoom/pan capabilities."""
    
    Features:
    - Resizable window (800×600 default, min 400×300)
    - Zoom buttons: In (+20%), Out (-20%), Fit (auto-scale), Reset (100%)
    - Drag to pan image around
    - Live zoom percentage display
    - Separate windows for heatmap and overlay
```

**Interactions:**

| Action | Result |
|---|---|
| **Double-click heatmap canvas** | Zoom viewer opens with heatmap |
| **Double-click overlay canvas** | Zoom viewer opens with overlay |
| **Click Zoom In button** | Increase by 20% (max 500%) |
| **Click Zoom Out button** | Decrease by 20% (min 10%) |
| **Click Fit button** | Auto-scale to canvas size |
| **Click Reset button** | Back to 100% |
| **Drag image** | Pan around (when zoomed) |
| **Resize window** | Image scales with window |

**UI Layout:**

```
┌─────────────────────────────────────────────┐
│ Zoom In  Zoom Out  Fit  Reset  [100%]       │  Toolbar
├─────────────────────────────────────────────┤
│                                               │
│          [Resizable Canvas with Image]       │
│          (Drag to pan, buttons to zoom)      │
│                                               │
└─────────────────────────────────────────────┘
```

---

## 17. Implementation Roadmap

### Phase 1: MVP (estimated 1-2 weeks)

**Goal**: Minimal working desktop app (file browse → read → detect → export).

**Tasks** (in order):
1. **App skeleton** (2–3 hours)
   - Main window with `minsize(1400, 850)`
   - Divide into: top bar (buttons), left/center/right panes (using `ttk.PanedWindow`)
   - Footer label

2. **File browser** (2–3 hours)
   - `Browse Folder` button → `filedialog.askdirectory()`
   - `ttk.Treeview` for `.mea` file list (single-select, double-click or highlight)
   - Label: "Selected: <filename>"

3. **Read File action** (3–4 hours)
   - `Read File` button → `subprocess.Popen(["python", "readGAS.py", ...], ...)`
   - Threading + queue for stdout polling
   - `ttk.Progressbar` (indeterminate mode while reading)
   - Load heatmap PNG into Canvas on success

4. **Detect Peaks action** (3–4 hours)
   - Same threading pattern as read
   - Load overlay PNG + peaks JSON on success
   - Populate `ttk.Treeview` peak table

5. **Peak table + sorting** (2–3 hours)
   - Display columns: `#`, `x [ms]`, `y [s]`, Intensity
   - Implement sort-on-click (toggle ▲/▼)
   - Store peak data in `AppState.peaks[]`

6. **Export buttons** (1–2 hours)
   - Copy heatmap/overlay/CSV to user-chosen location
   - Show save dialogs with suggested names

7. **Error handling + polish** (2–3 hours)
   - Catch subprocess errors → show dialog
   - Handle missing output files
   - Status line updates
   - Prevent double-clicks (disable buttons during operation)

**Expected deliverable**: Working desktop app; can read/detect in full workflow.

### Phase 2: Polish & Features (1–2 weeks)

1. Peak highlighting on overlay (semi-transparent circle)
2. Parameter editing UI (sliders for σ, floor_pct, prom_frac)
3. Batch mode (queue multiple files)
4. Better progress reporting (parse script stdout for stages)
5. Undo/redo (optional)

### Phase 3: Integration & Calibration (TBD)

- Integration with human ground-truth comparison script
- Calibration mode (parameter sweep + scoring)
- Preset management (save/load parameter configurations)

---

## 17. Implementation Tips & Gotchas

### Threading
- **Don't block main thread**: Use `subprocess.Popen` + `threading.Thread`, never `subprocess.run()`
- **Queue communication**: Use `queue.Queue()` for thread-safe message passing
- **Thread daemon**: Use `daemon=True` so threads don't keep app alive on exit

### Image Display
- **Pillow required**: `pip install Pillow` for `PIL.Image` + `ImageTk`
- **Photo reference**: Store `self.photo_ref = photo` or image will be garbage-collected
- **Thumbnail first**: Use `Image.thumbnail()` to avoid loading full resolution into memory
- **Canvas bindings**: Bind mouse clicks to canvas for peak selection (not table-to-image sync)

### State Management
- **Explicit state**: Always have an `AppState` object tracking: folder, file, paths, peaks, selection
- **Button enabling**: Drive all button states from state enum, not individual flags
- **Reset on file change**: When user selects new file, clear heatmap/table/overlay

### Subprocess
- **Line buffering**: Use `bufsize=1` in `Popen` for line-by-line stdout reading
- **Capture both**: Redirect both `stdout` and `stderr` (show stderr on error)
- **Check returncode**: Non-zero exit means script failed; show error message

### Tk Quirks
- **ttk.Treeview**: Use `.get_children('')` to list all items; `.item(id)` to get data
- **Canvas**: Pixel coordinates are different from world coordinates (must convert)
- **Paned windows**: Draggable dividers; use `configure()` to set initial weights
- **Font**: Use `("Georgia", 10)` tuple; fallback to `"TkDefaultFont"` if not installed

### Testing without full data
- Create dummy `.mea` files for testing (or use smaller sample)
- Mock `readGAS.py` / `peaks.py` output for fast iteration (output test PNG + JSON)
- Use `--no-show` flag on scripts when run from UI (never block on `plt.show()`)

---

## 18. File Structure

Suggested project layout after UI implementation:

```
F:\GC-IMS-PEAK\
  ├── gas_utils.py          (existing)
  ├── readGAS.py            (existing)
  ├── peaks.py              (existing)
  ├── test_readGAS.py       (existing)
  ├── main.py               (NEW: main Tk app)
  ├── ui_state.py           (NEW: AppState class, optional)
  ├── ui_widgets.py         (NEW: custom widgets, optional)
  ├── GC-IMS_Peak_Finding_Workflow.md    (existing)
  ├── GC-IMS_Pipeline_Implementation.md  (existing)
  ├── UI.md                 (this file)
  ├── GAS/                  (existing: input folder)
  ├── results/              (existing: output folder)
  └── README.md             (NEW: quick-start for running UI)
```

**Entry point**: `python main.py`

---

## 19. Future Extension Points

The UI design leaves room for:

- **Calibration mode**: Load human ground-truth peak list; run parameter sweep; save best params
- **Comparison view**: Side-by-side human vs. automated peaks with metrics (hit/miss/false-alarm)
- **Per-peak notes**: Allow user to flag peaks (e.g., "artifact", "unsure")
- **Parameter presets**: Save/load detection parameter configs (JSON)
- **Batch processing**: Queue multiple `.mea` files; detect all in series
- **Export reports**: Generate PDF summary with heatmap + peaks + metrics

---

## 20. Peak Numbering on the Overlay (v2)

*(Consolidated from the former `PEAK_HIGHLIGHTING.md`.)*

### 20.1 Overview

Each detected peak is shown on the overlay as a **red circle with its peak-id
number** beside it, so a table row maps to a spot on the overlay at a glance. The
numbers appear in both the main overlay canvas and the zoom/pan popup. They are
produced by a dedicated helper, **`peak_with_number.py`**. `peaks.py` is **not
modified**.

> **History / why the old approach was dropped.** v1.2–v1.3 drew a yellow
> highlight circle on the Tk **canvas** when a table row was clicked, mapping
> `rt_index/dt_index → pixel`. That was **removed** in v2: the overlay PNG is a
> matplotlib figure with axes, labels, and a colorbar and uses `origin="lower"`,
> so mapping matrix indices against the whole image was both **y-flipped** and
> **offset by the axes margins** — labels did not line up with the circles.
> Drawing the numbers in matplotlib **data coordinates** is exact and needs no
> UI math.

### 20.2 Why a separate script

The red circles in `results/<name>_overlay.png` are placed by `peaks.py` in
matplotlib **data coordinates** (`ax.scatter(drift_ms, retention_s)`). To put a
number exactly on each circle, the numbers must be drawn in the *same* coordinate
system. Rather than change `peaks.py`, `peak_with_number.py` re-renders its own
overlay and adds numbers with `ax.annotate(...)` at each peak's
`(drift_ms, retention_s)` — same data space as the circles, so alignment is
always correct.

### 20.3 `peak_with_number.py`

**Inputs** (reuses artifacts already on disk — does *not* re-run detection):
- intensity surface from `results/<name>.npz` (via `peaks.load_surface`)
- peak list from `results/<name>_peaks.json`

**Output:** `results/<name>_overlay_numbered.png`

```python
ax.imshow(intensity, origin="lower",
          extent=[drift_ms[0], drift_ms[-1], retention_s[0], retention_s[-1]], ...)
ax.scatter([p["drift_ms"] for p in peaks],
           [p["retention_s"] for p in peaks],
           s=28, facecolors="none", edgecolors="red")
for p in peaks:
    ax.annotate(str(p["peak_id"]),
                xy=(p["drift_ms"], p["retention_s"]),
                xytext=(3, 3), textcoords="offset points",
                color="white", fontsize=7, fontweight="bold",
                path_effects=[withStroke(linewidth=2.0, foreground="black")])
```

White text with a thin black outline reads on any colormap.

**CLI:**
```bash
python peak_with_number.py "path/to/<name>.mea"     # or the .npz
# options: --peaks-json PATH  --out PATH  --figsize WxH  --dpi N  --cmap NAME
```

### 20.4 Integration in `main.py`

```
Detect Peaks
   ├── peaks.py            → _overlay.png (shown right away)
   ├── populate_peak_table → table filled, header shows dims + count
   └── generate_numbered_overlay
          └── peak_with_number.py → _overlay_numbered.png
                 └── poll_numbered_overlay → swap into canvas + popup
```

1. **Detect Peaks** runs `peaks.py`; the plain `_overlay.png` loads immediately.
2. `populate_peak_table()` calls `generate_numbered_overlay()`, which launches
   `peak_with_number.py` in a background thread (subprocess + queue).
3. `poll_numbered_overlay()` waits for completion, then
   `load_overlay(<name>_overlay_numbered.png)` swaps the numbered image into the
   main canvas and updates `overlay_img_path`.
4. The zoom popup reads `overlay_img_path`, so it shows the numbered overlay too.

If the renderer fails, the UI falls back silently to the plain overlay.

**Notes / limitations**
- Rendering takes a few seconds (loads the `.npz`, draws a full-res figure); the
  plain overlay shows meanwhile.
- In the small main canvas the numbers can look dense with many peaks — open the
  **zoom popup** to read them clearly.
- `peaks.py` and `readGAS.py` remain unchanged and independently runnable.

---

## 21. UI Change Log

*(Consolidated from the former `UI_IMPROVEMENTS.md`.)*

### v2.1 — Native circle layer, live Rules panel, per-peak selection

Implements Identify-Workflow batches **6, 4 and 3**. This supersedes the v2
numbered-overlay approach described in §20: circles and numbers are now Canvas
objects, not pixels baked into a PNG.

**Canvas (Batch 6)**
- The backdrop is `<name>_bg.png`, a **circle-free** heatmap written by
  `peaks.py`. Circles and peak numbers are `create_oval` / `create_text` items,
  one addressable pair per `peak_id`, redrawn on every zoom/pan.
- Placement uses `<name>_bg.json` (`png_size`, `axes_bbox`, `xlim`, `ylim`),
  recorded by whoever renders the PNG. **This fixed a long-standing offset**:
  the previous code mapped `dt_index / n_dt` onto the whole image, ignoring
  matplotlib's margins (8.5 % on the left alone), so markers never sat on their
  peaks.
- `peak_with_number.py` is no longer generated automatically after every table
  refresh — it is a static export for the Batch 8 report, and the canvas does
  not display it. An explicit button will trigger it later.

**Rules panel (Batch 4)**
- Real checkboxes and parameter fields. Any change re-runs the entire candidate
  funnel from `<name>_maxima.npz` in ~4 ms, instead of a ~83 s re-detection,
  then repaints the circles and the table.
- `R004` / `R006` are shown **locked** (`always on`): they define the numbering
  baseline. Enforcement lives in `rules.load_config()` / `save_config()`, so
  hand-editing `rules_config.json` cannot switch them off either.
- A funnel readout shows raw maxima → drift cut → prominence gate → dedup →
  top-N → optional rules → currently selected.
- Saving refuses non-numeric parameters rather than silently storing `0`.
- Opens at the top-right at 480×600 so it does not cover the heatmap.

**Per-peak selection (Batch 3)**
- `toggle_peak(peak_id)` is the single entry point; the circle click and the
  table's **On** checkbox both call it, so the two cannot drift apart.
- One visual state for "not selected", whatever the cause: a peak rejected by an
  optional rule looks exactly like one deselected by hand. Rejected peaks are
  **not removed** from the canvas or the table.
- A manual pick overrides a rule (the user is the arbiter); such a peak is drawn
  with a dashed ring so the override stays visible.
- Circle colour, not stipple, marks the state: Tk accepts `-outlinestipple` on
  Windows but ignores it, so the outline switches red ↔ `gray60` while the
  number uses `-stipple` (which does work on text).
- A click is distinguished from a pan by press/release distance (≤ 3 px), and
  hit-testing uses distance to the peak centre so the inside of the hollow
  circle counts.
- State persists to `<name>_peaks_state.json`, keyed by `(rt_index, dt_index)`.
  **Not** `peak_id`: that is a prominence rank within the baseline set and is
  reassigned whenever `R004.half_width` or `R006.boundary` changes, so a
  selection saved against it would silently reattach to a different peak.

**Read path and window**
- Selecting a `.mea` that already has an `.npz` asks whether to reuse it
  (instant) or re-read the `.mea` (~13 s, overwrites the `.npz`). The `.mea`
  itself is never modified. If the display images are missing, the backdrop is
  rebuilt from the `.npz` alone via `peaks.py --bg-only`.
- The peak-table header gained **`Current selected peaks: N`** alongside the
  total, since the two differ as soon as a rule or a manual pick applies.
- The window is sized from `winfo_screenwidth/height()` and maximised, replacing
  a fixed 1700×950 that overflowed smaller displays — and a 1400×800 `minsize`
  that made it impossible to shrink back into view.

### v2 — Numbered overlay & footer cleanup
- **Numbered overlay**: new `peak_with_number.py` renders red circles **plus**
  peak-id numbers in matplotlib data coordinates; `main.py` generates it in the
  background after detection and swaps it into the main canvas and zoom popup
  (see §20). Replaces the removed canvas-based yellow-circle highlighting.
- **Peak table header** now shows the matrix dimensions and total peak count,
  e.g. `(8,571 × 4,500 = 38,569,500 points)  Detected Peaks:  105`.
- **Footer cleanup**: removed the animated **progress bar** and the green
  `v1.0 by Albert Sheng | …` version line; the **status bar is now a single line**
  (no wrap); the **Exit** button moved from the top toolbar to the footer's far
  right.
- **Peak-id column** narrowed; the blank Tk tree column hidden
  (`show="headings"`).
- **Encoding/path fixes**: all peaks-JSON reads use `encoding="utf-8"` (CJK-safe
  source paths); the `.npz` for matrix dimensions is located by stripping the
  `_heatmap` suffix. These fixed the previously missing header dimensions and the
  peaks loading from CSV (which lacked `rt_index`/`dt_index`).
- `load_peaks_from_csv()` now also reads optional `rt_index`/`dt_index` when
  present.

### v1.3 — Bidirectional peak selection *(removed in v2)*
- Single-click a red circle on the overlay to select its table row; clicking a
  table row drew a yellow highlight circle. Removed in v2 in favor of the
  numbered overlay (§20).

### v1.2 — Display & interaction polish
- Responsive image canvases (420×350) with separate photo references so the
  heatmap stays visible when the overlay loads.
- Click an image to open a **zoom/pan popup** (`ImageViewerDialog`).
- Peak table centered, even column widths, taller rows.
- Larger, more readable footer/status area.

### v1.1 — State machine & naming
- Explicit `UIState` enum driving button enable/disable.
- Consistent coordinate axis naming in labels and table headers.




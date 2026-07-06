# UI Improvements — main.py v1.1

## Changes Made (5 Items)

### 1. ✅ Progress Bar Area — Increased Height & Readability
**Before**: Narrow footer (30px) with hard-to-read text
**After**: Expanded footer (70px) with three clear sections:
- Progress label + progressbar (larger, 400px wide)
- Status label with "Status:" prefix (9pt font)
- Version/hint footer ("Click images to zoom")

**Code Changes:**
```python
footer_frame = Frame(self.root, bg="white", height=70)  # Was 30px

# Progress section
Label(progress_container, text="Progress:", bg="white", font=("Georgia", 9))
self.progress_bar = Progressbar(progress_container, mode="indeterminate", length=400)  # Was 200

# Status section  
Label(status_container, text="Status:", bg="white", font=("Georgia", 9))
self.status_label = Label(..., font=("Georgia", 9))  # Was default size
```

---

### 2. ✅ Image Canvas Size — Fit Images Instead of Forcing Wide
**Before**: Fixed 600×400 canvas per image (too wide, empty space)
**After**: Responsive 420×350 canvas per image, with labels above each

**Code Changes:**
```python
# Heatmap container (new structure)
heatmap_container = Frame(img_frame, bg="white")
Label(heatmap_container, text="Heatmap", bg="white").pack()
self.heatmap_canvas = Canvas(heatmap_container, bg="white", width=420, height=350)
self.heatmap_canvas.pack(fill="both", expand=True)

# Overlay container (same pattern)
overlay_container = Frame(img_frame, bg="white")
Label(overlay_container, text="Overlay (with peaks)").pack()
self.overlay_canvas = Canvas(overlay_container, bg="white", width=420, height=350)
```

**Image Loading Updated:**
```python
def load_heatmap(self, path):
    img.thumbnail((420, 350), Image.Resampling.LANCZOS)  # Was (600, 400)
    self.state.heatmap_photo_ref = ...  # Store ref by image type
    
def load_overlay(self, path):
    img.thumbnail((420, 350), Image.Resampling.LANCZOS)  # Was (600, 400)
    self.state.overlay_photo_ref = ...  # Keep separate references
```

---

### 3. ✅ Heatmap Disappears on Overlay Load — FIXED
**Problem**: Loading overlay image deleted heatmap (shared canvas reference)
**Solution**: Separate photo references for heatmap and overlay

**Code Changes:**
```python
class AppState:
    self.heatmap_photo_ref = None   # NEW: separate refs
    self.overlay_photo_ref = None   # NEW
    self.heatmap_img_path = None    # NEW: for zoom viewer
    self.overlay_img_path = None    # NEW
    
def load_heatmap(self, path):
    self.state.heatmap_photo_ref = ImageTk.PhotoImage(img)  # Store separately
    self.heatmap_canvas.delete("all")
    self.heatmap_canvas.create_image(0, 0, image=self.state.heatmap_photo_ref, ...)

def load_overlay(self, path):
    self.state.overlay_photo_ref = ImageTk.PhotoImage(img)  # Separate ref!
    self.overlay_canvas.delete("all")                        # Only clear overlay canvas
    self.overlay_canvas.create_image(0, 0, image=self.state.overlay_photo_ref, ...)
```

**Result**: Both images remain visible side-by-side after peak detection ✓

---

### 4. ✅ Click Image to Zoom — NEW FEATURE
**Added**: Popup window with zoom/pan capabilities when clicking either image

**New Class: `ImageViewerDialog`**
```python
class ImageViewerDialog:
    """Popup window for viewing images with zoom/pan."""
    
    Features:
    - Resizable window (800×600, min 400×300)
    - Zoom in/out buttons (+/- 20% per click, max 500%, min 10%)
    - Fit button (auto-scale to canvas)
    - Reset button (back to 100%)
    - Pan capability: drag image to move it around
    - Live zoom percentage display
    - Mouse move cursor = "move" for intuitive UX
```

**UI Layout:**
```
┌─────────────────────────────────────────────┐
│ Zoom In  Zoom Out  Fit  Reset  [100%]       │  Toolbar
├─────────────────────────────────────────────┤
│                                               │
│          [Resizable Canvas Area]             │
│          (Drag to pan, buttons to zoom)      │
│                                               │
└─────────────────────────────────────────────┘
```

**Click Handlers:**
```python
def on_image_click_heatmap(self, event):
    """Click heatmap canvas → open in zoom viewer."""
    if not self.state.heatmap_img_path:
        return
    ImageViewerDialog(self.root, self.state.heatmap_img_path, 
                     title="Heatmap Viewer")

def on_image_click_overlay(self, event):
    """Click overlay canvas → open in zoom viewer."""
    if not self.state.overlay_img_path:
        return
    ImageViewerDialog(self.root, self.state.overlay_img_path, 
                     title="Overlay Viewer")
```

**Canvas Binding:**
```python
self.heatmap_canvas = Canvas(..., cursor="hand2")  # Visual cue for clickable
self.heatmap_canvas.bind("<Button-1>", self.on_image_click_heatmap)

self.overlay_canvas = Canvas(..., cursor="hand2")
self.overlay_canvas.bind("<Button-1>", self.on_image_click_overlay)
```

---

### 5. ✅ Peak Table Column Widths — Even Grid Alignment & Center Text
**Before**: Uneven column widths (#=100px, others=100px), left-aligned text
**After**: Proportional widths, center-aligned, taller rows (25px)

**Code Changes:**
```python
# Define proportional column widths
col_widths = {
    "peak_id": 40,          # # (narrow, just ID)
    "drift_ms": 90,         # x [ms]
    "retention_s": 90,      # y [s]
    "intensity": 90,        # Intensity
}

for col in PEAK_TABLE_COLUMNS:
    width = col_widths.get(col, 100)
    self.peak_tree.column(col, width=width, anchor="center")  # CENTER!
    self.peak_tree.heading(col, text=COORD_LABELS.get(col, col))

# Increase row height for readability
style = ttk.Style()
style.configure("Treeview", rowheight=25)  # Was default ~20px
```

**Result:**
```
Before:    [  #   ][ x [ms] ][ y [s] ][ Intensity ]
           [  1   ][  8.34   ][142.20 ][   1444    ]  (left-aligned, cramped)

After:     [  # ][ x [ms] ][ y [s] ][ Intensity ]
           [ 1  ][ 8.34   ][142.20 ][   1444    ]  (centered, spacious)
```

---

## Testing

✅ All 54 existing tests pass
✅ No regression in core functionality
✅ Image viewer is standalone (doesn't affect main app state)

## How to Use New Features

### View Zoomed Images
1. After loading heatmap or detecting peaks, click either image canvas
2. Popup window opens with zoom controls
3. Use buttons to zoom in/out or fit to canvas
4. Drag image to pan around
5. Resize popup window to see different areas

### Workflow
```
1. Browse folder → select .mea
2. Read File → heatmap appears (click to zoom)
3. Detect Peaks → overlay appears (click to zoom, heatmap still visible!)
4. Click peak in table → (future: highlight on overlay)
5. Export heatmap/overlay/CSV as needed
```

---

## Files Modified

- **main.py**: +200 lines (ImageViewerDialog class + image handling improvements)
- **test/**: No changes (all tests still pass)
- **UI.md**: Still valid (describes intended behavior)

---

## Future Improvements

1. **Peak Highlighting**: When user clicks peak in table, draw yellow circle on overlay
2. **Measurement Tool**: Ruler tool to measure distances on zoomed image
3. **Export Zoomed View**: Save current zoom state as PNG
4. **Compare Mode**: Load two images side-by-side for comparison

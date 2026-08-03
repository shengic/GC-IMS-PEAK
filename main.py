"""
main.py  —  GC-IMS Desktop UI (Tk)
Version: 3.0 — by Albert Sheng

A desktop application for browsing, reading, and analyzing GC-IMS .mea files.
Workflow: browse folder → select .mea (auto-read) → show detected peaks → inspect.

Changelog:
  2.1 (Identify Workflow Batches 3, 4, 6)
      - Batch 6: peak circles and numbers are native Canvas objects drawn over a
        circle-free _bg.png, placed via the recorded plot-area geometry. This
        also fixed a long-standing offset — the old code mapped peaks onto the
        whole PNG, ignoring matplotlib's margins.
      - Batch 4: Rules panel is live. Toggling a rule or editing a parameter
        re-runs the whole candidate funnel from _maxima.npz in ~4 ms instead of
        re-detecting for ~83 s. R004/R006 shown locked ("always on").
      - Batch 3: toggle_peak() shared by circle clicks and the On checkbox.
        A peak rejected by a rule and one deselected by hand look identical
        (stippled number, grey row); a manual pick overrides the rule and is
        marked with a dashed ring. State persists to _peaks_state.json keyed by
        (rt_index, dt_index), not peak_id, which is reassigned when the
        mandatory-rule baseline moves.
      - Selecting a .mea offers to reuse an existing .npz rather than re-reading
        it; the display background can be rebuilt from the .npz alone.
      - Window is sized from the actual screen instead of a fixed 1700x950.
  1.1 (Identify Workflow Batch 1)
      - Toolbar redesigned per workflow §第八階段:
          Browse Folder | View Original Heatmap | Show Detected Peaks | Rules | Generate Report
      - "Read File" button removed; auto-reads on file selection
      - "Detect Peaks" renamed "Show Detected Peaks"
      - Export buttons removed (will be replaced by Generate Report in Batch 8)
      - Added Settings menu with "Browse Library Data..." (persists to ui_settings.json)
      - Rules / Generate Report currently open placeholder dialogs (Batches 4 / 8)

Usage:
    python main.py
"""

import datetime
import json
import os
import queue
import subprocess
import sys
import threading
from enum import Enum
from pathlib import Path
from tkinter import (
    filedialog, messagebox, ttk, Canvas, Frame, Label, Tk, Text, END,
    Toplevel, Menu, BooleanVar, StringVar, TclError,
)
from tkinter.ttk import Treeview, PanedWindow

import numpy as np
from PIL import Image, ImageTk

import calibration
import library
import peaks as peaks_mod
import rip as rip_mod
import rules

# 第四階段 RT→RI：整批 STD 校準的參照系列（本專案 STD 已確認為 C4–C9 甲基酮，
# RI 值為借用（見 methyl_ketone_RI_provenance.md），provenance 全程帶 assumed_unverified）
RI_SERIES = "methyl_ketone"

RULES_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "rules_config.json")


# ============================================================================== #
# UI Settings (persisted to ui_settings.json next to main.py)
# ============================================================================== #

SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "ui_settings.json"
)


def load_settings():
    """Read ui_settings.json. Missing/corrupt → empty dict."""
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_settings(settings):
    """Write settings dict to ui_settings.json."""
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except OSError as e:
        # Non-fatal — settings just won't persist this session
        print(f"[warning] could not save ui_settings.json: {e}", file=sys.stderr)

# ============================================================================== #
# Constants & Enums
# ============================================================================== #

COORD_LABELS = {
    "peak_id": "#",
    "drift_ms": "Drift [ms]",
    "drift_relative": "Drift rel. RIP",
    "retention_s": "Retention Time [s]",
    "ri": "RI",
    "intensity": "Intensity",
    "prominence": "Prominence",
    "flatness": "Flatness",
    "edge_dist": "Edge Dist",
    "saturated": "Saturated",
    # Batch 2: identification columns
    "on": "On",
    "gc_ims": "GC×IMS",
    "gc": "GC (RI)",
    "ims": "IMS",
    "trigger": "▶",
}

# Full column layout per workflow §第八階段 draft.12 (drift_relative inserted
# after drift_ms per this session's decision). Match values (gc_ims/gc/ims) are
# placeholders "—" until Batch 5 wires identify.py into main.py.
PEAK_TABLE_COLUMNS = (
    "peak_id", "drift_ms", "drift_relative", "retention_s", "ri", "intensity",
    "on", "gc_ims", "gc", "ims", "trigger",
)

# Checkbox glyphs for the "On" column (Treeview has no native checkbox widget)
CHECK_ON = "☑"
CHECK_OFF = "☐"
TRIGGER_ACTIVE = "▶"
TRIGGER_DIM = " "


class UIState(Enum):
    """State machine states."""
    START = 0
    FOLDER_SELECTED = 1
    FILE_SELECTED = 2
    READING = 3
    READ_DONE = 4
    DETECTING = 5
    PEAKS_DETECTED = 6
    ERROR = 7


# ============================================================================== #
# App State Management
# ============================================================================== #

class AppState:
    """Centralized app state (file paths, peaks, selections)."""

    def __init__(self):
        self.current_folder = None
        self.selected_mea_file = None
        self.heatmap_path = None
        self.overlay_path = None
        self.heatmap_img_path = None
        self.overlay_img_path = None
        self.peaks_json_path = None
        self.peaks_csv_path = None
        self.peaks = []
        self.detection_meta = None  # {"params":..., "stats":...} for Rules panel
        # Batch 6: circles are native Canvas objects, not baked-in pixels
        self.bg_img_path = None       # circle-free heatmap used as canvas backdrop
        self.canvas_geometry = None   # where the plot area sits inside bg_img
        self.peak_items = {}          # peak_id -> {"oval": id, "text": id}
        self.peak_state = {}          # peak_key -> user_active (sidecar)
        self.maxima = None            # _maxima.npz contents (live rule re-selection)
        self.rules_config = None      # live, panel-editable copy
        self.selected_peak_row = None
        self.heatmap_photo_ref = None
        self.overlay_photo_ref = None
        self.highlight_id = None
        self.matrix_shape = None  # (n_rt, n_dt) from JSON
        self.overlay_canvas_size = None  # (width, height) of canvas
        self.overlay_image_size = None  # (width, height) of actual image
        # 第四階段：本資料夾的 RT→RI 校準表（選資料夾時背景解析一次，跨檔案共用）
        self.ri_calibration = None
        self.ri_calibration_folder = None
        # 第十階段：載入一次的比對庫 (ril_rows, iml_rows, select_info)，跨峰共用
        self.library_rows = None
        self.library_unavailable = False   # 記住庫載入失敗，避免每次重繪重試
        # 每檔案的比對結果快取，key=(rt_index, dt_index)，換規則重繪時不必重算
        self.match_cache = {}

        # Persisted settings (ui_settings.json)
        self.settings = load_settings()
        self.library_dir = self.settings.get("library_dir")   # None → resolve chain

        # NOTE: the in-place zoom/pan fields used to be declared here too, but
        # nothing ever read them — the canvas code uses the identically named
        # attributes on GCIMSApp. Two sets of the same names is how they came
        # to be initialised in neither place; they now live on GCIMSApp only.

    def reset_after_file_selection(self):
        """Clear heatmap/peaks/overlay when user selects a new file."""
        self.heatmap_path = None
        self.overlay_path = None
        self.heatmap_img_path = None
        self.overlay_img_path = None
        self.peaks_json_path = None
        self.peaks_csv_path = None
        self.peaks = []
        self.detection_meta = None
        self.bg_img_path = None
        self.numbered_overlay_path = None
        self.canvas_geometry = None
        self.peak_items = {}
        self.peak_state = {}          # peak_key -> user_active (sidecar)
        self.maxima = None
        self.selected_peak_row = None
        self.heatmap_photo_ref = None
        self.overlay_photo_ref = None
        self.highlight_id = None
        self.match_cache = {}          # 換檔案 → 比對快取失效


# ============================================================================== #
# Subprocess & Threading Helpers
# ============================================================================== #

def run_subprocess(cmd, output_queue):
    """Run subprocess; stream stdout/stderr to queue."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
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


# ============================================================================== #
# File Operations
# ============================================================================== #

def load_peaks_from_json(json_path):
    """Load peaks from JSON; return (peaks_list, error_msg)."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        peaks = data.get("peaks", [])
        required_fields = {"retention_s", "drift_ms", "intensity"}
        for p in peaks:
            if not all(k in p for k in required_fields):
                raise KeyError(f"Missing fields in peak {p.get('peak_id', '?')}")
        return peaks, None
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"
    except KeyError as e:
        return None, f"JSON schema error: {e}"
    except Exception as e:
        return None, f"Unexpected error: {e}"


def load_detection_meta(json_path):
    """Load detection_params + stats from a _peaks.json; return (meta, error_msg).

    Separate from load_peaks_from_json() so the peak-loading contract (and its
    tests) stay untouched. Feeds the Rules panel's selection funnel — without
    it the panel could only show which rules are configured, not their effect.
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"params": data.get("detection_params", {}),
                "stats": data.get("stats", {})}, None
    except Exception as e:
        return None, f"Could not read detection metadata: {e}"


# --------------------------------------------------------------------------- #
# Per-peak selection state (workflow §第八階段 point 5)
# --------------------------------------------------------------------------- #
def peak_key(peak):
    """Durable identity for a peak: its position in the matrix.

    Deliberately NOT peak_id. peak_id is a prominence rank within the baseline
    set, so it is reassigned whenever the baseline moves (editing
    R004.half_width or R006.boundary, or re-detecting). A selection saved
    against peak_id would silently reattach to a different peak; the
    (rt_index, dt_index) coordinate never moves for a given .mea.
    """
    return f"{peak.get('rt_index')},{peak.get('dt_index')}"


def load_peak_state(state_path):
    """Read _peaks_state.json → {peak_key: bool}. Missing file → {}."""
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): bool(v) for k, v in (data.get("user_active") or {}).items()}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"peak state unreadable ({e}); starting from rule defaults")
        return {}


def save_peak_state(state_path, peaks):
    """Persist only explicit user choices (user_active is not None).

    Peaks the user never touched are omitted, so re-running detection with
    different rules does not resurrect stale opinions about peaks the user
    never actually looked at.
    
    """
    payload = {str(peak_key(p)): bool(p["user_active"])
               for p in peaks if p.get("user_active") is not None}
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"user_active": payload}, f, ensure_ascii=False, indent=2)


def effective_active(peak):
    """Rule verdict, overridden by an explicit user choice when present.

    Option (a) from the design discussion: the user is the final arbiter, so a
    manual pick beats a rule. is_rule_override() flags the case worth marking
    visually — the user rescued a peak the rules rejected.
    """
    user = peak.get("user_active")
    if user is None:
        return bool(peak.get("rule_active", True))
    return bool(user)


def is_rule_override(peak):
    """True when the user kept a peak the rules rejected (drawn with a ring)."""
    return peak.get("user_active") is True and not peak.get("rule_active", True)


def outputs_are_fresh(source_path, *outputs):
    """True when every output exists and is newer than the source .mea.

    Same mtime test the workflow specifies for batch_convert (§第九階段): all
    of them must be present and current, so a half-finished run (say .npz
    written but the PNG missing) counts as stale and is redone rather than
    silently reused.
    """
    try:
        src_mtime = os.path.getmtime(source_path)
    except OSError:
        return False
    for out in outputs:
        if not os.path.exists(out) or os.path.getmtime(out) < src_mtime:
            return False
    return True


def load_peaks_from_csv(csv_path):
    """Fallback: load peaks from CSV (includes optional rt_index/dt_index for labels)."""
    try:
        import csv
        peaks = []
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                peak = {
                    "peak_id": int(row["peak_id"]),
                    "retention_s": float(row["retention_s"]),
                    "drift_ms": float(row["drift_ms"]),
                    "intensity": int(row["intensity"]),
                }
                # Add optional fields if present
                if "rt_index" in row:
                    peak["rt_index"] = int(row["rt_index"])
                if "dt_index" in row:
                    peak["dt_index"] = int(row["dt_index"])
                peaks.append(peak)
        return peaks, None
    except Exception as e:
        return None, f"CSV parse error: {e}"


# ============================================================================== #
# Image Viewer Dialog (Zoom/Pan)
# ============================================================================== #

class ImageViewerDialog:
    """Popup window for viewing images with zoom/pan capabilities."""

    def __init__(self, parent, image_path, title="Image Viewer"):
        self.image_path = image_path
        self.zoom_level = 1.0
        self.pan_x, self.pan_y = 0, 0
        self.photo = None

        self.dialog = Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("900x700")
        self.dialog.minsize(400, 300)

        # Toolbar
        toolbar = Frame(self.dialog, bg="lightgray", height=50)
        toolbar.pack(side="top", fill="x", padx=5, pady=5)

        Label(toolbar, text="Zoom:", bg="lightgray", font=("Georgia", 10, "bold")).pack(side="left", padx=5, pady=5)
        ttk.Button(toolbar, text="Zoom In (+)", command=self.zoom_in).pack(side="left", padx=3, pady=5)
        ttk.Button(toolbar, text="Zoom Out (-)", command=self.zoom_out).pack(side="left", padx=3, pady=5)
        ttk.Button(toolbar, text="Fit", command=self.zoom_fit).pack(side="left", padx=3, pady=5)
        ttk.Button(toolbar, text="Reset (100%)", command=self.zoom_reset).pack(side="left", padx=3, pady=5)

        self.zoom_label = Label(toolbar, text="100%", bg="lightgray", font=("Georgia", 11, "bold"))
        self.zoom_label.pack(side="left", padx=15, pady=5)

        # Canvas for image display
        canvas_frame = Frame(self.dialog, bg="white")
        canvas_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.canvas = Canvas(canvas_frame, bg="white", cursor="fleur")
        self.canvas.pack(fill="both", expand=True)

        # Load image
        try:
            self.original_img = Image.open(image_path)
            # Force dialog to render so canvas has dimensions
            self.dialog.update()
            # Now display the image
            self.display_image()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load image: {e}")
            self.dialog.destroy()
            return

        # Bind mouse events for panning
        self.canvas.bind("<Button-1>", self.on_mouse_press)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)

        # Mouse-wheel zoom (Windows/macOS: <MouseWheel>; Linux: Button-4/5)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<Button-4>", lambda e: self.on_mouse_wheel(e, delta=120))
        self.canvas.bind("<Button-5>", lambda e: self.on_mouse_wheel(e, delta=-120))

    def display_image(self):
        """Render the current zoom level."""
        if not hasattr(self, 'original_img'):
            return

        w, h = self.original_img.size
        new_w = int(w * self.zoom_level)
        new_h = int(h * self.zoom_level)

        # Resize image
        resized = self.original_img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # Create PhotoImage (keep reference to prevent garbage collection)
        self.photo = ImageTk.PhotoImage(resized)

        # Clear canvas
        self.canvas.delete("all")

        # Get actual canvas size (if still too small, use reasonable defaults)
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()
        if canvas_w < 50:
            canvas_w = 800
        if canvas_h < 50:
            canvas_h = 600

        # Set scroll region
        self.canvas.config(scrollregion=(0, 0, max(new_w, canvas_w), max(new_h, canvas_h)))

        # Draw image on canvas
        self.canvas.create_image(self.pan_x, self.pan_y, image=self.photo, anchor="nw")

        # Update zoom label
        self.zoom_label.config(text=f"{int(self.zoom_level * 100)}%")

    def zoom_in(self):
        """Increase zoom level."""
        self.zoom_level = min(self.zoom_level + 0.2, 5.0)
        self.display_image()

    def zoom_out(self):
        """Decrease zoom level."""
        self.zoom_level = max(self.zoom_level - 0.2, 0.1)
        self.display_image()

    def zoom_fit(self):
        """Fit image to canvas."""
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()

        # Use reasonable defaults if canvas isn't fully sized yet
        if canvas_w < 50:
            canvas_w = 800
        if canvas_h < 50:
            canvas_h = 600

        img_w, img_h = self.original_img.size
        self.zoom_level = min(canvas_w / img_w, canvas_h / img_h) * 0.95
        self.pan_x, self.pan_y = 0, 0
        self.display_image()

    def zoom_reset(self):
        """Reset to 100% zoom."""
        self.zoom_level = 1.0
        self.pan_x, self.pan_y = 0, 0
        self.display_image()

    def on_mouse_press(self, event):
        """Start pan operation."""
        self.pan_start_x = event.x
        self.pan_start_y = event.y

    def on_mouse_drag(self, event):
        """Pan image while dragging."""
        dx = event.x - self.pan_start_x
        dy = event.y - self.pan_start_y
        self.pan_x += dx
        self.pan_y += dy
        self.pan_start_x = event.x
        self.pan_start_y = event.y

    def on_mouse_wheel(self, event, delta=None):
        """Zoom in/out on scroll; keep the image pixel under the cursor pinned.

        Windows/macOS deliver <MouseWheel> with event.delta = ±120 per notch.
        Linux delivers Button-4 (up) / Button-5 (down); the caller passes
        an explicit delta in that case.
        """
        d = delta if delta is not None else event.delta
        step = 0.1 if abs(d) < 240 else 0.2   # heavier steps for accelerated wheels
        zoom_new = self.zoom_level + step if d > 0 else self.zoom_level - step
        zoom_new = max(0.1, min(zoom_new, 5.0))
        if zoom_new == self.zoom_level:
            return

        # Zoom toward cursor: image pixel currently under cursor should stay under cursor
        img_x = (event.x - self.pan_x) / self.zoom_level
        img_y = (event.y - self.pan_y) / self.zoom_level
        self.pan_x = event.x - img_x * zoom_new
        self.pan_y = event.y - img_y * zoom_new
        self.zoom_level = zoom_new
        self.display_image()
        self.display_image()


# ============================================================================== #
# Main UI Application
# ============================================================================== #

class GCIMSApp:
    """Main Tk application for GC-IMS peak detection workflow."""

    def __init__(self, root):
        self.root = root
        self.state = AppState()
        self.ui_state = UIState.START
        self.root.title("GC-IMS Peak Detection — v2.1 by Albert Sheng")
        # Fit the actual screen instead of a fixed 1700x950: on a smaller
        # display that pushed part of the three-pane layout off-screen, and the
        # 1400x800 minsize then made it impossible to shrink back into view.
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        # Fallback geometry leaves room for a taskbar; "zoomed" below supersedes
        # it on platforms that support maximising (Windows, most X11 WMs).
        self.root.geometry(f"{screen_w}x{max(400, screen_h - 60)}+0+0")
        self.root.minsize(min(1100, screen_w - 80), min(700, screen_h - 80))
        try:
            self.root.state("zoomed")
        except TclError:
            pass

        # Canvas view state. Must exist before setup_ui(), because binding
        # <Configure> makes Tk fire a resize as soon as the canvas is mapped —
        # i.e. before any image has been loaded to create these lazily.
        self.main_canvas_original = None    # PIL.Image at native resolution
        self.main_canvas_zoom = 1.0
        self.main_canvas_pan_x = 0
        self.main_canvas_pan_y = 0
        self.main_canvas_kind = None        # "heatmap" | "overlay" | "bg" | None
        self._pan_last_x = None
        self._pan_last_y = None
        self._press_x = 0
        self._press_y = 0
        self._rules_after_id = None

        self.setup_ui()
        self.update_button_state()

    # --- UI Setup --- #

    def setup_ui(self):
        """Build main window layout."""
        # Top bar
        self.top_frame = Frame(self.root, bg="white", height=40)
        self.top_frame.pack(side="top", fill="x", padx=5, pady=5)

        Label(self.top_frame, text="Folder:", bg="white").pack(side="left", padx=5)
        self.folder_label = Label(
            self.top_frame, text="(none selected)", bg="white", fg="gray"
        )
        self.folder_label.pack(side="left", padx=5, fill="x", expand=True)

        # Configure style for larger buttons
        style = ttk.Style()
        style.configure("TButton", font=("Georgia", 10, "bold"), padding=8)
        # Distinct style for Generate Report (workflow §第十一階段：跟其他功能按鈕視覺區隔)
        style.configure("Report.TButton", font=("Georgia", 10, "bold"), padding=8,
                        foreground="darkgreen")

        # Toolbar order (user's revised layout, supersedes workflow §第八階段):
        #   Browse mea folder | Show Detected Peak Heatmap | Show Original Heatmap | Rules | Generate Report
        self.browse_btn = ttk.Button(
            self.top_frame, text="Browse mea folder", command=self.on_browse_folder, width=17
        )
        self.browse_btn.pack(side="left", padx=4, pady=2)

        self.show_detected_btn = ttk.Button(
            self.top_frame, text="Show Detected Peak Heatmap",
            command=self.on_show_detected_peaks, width=26,
        )
        self.show_detected_btn.pack(side="left", padx=4, pady=2)

        self.view_original_btn = ttk.Button(
            self.top_frame, text="Show Original Heatmap",
            command=self.on_show_original_heatmap, width=21,
        )
        self.view_original_btn.pack(side="left", padx=4, pady=2)

        self.rules_btn = ttk.Button(
            self.top_frame, text="Rules", command=self.on_open_rules, width=10,
        )
        self.rules_btn.pack(side="left", padx=4, pady=2)

        self.generate_report_btn = ttk.Button(
            self.top_frame, text="Generate Report",
            command=self.on_generate_report, width=16,
            style="Report.TButton",
        )
        self.generate_report_btn.pack(side="left", padx=4, pady=2)

        # Menu bar：Settings → Browse Library Data...（workflow user-added，不在工具列上）
        menubar = Menu(self.root)
        settings_menu = Menu(menubar, tearoff=0)
        settings_menu.add_command(
            label="Browse Library Data...", command=self.on_browse_library_data,
        )
        settings_menu.add_command(
            label="Reset Library Data to Default", command=self.on_reset_library_data,
        )
        settings_menu.add_separator()
        settings_menu.add_command(
            label="Show Current Library Location",
            command=self.on_show_library_location,
        )
        menubar.add_cascade(label="Settings", menu=settings_menu)
        self.root.config(menu=menubar)

        # Main content: file list | images | peak table (paned)
        # Three-pane layout per user's sketch (error.jpg):
        #   [Files 1] | [Main image 3] | [Peak table 2]
        # PanedWindow with proportional weights; user can drag sashes to resize.
        main_pane = PanedWindow(self.root, orient="horizontal")
        main_pane.pack(fill="both", expand=True, padx=5, pady=5)

        # ---- Left pane: MEA file list --------------------------------------
        left_frame = Frame(main_pane, bg="white")
        main_pane.add(left_frame, weight=1)

        Label(left_frame, text="MEA Files:", bg="white").pack(side="top", pady=5)
        self.file_tree = Treeview(left_frame, columns=(), height=20)
        self.file_tree.column("#0", width=300)
        self.file_tree.heading("#0", text="Filename")
        self.file_tree.pack(fill="both", expand=True)
        self.file_tree.bind("<<TreeviewSelect>>", self.on_file_selected)

        # ---- Center pane: main image (heatmap → overlay after detection) ---
        center_frame = Frame(main_pane, bg="white")
        main_pane.add(center_frame, weight=3)

        self.main_canvas_label = Label(
            center_frame, text="(no file loaded)", bg="white", font=("Georgia", 9),
        )
        self.main_canvas_label.pack(side="top", pady=5)
        self.main_canvas = Canvas(
            center_frame, bg="white", width=600, height=550, cursor="fleur",
            highlightthickness=0,
        )
        self.main_canvas.pack(fill="both", expand=True)
        # In-place interaction (replaces old click-to-popup ImageViewerDialog):
        #   left-drag = pan, wheel = zoom toward cursor
        #   a press+release that barely moved = toggle that peak (Batch 3)
        self.main_canvas.bind("<ButtonPress-1>", self.on_main_pan_start)
        self.main_canvas.bind("<B1-Motion>", self.on_main_pan_drag)
        self.main_canvas.bind("<ButtonRelease-1>", self.on_main_canvas_click_release)
        self.main_canvas.bind("<MouseWheel>", self.on_main_wheel)
        self.main_canvas.bind("<Button-4>", lambda e: self.on_main_wheel(e, delta=120))
        self.main_canvas.bind("<Button-5>", lambda e: self.on_main_wheel(e, delta=-120))
        # Re-render on canvas resize so the image continues to fit sensibly
        self.main_canvas.bind("<Configure>", self.on_main_canvas_resize)

        # ---- Right pane: peak table ----------------------------------------
        right_frame = Frame(main_pane, bg="white")
        main_pane.add(right_frame, weight=2)
        table_frame = right_frame

        self.peaks_header_label = Label(table_frame, text="Detected Peaks:", bg="white", font=("Georgia", 9))
        self.peaks_header_label.pack(side="top", pady=5)

        # Create frame for table and scrollbar
        tree_scroll_frame = Frame(table_frame, bg="white")
        tree_scroll_frame.pack(fill="both", expand=True)

        # Create scrollbar
        scrollbar = ttk.Scrollbar(tree_scroll_frame)
        scrollbar.pack(side="right", fill="y")

        # Create Treeview with scrollbar
        self.peak_tree = Treeview(
            tree_scroll_frame, columns=PEAK_TABLE_COLUMNS, height=10, show="headings",
            yscrollcommand=scrollbar.set
        )
        scrollbar.config(command=self.peak_tree.yview)
        self.peak_tree.pack(fill="both", expand=True, side="left")

        # Configure column widths and alignment
        col_widths = {
            "peak_id": 30, "drift_ms": 55, "drift_relative": 60,
            "retention_s": 60, "ri": 55, "intensity": 55,
            "on": 35, "gc_ims": 60, "gc": 78, "ims": 40, "trigger": 25,
        }
        for col in PEAK_TABLE_COLUMNS:
            width = col_widths.get(col, 100)
            self.peak_tree.column(col, width=width, anchor="center", stretch=False)
            # Make column headers clickable for sorting (identity columns don't sort)
            heading_cmd = (
                (lambda c=col: self.sort_peak_table(c))
                if col not in ("on", "trigger") else None
            )
            self.peak_tree.heading(col, text=COORD_LABELS.get(col, col),
                                   command=heading_cmd or (lambda: None))
        # Bind click-to-toggle for On / ▶ columns (row-selection handler still runs)
        self.peak_tree.bind("<Button-1>", self.on_peak_tree_click, add="+")

        # Center-align all cells
        style = ttk.Style()
        style.configure("Treeview", rowheight=25)

        self.peak_tree.bind("<<TreeviewSelect>>", self.on_peak_selected)

        # Track sort state
        self.sort_column = None
        self.sort_reverse = False

        # Footer (increased height for better readability)
        footer_frame = Frame(self.root, bg="white", height=50)
        footer_frame.pack(side="bottom", fill="x", padx=5, pady=5)

        # Status line + Exit button on the same row
        status_container = Frame(footer_frame, bg="white")
        status_container.pack(side="top", fill="x", padx=5, pady=5)

        # Exit button pinned to the far right of the footer
        self.exit_btn = ttk.Button(status_container, text="Exit", command=self.on_exit, width=10)
        self.exit_btn.pack(side="right", padx=5)

        Label(status_container, text="Status:", bg="white", font=("Georgia", 11, "bold")).pack(side="left", padx=5)
        self.status_label = Label(
            status_container,
            text="Ready",
            bg="white",
            fg="darkblue",
            justify="left",
            anchor="w",
            font=("Georgia", 11),
        )
        self.status_label.pack(side="left", fill="x", expand=True, padx=5)

        # Indeterminate progress bar for long-running subprocesses
        # (readGAS.py ≈ 13 s, peaks.py ≈ 83 s — no percentage available, so
        # we show a marquee to make clear the app hasn't hung)
        self.progress_bar = ttk.Progressbar(
            status_container, mode="indeterminate", length=240
        )
        self.progress_bar.pack(side="right", padx=5)

    # --- Progress-bar helpers --------------------------------------------- #

    def _progress_start(self):
        """Start the indeterminate marquee (used while a subprocess is running)."""
        try:
            self.progress_bar.start(interval=50)   # ms per step
        except Exception:
            pass

    def _progress_stop(self):
        try:
            self.progress_bar.stop()
        except Exception:
            pass

    # --- Event Handlers --- #

    def on_browse_folder(self):
        """Browse for folder containing .mea files.

        Initial folder priority:
          1. last-used folder (from settings if we saved it)
          2. <project>/GAS/  (project-local convention)
          3. system default (cwd)
        """
        # Prefer previously-selected folder if still exists, else GAS/, else default
        initial = self.state.current_folder
        if not initial or not os.path.isdir(initial):
            gas_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "GAS"
            )
            initial = gas_dir if os.path.isdir(gas_dir) else None
        folder = filedialog.askdirectory(
            title="Select folder with .mea files",
            initialdir=initial,
        )
        if not folder:
            return
        self.state.current_folder = folder
        self.folder_label.config(text=folder)
        self.populate_file_list(folder)
        self.ui_state = UIState.FOLDER_SELECTED
        self.update_button_state()
        # 第四階段：選資料夾即在背景「默默」解一次 RT→RI 校準（找 STD → 6 甲基酮
        # 錨點 → log-linear 內插常數），存進 state 供之後每個 .mea 共用、不重算。
        self._start_folder_calibration(folder)

    def _start_folder_calibration(self, folder):
        """背景解析本資料夾的 RT→RI 校準（第四階段），不阻塞 UI。

        流程呼應使用者描述：選資料夾 → 默默找 STD → 取 6 個甲基酮峰 → 算內插常數
        → 存 state。之後同資料夾換不同 .mea 檔時直接沿用（resolve_*_cached 有
        session/sidecar 快取，不重算 STD）。STD 尚未偵測（缺 _peaks.json）時會回
        unavailable，UI 就維持保留時間軸，不會卡住。
        """
        if self.state.ri_calibration_folder == folder and self.state.ri_calibration:
            return                                   # 同資料夾已解過，直接沿用
        self.state.ri_calibration = None
        self.state.ri_calibration_folder = folder

        def post(msg):
            self.root.after(0, lambda: self._folder_cal_status(folder, msg))

        def resolve():
            calibration.clear_session_cache()
            return calibration.resolve_ri_calibration_cached(
                folder, series_key=RI_SERIES, use_sidecar=False)

        def work():
            cal = mode = None
            try:
                cal, mode, _ = resolve()
                # STD 存在但尚未偵測（缺 _peaks.json）→ 背景逐一偵測後重解，直到取得
                # 絕對 RI（有可用 STD 就停，不必把每支 STD 都跑完）。
                if not (isinstance(cal, dict)
                        and cal.get("mode") == "multi_point_loglinear"):
                    for std in calibration.scan_folder_for_std(folder):
                        if folder != self.state.current_folder:
                            return                    # 使用者已換資料夾，放棄
                        base = Path(std).stem
                        if os.path.exists(os.path.join("results", base + "_peaks.json")):
                            continue
                        post(f"Detecting STD {base} for RI calibration (~90 s)…")
                        subprocess.run(
                            [sys.executable,
                             str(Path(__file__).with_name("peaks.py")), std],
                            capture_output=True)
                        cal, mode, _ = resolve()
                        if isinstance(cal, dict) and cal.get("mode") == "multi_point_loglinear":
                            break
            except Exception as e:
                cal, mode = None, f"error: {e}"
            self.root.after(0, lambda: self._on_calibration_ready(folder, cal, mode))

        threading.Thread(target=work, daemon=True).start()

    def _folder_cal_status(self, folder, msg):
        """Update status only if the user is still on the folder being calibrated."""
        if folder == self.state.current_folder:
            self.status_label.config(text=msg)

    def _on_calibration_ready(self, folder, cal, mode):
        """背景校準完成 → 存 state；若在此期間使用者換了資料夾則丟棄。"""
        if folder != self.state.current_folder:
            return
        if isinstance(cal, dict) and cal.get("mode") == "multi_point_loglinear":
            self.state.ri_calibration = cal
            n = cal.get("anchor_selection", {}).get("n_clean_anchors", "?")
            flag = " (assumed/borrowed)" if cal.get("assumed_unverified") else ""
            self.status_label.config(
                text=f"✓ RI calibration ready ({mode}, {n} ketone anchors){flag} — "
                     f"heatmap & table now in Retention Index")
            # 若已載入某檔的峰，立即重繪帶上 RI
            if self.state.peaks:
                self.populate_peak_table(self.state.peaks)
        else:
            self.state.ri_calibration = None
            self.status_label.config(
                text="RI calibration unavailable (no usable STD / not detected) — "
                     "showing retention time")

    def populate_file_list(self, folder):
        """List all .mea files in folder."""
        self.file_tree.delete(*self.file_tree.get_children())
        mea_files = sorted(Path(folder).glob("*.mea"))
        for mea_file in mea_files:
            self.file_tree.insert("", "end", text=mea_file.name, values=(str(mea_file),))

    def on_file_selected(self, event):
        """User selected a file → auto-trigger background read (workflow §第八階段)."""
        selection = self.file_tree.selection()
        if not selection:
            return
        item = selection[0]
        file_path = self.file_tree.item(item, "values")[0]
        self.state.selected_mea_file = file_path
        self.state.reset_after_file_selection()
        self.status_label.config(text=f"Selected: {Path(file_path).name}")
        self.ui_state = UIState.FILE_SELECTED
        self.update_button_state()
        # Auto-read（workflow：取消 Read File 獨立按鈕，13 秒隱性等待可接受）
        self._start_read()

    def _ask_use_cached_npz(self, mea, npz):
        """Ask whether to reuse an existing .npz instead of re-reading the .mea.

        Deliberately a question rather than an automatic decision: reusing
        silently would hide a stale cache, and re-reading silently would burn
        ~13 s on every file selection. The .mea itself is never touched either
        way — only the derived .npz is rewritten when the answer is No.
        """
        npz_mtime = os.path.getmtime(npz)
        when = datetime.datetime.fromtimestamp(npz_mtime).strftime("%Y-%m-%d %H:%M")
        size_mb = os.path.getsize(npz) / 1e6
        try:
            stale = npz_mtime < os.path.getmtime(mea)
        except OSError:
            stale = False   # .mea unreachable (removed drive, stale list) — the
                            # .npz is then the only copy, so don't scare the user
        warning = ("\n\n⚠ The .mea is NEWER than this .npz, so the cache may not "
                   "match the current file." if stale else "")
        return messagebox.askyesno(
            "Use the existing .npz?",
            f"A previously converted matrix already exists for this file:\n\n"
            f"    {Path(npz).name}\n"
            f"    {size_mb:.0f} MB, saved {when}{warning}\n\n"
            f"Yes  —  read the .npz (instant)\n"
            f"No   —  re-read {Path(mea).name} (~13 s) and overwrite the .npz\n\n"
            f"The original .mea is not modified either way.",
            icon="warning" if stale else "question",
        )

    def _finish_npz_load(self, base):
        """Display the background rebuilt from / kept alongside the .npz."""
        bg = f"results/{base}_bg.png"
        self.load_heatmap(bg)
        self.state.heatmap_path = bg
        self.state.bg_img_path = bg
        # Geometry belongs to the image, not to the peak list — load it here so
        # it is right even when the peaks turn out to be stale below.
        self._load_canvas_geometry(base)
        self.ui_state = UIState.READ_DONE
        self.status_label.config(text=f"✓ Loaded from existing .npz: {base}")
        self.update_button_state()
        self._load_existing_peaks(base)

    def _render_bg_from_npz(self, base, npz):
        """Rebuild the display background from the .npz — no .mea needed.

        Used when the .npz survived but its images did not. peaks.py --bg-only
        skips detection entirely, so this costs a render rather than the ~83 s
        the full pipeline would take.
        """
        self.ui_state = UIState.READING
        self.update_button_state()
        self.status_label.config(text=f"Rebuilding heatmap from {base}.npz...")
        self._progress_start()

        q = queue.Queue()
        cmd = [sys.executable, str(Path(__file__).with_name("peaks.py")),
               npz, "--bg-only"]
        if self.state.ri_calibration is not None:   # 背景圖 y 軸標成 RI
            cmd += ["--ri-series", RI_SERIES]
        threading.Thread(target=run_subprocess, args=(cmd, q), daemon=True).start()
        self._poll_bg_render(q, base)

    def _poll_bg_render(self, q, base):
        try:
            msg_type, msg = q.get_nowait()
            if msg_type == "stdout":
                self.status_label.config(text=msg[:80])
                self.root.after(100, lambda: self._poll_bg_render(q, base))
            elif msg_type == "done":
                self._progress_stop()
                if os.path.exists(f"results/{base}_bg.png"):
                    self._finish_npz_load(base)
                else:
                    messagebox.showerror(
                        "Rebuild failed",
                        "Could not rebuild the heatmap from the .npz.\n"
                        "Select the file again and choose No to re-read the .mea.")
                    self.ui_state = UIState.ERROR
                    self.update_button_state()
            elif msg_type == "error":
                self._progress_stop()
                messagebox.showerror("Rebuild failed", str(msg))
                self.ui_state = UIState.ERROR
                self.update_button_state()
        except queue.Empty:
            self.root.after(100, lambda: self._poll_bg_render(q, base))

    def _start_read(self):
        """Show the heatmap for the selected .mea, reusing the .npz if wanted."""
        if not self.state.selected_mea_file:
            return

        # The .npz holds the same matrix losslessly and is what every later
        # stage reads, so an existing one can stand in for the .mea entirely —
        # including redrawing the display background, which is why only the
        # .npz needs to be present for the shortcut to be offered.
        mea = self.state.selected_mea_file
        base = Path(mea).stem
        npz = f"results/{base}.npz"
        if os.path.exists(npz) and self._ask_use_cached_npz(mea, npz):
            if os.path.exists(f"results/{base}_bg.png"):
                self._finish_npz_load(base)
            else:
                self._render_bg_from_npz(base, npz)
            return

        self.ui_state = UIState.READING
        self.update_button_state()
        self.status_label.config(
            text=f"Reading {Path(self.state.selected_mea_file).name}..."
        )
        self._progress_start()

        output_queue = queue.Queue()
        cmd = [
            sys.executable,
            str(Path(__file__).with_name("readGAS.py")),
            self.state.selected_mea_file,
            "--no-show",
        ]
        # 第四階段：校準就緒時，原始熱圖也以 RI 為 y 軸（x 仍是正規化 drift）——
        # 呼應「所有熱圖都反映正規化座標：normalized drift × RI」。
        if self.state.ri_calibration is not None:
            cmd += ["--ri-series", RI_SERIES]
        thread = threading.Thread(
            target=run_subprocess, args=(cmd, output_queue), daemon=True
        )
        thread.start()
        self.poll_read_output(output_queue)

    def poll_read_output(self, q):
        """Poll subprocess output queue until the reader finishes."""
        try:
            msg_type, msg_content = q.get_nowait()
            if msg_type == "stdout":
                self.status_label.config(text=msg_content[:80])
                self.root.after(100, lambda: self.poll_read_output(q))
            elif msg_type == "done":
                self._progress_stop()
                base = Path(self.state.selected_mea_file).stem
                heatmap = f"results/{base}_heatmap.png"
                if os.path.exists(heatmap):
                    self.load_heatmap(heatmap)
                    self.state.heatmap_path = heatmap
                    self.ui_state = UIState.READ_DONE
                    self.status_label.config(text=f"✓ Read complete: {base}")
                else:
                    messagebox.showerror("Error", "Heatmap PNG not found after read.")
                    self.ui_state = UIState.ERROR
                self.update_button_state()
            elif msg_type == "error":
                self._progress_stop()
                messagebox.showerror("Read failed", msg_content[:200])
                self.ui_state = UIState.ERROR
                self.update_button_state()
        except queue.Empty:
            self.root.after(100, lambda: self.poll_read_output(q))

    def load_heatmap(self, path):
        """Load and display heatmap image on main canvas. Extract matrix shape from .npz."""
        try:
            self._render_main_from_path(path, kind="heatmap")
            self.state.heatmap_img_path = path

            # Extract matrix shape from .npz file.
            # Heatmap is named "<base>_heatmap.png" but the npz is "<base>.npz",
            # so strip the "_heatmap" suffix before building the npz path.
            base = os.path.splitext(path)[0]
            if base.endswith("_heatmap"):
                base = base[: -len("_heatmap")]
            npz_path = base + ".npz"
            if os.path.exists(npz_path):
                try:
                    z = np.load(npz_path)
                    intensity = z.get("intensity")
                    if intensity is not None:
                        self.state.matrix_shape = intensity.shape  # (n_rt, n_dt)
                        self.update_peaks_header()  # Show dimensions in peak table header
                except Exception:
                    pass
        except Exception as e:
            messagebox.showwarning("Image load failed", f"Could not load {path}:\n{e}")

    def on_show_detected_peaks(self):
        """Show the peak-detected heatmap in main canvas.

        If peaks are already detected for this file (overlay in state), just
        swap the canvas to the overlay — instant, no re-computation. Otherwise
        run peaks.py (~83 s) with progress-bar feedback, then swap when done.
        """
        if not self.state.selected_mea_file:
            messagebox.showwarning("No file", "Please select a file first.")
            return

        # Already detected for this file → just swap the view (circles are
        # Canvas objects, so this costs nothing beyond one image blit).
        # Requires peaks, not just the backdrop: a .npz reload can leave the
        # background present with a stale/absent peak list, and short-circuiting
        # on the image alone would show an empty canvas and never detect.
        if (self.state.peaks and self.state.bg_img_path
                and os.path.exists(self.state.bg_img_path)):
            self._show_peak_canvas()
            self.status_label.config(
                text=f"Showing {len(self.state.peaks)} detected peaks")
            return

        self.ui_state = UIState.DETECTING
        self.update_button_state()
        self.status_label.config(text="Detecting peaks (this can take ~80 s)...")
        self._progress_start()

        output_queue = queue.Queue()
        cmd = [sys.executable, str(Path(__file__).with_name("peaks.py")), self.state.selected_mea_file]
        if self.state.ri_calibration is not None:   # 背景圖/疊圈圖 y 軸標成 RI
            cmd += ["--ri-series", RI_SERIES]
        thread = threading.Thread(
            target=run_subprocess, args=(cmd, output_queue), daemon=True
        )
        thread.start()
        self.poll_detect_output(output_queue)

    def poll_detect_output(self, q):
        """Poll subprocess output queue until peak detection finishes."""
        try:
            msg_type, msg_content = q.get_nowait()
            if msg_type == "stdout":
                self.status_label.config(text=msg_content[:80])
                self.root.after(100, lambda: self.poll_detect_output(q))
            elif msg_type == "done":
                self._progress_stop()
                base = Path(self.state.selected_mea_file).stem
                overlay = f"results/{base}_overlay.png"
                json_path = f"results/{base}_peaks.json"
                csv_path = f"results/{base}_peaks.csv"

                # _overlay.png keeps its baked-in circles for CLI/report use;
                # the interactive canvas uses the circle-free _bg.png so the
                # circles can be individual Canvas objects (Batch 6).
                self.state.overlay_path = overlay if os.path.exists(overlay) else None
                bg = f"results/{base}_bg.png"
                self.state.bg_img_path = bg if os.path.exists(bg) else None
                self._load_canvas_geometry(base)
                self._load_maxima(base)
                self.state.peak_state = load_peak_state(
                    f"results/{base}_peaks_state.json")

                peaks, err = load_peaks_from_json(json_path)
                if err and os.path.exists(csv_path):
                    peaks, err = load_peaks_from_csv(csv_path)
                    if err:
                        messagebox.showerror("Error", f"Could not load peaks:\n{err}")
                        self.ui_state = UIState.ERROR
                    else:
                        # Load full JSON to get matrix_shape for peak highlighting
                        try:
                            with open(json_path, "r", encoding="utf-8") as f:
                                json_data = json.load(f)
                                self.state.matrix_shape = tuple(json_data.get("matrix_shape", []))
                        except:
                            pass
                        self.state.peaks_json_path = json_path
                        self.state.peaks_csv_path = csv_path
                        self.populate_peak_table(peaks)
                        self.ui_state = UIState.PEAKS_DETECTED
                        self.status_label.config(text=f"✓ Detected {len(peaks)} peaks")
                elif err:
                    messagebox.showerror("Error", f"Could not load peaks:\n{err}")
                    self.ui_state = UIState.ERROR
                else:
                    # Load matrix_shape from JSON for peak highlighting
                    try:
                        with open(json_path, "r", encoding="utf-8") as f:
                            json_data = json.load(f)
                            self.state.matrix_shape = tuple(json_data.get("matrix_shape", []))
                    except:
                        pass
                    self.state.peaks_json_path = json_path
                    self.state.peaks_csv_path = csv_path
                    self.populate_peak_table(peaks)
                    self.ui_state = UIState.PEAKS_DETECTED
                    self.status_label.config(text=f"✓ Detected {len(peaks)} peaks")

                # Funnel stats for the Rules panel (harmless no-op on the error path)
                self.state.detection_meta, _ = load_detection_meta(json_path)
                self._show_peak_canvas()
                self.update_button_state()
            elif msg_type == "error":
                self._progress_stop()
                messagebox.showerror("Detection failed", msg_content[:200])
                self.ui_state = UIState.ERROR
                self.update_button_state()
        except queue.Empty:
            self.root.after(100, lambda: self.poll_detect_output(q))

    def load_overlay(self, path):
        """Load and display overlay image on main canvas."""
        try:
            self._render_main_from_path(path, kind="overlay")
            self.state.overlay_img_path = path
            # Overlay image size used by peak-highlight math
            self.state.overlay_image_size = self.main_canvas_original.size
            self.state.overlay_canvas_size = (self.main_canvas.winfo_width(),
                                              self.main_canvas.winfo_height())
        except Exception as e:
            messagebox.showwarning("Image load failed", f"Could not load {path}:\n{e}")

    # ------------------------------------------------------------------------- #
    # In-place zoom/pan for main_canvas (replaces old ImageViewerDialog popup)
    # ------------------------------------------------------------------------- #
    def _render_main_from_path(self, path, kind):
        """Load PIL image from disk into main_canvas + reset view."""
        img = Image.open(path)
        self.main_canvas_original = img
        self.main_canvas_kind = kind
        # Fit-to-canvas initial zoom (respects current canvas size)
        cw = max(self.main_canvas.winfo_width(), 200)
        ch = max(self.main_canvas.winfo_height(), 200)
        iw, ih = img.size
        self.main_canvas_zoom = min(cw / iw, ch / ih) * 0.98
        self.main_canvas_pan_x = 0
        self.main_canvas_pan_y = 0
        self._render_main_canvas()
        # Update the header label to reflect what's showing
        self.main_canvas_label.config(text={
            "heatmap": "Original heatmap (wheel = zoom, drag = pan)",
            "overlay": "Detected-peaks heatmap (wheel = zoom, drag = pan)",
        }.get(kind, kind or ""))

    def _render_main_canvas(self):
        """Re-blit main_canvas_original at current zoom/pan (called after wheel/drag)."""
        if self.main_canvas_original is None:
            return
        z = self.main_canvas_zoom
        iw, ih = self.main_canvas_original.size
        nw = max(1, int(iw * z))
        nh = max(1, int(ih * z))
        resized = self.main_canvas_original.resize((nw, nh), Image.Resampling.LANCZOS)
        # Keep a reference on state so PhotoImage isn't garbage-collected
        self.state.overlay_photo_ref = ImageTk.PhotoImage(resized)
        self.main_canvas.delete("all")
        self.main_canvas.create_image(
            self.main_canvas_pan_x, self.main_canvas_pan_y,
            image=self.state.overlay_photo_ref, anchor="nw",
        )
        # Circles live above the backdrop and must follow every zoom/pan
        self._draw_peak_circles()

    # ------------------------------------------------------------------------- #
    # Batch 6: peak circles as native Canvas objects
    # ------------------------------------------------------------------------- #
    def _load_existing_peaks(self, base):
        """Load a previous detection instead of spending ~83 s redoing it.

        Only the display artefacts are reused; nothing here recomputes, so a
        stale set is never silently accepted — outputs_are_fresh() requires all
        of them to be newer than the .mea.
        """
        mea = self.state.selected_mea_file
        json_path = f"results/{base}_peaks.json"
        bg = f"results/{base}_bg.png"
        maxima = f"results/{base}_maxima.npz"
        if not outputs_are_fresh(mea, json_path, bg, maxima):
            return False

        peaks, err = load_peaks_from_json(json_path)
        if err:
            return False
        self.state.overlay_path = f"results/{base}_overlay.png"
        self.state.bg_img_path = bg
        self._load_canvas_geometry(base)
        self._load_maxima(base)
        self.state.peak_state = load_peak_state(f"results/{base}_peaks_state.json")
        self.state.detection_meta, _ = load_detection_meta(json_path)
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                self.state.matrix_shape = tuple(json.load(f).get("matrix_shape", []))
        except Exception:
            pass
        self.state.peaks_json_path = json_path
        self.state.peaks_csv_path = f"results/{base}_peaks.csv"
        self.populate_peak_table(peaks)
        self.ui_state = UIState.PEAKS_DETECTED
        self.update_button_state()
        self.status_label.config(
            text=f"✓ Loaded {len(peaks)} peaks from a previous run — "
                 f"click Show Detected Peak Heatmap to view")
        return True

    def _load_canvas_geometry(self, base):
        """Where the plot area sits inside _bg.png, for placing circles.

        _bg.json wins over _peaks.json: it is written by whoever renders the
        PNG, so it always describes the image actually on screen. _peaks.json
        may carry geometry from an older render (or none, if the background was
        rebuilt from the .npz without re-detecting).
        """
        self.state.canvas_geometry = None
        for path, key in ((f"results/{base}_bg.json", None),
                          (f"results/{base}_peaks.json", "canvas_geometry")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                geom = data if key is None else data.get(key)
            except Exception:
                continue
            if geom:
                self.state.canvas_geometry = geom
                return

    def _load_maxima(self, base):
        """Load the raw-maxima cache so the Rules panel can re-select live.

        Without it a rule that moves the prominence gate (R004) would need a
        full ~83 s re-detection; with it the whole funnel is recomputed in
        memory in milliseconds.
        """
        self.state.maxima = None
        try:
            self.state.maxima = peaks_mod.load_maxima_npz(f"results/{base}_maxima.npz")
        except Exception as e:
            print(f"maxima cache unavailable: {e}")

    def _show_peak_canvas(self):
        """Render the circle-free backdrop and draw peak circles on top."""
        path = self.state.bg_img_path or self.state.overlay_path
        if not path or not os.path.exists(path):
            return
        kind = "bg" if path == self.state.bg_img_path else "overlay"
        self._render_main_from_path(path, kind=kind)
        self.state.overlay_image_size = self.main_canvas_original.size

    def _peak_to_image_xy(self, peak):
        """Peak data coords -> pixel coords inside the background PNG.

        Uses the plot-area bbox recorded by peaks.write_overlay(). The old
        highlight_peak_on_overlay() assumed the data area filled the whole PNG
        and so drew circles offset by the matplotlib margins; this does not.
        Returns None when geometry or the peak's coords are unavailable.
        """
        g = self.state.canvas_geometry
        if not g:
            return None
        dr = peak.get("drift_relative")
        # 第四階段：背景圖若已重採樣成 RI 座標（geom.y_axis=='ri'），圈的 y 要用
        # peak['ri']（與圖同座標系）；否則用保留時間。RI 為 None 的峰不畫。
        if g.get("y_axis") == "ri":
            yval = peak.get("ri")
        else:
            yval = peak.get("retention_s")
        if dr is None or yval is None:
            return None
        pw, ph = g["png_size"]
        x0, y0, bw, bh = g["axes_bbox"]
        xmin, xmax = g["xlim"]
        ymin, ymax = g["ylim"]
        if xmax == xmin or ymax == ymin:
            return None
        fx = x0 + (dr - xmin) / (xmax - xmin) * bw
        fy = y0 + (yval - ymin) / (ymax - ymin) * bh   # figure fraction, from bottom
        return fx * pw, (1.0 - fy) * ph                # canvas y runs downward

    def _draw_peak_circles(self):
        """(Re)draw one oval + number per peak on top of the background image.

        Every baseline peak is drawn, whatever its state — a peak rejected by a
        rule looks exactly like one the user deselected by hand (stippled red),
        because both mean the same thing: not selected. Peaks removed by the
        mandatory rules (R004/R006) are absent from state.peaks entirely; they
        were never candidates, so there is nothing to show.
        """
        canvas = self.main_canvas
        canvas.delete("peakcircle")
        self.state.peak_items = {}
        if not self.state.canvas_geometry or self.main_canvas_kind != "bg":
            return
        z = self.main_canvas_zoom
        r = 7   # fixed screen radius: keeps circles clickable at any zoom
        for peak in self.state.peaks:
            xy = self._peak_to_image_xy(peak)
            if xy is None:
                continue
            x = self.main_canvas_pan_x + xy[0] * z
            y = self.main_canvas_pan_y + xy[1] * z
            pid = peak.get("peak_id", "?")
            active = effective_active(peak)
            # Tk canvas has no alpha channel. -stipple works on text, but
            # -outlinestipple is silently ignored on Windows, so the circle
            # cannot be dithered — it changes colour instead (verified by the
            # user: stippled numbers dimmed, stippled outlines did not).
            stipple = "" if active else "gray50"
            outline = "red" if active else "gray60"
            tags = ("peakcircle", f"peak{pid}")
            oval = canvas.create_oval(
                x - r, y - r, x + r, y + r, outline=outline, width=2, fill="",
                tags=tags)
            text = canvas.create_text(
                x + r + 1, y - r - 1, text=str(pid), fill="red", anchor="sw",
                font=("TkDefaultFont", 8), stipple=stipple, tags=tags)
            items = {"oval": oval, "text": text}
            if is_rule_override(peak):
                # Rules rejected this peak but the user kept it — mark it so the
                # override is visible later, not silently indistinguishable.
                items["ring"] = canvas.create_oval(
                    x - r - 3, y - r - 3, x + r + 3, y + r + 3,
                    outline="red", width=1, dash=(2, 2), fill="", tags=tags)
            self.state.peak_items[pid] = items

    def toggle_peak(self, peak_id):
        """Flip one peak's selection. The single entry point for every surface.

        Circle click and the table's On checkbox both land here, which is what
        keeps them in sync — there is no second copy of the state to drift.
        """
        peak = self._peak_by_id(peak_id)
        if peak is None:
            return
        peak["user_active"] = not effective_active(peak)
        self._refresh_peak_visuals(peak)
        self.update_peaks_header()      # keeps "Current selected peaks" live
        self._persist_peak_state()
        state = "selected" if effective_active(peak) else "deselected"
        extra = " (kept against the rules)" if is_rule_override(peak) else ""
        self.status_label.config(text=f"Peak #{peak_id} {state}{extra}")

    def _refresh_peak_visuals(self, peak):
        """Repaint one peak's circle, number and table row after a toggle."""
        self._draw_peak_circles()
        for item_id in self.peak_tree.get_children():
            values = self.peak_tree.item(item_id, "values")
            if values and str(values[0]) == str(peak.get("peak_id")):
                self._refresh_row(item_id, peak)
                break

    def _persist_peak_state(self):
        """Write the sidecar so selections survive a restart."""
        if not self.state.selected_mea_file:
            return
        base = Path(self.state.selected_mea_file).stem
        try:
            save_peak_state(f"results/{base}_peaks_state.json", self.state.peaks)
        except Exception as e:
            print(f"could not save peak state: {e}")

    def _apply_saved_peak_state(self, peaks):
        """Re-attach saved user choices to a freshly computed peak list."""
        saved = self.state.peak_state or {}
        for p in peaks:
            p["user_active"] = saved.get(peak_key(p))
        return peaks

    def on_main_canvas_click_release(self, event):
        """Treat a near-stationary press+release as a peak toggle, not a pan.

        Left-drag pans the canvas, so distance is what separates the two. The
        circles are hollow (fill=""), which in Tk means only the 2px outline is
        clickable, so hit-testing is done on distance to the peak centre
        instead of on canvas item identity — the inside of the circle counts.
        """
        if self._pan_last_x is None:
            return
        moved = abs(event.x - self._press_x) + abs(event.y - self._press_y)
        self._pan_last_x = self._pan_last_y = None
        if moved > 3:
            return
        peak = self._peak_at_canvas_xy(event.x, event.y)
        if peak is not None:
            self.toggle_peak(peak.get("peak_id"))

    def _peak_at_canvas_xy(self, cx, cy, threshold=12):
        """Nearest peak to a canvas point, or None if nothing is close enough."""
        if self.main_canvas_kind != "bg":
            return None
        z = self.main_canvas_zoom
        best, best_d = None, threshold
        for peak in self.state.peaks:
            xy = self._peak_to_image_xy(peak)
            if xy is None:
                continue
            dx = self.main_canvas_pan_x + xy[0] * z - cx
            dy = self.main_canvas_pan_y + xy[1] * z - cy
            d = (dx * dx + dy * dy) ** 0.5
            if d < best_d:
                best, best_d = peak, d
        return best

    def on_main_canvas_resize(self, event):
        """When the pane sash moves, refit the image without changing zoom origin."""
        # Only refit if we haven't been panned/zoomed since load — otherwise
        # respect user's manual zoom state
        if self.main_canvas_original is None:
            return
        # Trigger a re-render at current zoom (image will still fit if user
        # hasn't zoomed manually; otherwise just re-blit at same coords)
        self._render_main_canvas()

    def on_main_pan_start(self, event):
        self._pan_last_x = event.x
        self._pan_last_y = event.y
        # Remembered so release can tell a click from a drag
        self._press_x = event.x
        self._press_y = event.y

    def on_main_pan_drag(self, event):
        if self._pan_last_x is None:
            return
        dx = event.x - self._pan_last_x
        dy = event.y - self._pan_last_y
        self.main_canvas_pan_x += dx
        self.main_canvas_pan_y += dy
        self._pan_last_x = event.x
        self._pan_last_y = event.y
        self._render_main_canvas()

    def on_main_wheel(self, event, delta=None):
        """Zoom toward cursor position (Google Maps-style)."""
        if self.main_canvas_original is None:
            return
        d = delta if delta is not None else event.delta
        step = 0.1 if abs(d) < 240 else 0.2
        z_new = self.main_canvas_zoom + step if d > 0 else self.main_canvas_zoom - step
        z_new = max(0.1, min(z_new, 5.0))
        if z_new == self.main_canvas_zoom:
            return
        # Keep image pixel under cursor fixed on screen
        ix = (event.x - self.main_canvas_pan_x) / self.main_canvas_zoom
        iy = (event.y - self.main_canvas_pan_y) / self.main_canvas_zoom
        self.main_canvas_pan_x = event.x - ix * z_new
        self.main_canvas_pan_y = event.y - iy * z_new
        self.main_canvas_zoom = z_new
        self._render_main_canvas()

    def populate_peak_table(self, peaks):
        """Populate peak table from peak list.

        For Batch 2, GC×IMS/GC/IMS columns show placeholders when the peak has
        no `matches` field (i.e. identify.py hasn't been run inline yet).
        On column defaults to checked; three-way sync arrives in Batch 3.
        """
        self.peak_tree.delete(*self.peak_tree.get_children())
        # Ensure every peak has an "active" flag (default True)
        for p in peaks:
            p.setdefault("rule_active", p.get("active", True))
            p.setdefault("user_active", None)
        self._apply_saved_peak_state(peaks)
        # 第四階段：若本資料夾已解出絕對 RI 校準，就地把 ri 蓋到每個峰（RT→RI）
        if self.state.ri_calibration is not None:
            try:
                calibration.attach_ri(peaks, self.state.ri_calibration)
            except Exception as e:
                print(f"[warn] attach_ri failed: {e}", file=sys.stderr)
        # 第十階段：套用已快取的比對結果，讓 GC (RI) 欄一畫出來就有值（換規則重繪
        # 時免重算）。未快取的峰稍後由 _autofill_gc_matches() 背景補上。
        self._apply_cached_matches(peaks)
        self.state.peaks = peaks
        self.peak_tree.tag_configure("inactive", foreground="gray60")
        self.peak_tree.tag_configure("override", foreground="firebrick")
        for peak in peaks:
            values = tuple(self._cell_value(col, peak) for col in PEAK_TABLE_COLUMNS)
            self.peak_tree.insert("", "end", values=values,
                                  tags=self._row_tags(peak))

        # Update header with matrix dimensions
        self.update_peaks_header()
        self.sort_column = None
        self.sort_reverse = False

        # 第十階段：自動填 GC (RI) 欄——不需按 ▶。背景載庫 + 比對未快取的峰。
        self._autofill_gc_matches()

        # _overlay_numbered.png is a static export for the Batch 8 report, not
        # something the canvas shows any more (circles and numbers are Canvas
        # objects now). Generating it on every table refresh spent a subprocess
        # on a file nothing reads — it moves behind an explicit button instead.

    # ------------------------------------------------------------------------- #
    # Peak-table cell rendering & click dispatch (Batch 2)
    # ------------------------------------------------------------------------- #
    def _cell_value(self, col, peak):
        """Return display string for one cell of the peak table."""
        if col == "on":
            return CHECK_ON if effective_active(peak) else CHECK_OFF

        m = peak.get("matches") or {}
        if col == "gc_ims":
            combined = m.get("combined_matches") or []
            if combined:
                return combined[0].get("Name") or combined[0].get("NAME") or "?"
            return "—"
        if col == "gc":
            # Show the closest library RI value (with its Δ from the peak's RI) so
            # the user can compare it against the peak's own RI in the RI column.
            # Names / the full candidate list live in the ▶ panel. gc_hits are
            # delta-sorted; [0] is the closest.
            gc_hits = m.get("gc_matches")
            if not gc_hits:
                return "—"
            best = gc_hits[0]
            if "rt" in best.get("match_dimensions", []):
                val, delta = best.get("Rt[sec]"), best.get("delta_rt")
            else:
                val, delta = best.get("RI"), best.get("delta_ri")
            if val is None:
                return "—"
            d = f" (Δ{delta:.2f})" if delta is not None else ""
            return f"{val:g}{d}"
        if col == "ims":
            # "—" when the K0 dimension is unavailable (no calibration), which is
            # the current state — peaks are tagged k0_mode="unavailable" at match
            # time. "0" is reserved for "K0 available but no hit".
            if peak.get("k0_mode") == "unavailable":
                return "—"
            ims_hits = m.get("ims_matches")
            if ims_hits is None:
                return "—"
            combined_cas = {c.get("CAS") for c in (m.get("combined_matches") or [])}
            return str(sum(1 for h in ims_hits if h.get("CAS") not in combined_cas))
        if col == "trigger":
            return TRIGGER_ACTIVE if effective_active(peak) else TRIGGER_DIM

        if col == "ri":
            ri = peak.get("ri")
            if ri is None:
                return "—"                       # 無絕對校準 → 相對/未校準
            # 外插值加 '*'（draft.20/21：外插 + 標記）
            return f"{ri:.1f}{'*' if peak.get('ri_extrapolated') else ''}"

        # Default: raw peak field
        return peak.get(col)

    def _peak_by_id(self, peak_id):
        for p in self.state.peaks:
            if p.get("peak_id") == peak_id:
                return p
        return None

    def _refresh_row(self, item_id, peak):
        """Rebuild one row's values and its grey/normal styling."""
        values = tuple(self._cell_value(col, peak) for col in PEAK_TABLE_COLUMNS)
        self.peak_tree.item(item_id, values=values, tags=self._row_tags(peak))

    @staticmethod
    def _row_tags(peak):
        """Row styling mirrors the circle: deselected for any reason = grey."""
        if not effective_active(peak):
            return ("inactive",)
        return ("override",) if is_rule_override(peak) else ()

    def on_peak_tree_click(self, event):
        """Detect click column; dispatch On-toggle / ▶-trigger; else fall through.

        Runs alongside TreeviewSelect (row selection), so ordinary sortable
        columns still highlight the row like before.
        """
        region = self.peak_tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col_id = self.peak_tree.identify_column(event.x)   # "#1".."#N"
        row_id = self.peak_tree.identify_row(event.y)
        if not row_id:
            return
        try:
            col_idx = int(col_id.replace("#", "")) - 1
            col_name = PEAK_TABLE_COLUMNS[col_idx]
        except (ValueError, IndexError):
            return

        values = self.peak_tree.item(row_id, "values")
        if not values:
            return
        try:
            peak_id = int(values[0])
        except (ValueError, TypeError):
            return
        peak = self._peak_by_id(peak_id)
        if peak is None:
            return

        if col_name == "on":
            # Same entry point the circle click uses, which is what keeps the
            # two surfaces in sync (workflow §第八階段 point 3).
            self.toggle_peak(peak_id)
        elif col_name == "trigger":
            if not effective_active(peak):
                # Workflow §第八階段: ▶ disabled when inactive
                return
            self._open_compound_match_panel(peak)

    # ------------------------------------------------------------------------- #
    # Stage 10 — compound matching against .ril/.iml (GC column + ▶ panel)
    # ------------------------------------------------------------------------- #
    def _ensure_libraries_then(self, on_ready, notify=False):
        """Load .ril/.iml once (background, cached on state.library_rows), then
        call on_ready(). Only calls on_ready when libraries are actually available.
        notify=True pops a dialog on failure (▶ path); the auto-fill path stays
        quiet and remembers the failure so it does not retry every render."""
        if self.state.library_rows is not None:
            on_ready()
            return
        if getattr(self.state, "library_unavailable", False):
            if notify:
                messagebox.showwarning(
                    "No libraries", "No .ril/.iml library found (library_data/).")
            return
        self.status_label.config(text="Loading compound libraries…")
        q = queue.Queue()
        mea = self.state.selected_mea_file

        def work():
            try:
                import identify
                data_dir = library.resolve_data_dir()
                if data_dir is None:
                    q.put(("error", "No library folder found (library_data/ is empty "
                                    "or missing). Add .ril/.iml files first."))
                    return
                header = (identify.read_mea_header(mea)
                          if mea and os.path.exists(mea) else {})
                ril_paths, iml_paths, info = identify.select_library_files(data_dir, header)
                q.put(("done", (library.load_ril_many(ril_paths),
                                library.load_iml_many(iml_paths), info)))
            except Exception as e:   # noqa: BLE001 — surface any load failure
                q.put(("error", str(e)))

        threading.Thread(target=work, daemon=True).start()
        self._poll_libraries(q, on_ready, notify)

    def _poll_libraries(self, q, on_ready, notify):
        try:
            kind, payload = q.get_nowait()
        except queue.Empty:
            self.root.after(120, lambda: self._poll_libraries(q, on_ready, notify))
            return
        if kind == "done":
            self.state.library_rows = payload
            rr, ir, _ = payload
            self.status_label.config(
                text=f"✓ Libraries loaded ({len(rr):,} .ril + {len(ir):,} .iml rows)")
            on_ready()
        else:
            self.state.library_unavailable = True
            self.status_label.config(text="Compound library unavailable")
            if notify:
                messagebox.showwarning("Library load failed", payload)

    def _apply_cached_matches(self, peaks):
        """Set peak['matches'] from the per-file cache (keyed by coordinate) so the
        GC column renders immediately on re-draw (e.g. rule tweaks reuse it)."""
        cache = self.state.match_cache
        for p in peaks:
            res = cache.get((p.get("rt_index"), p.get("dt_index")))
            if res is not None:
                p["matches"] = res
                p.setdefault("k0_mode", "unavailable")

    def _autofill_gc_matches(self):
        """Fill the GC (RI) column automatically — no ▶ click needed. Applies any
        cached results, then matches the uncached peaks in the background."""
        peaks = list(self.state.peaks)
        if not peaks:
            return
        self._apply_cached_matches(peaks)
        uncached = [p for p in peaks
                    if (p.get("rt_index"), p.get("dt_index")) not in self.state.match_cache]
        if uncached:
            self._ensure_libraries_then(
                lambda: self._match_and_cache(uncached, refresh=True), notify=False)

    def _match_and_cache(self, targets, refresh=False, then=None):
        """Match `targets` (background), store results in the per-file cache by
        coordinate, then optionally refresh the table / run `then`."""
        ril_rows, iml_rows, _info = self.state.library_rows
        q = queue.Queue()

        def work():
            import match
            out = []
            for p in targets:
                out.append(((p.get("rt_index"), p.get("dt_index")),
                            match.match_all(p, ril_rows, iml_rows)))
            q.put(out)

        threading.Thread(target=work, daemon=True).start()
        self._poll_match_and_cache(q, refresh, then)

    def _poll_match_and_cache(self, q, refresh, then):
        try:
            results = q.get_nowait()
        except queue.Empty:
            self.root.after(120, lambda: self._poll_match_and_cache(q, refresh, then))
            return
        for key, res in results:
            self.state.match_cache[key] = res
        self._apply_cached_matches(self.state.peaks)
        if refresh:
            self._refresh_match_columns()
            self.status_label.config(text="✓ GC (RI) matches ready")
        if then:
            then()

    def _refresh_match_columns(self):
        """Re-render every table row now that peaks carry `matches`."""
        for item in self.peak_tree.get_children():
            vals = self.peak_tree.item(item, "values")
            try:
                pid = int(vals[0])
            except (ValueError, TypeError, IndexError):
                continue
            p = self._peak_by_id(pid)
            if p is not None:
                self._refresh_row(item, p)

    def _open_compound_match_panel(self, peak):
        """Stage 10: list candidate compounds for `peak` in a popup. Matching uses
        the same cache as the GC column, so this is instant once auto-fill ran."""
        def show():
            self._apply_cached_matches([peak])
            info = self.state.library_rows[2]
            if "matches" in peak:
                self._render_match_panel(peak, peak["matches"], info)
            else:
                self._match_and_cache([peak], then=lambda: (
                    self._apply_cached_matches([peak]),
                    self._render_match_panel(peak, peak.get("matches") or {}, info)))

        self._ensure_libraries_then(show, notify=True)

    def _render_match_panel(self, peak, result, select_info):
        """Toplevel listing candidate compounds (combined / GC-only / IMS-only)."""
        import match
        gc_dim = result.get("gc_dimension")
        combined = result.get("combined_matches") or []
        gc_hits = result.get("gc_matches") or []
        ims_hits = result.get("ims_matches") or []
        combined_cas = {c.get("CAS") for c in combined}
        gc_only = [h for h in gc_hits if h.get("CAS") not in combined_cas]
        ims_only = [h for h in ims_hits if h.get("CAS") not in combined_cas]

        win = Toplevel(self.root)
        win.title(f"Peak #{peak.get('peak_id')} — compound candidates")
        win.geometry("720x480")

        # ---- peak summary header ----
        ri = peak.get("ri")
        ri_txt = "—" if ri is None else f"{ri:.1f}{'*' if peak.get('ri_extrapolated') else ''}"
        dim_txt = {"ri": "Retention Index", "rt": "Retention time (s)",
                   None: "none"}.get(gc_dim, str(gc_dim))
        head = (f"Peak #{peak.get('peak_id')}   "
                f"RI={ri_txt}   Rt={peak.get('retention_s', '—')}s   "
                f"Drift(rel RIP)={peak.get('drift_relative', '—')}\n"
                f"GC match dimension: {dim_txt}   |   "
                f"IMS (K0): {'—, no K0 calibration' if not ims_hits else f'{len(ims_hits)} hit(s)'}")
        Label(win, text=head, justify="left", anchor="w",
              font=("Georgia", 9), bg="white").pack(fill="x", padx=8, pady=(8, 2))

        if peak.get("ri_assumed_unverified"):
            Label(win, text="⚠ Peak RI is assumed/borrowed (unverified) — matches are provisional.",
                  fg="firebrick", anchor="w", font=("Georgia", 8), bg="white"
                  ).pack(fill="x", padx=8)

        # ---- candidates tree ----
        cols = ("match", "name", "cas", "formula", "libval", "delta", "source")
        headers = {"match": "Match", "name": "Compound", "cas": "CAS",
                   "formula": "Formula", "libval": "Lib RI/Rt", "delta": "Δ",
                   "source": "Source file"}
        widths = {"match": 70, "name": 190, "cas": 90, "formula": 80,
                  "libval": 70, "delta": 55, "source": 150}
        frame = Frame(win, bg="white")
        frame.pack(fill="both", expand=True, padx=8, pady=6)
        tree = Treeview(frame, columns=cols, show="headings", height=14)
        for c in cols:
            tree.heading(c, text=headers[c])
            tree.column(c, width=widths[c], anchor="w", stretch=(c == "name"))
        vs = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vs.set)
        tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

        def _row(cand, label):
            name = cand.get("Name") or cand.get("NAME") or "?"
            if "ri" in cand.get("match_dimensions", []):
                libval, delta = cand.get("RI"), cand.get("delta_ri")
            elif "rt" in cand.get("match_dimensions", []):
                libval, delta = cand.get("Rt[sec]"), cand.get("delta_rt")
            else:
                libval, delta = cand.get("Dt[a.u.]"), cand.get("delta_k0")
            src = (cand.get("source_file") or cand.get("source_file_gc")
                   or cand.get("source_file_ims") or "—")
            return (label, name, cand.get("CAS") or "—", cand.get("Formula") or "—",
                    "—" if libval is None else f"{libval:g}",
                    "—" if delta is None else f"{delta:.3g}", src)

        # ±10 RI on a 100k-row library can return hundreds of hits; show the
        # closest MAX_PER of each branch (already delta-sorted) to stay usable.
        MAX_PER = 150
        tree.tag_configure("combined", background="#e7f5e7")
        for c in combined:
            tree.insert("", "end", values=_row(c, "GC+IMS"), tags=("combined",))
        for c in gc_only[:MAX_PER]:
            tree.insert("", "end", values=_row(c, "GC"))
        for c in ims_only[:MAX_PER]:
            tree.insert("", "end", values=_row(c, "IMS"))

        total = len(combined) + len(gc_only) + len(ims_only)
        shown = len(combined) + min(len(gc_only), MAX_PER) + min(len(ims_only), MAX_PER)
        trunc = "" if shown == total else f" — showing closest {shown} of {total}"
        foot = (f"{total} candidate(s) within tolerance{trunc} "
                f"(RI ±{match.DEFAULT_RI_TOLERANCE:g}, "
                f"Rt ±{match.DEFAULT_RT_TOLERANCE:g}s, "
                f"K0 ±{match.DEFAULT_K0_TOLERANCE:g}; sorted by Δ).  "
                f"GC lib strategy: {select_info.get('ril_strategy', '?')}"
                + ("" if total else
                   "  —  nothing matched; widen tolerance or check RI calibration."))
        Label(win, text=foot, justify="left", anchor="w",
              font=("Georgia", 8), fg="gray30", bg="white").pack(fill="x", padx=8, pady=(0, 8))

    def generate_numbered_overlay(self):
        """Invoke peak_with_number.py to render an overlay with peak-id numbers."""
        if not self.state.selected_mea_file:
            return
        q = queue.Queue()
        cmd = [sys.executable,
               str(Path(__file__).with_name("peak_with_number.py")),
               self.state.selected_mea_file]
        # 第四階段：校準就緒時，讓疊圈圖 y 軸改標成 RI（x 軸仍是正規化 drift）
        if self.state.ri_calibration is not None:
            cmd += ["--ri-series", RI_SERIES]
        threading.Thread(target=run_subprocess, args=(cmd, q), daemon=True).start()
        self.poll_numbered_overlay(q)

    def poll_numbered_overlay(self, q):
        """Poll the numbered-overlay renderer; swap image in when it finishes."""
        try:
            msg_type, _ = q.get_nowait()
            if msg_type == "stdout":
                self.root.after(100, lambda: self.poll_numbered_overlay(q))
            elif msg_type == "done":
                base = Path(self.state.selected_mea_file).stem
                numbered = f"results/{base}_overlay_numbered.png"
                if os.path.exists(numbered):
                    # Kept as a static artifact for the Batch 8 report only —
                    # swapping it into the canvas would bury the native circles
                    # under baked-in pixels (workflow §第八階段 架構修正).
                    self.state.numbered_overlay_path = numbered
                    self.status_label.config(
                        text=f"✓ Detected {len(self.state.peaks)} peaks "
                             f"(numbered overlay saved for report)")
            elif msg_type == "error":
                # Fall back silently to the plain overlay already displayed.
                pass
        except queue.Empty:
            self.root.after(100, lambda: self.poll_numbered_overlay(q))

    def sort_peak_table(self, col):
        """Sort table by column. Click same column to toggle ascending/descending."""
        # Toggle sort direction if clicking same column
        if self.sort_column == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = col
            self.sort_reverse = False

        # Get all items and their values
        items = []
        for item in self.peak_tree.get_children():
            values = self.peak_tree.item(item, "values")
            items.append((item, values))

        # Sort by the clicked column
        col_index = PEAK_TABLE_COLUMNS.index(col) if col in PEAK_TABLE_COLUMNS else 0

        # Try numeric sort first, fall back to string sort
        try:
            items.sort(key=lambda x: float(x[1][col_index]), reverse=self.sort_reverse)
        except (ValueError, IndexError):
            items.sort(key=lambda x: str(x[1][col_index]) if col_index < len(x[1]) else "", reverse=self.sort_reverse)

        # Re-insert items in sorted order
        for idx, (item, values) in enumerate(items):
            self.peak_tree.move(item, "", idx)

        # Update column header to show sort direction
        for c in PEAK_TABLE_COLUMNS:
            header_text = COORD_LABELS.get(c, c)
            if c == self.sort_column:
                arrow = " ▼" if self.sort_reverse else " ▲"
                header_text += arrow
            self.peak_tree.heading(c, text=header_text)

    def update_peaks_header(self):
        """Update peak table header with matrix dimensions and peak count."""
        header_text = "Detected Peaks:"

        # Add matrix dimensions on left
        if self.state.matrix_shape and len(self.state.matrix_shape) == 2:
            n_rt, n_dt = self.state.matrix_shape
            if n_rt > 0 and n_dt > 0:
                total_points = n_rt * n_dt
                header_text = f"({n_rt:,} × {n_dt:,} = {total_points:,} points)  Detected Peaks:"

        # Add peak count on right, plus how many are currently selected —
        # the two differ as soon as a rule rejects a peak or the user
        # deselects one, and only the selected ones reach the report.
        if self.state.peaks:
            peak_count = len(self.state.peaks)
            selected = sum(1 for p in self.state.peaks if effective_active(p))
            header_text += f"  {peak_count}    Current selected peaks: {selected}"

        self.peaks_header_label.config(text=header_text)

    def on_peak_selected(self, event):
        """User selected a peak from table (or programmatically). Highlight on overlay."""
        selection = self.peak_tree.selection()
        if not selection:
            return

        self.state.selected_peak_row = selection[0]

        # Get selected peak data
        item_id = selection[0]
        item_values = self.peak_tree.item(item_id, "values")

        if not item_values or len(item_values) < 4:
            return

        # Find peak in peaks list by matching values
        try:
            peak_id = int(item_values[0])
            selected_peak = next((p for p in self.state.peaks if p.get("peak_id") == peak_id), None)

            if selected_peak and self.state.overlay_image_size and self.state.matrix_shape:
                self.highlight_peak_on_overlay(selected_peak)
        except (ValueError, IndexError, StopIteration):
            pass

    def highlight_peak_on_overlay(self, peak):
        """Draw yellow circle on main canvas at the peak's zoom-aware position."""
        # Clear previous highlight
        if self.state.highlight_id:
            try:
                self.main_canvas.delete(self.state.highlight_id)
            except Exception:
                pass
            self.state.highlight_id = None

        # Uses the recorded plot-area bbox. The previous version mapped
        # dt_index/n_dt onto the whole PNG, ignoring the matplotlib margins
        # (~8.5% on the left alone), so the yellow ring never sat on its peak.
        xy = self._peak_to_image_xy(peak)
        if xy is None:
            return
        x_px = self.main_canvas_pan_x + xy[0] * self.main_canvas_zoom
        y_px = self.main_canvas_pan_y + xy[1] * self.main_canvas_zoom

        radius = 12
        self.state.highlight_id = self.main_canvas.create_oval(
            x_px - radius, y_px - radius,
            x_px + radius, y_px + radius,
            outline="yellow", width=3, fill=""
        )
        self.main_canvas.tag_raise(self.state.highlight_id)

        peak_id = peak.get("peak_id", "?")
        self.status_label.config(
            text=f"✓ Peak #{peak_id} highlighted @ (x={peak.get('drift_ms', 0):.2f}ms, "
                 f"y={peak.get('retention_s', 0):.2f}s)"
        )

    # (on_main_canvas_click removed — main_canvas now handles zoom/pan in-place.
    # ImageViewerDialog is still available for popups if we ever wire it back in.)

    def find_nearest_peak_to_click(self, click_x, click_y, threshold=20):
        """Find peak closest to click position. Return peak if within threshold."""
        if not self.state.matrix_shape or not self.state.overlay_image_size:
            return None

        n_rt, n_dt = self.state.matrix_shape
        img_w, img_h = self.state.overlay_image_size

        nearest_peak = None
        min_distance = threshold

        for peak in self.state.peaks:
            rt_index = peak.get("rt_index", 0)
            dt_index = peak.get("dt_index", 0)

            # Convert peak matrix indices to canvas pixel coordinates
            x_normalized = dt_index / n_dt if n_dt > 0 else 0
            y_normalized = rt_index / n_rt if n_rt > 0 else 0
            peak_x = int(x_normalized * img_w)
            peak_y = int(y_normalized * img_h)

            # Calculate distance from click to peak
            distance = ((click_x - peak_x) ** 2 + (click_y - peak_y) ** 2) ** 0.5

            if distance < min_distance:
                min_distance = distance
                nearest_peak = peak

        return nearest_peak

    def select_peak_in_table(self, peak_id):
        """Select and highlight peak row in table by peak_id."""
        # Find table row with matching peak_id
        for item in self.peak_tree.get_children():
            item_values = self.peak_tree.item(item, "values")
            if item_values and len(item_values) > 0:
                try:
                    if int(item_values[0]) == peak_id:
                        # Clear previous selection and select this row
                        self.peak_tree.selection_set(item)
                        self.peak_tree.see(item)  # Scroll to make visible
                        # Trigger highlight
                        self.on_peak_selected(None)
                        return
                except (ValueError, IndexError):
                    pass

    # ------------------------------------------------------------------------- #
    # New toolbar action handlers (workflow §第八階段)
    # ------------------------------------------------------------------------- #
    def on_show_original_heatmap(self):
        """Swap main canvas back to the raw heatmap (no popup — in-place).

        User can zoom / pan directly on main_canvas via wheel + left-drag.
        """
        if not self.state.heatmap_img_path or not os.path.exists(self.state.heatmap_img_path):
            messagebox.showinfo(
                "Not ready", "No heatmap yet — pick a .mea and wait for auto-read."
            )
            return
        self._render_main_from_path(self.state.heatmap_img_path, kind="heatmap")
        self.status_label.config(text="Showing original heatmap")

    # ------------------------------------------------------------------------- #
    # Rules panel (workflow §第七階段) — live re-selection
    # ------------------------------------------------------------------------- #
    def _recompute_selection(self, config):
        """Re-run the full candidate funnel in memory for `config`.

        Returns (peaks, stats). Requires the _maxima.npz cache: R004 changes
        which candidates exist *before* max_prominence is taken, so the
        prominence gate has to be recomputed, not just re-filtered.
        """
        m = self.state.maxima
        if m is None:
            return None, None
        params = (self.state.detection_meta or {}).get("params", {})
        half_width, exclude_before, boundary = peaks_mod.pre_gate_params(config)
        rip_index = int(m["rip_index"])
        floor = float(m["floor"])

        peaks, stats = peaks_mod.select_from_maxima(
            m["pix"], m["val_smooth"], m["prom"], m["val_raw"], m["flatness"],
            tuple(int(v) for v in m["shape"]), floor,
            prom_frac=params.get("prom_frac", 0.02),
            min_prominence=params.get("min_prominence", 0.0),
            min_distance=params.get("min_distance", 3),
            top_n=params.get("top_n", 0),
            rip_index=rip_index, rip_half_width=half_width,
            exclude_before_rip=exclude_before, rip_boundary=boundary, verbose=False,
        )
        peaks_mod.attach_coords(peaks, m["drift_ms"], m["retention_s"])
        rip_mod.attach_drift_relative(peaks, rip_index)
        for p in peaks:
            p.pop("_val_smooth", None)

        # mark_rules, not apply_rules: rejected peaks stay on screen as
        # stippled circles / grey rows rather than vanishing, so the user can
        # see what a rule did (and override it — see effective_active()).
        report = rules.mark_rules(
            peaks, config, context={"floor": floor, "rip_index": rip_index})
        self._apply_saved_peak_state(peaks)
        stats["n_before_rules"] = len(peaks)
        stats["n_after_rules"] = report["n_out"]
        stats["n_selected"] = sum(1 for p in peaks if effective_active(p))
        stats["rules_report"] = report
        return peaks, stats

    def on_open_rules(self):
        """Rules panel: toggle a rule, see the circles change immediately."""
        if self.state.rules_config is None:
            self.state.rules_config = rules.load_config(RULES_CONFIG)

        win = Toplevel(self.root)
        win.title("Rules — live peak selection")
        # Top-right, not Tk's default +0+0 — there it covered the heatmap,
        # which is exactly what the user needs to watch while toggling rules.
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        panel_w = min(480, screen_w - 60)
        panel_h = min(600, screen_h - 100)
        win.geometry(f"{panel_w}x{panel_h}+{max(0, screen_w - panel_w - 20)}+40")
        win.transient(self.root)
        outer = Frame(win, padx=12, pady=10)
        outer.pack(fill="both", expand=True)

        # Merge saved config over the registry so every rule gets a row
        defaults = {e["rule_number"]: e for e in rules.default_config()}
        saved = {e.get("rule_number"): e for e in self.state.rules_config}
        widgets = {}   # rule_number -> {"on": BooleanVar, "params": {name: StringVar}}

        Label(outer, text="Rules", font=("TkDefaultFont", 11, "bold"),
              anchor="w").pack(fill="x")
        rules_frame = Frame(outer)
        rules_frame.pack(fill="x", pady=(4, 10))

        for row, rule in enumerate(rules.list_rules()):
            rn = rule["rule_number"]
            entry = saved.get(rn) or defaults.get(rn) or {"enabled": False, "params": {}}
            # R004 / R006 define the numbering baseline, so they cannot be
            # switched off — the checkbox is locked on rather than hidden, so
            # it stays visible that they are running.
            locked = rule.get("mandatory", False)
            on_var = BooleanVar(value=True if locked else bool(entry.get("enabled")))
            chk = ttk.Checkbutton(rules_frame, variable=on_var,
                                  text=f"{rn}  {rule['name']}"
                                       + ("   (always on)" if locked else ""),
                                  command=lambda: self._on_rules_changed(widgets))
            if locked:
                chk.state(["disabled"])
            chk.grid(row=row, column=0, sticky="w", pady=2)

            pvars = {}
            pframe = Frame(rules_frame)
            pframe.grid(row=row, column=1, sticky="w", padx=(16, 0))
            for name, value in (entry.get("params") or {}).items():
                Label(pframe, text=f"{name} =").pack(side="left")
                sv = StringVar(value=str(value))
                ent = ttk.Entry(pframe, textvariable=sv, width=8)
                ent.pack(side="left", padx=(2, 10))
                ent.bind("<KeyRelease>",
                         lambda e: self._on_rules_changed(widgets, debounce=True))
                pvars[name] = (sv, ent)
            widgets[rn] = {"on": on_var, "params": pvars, "type": rule["type"]}

        rules_frame.columnconfigure(1, weight=1)

        Label(outer, text="Selection funnel", font=("TkDefaultFont", 11, "bold"),
              anchor="w").pack(fill="x")
        self._rules_funnel = Text(outer, height=8, wrap="none",
                                  font=("Consolas", 9), relief="solid", borderwidth=1)
        self._rules_funnel.pack(fill="both", expand=True, pady=(4, 8))

        self._rules_note = Label(outer, text="", anchor="w", justify="left",
                                 wraplength=panel_w - 40, fg="gray25")
        self._rules_note.pack(fill="x", pady=(0, 8))

        btns = Frame(outer)
        btns.pack(fill="x")
        ttk.Button(btns, text="Save to rules_config.json",
                   command=lambda: self._save_rules(widgets)).pack(side="left")
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right")

        self._rules_win = win
        self._on_rules_changed(widgets)

    def _collect_rules_config(self, widgets):
        """Read the panel widgets back into a rules_config list."""
        config, bad = [], []
        for rn, w in widgets.items():
            params = {}
            for name, (sv, _ent) in w["params"].items():
                raw = sv.get().strip()
                try:
                    params[name] = int(raw) if raw.lstrip("-").isdigit() else float(raw)
                except ValueError:
                    bad.append(f"{rn}.{name}")
                    params[name] = 0
            config.append({"rule_number": rn, "enabled": bool(w["on"].get()),
                           "params": params})
        # Safety net: the mandatory checkboxes are locked on, but enforce it
        # here too so a future rule marked mandatory can never slip through
        # just because its panel row was built before the flag existed.
        return rules._enforce_mandatory(config), bad

    def _on_rules_changed(self, widgets, debounce=False):
        """Recompute the selection and push it to table + canvas + funnel."""
        if debounce:
            if getattr(self, "_rules_after_id", None):
                self.root.after_cancel(self._rules_after_id)
            self._rules_after_id = self.root.after(
                350, lambda: self._on_rules_changed(widgets))
            return
        self._rules_after_id = None

        config, bad = self._collect_rules_config(widgets)
        self.state.rules_config = config

        notes = []
        if bad:
            notes.append(f"⚠ Not a number, treated as 0: {', '.join(bad)}")

        peaks, stats = self._recompute_selection(config)
        if peaks is None:
            self._rules_funnel_set("(no _maxima.npz cache for this file — run "
                                   "Show Detected Peak Heatmap once to build it)")
            notes.append("Rules cannot be applied live without the maxima cache.")
            self._rules_note.config(text="\n".join(notes))
            return

        self.populate_peak_table(peaks)
        self.update_peaks_header()
        if self.main_canvas_kind == "bg":
            # Already on the peak canvas — redraw circles only, so the user's
            # current zoom/pan survives repeated rule tweaks
            self._render_main_canvas()
        else:
            self._show_peak_canvas()        # switch over so the change is visible
        self._rules_funnel_set(self._format_funnel(stats))

        if any(p.get("flatness") is None for p in peaks) and \
                any(e["rule_number"] == "R003" and e["enabled"] for e in config):
            notes.append("⚠ R003 is inert for peaks that appeared after the last "
                         "full detection — flatness is only measured during "
                         "detection, so those peaks pass unchecked.")
        if stats.get("rules_report", {}).get("rip_missing_warning"):
            notes.append("⚠ R004 enabled but peaks lack drift_relative.")
        # Prose lives here, not in the funnel box: that box is wrap="none" so
        # its columns stay aligned, which would clip sentences at this width.
        notes.append("Peaks failing an optional rule are not removed — they stay "
                     "as stippled circles / grey rows, and clicking one keeps it "
                     "anyway (dashed ring).")
        notes.append("Changes are live but not saved until you press Save.")
        self._rules_note.config(text="\n".join(notes))
        self.status_label.config(text=f"Rules applied → {len(peaks)} peaks")

    def _rules_funnel_set(self, text):
        self._rules_funnel.config(state="normal")
        self._rules_funnel.delete("1.0", END)
        self._rules_funnel.insert(END, text)
        self._rules_funnel.config(state="disabled")

    def _save_rules(self, widgets):
        config, bad = self._collect_rules_config(widgets)
        if bad:
            # Saving a typo as 0 silently would quietly change what the next
            # full detection does — refuse instead.
            messagebox.showerror(
                "Not saved",
                "These values are not numbers:\n  " + "\n  ".join(bad) +
                "\n\nFix them and save again.")
            return
        try:
            rules.save_config(RULES_CONFIG, config)
            self.state.rules_config = config
            messagebox.showinfo("Saved", f"Written to {RULES_CONFIG}\n\n"
                                         "The next full detection will use it.")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    @staticmethod
    def _format_funnel(stats):
        """Render the candidate funnel produced by peaks.select_from_maxima()."""
        if not stats:
            return "(no detection loaded)"

        def n(key):
            v = stats.get(key)
            return f"{v:,}" if isinstance(v, int) else "—"

        def row(label, key):
            """One funnel line: label left, count right-aligned in a fixed column."""
            return f"{label:<34}{n(key):>10}"

        hw = stats.get("rip_half_width") or 0
        cut = stats.get("drift_cut_dt_index")
        # Both mandatory rules cut the drift axis, so name whichever applied
        cut_label = f"  − RIP cut (R004 ±{hw} + R006)" if cut \
                    else f"  − R004 RIP band (±{hw})"

        lines = [row("raw local maxima (above floor)", "n_raw_maxima"),
                 row(cut_label, "n_rip_excluded")]
        if cut:
            lines.append(f"       keep dt_index > {cut:g}")
        lines += [
            row(f"  → prominence ≥ {stats.get('prom_threshold', '—')}",
                "n_after_prom"),
            f"       max_prominence = {stats.get('max_prominence', '—')}",
            row("  → min_distance dedup", "n_after_distance"),
            row("  → top_n", "n_final"),
            row("  → optional rules pass", "n_after_rules"),
            row("  → selected (rules + your picks)", "n_selected"),
        ]
        return "\n".join(lines)

    def on_generate_report(self):
        """Generate Report placeholder (workflow §第十一階段).

        Batch 8 placeholder — export format not yet finalized.
        """
        messagebox.showinfo(
            "Generate Report — not yet implemented",
            "Report generation is planned for UI Batch 8 of the Identify Workflow\n"
            "rollout. Content structure is defined in Report_Content_Example.md."
        )

    # ------------------------------------------------------------------------- #
    # Settings menu handlers
    # ------------------------------------------------------------------------- #
    def on_browse_library_data(self):
        """Ask user for library data folder; persist to ui_settings.json."""
        current = self.state.library_dir or library.resolve_data_dir() or ""
        chosen = filedialog.askdirectory(
            title="Select library data folder (contains .ril / .iml files)",
            initialdir=current if current else None,
        )
        if not chosen:
            return
        self.state.library_dir = chosen
        self.state.settings["library_dir"] = chosen
        save_settings(self.state.settings)
        self.status_label.config(text=f"Library data: {chosen}")

    def on_reset_library_data(self):
        """Clear stored library_dir → fall back to library.resolve_data_dir() chain."""
        self.state.library_dir = None
        self.state.settings.pop("library_dir", None)
        save_settings(self.state.settings)
        resolved = library.resolve_data_dir()
        msg = f"Reset. Library data resolves to:\n  {resolved or '(none — nothing found)'}"
        messagebox.showinfo("Library data reset", msg)
        self.status_label.config(text=f"Library data: {resolved or '(none)'}")

    def on_show_library_location(self):
        """Report where library data currently resolves to."""
        resolved = library.resolve_data_dir(explicit=self.state.library_dir)
        if resolved:
            n_ril = len([f for f in os.listdir(resolved) if f.lower().endswith(".ril")]) \
                if os.path.isdir(resolved) else 0
            n_iml = len([f for f in os.listdir(resolved) if f.lower().endswith(".iml")]) \
                if os.path.isdir(resolved) else 0
            messagebox.showinfo(
                "Library data location",
                f"Resolved: {resolved}\n\n{n_ril} .ril files, {n_iml} .iml files"
            )
        else:
            messagebox.showwarning(
                "Library data not found",
                "No library data folder is set and no default was found.\n\n"
                "Use Settings → Browse Library Data... to pick a folder,\n"
                "or copy .ril / .iml files into <project>/library_data/"
            )

    def on_exit(self):
        """Exit application."""
        self.root.quit()

    # --- State Management --- #

    def update_button_state(self):
        """Enable/disable buttons based on current state (new toolbar per §第八階段).

        Buttons:
          browse_btn            always available except during subprocess run
          view_original_btn     enabled once read done (heatmap image exists)
          show_detected_btn     enabled once read done; explicit click (83 s)
          rules_btn             always available (opens rules panel, independent of data)
          generate_report_btn   enabled only after peaks detected
        """
        button_states = {
            UIState.START: {
                "browse_btn": "normal",
                "view_original_btn": "disabled",
                "show_detected_btn": "disabled",
                "rules_btn": "normal",
                "generate_report_btn": "disabled",
            },
            UIState.FOLDER_SELECTED: {
                "browse_btn": "normal",
                "view_original_btn": "disabled",
                "show_detected_btn": "disabled",
                "rules_btn": "normal",
                "generate_report_btn": "disabled",
            },
            UIState.FILE_SELECTED: {
                # 選檔後立刻進 auto-read，此狀態為極短暫過渡；一律停用其餘按鈕
                "browse_btn": "disabled",
                "view_original_btn": "disabled",
                "show_detected_btn": "disabled",
                "rules_btn": "normal",
                "generate_report_btn": "disabled",
            },
            UIState.READING: {
                "browse_btn": "disabled",
                "view_original_btn": "disabled",
                "show_detected_btn": "disabled",
                "rules_btn": "normal",
                "generate_report_btn": "disabled",
            },
            UIState.READ_DONE: {
                "browse_btn": "normal",
                "view_original_btn": "normal",
                "show_detected_btn": "normal",
                "rules_btn": "normal",
                "generate_report_btn": "disabled",
            },
            UIState.DETECTING: {
                "browse_btn": "disabled",
                "view_original_btn": "normal",   # heatmap 仍可看
                "show_detected_btn": "disabled",
                "rules_btn": "normal",
                "generate_report_btn": "disabled",
            },
            UIState.PEAKS_DETECTED: {
                "browse_btn": "normal",
                "view_original_btn": "normal",
                "show_detected_btn": "normal",
                "rules_btn": "normal",
                "generate_report_btn": "normal",
            },
            UIState.ERROR: {
                "browse_btn": "normal",
                "view_original_btn": (
                    "normal" if self.state.heatmap_img_path else "disabled"
                ),
                "show_detected_btn": (
                    "normal" if self.state.selected_mea_file else "disabled"
                ),
                "rules_btn": "normal",
                "generate_report_btn": "disabled",
            },
        }

        states = button_states.get(self.ui_state, {})
        for btn_name, btn_state in states.items():
            btn = getattr(self, btn_name)
            btn.config(state=btn_state)


# ============================================================================== #
# Main Entry Point
# ============================================================================== #

def main():
    root = Tk()
    app = GCIMSApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()







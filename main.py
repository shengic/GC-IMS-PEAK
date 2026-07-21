"""
main.py  —  GC-IMS Desktop UI (Tk)
Version: 1.1 — by Albert Sheng

A desktop application for browsing, reading, and analyzing GC-IMS .mea files.
Workflow: browse folder → select .mea (auto-read) → show detected peaks → inspect.

Changelog:
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
    Toplevel, Menu,
)
from tkinter.ttk import Treeview, PanedWindow

import numpy as np
from PIL import Image, ImageTk

import library


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
    "intensity": "Intensity",
    "prominence": "Prominence",
    "flatness": "Flatness",
    "edge_dist": "Edge Dist",
    "saturated": "Saturated",
    # Batch 2: identification columns
    "on": "On",
    "gc_ims": "GC×IMS",
    "gc": "GC",
    "ims": "IMS",
    "trigger": "▶",
}

# Full column layout per workflow §第八階段 draft.12 (drift_relative inserted
# after drift_ms per this session's decision). Match values (gc_ims/gc/ims) are
# placeholders "—" until Batch 5 wires identify.py into main.py.
PEAK_TABLE_COLUMNS = (
    "peak_id", "drift_ms", "drift_relative", "retention_s", "intensity",
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
        self.selected_peak_row = None
        self.heatmap_photo_ref = None
        self.overlay_photo_ref = None
        self.highlight_id = None
        self.matrix_shape = None  # (n_rt, n_dt) from JSON
        self.overlay_canvas_size = None  # (width, height) of canvas
        self.overlay_image_size = None  # (width, height) of actual image

        # Persisted settings (ui_settings.json)
        self.settings = load_settings()
        self.library_dir = self.settings.get("library_dir")   # None → resolve chain

        # In-place zoom/pan state for main_canvas
        self.main_canvas_original = None    # PIL.Image at native resolution
        self.main_canvas_zoom = 1.0
        self.main_canvas_pan_x = 0
        self.main_canvas_pan_y = 0
        self.main_canvas_kind = None        # "heatmap" | "overlay" | None
        self._pan_last_x = None
        self._pan_last_y = None

    def reset_after_file_selection(self):
        """Clear heatmap/peaks/overlay when user selects a new file."""
        self.heatmap_path = None
        self.overlay_path = None
        self.heatmap_img_path = None
        self.overlay_img_path = None
        self.peaks_json_path = None
        self.peaks_csv_path = None
        self.peaks = []
        self.selected_peak_row = None
        self.heatmap_photo_ref = None
        self.overlay_photo_ref = None
        self.highlight_id = None


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
        self.root.title("GC-IMS Peak Detection — v1.1 by Albert Sheng")
        # Wider default to accommodate three-pane layout (files | main image | peak table)
        self.root.geometry("1700x950")
        self.root.minsize(1400, 800)

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
        self.main_canvas.bind("<ButtonPress-1>", self.on_main_pan_start)
        self.main_canvas.bind("<B1-Motion>", self.on_main_pan_drag)
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
            "retention_s": 60, "intensity": 55,
            "on": 35, "gc_ims": 100, "gc": 35, "ims": 35, "trigger": 25,
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

    def _start_read(self):
        """Kick off readGAS.py subprocess (formerly on_read_file button handler)."""
        if not self.state.selected_mea_file:
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

        # If we already have an overlay for this file, just swap the view
        if self.state.overlay_img_path and os.path.exists(self.state.overlay_img_path):
            self._render_main_from_path(self.state.overlay_img_path, kind="overlay")
            self.status_label.config(text="Showing detected-peaks heatmap")
            return

        self.ui_state = UIState.DETECTING
        self.update_button_state()
        self.status_label.config(text="Detecting peaks (this can take ~80 s)...")
        self._progress_start()

        output_queue = queue.Queue()
        cmd = [sys.executable, str(Path(__file__).with_name("peaks.py")), self.state.selected_mea_file]
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

                if os.path.exists(overlay):
                    self.load_overlay(overlay)
                    self.state.overlay_path = overlay

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
            p.setdefault("active", True)
        self.state.peaks = peaks
        for peak in peaks:
            values = tuple(self._cell_value(col, peak) for col in PEAK_TABLE_COLUMNS)
            self.peak_tree.insert("", "end", values=values)

        # Update header with matrix dimensions
        self.update_peaks_header()
        self.sort_column = None
        self.sort_reverse = False

        # Render a numbered overlay (circles + peak-id numbers) via peak_with_number.py
        # and swap it in when ready. Perfectly aligned since it's drawn in data coords.
        self.generate_numbered_overlay()

    # ------------------------------------------------------------------------- #
    # Peak-table cell rendering & click dispatch (Batch 2)
    # ------------------------------------------------------------------------- #
    def _cell_value(self, col, peak):
        """Return display string for one cell of the peak table."""
        if col == "on":
            return CHECK_ON if peak.get("active", True) else CHECK_OFF

        m = peak.get("matches") or {}
        if col == "gc_ims":
            combined = m.get("combined_matches") or []
            if combined:
                return combined[0].get("Name") or combined[0].get("NAME") or "?"
            return "—"
        if col == "gc":
            gc_hits = m.get("gc_matches")
            if gc_hits is None:
                return "—"
            combined_cas = {c.get("CAS") for c in (m.get("combined_matches") or [])}
            return str(sum(1 for h in gc_hits if h.get("CAS") not in combined_cas))
        if col == "ims":
            if peak.get("k0_mode") == "unavailable":
                return "—"
            ims_hits = m.get("ims_matches")
            if ims_hits is None:
                return "—"
            combined_cas = {c.get("CAS") for c in (m.get("combined_matches") or [])}
            return str(sum(1 for h in ims_hits if h.get("CAS") not in combined_cas))
        if col == "trigger":
            return TRIGGER_ACTIVE if peak.get("active", True) else TRIGGER_DIM

        # Default: raw peak field
        return peak.get(col)

    def _peak_by_id(self, peak_id):
        for p in self.state.peaks:
            if p.get("peak_id") == peak_id:
                return p
        return None

    def _refresh_row(self, item_id, peak):
        """Rebuild the values of one row (used after On toggle etc)."""
        values = tuple(self._cell_value(col, peak) for col in PEAK_TABLE_COLUMNS)
        self.peak_tree.item(item_id, values=values)

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
            peak["active"] = not peak.get("active", True)
            self._refresh_row(row_id, peak)
            # Batch 3 will sync back to overlay circle + tag styling here.
        elif col_name == "trigger":
            if not peak.get("active", True):
                # Workflow §第八階段: ▶ disabled when inactive
                return
            self._open_compound_match_panel(peak)

    def _open_compound_match_panel(self, peak):
        """Placeholder for compound-match panel (Batch 5)."""
        messagebox.showinfo(
            "Compound match panel — not yet implemented",
            f"Peak #{peak.get('peak_id')} → the compound-match panel is planned\n"
            f"for UI Batch 5. It will show the three-strip stacked view with\n"
            f"confidence dots and source_file trace using data from identify.py."
        )

    def generate_numbered_overlay(self):
        """Invoke peak_with_number.py to render an overlay with peak-id numbers."""
        if not self.state.selected_mea_file:
            return
        q = queue.Queue()
        cmd = [sys.executable,
               str(Path(__file__).with_name("peak_with_number.py")),
               self.state.selected_mea_file]
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
                    self.load_overlay(numbered)
                    self.state.overlay_path = numbered
                    self.status_label.config(
                        text=f"✓ Detected {len(self.state.peaks)} peaks (numbered overlay ready)")
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

        # Add peak count on right
        if self.state.peaks:
            peak_count = len(self.state.peaks)
            header_text += f"  {peak_count}"

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

        if not self.state.matrix_shape or not self.state.overlay_image_size:
            return
        n_rt, n_dt = self.state.matrix_shape
        iw, ih = self.state.overlay_image_size

        rt_index = peak.get("rt_index", 0)
        dt_index = peak.get("dt_index", 0)

        if n_dt > 0 and n_rt > 0:
            # Position in image pixels (0..iw, 0..ih), then apply current zoom + pan
            x_img = (dt_index / n_dt) * iw
            y_img = (rt_index / n_rt) * ih
            x_px = self.main_canvas_pan_x + int(x_img * self.main_canvas_zoom)
            y_px = self.main_canvas_pan_y + int(y_img * self.main_canvas_zoom)

            radius = 12
            self.state.highlight_id = self.main_canvas.create_oval(
                x_px - radius, y_px - radius,
                x_px + radius, y_px + radius,
                outline="yellow", width=3, fill=""
            )
            self.main_canvas.tag_raise(self.state.highlight_id)

            # Update status
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

    def on_open_rules(self):
        """Open Rules management panel (workflow §第七階段).

        Batch 4 placeholder — panel not yet implemented.
        """
        messagebox.showinfo(
            "Rules panel — not yet implemented",
            "The Rules management panel is planned for UI Batch 4 of the Identify\n"
            "Workflow rollout. For now, edit rules_config.json manually if needed."
        )

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







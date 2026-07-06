"""
main.py  —  GC-IMS Desktop UI (Tk)
Version: 1.0 — by Albert Sheng

A desktop application for browsing, reading, and analyzing GC-IMS .mea files.
Workflow: browse folder → select .mea → read (readGAS.py) → detect peaks (peaks.py) → inspect & export.

Usage:
    python main.py
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from enum import Enum
from pathlib import Path
from tkinter import (
    filedialog, messagebox, ttk, Canvas, Frame, Label, Tk, Text, END, Toplevel
)
from tkinter.ttk import Treeview, PanedWindow

import numpy as np
from PIL import Image, ImageTk

# ============================================================================== #
# Constants & Enums
# ============================================================================== #

COORD_LABELS = {
    "peak_id": "#",
    "drift_ms": "Drift Time [ms]",
    "retention_s": "Retention Time [s]",
    "intensity": "Intensity",
    "prominence": "Prominence",
    "flatness": "Flatness",
    "edge_dist": "Edge Dist",
    "saturated": "Saturated",
}

PEAK_TABLE_COLUMNS = ("peak_id", "drift_ms", "retention_s", "intensity")


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
        self.root.title("GC-IMS Peak Detection — v1.0 by Albert Sheng")
        self.root.geometry("1400x850")
        self.root.minsize(1200, 750)

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

        self.browse_btn = ttk.Button(
            self.top_frame, text="Browse Folder", command=self.on_browse_folder, width=15
        )
        self.browse_btn.pack(side="left", padx=4, pady=2)

        self.read_btn = ttk.Button(
            self.top_frame, text="Read File", command=self.on_read_file, width=12
        )
        self.read_btn.pack(side="left", padx=4, pady=2)

        self.detect_btn = ttk.Button(
            self.top_frame, text="Detect Peaks", command=self.on_detect_peaks, width=14
        )
        self.detect_btn.pack(side="left", padx=4, pady=2)

        self.export_heatmap_btn = ttk.Button(
            self.top_frame, text="Export Heatmap", command=self.on_export_heatmap, width=15
        )
        self.export_heatmap_btn.pack(side="left", padx=4, pady=2)

        self.export_overlay_btn = ttk.Button(
            self.top_frame, text="Export Overlay", command=self.on_export_overlay, width=14
        )
        self.export_overlay_btn.pack(side="left", padx=4, pady=2)

        self.export_csv_btn = ttk.Button(
            self.top_frame, text="Export Peaks CSV", command=self.on_export_peaks, width=14
        )
        self.export_csv_btn.pack(side="left", padx=4, pady=2)

        # Main content: file list | images | peak table (paned)
        main_pane = PanedWindow(self.root, orient="horizontal")
        main_pane.pack(fill="both", expand=True, padx=5, pady=5)

        # Left: file list
        left_frame = Frame(main_pane, bg="white")
        main_pane.add(left_frame, weight=1)

        Label(left_frame, text="MEA Files:", bg="white").pack(side="top", pady=5)
        self.file_tree = Treeview(left_frame, columns=(), height=20)
        self.file_tree.column("#0", width=300)
        self.file_tree.heading("#0", text="Filename")
        self.file_tree.pack(fill="both", expand=True)
        self.file_tree.bind("<<TreeviewSelect>>", self.on_file_selected)

        # Center-right: images + table
        right_frame = PanedWindow(main_pane, orient="vertical")
        main_pane.add(right_frame, weight=2)

        # Images (heatmap + overlay side by side, container frame)
        img_frame = Frame(right_frame, bg="white")
        right_frame.add(img_frame)

        # Heatmap canvas with label (click to zoom)
        heatmap_container = Frame(img_frame, bg="white")
        heatmap_container.pack(side="left", padx=5, pady=5, fill="both", expand=True)
        Label(heatmap_container, text="Heatmap (click to zoom)", bg="white", font=("Georgia", 9)).pack()
        self.heatmap_canvas = Canvas(heatmap_container, bg="white", width=420, height=350, cursor="hand2")
        self.heatmap_canvas.pack(fill="both", expand=True)
        self.heatmap_canvas.bind("<Button-1>", self.on_heatmap_click)

        # Overlay canvas with label (click to open zoom viewer for peak selection)
        overlay_container = Frame(img_frame, bg="white")
        overlay_container.pack(side="left", padx=5, pady=5, fill="both", expand=True)
        Label(overlay_container, text="Overlay (click to zoom & select peaks)", bg="white", font=("Georgia", 9)).pack()
        self.overlay_canvas = Canvas(overlay_container, bg="white", width=420, height=350, cursor="hand2")
        self.overlay_canvas.pack(fill="both", expand=True)
        self.overlay_canvas.bind("<Button-1>", self.on_overlay_click_open_zoom)

        # Peak table
        table_frame = Frame(right_frame, bg="white")
        right_frame.add(table_frame)

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
        col_widths = {"peak_id": 6, "drift_ms": 50, "retention_s": 50, "intensity": 50}
        for col in PEAK_TABLE_COLUMNS:
            width = col_widths.get(col, 100)
            self.peak_tree.column(col, width=width, anchor="center")
            # Make column headers clickable for sorting
            self.peak_tree.heading(col, text=COORD_LABELS.get(col, col),
                                  command=lambda c=col: self.sort_peak_table(c))

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

    # --- Event Handlers --- #

    def on_browse_folder(self):
        """Browse for folder containing .mea files."""
        folder = filedialog.askdirectory(title="Select folder with .mea files")
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
        """User selected a file from list."""
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

    def on_read_file(self):
        """Invoke readGAS.py in background."""
        if not self.state.selected_mea_file:
            messagebox.showwarning("No file", "Please select a file first.")
            return

        self.ui_state = UIState.READING
        self.update_button_state()
        self.status_label.config(text=f"Reading {Path(self.state.selected_mea_file).name}...")

        output_queue = queue.Queue()
        cmd = [sys.executable, str(Path(__file__).with_name("readGAS.py")), self.state.selected_mea_file, "--no-show"]
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
                messagebox.showerror("Read failed", msg_content[:200])
                self.ui_state = UIState.ERROR
                self.update_button_state()
        except queue.Empty:
            self.root.after(100, lambda: self.poll_read_output(q))

    def load_heatmap(self, path):
        """Load and display heatmap image. Extract matrix shape from .npz file."""
        try:
            img = Image.open(path)
            img.thumbnail((420, 350), Image.Resampling.LANCZOS)
            self.state.heatmap_photo_ref = ImageTk.PhotoImage(img)
            self.heatmap_canvas.delete("all")
            self.heatmap_canvas.create_image(0, 0, image=self.state.heatmap_photo_ref, anchor="nw")
            self.state.heatmap_img_path = path  # Store path for zoom viewer

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

    def on_detect_peaks(self):
        """Invoke peaks.py in background."""
        if not self.state.selected_mea_file:
            messagebox.showwarning("No file", "Please select a file first.")
            return

        self.ui_state = UIState.DETECTING
        self.update_button_state()
        self.status_label.config(text="Detecting peaks...")

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
                messagebox.showerror("Detection failed", msg_content[:200])
                self.ui_state = UIState.ERROR
                self.update_button_state()
        except queue.Empty:
            self.root.after(100, lambda: self.poll_detect_output(q))

    def load_overlay(self, path):
        """Load and display overlay image (keeping heatmap visible)."""
        try:
            img = Image.open(path)
            original_size = img.size  # (width, height) before scaling
            img.thumbnail((420, 350), Image.Resampling.LANCZOS)
            scaled_size = img.size  # (width, height) after scaling

            self.state.overlay_photo_ref = ImageTk.PhotoImage(img)
            self.overlay_canvas.delete("all")
            self.overlay_canvas.create_image(0, 0, image=self.state.overlay_photo_ref, anchor="nw")
            self.state.overlay_img_path = path  # Store path for zoom viewer

            # Store image and canvas dimensions for peak labeling
            self.state.overlay_image_size = scaled_size
            self.state.overlay_canvas_size = (self.overlay_canvas.winfo_width(),
                                              self.overlay_canvas.winfo_height())
        except Exception as e:
            messagebox.showwarning("Image load failed", f"Could not load {path}:\n{e}")

    def populate_peak_table(self, peaks):
        """Populate peak table from peak list."""
        self.peak_tree.delete(*self.peak_tree.get_children())
        self.state.peaks = peaks
        for peak in peaks:
            values = tuple(peak.get(col) for col in PEAK_TABLE_COLUMNS)
            self.peak_tree.insert("", "end", values=values)

        # Update header with matrix dimensions
        self.update_peaks_header()
        self.sort_column = None
        self.sort_reverse = False

        # Render a numbered overlay (circles + peak-id numbers) via peak_with_number.py
        # and swap it in when ready. Perfectly aligned since it's drawn in data coords.
        self.generate_numbered_overlay()

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
        """Draw yellow circle on overlay canvas to highlight selected peak."""
        # Clear previous highlight
        if self.state.highlight_id:
            self.overlay_canvas.delete(self.state.highlight_id)

        # Get matrix dimensions and image size
        n_rt, n_dt = self.state.matrix_shape
        img_w, img_h = self.state.overlay_image_size

        # Get peak matrix indices
        rt_index = peak.get("rt_index", 0)
        dt_index = peak.get("dt_index", 0)

        # Convert matrix indices to image pixel coordinates
        # dt_index is column → x coordinate
        # rt_index is row → y coordinate
        if n_dt > 0 and n_rt > 0:
            x_normalized = dt_index / n_dt
            y_normalized = rt_index / n_rt

            x_px = int(x_normalized * img_w)
            y_px = int(y_normalized * img_h)

            # Draw highlight circle (yellow outline, no fill)
            radius = 12
            self.state.highlight_id = self.overlay_canvas.create_oval(
                x_px - radius, y_px - radius,
                x_px + radius, y_px + radius,
                outline="yellow", width=3, fill=""
            )
            self.overlay_canvas.tag_raise(self.state.highlight_id)

            # Update status
            peak_id = peak.get("peak_id", "?")
            self.status_label.config(
                text=f"✓ Peak #{peak_id} highlighted @ (x={peak.get('drift_ms', 0):.2f}ms, "
                     f"y={peak.get('retention_s', 0):.2f}s)"
            )

    def on_heatmap_click(self, event):
        """Click heatmap to open zoom viewer."""
        if not self.state.heatmap_img_path:
            return
        ImageViewerDialog(self.root, self.state.heatmap_img_path, title="Heatmap Viewer")

    def on_overlay_click_open_zoom(self, event):
        """Single-click overlay to open zoom viewer for peak selection."""
        if not self.state.overlay_img_path:
            return
        ImageViewerDialog(self.root, self.state.overlay_img_path, title="Overlay Viewer - Peak Selection")

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

    def on_export_heatmap(self):
        """Export heatmap PNG."""
        if not self.state.heatmap_path:
            messagebox.showwarning("Nothing to export", "No heatmap loaded.")
            return
        base = Path(self.state.selected_mea_file).stem
        save_path = filedialog.asksaveasfilename(
            title="Save Heatmap As",
            defaultextension=".png",
            initialfile=f"{base}_heatmap.png",
            initialdir=os.path.dirname(self.state.heatmap_path),
            filetypes=[("PNG images", "*.png"), ("All files", "*.*")],
        )
        if not save_path:
            return
        try:
            shutil.copy(self.state.heatmap_path, save_path)
            self.status_label.config(text=f"✓ Saved: {Path(save_path).name}")
        except Exception as e:
            messagebox.showerror("Save failed", f"Could not save file:\n{e}")

    def on_export_overlay(self):
        """Export overlay PNG."""
        if not self.state.overlay_path:
            messagebox.showwarning("Nothing to export", "No overlay loaded.")
            return
        base = Path(self.state.selected_mea_file).stem
        save_path = filedialog.asksaveasfilename(
            title="Save Overlay As",
            defaultextension=".png",
            initialfile=f"{base}_overlay.png",
            initialdir=os.path.dirname(self.state.overlay_path),
            filetypes=[("PNG images", "*.png"), ("All files", "*.*")],
        )
        if not save_path:
            return
        try:
            shutil.copy(self.state.overlay_path, save_path)
            self.status_label.config(text=f"✓ Saved: {Path(save_path).name}")
        except Exception as e:
            messagebox.showerror("Save failed", f"Could not save file:\n{e}")

    def on_export_peaks(self):
        """Export peaks CSV."""
        if not self.state.peaks_csv_path:
            messagebox.showwarning("Nothing to export", "No peaks detected.")
            return
        base = Path(self.state.selected_mea_file).stem
        save_path = filedialog.asksaveasfilename(
            title="Save Peaks CSV As",
            defaultextension=".csv",
            initialfile=f"{base}_peaks.csv",
            initialdir=os.path.dirname(self.state.peaks_csv_path),
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not save_path:
            return
        try:
            shutil.copy(self.state.peaks_csv_path, save_path)
            self.status_label.config(text=f"✓ Saved: {Path(save_path).name}")
        except Exception as e:
            messagebox.showerror("Save failed", f"Could not save file:\n{e}")

    def on_exit(self):
        """Exit application."""
        self.root.quit()

    # --- State Management --- #

    def update_button_state(self):
        """Enable/disable buttons based on current state."""
        button_states = {
            UIState.START: {
                "browse_btn": "normal",
                "read_btn": "disabled",
                "detect_btn": "disabled",
                "export_heatmap_btn": "disabled",
                "export_overlay_btn": "disabled",
                "export_csv_btn": "disabled",
            },
            UIState.FOLDER_SELECTED: {
                "browse_btn": "normal",
                "read_btn": "disabled",
                "detect_btn": "disabled",
                "export_heatmap_btn": "disabled",
                "export_overlay_btn": "disabled",
                "export_csv_btn": "disabled",
            },
            UIState.FILE_SELECTED: {
                "browse_btn": "normal",
                "read_btn": "normal",
                "detect_btn": "disabled",
                "export_heatmap_btn": "disabled",
                "export_overlay_btn": "disabled",
                "export_csv_btn": "disabled",
            },
            UIState.READING: {
                "browse_btn": "disabled",
                "read_btn": "disabled",
                "detect_btn": "disabled",
                "export_heatmap_btn": "disabled",
                "export_overlay_btn": "disabled",
                "export_csv_btn": "disabled",
            },
            UIState.READ_DONE: {
                "browse_btn": "normal",
                "read_btn": "normal",
                "detect_btn": "normal",
                "export_heatmap_btn": "normal",
                "export_overlay_btn": "disabled",
                "export_csv_btn": "disabled",
            },
            UIState.DETECTING: {
                "browse_btn": "disabled",
                "read_btn": "disabled",
                "detect_btn": "disabled",
                "export_heatmap_btn": "disabled",
                "export_overlay_btn": "disabled",
                "export_csv_btn": "disabled",
            },
            UIState.PEAKS_DETECTED: {
                "browse_btn": "normal",
                "read_btn": "normal",
                "detect_btn": "normal",
                "export_heatmap_btn": "normal",
                "export_overlay_btn": "normal",
                "export_csv_btn": "normal",
            },
            UIState.ERROR: {
                "browse_btn": "normal",
                "read_btn": "disabled" if not self.state.selected_mea_file else "normal",
                "detect_btn": "disabled",
                "export_heatmap_btn": "disabled",
                "export_overlay_btn": "disabled",
                "export_csv_btn": "disabled",
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







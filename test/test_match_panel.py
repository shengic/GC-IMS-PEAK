"""
test_match_panel.py — Render tests for the Stage-10 compound-match panel.

These build the actual Toplevel (`GCIMSApp._render_match_panel`) with synthetic
match_all output and assert the tree is populated correctly. They need a Tk
display, so each test skips cleanly on a headless box (e.g. Linux CI without X).
The matcher itself is covered by test_match.py; this covers the panel wiring.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from main import GCIMSApp


def _tk_root_or_skip():
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no Tk display available")
    root.withdraw()
    return tk, root


def _panel_shim(root):
    class _Shim:
        pass
    shim = _Shim()
    shim.root = root
    shim._match_windows = {}
    shim._render_match_panel = GCIMSApp._render_match_panel.__get__(shim, _Shim)
    return shim


def _tree_in(widget):
    from tkinter import ttk
    for child in widget.winfo_children():
        if isinstance(child, ttk.Treeview):
            return child
        found = _tree_in(child)
        if found:
            return found
    return None


PEAK = {"peak_id": 1, "ri": 726.3, "retention_s": 358.7, "drift_relative": 1.1,
        "ri_assumed_unverified": True}


def test_panel_defaults_to_combined_and_gc_toggle_reveals_gc_only():
    tk, root = _tk_root_or_skip()
    try:
        result = {
            "gc_dimension": "ri", "ims_matches": [], "combined_matches": [],
            "gc_matches": [
                {"Name": "Ethanol", "CAS": "C64175", "Formula": "C2H6O",
                 "RI": 720.0, "delta_ri": 6.3, "match_dimensions": ["ri"],
                 "source_file": "a.iml"},
                {"NAME": "Acetone", "CAS": "C67641", "RI": 722.0, "delta_ri": 4.3,
                 "match_dimensions": ["ri"], "source_file": "b.ril"},
            ],
        }
        shim = _panel_shim(root)
        shim._render_match_panel(PEAK, result, {"ril_strategy": "column_name"})
        win = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
        tree = win._tree
        # Default: GC+IMS only → GC-only hits hidden → empty tree
        assert len(tree.get_children()) == 0
        # Toggle "GC only" on → the two GC candidates appear
        win._filter_vars["gc"].set(True)
        win._refresh_candidates()
        rows = [tree.item(r, "values") for r in tree.get_children()]
        assert len(rows) == 2
        assert {r[1] for r in rows} == {"Ethanol", "Acetone"}
        assert all(r[0] == "GC" for r in rows)
    finally:
        root.destroy()


def test_panel_combined_dedups_and_labels():
    tk, root = _tk_root_or_skip()
    try:
        # same CAS in gc + ims → one combined row, not three
        cand = {"Name": "X", "CAS": "C1", "Formula": "CH4", "RI": 726,
                "delta_ri": 0.3, "delta_k0": 0.01,
                "match_dimensions": ["ri", "k0"], "source_file_gc": "a.iml"}
        result = {"gc_dimension": "ri", "gc_matches": [cand],
                  "ims_matches": [cand], "combined_matches": [cand]}
        shim = _panel_shim(root)
        shim._render_match_panel(PEAK, result, {"ril_strategy": "x"})
        tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        tree = _tree_in(tops[-1])
        rows = tree.get_children()
        assert len(rows) == 1
        assert tree.item(rows[0], "values")[0] == "GC+IMS"
    finally:
        root.destroy()


def test_panel_opens_with_no_candidates():
    tk, root = _tk_root_or_skip()
    try:
        result = {"gc_dimension": None, "gc_matches": [], "ims_matches": [],
                  "combined_matches": []}
        shim = _panel_shim(root)
        shim._render_match_panel({"peak_id": 9}, result, {})   # must not crash
        tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        assert len(tops) == 1
        assert len(_tree_in(tops[0]).get_children()) == 0
    finally:
        root.destroy()


def test_autofill_populates_gc_column_without_trigger():
    """The GC (RI) column fills on its own (no ▶ click): _autofill_gc_matches
    matches the peaks against the loaded libraries and refreshes the table."""
    import time
    tk, root = _tk_root_or_skip()
    try:
        from tkinter.ttk import Treeview
        from main import AppState, PEAK_TABLE_COLUMNS

        class _Shim:
            pass
        app = _Shim()
        app.root = root
        app.state = AppState()
        # tiny synthetic library: Ethanol RI 726 is within ±10 of the peak's 726.3
        app.state.library_rows = (
            [{"NAME": "Ethanol", "CAS": "A", "RI": 726.0},
             {"NAME": "Nonanal", "CAS": "B", "RI": 1100.0}], [], {"ril_strategy": "t"})
        app.peak_tree = Treeview(root, columns=PEAK_TABLE_COLUMNS, show="headings")

        class _L:
            def config(self, **k):
                pass
        app.status_label = _L()
        for name in ("_apply_cached_matches", "_autofill_gc_matches", "_match_and_cache",
                     "_poll_match_and_cache", "_ensure_libraries_then", "_poll_libraries",
                     "_refresh_match_columns", "_cell_value", "_peak_by_id", "_refresh_row"):
            setattr(app, name, getattr(GCIMSApp, name).__get__(app, _Shim))
        app._row_tags = GCIMSApp._row_tags   # staticmethod

        peak = {"peak_id": 1, "ri": 726.3, "rt_index": 100, "dt_index": 50,
                "retention_s": 300.0, "drift_relative": 1.1,
                "rule_active": True, "user_active": None}
        app.state.peaks = [peak]
        app.peak_tree.insert("", "end",
                             values=tuple(app._cell_value(c, peak) for c in PEAK_TABLE_COLUMNS))
        # Before autofill the GC cell is a dash (no matches yet)
        assert app._cell_value("gc", peak) == "—"

        app._autofill_gc_matches()               # no ▶ click
        for _ in range(100):                      # pump the loop for the bg match
            root.update()
            if peak.get("matches"):
                break
            time.sleep(0.02)

        assert peak.get("matches") is not None
        assert (100, 50) in app.state.match_cache            # cached by coordinate
        gc = app._cell_value("gc", peak)
        assert gc.startswith("726")                          # matched RI value shown
        assert "Ethanol" not in gc                           # value, not name
    finally:
        root.destroy()


def test_selection_highlight_survives_redraw():
    """The yellow selection ring is re-applied on every canvas redraw (resize/pan/
    zoom), so it stays in sync with the highlighted table row instead of vanishing."""
    tk, root = _tk_root_or_skip()
    try:
        from tkinter import Canvas
        from main import AppState

        class _Shim:
            pass
        app = _Shim()
        app.main_canvas = Canvas(root, width=400, height=400)
        app.state = AppState()
        app.state.canvas_geometry = {"png_size": [400, 400],
                                     "axes_bbox": [0.1, 0.1, 0.8, 0.8],
                                     "xlim": [0, 4], "ylim": [500, 1100], "y_axis": "ri"}
        app.state.peaks = [{"peak_id": 1, "drift_relative": 2.0, "ri": 800,
                            "rule_active": True, "user_active": None}]
        app.state.highlighted_peak_id = 1
        app.main_canvas_kind = "bg"
        app.main_canvas_zoom = 1.0
        app.main_canvas_pan_x = 0
        app.main_canvas_pan_y = 0
        for name in ("_draw_peak_circles", "_reapply_highlight", "_draw_highlight",
                     "_peak_to_image_xy", "_peak_by_id"):
            setattr(app, name, getattr(GCIMSApp, name).__get__(app, _Shim))

        app._draw_peak_circles()                       # initial draw
        assert app.state.highlight_id is not None
        assert app.main_canvas.find_withtag("highlight")   # ring present

        app.main_canvas.delete("all")                  # what _render_main_canvas does
        app._draw_peak_circles()                       # redraw (as after pan/zoom)
        assert app.state.highlight_id is not None       # ring restored
        assert app.main_canvas.find_withtag("highlight")

        # with nothing selected, no ring is drawn
        app.state.highlighted_peak_id = None
        app.main_canvas.delete("all")
        app._draw_peak_circles()
        assert not app.main_canvas.find_withtag("highlight")
    finally:
        root.destroy()


def test_one_match_window_per_peak():
    """Re-triggering the same peak raises its existing panel instead of stacking a
    duplicate; closing the panel de-registers it."""
    tk, root = _tk_root_or_skip()
    try:
        from main import AppState

        class _Shim:
            pass
        app = _Shim()
        app.root = root
        app.state = AppState()
        app._match_windows = {}
        app._render_match_panel = GCIMSApp._render_match_panel.__get__(app, _Shim)
        app._open_compound_match_panel = GCIMSApp._open_compound_match_panel.__get__(app, _Shim)

        result = {"gc_dimension": "ri", "gc_matches": [], "ims_matches": [],
                  "combined_matches": []}
        peak = {"peak_id": 1}
        app._render_match_panel(peak, result, {})      # opens + registers window 1
        assert 1 in app._match_windows
        n_before = sum(isinstance(w, tk.Toplevel) for w in root.winfo_children())

        # Re-trigger peak 1: dedup path raises the existing window, no new Toplevel
        app._open_compound_match_panel(peak)
        n_after = sum(isinstance(w, tk.Toplevel) for w in root.winfo_children())
        assert n_after == n_before

        # Closing the window removes it from the registry
        app._match_windows[1].destroy()
        root.update()
        assert 1 not in app._match_windows
    finally:
        root.destroy()


if __name__ == "__main__":
    test_panel_defaults_to_combined_and_gc_toggle_reveals_gc_only()
    test_panel_combined_dedups_and_labels()
    test_panel_opens_with_no_candidates()
    test_autofill_populates_gc_column_without_trigger()
    test_selection_highlight_survives_redraw()
    test_one_match_window_per_peak()
    print("✓ match-panel render + autofill + highlight + dedup checks passed")

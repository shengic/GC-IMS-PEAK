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


def test_panel_lists_gc_candidates():
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
        tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        assert len(tops) == 1 and "Peak #1" in tops[0].title()
        tree = _tree_in(tops[0])
        rows = [tree.item(r, "values") for r in tree.get_children()]
        assert len(rows) == 2
        assert {r[1] for r in rows} == {"Ethanol", "Acetone"}   # names (col 2)
        assert all(r[0] == "GC" for r in rows)                  # match label
        assert rows[0][2] in {"C64175", "C67641"}               # CAS shown
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


if __name__ == "__main__":
    test_panel_lists_gc_candidates()
    test_panel_combined_dedups_and_labels()
    test_panel_opens_with_no_candidates()
    print("✓ match-panel render checks passed")

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


def _destroy(root):
    """拆掉 Tk root，且不把 unraisable 例外洩漏給後面的測試。

    `tkinter.Variable.__del__` 會回呼直譯器。`_render_match_panel` 建的那幾個
    BooleanVar 掛在 `win._filter_vars` 上，如果 root 先被 destroy、它們才被 GC，
    `__del__` 就會對著已死的直譯器丟
    `RuntimeError: main thread is not in main loop`。Python 把它歸類為
    **unraisable** 例外，pytest 則算到「當下正在跑的那個測試」頭上——所以本檔多加
    一個 Tk 測試之後，失敗的是毫不相干的 test_subprocess。

    做法：先清掉 widget 的 Python 端參照（`__dict__`，含 `_filter_vars`），在 root
    還活著時 gc.collect() 讓那些 `__del__` 跑完，最後才 destroy root。
    """
    import gc
    for w in list(root.winfo_children()):
        # 只丟掉持有 BooleanVar 的那個屬性——清整個 __dict__ 會連 widget 自己的
        # _w / tk / master 一起清掉，widget 就壞了（試過，4 個測試當場失敗）。
        for attr in ("_filter_vars", "_refresh_candidates", "_tree"):
            w.__dict__.pop(attr, None)
        try:
            w.destroy()
        except Exception:
            pass
    gc.collect()          # Variable.__del__ 此時直譯器仍活著
    root.destroy()
    gc.collect()


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
        _destroy(root)


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
        _destroy(root)


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
        _destroy(root)


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
        _destroy(root)


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
        _destroy(root)


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
        app._raise_window = GCIMSApp._raise_window          # staticmethod

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
        _destroy(root)


def test_raise_window_does_not_leave_the_panel_pinned_on_top():
    """re-click ▶ 必須把既有面板抬到前景，但**不能**讓它永久置頂。

    Windows 上 lift()+focus_set() 抬不動被其他 Toplevel 蓋住的視窗（前景鎖定），
    所以改用 -topmost 開→關 的手法。這裡鎖住的是「關」那一半：忘了關掉的話，
    比對面板會永遠浮在主視窗上面，使用者無法把它推到背景——那是比原本的 bug
    更難忍受的行為，而且很容易在重構時被順手刪掉。
    """
    tk, root = _tk_root_or_skip()
    try:
        win = tk.Toplevel(root)
        root.update()
        GCIMSApp._raise_window(win)
        assert bool(win.attributes("-topmost")) is True, "抬升當下應暫時置頂"
        root.update()                      # 讓 after_idle 執行
        assert bool(win.attributes("-topmost")) is False, "抬升後必須取消置頂"
        assert win.winfo_exists()
        # 視窗已銷毀時不得拋例外（winfo_exists 與呼叫之間可能被關掉）
        win.destroy()
        GCIMSApp._raise_window(win)
    finally:
        _destroy(root)


if __name__ == "__main__":
    test_panel_defaults_to_combined_and_gc_toggle_reveals_gc_only()
    test_panel_combined_dedups_and_labels()
    test_panel_opens_with_no_candidates()
    test_autofill_populates_gc_column_without_trigger()
    test_selection_highlight_survives_redraw()
    test_one_match_window_per_peak()
    print("✓ match-panel render + autofill + highlight + dedup checks passed")


def test_peak_view_is_the_default_when_results_exist():
    """選 .mea 後主畫面應直接是帶圈的峰圖，不是原始熱圖。

    使用者明確要求的行為決策，很容易在重構載入流程時退化回「停在原始熱圖、
    等使用者再按一次按鈕」。這裡只驗分支：有現成結果 → 切峰畫布；沒有 → 不切、
    且狀態列要說清楚該按哪個按鈕（**不自動觸發偵測**，那是 workflow §第八階段
    的既有決策，約 90 秒的靜默等待會讓人以為程式當掉）。
    """
    class _Shim:
        pass

    for has_results, expect_switch in ((True, True), (False, False)):
        app = _Shim()
        app._loaded = []
        app._shown = []
        app._load_existing_peaks = lambda b: (app._loaded.append(b), has_results)[1]
        app._show_peak_canvas = lambda: app._shown.append(True)

        class _L:
            def __init__(self): self.texts = []
            def config(self, **k): self.texts.append(k.get("text", ""))
        app.status_label = _L()
        app._show_peaks_if_available = GCIMSApp._show_peaks_if_available.__get__(app, _Shim)

        got = app._show_peaks_if_available("sample_1")
        assert got is expect_switch
        assert app._loaded == ["sample_1"]
        assert bool(app._shown) is expect_switch
        if not has_results:
            assert "Show Detected Peak Heatmap" in app.status_label.texts[-1], \
                "沒有現成結果時，必須告訴使用者要按哪個按鈕"

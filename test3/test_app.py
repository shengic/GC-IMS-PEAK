"""test_app.py — 第三支應用**介面**（`compound_consensus/app.py`）的測試。

本檔幾乎每一項都是**實際踩過的錯**的回歸測試，不是為了覆蓋率湊出來的：

- 熱圖上一個圈都畫不出來，而且沒有任何錯誤訊息（RI 沒掛上去）
- `_build()` 被切成兩半，三個面板變成無法到達的程式碼（視窗只剩工具列）
- 「加入這組」用雙擊，於是每次都順帶白跑一次載入熱圖
- 背景執行緒無聲死掉，UI 永遠等下去

需要 Tk display；無視窗環境會整支 skip（同 `test2/test_main2.py` 的作法）。
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import areas2  # noqa: E402
from compound_consensus import logic as L  # noqa: E402

GAS = os.path.join(PROJECT_ROOT, "GAS")


def _tk_or_skip():
    """建 Tk root，失敗就 skip **並帶上真正的錯誤**。

    只寫「no display」會把偶發的建立失敗與真正無視窗環境混為一談——第一支應用
    先前就因此掩蓋過一次真實問題。重試一次吸收暫時性失敗。
    """
    import tkinter as tk
    last = None
    for _ in range(2):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            last = exc
            continue
        root.withdraw()
        return tk, root
    pytest.skip("cannot create a Tk root: %s" % last)


def _app(monkeypatch):
    """建一個 ConsensusApp，並把 modal 對話框換掉（測試不能被卡住）。"""
    tk, root = _tk_or_skip()
    from tkinter import messagebox
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **k: None)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)
    from compound_consensus import app as appmod
    return tk, root, appmod, appmod.ConsensusApp(root)


def _destroy(root):
    import gc
    try:
        root.destroy()
    except Exception:
        pass
    gc.collect()


def _fake_peaks(n=3, with_ri=True):
    out = []
    for i in range(n):
        out.append({"peak_id": i + 1, "rt_index": 1000 + i * 100, "dt_index": 700 + i,
                    "intensity": 500 + i, "prominence": 100 + i,
                    "retention_s": 400.0 + i * 100, "drift_ms": 4.7,
                    "drift_relative": 1.10 + i * 0.05,
                    "ri": (900.0 + i * 50) if with_ri else None,
                    "rule_active": True, "active": True})
    return out


RI_GEOM = {"png_size": [1200, 1350], "axes_bbox": [0.085, 0.052, 0.910, 0.919],
           "xlim": [0.0, 3.75], "ylim": [820.0, 1570.0], "y_axis": "ri"}
RT_GEOM = dict(RI_GEOM, ylim=[0.0, 2700.0], y_axis="retention_s")


# --------------------------------------------------------------------------- #
# 圈畫不出來 —— 本輪最嚴重的一個，而且完全沒有錯誤訊息
# --------------------------------------------------------------------------- #
def test_ri_axis_background_needs_peaks_that_carry_ri(monkeypatch):
    """背景圖是 RI 座標時，沒有 `ri` 的峰**一個圈都畫不出來**。

    實測：`areas2.detect_one()` 只找峰、不掛 RI，於是 28 顆峰全部 `ri=None`，
    畫面上零個圈，而且不會有任何例外或訊息。這一條把它釘死。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        app.geom = RI_GEOM
        app.peaks = _fake_peaks(3, with_ri=False)
        assert all(app._xy(p) is None for p in app.peaks), "沒有 RI 就算不出座標"
        app._draw_circles()
        assert len(app.circles) == 0

        app.peaks = _fake_peaks(3, with_ri=True)
        app._draw_circles()
        assert len(app.circles) == 3, "掛上 RI 之後每顆峰都要有圈"
    finally:
        _destroy(root)


def test_rt_axis_background_uses_retention_time(monkeypatch):
    """背景圖是保留時間座標時，沒有 RI 也照樣畫得出圈——不可以一律要求 RI。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        app.geom = RT_GEOM
        app.peaks = _fake_peaks(3, with_ri=False)
        app._draw_circles()
        assert len(app.circles) == 3
    finally:
        _destroy(root)


def test_circle_position_respects_the_plot_margins(monkeypatch):
    """圈的座標要用 `axes_bbox`，不能假設資料區佔滿整張 PNG。

    第一支應用踩過：`highlight_peak_on_overlay()` 假設滿版，matplotlib 光左邊就
    留了 8.5% 邊界，於是每個圈都偏掉。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        app.geom = RI_GEOM
        app.scale = 1.0
        x0, y0, bw, bh = RI_GEOM["axes_bbox"]
        pw, ph = RI_GEOM["png_size"]
        # 座標軸最小值的那個角落
        p = {"drift_relative": RI_GEOM["xlim"][0], "ri": RI_GEOM["ylim"][0]}
        x, y = app._xy(p)
        assert x == pytest.approx(x0 * pw, abs=1.0)
        assert y == pytest.approx((1.0 - y0) * ph, abs=1.0)
        assert x > 0, "滿版假設會讓這裡等於 0"
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 版面 —— `_build()` 曾被切成兩半
# --------------------------------------------------------------------------- #
def test_build_creates_all_three_panels(monkeypatch):
    """三個面板都必須真的被建出來。

    回歸測試：`quit_app` 曾被插進 `_build()` 中間，`self.root.destroy()` 之後的
    三行建面板變成無法到達的程式碼——檔案照樣 import 成功、語法照樣正確，
    但視窗打開只剩一條工具列。只有實際建構才抓得到。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        for name in ("tree_files", "canvas", "tree_peaks", "tree_group",
                     "tree_cmpd", "status", "hint"):
            assert hasattr(app, name), "缺少 %s" % name
        assert app.tree_files.winfo_exists()
        assert app.canvas.winfo_exists()
    finally:
        _destroy(root)


def test_three_modes_switch_the_right_panel(monkeypatch):
    """模式切換要換掉右側面板，而且不能有例外。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        for mode, pane in ((appmod.MODE_HEATMAP, "pane_peaks"),
                           (appmod.MODE_GROUP, "pane_group"),
                           (appmod.MODE_COMPOUND, "pane_cmpd")):
            app.mode.set(mode)
            app.on_mode_change()
            shown = [p for p in (app.pane_peaks, app.pane_group, app.pane_cmpd)
                     if p.winfo_manager()]
            assert shown == [getattr(app, pane)], "模式 %s 應只顯示 %s" % (mode, pane)
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 點檔案的意思由模式決定，不是靠單擊 vs 雙擊
# --------------------------------------------------------------------------- #
def test_group_mode_click_does_not_load_the_heatmap(monkeypatch):
    """模式 2 點檔案只該改變這一組，**不可以**順帶載入熱圖。

    這正是不用「雙擊＝加入這組」的理由：Tk 一定先送單擊，於是每次加入都白跑一次
    偵測與繪圖，看得到卡頓。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        loaded = []
        monkeypatch.setattr(app, "_load_file", lambda p: loaded.append(p))
        app.files = ["a.mea", "b.mea"]
        app.mode.set(appmod.MODE_GROUP)
        app.on_mode_change()
        app._group_click("a.mea")
        assert loaded == [], "模式 2 不該載入熱圖"
        assert "a.mea" in app.group
        app._group_click("b.mea")
        assert app.group == {"a.mea", "b.mea"}
        app._group_click("b.mea")            # 再點一次＝移出
        assert app.group == {"a.mea"}
    finally:
        _destroy(root)


def test_group_mode_without_similarity_only_takes_the_clicked_file(monkeypatch):
    """還沒算相似度時，不可以憑空猜一組——只帶入被點的那一個。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.files = ["a.mea", "b.mea", "c.mea"]
        app.corr, app.corr_files = None, []
        assert app._suggest_group("a.mea") == ["a.mea"]
    finally:
        _destroy(root)


def test_suggested_group_follows_similarity(monkeypatch):
    """相似度高的才進建議名單，低的不進。"""
    import numpy as np
    tk, root, appmod, app = _app(monkeypatch)
    try:
        files = ["a.mea", "b.mea", "c.mea"]
        app.files = list(files)
        app.corr_files = list(files)
        app.corr = np.array([[1.00, 0.95, 0.10],
                             [0.95, 1.00, 0.12],
                             [0.10, 0.12, 1.00]])
        assert app._suggest_group("a.mea") == ["a.mea", "b.mea"]
        assert app._suggest_group("c.mea") == ["c.mea"]
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 背景執行緒與佇列 —— 不可以無聲死掉
# --------------------------------------------------------------------------- #
def test_worker_error_reaches_the_status_bar(monkeypatch):
    """背景出錯要送進佇列並顯示，不能靜靜結束讓 UI 永遠等。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        app.q.put(("error", "RuntimeError: boom"))
        app._drain()
        assert "boom" in app.status.cget("text")
    finally:
        _destroy(root)


def test_prepared_for_another_file_does_not_hijack_the_view(monkeypatch):
    """等待期間切到別的檔時，先前那個做完**不可以**把畫面搶回去。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        shown = []
        monkeypatch.setattr(app, "_show_loaded", lambda p: shown.append(p))
        app.current = "b.mea"
        app.q.put(("prepared", "a.mea"))
        app._drain()
        assert shown == [], "使用者已經在看 b.mea 了"
        app.q.put(("prepared", "b.mea"))
        app._drain()
        assert shown == ["b.mea"]
    finally:
        _destroy(root)


def test_busy_blocks_a_second_concurrent_preparation(monkeypatch, tmp_path):
    """已經在準備一個檔時，再點別的檔不可以又開一條執行緒。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        started = []
        monkeypatch.setattr(app, "_prepare_worker", lambda p: started.append(p))
        monkeypatch.setattr(L, "peaks_are_current", lambda *a, **k: False)
        monkeypatch.setattr(os.path, "exists", lambda p: False)
        app.busy = "掃描（找峰）"
        app._load_file(str(tmp_path / "x.mea"))
        assert started == []
        assert "請等它跑完" in app.status.cget("text")
    finally:
        _destroy(root)


def test_calibration_message_records_the_mode(monkeypatch):
    """RI 校正解完要把 `ri_mode` 講出來——provenance 一路要看得見。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        app.q.put(("calibration", ({"n_anchors": 6}, "batch_own_std")))
        app._drain()
        assert "batch_own_std" in app.status.cget("text")
        assert app.ri_cal == {"n_anchors": 6}
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 共識化合物面板
# --------------------------------------------------------------------------- #
def _fake_consolidated():
    return [
        {"votes": 3, "votes_total": 3, "vote_tier": 4, "below": False,
         "rt": 321.3, "dr": 1.101, "ri": 841.9, "ri_spread": 0.89,
         "n_files_with_peak": 3, "files_without_peak": [],
         "match_dimension": "combined",
         "candidates": [{"cas": "78-93-3", "name": "2-butanone", "library_ri": 908.0,
                         "n_support": 3, "n_files_with_peak": 3, "support": 1.0,
                         "mean_abs_delta_ri": 0.31, "files": []}]},
        {"votes": 2, "votes_total": 3, "vote_tier": 2, "below": True,
         "rt": 1963.8, "dr": 1.315, "ri": 1471.8, "ri_spread": None,
         "n_files_with_peak": 2, "files_without_peak": ["c.mea"],
         "match_dimension": "gc_only", "candidates": []},
    ]


def test_compound_panel_shows_votes_and_match_dimension(monkeypatch):
    """票數要顯示成 n/N，只有 RI 對上的要標出來——證據強度差一個數量級。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.consolidated = _fake_consolidated()
        app.mode.set(appmod.MODE_COMPOUND)
        app.on_mode_change()
        rows = app.tree_cmpd.get_children()
        assert len(rows) == 2
        v0 = app.tree_cmpd.item(rows[0], "values")
        assert v0[0] == "3/3"
        assert v0[1] == "2D"
        assert app.tree_cmpd.item(rows[1], "values")[1] == "RI"
        assert app.tree_cmpd.item(rows[0], "text") == "2-butanone"
    finally:
        _destroy(root)


def test_below_threshold_rows_are_kept_not_deleted(monkeypatch):
    """未達門檻的區域**保留顯示**（灰），不刪除。

    少勾一個檔就可能讓真實化合物掉到門檻以下；靜靜消失會讓人以為那裡本來就沒東西。
    同專案 `n_det=None` vs `0`、空白格 vs 0 的原則。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.consolidated = _fake_consolidated()
        app.mode.set(appmod.MODE_COMPOUND)
        app.on_mode_change()
        assert len(app.tree_cmpd.get_children()) == 2, "未達門檻的那一列也要在"
        tags = app.tree_cmpd.item(app.tree_cmpd.get_children()[1], "tags")
        assert "t2" in tags, "要帶著票數分級的底色標籤"
    finally:
        _destroy(root)


def test_compound_panel_says_so_when_there_is_nothing_yet(monkeypatch):
    """還沒彙整就要講清楚下一步，不是給一張空表。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.consolidated = []
        app.mode.set(appmod.MODE_COMPOUND)
        app.on_mode_change()
        assert "Consolidate" in app.right_note.cget("text")
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 隔離規則
# --------------------------------------------------------------------------- #
def test_app_never_writes_the_first_apps_selection_file(monkeypatch, tmp_path):
    """峰的選取只能寫 `_peaks_state3.json`，不可以覆蓋第一支應用的 `_peaks_state.json`。"""
    from compound_consensus import state as state_mod
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(tmp_path))
    mea = str(tmp_path / "x.mea")
    peaks = _fake_peaks(2)
    state_mod.save(mea, peaks)
    written = sorted(p.name for p in tmp_path.iterdir())
    assert written == ["x_peaks_state3.json"]
    assert "x_peaks_state.json" not in written


# --------------------------------------------------------------------------- #
# 縮放、平移與選取回饋 —— 以下每一條都是使用者實際回報的
# --------------------------------------------------------------------------- #
class _Ev:
    """假的滑鼠事件。"""
    def __init__(self, x, y, delta=0):
        self.x, self.y, self.delta = x, y, delta


def _with_image(app, w=1200, h=1350):
    from PIL import Image
    app.img_orig = Image.new("RGB", (w, h), "white")
    app.geom = RI_GEOM
    app.peaks = _fake_peaks(3, with_ri=True)
    app.zoom, app.pan_x, app.pan_y = 1.0, 0.0, 0.0
    app._fit()
    app._render_canvas()
    return app


def test_selection_ring_is_yellow_not_a_thicker_red_circle(monkeypatch):
    """選取回饋要用**黃色環**，與第一支應用一致。

    使用者回報：把紅圈加粗在一堆紅圈裡幾乎看不出來；而且被選到的峰若是灰的
    （未勾選），加粗紅圈完全沒有回饋。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app._highlight(1)
        assert app.highlight_id is not None
        assert app.canvas.itemcget(app.highlight_id, "outline") == "yellow"
        assert float(app.canvas.itemcget(app.highlight_id, "width")) >= 3
    finally:
        _destroy(root)


def test_highlight_ring_follows_the_peak_through_zoom(monkeypatch):
    """縮放之後黃環必須還黏在同一顆峰上，不能留在原地。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app._highlight(1)
        before = app.canvas.coords(app.highlight_id)
        app.on_wheel(_Ev(400, 400, delta=240))
        after = app.canvas.coords(app.highlight_id)
        assert before != after, "縮放後環要跟著動"
        cx = (after[0] + after[2]) / 2.0
        cy = (after[1] + after[3]) / 2.0
        px, py = app._canvas_xy(app.peaks[1])
        assert cx == pytest.approx(px, abs=1.0)
        assert cy == pytest.approx(py, abs=1.0)
    finally:
        _destroy(root)


def test_wheel_zooms_and_keeps_the_point_under_the_cursor_fixed(monkeypatch):
    """滾輪要能縮放，而且游標下的那一點不動。

    使用者回報：滾輪完全沒有反應，所以看不出圈有沒有對準峰。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        z0 = app.zoom
        target = app._canvas_xy(app.peaks[0])
        app.on_wheel(_Ev(target[0], target[1], delta=240))
        assert app.zoom > z0, "向前滾要放大"
        moved = app._canvas_xy(app.peaks[0])
        assert moved[0] == pytest.approx(target[0], abs=1.0)
        assert moved[1] == pytest.approx(target[1], abs=1.0)
        app.on_wheel(_Ev(target[0], target[1], delta=-240))
        assert app.zoom == pytest.approx(z0, abs=1e-9), "往回滾要縮小"
    finally:
        _destroy(root)


def test_circles_move_with_the_image_when_zooming(monkeypatch):
    """圈是畫在畫布上的獨立物件，縮放時必須跟著圖走，否則會與峰脫節。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        before = app.canvas.coords(app.circles[0][0])
        app.on_wheel(_Ev(300, 300, delta=240))
        after = app.canvas.coords(app.circles[0][0])
        assert before != after
        assert len(app.circles) == len(app.peaks), "縮放後每顆峰都還要有圈"
    finally:
        _destroy(root)


def test_dragging_does_not_toggle_a_peak(monkeypatch):
    """拖曳平移放開時**不可以**誤觸底下的峰。

    不分辨「點」與「拖」的話，每次平移結束都會把經過的峰切掉，而使用者根本沒有
    要點它。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.current = "x.mea"
        monkeypatch.setattr("compound_consensus.state.save", lambda *a, **k: None)
        x, y = app._canvas_xy(app.peaks[0])
        was = app.peaks[0]["active"]
        app.on_press(_Ev(x, y))
        app.on_drag(_Ev(x + 40, y + 30))
        app.on_release(_Ev(x + 40, y + 30))
        assert app.peaks[0]["active"] == was, "這是拖曳，不是點選"
        # 真的點一下才切換
        x2, y2 = app._canvas_xy(app.peaks[0])
        app.on_press(_Ev(x2, y2))
        app.on_release(_Ev(x2, y2))
        assert app.peaks[0]["active"] != was
    finally:
        _destroy(root)


def test_image_is_centred_so_the_gap_is_not_all_at_the_bottom(monkeypatch):
    """保持長寬比多出來的空白要**平均分在兩側**，不是全部堆在下方。

    使用者回報：熱圖下方有一大塊黑色區域。原因是圖靠上對齊、畫布底色又是深色。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        root.update_idletasks()
        _with_image(app, w=1200, h=1350)
        app._fit()                      # 用「現在」的畫布尺寸重算，避免尺寸在中途變動
        # 用與 `_fit()` 相同的下限取尺寸：root 被 withdraw 時 winfo_width() 會回 1，
        # 兩邊量到不同的畫布就比不出東西來。這裡要驗的是「置中」這條算式本身。
        cw = max(app.canvas.winfo_width(), 50)
        ch = max(app.canvas.winfo_height(), 50)
        eff = app.fit_scale * app.zoom
        # 真正要成立的性質：圖的中心與畫布的中心重合（空白平均分在兩側）
        assert app.pan_x + 1200 * eff / 2.0 == pytest.approx(cw / 2.0, abs=1.0)
        assert app.pan_y + 1350 * eff / 2.0 == pytest.approx(ch / 2.0, abs=1.0)
        gap_top = app.pan_y
        gap_bottom = ch - (app.pan_y + 1350 * eff)
        assert gap_top == pytest.approx(gap_bottom, abs=1.0),             "空白全部堆在下方就會出現那條黑帶"
        assert app.canvas.cget("bg") == "white", "底色要與圖的白邊一致，深色會變黑帶"
    finally:
        _destroy(root)


def test_double_click_on_empty_space_returns_to_fit(monkeypatch):
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.on_wheel(_Ev(300, 300, delta=240))
        assert app.zoom != 1.0
        app.on_canvas_double(_Ev(2, 2))
        assert app.zoom == 1.0
    finally:
        _destroy(root)


def test_double_click_on_a_peak_does_not_reset_the_zoom(monkeypatch):
    """雙擊峰是要切換它，不該順便把畫面縮回全圖。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.on_wheel(_Ev(300, 300, delta=240))
        z = app.zoom
        x, y = app._canvas_xy(app.peaks[0])
        app.on_canvas_double(_Ev(x, y))
        assert app.zoom == z
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# On 欄的核取方塊要真的能點
# --------------------------------------------------------------------------- #
def test_clicking_the_on_column_toggles_the_peak(monkeypatch):
    """核取方塊看起來可以點，就必須真的可以點。

    使用者回報：表格裡的方塊只是裝飾，點了沒反應。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.current = "x.mea"
        monkeypatch.setattr("compound_consensus.state.save", lambda *a, **k: None)
        app._fill_peak_table()
        row = app.tree_peaks.get_children()[0]
        was = app.peaks[0]["active"]

        class _R:
            def __init__(self, region, col, row_):
                self.region, self.col, self.row = region, col, row_

        monkeypatch.setattr(app.tree_peaks, "identify_region",
                            lambda x, y: "cell")
        monkeypatch.setattr(app.tree_peaks, "identify_column", lambda x: "#2")
        monkeypatch.setattr(app.tree_peaks, "identify_row", lambda y: row)
        assert app.on_peaks_click(_Ev(10, 10)) == "break", "要吃掉事件，避免又改選取"
        assert app.peaks[0]["active"] != was
    finally:
        _destroy(root)


def test_clicking_another_column_does_not_toggle(monkeypatch):
    """點 RI 或強度欄只是選取，不該切換。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app._fill_peak_table()
        row = app.tree_peaks.get_children()[0]
        was = app.peaks[0]["active"]
        monkeypatch.setattr(app.tree_peaks, "identify_region", lambda x, y: "cell")
        monkeypatch.setattr(app.tree_peaks, "identify_column", lambda x: "#4")
        monkeypatch.setattr(app.tree_peaks, "identify_row", lambda y: row)
        assert app.on_peaks_click(_Ev(10, 10)) is None
        assert app.peaks[0]["active"] == was
    finally:
        _destroy(root)


def test_peak_table_has_a_number_column_and_no_retention_time(monkeypatch):
    """`#` 要對得上圈上的編號；RT s 不佔位（y 軸顯示的已經是 RI）。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app._fill_peak_table()
        heads = [app.tree_peaks.heading(c)["text"]
                 for c in app.tree_peaks["columns"]]
        assert heads[0] == "#"
        assert "RT s" not in heads
        first = app.tree_peaks.item(app.tree_peaks.get_children()[0], "values")
        assert str(first[0]) == "1", "第一列的編號要是 1，與圈上的 1 對應"
        assert first[1] in (_appmod.CHECK_ON, _appmod.CHECK_OFF)
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# Rules 面板
# --------------------------------------------------------------------------- #
def test_optional_rule_params_are_editable_and_apply_live(monkeypatch):
    """改選配規則的參數要立刻重新標記，不用重跑偵測。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.current = "x.mea"
        for p in app.peaks:
            p["prominence"] = 100.0
        app.open_rules()
        app._rule_vars["R001"]["on"].set(True)
        app._rule_vars["R001"]["params"]["threshold"]["var"].set("500")
        app._on_rules_changed()
        cfg = {e["rule_number"]: e for e in app.rules_config}
        assert cfg["R001"]["params"]["threshold"] == 500
        assert sum(1 for p in app.peaks if not p.get("rule_active", True)) > 0, \
            "門檻 500 應該把突出度 100 的峰全部否決"
    finally:
        _destroy(root)


def test_param_type_follows_the_original(monkeypatch):
    """`top_n` 是整數就要存成整數——全都當 float 會寫出 `top_n: 0.0`。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        assert app._coerce("7", 0) == 7 and isinstance(app._coerce("7", 0), int)
        assert app._coerce("0.05", 0.02) == pytest.approx(0.05)
        assert isinstance(app._coerce("0.05", 0.02), float)
    finally:
        _destroy(root)


def test_invalid_param_is_refused_not_silently_zeroed(monkeypatch):
    """看不懂的值不寫進 config，也不靜靜當成 0——要在漏斗裡講出來。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.open_rules()
        app._rule_vars["R001"]["params"]["threshold"]["var"].set("40")
        app._on_rules_changed()
        app._rule_vars["R001"]["params"]["threshold"]["var"].set("не число")
        app._on_rules_changed()
        cfg = {e["rule_number"]: e for e in app.rules_config}
        assert cfg["R001"]["params"]["threshold"] == 40, "無效輸入不可以覆蓋舊值"
        assert "看不懂" in app._rules_funnel.get("1.0", "end")
    finally:
        _destroy(root)


def test_changing_a_mandatory_rule_param_warns_that_redetection_is_needed(
        monkeypatch):
    """R004/R006 的參數在突出度門檻之前生效，改了必須重跑偵測——要講出來。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.open_rules()
        app._rule_vars["R004"]["params"]["half_width"]["var"].set("0.05")
        app._on_rules_changed()
        assert "R004/R006 的參數改過了" in app._rules_funnel.get("1.0", "end")
    finally:
        _destroy(root)


def test_mandatory_rules_cannot_be_switched_off_from_the_panel(monkeypatch):
    """強制規則的開關鎖住——它們定義峰編號的基準集合。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.open_rules()
        assert app._rule_vars["R004"]["locked"] is True
        assert app._rule_vars["R006"]["locked"] is True
        assert app._rule_vars["R001"]["locked"] is False
        app._rule_vars["R004"]["on"].set(False)     # 就算硬設也不能寫進 config
        app._on_rules_changed()
        cfg = {e["rule_number"]: e for e in app.rules_config}
        assert cfg["R004"]["enabled"] is True
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 規則的判定要一路貫穿到圈、表格與共識
# --------------------------------------------------------------------------- #
def test_rule_rejected_peaks_grey_out_on_the_heatmap(monkeypatch):
    """規則否決的峰，圈要跟著變灰。

    使用者回報：把 top_n 設成 10 之後表格對了，熱圖上第 11 顆之後的圈**沒有**跟著
    變灰。原因是圈看的是 `active`，而 `mark_rules()` 寫的是 `rule_active`，
    兩個鍵沒有接起來。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.peaks = _fake_peaks(5)
        for i, p in enumerate(app.peaks):
            p["rule_active"] = i < 2          # 只有前兩顆通過規則
            p["user_active"] = None
        L.apply_effective(app.peaks)
        app._draw_circles()
        red = sum(1 for i in app.circles
                  if app.canvas.itemcget(app.circles[i][0], "outline") == "#ff3b30")
        grey = sum(1 for i in app.circles
                   if app.canvas.itemcget(app.circles[i][0], "outline") == "#888888")
        assert (red, grey) == (2, 3)
    finally:
        _destroy(root)


def test_rule_verdict_reaches_the_active_flag_used_by_consolidation(monkeypatch):
    """`active` 必須跟著規則走——`areas2` 的 `active_only` 讀的就是它。

    不同步的話，圈是灰的、表格是琥珀色的，實際上那些峰**還是被算進共識**。
    """
    peaks = [{"rule_active": False, "user_active": None},
             {"rule_active": True, "user_active": None},
             {"rule_active": False, "user_active": True},    # 使用者救回來
             {"rule_active": True, "user_active": False}]    # 使用者取消
    L.apply_effective(peaks)
    assert [p["active"] for p in peaks] == [False, True, True, False]


def test_user_choice_beats_the_rule_and_is_marked(monkeypatch):
    """使用者是最終裁決者；救回來的峰畫虛線環，看得出是覆蓋了規則。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.peaks = _fake_peaks(2)
        app.current = "x.mea"
        monkeypatch.setattr("compound_consensus.state.save", lambda *a, **k: None)
        app.peaks[0]["rule_active"] = False
        app.peaks[0]["user_active"] = None
        L.apply_effective(app.peaks)
        app._draw_circles()
        assert app.canvas.itemcget(app.circles[0][0], "outline") == "#888888"

        app._toggle_index(0)               # 救回來
        assert L.effective_active(app.peaks[0]) is True
        assert L.is_rule_override(app.peaks[0]) is True
        assert app.canvas.itemcget(app.circles[0][0], "dash") != ""
    finally:
        _destroy(root)


def test_saved_state_keeps_only_explicit_user_choices(monkeypatch, tmp_path):
    """存檔只記使用者**明確表示過**的，`None` 不寫。

    存 `active` 會把當下的規則判定一起醃進檔案——之後規則改了也解不開，
    分不出這個 False 是規則說的還是使用者說的。
    """
    from compound_consensus import state as state_mod
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(tmp_path))
    mea = str(tmp_path / "x.mea")
    peaks = _fake_peaks(3)
    peaks[0]["user_active"] = False
    peaks[1]["user_active"] = None
    peaks[2]["user_active"] = True
    state_mod.save(mea, peaks)
    import json as _json
    saved = _json.loads((tmp_path / "x_peaks_state3.json").read_text(encoding="utf-8"))
    assert len(saved["active"]) == 2, "沒表示意見的那一顆不該被寫進去"

    fresh = _fake_peaks(3)
    state_mod.load(mea, fresh)
    assert [p["user_active"] for p in fresh] == [False, None, True]


# --------------------------------------------------------------------------- #
# 掃描期間的視窗與執行緒
# --------------------------------------------------------------------------- #
def test_status_text_changes_do_not_resize_the_window(monkeypatch):
    """狀態列的文字長度不可以決定視窗大小。

    使用者回報：按下「2. 掃描」時整個視窗縮成一小塊，掃完才彈回去。原因是狀態列
    每個檔換一次文字，pack 把寬度變化一路傳到最上層，而面板還空著時「自然大小」很小。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        root.update_idletasks()
        before = root.winfo_width()
        for t in ("找峰 1/18：260623_144213_A_1_1.mea", "短", ""):
            app.status.config(text=t)
            root.update_idletasks()
        assert root.winfo_width() == before
        assert int(app.status.cget("width")) > 0, "狀態列要有固定寬度"
    finally:
        _destroy(root)


def test_window_has_a_minimum_size(monkeypatch):
    """有下限就縮不成一小塊，不管版面怎麼重算。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        mw, mh = root.minsize()
        assert mw >= 800 and mh >= 600
    finally:
        _destroy(root)


def test_similarity_needs_at_least_three_files():
    """**兩個檔的相似度一定是 ±1，與資料無關**——不可以拿它當答案。

    每個區域跨檔標準化時，n=2 會讓兩個值必然變成 +a 與 −a，相關係數恆為 −1。
    使用者實測看到的 `-1.00` 就是這個算式產物。單一檔更會讓 `np.corrcoef` 回
    0 維的 nan，之後 `corr[i, j]` 直接 IndexError。
    """
    import numpy as np
    for n in (1, 2):
        with pytest.raises(ValueError, match="至少要 3 個檔"):
            L.similarity_matrix([[1.0, 2.0, 3.0, 4.0]] * n)
    # 記錄「錯的做法會怎樣」：n=2 的相關係數與資料無關，永遠 -1
    X = np.log10(np.array([[5.0, 90.0, 3.0], [70.0, 2.0, 40.0]]) + 1)
    Z = (X - X.mean(0)) / (X.std(0) + 1e-12)
    assert np.corrcoef(Z)[0, 1] == pytest.approx(-1.0, abs=1e-9)


def test_scan_covers_the_whole_folder_not_just_the_selected_group(monkeypatch):
    """掃描要涵蓋所有候選檔。

    相似度是拿來「幫你決定哪些檔同組」的，只掃已經選好的那一組是循環論證。
    實測回報：勾了一個檔再按掃描，只有那一個被處理，其餘整欄變成「不在掃描範圍」。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        started = []
        monkeypatch.setattr(threading := __import__("threading"), "Thread",
                            lambda target, args, daemon: type(
                                "T", (), {"start": lambda s: started.append(args[0])})())
        app.files = ["a.mea", "b.mea", "c.mea", "d.mea"]
        app.group = {"a.mea"}                 # 只勾了一個
        app.scan_group()
        assert started and set(started[0]) == set(app.files),             "掃描的範圍要是整個資料夾，不是那一個"
    finally:
        _destroy(root)


def test_similarity_is_computed_off_the_ui_thread(monkeypatch):
    """相似度不可以在主執行緒算——那會讓視窗凍住十幾秒到好幾分鐘。"""
    src = open(os.path.join(PROJECT_ROOT, "compound_consensus", "app.py"),
               encoding="utf-8").read()
    assert "_compute_similarity" not in src, \
        "相似度要在 _scan_worker 裡算完，只把結果丟回佇列"
    assert '"corr"' in src, "結果用 corr 訊息回主執行緒"

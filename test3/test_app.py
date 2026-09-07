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
    # **每一個 modal 都要換掉,漏一個就整批卡死。** `showinfo` 漏掉過一次:
    # 測試跑到第 11 項時開了一個真的對話框,pytest 沒有輸出、也不會逾時,
    # 看起來像測試變慢而不是卡住。
    for name in ("askyesno", "askokcancel", "askquestion", "askretrycancel",
                 "showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(messagebox, name, lambda *a, **k: True)
    from compound_consensus import app as appmod
    return tk, root, appmod, appmod.ConsensusApp(root)


def _destroy(root):
    import gc
    try:
        root.destroy()
    except Exception:
        pass
    gc.collect()



def _ready_for_mode(app, base=None, group=None):
    """讓模式 2/3 進得去,**但不覆蓋測試自己擺好的狀態**。

    模式 2/3 在沒有基準時是灰的(沒有基準就沒有相似度,沒有相似度就挑不出同組)。
    測試要看那兩個面板的內容時得先滿足這個前提,否則 `on_mode_change()` 會把模式
    退回 1,面板永遠是空的。

    只補「還沒設好的」——直接指定會把測試自己建的組員洗掉,錯誤訊息看起來像
    程式錯了而其實是測試互相打架。順便清 `_cons_dirty`:模式 3 現在會自己彙整,
    測試不該真的跑那一輪。
    """
    if not app.base:
        app.base = base or (sorted(app.group)[0] if app.group
                            else (app.files[0] if app.files else "/x/a.mea"))
    if len(app.group) < 2:
        app.group = set(group or {app.base, "/x/_filler.mea"})
    app._cons_dirty = False
    app._sync_mode_buttons()
    return app


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
        for name in ("tree_files", "canvas", "tree_peaks", "group_txt",
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
        _ready_for_mode(app)          # 模式 2/3 沒有基準就是灰的,進不去
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
        _ready_for_mode(app)
        app.mode.set(appmod.MODE_COMPOUND)
        app.on_mode_change()
        rows = app.tree_cmpd.get_children()
        assert len(rows) == 2
        v0 = app.tree_cmpd.item(rows[0], "values")
        assert v0[0] == "3/3"
        # 標籤講的是**哪些軸對上**，不是「幾維」：使用者要在共識表上直接分辨
        # GC / IMS / 兩者，"2D" 說不出是哪兩軸。
        assert v0[1] == "GC+IMS"
        assert app.tree_cmpd.item(rows[1], "values")[1] == "GC"
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
        _ready_for_mode(app)
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
        _ready_for_mode(app)
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
def test_optional_rule_params_are_editable_and_apply_live(monkeypatch, tmp_path):
    """改選配規則的參數要立刻重新標記，不用重跑偵測。

    載入了檔案時改的是**那個檔自己的**那一份（`cur_rules`），不是預設——
    見 `test_editing_rules_does_not_touch_the_default`。
    """
    import areas2 as _areas2
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        monkeypatch.setattr(_areas2, "RESULTS_DIR", str(tmp_path))
        _with_image(app)
        app.current = "x.mea"
        for p in app.peaks:
            p["prominence"] = 100.0
        app.open_rules()
        app._rule_vars["R001"]["on"].set(True)
        app._rule_vars["R001"]["params"]["threshold"]["var"].set("500")
        app._on_rules_changed()
        cfg = {e["rule_number"]: e for e in app.cur_rules}
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


def test_user_choice_beats_the_rule(monkeypatch):
    """使用者是最終裁決者：規則否決的峰可以救回來。

    救回來之後圈就是**普通的實線紅**——與其他選取的峰一樣。虛線環拿掉了
    （使用者要求）：熱圖上第三種樣式分不出是什麼意思，反而像畫錯了。
    「這是覆蓋了規則」的資訊留在表格（琥珀色），那裡有欄位標題可以解釋自己。
    """
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
        assert L.is_rule_override(app.peaks[0]) is True, "邏輯上仍然是覆蓋"
        assert app.canvas.itemcget(app.circles[0][0], "outline") == "#ff3b30"
        assert "byrule" in app._peak_tags(app.peaks[0]), "表格仍要標出來"
    finally:
        _destroy(root)


def test_circles_have_only_two_styles(monkeypatch):
    """圈只有兩種樣子：選了＝實線紅，沒選＝實線灰。**沒有虛線。**

    回歸測試（使用者回報）：取消選取的圈變成紅色虛線，看起來像程式畫錯了。
    四種狀態全部檢查一次，確保沒有第三種樣式漏回來。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.peaks = _fake_peaks(4)
        for p, (ra, ua) in zip(app.peaks, [(True, None), (True, False),
                                           (False, None), (False, True)]):
            p["rule_active"], p["user_active"] = ra, ua
        L.apply_effective(app.peaks)
        app._draw_circles()
        want = ["#ff3b30", "#888888", "#888888", "#ff3b30"]
        for i, col in enumerate(want):
            cid = app.circles[i][0]
            assert app.canvas.itemcget(cid, "outline") == col, i
            assert app.canvas.itemcget(cid, "dash") == "", "第 %d 顆是虛線" % i
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


# --------------------------------------------------------------------------- #
# 逐峰化合物比對欄（GC×IMS / GC / IMS / ▶）
#
# 沒有這四欄的話，逐檔挑峰時看不到峰對到什麼化合物——等於閉著眼睛選。
# 語意與第一支應用 `main.py` 的 `PEAK_TABLE_COLUMNS` 一致。
# --------------------------------------------------------------------------- #
def test_peak_table_has_the_compound_columns(monkeypatch):
    """四個比對欄要在，而且 `#` / `On` 仍然是前兩欄（點擊分派靠欄位序號）。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app._fill_peak_table()
        cols = list(app.tree_peaks["columns"])
        assert cols[:2] == ["n", "on"], "On 必須是 #2，on_peaks_click 依序號分派"
        assert cols[8] == "trig", "▶ 必須是 #9，同上"
        for c in ("gc_ims", "gc", "ims", "trig"):
            assert c in cols
        heads = [app.tree_peaks.heading(c)["text"] for c in cols]
        assert "GC×IMS" in heads and "IMS" in heads
    finally:
        _destroy(root)


def test_uncompared_peak_shows_pending_not_a_dash(monkeypatch):
    """還沒比對的格子要顯示「…」而不是「—」。

    「—」的意思是**比過了、沒有命中**；比對還在背景跑時顯示「—」會讓使用者
    以為這顆峰沒有任何候選，然後就把它取消勾選了。兩種狀態必須分得開。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        p = _fake_peaks(1)[0]                    # 沒有 "matches" 鍵
        assert app._cell_gc_ims(p) == "…"
        assert app._cell_gc(p) == "…"
        assert app._cell_ims(p) == "…"
        p["matches"] = {"gc_matches": [], "ims_matches": [], "combined_matches": []}
        assert app._cell_gc_ims(p) == "—", "比過了、沒命中才是「—」"
        assert app._cell_gc(p) == "—"
        assert app._cell_ims(p) == "—"
    finally:
        _destroy(root)


def test_gc_column_marks_seconds_when_it_falls_back_to_retention_time(monkeypatch):
    """退到保留時間時，格子要帶單位、標題要變成 `GC (RT s)`。

    回歸測試（`CLAUDE.md` 不變量）：沒有 RI 校正時 `match_all()` 會**靜靜**改用
    保留時間比庫的 `Rt[sec]`。標題若還寫 "GC (RI)"，畫面上秒數與 RI 長得一模一樣，
    而保留時間不跨儀器/管柱/方法轉移。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        for p in app.peaks:
            p["matches"] = {
                "gc_dimension": "rt", "ims_dimension": None,
                "gc_matches": [{"Name": "X", "Rt[sec]": 412.0, "delta_rt": 0.5,
                                "match_dimensions": ["rt"]}],
                "ims_matches": [], "combined_matches": []}
        app._fill_peak_table()
        assert app._cell_gc(app.peaks[0]).endswith("s (Δ0.50)"), \
            app._cell_gc(app.peaks[0])
        assert app.tree_peaks.heading("gc")["text"] == "GC (RT s)"
        assert "保留時間" in app.status.cget("text")
    finally:
        _destroy(root)


def test_gc_column_shows_ri_when_calibrated(monkeypatch):
    """有 RI 時標題是 `GC (RI)`，格子不帶秒的單位。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        for p in app.peaks:
            p["matches"] = {
                "gc_dimension": "ri", "ims_dimension": "drift_rel",
                "gc_matches": [{"Name": "X", "RI": 901.5, "delta_ri": 1.5,
                                "match_dimensions": ["ri"]}],
                "ims_matches": [{"Dt[a.u.]": 1.104, "delta_drift_rel": 0.004,
                                 "match_dimensions": ["drift_rel"]}],
                "combined_matches": []}
        app._fill_peak_table()
        assert app.tree_peaks.heading("gc")["text"] == "GC (RI)"
        assert "s (" not in app._cell_gc(app.peaks[0])
        assert app._cell_ims(app.peaks[0]) == "1.104 (Δ0.004)"
    finally:
        _destroy(root)


def test_gc_ims_column_reports_how_many_other_candidates_share_the_hit(monkeypatch):
    """兩軸都同意時不只給第一名——後面還有幾個要講。

    庫裡 ±5 RI 的窗內常有上百個化合物，即使兩軸都對上也可能不只一個。只顯示
    第一名會讓人以為鑑定是唯一的。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        p = _fake_peaks(1)[0]
        p["matches"] = {"combined_matches": [{"Name": "Acetone"}, {"Name": "Other"}],
                        "gc_matches": [], "ims_matches": []}
        assert app._cell_gc_ims(p) == "Acetone（+1）"
        p["matches"]["combined_matches"] = [{"Name": "Acetone"}]
        assert app._cell_gc_ims(p) == "Acetone"
    finally:
        _destroy(root)


def test_trigger_is_blank_for_a_deselected_peak(monkeypatch):
    """取消勾選的峰不給 ▶——它不參與共識，開它的候選只會誤導。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.peaks[0]["user_active"] = False
        L.apply_effective(app.peaks)
        assert app._peak_row(0, app.peaks[0])[8] == " "
        assert app._peak_row(1, app.peaks[1])[8] == "▶"
    finally:
        _destroy(root)


def test_match_results_are_cached_by_coordinate_not_peak_id(monkeypatch):
    """比對快取的鍵是 `(檔名, rt_index, dt_index)`，**不是 `peak_id`**。

    回歸測試：`peak_id` 是基準集內的突出度排名，規則參數一改就重新編號，
    用它當鍵會讓快取黏到別顆峰上去——`state.py` 開頭記的是同一條坑。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        app.current = "/x/A.mea"
        p = {"peak_id": 7, "rt_index": 1200, "dt_index": 733}
        key = app._cache_key(app.current, p)
        assert key == ("A.mea", 1200, 733)
        assert 7 not in key, "peak_id 不可以進快取的鍵"
        app.match_cache[key] = {"gc_matches": [{"Name": "Z"}]}
        p2 = {"peak_id": 99, "rt_index": 1200, "dt_index": 733}   # 重新編號過
        app._apply_cached_matches(app.current, [p2])
        assert p2["matches"]["gc_matches"][0]["Name"] == "Z"
    finally:
        _destroy(root)


def test_matches_for_another_file_do_not_hijack_the_view(monkeypatch):
    """使用者切到別的檔之後，舊檔的比對結果只進快取，不重畫畫面。

    與 `prepared` 那條同一個道理：背景結果回來時使用者可能已經在看別的檔，
    照畫會把畫面換成舊檔的內容而看不出為什麼。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.current = "/x/NOW.mea"
        seen = []
        monkeypatch.setattr(app, "_refresh_peak_rows", lambda: seen.append(1))
        app.q.put(("matched", ("/x/OLD.mea", [(("OLD.mea", 1, 2), {"gc_matches": []})])))
        app._drain()
        assert ("OLD.mea", 1, 2) in app.match_cache, "結果照樣要進快取"
        assert seen == [], "但不重畫目前這個檔"
    finally:
        _destroy(root)


def test_library_failure_is_remembered_and_not_retried(monkeypatch):
    """庫載不起來就記住，不要每換一個檔再失敗一次把狀態列洗掉。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        started = []
        monkeypatch.setattr(_appmod.threading, "Thread",
                            lambda *a, **k: started.append(k) or _Dummy())
        app.q.put(("lib_fail", "no library_data/"))
        app._drain()
        assert app.lib_state == "unavailable"
        assert app._ensure_libraries() is False
        assert started == [], "已知失敗就不要再開一條執行緒去重試"
    finally:
        _destroy(root)


class _Dummy:
    def start(self):
        pass


def test_default_threshold_is_half(monkeypatch):
    """門檻預設「超過一半的重複看得到」。"""
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        assert app.frac.get() == "1/2"
    finally:
        _destroy(root)


def test_window_opens_maximised(monkeypatch):
    """預設就攤滿整個螢幕。

    判斷用 `state()`（zoomed）而**不是**比對 geometry 的高度：最大化之後視窗
    管理員回報的是**工作區**高度，會比螢幕矮一個工作列。拿螢幕高度去斷言，
    會在一台完全正常的機器上失敗（實測 889 vs 960）。
    """
    tk, root, _appmod, app = _app(monkeypatch)
    try:
        root.update_idletasks()
        if root.state() == "zoomed":
            w = int(root.geometry().split("+")[0].split("x")[0])
            assert w >= root.winfo_screenwidth() - 8, root.geometry()
            return
        # 視窗管理員不支援 zoomed 時，退路的 geometry 要是滿螢幕
        w, h = (int(v) for v in root.geometry().split("+")[0].split("x"))
        assert w >= root.winfo_screenwidth() - 8
        assert h >= root.winfo_screenheight() - 8
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 共識表也要看得出 GC / IMS / 兩者
# --------------------------------------------------------------------------- #
def test_ims_only_region_is_labelled_ims_not_collapsed_into_ri(monkeypatch):
    """只有漂移對上的區域要標 IMS，不能跟「只有 RI」混為一談。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        rows = _fake_consolidated()
        rows[1]["match_dimension"] = "ims_only"
        app.consolidated = rows
        _ready_for_mode(app)
        app.mode.set(appmod.MODE_COMPOUND)
        app.on_mode_change()
        got = app.tree_cmpd.item(app.tree_cmpd.get_children()[1], "values")[1]
        assert got == "IMS", got
    finally:
        _destroy(root)


def test_mixed_region_is_not_shown_as_a_single_dimension(monkeypatch):
    """各檔用了不同維度時要標「混合」，不可以挑一個當代表。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        rows = _fake_consolidated()
        rows[0]["match_dimension"] = "mixed"
        app.consolidated = rows
        _ready_for_mode(app)
        app.mode.set(appmod.MODE_COMPOUND)
        app.on_mode_change()
        assert app.tree_cmpd.item(app.tree_cmpd.get_children()[0],
                                  "values")[1] == "混合"
    finally:
        _destroy(root)


def test_candidate_detail_shows_a_dimension_per_candidate(monkeypatch):
    """雙擊之後，**每一個候選**都要有自己的維度欄。

    區域層級只給一個標籤是不夠的：同一個區域裡有些候選兩軸都對上、有些只有 RI，
    證據強度差很多，混在一張表裡看不出來就會被平等對待。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        rows = _fake_consolidated()
        rows[0]["candidates"] = [
            dict(rows[0]["candidates"][0], dimension="combined"),
            {"cas": "67-64-1", "name": "Acetone", "library_ri": 842.0,
             "n_support": 2, "n_files_with_peak": 3, "support": 2 / 3,
             "mean_abs_delta_ri": 1.2, "files": [], "dimension": "gc_only"},
        ]
        app.consolidated = rows
        _ready_for_mode(app)
        app.mode.set(appmod.MODE_COMPOUND)
        app.on_mode_change()
        app.tree_cmpd.selection_set("0")
        app.tree_cmpd.focus("0")
        app.show_candidates()
        from tkinter import ttk
        win = [w for w in app.root.winfo_children()
               if isinstance(w, tk.Toplevel)][-1]
        t = [c for c in win.winfo_children() if isinstance(c, ttk.Treeview)][0]
        assert "dim" in t["columns"]
        vals = [t.item(i, "values") for i in t.get_children()]
        assert vals[0][1] == "GC+IMS"
        assert vals[1][1] == "GC"
        win.destroy()
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 檔案清單的選取回饋
# --------------------------------------------------------------------------- #
def test_file_list_shows_an_empty_box_when_not_in_the_group(monkeypatch):
    """未選的檔要畫 `☐`，不可以留空白。

    空格子看不出這一欄可以點——使用者只好繞去模式 2 才敢加檔。峰表的 `On` 欄
    早就是 `☑`/`☐` 兩態，這裡漏了。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.files = ["/x/a.mea", "/x/b.mea"]
        app.group = {"/x/a.mea"}
        app._refresh_files()
        rows = app.tree_files.get_children()
        # values[0] 現在是「基準」欄，組別在 [1]
        assert app.tree_files.item(rows[0], "values")[1] == appmod.CHECK_ON
        assert app.tree_files.item(rows[1], "values")[1] == appmod.CHECK_OFF
    finally:
        _destroy(root)


def test_file_in_the_group_is_tinted_as_well_as_ticked(monkeypatch):
    """在組裡的列要**同時**有勾與底色。

    兩個都要：ttk 的選取色會蓋掉 tag 的底色（style map 裡
    `('selected', 'SystemHighlight')`），所以正在看的那一列即使在組裡也不會是
    藍底——那時只剩方塊在說話。反過來，一眼掃過整份清單時底色比方塊好認。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.files = ["/x/a.mea", "/x/b.mea"]
        app.group = {"/x/a.mea"}
        app._refresh_files()
        rows = app.tree_files.get_children()
        assert "ingroup" in app.tree_files.item(rows[0], "tags")
        assert "ingroup" not in app.tree_files.item(rows[1], "tags")
        assert app.tree_files.tag_configure("ingroup", "background") != ""
    finally:
        _destroy(root)


def test_group_checkbox_is_clickable_in_every_mode(monkeypatch):
    """組欄的方塊在**任何模式**都要能點——看起來能點就要能點。

    不必為了把一個檔加進這一組而特地切到模式 2。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.files = ["/x/a.mea"]
        app._refresh_files()
        # 組欄是 #1；直接餵一個假事件，繞開真實座標
        monkeypatch.setattr(app.tree_files, "identify_region", lambda x, y: "cell")
        # 組欄是 #2（#1 已經讓給「基準」欄）
        monkeypatch.setattr(app.tree_files, "identify_column", lambda x: "#2")
        monkeypatch.setattr(app.tree_files, "identify_row", lambda y: "/x/a.mea")

        class _Ev:
            x = y = 5

        for mode in (appmod.MODE_HEATMAP, appmod.MODE_GROUP, appmod.MODE_COMPOUND):
            app.mode.set(mode)
            app.group = set()
            assert app.on_files_click(_Ev()) == "break", \
                "要回 break，否則模式 1 會順帶白跑一次載入熱圖"
            assert "/x/a.mea" in app.group, "模式 %s 下方塊點不動" % mode
    finally:
        _destroy(root)


def test_clicking_the_similarity_column_does_not_toggle_the_group(monkeypatch):
    """點相似度那一欄是「照模式處理」，不可以順手改變這一組。

    欄位序號會變（狀態燈插進 #2 之後相似度變成 #3）——`on_files_click` 依序號
    分派，所以這一條同時守著「別把分派打到別欄去」。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.files = ["/x/a.mea"]
        app._refresh_files()
        monkeypatch.setattr(app.tree_files, "identify_region", lambda x, y: "cell")
        monkeypatch.setattr(app.tree_files, "identify_column", lambda x: "#4")
        monkeypatch.setattr(app.tree_files, "identify_row", lambda y: "/x/a.mea")

        class _Ev:
            x = y = 5

        app.group = set()
        assert app.on_files_click(_Ev()) is None
        assert app.group == set(), "點非組欄不該改變這一組"
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 檔案面板的狀態燈
#
# 使用者(2026-09-07):「按 2 掃描才有」這種寫法不專業;每個 mea 應該給一個紅/綠燈,
# 綠＝已經有 npz,紅＝沒有,點下去不管有沒有都重做。
# --------------------------------------------------------------------------- #
def test_ready_light_is_green_only_when_clicking_is_actually_instant(
        monkeypatch, tmp_path):
    """綠燈的意思必須是「點下去就有」——**三態不是兩態**。

    只看 `.npz` 的話，有 `.npz` 但還沒找峰的檔會亮綠燈，而點下去仍要等 55 秒。
    綠燈說謊比沒有燈更糟。
    """
    import areas2 as _areas2
    tk, root, appmod, app = _app(monkeypatch)
    try:
        monkeypatch.setattr(_areas2, "RESULTS_DIR", str(tmp_path))
        mea = "/x/a.mea"
        # 什麼都沒有 → 紅
        assert app._ready_light(mea) == appmod.READY_RED
        # 只有 .npz → 黃（還要找峰）
        open(_areas2._npz_path(mea), "wb").close()
        monkeypatch.setattr(appmod.L, "peaks_are_current", lambda *a, **k: False)
        assert app._ready_light(mea) == appmod.READY_AMBER
        # .npz + 找峰都在 → 綠
        monkeypatch.setattr(appmod.L, "peaks_are_current", lambda *a, **k: True)
        assert app._ready_light(mea) == appmod.READY_GREEN
    finally:
        _destroy(root)


def test_file_panel_cells_are_short_labels_not_sentences(monkeypatch):
    """格子裡放短標籤，整句解釋放在下方說明。

    回歸測試(使用者回報)：「按「2. 掃描」後才有」塞在格子裡既把欄寬撐開，
    讀起來也像錯誤訊息。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.files = ["/x/a.mea"]
        app.corr = None
        app._refresh_files()
        vals = app.tree_files.item(app.tree_files.get_children()[0], "values")
        # 還沒選基準時就直說「選基準」——那是使用者下一步該做的事,
        # 而不是丟一個「未計算」讓他自己猜要按哪裡。
        assert vals[3] == "選基準"
        assert "按" not in vals[3] and len(vals[3]) <= 4
        # **不可以寫「待掃描」**：那和「狀態」欄、和「2. 掃描全部」按鈕撞在一起，
        # 已經找過峰的檔看到它會以為峰沒找完（使用者實際回報過）。
        assert "掃描" not in vals[3]
        app.on_mode_change()
        assert "相似度" in app.hint.cget("text"), "解釋要在說明區看得到"
    finally:
        _destroy(root)


def test_clicking_the_status_light_regenerates_even_when_it_exists(
        monkeypatch, tmp_path):
    """點狀態燈＝**不管有沒有都重做**，而且要先問一聲（約 68 秒，容易誤點）。"""
    import areas2 as _areas2
    tk, root, appmod, app = _app(monkeypatch)
    try:
        monkeypatch.setattr(_areas2, "RESULTS_DIR", str(tmp_path))
        asked = []
        from tkinter import messagebox
        monkeypatch.setattr(messagebox, "askyesno",
                            lambda *a, **k: asked.append(a) or True)
        started = []

        class _T:
            def __init__(self, **kw):
                started.append(kw.get("args"))

            def start(self):
                pass

        monkeypatch.setattr(appmod.threading, "Thread",
                            lambda *a, **kw: _T(**kw))
        app.files = ["/x/a.mea"]
        app._refresh_files()
        monkeypatch.setattr(app.tree_files, "identify_region", lambda x, y: "cell")
        monkeypatch.setattr(app.tree_files, "identify_column", lambda x: "#3")
        monkeypatch.setattr(app.tree_files, "identify_row", lambda y: "/x/a.mea")

        class _Ev:
            x = y = 5

        assert app.on_files_click(_Ev()) == "break"
        assert asked, "重做要花 68 秒，一定要先確認"
        assert started == [("/x/a.mea",)], "要真的開背景工作"
        assert app.group == set(), "點狀態燈不該順手改變這一組"
    finally:
        _destroy(root)


def test_regenerate_removes_the_stale_npz_first(monkeypatch, tmp_path):
    """`ensure_npz()` 存在就直接回傳、沒有 force 參數——要重做只能先移掉。"""
    import areas2 as _areas2
    tk, root, appmod, app = _app(monkeypatch)
    try:
        monkeypatch.setattr(_areas2, "RESULTS_DIR", str(tmp_path))
        mea = "/x/a.mea"
        npz = _areas2._npz_path(mea)
        with open(npz, "wb") as f:
            f.write(b"stale")
        seen = {}
        monkeypatch.setattr(_areas2, "ensure_npz",
                            lambda p, **k: seen.setdefault(
                                "existed_at_call", os.path.exists(npz)))
        monkeypatch.setattr(_areas2, "detect_one", lambda *a, **k: ([], {}, {}))
        monkeypatch.setattr(appmod.L, "detect_cached", lambda *a, **k: ([], {}, {}))
        app._regen_worker(mea)
        assert seen["existed_at_call"] is False, "舊的 .npz 要先移掉，否則等於沒重做"
        kinds = []
        while not app.q.empty():
            kinds.append(app.q.get_nowait()[0])
        assert "regenerated" in kinds
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 彙整必須拿「使用者目前勾選的峰」去做
#
# 使用者的提問（2026-09-07）:「在熱圖上選/不選峰你會記住吧?因為要找共同峰時
# 應該要拿目前選取的峰去彙整」。修之前答案是**不會**。
# --------------------------------------------------------------------------- #
def test_consolidation_applies_the_users_peak_choices(monkeypatch, tmp_path):
    """`_cons_worker` 要把使用者的勾選經由 `on_peaks` 套進共識,而且要 apply_effective。

    回歸測試(兩個原因疊在一起,都不會報錯):
    1. `state.load()` 只寫 `user_active`,不呼叫 `apply_effective()` 的話
       `build_consensus_areas(active_only=True)` 讀的 `active` 根本不會變。
    2. 先跑一次 `consensus_regions()`、對回傳的 `per_file` 套選取、再跑第二次是
       無效的——第二次重新偵測、產生全新的 dict,選取整批被丟掉。
    """
    import areas2 as _areas2
    from compound_consensus import state as state_mod
    tk, root, appmod, app = _app(monkeypatch)
    try:
        monkeypatch.setattr(_areas2, "RESULTS_DIR", str(tmp_path))
        # 使用者把 (1,2) 那顆峰關掉
        state_mod.save("a.mea", [{"rt_index": 1, "dt_index": 2,
                                  "user_active": False}])

        captured = {}

        def fake_regions(paths, rc, **kw):
            captured["on_peaks"] = kw.get("on_peaks")
            return [], {p: [] for p in paths}, {}

        monkeypatch.setattr(appmod.L, "consensus_regions", fake_regions)
        monkeypatch.setattr(appmod.calibration, "_read_header_lite",
                            lambda p: {})
        monkeypatch.setattr(appmod.calibration, "resolve_calibrations_cached",
                            lambda *a, **k: {"ri": (None, "none", None)})
        monkeypatch.setattr(appmod.library, "resolve_data_dir", lambda: str(tmp_path))
        monkeypatch.setattr(appmod.identify, "load_libraries",
                            lambda *a, **k: ([], [], {}))
        monkeypatch.setattr(appmod.L, "rank_areas", lambda *a, **k: [])

        app.frac.set("1/2")
        app._cons_worker(["a.mea", "b.mea"])

        hook = captured.get("on_peaks")
        assert callable(hook), "彙整必須傳 on_peaks，否則選取套不進去"

        peaks = [{"rt_index": 1, "dt_index": 2, "active": True, "rule_active": True},
                 {"rt_index": 9, "dt_index": 9, "active": True, "rule_active": True}]
        hook("a.mea", peaks)
        assert peaks[0]["active"] is False, \
            "使用者關掉的峰仍被算進共識——apply_effective 沒被呼叫"
        assert peaks[1]["active"] is True, "沒表示意見的峰要聽規則的"
    finally:
        _destroy(root)


def test_consolidation_detects_each_file_only_once(monkeypatch, tmp_path):
    """彙整只跑一輪偵測。兩輪除了丟掉選取之外也白花時間。"""
    import areas2 as _areas2
    tk, root, appmod, app = _app(monkeypatch)
    try:
        monkeypatch.setattr(_areas2, "RESULTS_DIR", str(tmp_path))
        calls = []

        def fake_regions(paths, rc, **kw):
            calls.append(list(paths))
            return [], {p: [] for p in paths}, {}

        monkeypatch.setattr(appmod.L, "consensus_regions", fake_regions)
        monkeypatch.setattr(appmod.calibration, "_read_header_lite", lambda p: {})
        monkeypatch.setattr(appmod.calibration, "resolve_calibrations_cached",
                            lambda *a, **k: {"ri": (None, "none", None)})
        monkeypatch.setattr(appmod.library, "resolve_data_dir", lambda: str(tmp_path))
        monkeypatch.setattr(appmod.identify, "load_libraries",
                            lambda *a, **k: ([], [], {}))
        monkeypatch.setattr(appmod.L, "rank_areas", lambda *a, **k: [])

        app.frac.set("1/2")
        app._cons_worker(["a.mea", "b.mea"])
        assert len(calls) == 1, "偵測跑了 %d 輪，應該只有 1 輪" % len(calls)
    finally:
        _destroy(root)


def test_toggling_a_peak_saves_immediately(monkeypatch, tmp_path):
    """每切換一次就存檔——不必按任何「儲存」,關掉視窗也不會丟。"""
    import areas2 as _areas2
    from compound_consensus import state as state_mod
    tk, root, appmod, app = _app(monkeypatch)
    try:
        monkeypatch.setattr(_areas2, "RESULTS_DIR", str(tmp_path))
        _with_image(app)
        app.current = "/x/a.mea"
        app._toggle_index(0)
        path = state_mod.state_path("/x/a.mea")
        assert os.path.exists(path), "切換之後應該立刻有狀態檔"
        import json
        saved = json.load(open(path, encoding="utf-8"))["active"]
        key = state_mod.peak_key(app.peaks[0])
        assert saved[key] is False, saved
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 模式 2 的右側面板 —— 不再重複左邊已經說過的事
#
# 使用者(2026-09-07):「選項 2 除了把我選的檔列在右邊之外什麼也沒做,左邊已經看得到
# 反白的 mea 了,這樣是不是多餘?」是。組員與相似度兩欄跟左側檔案面板一字不差。
# --------------------------------------------------------------------------- #
def test_group_panel_does_not_just_relist_the_left_panel(monkeypatch):
    """右側不再是成員名單——那三欄有兩欄跟左邊重複。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        assert not hasattr(app, "tree_group"), "成員名單應該已經拿掉"
        assert hasattr(app, "group_txt")
    finally:
        _destroy(root)


def test_group_panel_names_the_file_dragging_the_group_down(monkeypatch):
    """離群者要指名道姓,而且要在**按下彙整之前**就看得到。"""
    import numpy as np
    tk, root, appmod, app = _app(monkeypatch)
    try:
        files = ["/x/a.mea", "/x/b.mea", "/x/c.mea", "/x/d.mea"]
        app.files = list(files)
        app.group = set(files)
        app.corr_files = list(files)
        app.corr = np.array([[1.00, 0.95, 0.93, 0.55],
                             [0.95, 1.00, 0.94, 0.57],
                             [0.93, 0.94, 1.00, 0.54],
                             [0.55, 0.57, 0.54, 1.00]])
        _ready_for_mode(app)
        app.mode.set(appmod.MODE_GROUP)
        app.on_mode_change()
        txt = app.group_txt.get("1.0", "end")
        assert "這一組 4 個檔" in txt
        assert "d.mea" in txt, "要說出是哪一個檔"
        assert "明顯不同組" in txt
    finally:
        _destroy(root)


def test_group_panel_translates_the_threshold_into_a_file_count(monkeypatch):
    """門檻要換算成「幾個檔」——原本要等彙整完才看得到。"""
    import numpy as np
    tk, root, appmod, app = _app(monkeypatch)
    try:
        files = ["/x/%d.mea" % i for i in range(5)]
        app.files = list(files)
        app.group = set(files)
        app.corr_files = list(files)
        app.corr = np.full((5, 5), 0.95)
        app.frac.set("1/2")
        _ready_for_mode(app)
        app.mode.set(appmod.MODE_GROUP)
        app.on_mode_change()
        assert "要有 3 個" in app.group_txt.get("1.0", "end")
        app.frac.set("2/3")
        app._fill_group_panel()
        assert "要有 4 個" in app.group_txt.get("1.0", "end")
    finally:
        _destroy(root)


def test_group_panel_says_unknown_before_the_scan(monkeypatch):
    """還沒掃描就沒有相似度——要講「尚未計算」,不是假裝一致。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.files = ["/x/a.mea", "/x/b.mea"]
        app.group = {"/x/a.mea", "/x/b.mea"}
        app.corr, app.corr_files = None, []
        _ready_for_mode(app)
        app.mode.set(appmod.MODE_GROUP)
        app.on_mode_change()
        txt = app.group_txt.get("1.0", "end")
        assert "尚未計算" in txt
        assert "明顯不同組" not in txt and "可以彙整" not in txt
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 模式 2 / 3 的熱圖是唯讀的
# --------------------------------------------------------------------------- #
def test_clicking_a_peak_in_group_mode_does_not_toggle_it(monkeypatch):
    """模式 2/3 點熱圖不可以改選取。

    使用者回報:那兩個模式的右側面板不是峰表,點掉一顆峰完全沒有回饋——選取被改了
    而畫面上看不出來,等到彙整才發現票數不對。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.current = "/x/a.mea"
        xy = app._canvas_xy(app.peaks[0])

        class _Ev:
            pass

        for mode in (appmod.MODE_GROUP, appmod.MODE_COMPOUND):
            app.mode.set(mode)
            before = [L.effective_active(p) for p in app.peaks]
            press, rel = _Ev(), _Ev()
            press.x, press.y = int(xy[0]), int(xy[1])
            rel.x, rel.y = int(xy[0]), int(xy[1])
            app.on_press(press)
            app.on_release(rel)
            after = [L.effective_active(p) for p in app.peaks]
            assert after == before, "模式 %s 下熱圖應該是唯讀的" % mode
            assert "唯讀" in app.status.cget("text")
    finally:
        _destroy(root)


def test_clicking_a_peak_in_heatmap_mode_still_toggles(monkeypatch, tmp_path):
    """模式 1 照舊可以點——唯讀只該擋住模式 2/3。"""
    import areas2 as _areas2
    tk, root, appmod, app = _app(monkeypatch)
    try:
        monkeypatch.setattr(_areas2, "RESULTS_DIR", str(tmp_path))
        _with_image(app)
        app.current = "/x/a.mea"
        app.mode.set(appmod.MODE_HEATMAP)
        xy = app._canvas_xy(app.peaks[0])

        class _Ev:
            pass

        press, rel = _Ev(), _Ev()
        press.x, press.y = int(xy[0]), int(xy[1])
        rel.x, rel.y = int(xy[0]), int(xy[1])
        before = L.effective_active(app.peaks[0])
        app.on_press(press)
        app.on_release(rel)
        assert L.effective_active(app.peaks[0]) is not before
    finally:
        _destroy(root)


def test_readonly_badge_is_drawn_only_outside_heatmap_mode(monkeypatch):
    """唯讀要**看得見**。點了沒反應跟壞掉了看起來一模一樣。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.mode.set(appmod.MODE_HEATMAP)
        app._refresh_badge()
        assert app.canvas.find_withtag("readonly") == ()
        _ready_for_mode(app)
        app.mode.set(appmod.MODE_GROUP)
        app._refresh_badge()
        assert app.canvas.find_withtag("readonly") != (), "模式 2 要有唯讀標記"
        app.mode.set(appmod.MODE_HEATMAP)
        app._refresh_badge()
        assert app.canvas.find_withtag("readonly") == (), "切回模式 1 標記要消失"
    finally:
        _destroy(root)


def test_zoom_still_works_in_readonly_modes(monkeypatch):
    """唯讀只擋住「改狀態」,純瀏覽照舊——縮放與平移不改任何東西。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        _ready_for_mode(app)
        app.mode.set(appmod.MODE_GROUP)
        before = app.zoom

        class _Ev:
            x, y, delta = 100, 100, 120

        app.on_wheel(_Ev())
        assert app.zoom > before, "模式 2 仍然要能縮放"
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 與規則一致時不要留下「意見」
# --------------------------------------------------------------------------- #
def test_agreeing_with_the_rule_stores_no_opinion(monkeypatch, tmp_path):
    """點到跟規則一樣的狀態就存回 `None`（沒意見）,不是存一個明確的值。

    回歸測試(使用者實際遇到):規則一度靜靜失效,那段時間點過的峰全部存成
    `user_active=True`(實測 3 個檔 75 顆)。規則修好之後那 75 顆變成
    「使用者堅持要」,`top_n` 對它們完全無效——熱圖上該變灰的沒變灰。
    """
    import areas2 as _areas2
    tk, root, appmod, app = _app(monkeypatch)
    try:
        monkeypatch.setattr(_areas2, "RESULTS_DIR", str(tmp_path))
        _with_image(app)
        app.current = "/x/a.mea"
        app.peaks[0]["rule_active"] = True
        app.peaks[0]["user_active"] = None
        L.apply_effective(app.peaks)

        app._toggle_index(0)               # 關掉 —— 與規則相反,要記下來
        assert app.peaks[0]["user_active"] is False
        app._toggle_index(0)               # 再開 —— 與規則一致,回到「沒意見」
        assert app.peaks[0]["user_active"] is None, \
            "與規則一致時不可以留下意見,否則規則之後就套不到這顆峰"
        assert L.effective_active(app.peaks[0]) is True
    finally:
        _destroy(root)


def test_rescuing_a_rejected_peak_is_still_recorded(monkeypatch, tmp_path):
    """反向:與規則**不同**時一定要記下來,使用者才是最終裁決者。"""
    import areas2 as _areas2
    tk, root, appmod, app = _app(monkeypatch)
    try:
        monkeypatch.setattr(_areas2, "RESULTS_DIR", str(tmp_path))
        _with_image(app)
        app.current = "/x/a.mea"
        app.peaks[0]["rule_active"] = False
        app.peaks[0]["user_active"] = None
        L.apply_effective(app.peaks)

        app._toggle_index(0)               # 救回來
        assert app.peaks[0]["user_active"] is True
        assert L.is_rule_override(app.peaks[0]) is True
        app._toggle_index(0)               # 放掉 —— 與規則一致,回到「沒意見」
        assert app.peaks[0]["user_active"] is None
        assert L.effective_active(app.peaks[0]) is False
    finally:
        _destroy(root)


def test_a_rules_change_still_reaches_a_toggled_then_untoggled_peak(
        monkeypatch, tmp_path):
    """點兩下之後,規則仍然管得到這顆峰——這正是存 `None` 的目的。"""
    import areas2 as _areas2
    tk, root, appmod, app = _app(monkeypatch)
    try:
        monkeypatch.setattr(_areas2, "RESULTS_DIR", str(tmp_path))
        _with_image(app)
        app.current = "/x/a.mea"
        app.peaks[0]["rule_active"] = True
        app.peaks[0]["user_active"] = None
        L.apply_effective(app.peaks)
        app._toggle_index(0)
        app._toggle_index(0)               # 回到原狀

        app.peaks[0]["rule_active"] = False        # 規則改了,現在否決它
        L.apply_effective(app.peaks)
        assert L.effective_active(app.peaks[0]) is False, \
            "留下了意見的話,規則就再也關不掉這顆峰"
    finally:
        _destroy(root)


def test_status_uses_text_not_colour_glyphs(monkeypatch, tmp_path):
    """狀態要用**文字**，不能靠顏色。

    回歸測試（使用者實測回報）：原本用 🟢🟡🔴，在 Windows 的 Tk 裡退回單色字形，
    三種狀態長得一模一樣——18 個檔明明有三種不同狀態，畫面上卻看不出差別。
    ttk 的 Treeview 也沒辦法只染一格（tag 一律套用整列），所以顏色在這個元件上
    是走不通的。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        three = {appmod.READY_GREEN, appmod.READY_AMBER, appmod.READY_RED}
        assert len(three) == 3, "三種狀態必須長得不一樣"
        for label in three:
            assert label.isprintable()
            # 非 ASCII 的漢字可以，emoji 不行——後者會退回單色字形
            assert not any(ord(ch) > 0x1F000 for ch in label), \
                "%r 是 emoji，會退回單色字形而分不出來" % label
    finally:
        _destroy(root)


def test_compound_panel_shows_the_position_coordinates(monkeypatch):
    """每一列要給得出**熱圖上的座標**：Drift rel（x 軸）與 RI（y 軸）。

    回歸測試（使用者回報）：原本只有 RT s。少了 x/y 座標，使用者沒辦法把這一列
    對回圖上哪一個圈；而且熱圖的 y 軸畫的是 RI，不是保留時間——只給 RT 等於
    給了一個對不上畫面的數字。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.consolidated = _fake_consolidated()
        _ready_for_mode(app)
        app.mode.set(appmod.MODE_COMPOUND)
        app.on_mode_change()
        cols = list(app.tree_cmpd["columns"])
        assert "dr" in cols and "ri" in cols
        heads = [app.tree_cmpd.heading(c)["text"] for c in cols]
        assert "Drift rel" in heads and "RI" in heads
        v = app.tree_cmpd.item(app.tree_cmpd.get_children()[0], "values")
        # `item(..., "values")` 只回 `columns` 的值，不含 #0 那一欄——不要加位移
        assert v[cols.index("dr")] == "1.101"
        assert v[cols.index("ri")] == "841.9"
    finally:
        _destroy(root)


def test_compound_panel_survives_a_region_without_ri(monkeypatch):
    """沒有 RI 校正時要顯示「—」，不是崩掉也不是印 None。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        rows = _fake_consolidated()
        rows[0]["ri"] = None
        app.consolidated = rows
        _ready_for_mode(app)
        app.mode.set(appmod.MODE_COMPOUND)
        app.on_mode_change()
        cols = list(app.tree_cmpd["columns"])
        v = app.tree_cmpd.item(app.tree_cmpd.get_children()[0], "values")
        assert v[cols.index("ri")] == "—"
    finally:
        _destroy(root)


def test_candidate_count_is_explained_in_the_panel_note(monkeypatch):
    """`候選` 這一欄要有解釋——1 跟 38 的意義天差地遠。

    使用者問過「候選是什麼意思」。欄位標題只有兩個字，說明必須在旁邊。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.consolidated = _fake_consolidated()
        _ready_for_mode(app)
        app.mode.set(appmod.MODE_COMPOUND)
        app.on_mode_change()
        note = app.right_note.cget("text")
        assert "候選" in note
        assert "容差窗" in note, "要說清楚那是「幾個化合物對得上」，不是別的計數"
    finally:
        _destroy(root)


def test_value_columns_are_centred_and_names_are_left_aligned(monkeypatch):
    """值一律置中,只有化合物名稱靠左。

    使用者要求(兩次:先是檔案面板的相似度欄,再是共識化合物表)。名稱不置中是
    因為長度差很多——置中之後每一列的起點都不一樣,掃讀很累。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        # `column(..., "anchor")` 回的是 Tcl 的 index 物件,不是 str——要先轉字串,
        # 否則比較永遠不相等而錯誤訊息看起來像值不對(實際上型別不對)。
        def anchor(tree, col):
            return str(tree.column(col, "anchor"))

        assert anchor(app.tree_cmpd, "#0") == "w"
        for c in app.tree_cmpd["columns"]:
            assert anchor(app.tree_cmpd, c) == "center", c
        # 峰表:GC×IMS 放的是化合物名稱,同樣靠左
        for c in app.tree_peaks["columns"]:
            want = "w" if c == "gc_ims" else "center"
            assert anchor(app.tree_peaks, c) == want, c
    finally:
        _destroy(root)


def test_candidate_dialog_columns_are_centred_too(monkeypatch):
    """▶ 候選視窗原本整片靠左,與其他表格不一致。"""
    from tkinter import ttk
    tk, root, appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        p = app.peaks[0]
        p["matches"] = {"combined_matches": [{"Name": "X", "CAS": "1-1-1"}],
                        "gc_matches": [], "ims_matches": []}
        app._render_candidates(0, p)
        win = [w for w in app.root.winfo_children()
               if isinstance(w, tk.Toplevel)][-1]

        def find_tree(w):
            """遞迴找 Treeview——版面是巢狀的,固定索引一改版面就抓錯元件。"""
            if isinstance(w, ttk.Treeview):
                return w
            for kid in w.winfo_children():
                got = find_tree(kid)
                if got is not None:
                    return got
            return None

        t = find_tree(win)
        assert t is not None, "候選視窗裡應該有一個 Treeview"
        for c in t["columns"]:
            want = "w" if c == "name" else "center"
            assert str(t.column(c, "anchor")) == want, c
        win.destroy()
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 「基準」欄 —— 相似度的比較對象要看得見、而且只有一個
#
# 使用者(2026-09-07):「我選一個 base mea,你就算出這個資料夾裡每個檔對它的相似度,
# 並自動選起 >0.8 的;換一個 base 就重算重選;一次只能有一個 base。」
# --------------------------------------------------------------------------- #
def _corr_app(monkeypatch, rs):
    """建一個已經有相似度矩陣的 app。`rs` 是 4x4 的相關係數。"""
    import numpy as np
    tk, root, appmod, app = _app(monkeypatch)
    files = ["/x/a.mea", "/x/b.mea", "/x/c.mea", "/x/d.mea"]
    app.files = list(files)
    app.corr_files = list(files)
    app.corr = np.array(rs)
    return tk, root, appmod, app, files


ONE_ODD = [[1.00, 0.95, 0.93, 0.20],
           [0.95, 1.00, 0.94, 0.22],
           [0.93, 0.94, 1.00, 0.19],
           [0.20, 0.22, 0.19, 1.00]]


def test_setting_a_base_selects_everything_similar_enough(monkeypatch):
    """設基準＝自動選起相似度 ≥ 門檻的檔（含基準本身）。"""
    tk, root, appmod, app, files = _corr_app(monkeypatch, ONE_ODD)
    try:
        app.set_base("/x/a.mea")
        assert app.base == "/x/a.mea"
        assert app.group == {"/x/a.mea", "/x/b.mea", "/x/c.mea"}
        assert "/x/d.mea" not in app.group, "0.20 遠低於門檻，不該被選進來"
    finally:
        _destroy(root)


def test_only_one_base_at_a_time_and_switching_reselects(monkeypatch):
    """**一次只有一個基準**，換一個就整組重挑,不是累加。

    相似度是相對量,兩個基準就沒有一欄數字可以並排比較;而使用者要的是
    「換一個標本看看」,不是把兩個標本的組員混在一起。
    """
    tk, root, appmod, app, files = _corr_app(monkeypatch, ONE_ODD)
    try:
        app.set_base("/x/a.mea")
        assert app.group == {"/x/a.mea", "/x/b.mea", "/x/c.mea"}
        app.set_base("/x/d.mea")           # d 跟誰都不像
        assert app.base == "/x/d.mea", "基準要換過去"
        assert app.group == {"/x/d.mea"}, "整組重挑,不可以留著上一個基準的組員"
        rows = app.tree_files.get_children()
        marks = [app.tree_files.item(r, "values")[0] for r in rows]
        assert marks.count(appmod.BASE_ON) == 1, "同時只能有一個 ◉"
    finally:
        _destroy(root)


def test_the_similarity_column_is_measured_against_the_base(monkeypatch):
    """整欄數字的參照點是**基準**,不是「正在看哪個熱圖」。

    回歸測試:原本用 `self.current`,於是在模式 1 點檔案看圖會順手換掉整欄的
    比較對象——數字全變了而畫面上沒有任何東西解釋為什麼。
    """
    tk, root, appmod, app, files = _corr_app(monkeypatch, ONE_ODD)
    try:
        app.set_base("/x/a.mea")
        app.current = "/x/d.mea"           # 正在看別的檔的熱圖
        app._refresh_files()
        assert "a" in app.tree_files.heading("r")["text"]
        rows = app.tree_files.get_children()
        vals = {app.tree_files.item(r, "text").split()[0]:
                app.tree_files.item(r, "values")[3] for r in rows}
        assert vals["a.mea"] == "基準"
        assert vals["b.mea"] == "+0.95", "要對基準 a,不是對正在看的 d"
    finally:
        _destroy(root)


def test_clicking_the_base_column_sets_the_base(monkeypatch):
    """「基準」欄要真的可以點——而且在任何模式都可以。"""
    tk, root, appmod, app, files = _corr_app(monkeypatch, ONE_ODD)
    try:
        app._refresh_files()
        monkeypatch.setattr(app.tree_files, "identify_region", lambda x, y: "cell")
        monkeypatch.setattr(app.tree_files, "identify_column", lambda x: "#1")
        monkeypatch.setattr(app.tree_files, "identify_row", lambda y: "/x/b.mea")

        class _Ev:
            x = y = 5

        for mode in (appmod.MODE_HEATMAP, appmod.MODE_GROUP, appmod.MODE_COMPOUND):
            app.mode.set(mode)
            app.base = None
            assert app.on_files_click(_Ev()) == "break"
            assert app.base == "/x/b.mea", "模式 %s 下基準欄點不動" % mode
    finally:
        _destroy(root)


def test_base_without_similarity_still_takes_that_one_file(monkeypatch):
    """還沒算相似度時設基準:挑不了整組,但基準本身要進去。

    回歸測試:一度做成什麼都不選,使用者點了基準卻發現一個檔都沒選上,
    看起來像沒有反應。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.files = ["/x/a.mea", "/x/b.mea"]
        app.corr, app.corr_files = None, []
        app.set_base("/x/a.mea")
        assert app.base == "/x/a.mea"
        assert app.group == {"/x/a.mea"}
        # 只有兩個檔算不出相似度,要講出**為什麼**,不是只說「未計算」
        assert "3 個檔" in app.status.cget("text")
    finally:
        _destroy(root)


def test_setting_a_base_computes_the_similarity_itself(monkeypatch):
    """**選了基準就把相似度算出來**,不要叫使用者再去按另一顆按鈕。

    回歸測試(使用者實際回報):「我選了 base 但整欄還是『未計算』——我以為
    選了 base 每個檔的相似度就會出來。」那個期待是對的:所需的東西(每個檔的峰)
    多半早就備好了,只是還沒有人去按那一步。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        started = []

        class _T:
            def __init__(self, **kw):
                started.append(kw.get("args"))

            def start(self):
                pass

        monkeypatch.setattr(appmod.threading, "Thread", lambda *a, **kw: _T(**kw))
        monkeypatch.setattr(appmod.L, "scan_cost",
                            lambda t: {"n_need_detect": 0, "est_seconds": 0})
        app.files = ["/x/a.mea", "/x/b.mea", "/x/c.mea"]
        app.corr, app.corr_files = None, []
        app.set_base("/x/a.mea")
        assert started, "設基準之後要真的開始算相似度"
        assert started[0][0] == app.files, "要算**整個資料夾**,不是只算選取的那組"
    finally:
        _destroy(root)


def test_similarity_that_needs_peak_finding_asks_first(monkeypatch):
    """有檔還沒找峰時要先問——那是每檔約 55 秒的等待。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        from tkinter import messagebox
        asked = []
        monkeypatch.setattr(messagebox, "askyesno",
                            lambda *a, **k: asked.append(a) or False)
        started = []
        monkeypatch.setattr(appmod.threading, "Thread",
                            lambda *a, **kw: started.append(1) or _Dummy())
        monkeypatch.setattr(appmod.L, "scan_cost",
                            lambda t: {"n_need_detect": 5, "est_seconds": 275})
        app.files = ["/x/a.mea", "/x/b.mea", "/x/c.mea"]
        app.corr, app.corr_files = None, []
        app.set_base("/x/a.mea")
        assert asked, "55 秒/檔的等待一定要先問過"
        assert started == [], "使用者說不要就不可以開始"
        assert app.base == "/x/a.mea", "基準還是要設好"
    finally:
        _destroy(root)


def test_scan_completing_reselects_from_the_existing_base(monkeypatch):
    """先選基準、再掃描:算完要自動補挑一次,不必再點一次基準。"""
    import numpy as np
    tk, root, appmod, app = _app(monkeypatch)
    try:
        files = ["/x/a.mea", "/x/b.mea", "/x/c.mea", "/x/d.mea"]
        app.files = list(files)
        app.corr, app.corr_files = None, []
        app.set_base("/x/a.mea")
        assert app.group == {"/x/a.mea"}
        app.q.put(("corr", (np.array(ONE_ODD), list(files), 12, 12)))
        app._drain()
        assert app.group == {"/x/a.mea", "/x/b.mea", "/x/c.mea"}
    finally:
        _destroy(root)


def test_base_row_is_visually_marked(monkeypatch):
    """基準那一列要看得出來——一整欄數字都相對於它。"""
    tk, root, appmod, app, files = _corr_app(monkeypatch, ONE_ODD)
    try:
        app.set_base("/x/a.mea")
        rows = app.tree_files.get_children()
        assert "isbase" in app.tree_files.item(rows[0], "tags")
        assert "isbase" not in app.tree_files.item(rows[1], "tags")
        assert app.tree_files.item(rows[0], "values")[0] == appmod.BASE_ON
        assert app.tree_files.item(rows[1], "values")[0] == appmod.BASE_OFF
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 工具列瘦身:模式**就是**動作
#
# 使用者(2026-09-07):「3. consolidate 可以拿掉,點選項 3 就該開始彙整;
# 2. 掃描全部也可以省掉;沒選 base 之前選項 2、3 灰掉;久一點的計算要有進度視窗。」
# --------------------------------------------------------------------------- #
def test_the_numbered_action_buttons_are_gone(monkeypatch):
    """工具列不再有「2. 掃描全部」「3. Consolidate」——模式自己就是那兩步。

    兩套編號(工具列三顆 + 左側三個模式)描述同一條流程,使用者得自己猜哪個先。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        assert not hasattr(app, "btn_scan")
        assert not hasattr(app, "btn_cons")
    finally:
        _destroy(root)


def test_modes_2_and_3_are_disabled_until_a_base_is_chosen(monkeypatch):
    """沒有基準就沒有相似度,沒有相似度就挑不出同組——三件事有順序。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.base = None
        app.group = set()
        app._sync_mode_buttons()
        for m in (appmod.MODE_GROUP, appmod.MODE_COMPOUND):
            assert str(app._mode_rb[m].cget("state")) == "disabled", m
        assert str(app._mode_rb[appmod.MODE_HEATMAP].cget("state")) == "normal"

        app.base = "/x/a.mea"
        app.group = {"/x/a.mea", "/x/b.mea"}
        app._sync_mode_buttons()
        for m in (appmod.MODE_GROUP, appmod.MODE_COMPOUND):
            assert str(app._mode_rb[m].cget("state")) == "normal", m
    finally:
        _destroy(root)


def test_selecting_a_disabled_mode_falls_back_instead_of_showing_nothing(
        monkeypatch):
    """灰掉的模式若真的被選中,要退回模式 1 並說明,不要停在空面板上。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.base = None
        app.group = set()
        app.mode.set(appmod.MODE_COMPOUND)
        app.on_mode_change()
        assert app.mode.get() == appmod.MODE_HEATMAP
        assert "基準" in app.status.cget("text")
    finally:
        _destroy(root)


def test_entering_mode_3_consolidates_but_only_when_stale(monkeypatch):
    """切到模式 3 就彙整——**但只在過期時**,否則來回切換每次都白跑一輪。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        calls = []
        monkeypatch.setattr(app, "consolidate", lambda: calls.append(1))
        _ready_for_mode(app)
        app._mark_dirty()
        app.mode.set(appmod.MODE_COMPOUND)
        app.on_mode_change()
        assert calls == [1], "過期就要重算"

        app._cons_dirty = False
        app.consolidated = _fake_consolidated()
        app.mode.set(appmod.MODE_HEATMAP)
        app.on_mode_change()
        app.mode.set(appmod.MODE_COMPOUND)
        app.on_mode_change()
        assert calls == [1], "沒變就不要重算"
    finally:
        _destroy(root)


def test_changing_the_group_marks_the_consolidation_stale(monkeypatch):
    """改組員 = 彙整結果過期。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        _ready_for_mode(app)
        app._cons_dirty = False
        app.files = ["/x/a.mea", "/x/b.mea", "/x/c.mea"]
        monkeypatch.setattr(app.tree_files, "identify_region", lambda x, y: "cell")
        monkeypatch.setattr(app.tree_files, "identify_column", lambda x: "#2")
        monkeypatch.setattr(app.tree_files, "identify_row", lambda y: "/x/c.mea")

        class _Ev:
            x = y = 5

        app.on_files_click(_Ev())
        assert app._cons_dirty is True
    finally:
        _destroy(root)


def test_entering_mode_2_computes_similarity_when_missing(monkeypatch):
    """切到模式 2 就把相似度算出來——那個模式的用途就是看那一欄。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        started = []
        monkeypatch.setattr(app, "_start_similarity",
                            lambda reason="": started.append(reason))
        _ready_for_mode(app)
        app.corr = None
        app.mode.set(appmod.MODE_GROUP)
        app.on_mode_change()
        assert started, "模式 2 應該自己觸發相似度計算"
    finally:
        _destroy(root)


def test_a_long_job_shows_a_progress_window_that_closes_itself(monkeypatch):
    """久一點的計算要有進度視窗,跑完自己消失。

    狀態列一行字太容易被忽略——使用者按了之後不知道程式在做事還是當掉了。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app._progress_open("測試", "做事中…")
        assert app._prog_win is not None and app._prog_win.winfo_exists()
        app._progress_update("換一句話")
        assert app._prog_lbl.cget("text") == "換一句話"
        # 背景工作結束的訊息要把它收掉
        app.q.put(("busy_clear", None))
        app._drain()
        assert app._prog_win is None
    finally:
        _destroy(root)


def test_mode_2_is_named_for_what_it_does(monkeypatch):
    """「選同標本這一組」讀起來很怪(使用者回報),改成「選擇相似 mea」。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        assert "相似" in app._mode_rb[appmod.MODE_GROUP].cget("text")
        assert "同標本這一組" not in app._mode_rb[appmod.MODE_GROUP].cget("text")
    finally:
        _destroy(root)


def test_status_has_a_real_colour_dot_not_just_text(monkeypatch):
    """狀態要**有顏色**——用自己畫的圓點,不是 emoji、也不是 tag。

    走過三種做法:
      1. 🟢🟡🔴 —— Windows 的 Tk 退回單色字形,三種狀態長得一模一樣(使用者實測)
      2. tag 的前景/背景 —— 只能染**整列**,染不了單一格
      3. item 的 `image` —— ✅ 自己畫一顆 PhotoImage 圓點放在檔名前面,
         真的有顏色而且不依賴任何字形
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        seen = {}
        for state in (appmod.READY_GREEN, appmod.READY_AMBER, appmod.READY_RED):
            img = app._ready_dot(state)
            assert img, state
            # 圓心是實色、四角透明——透明才能浮在該列自己的底色上
            mid = img.width() // 2
            assert img.get(mid, mid)[:3] != (0, 0, 0), state
            assert img.transparency_get(0, 0) is True, "四角要透明"
            seen[state] = img.get(mid, mid)[:3]
        assert len(set(seen.values())) == 3, "三種狀態要是三種顏色: %r" % seen
    finally:
        _destroy(root)


def test_the_dot_images_are_kept_alive(monkeypatch):
    """圖片參照一定要留住。

    Tk 的 PhotoImage 被 Python 回收之後,畫面上那張圖會**直接消失而且不報錯**
    ——典型的「圖不見了」災情。快取在 `self._dots` 裡就是為了這個。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.files = ["/x/a.mea"]
        app._refresh_files()
        assert getattr(app, "_dots", None), "圓點要留在 app 上,不能是區域變數"
        first = app._ready_dot(appmod.READY_GREEN)
        assert app._ready_dot(appmod.READY_GREEN) is first, "同一顆點要重用"
    finally:
        _destroy(root)


def test_colour_is_not_the_only_signal(monkeypatch):
    """顏色**不是**唯一的資訊來源——文字仍然在。

    色覺差異、螢幕擷圖、黑白列印都可能讓顏色消失;而且使用者是靠擷圖回報問題的。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.files = ["/x/a.mea"]
        app._refresh_files()
        vals = app.tree_files.item(app.tree_files.get_children()[0], "values")
        assert vals[2] in (appmod.READY_GREEN, appmod.READY_AMBER,
                           appmod.READY_RED)
    finally:
        _destroy(root)

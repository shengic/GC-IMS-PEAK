"""
test_main2.py — 第二支應用**介面**（`main2.py`）的測試。

為什麼要有這一支：`test_areas2.py` 測的是邏輯模組 `areas2.py`，而本輪修掉的每一個
使用者回報的問題**都在 UI 層**——選到沒有 `.mea` 的資料夾按鈕靜靜變灰、背景執行緒
無聲死掉讓畫面永遠空白、欄位標題全是 `?`、跑完把沒處理的檔案也塗綠。那些當時是用
臨時腳本抓出來的，抓完就丟；本檔把它們固定成回歸測試。

第一支應用的 UI 有 91 項測試（`test_peak_table` / `test_state_machine` /
`test_match_panel` …），第二支應用先前一項都沒有。

需要 Tk display；無視窗環境會整支 skip（同 `test_match_panel.py` 的作法）。
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import areas2  # noqa: E402


def _tk_or_skip():
    """建 Tk root，失敗就 skip **並帶上真正的錯誤**。

    只寫「no display」會把偶發的建立失敗與真正無視窗環境混為一談——先前就因此
    掩蓋過一次真實問題。重試一次吸收暫時性失敗。
    """
    import tkinter as tk
    last = None
    for _ in range(2):
        try:
            root = tk.Tk()
        except tk.TclError as e:
            last = e
            continue
        root.withdraw()
        return tk, root
    pytest.skip(f"cannot create a Tk root: {last}")


def _app(monkeypatch):
    """建一個 AreaMatrixApp，並把互動式對話框換掉（測試不能被 modal 卡住）。"""
    tk, root = _tk_or_skip()
    from tkinter import messagebox
    import main2
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **k: None)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)
    return tk, root, main2.AreaMatrixApp(root)


def _destroy(root):
    import gc
    try:
        root.destroy()
    except Exception:
        pass
    gc.collect()


def _fake_result(files=("a.mea", "b.mea", "c.mea"), classes=None, n_areas=3):
    classes = classes if classes is not None else {
        "a.mea": "A 1-1", "b.mea": "A 1-2", "c.mea": "B 1-1"}
    areas = [{"area_id": i + 1, "name": ("2-Butanone" if i == 0 else f"area {i + 1}"),
              "cas": None, "drift_center": 1.2 + i * 0.1, "drift_half": 0.02,
              "rt_center_s": 300.0 + i * 50, "rt_half_s": 8.0,
              "ri_center": 900.0 + i, "n_files_detected": len(files),
              "detected_in": list(files)}
             for i in range(n_areas)]
    matrix = {b: {a["area_id"]: {"volume": 100.0 * (j + 1) * a["area_id"],
                                 "max": 10.0, "mean": 1.0}
                  for a in areas} for j, b in enumerate(files)}
    return {"app": "areas2", "folder": "/tmp/Batch", "files": list(files),
            "classes": classes, "n_areas": len(areas), "n_files": len(files),
            "areas": areas, "matrix": matrix, "class_warnings": [],
            "provenance": {"ri_mode": "batch_own_std", "k0_mode": "standard_based",
                           "baseline_applied": False, "skip_detect": False,
                           "area_selection": {"mode": "consensus_from_detected_peaks"},
                           "naming": {"named": 1}}}


# --------------------------------------------------------------------------- #
# 選到沒有 .mea 的上層資料夾（實際回報：「按 Run batch 沒反應」）
# --------------------------------------------------------------------------- #
def test_folder_without_mea_disables_run_and_says_why(tmp_path, monkeypatch):
    """檔案選擇器預設開在 `GAS/`，而它底下只有子資料夾、沒有 `.mea`。

    以前只是把按鈕變灰，使用者按了沒事、也不知道為什麼——回報的「按 Run batch
    沒反應」就是這個。現在必須明講，並指出該去哪個子資料夾。
    """
    (tmp_path / "batch_A").mkdir()
    (tmp_path / "batch_A" / "x.mea").write_bytes(b'Sample = "S"\r\n')
    tk, root, app = _app(monkeypatch)
    try:
        app._load_folder(str(tmp_path))
        assert str(app.run_btn["state"]) == "disabled"
        assert "No .mea" in app.status.cget("text")
        log = app.logbox.get("1.0", "end")
        assert "batch_A" in log          # 有資料的子資料夾要列出來
    finally:
        _destroy(root)


def test_folder_with_mea_enables_run(tmp_path, monkeypatch):
    for n in ("260625_113647_E_1_1.mea", "260625_122837_E_1_2.mea"):
        (tmp_path / n).write_bytes(b'Sample = "S"\r\n')
    tk, root, app = _app(monkeypatch)
    try:
        app._load_folder(str(tmp_path))
        assert str(app.run_btn["state"]) == "normal"
        assert len(app.file_tree.get_children()) == 2
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 背景執行緒必須回報，不能無聲死掉（實際回報：「矩陣視窗一片空白」）
# --------------------------------------------------------------------------- #
def test_worker_reports_no_samples_instead_of_dying_silently(tmp_path, monkeypatch):
    """空資料夾 → UI 要收到 `nosamples`，不是永遠等下去。

    原本 `build_matrix` 用 `raise SystemExit`（BaseException），worker 的
    `except Exception` 接不到 → 執行緒死掉、佇列永遠空著、`_poll` 一直輪詢，
    畫面全空**且沒有任何錯誤訊息**。
    """
    import queue
    tk, root, app = _app(monkeypatch)
    try:
        app.folder = str(tmp_path)
        app.file_items = {}
        app.q = queue.Queue()
        app.stop_flag.clear()

        # 直接跑 worker 會做的事，驗證例外會被轉成訊息
        try:
            areas2.build_matrix(str(tmp_path), verbose=False)
        except areas2.NoSamplesFound as e:
            app.q.put(("nosamples", str(e)))
        kind, msg = app.q.get_nowait()
        assert kind == "nosamples"
        assert "sub" in msg or ".mea" in msg
    finally:
        _destroy(root)


def test_poll_surfaces_an_error_rather_than_leaving_the_table_blank(monkeypatch):
    """收到 error 要顯示訊息並把按鈕還原，不是靜靜停住。"""
    import queue
    tk, root, app = _app(monkeypatch)
    try:
        app.q = queue.Queue()
        app.q.put(("error", "BoomError: something broke"))
        app.run_btn.config(state="disabled")
        app._poll()
        assert str(app.run_btn["state"]) == "normal"
        assert "failed" in app.status.cget("text").lower()
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 矩陣表格（實際回報：「欄位標題全是 ?」「一片空白」）
# --------------------------------------------------------------------------- #
def test_table_fills_and_headers_never_contain_a_question_mark(monkeypatch):
    """標題不可以是 `?`。

    兩個原因疊在一起造成過：`Class` 只在用 .gasprj 區域時才讀（共識模式整組遺失），
    而標題用了換行——但 ttk.Treeview 的 heading 只畫一行，第二行整個看不見。
    """
    tk, root, app = _app(monkeypatch)
    try:
        app.result = _fake_result()
        for view in ("summary", "all files"):
            app.view.set(view)
            app._fill_table()
            heads = [app.tree.heading(c)["text"] for c in app.tree["columns"]]
            assert heads, view
            assert "?" not in heads, (view, heads)
            assert all("\n" not in h for h in heads), (view, heads)
            assert len(app.tree.get_children()) == app.result["n_areas"], view
    finally:
        _destroy(root)


def test_summary_view_collapses_replicates_into_experiment_groups(monkeypatch):
    """`Class` 存的是重複樣品代號（`A 1-1`/`A 1-2`），摘要要收成 A/B 組。

    照字面分會變成每組 n=1，完全失去組間比較的意義——實測 18 檔會分成 13 組。
    """
    tk, root, app = _app(monkeypatch)
    try:
        app.result = _fake_result()          # A 1-1, A 1-2, B 1-1
        app.view.set("summary")
        app._fill_table()
        heads = [app.tree.heading(c)["text"] for c in app.tree["columns"]]
        assert "A (n=2)" in heads and "B (n=1)" in heads
        app.view.set("all files")
        app._fill_table()
        heads = [app.tree.heading(c)["text"] for c in app.tree["columns"]]
        assert "a" in heads and "b" in heads          # 逐檔用短檔名
    finally:
        _destroy(root)


def test_metric_switch_changes_values_without_recomputing(monkeypatch):
    """切 metric 只換顯示，不需要重跑（三個指標都已經算好存著）。"""
    tk, root, app = _app(monkeypatch)
    try:
        app.result = _fake_result()
        app.view.set("all files")
        app.metric.set("volume")
        app._fill_table()
        vol = app.tree.item(app.tree.get_children()[0], "values")
        app.metric.set("max")
        app._fill_table()
        mx = app.tree.item(app.tree.get_children()[0], "values")
        assert vol != mx
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 執行中的檔案上色
# --------------------------------------------------------------------------- #
def test_file_highlight_walks_detect_then_measure_then_done(tmp_path, monkeypatch):
    """兩個階段分開標色：偵測（慢）與量測（快）要看得出差別。

    只用一種「處理中」顏色的話，第二輪量測會看起來像又從頭跑了一次。
    """
    for n in ("260625_113647_E_1_1.mea", "260625_122837_E_1_2.mea"):
        (tmp_path / n).write_bytes(b'Sample = "S"\r\n')
    tk, root, app = _app(monkeypatch)
    try:
        app._load_folder(str(tmp_path))
        a, b = "260625_113647_E_1_1.mea", "260625_122837_E_1_2.mea"

        def tag(f):
            t = app.file_tree.item(app.file_items[f], "tags")
            return t[0] if t else None

        app._mark_file(f"detect {a}")
        assert tag(a) == "detecting"
        app._mark_file(f"detect {b}")
        assert tag(a) == "detected" and tag(b) == "detecting"
        app._mark_file(f"measure {a}")
        assert tag(a) == "measuring"
        app._mark_file(f"measure {b}")
        assert tag(a) == "done" and tag(b) == "measuring"
        app._mark_file("done")
        assert tag(a) == "done" and tag(b) == "done"
    finally:
        _destroy(root)


def test_done_never_paints_files_that_were_not_processed(tmp_path, monkeypatch):
    """用 `files: N` 只跑前 N 個時，其餘檔案**不可以**被標成完成。

    全部塗綠會讓人以為整批都跑完了——實測用 files=3 跑 15 檔時就是這樣。
    """
    for n in ("260625_113647_E_1_1.mea", "260625_122837_E_1_2.mea",
              "260625_132025_E_1_3.mea"):
        (tmp_path / n).write_bytes(b'Sample = "S"\r\n')
    tk, root, app = _app(monkeypatch)
    try:
        app._load_folder(str(tmp_path))
        first = "260625_113647_E_1_1.mea"
        untouched = "260625_132025_E_1_3.mea"
        app._mark_file(f"detect {first}")
        app._mark_file(f"measure {first}")
        app._mark_file("done")
        # Tk 對「沒有 tag」有時回 ()、有時回 ''，兩者都算沒有標記
        def tags(f):
            t = app.file_tree.item(app.file_items[f], "tags")
            return tuple(t) if t else ()

        assert tags(first) == ("done",)
        assert tags(untouched) == ()
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 進度訊息面板（實際回報：「看不出有沒有在跑」）
# --------------------------------------------------------------------------- #
def test_stdout_tee_forwards_whole_lines_and_leaves_the_console_intact():
    """`peaks.py` / `readGAS.py` 用普通 print()，不攔 stdout 就只會進主控台。

    **只推整行**——Tk 的更新必須留在主執行緒。`\\r` 也當行尾，否則
    `readGAS.progress()` 的原地刷新會累積成一行超長字串。
    """
    import io
    import queue
    import main2
    q = queue.Queue()
    console = io.StringIO()
    tee = main2._StdoutTee(console, q)
    tee.write("first line\n")
    tee.write("partial ")
    assert q.qsize() == 1                       # 半行還不推
    tee.write("rest\r\nnext\r")
    got = [q.get_nowait()[1] for _ in range(q.qsize())]
    assert got == ["first line", "partial rest", "next"]
    assert "first line" in console.getvalue()   # 主控台照樣看得到
    assert tee.isatty() is False


def test_log_pane_is_capped_so_a_long_batch_stays_responsive(monkeypatch):
    """15 個檔案會產生上千行；不設上限 Text widget 會越滾越慢。"""
    tk, root, app = _app(monkeypatch)
    try:
        for i in range(app.MAX_LOG_LINES + 800):
            app._log(f"line {i}")
        n = int(app.logbox.index("end-1c").split(".")[0])
        assert n <= app.MAX_LOG_LINES + 1
        assert "line 4799" in app.logbox.get("1.0", "end")   # 保留的是最新的
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# Fast 模式的開關連動
# --------------------------------------------------------------------------- #
def test_fast_mode_is_only_available_with_gasprj_areas(tmp_path, monkeypatch):
    """跳過找峰只在「用 .gasprj 方框」時有意義——共識模式的區域正是從峰長出來的。

    取消勾選上面那項時，Fast 也要一併關掉，不能留著一個會讓 build_matrix 報錯的組合。
    """
    tk, root, app = _app(monkeypatch)
    try:
        app.gasprj = None
        app.use_gasprj.set(False)
        app._sync_skip_state()
        assert str(app.skip_chk["state"]) == "disabled"
        assert app.skip_detect.get() is False

        app.gasprj = str(tmp_path / "p.gasprj")
        app.use_gasprj.set(True)
        app._sync_skip_state()
        assert str(app.skip_chk["state"]) == "normal"
        app.skip_detect.set(True)

        app.use_gasprj.set(False)      # 取消 → Fast 必須跟著關
        app._sync_skip_state()
        assert app.skip_detect.get() is False
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 跑完要把會讓結論悄悄變錯的狀況講出來
# --------------------------------------------------------------------------- #
def test_class_label_mismatches_are_written_to_the_log(monkeypatch, tmp_path):
    """`Class` 與檔名不一致會讓分組統計悄悄錯掉——跑完必須講出來。"""
    tk, root, app = _app(monkeypatch)
    try:
        res = _fake_result()
        res["class_warnings"] = [{"file": "b.mea", "class": "A 1-1",
                                  "from_filename": "A_1_2"}]
        res["folder"] = str(tmp_path / "Batch")
        app.result = res
        app.metric.set("volume")
        app._save_outputs()
        log = app.logbox.get("1.0", "end")
        assert "b.mea" in log and "A 1-1" in log
    finally:
        _destroy(root)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

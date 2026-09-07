"""test_main3.py — `main3.py`(第三支應用的進入點)與**三支並存**的回歸測試。

這一檔守的是兩件事:

1. **`python main3.py` 真的跑得起來。** 這支應用住在套件裡,直接跑
   `python compound_consensus/app.py` 會把子資料夾放進 `sys.path[0]`,根目錄的
   `peaks` / `calibration` / `match` 就 import 不到——而 Python 只會說
   `No module named 'peaks'`,看不出真正的原因是啟動方式。`main3.py` 存在就是為了
   讓使用者不必知道這件事。

2. **加了第三支不會弄壞前兩支。** 使用者的要求原話:「任何你改或加的程式碼,
   請確保既有的程式仍可運作(我還是可以跑 main.py、main2.py 和其他 py)」。
"""
import os
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

PY = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable


def _run(code, cwd=PROJECT_ROOT):
    """在**乾淨的子行程**裡跑一段程式碼,回傳 CompletedProcess。

    用子行程而不是直接 import:啟動方式的問題全部出在 `sys.path[0]`,而那在
    測試行程裡早就被 pytest 設好了,同一個行程裡永遠複現不出來。
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # `text=True` 用**父行程**的地區設定解碼（這台機器是 cp950），子行程卻照
    # `PYTHONIOENCODING` 寫 UTF-8——中文一出現就 UnicodeDecodeError，而且錯在
    # 讀取執行緒裡，看起來與被測程式無關。兩邊都指定 utf-8 才對得上。
    return subprocess.run([PY, "-c", code], cwd=cwd, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180)


# --------------------------------------------------------------------------- #
# 進入點
# --------------------------------------------------------------------------- #
def test_main3_exists_at_the_project_root():
    """進入點要在根目錄,與 `main.py` / `main2.py` 並排。"""
    assert os.path.exists(os.path.join(PROJECT_ROOT, "main3.py"))


def test_main3_imports_without_opening_a_window():
    """import 不可以有副作用——`main()` 只在 `__main__` 時才呼叫。

    寫成 import 就開視窗的話,任何 `import main3`(包括這一份測試)都會卡在
    `mainloop()` 上不回來。
    """
    r = _run("import main3; print('OK', callable(main3.main))")
    assert r.returncode == 0, r.stderr
    assert "OK True" in r.stdout


def test_main3_makes_the_root_modules_importable():
    """經由 `main3` 進來之後,根目錄的模組要 import 得到。

    這正是 `python compound_consensus/app.py` 會壞掉的那一條:
    `peaks` / `calibration` / `match` 都住在根目錄。
    """
    r = _run("import main3, peaks, calibration, match, library; print('OK')")
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_running_the_package_file_directly_is_the_broken_path():
    """`python compound_consensus/app.py` 會失敗——這正是 `main3.py` 要取代的。

    這一條是**說明性**的:它把「為什麼需要 main3.py」釘成可執行的事實。哪天
    import 結構變了、直接跑也能通,這條會失敗並提醒我們重新評估這段文件。
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([PY, os.path.join("compound_consensus", "app.py")],
                       cwd=PROJECT_ROOT, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=180)
    assert r.returncode != 0
    assert "No module named" in r.stderr, r.stderr[-800:]


def test_main3_works_from_another_working_directory(tmp_path):
    """從別的工作目錄呼叫也要成立(捷徑、排程、打包後的 exe)。"""
    r = _run("import runpy, sys; sys.argv=['main3.py']; "
             "import importlib.util as u; "
             "spec=u.spec_from_file_location('m3', r'%s'); "
             "m=u.module_from_spec(spec); spec.loader.exec_module(m); "
             "print('OK', callable(m.main))"
             % os.path.join(PROJECT_ROOT, "main3.py"),
             cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "OK True" in r.stdout


# --------------------------------------------------------------------------- #
# 三支並存 —— 加第三支不可以弄壞前兩支
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mod", ["main", "main2", "areas2", "peaks", "calibration",
                                 "identify", "match", "library", "rules", "rip"])
def test_existing_modules_still_import(mod):
    """既有模組全部照樣 import 得動。"""
    r = _run("import %s; print('OK')" % mod)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_third_app_does_not_modify_the_first_two():
    """第三支只**呼叫**既有模組,不改它們(隔離規則 1)。

    以 git 是否有未提交的改動為準太脆弱,改成檢查第三支沒有在既有模組上
    monkeypatch:import 完第三支之後,`areas2.detect_one` 等仍是原本的函式。
    """
    r = _run(
        "import areas2, peaks, calibration, match\n"
        "before = (areas2.detect_one, peaks.load_surface, match.match_all,\n"
        "          calibration.attach_ri)\n"
        "import compound_consensus.app, compound_consensus.logic\n"
        "after = (areas2.detect_one, peaks.load_surface, match.match_all,\n"
        "         calibration.attach_ri)\n"
        "print('OK' if before == after else 'PATCHED')")
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout, "第三支不可以就地換掉既有模組的函式"


def test_the_three_apps_have_separate_entry_points():
    """三個進入點各自獨立,`main3` 不是 `main` 的變體。"""
    r = _run("import main3, main, main2; "
             "print(main3.__file__ != main.__file__ != main2.__file__)")
    assert r.returncode == 0, r.stderr
    assert "True" in r.stdout


def test_state_files_of_the_three_apps_do_not_collide(tmp_path, monkeypatch):
    """第三支的選取狀態寫 `_peaks_state3.json`,不覆蓋第一支的。

    回歸測試(隔離規則 2):寫進 `<name>_peaks_state.json` 會無聲改掉使用者在
    第一支應用裡的選取。
    """
    import areas2
    from compound_consensus import state as state_mod
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(tmp_path))
    p = state_mod.state_path("/x/SAMPLE.mea")
    assert p.endswith("SAMPLE_peaks_state3.json")
    assert not p.endswith("SAMPLE_peaks_state.json")


# --------------------------------------------------------------------------- #
# Ctrl+C 不該看起來像當掉
# --------------------------------------------------------------------------- #
def test_ctrl_c_exits_quietly_instead_of_dumping_a_traceback():
    """`mainloop()` 被 Ctrl+C 打斷時要安靜收掉,並說一句「已中斷」。

    回歸測試(使用者實際回報):Tk 不會接住 KeyboardInterrupt,Python 會吐一整段
    以 `self.tk.mainloop(n)` 收尾的紅字 traceback——看起來像程式當掉,但其實只是
    使用者自己按了 Ctrl+C,而且做完的東西都已經寫出去了。
    """
    r = _run(
        "import tkinter as tk\n"
        "def boom(*a, **k):\n"
        "    raise KeyboardInterrupt\n"
        "tk.Tk.mainloop = boom\n"
        "import compound_consensus.app as A\n"
        "A.ConsensusApp = lambda root: None\n"
        "A.main()\n"
        "print('CLEAN-EXIT')")
    assert r.returncode == 0, r.stderr
    assert "CLEAN-EXIT" in r.stdout
    assert "已中斷" in r.stdout
    assert "Traceback" not in r.stderr, r.stderr[-500:]


def test_preprocess_ctrl_c_says_finished_work_is_kept():
    """預處理被 Ctrl+C 時要講明**已完成的檔不會白費**。

    每個檔各自寫出自己的 `.npz` / `_peaks2.json`,中斷只丟掉正在處理的那一個。
    不講的話使用者會以為整批都要重來。
    """
    r = _run(
        "import tkinter as tk\n"
        "def boom(*a, **k):\n"
        "    raise KeyboardInterrupt\n"
        "tk.Tk.mainloop = boom\n"
        "import compound_consensus.preprocess as P\n"
        "P.gui()\n"
        "print('CLEAN-EXIT')")
    assert r.returncode == 0, r.stderr
    assert "CLEAN-EXIT" in r.stdout
    assert "不會白費" in r.stdout or "從沒做的地方繼續" in r.stdout
    assert "Traceback" not in r.stderr, r.stderr[-500:]

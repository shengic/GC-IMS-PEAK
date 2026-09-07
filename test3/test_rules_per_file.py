"""test_rules_per_file.py — 逐檔規則在**介面上**的行為。

`rules_store` 本身的測試在 `test_rules_store.py`；這一份測的是接線：面板改的是誰的
規則、「套用到選取的檔」有沒有真的寫給整組、彙整與掃描有沒有逐檔取規則。

使用者的要求（2026-09-07）原話：
「改規則預設套用到所有 mea，但使用者仍可改單一 mea 的規則；main3 啟動時檢查 json
有沒有該 mea 的專屬規則，有就用，沒有就跟預設；另外在規則面板放一顆 apply 按鈕，
把某個 mea 的規則套到所有選取的檔。」
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import areas2  # noqa: E402
from compound_consensus import rules_store as RS  # noqa: E402

from .test_app import _app, _destroy, _with_image  # noqa: E402


@pytest.fixture()
def results(tmp_path, monkeypatch):
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(tmp_path))
    return tmp_path


def _top_n(config):
    for e in config:
        if e.get("rule_number") == "R002":
            return e.get("params", {}).get("top_n")
    return None


# --------------------------------------------------------------------------- #
# 面板改的是誰的規則
# --------------------------------------------------------------------------- #
def test_editing_applies_to_every_file_by_default(monkeypatch, results):
    """**改規則預設套用到所有 .mea**，不是只有目前這一個。

    回歸測試（使用者實際回報）：一開始做成「只改目前這個檔」，於是調一次參數要
    逐檔重調 18 次。使用者的要求原話是「by default it applies to all the mea files」。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.current = "/x/a.mea"
        app._load_rules_for_current()
        app.open_rules()
        assert app.rules_all.get() is True, "套用範圍預設就是「全部檔案」"
        app._rule_vars["R002"]["on"].set(True)
        app._rule_vars["R002"]["params"]["top_n"]["var"].set("10")
        app._on_rules_changed()

        assert _top_n(app.rules_config) == 10, "預設要跟著變"
        # 別的檔沒有自訂，所以它們解析出來的規則也要是新的
        assert _top_n(app._rules_for("/x/other.mea")) == 10
        assert not RS.has_override("/x/a.mea"), \
            "範圍是「全部」時不該偷偷把目前這個檔釘成自訂"
    finally:
        _destroy(root)


def test_scoping_to_one_file_leaves_the_default_alone(monkeypatch, results):
    """切成「只有目前這個檔」時才寫逐檔覆寫，預設一個字都不動。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.current = "/x/a.mea"
        app._load_rules_for_current()
        app.open_rules()
        app.rules_all.set(False)                 # 只有這個檔
        app._rule_vars["R002"]["on"].set(True)
        app._rule_vars["R002"]["params"]["top_n"]["var"].set("10")
        app._on_rules_changed()

        assert _top_n(app.cur_rules) == 10
        assert _top_n(app.rules_config) == 0, "預設一個字都不該被動到"
        assert RS.has_override("/x/a.mea")
        assert _top_n(app._rules_for("/x/other.mea")) == 0, "別的檔不受影響"
    finally:
        _destroy(root)


def test_a_customised_file_is_not_dragged_along_by_default_changes(
        monkeypatch, results):
    """已經自訂的檔不跟著預設走——那正是「自訂」的意思。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        RS.save_override("/x/pinned.mea",
                         [{"rule_number": "R002", "enabled": True,
                           "params": {"top_n": 3}}])
        app.files = ["/x/a.mea", "/x/pinned.mea"]
        app.current = None
        app.open_rules()
        app._rule_vars["R002"]["params"]["top_n"]["var"].set("10")
        app._on_rules_changed()
        assert _top_n(app._rules_for("/x/a.mea")) == 10
        assert _top_n(app._rules_for("/x/pinned.mea")) == 3
    finally:
        _destroy(root)


def test_changing_the_default_does_not_touch_the_shared_config(
        monkeypatch, results):
    """改預設**不碰 `rules_config.json`**，也不寫任何檔。

    那一份是三支共用的：在第三支調參數不該讓 `main.py` 下次打開時峰數不一樣。
    要讓三支一起改，得按「存成共用預設」並確認。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        wrote = []
        monkeypatch.setattr(appmod.rules_mod, "save_config",
                            lambda path, cfg: wrote.append(path))
        app.current = None
        app.open_rules()
        app._rule_vars["R002"]["params"]["top_n"]["var"].set("5")
        app._on_rules_changed()
        assert _top_n(app.rules_config) == 5
        assert wrote == [], "不可以自動寫回共用的 rules_config.json"
        assert os.listdir(results) == [], "規則不寫檔"
        assert not RS.has_override("/x/anything.mea")
    finally:
        _destroy(root)


def test_restart_resets_the_default_but_keeps_customised_files(
        monkeypatch, results):
    """**重開一次：預設歸零，自訂過的檔留著。**

    兩層的壽命刻意不同（使用者 2026-09-07 的決定，經過一次反覆）：

    - 改「全部檔案」是**這一輪想怎麼看**——調 `top_n` 看看少幾顆峰長什麼樣，
      下次打開不該還黏著。使用者原話：「top_n=10 不是預設」。
    - 把某個檔**特別**調成不一樣是對那個檔的**判斷**，重開一次不該要人重做。
      使用者原話：「once they are customized their own rule will be overwritten」。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.current = "/x/a.mea"
        app._load_rules_for_current()
        app.open_rules()
        app._rule_vars["R002"]["params"]["top_n"]["var"].set("6")
        app._on_rules_changed()                      # 範圍＝全部檔案
        app.rules_all.set(False)                     # 改成只有這個檔
        app._rebuild_rules_panel()
        app._rule_vars["R002"]["params"]["top_n"]["var"].set("9")
        app._on_rules_changed()
        assert _top_n(app.rules_config) == 6
        assert RS.has_override("/x/a.mea")
    finally:
        _destroy(root)

    tk, root, appmod, app2 = _app(monkeypatch)       # 重開
    try:
        assert _top_n(app2.rules_config) == 0, "預設要回到 rules_config.json"
        assert app2.rules_src == "shared"
        assert RS.has_override("/x/a.mea"), "自訂過的檔要留著"
        assert _top_n(app2._rules_for("/x/a.mea")) == 9, "而且用的是它自己那份"
        assert _top_n(app2._rules_for("/x/other.mea")) == 0, "沒自訂的回到預設"
    finally:
        _destroy(root)


def test_group_selection_also_starts_empty(monkeypatch, results):
    """同標本分組同樣不跨執行保留——規則的行為要跟它一致。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.group = {"/x/a.mea", "/x/b.mea"}
    finally:
        _destroy(root)
    tk, root, appmod, app2 = _app(monkeypatch)
    try:
        assert app2.group == set()
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 啟動時的解析：有專屬的就用，沒有就跟預設
# --------------------------------------------------------------------------- #
def test_rules_for_prefers_the_files_own_override(monkeypatch, results):
    tk, root, appmod, app = _app(monkeypatch)
    try:
        RS.save_override("/x/custom.mea",
                         [{"rule_number": "R002", "enabled": True,
                           "params": {"top_n": 42}}])
        assert _top_n(app._rules_for("/x/custom.mea")) == 42
        assert _top_n(app._rules_for("/x/plain.mea")) == 0, "沒有覆寫就跟預設"
    finally:
        _destroy(root)


def test_rules_for_the_current_file_comes_from_the_panel_not_the_disk(
        monkeypatch, results):
    """正在編輯的那個檔要用**面板上**那一份。

    否則使用者改了參數、按下「重新產生」，跑的還是磁碟上的舊值。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.current = "/x/a.mea"
        app._load_rules_for_current()
        for e in app.cur_rules:
            if e["rule_number"] == "R002":
                e["params"]["top_n"] = 99
        assert _top_n(app._rules_for("/x/a.mea")) == 99
    finally:
        _destroy(root)


def test_two_files_do_not_share_one_config_object(monkeypatch, results):
    """改 A 檔的參數不可以動到 B 檔——共用同一個 dict 就會。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        a = app._rules_for("/x/a.mea")
        b = app._rules_for("/x/b.mea")
        for e in a:
            if e["rule_number"] == "R002":
                e["params"]["top_n"] = 77
        assert _top_n(b) == 0
        assert _top_n(app.rules_config) == 0, "連預設本身都不可以被改到"
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 「套用到選取的檔」
# --------------------------------------------------------------------------- #
def test_apply_pushes_the_current_rules_to_every_selected_file(
        monkeypatch, results):
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.files = ["/x/a.mea", "/x/b.mea", "/x/c.mea"]
        app.group = set(app.files)
        app.current = "/x/a.mea"
        app._load_rules_for_current()
        for e in app.cur_rules:
            if e["rule_number"] == "R002":
                e["params"]["top_n"] = 12
        app._apply_rules_to_group()
        for m in app.files:
            assert _top_n(RS.load_override(m)) == 12, m
    finally:
        _destroy(root)


def test_apply_leaves_unselected_files_alone(monkeypatch, results):
    """只套到**選取的**檔。沒勾的不動——那是使用者沒有表示意見的檔。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.files = ["/x/a.mea", "/x/b.mea", "/x/out.mea"]
        app.group = {"/x/a.mea", "/x/b.mea"}
        app.current = "/x/a.mea"
        app._load_rules_for_current()
        app._apply_rules_to_group()
        assert RS.has_override("/x/b.mea")
        assert not RS.has_override("/x/out.mea"), "沒勾的檔不該被改"
    finally:
        _destroy(root)


def test_apply_does_nothing_without_a_selection(monkeypatch, results):
    """一個檔都沒勾就不該默默寫出任何東西。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.files = ["/x/a.mea"]
        app.group = set()
        app.current = "/x/a.mea"
        app._load_rules_for_current()
        app._apply_rules_to_group()
        assert os.listdir(results) == []
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 回頭路與標示
# --------------------------------------------------------------------------- #
def test_reset_puts_the_file_back_on_the_default(monkeypatch, results):
    """一定要有回頭路，否則一個檔自訂過就再也回不去預設。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        app.files = ["/x/a.mea"]
        app.current = "/x/a.mea"
        RS.save_override("/x/a.mea", [{"rule_number": "R002", "enabled": True,
                                       "params": {"top_n": 42}}])
        app._load_rules_for_current()
        assert app.cur_rules_src == "custom"
        app._reset_rules_for_current()
        assert not RS.has_override("/x/a.mea")
        assert app.cur_rules_src == "default"
        assert _top_n(app._rules_for("/x/a.mea")) == 0
    finally:
        _destroy(root)


def test_files_with_custom_rules_are_marked_in_the_list(monkeypatch, results):
    """有自訂規則的檔要標出來。

    兩個檔看起來同一組參數卻給出不同的峰數是完全可能的——不標的話那看起來就是
    程式壞了。同 `ri_mode` / `k0_mode` 的 provenance 原則。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        RS.save_override("/x/b.mea", [{"rule_number": "R002", "enabled": True,
                                       "params": {"top_n": 3}}])
        app.files = ["/x/a.mea", "/x/b.mea"]
        app._refresh_files()
        rows = app.tree_files.get_children()
        assert "[自訂規則]" not in app.tree_files.item(rows[0], "text")
        assert "[自訂規則]" in app.tree_files.item(rows[1], "text")
    finally:
        _destroy(root)


def test_saving_the_default_does_not_override_files_that_customised(
        monkeypatch, results, tmp_path):
    """存成預設只影響**還沒自訂**的檔，而且要講出有幾個檔不受影響。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        monkeypatch.setattr(appmod.rules_mod, "save_config",
                            lambda path, cfg: None)
        RS.save_override("/x/b.mea", [{"rule_number": "R002", "enabled": True,
                                       "params": {"top_n": 3}}])
        app.files = ["/x/a.mea", "/x/b.mea"]
        app.current = None
        app._save_rules()
        assert _top_n(RS.load_override("/x/b.mea")) == 3, "自訂的檔不受影響"
        assert "1 個檔" in app.status.cget("text"), app.status.cget("text")
    finally:
        _destroy(root)


# --------------------------------------------------------------------------- #
# 下游要真的用到逐檔規則
# --------------------------------------------------------------------------- #
def _stub_consolidation(monkeypatch, appmod, tmp_path, captured):
    def fake_regions(paths, rc, **kw):
        captured.update(kw)
        return [], {p: [] for p in paths}, {}

    monkeypatch.setattr(appmod.L, "consensus_regions", fake_regions)
    monkeypatch.setattr(appmod.calibration, "_read_header_lite", lambda p: {})
    monkeypatch.setattr(appmod.calibration, "resolve_calibrations_cached",
                        lambda *a, **k: {"ri": (None, "none", None)})
    monkeypatch.setattr(appmod.library, "resolve_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(appmod.identify, "load_libraries",
                        lambda *a, **k: ([], [], {}))
    monkeypatch.setattr(appmod.L, "rank_areas", lambda *a, **k: [])


def test_consolidation_resolves_rules_per_file(monkeypatch, results, tmp_path):
    """彙整要逐檔取規則。沒傳 `rules_for` 的話自訂規則在彙整時被無聲忽略。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        captured = {}
        _stub_consolidation(monkeypatch, appmod, tmp_path, captured)
        app._cons_worker(["/x/a.mea", "/x/b.mea"])
        assert captured.get("rules_for") == app._rules_for
    finally:
        _destroy(root)


def test_the_resolver_actually_returns_each_files_own_rules(monkeypatch, results):
    """把解析器真的叫一次——傳對了函式但解析錯了一樣沒用。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        RS.save_override("/x/b.mea", [{"rule_number": "R002", "enabled": True,
                                       "params": {"top_n": 8}}])
        assert _top_n(app._rules_for("/x/a.mea")) == 0
        assert _top_n(app._rules_for("/x/b.mea")) == 8
    finally:
        _destroy(root)


def test_opening_the_panel_does_not_pin_the_file(monkeypatch, results):
    """**光是打開規則面板不可以把這個檔釘成自訂。**

    回歸測試（使用者實際回報）：清單上前三個檔冒出自訂標記，而使用者什麼都沒改。
    原因是 `open_rules()` 最後會呼叫 `_on_rules_changed()` 一次，而那一次無條件
    寫出覆寫——「看一眼」等於「改過了」。現在只有內容真的變了才寫檔。
    """
    tk, root, appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.current = "/x/a.mea"
        app._load_rules_for_current()
        app.rules_all.set(False)              # 最容易誤寫的情況：範圍是單檔
        app.open_rules()                      # 只是打開，不動任何東西
        assert not RS.has_override("/x/a.mea"), "看一眼就被釘成自訂"
        assert app.cur_rules_src == "default"
    finally:
        _destroy(root)


def test_a_no_op_edit_does_not_write_anything(monkeypatch, results):
    """把值改成跟原本一樣也不該寫檔。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.current = "/x/a.mea"
        app._load_rules_for_current()
        app.rules_all.set(False)
        app.open_rules()
        cur = app._rule_vars["R002"]["params"]["top_n"]["var"].get()
        app._rule_vars["R002"]["params"]["top_n"]["var"].set(cur)   # 原值
        app._on_rules_changed()
        assert not RS.has_override("/x/a.mea")
    finally:
        _destroy(root)


def test_a_real_edit_still_writes(monkeypatch, results):
    """反向：真的改了就一定要落地，否則切換檔案就沒了。"""
    tk, root, appmod, app = _app(monkeypatch)
    try:
        _with_image(app)
        app.current = "/x/a.mea"
        app._load_rules_for_current()
        app.rules_all.set(False)
        app.open_rules()
        app._rule_vars["R002"]["params"]["top_n"]["var"].set("7")
        app._on_rules_changed()
        assert RS.has_override("/x/a.mea")
        assert _top_n(RS.load_override("/x/a.mea")) == 7
    finally:
        _destroy(root)

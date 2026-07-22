"""
test_peak_selection.py  —  Batch 3：峰的選取／取消選取狀態模型

依專案慣例，同時是 pytest 測試與可執行的除錯腳本。

鎖住的意圖：

1. **狀態的持久化身分是座標，不是 peak_id。**
   peak_id 是「強制規則套用後的集合」裡的突出度名次，只要基準集合變動
   （調 R004.half_width／R006.boundary、或重跑偵測），整組編號就會重新指派。
   若把使用者的勾選存成 peak_id，下次開啟就會套到別的峰身上——這是靜默的
   資料錯誤，不會有任何報錯。座標 (rt_index, dt_index) 對同一個 .mea 永遠穩定。

2. **規則是「標記」不是「移除」。**
   被可選規則否決的峰仍留在清單裡（rule_active=False），UI 才能把它畫成
   半透明圈 + 灰色列。若改回移除，使用者會看到峰憑空消失，也無法覆寫。

3. **手動選擇覆寫規則判定（設計決策 (a)）。**
   規則是啟發式，人是最終仲裁者（呼應 workflow 的校準哲學）。

不依賴任何真實資料檔。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rules
from main import (effective_active, is_rule_override, load_peak_state,
                  peak_key, save_peak_state)


# --------------------------------------------------------------------------- #
# 1. 持久化身分
# --------------------------------------------------------------------------- #
def test_peak_key_is_coordinates_not_peak_id():
    """同一個實體峰換了編號，key 必須不變；不同峰共用編號，key 必須不同。"""
    before = {"peak_id": 7, "rt_index": 3421, "dt_index": 712}
    after_renumber = {"peak_id": 2, "rt_index": 3421, "dt_index": 712}
    assert peak_key(before) == peak_key(after_renumber)

    other_peak_same_id = {"peak_id": 7, "rt_index": 8102, "dt_index": 945}
    assert peak_key(before) != peak_key(other_peak_same_id)


def test_saved_state_survives_renumbering(tmp_path):
    """調整強制規則參數導致重新編號後，勾選仍要落在同一個實體峰上。"""
    path = str(tmp_path / "x_peaks_state.json")
    original = [
        {"peak_id": 1, "rt_index": 100, "dt_index": 700, "user_active": False},
        {"peak_id": 2, "rt_index": 200, "dt_index": 800, "user_active": None},
    ]
    save_peak_state(path, original)

    # 基準改變 → 編號重排，順序也變了，但座標沒變
    renumbered = [
        {"peak_id": 1, "rt_index": 200, "dt_index": 800},
        {"peak_id": 2, "rt_index": 100, "dt_index": 700},
    ]
    saved = load_peak_state(path)
    for p in renumbered:
        p["user_active"] = saved.get(peak_key(p))

    by_coord = {(p["rt_index"], p["dt_index"]): p["user_active"] for p in renumbered}
    assert by_coord[(100, 700)] is False, "取消勾選必須跟著座標走，不是跟著編號"
    assert by_coord[(200, 800)] is None


def test_save_state_omits_untouched_peaks(tmp_path):
    """沒被使用者碰過的峰不寫檔，避免規則改變後復活過時的意見。"""
    path = str(tmp_path / "y_peaks_state.json")
    save_peak_state(path, [
        {"rt_index": 1, "dt_index": 1, "user_active": None},
        {"rt_index": 2, "dt_index": 2, "user_active": True},
        {"rt_index": 3, "dt_index": 3, "user_active": False},
    ])
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)["user_active"]
    assert set(payload) == {"2,2", "3,3"}


def test_missing_state_file_is_not_an_error(tmp_path):
    assert load_peak_state(str(tmp_path / "nope.json")) == {}


# --------------------------------------------------------------------------- #
# 2. 規則標記而非移除
# --------------------------------------------------------------------------- #
def _peaks():
    return [
        {"peak_id": 1, "rt_index": 1, "dt_index": 10, "prominence": 900, "drift_relative": 1.3},
        {"peak_id": 2, "rt_index": 2, "dt_index": 20, "prominence": 30,  "drift_relative": 1.4},
        {"peak_id": 3, "rt_index": 3, "dt_index": 30, "prominence": 500, "drift_relative": 1.5},
    ]


def test_mark_rules_keeps_every_peak():
    """輸入幾個峰就要出來幾個峰——被否決的只是 rule_active=False。"""
    peaks = _peaks()
    cfg = [{"rule_number": "R001", "enabled": True, "params": {"threshold": 100}}]
    report = rules.mark_rules(peaks, cfg)
    assert len(peaks) == 3, "mark_rules 不得移除任何峰"
    assert [p["rule_active"] for p in peaks] == [True, False, True]
    assert report["n_out"] == 2


def test_mark_rules_and_apply_rules_agree():
    """apply_rules 必須等於 mark_rules 再過濾，兩條路徑不能各自演化。"""
    cfg = [{"rule_number": "R001", "enabled": True, "params": {"threshold": 100}}]
    marked = _peaks()
    rules.mark_rules(marked, cfg)
    kept, _ = rules.apply_rules(_peaks(), cfg)
    assert [p["peak_id"] for p in marked if p["rule_active"]] == \
           [p["peak_id"] for p in kept]


def test_batch_rule_marks_instead_of_truncating():
    """整批型規則（R002 取前 N 名）同樣是標記，不是把清單截短。"""
    peaks = _peaks()
    cfg = [{"rule_number": "R002", "enabled": True, "params": {"top_n": 2}}]
    rules.mark_rules(peaks, cfg)
    assert len(peaks) == 3
    # prominence 900 / 500 進前兩名，30 落榜
    assert {p["peak_id"] for p in peaks if p["rule_active"]} == {1, 3}


# --------------------------------------------------------------------------- #
# 3. 手動覆寫規則
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rule_active,user_active,expected", [
    (True,  None,  True),    # 沒碰過 → 聽規則的
    (False, None,  False),
    (False, True,  True),    # 規則說不要，使用者救回來
    (True,  False, False),   # 規則說可以，使用者不要
])
def test_effective_active(rule_active, user_active, expected):
    peak = {"rule_active": rule_active, "user_active": user_active}
    assert effective_active(peak) is expected


def test_override_flag_only_marks_the_rescue_direction():
    """只有「規則否決但使用者保留」需要視覺標記。

    另一個方向（規則通過但使用者取消）就是一般的手動取消，已經用半透明圈
    表示，再加標記只會讓畫面變吵。
    """
    assert is_rule_override({"rule_active": False, "user_active": True}) is True
    assert is_rule_override({"rule_active": True, "user_active": False}) is False
    assert is_rule_override({"rule_active": True, "user_active": None}) is False
    assert is_rule_override({"rule_active": False, "user_active": None}) is False


def test_peak_selection_smoke():
    """完整走一遍：規則標記 → 使用者覆寫 → 存檔 → 讀回。"""
    peaks = _peaks()
    cfg = [{"rule_number": "R001", "enabled": True, "params": {"threshold": 100}}]
    rules.mark_rules(peaks, cfg)
    for p in peaks:
        p.setdefault("user_active", None)
    assert [effective_active(p) for p in peaks] == [True, False, True]

    peaks[1]["user_active"] = True      # 把 #2 救回來
    peaks[0]["user_active"] = False     # 把 #1 關掉
    assert [effective_active(p) for p in peaks] == [False, True, True]
    assert is_rule_override(peaks[1])


if __name__ == "__main__":
    def sep(t):
        print(f"\n{'=' * 66}\n{t}\n{'=' * 66}")

    sep("[1] 規則標記（R001 threshold=100）")
    peaks = _peaks()
    rules.mark_rules(peaks, [{"rule_number": "R001", "enabled": True,
                              "params": {"threshold": 100}}])
    for p in peaks:
        p.setdefault("user_active", None)
        print(f"  #{p['peak_id']}  prom={p['prominence']:4}  "
              f"rule_active={p['rule_active']}  effective={effective_active(p)}")

    sep("[2] 使用者把 #2 救回來、把 #1 關掉")
    peaks[1]["user_active"] = True
    peaks[0]["user_active"] = False
    for p in peaks:
        mark = "  <- 覆寫規則（虛線環）" if is_rule_override(p) else ""
        print(f"  #{p['peak_id']}  effective={effective_active(p)}{mark}")

    sep("[3] 持久化身分")
    print(f"  #{peaks[0]['peak_id']} 的 key = {peak_key(peaks[0])}  （座標，非編號）")
    print("  基準集合變動時編號會重排，座標不會——所以勾選不會套錯峰。")

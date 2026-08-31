"""第三支應用自己的峰選取狀態。

**不共用第一支應用的 `<name>_peaks_state.json`。** 那是 `main.py` 的產物，寫進去會
無聲改掉使用者在第一支應用裡的選取（隔離規則 2）。本應用一律寫 `_peaks_state3.json`。

鍵用 `(rt_index, dt_index)` 而**不是** `peak_id`——後者是基準集內的突出度排名，規則
參數一改就重新編號，用它存的選取會靜靜地黏到別顆峰上去。這條是第一支應用踩過的坑，
原樣沿用。

Version: 1.0 — by Albert Sheng（第三支應用，2026-08-31）
"""
import json
import os

import areas2


def state_path(mea_path):
    base = os.path.splitext(os.path.basename(mea_path))[0]
    return os.path.join(areas2.RESULTS_DIR, base + "_peaks_state3.json")


def peak_key(peak):
    return f"{peak.get('rt_index')},{peak.get('dt_index')}"


def load(mea_path, peaks):
    """把存過的選取套回 `peaks`（就地寫 `user_active`）。

    **存的是 `user_active` 而不是 `active`**，而且只存使用者**明確表示過**的那些：
    `None` 代表「沒意見，聽規則的」。存 `active` 會把規則當下的判定一起醃進檔案，
    之後規則改了也解不開——分不出這個 False 是規則說的還是使用者說的。
    """
    path = state_path(mea_path)
    saved = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f).get("active", {})
        except (OSError, ValueError):
            saved = {}                      # 壞掉的狀態檔不該讓整支應用打不開
    for p in peaks:
        k = peak_key(p)
        # 三態：True / False 是使用者說的，None 是「沒表示意見，聽規則的」。
        # 用 `saved.get(k, True)` 會把沒存過的一律當成「使用者說要」，規則就再也
        # 否決不了任何東西——那正是 top_n 設了卻沒有半顆峰變灰的原因。
        p["user_active"] = bool(saved[k]) if k in saved else None
    return peaks


def save(mea_path, peaks):
    os.makedirs(areas2.RESULTS_DIR, exist_ok=True)
    # 只寫使用者明確表示過的；沒表示意見的不寫，之後規則變了才跟得上
    payload = {"mea": os.path.basename(mea_path),
               "active": {peak_key(p): bool(p["user_active"]) for p in peaks
                          if p.get("user_active") is not None}}
    with open(state_path(mea_path), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

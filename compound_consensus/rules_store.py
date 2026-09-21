"""逐檔的規則覆寫。

**兩層，而且兩層的壽命刻意不同**（使用者 2026-09-07 的決定，經過一次反覆）：

| | 存在哪 | 關掉程式之後 |
|---|---|---|
| **預設** | 記憶體（起點是共用的 `rules_config.json`） | **歸零**，回到 `rules_config.json` |
| **逐檔覆寫** | `results/<base>_rules3.json` | **留著** |

理由是這兩件事的性質不同。改「全部檔案」的規則是**這一輪想怎麼看**——調 `top_n`
看看少幾顆峰長什麼樣，下次打開不該還黏著；使用者原話：「top_n=10 不是預設」。
而把某個檔**特別**調成不一樣，是對那個檔的判斷：這批訊號比較弱、那個檔 RIP 拖尾
——那是結論，重開一次不該要人重做一遍。使用者原話：「once they are customized
their own rule will be overwritten」。

所以：沒有自訂的檔每次都從 `rules_config.json` 開始；自訂過的檔一直用自己的那份，
直到按下「本檔改回預設」。

為什麼要逐檔：不同標本的訊號強弱差很多，一組吃得下 `top_n=10` 的參數，另一組可能
只有 4 顆峰值得看。全域一套參數等於逼使用者在「某些檔漏峰」與「某些檔一堆雜訊」
之間二選一。

**覆寫會改變偵測結果，不只是標記。** R004/R006 在突出度門檻之前生效
（`peaks.pre_gate_params()`），所以每個檔的參數指紋是拿**它自己的**規則算的——
`logic.params_fingerprint()` 收到哪一份 config，快取就對哪一份負責。傳錯 config
會讓快取無聲地對應到別的參數，這是本模組存在的唯一風險點。

隔離規則 2：檔名帶 `3`，不碰前兩支應用的產物。`GAS/` 完全不寫（隔離規則 3）。

Version: 1.2 — by Albert Sheng（第三支應用，2026-09-21）
"""
import copy
import glob
import json
import os

import areas2

#: 覆寫檔的格式版本。欄位改過就跳號，載入舊的會被當成沒有覆寫而退回預設——
#: 悄悄套用一份看不懂的設定比忽略它更糟。
SCHEMA_VERSION = 1

#: 這一輪的預設；`None` 表示還沒改過，跟著傳進來的共用預設走。**不寫檔。**
_default = None


def reset_session():
    """回到乾淨的起點——**只清記憶體裡的預設**。

    逐檔覆寫留在磁碟上，那是使用者對個別檔案的判斷，重開一次不該要他重做。
    應用啟動時呼叫；測試之間也靠它互不干擾。
    """
    global _default
    _default = None


# --------------------------------------------------------------------------- #
# 這一輪的預設 —— 記憶體，關掉就沒
# --------------------------------------------------------------------------- #
def load_default(fallback):
    """`(這一輪的預設, 來源)`，來源是 `"session"` 或 `"shared"`。"""
    if _default is not None:
        return copy.deepcopy(_default), "session"
    return copy.deepcopy(fallback), "shared"


def save_default(rules_config):
    global _default
    _default = copy.deepcopy(rules_config)


def clear_default():
    global _default
    had = _default is not None
    _default = None
    return had


# --------------------------------------------------------------------------- #
# 逐檔覆寫 —— 磁碟，留到下一次
# --------------------------------------------------------------------------- #
def rules_path(mea_path):
    base = os.path.splitext(os.path.basename(mea_path))[0]
    return os.path.join(areas2.RESULTS_DIR, base + "_rules3.json")


def has_override(mea_path):
    return os.path.exists(rules_path(mea_path))


def load_override(mea_path):
    """這個檔自己的規則；沒有、或讀不動、或版本對不上就回 `None`。

    壞掉的覆寫檔不該讓整支應用打不開，也不該被半套地套用——直接當成沒有。
    """
    path = rules_path(mea_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None
    if payload.get("schema") != SCHEMA_VERSION:
        return None
    rules = payload.get("rules")
    return rules if isinstance(rules, list) else None


def save_override(mea_path, rules_config):
    """把這一份規則存成該檔的覆寫。**存深拷貝**——呼叫端之後再改不會回頭污染。"""
    os.makedirs(areas2.RESULTS_DIR, exist_ok=True)
    payload = {"schema": SCHEMA_VERSION,
               "mea": os.path.basename(mea_path),
               "rules": copy.deepcopy(rules_config)}
    with open(rules_path(mea_path), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def clear_override(mea_path):
    """移除覆寫，這個檔改回跟著預設走。回傳是否真的移除了東西。"""
    path = rules_path(mea_path)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def customised_files():
    """目前有自訂規則的檔（以 `results/` 裡的覆寫檔為準，排序後）。

    回的是**檔名主幹**，不是完整路徑——覆寫是照 `.mea` 的檔名存的，這裡拿不回
    原本的資料夾。呼叫端要數「這個資料夾有幾個檔自訂過」時自己比對檔名。
    """
    pat = os.path.join(areas2.RESULTS_DIR, "*_rules3.json")
    return sorted(os.path.basename(p)[:-len("_rules3.json")]
                  for p in glob.glob(pat))


def effective(mea_path, default_config):
    """`(要用的規則, 來源)`，來源是 `"custom"` 或 `"default"`。

    **來源要跟著回傳**，不能只給規則：畫面上得說得出「這個檔為什麼跟隔壁不一樣」，
    否則使用者看到兩個檔同一組參數卻給出不同的峰數，只會覺得程式壞了。

    一律回**深拷貝**：呼叫端會就地編輯（面板直接改 dict），共用同一份物件會讓
    改 A 檔的參數把 B 檔一起改掉。
    """
    if mea_path:
        custom = load_override(mea_path)
        if custom is not None:
            return custom, "custom"
    return copy.deepcopy(default_config), "default"


def apply_to_all(mea_paths, rules_config, skip=None):
    """把同一份規則寫成一整組檔的覆寫。回傳實際寫了幾個。

    `skip` 裡的檔跳過（通常是規則的來源檔，它已經有了）。
    """
    skip = set(skip or ())
    n = 0
    for m in mea_paths:
        if m in skip:
            continue
        save_override(m, rules_config)
        n += 1
    return n

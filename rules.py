"""
rules.py  —  GC-IMS Identify Workflow 第七階段：候選峰篩選規則引擎
Version: ver.01 — by Albert Sheng

依 GC-IMS_Identify_Workflow.md §第七階段（draft.13 定案）：
  - RULE_REGISTRY + @register_rule 裝飾器
  - 三種規則型態：
      per_peak                    (peak, params) -> bool
      per_peak_with_context       (peak, params, context) -> bool
                                  context 提供影像層級常數（floor 等）
      batch                       (peaks_sorted, params) -> list
                                  無法用逐峰判斷表達（如「取前 N 名」）
  - apply_rules() 對逐峰型規則做 AND 合成，最後套用批次型規則
  - rules_config.json 存規則 enabled/params，rule_number 為穩定識別碼
    （不因函式改名/停用/新增而變動；一旦指定不重複使用）

初版內建五條規則（見下方 REGISTRY 建立）：
  R001 rule_min_prominence         突出度 >= threshold（絕對值）
  R002 rule_max_candidates         依突出度排序取前 top_n
  R003 rule_max_flatness           flatness <= threshold
  R004 rule_exclude_rip_band       |drift_relative - 1.0| > half_width
  R005 rule_min_relative_steepness prominence / (intensity - floor) >= min_ratio

執行順序限制（workflow §第七階段最後一段）：
  peaks.py (measure) → rip.py (drift_relative) → rules.py → UI 圈選 → identify.py

peaks.py 遷移（workflow §第七階段「與現有 peaks.py 規則的對應」）：
  peaks.py CLI 的 --prom-frac / --top-n 已被 R001 / R002 取代，但 peaks.py
  的實作本輪暫未修改（待 UI 端接上 rules_config.json 後同步移除）；期間
  peaks.py 若同時套用內建 prom_frac 與此處 R001 會造成兩次過濾，實測時
  請把 peaks.py 的 --prom-frac 0 --top-n 0 一起關掉，只讓 rules.py 篩選。

依賴：僅 Python stdlib
"""

import json


# --------------------------------------------------------------------------- #
# 規則登記
# --------------------------------------------------------------------------- #
RULE_TYPE_PER_PEAK = "per_peak"
RULE_TYPE_PER_PEAK_WITH_CONTEXT = "per_peak_with_context"
RULE_TYPE_BATCH = "batch"

_VALID_TYPES = {RULE_TYPE_PER_PEAK, RULE_TYPE_PER_PEAK_WITH_CONTEXT, RULE_TYPE_BATCH}

RULE_REGISTRY = {}


def register_rule(rule_number, name, description, rule_type=RULE_TYPE_PER_PEAK):
    """把一個規則函式登記進 RULE_REGISTRY。

    參數
    ----
    rule_number : str
        R + 三位數，e.g. 'R001'。一旦指定即為穩定識別碼，禁止重覆使用。
    name : str
        簡短規則名稱（顯示於 UI 面板）。
    description : str
        一句話說明，顯示於 UI 面板旁的說明欄。
    rule_type : str
        RULE_TYPE_PER_PEAK / RULE_TYPE_PER_PEAK_WITH_CONTEXT / RULE_TYPE_BATCH
        之一，決定 apply_rules() 如何呼叫這個函式。

    Raises
    ------
    ValueError
        rule_number 已被登記，或 rule_type 不在允許集合。
    """
    if rule_type not in _VALID_TYPES:
        raise ValueError(f"rule_type 必須為 {_VALID_TYPES} 之一，收到 {rule_type!r}")
    if rule_number in RULE_REGISTRY:
        raise ValueError(f"規則編號 {rule_number} 已被登記，不可重覆使用（穩定識別碼原則）")

    def decorator(fn):
        RULE_REGISTRY[rule_number] = {
            "fn": fn,
            "name": name,
            "description": description,
            "type": rule_type,
        }
        return fn
    return decorator


def list_rules():
    """回傳所有已登記規則的 metadata 清單（供 UI 面板列出全部規則）。

    每筆為 dict：{rule_number, name, description, type}
    依 rule_number 字典序排序。
    """
    return [
        {"rule_number": rn, "name": meta["name"],
         "description": meta["description"], "type": meta["type"]}
        for rn, meta in sorted(RULE_REGISTRY.items())
    ]


# --------------------------------------------------------------------------- #
# 內建五條規則
# --------------------------------------------------------------------------- #

@register_rule(
    "R001", "最小突出度",
    "突出度低於 threshold 的候選峰剔除（絕對值門檻）",
    rule_type=RULE_TYPE_PER_PEAK,
)
def rule_min_prominence(peak, params):
    # 註：workflow 表格文字說「相對於全圖最大突出度的比例」，但同章節的範例
    # 程式與 config 範例都用絕對值 threshold。這裡照範例程式實作（絕對值）——
    # 需要相對值語意時，呼叫者可先算 max_prominence 再自行換算 threshold。
    return peak.get("prominence", 0) >= params.get("threshold", 0)


@register_rule(
    "R002", "前 N 名上限",
    "依突出度排序，只保留前 N 名候選峰",
    rule_type=RULE_TYPE_BATCH,
)
def rule_max_candidates(peaks_sorted, params):
    top_n = params.get("top_n", 0)
    if not top_n or top_n <= 0:
        return list(peaks_sorted)
    # 依 prominence 排序（穩定，降冪），再取前 N
    ordered = sorted(peaks_sorted, key=lambda p: p.get("prominence", 0), reverse=True)
    return ordered[:top_n]


@register_rule(
    "R003", "最大平坦度",
    "平坦度高於 threshold 視為 plateau，剔除",
    rule_type=RULE_TYPE_PER_PEAK,
)
def rule_max_flatness(peak, params):
    flatness = peak.get("flatness")
    if flatness is None:
        return True   # 無 flatness 欄位時保守放行，避免誤刪
    return flatness <= params.get("threshold", 1.0)


@register_rule(
    "R004", "排除 RIP 柱狀帶",
    "漂移相對值落在 RIP 位置（x=1）± half_width 範圍內的峰予以剔除",
    rule_type=RULE_TYPE_PER_PEAK,
)
def rule_exclude_rip_band(peak, params):
    dr = peak.get("drift_relative")
    if dr is None:
        # 尚未跑過 rip.py 時放行，避免誤刪；apply_rules() 會另外報 warning
        return True
    half_width = params.get("half_width", 0.02)
    return abs(dr - 1.0) > half_width


@register_rule(
    "R005", "相鄰峰谷深比（駱駝背過濾）",
    "突出度相對於峰自身峰高的比例低於 min_ratio 則剔除",
    rule_type=RULE_TYPE_PER_PEAK_WITH_CONTEXT,
)
def rule_min_relative_steepness(peak, params, context):
    floor = context.get("floor")
    if floor is None:
        # 沒有 floor（影像層級常數）就無法算，保守放行
        return True
    peak_height = peak.get("intensity", 0) - floor
    if peak_height <= 0:
        return False   # 峰高不在 floor 之上，剔除
    steepness_ratio = peak.get("prominence", 0) / peak_height
    return steepness_ratio >= params.get("min_ratio", 0.3)


# --------------------------------------------------------------------------- #
# 套用引擎
# --------------------------------------------------------------------------- #
def _split_by_type(config):
    """把 enabled 且已登記的規則依型態分三組：per_peak / per_peak_ctx / batch。"""
    per_peak, per_peak_ctx, batch = [], [], []
    unknown = []
    for entry in config:
        if not entry.get("enabled", False):
            continue
        rn = entry.get("rule_number")
        if rn not in RULE_REGISTRY:
            unknown.append(rn)
            continue
        meta = RULE_REGISTRY[rn]
        item = (rn, meta["fn"], entry.get("params", {}))
        if meta["type"] == RULE_TYPE_PER_PEAK:
            per_peak.append(item)
        elif meta["type"] == RULE_TYPE_PER_PEAK_WITH_CONTEXT:
            per_peak_ctx.append(item)
        elif meta["type"] == RULE_TYPE_BATCH:
            batch.append(item)
    return per_peak, per_peak_ctx, batch, unknown


def apply_rules(peaks, config, context=None):
    """
    對候選峰清單依 config 套用所有 enabled 規則，回傳篩選後清單。

    - 逐峰規則（per_peak / per_peak_with_context）以 AND 合成：全部通過才留
    - 批次規則（batch）在逐峰過完後套用，維持 workflow 定的執行順序
    - 若 config 引用未登記的 rule_number，跳過並記錄於回傳的 report

    參數
    ----
    peaks : list[dict]
        peaks.py + rip.py 產出的完整峰清單。
    config : list[dict]
        [{"rule_number": "R001", "enabled": True, "params": {...}}, ...]
    context : dict | None
        影像層級常數，e.g. {"floor": 218.6, "rip_index": 680}。
        per_peak_with_context 型規則會拿到這個字典。

    回傳
    ----
    (filtered_peaks, report) : (list[dict], dict)
        report 內容：
          n_in                  : 進入時的峰數
          n_out                 : 出去時的峰數
          applied               : [(rule_number, kept_after_this_rule), ...]
          unknown_rule_numbers  : config 引用但未登記的 rule_number（若有）
          rip_missing_warning   : R004 啟用但峰無 drift_relative 時為 True
    """
    context = context or {}
    per_peak, per_peak_ctx, batch, unknown = _split_by_type(config)

    n_in = len(peaks)
    applied = []
    rip_missing_warning = False

    # 逐峰 AND 過濾
    kept = []
    for p in peaks:
        ok = True
        for _rn, fn, params in per_peak:
            if not fn(p, params):
                ok = False
                break
        if ok:
            for _rn, fn, params in per_peak_ctx:
                if not fn(p, params, context):
                    ok = False
                    break
        if ok:
            kept.append(p)

    # 若 R004 啟用但所有峰都缺 drift_relative，發 warning（R004 自身放行是保守策略，
    # 但呼叫者需知道規則實際上沒運作）
    if any(rn == "R004" for rn, _, _ in per_peak):
        has_dr = any(p.get("drift_relative") is not None for p in peaks)
        if not has_dr:
            rip_missing_warning = True

    # 記錄逐峰階段整體結果（不再細分每條規則的獨立貢獻，避免計算成本翻倍）
    applied.append(("<per_peak_stage>", len(kept)))

    # 批次規則依序套用
    for rn, fn, params in batch:
        kept = fn(kept, params)
        applied.append((rn, len(kept)))

    report = {
        "n_in": n_in,
        "n_out": len(kept),
        "applied": applied,
        "unknown_rule_numbers": unknown,
        "rip_missing_warning": rip_missing_warning,
    }
    return kept, report


# --------------------------------------------------------------------------- #
# rules_config.json I/O
# --------------------------------------------------------------------------- #
def default_config():
    """回傳預設 config（所有內建規則登記在此，但多數 enabled=False，避免安裝
    後首次執行就把候選都篩光）。實測校準後再讓使用者切開需要的規則。"""
    return [
        {"rule_number": "R001", "enabled": False, "params": {"threshold": 0}},
        {"rule_number": "R002", "enabled": False, "params": {"top_n": 0}},
        {"rule_number": "R003", "enabled": False, "params": {"threshold": 1.0}},
        {"rule_number": "R004", "enabled": True,  "params": {"half_width": 0.02}},
        {"rule_number": "R005", "enabled": False, "params": {"min_ratio": 0.3}},
    ]


def load_config(path):
    """讀 rules_config.json，回傳 config list。找不到 → 回傳 default_config()。

    載入後會保留 config 內原始順序（供 UI 面板依序顯示規則列）；未登記的
    rule_number 也一併保留，apply_rules() 執行時會 skip 且記錄於 report。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return default_config()
    if not isinstance(data, list):
        raise ValueError(f"{path} 內容應為 list，收到 {type(data).__name__}")
    return data


def save_config(path, config):
    """把 config 寫回 rules_config.json（utf-8, indent=2）。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

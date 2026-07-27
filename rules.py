"""
rules.py  —  GC-IMS Identify Workflow 第七階段：候選峰篩選規則引擎
Version: 3.0 — by Albert Sheng

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

變更記錄：
  2.1  — 新增 R006（排除 RIP 之前）；新增 mandatory 機制（R004/R006 不可停用，
         強制落在 load_config()/save_config() 而非 UI）；新增 mark_rules()，
         規則改為「標記」而非「移除」，apply_rules() 改為它加一次過濾。
  ver.01 — 初版，R001–R005。

內建六條規則：
  R001 rule_min_prominence         突出度 >= threshold（絕對值）
  R002 rule_max_candidates         依突出度排序取前 top_n              [批次型]
  R003 rule_max_flatness           flatness <= threshold
  R004 rule_exclude_rip_band       |drift_relative - 1.0| > half_width  [強制]
  R005 rule_min_relative_steepness prominence / (intensity - floor) >= min_ratio
  R006 rule_exclude_before_rip     drift_relative > boundary            [強制]

兩種套用模式：
  mark_rules()   為每個峰設 rule_active，不移除任何峰。UI 用這個——被規則否決
                 的峰仍要留在畫面上（半透明圈 + 灰列），使用者才看得到規則做了
                 什麼，也才能手動覆寫（見 main.effective_active）。
  apply_rules()  mark_rules() 再過濾。identify.py 用這個——比對階段沒必要對
                 使用者不要的峰浪費運算。

強制規則與執行順序（workflow §第七階段，draft.14）：
  R004/R006 定義了峰編號的基準集合，必須在 peaks.py **內部**的突出度門檻之前
  生效，不能等偵測完再篩：門檻是相對值（prom_frac × max_prominence），而 RIP
  通常就是全圖最大突出度，留著會把門檻墊高數倍、誤殺真峰。實作見
  peaks.select_from_maxima()；本檔的登記讓它們能出現在規則面板，並在偵測後
  重跑（該次為 no-op）。

  peaks.py (measure) → 門檻前裁切 R004/R006 → 突出度門檻 → 指派 peak_id
      → mark_rules() 套用 R001/R002/R003/R005 → UI 圈選 → identify.py

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


def register_rule(rule_number, name, description, rule_type=RULE_TYPE_PER_PEAK,
                  mandatory=False):
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
    mandatory : bool
        True 表示這條規則不可停用（使用者決策，非技術限制）。峰的編號基準線就
        建立在強制規則套用之後的集合上，若允許關閉，整組編號會跟著變動。
        load_config() 會強制把它們設為 enabled，手動改 rules_config.json 也
        繞不過去——「不可取消」若只做在 UI 上，就只是假象。

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
            "mandatory": mandatory,
        }
        return fn
    return decorator


def mandatory_rules():
    """不可停用的規則編號集合（編號基準線由這些規則定義）。"""
    return {rn for rn, meta in RULE_REGISTRY.items() if meta.get("mandatory")}


def list_rules():
    """回傳所有已登記規則的 metadata 清單（供 UI 面板列出全部規則）。

    每筆為 dict：{rule_number, name, description, type}
    依 rule_number 字典序排序。
    """
    return [
        {"rule_number": rn, "name": meta["name"],
         "description": meta["description"], "type": meta["type"],
         "mandatory": meta.get("mandatory", False)}
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
    rule_type=RULE_TYPE_PER_PEAK, mandatory=True,
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


@register_rule(
    "R006", "排除 RIP 之前（比 RIP 快）",
    "drift_relative ≤ 1.0 的峰剔除：比 RIP 更快抵達，物理上不是分析物",
    rule_type=RULE_TYPE_PER_PEAK, mandatory=True,
)
def rule_exclude_before_rip(peak, params):
    """RIP 是反應離子；分析物離子較重、遷移較慢，drift_relative 理應 > 1.0。
    落在 RIP 之前的訊號跑得比反應離子還快，不視為分析物峰。

    與 R004 的關係：R004 剔除 1.0 附近的窄帶（RIP 柱狀帶本身），R006 剔除整個
    1.0 以下的區域。兩者同時啟用時，聯集等價於單邊切 drift_relative > 1.0 +
    half_width。兩條分開登記，是為了讓使用者能單獨關掉 R006 去檢視 1.0 以下
    的內容，而不必連 RIP 柱狀帶一起放回來。

    與 R004 同屬「門檻前」規則：見 peaks.select_from_maxima()。此處的登記讓它
    能出現在規則面板並在偵測後重跑（該次為 no-op），語意來源仍是這個函式。
    """
    dr = peak.get("drift_relative")
    if dr is None:
        return True   # 尚未跑過 rip.py 時放行，避免誤刪（同 R004）
    return dr > params.get("boundary", 1.0)


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


def mark_rules(peaks, config, context=None):
    """對每個峰標記 `rule_active`，**不移除任何峰**（就地修改）。

    為什麼需要這個模式（draft.15 使用者決策）：UI 上被規則篩掉的峰不該憑空消失，
    而是留在畫面上以「未選取」樣態顯示（圈半透明、表格列變灰），與使用者手動
    取消勾選的外觀完全一致。使用者因此看得到「規則做了什麼」，而不是東西不見了。

    與 apply_rules() 的關係：apply_rules() 就是本函式加上一次過濾，保留給
    identify.py 使用——比對階段沒必要對使用者不要的峰浪費運算（workflow
    §第七階段「與 identify.py 的關係」）。

    注意：本函式只處理**可選規則**。強制規則 R004/R006 在 peaks.py 的突出度
    門檻之前就已生效，那些候選根本不在 peaks 清單裡，不是「被標記為不要」。

    回傳 report，格式同 apply_rules()。
    """
    context = context or {}
    per_peak, per_peak_ctx, batch, unknown = _split_by_type(config)

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
        p["rule_active"] = ok

    survivors = [p for p in peaks if p["rule_active"]]
    applied = [("<per_peak_stage>", len(survivors))]

    # 整批型規則（R002）作用在逐峰規則的倖存者上；被截掉的同樣標記而非移除
    for rn, fn, params in batch:
        kept = fn(survivors, params)
        kept_ids = {id(x) for x in kept}
        for p in survivors:
            if id(p) not in kept_ids:
                p["rule_active"] = False
        survivors = [p for p in survivors if p["rule_active"]]
        applied.append((rn, len(survivors)))

    rip_missing_warning = False
    if any(rn == "R004" for rn, _, _ in per_peak):
        if not any(p.get("drift_relative") is not None for p in peaks):
            rip_missing_warning = True

    return {
        "n_in": len(peaks),
        "n_out": len(survivors),
        "applied": applied,
        "unknown_rule_numbers": unknown,
        "rip_missing_warning": rip_missing_warning,
    }


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
    report = mark_rules(peaks, config, context)
    return [p for p in peaks if p.get("rule_active", True)], report


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
        {"rule_number": "R006", "enabled": True,  "params": {"boundary": 1.0}},
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
    return _enforce_mandatory(data)


def _enforce_mandatory(config):
    """把強制規則補回 config 並確保 enabled=True（就地修改後回傳）。

    涵蓋兩種繞過方式：手動把 enabled 改成 false，以及整條從檔案裡刪掉。
    參數維持使用者設定的值——強制的是「這條規則會跑」，不是它的參數。
    """
    required = mandatory_rules()
    present = {e.get("rule_number") for e in config}
    for entry in config:
        if entry.get("rule_number") in required:
            entry["enabled"] = True
    for rn in sorted(required - present):
        fallback = next((e for e in default_config() if e["rule_number"] == rn), None)
        if fallback:
            config.append(dict(fallback, enabled=True))
    return config


def save_config(path, config):
    """把 config 寫回 rules_config.json（utf-8, indent=2）。"""
    config = _enforce_mandatory(list(config))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

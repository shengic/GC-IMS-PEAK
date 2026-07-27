"""
calibration.py  —  GC-IMS Identify Workflow 第四階段：RT→RI 轉換（保留時間正規化）
Version: 3.0 — by Albert Sheng

依 GC-IMS_Identify_Workflow.md §第四階段（本輪定案）。VOCal 校準表存
{ColNormY=RI, ColNormX=log10(Rt)}、ColNormisLog=true —— 即 RI 對 log10(Rt) 做
分段線性內插／外插（six-point log-linear，Kovats log 形式，非 Van den Dool 線性）。

**本檔的預設是「相對模式」，不是烷烴假設（§第四階段第 9 點，優先於第 8 點）**
  化合物身分是整條鏈路唯一真正卡住的缺口（第 7 點）：GC-IMS 無質譜，RT/DT 座標
  與 .gasprj 結構都推不回化合物是什麼（第 3 點：VOCal 的 RI 編輯器只存數值點、
  不存化合物名）。故：
    - 預設 series_key=None → single_point_relative：只算 log10(Rt/Rt_ref) 相對座標，
      known_ri_available=False，**不指派任何絕對 RI**，避免把未驗證假設偷渡成事實。
    - 對照表到位後，呼叫端顯式給 series_key（如 "n_alkane" 或 "custom"）→ 升級成
      multi_point_loglinear 絕對 RI，本檔內插演算法完全不用動。

模式（provenance 標記，沿用 dt_convert.py 的 k0_mode 原則）
  single_point_relative  預設；ri=None、known_ri_available=False；ri_relative=log10(Rt/Rt_ref)
  multi_point_loglinear  給了 series_key 且錨點足夠；ri=絕對 RI；known_ri_available=True；
                         assumed_unverified 依系列而定（n_alkane=True）
  unavailable            STD 不可用或錨點 < 2 → ri=None + reason，不靜默造假

**錨點偵測不寫死數量（§第四階段第 5 點）**：141215 實測 7 個候選，其中 334.3/347.9
  這組雙峰無法判定 monomer/dimer，維持排除 → 5 個乾淨錨點（282/400.3/521.8/697/949）。
  select_anchor_peaks() 動態挑錨點、把 RT 過近的相鄰峰歸為 ambiguous_groups。

**STD 品質前置過濾（§第四階段第 6 點）**：012251 為 Status=doubtful、只 3 個峰、
  連最強的 282s 峰都缺，assess_std() 予以排除，不與 141215 做時間加權 bracket 內插。

依賴：numpy/scipy（僅 get_interp_fn 用 scipy.interpolate）+ reference_series（本專案）。
      吃 peaks.py 產出的峰 dict（含 retention_s/prominence/active），不 import
      readGAS/peaks，避免循環依賴與重跑偵測。
"""

import datetime
import glob
import json
import math
import os
import re

import reference_series as rs


# --------------------------------------------------------------------------- #
# 相對座標（single_point_relative）
# --------------------------------------------------------------------------- #
def relative_single_point(rt_s, rt_ref):
    """log10(rt_s / rt_ref)：只需一個穩定參考保留時間、不需化合物身分。
    與 multi_point_loglinear 的內插空間一致（皆 log10），對照表到位後可平滑升級。"""
    if rt_s <= 0 or rt_ref <= 0:
        raise ValueError("rt_s 與 rt_ref 皆須 > 0")
    return math.log10(rt_s) - math.log10(rt_ref)


# --------------------------------------------------------------------------- #
# 錨點偵測（不寫死數量；標記待釐清雙峰）
# --------------------------------------------------------------------------- #
def select_anchor_peaks(peaks, prominence_frac=0.05, doublet_gap_s=20.0,
                        require_active=True):
    """
    從 STD 峰清單挑「乾淨錨點」，把 RT 過近、無法判定的相鄰峰歸成待釐清群組。
    **不硬取固定數量。** 對 141215 的 7 個候選、預設 doublet_gap_s=20，會得到
    乾淨錨點 [282,400.3,521.8,697,949] 與待釐清群組 [[334.3,347.9]]。

    參數
    ----
    peaks : list[dict]        peaks.py 峰清單，至少含 retention_s、prominence
    prominence_frac : float   突出度門檻＝此比例 × 最大突出度（intensity 缺 prominence
                              時退用 intensity）
    doublet_gap_s : float     相鄰候選 RT 差 < 此值 → 歸待釐清雙峰
    require_active : bool      True → 只取 active!=False 的峰

    回傳
    ----
    dict : {"clean_anchors":[...], "ambiguous_groups":[[...],...], "n_candidates":int}
    """
    def strength(p):
        return p.get("prominence") if p.get("prominence") is not None else (p.get("intensity") or 0.0)

    cands = [p for p in peaks if p.get("retention_s") is not None]
    if require_active:
        cands = [p for p in cands if p.get("active", True)]
    if not cands:
        return {"clean_anchors": [], "ambiguous_groups": [], "n_candidates": 0}

    max_s = max(strength(p) for p in cands) or 1.0
    thresh = prominence_frac * max_s
    cands = [p for p in cands if strength(p) >= thresh]
    cands.sort(key=lambda p: p["retention_s"])

    groups = []
    for p in cands:
        if groups and (p["retention_s"] - groups[-1][-1]["retention_s"]) < doublet_gap_s:
            groups[-1].append(p)
        else:
            groups.append([p])

    clean = [g[0] for g in groups if len(g) == 1]
    ambiguous = [g for g in groups if len(g) > 1]
    return {"clean_anchors": clean, "ambiguous_groups": ambiguous,
            "n_candidates": len(cands)}


# --------------------------------------------------------------------------- #
# 待釐清雙峰解析（draft.18 §4：DT_rel 間距均勻度定量判斷）
# --------------------------------------------------------------------------- #
def _spacing_std(values):
    """相鄰差的母體標準差（純 stdlib，不引 numpy）。"""
    if len(values) < 2:
        return 0.0
    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    mean = sum(diffs) / len(diffs)
    return (sum((d - mean) ** 2 for d in diffs) / len(diffs)) ** 0.5


def resolve_anchor_doublets(clean_anchors, ambiguous_groups,
                            dt_rel_key="drift_relative", dt_rel_dup_tol=0.02):
    """
    嘗試把待釐清雙峰群組解析成錨點（draft.18 §4）。

    **[警告] 這是案例導向的啟發式，不是通用演算法**（workflow §第四階段第 5 點
    自己就這樣註記）——門檻/邏輯換別的 STD 可能要調。故：預設仍先偵測（保留
    ambiguous_groups 原始資訊），本函式只在「有 DT_rel 資料」時嘗試解析，無資料
    則整組維持隔離、不硬猜。

    對每個群組：
      1. 濾掉 DT_rel 與「既有乾淨錨點」重複（±dt_rel_dup_tol）的候選——這類多半是
         強峰的拖尾/肩峰（347.9s 的 DT_rel≈1.104 與 282.0s 同值即屬此類）。
      2. 剩餘候選中，挑「併入後整條 DT_rel 階梯間距標準差最小」的當錨點（同系物
         序列的 DT_rel 應近乎等差）。
      3. 全數被濾掉 → 這組不貢獻錨點。

    回傳 (resolved_anchors_sorted, resolution_log)。
    """
    if not clean_anchors or any(p.get(dt_rel_key) is None for p in clean_anchors):
        return list(clean_anchors), [{"note": "缺 DT_rel，雙峰維持隔離，未解析",
                                      "n_groups": len(ambiguous_groups)}]

    resolved = list(clean_anchors)
    log = []
    for g in ambiguous_groups:
        cands = [p for p in g if p.get(dt_rel_key) is not None]
        existing_dt = [p[dt_rel_key] for p in resolved]
        kept = [p for p in cands
                if not any(abs(p[dt_rel_key] - e) <= dt_rel_dup_tol for e in existing_dt)]
        artifact = [p for p in cands if p not in kept]

        entry = {"group_rt": [round(p["retention_s"], 1) for p in g],
                 "excluded_as_artifact": [round(p["retention_s"], 1) for p in artifact]}
        if not kept:
            entry["chosen"] = None
            log.append(entry)
            continue

        best, best_std = None, float("inf")
        for c in kept:
            trial = sorted(resolved + [c], key=lambda p: p["retention_s"])
            s = _spacing_std([p[dt_rel_key] for p in trial])
            if s < best_std:
                best_std, best = s, c
        resolved = sorted(resolved + [best], key=lambda p: p["retention_s"])
        entry["chosen"] = round(best["retention_s"], 1)
        entry["excluded_as_artifact"] = [round(p["retention_s"], 1)
                                         for p in cands if p is not best]
        entry["dt_rel_spacing_std"] = round(best_std, 4)
        log.append(entry)

    return sorted(resolved, key=lambda p: p["retention_s"]), log


# --------------------------------------------------------------------------- #
# 錨點釘定（pin）：把偵測到的 N 峰對齊到已知的 M 個錨點模板
# --------------------------------------------------------------------------- #
def template_from_series(rts, series_key, ri_values=None):
    """
    把一組已知錨點 RT 配上某系列的身分標籤（與 RI，若有），組成 pin_anchors 用的
    expected 模板。標籤/碳數取自 reference_series（如 methyl_ketone 的
    2-butanone…2-nonanone）；ri 優先用傳入的 ri_values，其次系列內建的。

    回傳 [{"rt_s":.., "label":.., "carbon":.., "ri":..|None}, ...]（依 RT 遞增）
    """
    series = rs.REFERENCE_SERIES.get(series_key, {})
    members = series.get("members") or []
    carbons = series.get("carbon_numbers") or []
    ris = ri_values if ri_values is not None else series.get("ri_values")
    out = []
    for i, rt in enumerate(sorted(float(r) for r in rts)):
        out.append({
            "rt_s": rt,
            "label": members[i] if i < len(members) else f"{series_key}[{i}]",
            "carbon": carbons[i] if i < len(carbons) else None,
            "ri": ris[i] if (ris is not None and i < len(ris)) else None,
        })
    return out


def pin_anchors(peaks, expected, tol_s=20.0, key="retention_s"):
    """
    把偵測到的 STD 峰「釘」到已知錨點模板：對每個 expected 錨點，在容差 tol_s 內
    找最靠近、且尚未被別的錨點用掉的偵測峰，配成一對。

    解決「偵測器找到 N 個峰（實測 9），但 STD 只有 M 個已知化合物（甲基酮 6 個）」
    的問題——多出來的峰自動忽略，容差內找不到的錨點明確標 missing（不會靜默變少）。

    參數
    ----
    peaks : list[dict]     STD 的偵測峰（含 retention_s）
    expected : list[dict]  template_from_series() 產物；依 rt_s 遞增
    tol_s : float          容差視窗（秒），吸收保留時間漂移
    key : str              峰的保留時間欄位

    回傳
    ----
    (pinned_peaks, report)
        pinned_peaks : list[dict]  命中的偵測峰（複本，附 _pinned_label/_pinned_ri/
                       _pinned_carbon/_pinned_residual_s），依 rt 遞增
        report : list[dict]        每個 expected 的命中/未命中明細（供 provenance）
    """
    used = set()
    pinned, report = [], []
    for exp in expected:
        best_i, best_d = None, tol_s
        for i, p in enumerate(peaks):
            if i in used or p.get(key) is None:
                continue
            d = abs(p[key] - exp["rt_s"])
            if d <= best_d:
                best_i, best_d = i, d
        if best_i is None:
            report.append({"expected_rt": exp["rt_s"], "label": exp.get("label"),
                           "matched_rt": None, "note": f"容差 ±{tol_s}s 內無偵測峰（缺）"})
            continue
        used.add(best_i)
        a = dict(peaks[best_i])
        a["_pinned_label"] = exp.get("label")
        a["_pinned_carbon"] = exp.get("carbon")
        a["_pinned_ri"] = exp.get("ri")
        a["_pinned_residual_s"] = round(peaks[best_i][key] - exp["rt_s"], 2)
        pinned.append(a)
        report.append({"expected_rt": exp["rt_s"], "label": exp.get("label"),
                       "matched_rt": round(peaks[best_i][key], 2),
                       "residual_s": a["_pinned_residual_s"]})
    pinned.sort(key=lambda p: p[key])
    return pinned, report


def select_homolog_ladder(peaks, n_expected, dt_rel_key="drift_relative",
                          prominence_frac=0.05, max_candidates=14):
    """
    自動從 STD 偵測峰中挑出 n_expected 個「同系物階梯」錨點（如 6 個甲基酮），
    不需使用者提供模板 RT。用同系物的物理性質：碳數遞增 → RT 遞增且 DT_rel 嚴格
    遞增、間距近乎等差。

    作法：突出度濾強峰 → 取前 max_candidates 名 → 在所有 n_expected 組合中，只留
    DT_rel 嚴格遞增者，選「DT_rel 間距標準差最小、突出度總和最大」那一組。

    回傳 (chosen_sorted_by_rt, report)。挑不到足量 → 回傳現有的、report 記原因。
    """
    import itertools

    def strength(p):
        return p.get("prominence") if p.get("prominence") is not None else (p.get("intensity") or 0.0)

    cands = [p for p in peaks
             if p.get("retention_s") is not None and p.get(dt_rel_key) is not None
             and p.get("active", True)]
    if not cands:
        return [], {"note": "無帶 DT_rel 的候選峰"}
    max_s = max(strength(p) for p in cands) or 1.0
    cands = [p for p in cands if strength(p) >= prominence_frac * max_s]
    cands = sorted(cands, key=strength, reverse=True)[:max_candidates]

    if len(cands) <= n_expected:
        chosen = sorted(cands, key=lambda p: p["retention_s"])
        return chosen, {"note": "候選數 <= n_expected，全採用",
                        "n_candidates": len(cands), "n_selected": len(chosen)}

    best, best_score = None, None
    for combo in itertools.combinations(cands, n_expected):
        s = sorted(combo, key=lambda p: p["retention_s"])
        dts = [p[dt_rel_key] for p in s]
        if any(dts[i + 1] <= dts[i] for i in range(len(dts) - 1)):
            continue                                    # DT_rel 必須嚴格遞增（同系物）
        score = (_spacing_std(dts), -sum(strength(p) for p in s))
        if best_score is None or score < best_score:
            best_score, best = score, s
    if best is None:                                    # 無嚴格遞增組合 → 退用最強前 N
        best = sorted(cands[:n_expected], key=lambda p: p["retention_s"])
        return best, {"note": "無嚴格遞增 DT_rel 組合，退用最強前 N",
                      "n_candidates": len(cands)}
    return best, {"n_candidates": len(cands), "n_selected": len(best),
                  "selected_rt_s": [round(p["retention_s"], 1) for p in best],
                  "dt_rel_spacing_std": round(_spacing_std(
                      [p[dt_rel_key] for p in best]), 4)}


# --------------------------------------------------------------------------- #
# STD 品質前置過濾
# --------------------------------------------------------------------------- #
def assess_std(header, n_clean_anchors, reference_n_anchors=None,
               min_anchors=2, sparse_ratio=0.5,
               top_intensity=None, min_top_intensity=2000):
    """
    判定這支 STD 能不能用來建曲線。**建曲線前先跑。**

    draft.18 §8 原則：**不要只看 Status=="doubtful" 這個布林標籤**（本批兩支檔案
    都標 doubtful 但品質天差地別）——以實際峰數/強度為準，Status 僅當輔助解釋。

    - Status=doubtful/invalid → 列 warnings（不強制否決）。
    - 錨點數 < min_anchors → 不可用（log-linear 至少 2）。
    - top_intensity < min_top_intensity → 訊號過弱、不可用（012251 最強峰缺失）。
    - 給了 reference_n_anchors 時，錨點數 < reference × sparse_ratio → 訊號缺失。

    回傳
    ----
    dict : {"usable":bool,"reasons":[...],"warnings":[...],"status":str|None,
            "n_clean_anchors":int}
    """
    reasons, warnings = [], []
    status = header.get("Status") if header else None
    status_comment = header.get("Status comment", "") if header else ""

    if status and status.strip().lower() in {"doubtful", "invalid", "error"}:
        warnings.append(f"表頭 Status={status!r}"
                        + (f"（{status_comment}）" if status_comment else ""))

    if n_clean_anchors < min_anchors:
        reasons.append(f"乾淨錨點數 {n_clean_anchors} < 最少需求 {min_anchors}")

    if top_intensity is not None and top_intensity < min_top_intensity:
        reasons.append(f"最強峰強度 {top_intensity} < 門檻 {min_top_intensity}，訊號過弱")

    if reference_n_anchors and n_clean_anchors < sparse_ratio * reference_n_anchors:
        reasons.append(
            f"乾淨錨點數 {n_clean_anchors} < 對照 STD 的 "
            f"{sparse_ratio:g}×{reference_n_anchors}={sparse_ratio * reference_n_anchors:g}"
            "，疑似訊號缺失")

    return {"usable": len(reasons) == 0, "reasons": reasons, "warnings": warnings,
            "status": status, "n_clean_anchors": n_clean_anchors}


# --------------------------------------------------------------------------- #
# 建校正表（純核心；不知道也不需要知道是哪個系列）
# --------------------------------------------------------------------------- #
def build_calibration(anchor_rts, series_key=None,
                      start_carbon=None, ri_values=None):
    """
    從「已挑好、已排序的乾淨錨點保留時間」建校正表。回傳 dict **可 JSON 序列化**
    （不放 interp 函式，改存 anchor_rts/ri_values/log_rt；套用時 get_interp_fn() 重建）。

    模式決策：
      series_key is None                → single_point_relative（預設，第 9 點）
      series_key 有值且指派成功          → multi_point_loglinear（絕對 RI）
      series_key 有值但無法指派 RI       → 退回 single_point_relative + reason
      錨點 < 2                          → unavailable

    參數
    ----
    anchor_rts : list[float]  乾淨錨點保留時間（秒）；內部會排序
    series_key : str|None      reference_series 的系列；None=不假設、走相對模式
    start_carbon : int|None    n_alkane 用；覆寫 assumed_start_carbon（第二層假設）
    ri_values : list|None      table 類（custom/methyl_ketone）用；直接給各錨點 RI
    """
    anchor_rts = sorted(float(r) for r in anchor_rts)
    n = len(anchor_rts)

    cal = {
        "mode": "unavailable",
        "known_ri_available": False,
        "series_used": series_key,
        "assumed_unverified": False,
        "provenance_note": None,
        "n_anchors": n,
        "anchor_rts": anchor_rts,
        "ri_values": None,
        "log_rt": None,
        "reference_rt_s": None,
        "reason": None,
    }
    if n < 2:
        cal["reason"] = f"錨點數 {n} < 2，無法建曲線"
        return cal

    # 預設：無系列 → 相對模式（第 9 點，優先於烷烴假設）
    if series_key is None:
        cal["mode"] = "single_point_relative"
        cal["reference_rt_s"] = anchor_rts[-1]        # 最晚洗出的乾淨錨點
        cal["reason"] = ("尚無化合物對照表；僅提供相對座標 log10(Rt/Rt_ref)，"
                         "ri=None、known_ri_available=False，待對照表到位以 series_key 升級")
        return cal

    # 有系列 → 嘗試指派絕對 RI
    try:
        ris = rs.assign_ri(n, series_key, start_carbon=start_carbon, ri_values=ri_values)
    except (KeyError, ValueError) as e:
        cal["mode"] = "single_point_relative"
        cal["reference_rt_s"] = anchor_rts[-1]
        cal["reason"] = f"series_key={series_key!r} 無法指派絕對 RI（{e}）；退回相對座標"
        return cal

    cal["mode"] = "multi_point_loglinear"
    cal["known_ri_available"] = True
    cal["assumed_unverified"] = rs.series_is_assumed(series_key)
    cal["provenance_note"] = rs.REFERENCE_SERIES.get(series_key, {}).get("note")
    cal["ri_confidence"] = rs.series_confidence(series_key)
    cal["ri_values"] = list(ris)
    cal["log_rt"] = [math.log10(r) for r in anchor_rts]
    cal["reference_rt_s"] = anchor_rts[-1]
    if start_carbon is not None:
        cal["start_carbon"] = start_carbon
    return cal


def get_interp_fn(cal):
    """由 JSON 化校正表重建 RI 內插函式（scipy 線性、範圍外**沿邊界斜率外插**，
    draft.21 已定案不 clamp）。輸入 log10(Rt)。僅 multi_point_loglinear 可用。"""
    if cal.get("mode") != "multi_point_loglinear":
        raise ValueError("只有 multi_point_loglinear 模式有 RI 內插函式")
    from scipy.interpolate import interp1d
    return interp1d(cal["log_rt"], cal["ri_values"], kind="linear",
                    bounds_error=False, fill_value="extrapolate")


def make_rt_to_ri(cal):
    """
    回傳一個 rt_to_ri(rt_query) -> (ri_value, extrapolated) 閉包，內含**單一** interp_fn。

    **draft.21 檢查清單第 7 點：attach_ri() 與 ri_yticks（及任何讀校準結果的地方）
    都必須透過本工廠取得同一份 RT→RI／外插判斷邏輯，不可各自重造**，否則會出現
    「峰表用外插、軸刻度用 clamp」這種顯示與資料不一致。

    數學（見 RT_to_RI_normalization_math.md）：Step 2 查詢時對 RT 取 log10，Step 3–5
    在 log10(RT) 空間分段線性內插；錨點範圍外沿邊界斜率外插並標 extrapolated=True。
    僅 multi_point_loglinear 可用。
    """
    fn = get_interp_fn(cal)
    anchors = cal.get("anchor_rts") or []
    x_lo = math.log10(min(anchors)) if anchors else None
    x_hi = math.log10(max(anchors)) if anchors else None

    def rt_to_ri(rt_query):
        if rt_query is None or rt_query <= 0:
            return None, None
        x = math.log10(rt_query)                       # Step 2：查詢也要取 log10
        extrapolated = bool(x_lo is not None and (x < x_lo or x > x_hi))
        return float(fn(x)), extrapolated              # Step 3–5 由 interp_fn 完成

    return rt_to_ri


def ri_yticks(cal, rt_min, rt_max, step=None, max_ticks=8, extrap_margin_steps=1):
    """
    產生把熱圖 y 軸從「保留時間秒數」改標成「保留指數 RI」的刻度（**純顯示層**，
    不動像素、不動 x 軸 drift 正規化）。

    RT→RI 是 log10(RT) 上的分段線性，屬非線性，故不能直接把 extent 換成 RI；本
    函式改回傳「等距 RI 整數值（如每 100）各自對應到的 RT 位置」，讓 RI 網格線
    落在正確的、非等距的 RT 位置上。僅 multi_point_loglinear（有絕對 RI）可用；
    其餘模式（相對/unavailable）回 None。

    外插策略（draft.20 決策）：**外插 + 標記**。允許超出錨點 RI 範圍
    extrap_margin_steps 格的刻度（避免 log10(RT→0) 無限外插爆掉），凡落在錨點
    RI 範圍外的刻度，標籤加 '*' 保留「此為外插、可信度較低」的資訊。

    參數
    ----
    rt_min, rt_max : float       熱圖 y 軸的保留時間範圍（秒）
    step : float|None            RI 刻度間距；None → 依錨點 RI 跨度自動選 nice 值
    max_ticks : int              自動選 step 時的目標刻度數上限
    extrap_margin_steps : int    允許往錨點範圍外外插幾格刻度（0＝不外插）

    回傳
    ----
    (rt_positions_s, ri_labels) : (list[float], list[str]) 或 None
        rt_positions_s 為秒；呼叫端若軸用分鐘需自行 /60。外插刻度標籤帶 '*'。
    """
    if not isinstance(cal, dict) or cal.get("mode") != "multi_point_loglinear":
        return None
    import numpy as np
    anchor_ri = cal.get("ri_values") or []
    if len(anchor_ri) < 2:
        return None
    ari_lo, ari_hi = float(min(anchor_ri)), float(max(anchor_ri))

    rt0, rt1 = max(float(rt_min), 1e-6), float(rt_max)
    if rt1 <= rt0:
        return None
    # RT→RI 走**共用**工廠（draft.21 清單第 7 點）——與 attach_ri 同一份外插邏輯，
    # 不各自重造。下面的 np.interp 只用來「反查」RI→RT 刻度位置，不是做 RT→RI 換算。
    rt_to_ri = make_rt_to_ri(cal)
    rts = np.linspace(rt0, rt1, 1024)
    ris = np.array([rt_to_ri(t)[0] for t in rts])
    if not np.all(np.diff(ris) > 0):              # 理論上單調遞增；保險排序
        order = np.argsort(ris)
        ris, rts = ris[order], rts[order]

    # step 依「錨點」RI 跨度選 nice 值（穩定，不受外插極端值影響）
    if step is None:
        span = ari_hi - ari_lo
        step = (next((s for s in (10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000)
                      if span / s <= max_ticks), 5000) if span > 0 else 100)

    # 決策（draft.20）：外插 + 標記。允許超出錨點 RI 範圍 extrap_margin_steps 格的
    # 外插刻度（不做無限外插——log10(RT→0) 會爆），超出錨點範圍者標 '*' 保留資訊。
    lo_bound = max(float(ris[0]), ari_lo - extrap_margin_steps * step)
    hi_bound = min(float(ris[-1]), ari_hi + extrap_margin_steps * step)
    if hi_bound <= lo_bound:
        return None
    ticks_ri, v = [], math.ceil(lo_bound / step) * step
    while v <= hi_bound + 1e-9:
        ticks_ri.append(v)
        v += step
    if len(ticks_ri) < 2:
        return None
    rt_pos = np.interp(ticks_ri, ris, rts)
    labels = [f"{int(round(r))}" + ("*" if (r < ari_lo - 1e-9 or r > ari_hi + 1e-9) else "")
              for r in ticks_ri]
    return [float(x) for x in rt_pos], labels


def warp_rows_to_ri(img, row_rts, cal, n_out=None):
    """
    把顯示影像沿 y（列）從「均勻於 RT」重採樣成「均勻於 RI」，讓熱圖 RI 軸**線性
    （非 log 刻度）** —— 因為 RI≈log10(RT)，直接在 RT 線性像素上標 RI 會擠成 log
    樣子。重採樣後 y extent 直接用 [ri_lo, ri_hi]、matplotlib 畫等距 RI 刻度。

    範圍：底部把 RT→0 的 -∞ 外插夾在「首錨 RI − 一格」，頂部保留到資料最上緣
    （含外插），故樣品中比末錨更晚洗出的峰不會被裁掉。

    參數
    ----
    img : ndarray            顯示影像（rows = y = RT 方向，均勻於 RT）
    row_rts : 1d array       每一列對應的保留時間（秒），遞增
    cal : dict               multi_point_loglinear 校正表
    n_out : int|None         輸出列數（預設同輸入）

    回傳
    ----
    (warped_img, ri_lo, ri_hi) — 非絕對校正時回 (img, None, None)
    """
    import numpy as np
    if not isinstance(cal, dict) or cal.get("mode") != "multi_point_loglinear":
        return img, None, None
    ri_values = cal.get("ri_values") or []
    if len(ri_values) < 2:
        return img, None, None
    r2r = make_rt_to_ri(cal)
    row_rts = np.clip(np.asarray(row_rts, dtype=float), 1e-6, None)
    src_ri = np.array([r2r(t)[0] for t in row_rts])
    if not np.all(np.diff(src_ri) > 0):              # 保險：確保單調遞增供 interp
        order = np.argsort(src_ri)
        src_ri, img = src_ri[order], img[order]
    ri_a_lo, ri_a_hi = float(min(ri_values)), float(max(ri_values))
    step = (ri_a_hi - ri_a_lo) / (len(ri_values) - 1)
    ri_lo = max(float(src_ri[0]), ri_a_lo - step)    # 夾住底部 -∞ 外插
    ri_hi = float(src_ri[-1])
    if ri_hi <= ri_lo:
        return img, None, None
    n = n_out or img.shape[0]
    ri_target = np.linspace(ri_lo, ri_hi, n)
    idx = np.interp(ri_target, src_ri, np.arange(img.shape[0]))
    lo = np.clip(np.floor(idx).astype(int), 0, img.shape[0] - 1)
    hi = np.clip(lo + 1, 0, img.shape[0] - 1)
    frac = (idx - lo)[:, None]
    warped = img[lo] * (1.0 - frac) + img[hi] * frac
    return warped.astype(img.dtype, copy=False), ri_lo, ri_hi


# --------------------------------------------------------------------------- #
# 套用到樣品峰（provenance 一路傳遞）
# --------------------------------------------------------------------------- #
def attach_ri(peaks, cal):
    """
    依校正表就地在每個 peak 加上 ri / ri_mode / ri_relative / ri_known_available /
    ri_series_used / ri_assumed_unverified / ri_reason。回傳原清單。

    **known_ri_available 與 assumed_unverified 必須跟著每筆 RI 走**：下游 Stage 5
    比對要繼承這些信心標記，日後拿到真對照表重跑時，才能一眼分辨哪些歷史結果
    是相對座標、哪些建立在未驗證系列假設之上。
    """
    mode = cal.get("mode", "unavailable")

    if mode == "unavailable":
        for p in peaks:
            p["ri"] = None
            p["ri_mode"] = "unavailable"
            p["ri_known_available"] = False
            p["ri_reason"] = cal.get("reason")
        return peaks

    if mode == "single_point_relative":
        rt_ref = cal["reference_rt_s"]
        for p in peaks:
            rt = p.get("retention_s")
            p["ri"] = None
            p["ri_mode"] = "single_point_relative"
            p["ri_known_available"] = False
            p["ri_relative"] = (relative_single_point(rt, rt_ref)
                                if rt and rt > 0 else None)
        return peaks

    if mode == "multi_point_loglinear":
        rt_to_ri = make_rt_to_ri(cal)              # 共用工廠（draft.21 清單第 7 點）
        rt_ref = cal["reference_rt_s"]
        assumed = cal.get("assumed_unverified", False)
        series = cal.get("series_used")
        confidence = cal.get("ri_confidence")
        for p in peaks:
            rt = p.get("retention_s")
            # 決策（draft.20/21）：錨點外「外插 + 標記」——值與旗標兩份資訊都保留
            ri, extrap = rt_to_ri(rt)
            p["ri"] = ri
            p["ri_extrapolated"] = extrap
            p["ri_relative"] = (relative_single_point(rt, rt_ref)
                                if rt and rt > 0 else None)
            p["ri_mode"] = "multi_point_loglinear"
            p["ri_known_available"] = True
            p["ri_series_used"] = series
            p["ri_assumed_unverified"] = assumed
            p["ri_confidence"] = confidence
        return peaks

    raise ValueError(f"未知校正模式：{mode!r}")


# --------------------------------------------------------------------------- #
# 高階整合：從 STD 峰清單一步到位（偵測 → 品質判定 → 建表）
# --------------------------------------------------------------------------- #
def build_from_std_peaks(std_peaks, header=None, series_key=None,
                         start_carbon=None, ri_values=None,
                         reference_n_anchors=None,
                         prominence_frac=0.05, doublet_gap_s=20.0,
                         resolve_doublets=True, min_top_intensity=2000,
                         expected_anchors=None, pin_tol_s=20.0):
    """
    串起 錨點選取 → assess_std → build_calibration，回傳 (calibration_dict, sel)。

    兩種錨點取得方式：
      - expected_anchors 給定 → **釘定模式**：pin_anchors() 把偵測峰對齊到已知的 M
        個錨點模板（如 6 個甲基酮），多的峰忽略、缺的標 missing，錨點數固定為模板數。
        模板每格帶 ri 時直接產生絕對 RI；否則走 single_point_relative。
      - expected_anchors 為 None → **動態模式**：select_anchor_peaks + （可選）
        resolve_anchor_doublets 自行挑錨點（數量隨資料而定）。

    series_key 預設 None（相對模式，第 9 點）。STD 不可用時 mode='unavailable'。
    """
    pin_report = None
    ladder_report = None
    series_ri = (rs.REFERENCE_SERIES.get(series_key, {}).get("ri_values")
                 if series_key else None)

    if expected_anchors:
        clean, pin_report = pin_anchors(std_peaks, expected_anchors, pin_tol_s)
        sel = {"clean_anchors": clean, "ambiguous_groups": []}
        resolution_log = []
        pinned_ris = [a.get("_pinned_ri") for a in clean]
        if clean and all(r is not None for r in pinned_ris):
            build_series = series_key or "custom"     # 模板帶 RI → 絕對
            build_ri_values = pinned_ris
        else:
            build_series = None                       # 缺 RI → 相對（釘定的 6 點）
            build_ri_values = None
    elif series_ri is not None and ri_values is None:
        # 系列已有固定 N 點 RI（如 methyl_ketone 6 點）→ 自動挑 N 個同系物階梯錨點，
        # 不需使用者提供模板 RT（呼應「選資料夾即自動找 6 峰」的流程）。
        clean, ladder_report = select_homolog_ladder(
            std_peaks, len(series_ri), prominence_frac=prominence_frac)
        sel = {"clean_anchors": clean, "ambiguous_groups": []}
        resolution_log = []
        build_series = series_key                     # assign_ri 從系列取 RI 值
        build_ri_values = None
    else:
        sel = select_anchor_peaks(std_peaks, prominence_frac, doublet_gap_s)
        clean = list(sel["clean_anchors"])
        resolution_log = []
        if resolve_doublets and sel["ambiguous_groups"]:
            clean, resolution_log = resolve_anchor_doublets(clean, sel["ambiguous_groups"])
        build_series = series_key
        build_ri_values = ri_values

    top_intensity = max((p.get("intensity") or 0 for p in std_peaks), default=None)
    quality = assess_std(header or {}, len(clean), reference_n_anchors,
                         top_intensity=top_intensity, min_top_intensity=min_top_intensity)

    if not quality["usable"]:
        cal = {
            "mode": "unavailable",
            "known_ri_available": False,
            "series_used": series_key,
            "assumed_unverified": False,
            "n_anchors": len(clean),
            "reason": "STD 不可用：" + "；".join(quality["reasons"]),
        }
    else:
        cal = build_calibration([p["retention_s"] for p in clean],
                                build_series, start_carbon, build_ri_values)

    cal["std_quality"] = quality
    cal["anchor_selection"] = {
        "mode": "pinned" if expected_anchors else "dynamic",
        "n_clean_anchors": len(clean),
        "clean_rt_s": [round(p["retention_s"], 3) for p in clean],
        "n_ambiguous_groups": len(sel["ambiguous_groups"]),
        "ambiguous_rt_s": [[round(p["retention_s"], 1) for p in g]
                           for g in sel["ambiguous_groups"]],
        "doublet_resolution": resolution_log,
        "top_intensity": top_intensity,
        "pinned": pin_report,
        "ladder": ladder_report,
    }
    return cal, sel


# --------------------------------------------------------------------------- #
# 批次資料夾 STD 解析（draft.18 §12：三層 + registry 借用 + provenance）
# --------------------------------------------------------------------------- #
def _read_header_lite(mea_path, max_bytes=32768):
    """輕量讀 .mea 純文字表頭（只讀前 max_bytes，不載入整個矩陣）。回傳 dict。"""
    with open(mea_path, "rb") as f:
        raw = f.read(max_bytes)
    header = {}
    for line in raw.decode("latin-1", errors="replace").split("\n"):
        if any(ord(c) < 32 and c not in "\t\r" for c in line):
            break                                        # 進入二進位資料區
        if "=" in line:
            k, _, v = line.partition("=")
            header[k.strip()] = v.strip().strip('"').strip()
    return header


def scan_folder_for_std(folder_path):
    """依表頭 Sample=='STD' 找 STD 檔（非檔名慣例，操作者可能漏打/打錯）。"""
    stds = []
    if not os.path.isdir(folder_path):
        return stds
    for f in sorted(glob.glob(os.path.join(folder_path, "*.mea"))):
        try:
            if _read_header_lite(f).get("Sample", "").strip().upper() == "STD":
                stds.append(f)
        except OSError:
            continue
    return stds


def extract_registry_dims(header):
    """從表頭抽出 registry key 的三維（儀器＋管柱＋方法），比照 Stage 2 綁定精神。"""
    machine = header.get("Machine type", "?")
    serial = header.get("Machine serial", "?")
    column = header.get("GC Column", "?")
    m = re.search(r"Name=`?([^`|]+)`?", header.get("Program", ""))
    method = m.group(1).strip() if m else "?"
    return {"instrument": f"{machine} {serial}".strip(), "column": column, "method": method}


def registry_key(instrument, column, method):
    return f"{instrument}|{column}|{method}"


def load_registry(registry_path):
    if not os.path.exists(registry_path):
        return {}
    with open(registry_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_registry(registry_path, registry):
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)


def _days_since(built_date_iso, today_date=None):
    """built_date_iso（ISO 字串）到今天的天數；解析失敗回 None。"""
    if not built_date_iso:
        return None
    try:
        bd = datetime.date.fromisoformat(str(built_date_iso)[:10])
    except ValueError:
        return None
    return ((today_date or datetime.date.today()) - bd).days


def _default_std_peaks_loader(mea_path, results_dir="results"):
    """預設：從 results/<base>_peaks.json 載 STD 峰（peaks.py 產出）。
    尚未偵測時回 (None, None)，讓上層記「請先跑 peaks.py」而非硬跑 83s 偵測。"""
    base = os.path.splitext(os.path.basename(mea_path))[0]
    pj = os.path.join(results_dir, base + "_peaks.json")
    if not os.path.exists(pj):
        return None, None
    return _load_peaks_json(pj)


def build_calibration_from_std_files(std_files, std_peaks_loader,
                                     series_key=None, start_carbon=None,
                                     ri_values=None, **kw):
    """
    對每支 STD 跑 build_from_std_peaks、過濾不可用者，回傳 (best_cal|None, evaluated)。
    多支可用時目前選乾淨錨點最多者；跨時間 bracket 加權列為未來增強（誠實標 note，
    不半套一個未驗證的時間加權）。
    """
    evaluated = []
    for f in std_files:
        peaks, header = std_peaks_loader(f)
        name = os.path.basename(f)
        if peaks is None:
            evaluated.append({"std": name, "usable": False,
                              "reason": "尚未偵測（缺 _peaks.json），請先跑 peaks.py"})
            continue
        cal, _ = build_from_std_peaks(peaks, header, series_key=series_key,
                                      start_carbon=start_carbon, ri_values=ri_values, **kw)
        usable = cal.get("mode") != "unavailable"
        evaluated.append({
            "std": name, "usable": usable,
            "n_clean_anchors": cal.get("anchor_selection", {}).get("n_clean_anchors"),
            "reason": None if usable else cal.get("reason"),
            "cal": cal,
        })

    usable = [e for e in evaluated if e["usable"]]
    if not usable:
        return None, evaluated
    best = max(usable, key=lambda e: e.get("n_clean_anchors") or 0)
    chosen = dict(best["cal"])
    if len(usable) > 1:
        chosen["bracket_note"] = (f"{len(usable)} 支可用 STD，目前選乾淨錨點最多者"
                                  "（{}）；跨時間 bracket 加權為未來增強".format(best["std"]))
    return chosen, evaluated


def resolve_ri_calibration(folder_path, dims=None,
                           registry_path="ri_calibration_registry.json",
                           std_peaks_loader=None, series_key=None,
                           start_carbon=None, ri_values=None,
                           max_days_gap=None, today_date=None, **kw):
    """
    三層解析（draft.18 §12），回傳 (calibration_dict|None, ri_mode, detail)。

    (a) 批次內有 STD 且至少一支可用       → ri_mode="batch_own_std"
    (b) 無可用 STD 但 registry 有同組合   → ri_mode="borrowed_from_registry"
        （帶 days_gap；max_days_gap 超過視同不可用）
    (c) 皆無                              → ri_mode="unavailable"（None）

    與 Stage 2 k0_mode 三態對稱；RI/K0 各自獨立標記 provenance，缺一不整峰放棄。
    dims: {"instrument","column","method"}，(b) 借用時才需要。
    std_peaks_loader(mea_path)->(peaks,header)；預設讀 results/<base>_peaks.json。
    """
    loader = std_peaks_loader or _default_std_peaks_loader
    std_files = scan_folder_for_std(folder_path)
    detail = {"std_files": [os.path.basename(f) for f in std_files]}

    # (a) 批次內有 STD
    if std_files:
        cal, evaluated = build_calibration_from_std_files(
            std_files, loader, series_key, start_carbon, ri_values, **kw)
        detail["evaluated"] = [{k: v for k, v in e.items() if k != "cal"}
                               for e in evaluated]
        if cal is not None:
            cal["ri_mode"] = "batch_own_std"
            return cal, "batch_own_std", detail
        detail["note_a"] = "資料夾內有 STD 但皆不可用，改試 registry"

    # (b) registry 借用
    if dims:
        registry = load_registry(registry_path)
        key = registry_key(dims["instrument"], dims["column"], dims["method"])
        detail["registry_key"] = key
        entry = registry.get(key)
        if entry:
            days_gap = _days_since(entry.get("built_date"), today_date)
            detail["days_gap"] = days_gap
            if max_days_gap is not None and days_gap is not None and days_gap > max_days_gap:
                detail["note_b"] = f"days_gap={days_gap} > 上限 {max_days_gap}，視同不可用"
            else:
                cal = dict(entry.get("calibration") or {})
                cal["ri_mode"] = "borrowed_from_registry"
                cal["ri_confidence_note"] = f"days_gap={days_gap}, 非本批次自建校正"
                return cal, "borrowed_from_registry", detail

    # (c) 皆無
    return None, "unavailable", detail


def stamp_ri_provenance(peaks, cal, ri_mode):
    """attach_ri 之後，把解析層的來源 provenance（ri_mode/ri_confidence_note）也
    蓋到每個峰上（point 12 的 ri_mode 是「來源」，與 attach_ri 寫的『校正方法』
    ri_mode 不同層次，故另存 ri_source 欄位避免覆蓋，並附信心註記）。"""
    note = cal.get("ri_confidence_note") if isinstance(cal, dict) else None
    for p in peaks:
        p["ri_source"] = ri_mode
        if note:
            p["ri_confidence_note"] = note
    return peaks


# 本 session 內同一資料夾只解析一次（§10）
_SESSION_CACHE = {}


def resolve_ri_calibration_cached(folder_path, dims=None, use_sidecar=True,
                                  sidecar_name="_folder_calibration.json", **kw):
    """
    §10 資料夾層級快取：session 記憶體 → sidecar 檔（依 .mea mtime 判新鮮）→ 現算。
    回傳 (calibration_dict|None, ri_mode, detail)。sidecar 寫在資料夾內（GAS/ 已被
    .gitignore，不會進版控）。
    """
    if folder_path in _SESSION_CACHE:
        return _SESSION_CACHE[folder_path]

    sidecar = os.path.join(folder_path, sidecar_name)
    if use_sidecar and os.path.exists(sidecar) and _sidecar_fresh(sidecar, folder_path):
        with open(sidecar, "r", encoding="utf-8") as f:
            saved = json.load(f)
        result = (saved.get("calibration"), saved.get("ri_mode"), saved.get("detail", {}))
        _SESSION_CACHE[folder_path] = result
        return result

    cal, ri_mode, detail = resolve_ri_calibration(folder_path, dims=dims, **kw)
    if use_sidecar and os.path.isdir(folder_path):
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump({"calibration": cal, "ri_mode": ri_mode, "detail": detail},
                      f, ensure_ascii=False, indent=2)
    result = (cal, ri_mode, detail)
    _SESSION_CACHE[folder_path] = result
    return result


def _sidecar_fresh(sidecar, folder_path):
    """sidecar 比資料夾內所有 .mea 都新 → 視為仍有效。"""
    smt = os.path.getmtime(sidecar)
    return all(os.path.getmtime(f) < smt
               for f in glob.glob(os.path.join(folder_path, "*.mea")))


def clear_session_cache():
    """清掉 session 快取（測試/換批次時用）。"""
    _SESSION_CACHE.clear()


# --------------------------------------------------------------------------- #
# 檔案 I/O + CLI
# --------------------------------------------------------------------------- #
def _load_peaks_json(path):
    """讀 peaks.py 的 _peaks.json，回傳 (peaks_list, header_dict)。"""
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    if isinstance(d, list):
        return d, {}
    header = {k: d[k] for k in ("Status", "Status comment", "sample", "machine")
              if k in d}
    return d.get("peaks", []), header


def main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(
        description="第四階段 RT→RI 校正：從 STD 的 _peaks.json 建校正表"
                    "（預設相對模式；給 --series 才升級成絕對 RI）")
    ap.add_argument("std_peaks_json", help="STD 的 results/<name>_peaks.json")
    ap.add_argument("--series", default=None, choices=rs.list_series(),
                    help="參照系列；省略＝single_point_relative 相對模式（預設）")
    ap.add_argument("--start-carbon", type=int,
                    help="n_alkane 起始碳數（覆寫暫定 C6，第二層假設）")
    ap.add_argument("--ri-values", help="table 類系列的各錨點 RI，逗號分隔")
    ap.add_argument("--ref-peaks-json", help="對照 STD 的 _peaks.json（品質比對）")
    ap.add_argument("--prominence-frac", type=float, default=0.05)
    ap.add_argument("--doublet-gap-s", type=float, default=20.0)
    ap.add_argument("--out", help="輸出校正表 JSON 路徑（省略 → 只印報告）")
    args = ap.parse_args(argv)

    peaks, header = _load_peaks_json(args.std_peaks_json)

    ref_n = None
    if args.ref_peaks_json:
        ref_peaks, _ = _load_peaks_json(args.ref_peaks_json)
        ref_n = len(select_anchor_peaks(
            ref_peaks, args.prominence_frac, args.doublet_gap_s)["clean_anchors"])

    ri_values = ([float(x) for x in args.ri_values.split(",")]
                 if args.ri_values else None)

    cal, _ = build_from_std_peaks(
        peaks, header, series_key=args.series, start_carbon=args.start_carbon,
        ri_values=ri_values, reference_n_anchors=ref_n,
        prominence_frac=args.prominence_frac, doublet_gap_s=args.doublet_gap_s)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(json.dumps(cal, ensure_ascii=False, indent=2))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(cal, f, ensure_ascii=False, indent=2)
        print(f"\n已寫出校正表：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

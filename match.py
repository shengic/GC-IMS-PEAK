"""
match.py  —  GC-IMS Identify Workflow 第五階段：容許窗比對
Version: 3.0 — by Albert Sheng

依 GC-IMS_Identify_Workflow.md §第五階段：
  - 核心：|library_value - peak_value| <= tolerance
  - 兩個獨立維度（可再加 RT 退路第三維度）：
      RI 維度   peak_ri  vs  library RI 欄位
      K0 維度   peak_k0  vs  library "Dt[a.u.]" 欄位（先篩 DtMode 含 "K0"）
      RT 退路   peak_retention_s vs library "Rt[sec]"（.iml 才有）
  - 輸出三個候選清單（workflow §第五階段第 3 點）：
      gc_matches       只有 RI（或 RT 退路）命中
      ims_matches      只有 K0 命中
      combined_matches 兩者都命中且為同一 CAS 的交集

比對候選格式：每筆為 library row 的淺拷貝 + 幾個附加欄位：
    delta_ri / delta_rt / delta_k0   離 peak 中心的絕對差
    match_dimensions                 e.g. ["ri"], ["k0"], ["ri", "k0"]
source_file 由 library.py 附在 library row 上，比對時複製 dict 自動傳遞（見
workflow §第三階段第 4 點），不需要在此另外處理。

[待決策] 容許窗寬度：
  - workflow §第五階段第 4 點列為 unresolved
  - 目前預設值只是佔位（RI ±10、Rt ±5s、K0 ±0.05），實測校準時需調整
  - 呼叫者可個別覆寫；或未來擴充成依 peak 的 flatness/prominence 動態調整

依賴：僅 Python stdlib
"""


# --------------------------------------------------------------------------- #
# 預設容許窗寬度（佔位值，未校準；workflow §第五階段第 4 點）
# --------------------------------------------------------------------------- #
DEFAULT_RI_TOLERANCE = 5.0     # RI 絕對容差（單位）
DEFAULT_RT_TOLERANCE = 5.0     # 秒
DEFAULT_K0_TOLERANCE = 0.05
DEFAULT_DRIFTREL_TOLERANCE = 0.05   # 漂移相對 RIP（RIPrel）；佔位值，未校準


# --------------------------------------------------------------------------- #
# 逐維度比對函式
# --------------------------------------------------------------------------- #
def match_ri(peak_ri, library_rows, tolerance=DEFAULT_RI_TOLERANCE):
    """
    以 peak_ri 為中心、tolerance 為半寬，篩出 library rows 裡 RI 命中的候選。

    參數
    ----
    peak_ri : float | None
        該峰的 RI 值（由 calibration.py 第四階段換算；未實作前可能為 None）。
    library_rows : list[dict]
        library.load_ril_many() / load_iml_many() 產出，需含 "RI" 欄位。
    tolerance : float

    回傳
    ----
    list[dict]
        依 delta_ri 遞增排序（最相近在前）。每筆為 row 淺拷貝 + delta_ri +
        match_dimensions=["ri"]。
    """
    if peak_ri is None:
        return []
    hits = []
    for row in library_rows:
        ri = row.get("RI")
        if ri is None:
            continue
        delta = abs(ri - peak_ri)
        if delta <= tolerance:
            m = dict(row)
            m["delta_ri"] = delta
            m["match_dimensions"] = ["ri"]
            hits.append(m)
    hits.sort(key=lambda x: x["delta_ri"])
    return hits


def match_rt(peak_retention_s, library_rows, tolerance=DEFAULT_RT_TOLERANCE):
    """
    RT 退路（workflow §第四階段第 5 點）：無 RI 校準資料時，直接以保留時間
    秒數比對 library "Rt[sec]" 欄位。只有 .iml 才存 Rt，`.ril` row 會被略過。

    回傳格式同 match_ri，欄位改為 delta_rt / match_dimensions=["rt"]。
    """
    if peak_retention_s is None:
        return []
    hits = []
    for row in library_rows:
        rt = row.get("Rt[sec]")
        if rt is None:
            continue
        delta = abs(rt - peak_retention_s)
        if delta <= tolerance:
            m = dict(row)
            m["delta_rt"] = delta
            m["match_dimensions"] = ["rt"]
            hits.append(m)
    hits.sort(key=lambda x: x["delta_rt"])
    return hits


def match_k0(peak_k0, library_rows, tolerance=DEFAULT_K0_TOLERANCE):
    """
    以 peak_k0 為中心、tolerance 為半寬，篩出 library rows 裡 K0 命中的候選。

    須先透過 DtMode 篩選：只保留 DtMode 含 "K0" 的 row（表示該 row 的
    "Dt[a.u.]" 存的是 1/K0 或 K0，可跟本專案 dt_convert.compute_k0 產出的值
    做同單位比對）；其他 DtMode（Dt 直接以毫秒等別的單位存）跳過。

    參數
    ----
    peak_k0 : float | None
        該峰的 K0 值（由 dt_convert.compute_k0 產出）。None → 回空清單。
    library_rows : list[dict]
        通常為 .iml 讀取結果（.ril 沒有 Dt/DtMode 欄位）。
    tolerance : float

    回傳
    ----
    list[dict]
        依 delta_k0 遞增排序。欄位為 delta_k0 / match_dimensions=["k0"]。
    """
    if peak_k0 is None:
        return []
    hits = []
    for row in library_rows:
        dt = row.get("Dt[a.u.]")
        if dt is None:
            continue
        dtmode = (row.get("DtMode") or "").strip()
        if "K0" not in dtmode:
            # DtMode 非 K0 相關（可能是別的漂移時間表達法），跳過避免單位混亂
            continue
        delta = abs(dt - peak_k0)
        if delta <= tolerance:
            m = dict(row)
            m["delta_k0"] = delta
            m["match_dimensions"] = ["k0"]
            hits.append(m)
    hits.sort(key=lambda x: x["delta_k0"])
    return hits


def match_drift_rel(peak_drift_rel, library_rows, tolerance=DEFAULT_DRIFTREL_TOLERANCE):
    """
    漂移軸（IMS）比對的**免 K0 路線**：直接用峰的 drift_relative（相對 RIP）比對
    library 中 DtMode == "RIPrel" 的 Dt[a.u.]（同樣是相對 RIP 的漂移值，單位一致、
    可直接比）。不需要 K0 校準即可運作。

    只在 DtMode 精確為 "RIPrel" 的 row 上比對——其他 DtMode（1/K0、溫度欄位錯位
    等）單位不同，混比會出錯，一律跳過。

    參數
    ----
    peak_drift_rel : float | None   峰的 drift_relative（rip.attach_drift_relative 補上）
    library_rows : list[dict]       通常為 .iml 讀取結果
    tolerance : float

    回傳
    ----
    list[dict]  依 delta_drift_rel 遞增排序；欄位 delta_drift_rel /
                match_dimensions=["drift_rel"]。
    """
    if peak_drift_rel is None:
        return []
    hits = []
    for row in library_rows:
        if (row.get("DtMode") or "").strip() != "RIPrel":
            continue
        dt = row.get("Dt[a.u.]")
        if dt is None:
            continue
        delta = abs(dt - peak_drift_rel)
        if delta <= tolerance:
            m = dict(row)
            m["delta_drift_rel"] = delta
            m["match_dimensions"] = ["drift_rel"]
            hits.append(m)
    hits.sort(key=lambda x: x["delta_drift_rel"])
    return hits


# --------------------------------------------------------------------------- #
# 交集：同一 CAS 出現在 GC 命中與 IMS 命中兩邊 → combined
# --------------------------------------------------------------------------- #
def combine_by_cas(gc_hits, ims_hits):
    """
    以 CAS 為 key 找 gc_hits 與 ims_hits 的交集。

    - 若同 CAS 在 GC 命中出現多筆（.ril 多來源時可能），取 delta 最小者代表
    - 同理 IMS 命中
    - 合併後 row 兼容兩邊的 delta 欄位與 match_dimensions（e.g. ["ri", "k0"]）
    - source_file 分別保留成 source_file_gc / source_file_ims，避免哪邊被覆寫

    參數
    ----
    gc_hits : list[dict]   從 match_ri() 或 match_rt() 來
    ims_hits : list[dict]  從 match_k0() 來

    回傳
    ----
    list[dict]
        依 combined delta 排序（delta_ri + delta_k0 加總；未有的維度以 0 計）
    """
    def _best_by_cas(hits):
        best = {}
        for h in hits:
            cas = h.get("CAS")
            if not cas:
                continue
            # 取排序後第一筆（外層已 sort by delta），同 CAS 已排序在前即為最佳
            if cas not in best:
                best[cas] = h
        return best

    gc_by_cas = _best_by_cas(gc_hits)
    ims_by_cas = _best_by_cas(ims_hits)

    combined = []
    for cas, gc_row in gc_by_cas.items():
        if cas not in ims_by_cas:
            continue
        ims_row = ims_by_cas[cas]
        merged = dict(gc_row)   # 從 gc 那邊起 base（多半資訊較豐富）
        # 保留兩邊 delta
        for k in ("delta_ri", "delta_rt", "delta_k0", "delta_drift_rel"):
            if k in ims_row and k not in merged:
                merged[k] = ims_row[k]
        # 合併 match_dimensions（去重、保序）
        dims = list(dict.fromkeys(
            gc_row.get("match_dimensions", []) + ims_row.get("match_dimensions", [])
        ))
        merged["match_dimensions"] = dims
        # source_file 分列，避免覆寫（workflow §第三階段第 4 點：溯源）
        merged["source_file_gc"] = gc_row.get("source_file")
        merged["source_file_ims"] = ims_row.get("source_file")
        # 也保留一個聚合 source_file（若某邊沒有 → None）供 UI 便利顯示
        merged.pop("source_file", None)
        combined.append(merged)

    def _combined_delta(m):
        return (m.get("delta_ri", 0) + m.get("delta_rt", 0)
                + m.get("delta_k0", 0) + m.get("delta_drift_rel", 0))
    combined.sort(key=_combined_delta)
    return combined


# --------------------------------------------------------------------------- #
# 便利套件：一口氣三分支
# --------------------------------------------------------------------------- #
def match_all(peak, ril_rows, iml_rows,
              ri_tolerance=DEFAULT_RI_TOLERANCE,
              rt_tolerance=DEFAULT_RT_TOLERANCE,
              k0_tolerance=DEFAULT_K0_TOLERANCE,
              driftrel_tolerance=DEFAULT_DRIFTREL_TOLERANCE):
    """
    對一顆峰同時跑 GC（RI/RT）與 IMS（K0 或 RIPrel 漂移）兩個維度比對，回傳
    workflow §第五階段第 3 點的三個候選清單。

    GC 分支優先策略：
      - peak["ri"] 有值 → RI 比對（.ril + .iml 合併作為 gc 候選庫）
      - 否則 peak["retention_s"] 有值 → RT 退路（僅 .iml 有 Rt[sec]）

    IMS 分支優先策略：
      - peak["k0_value"] 有值 → K0 比對（library DtMode 含 "K0"，需 K0 校準）
      - 否則 peak["drift_relative"] 有值 → **RIPrel 漂移比對**（library
        DtMode == "RIPrel"，與峰的 drift_relative 同為相對 RIP，免 K0 校準）

    回傳
    ----
    dict：
      {
        "gc_matches", "ims_matches", "combined_matches",
        "gc_dimension":  "ri" | "rt" | None,
        "ims_dimension": "k0" | "drift_rel" | None,   (實際用了哪個 IMS 維度)
      }
    """
    # GC 分支：優先 RI
    gc_dimension = None
    gc_hits = []
    if peak.get("ri") is not None:
        gc_hits = match_ri(peak["ri"], ril_rows + iml_rows, ri_tolerance)
        gc_dimension = "ri"
    elif peak.get("retention_s") is not None:
        gc_hits = match_rt(peak["retention_s"], iml_rows, rt_tolerance)
        gc_dimension = "rt" if gc_hits else None

    # IMS 分支：優先 K0，否則走免校準的 RIPrel 漂移
    ims_dimension = None
    if peak.get("k0_value") is not None:
        ims_hits = match_k0(peak["k0_value"], iml_rows, k0_tolerance)
        ims_dimension = "k0" if ims_hits else None
    elif peak.get("drift_relative") is not None:
        ims_hits = match_drift_rel(peak["drift_relative"], iml_rows, driftrel_tolerance)
        ims_dimension = "drift_rel" if ims_hits else None
    else:
        ims_hits = []

    combined = combine_by_cas(gc_hits, ims_hits)

    return {
        "gc_matches": gc_hits,
        "ims_matches": ims_hits,
        "combined_matches": combined,
        "gc_dimension": gc_dimension,
        "ims_dimension": ims_dimension,
    }

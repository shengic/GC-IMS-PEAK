"""
areas2.py  —  第二支應用：跨檔案「區域強度矩陣」（VOCal MeasAreas 模式）
Version: 1.0 — by Albert Sheng

第二支應用自己的版本序列，從 1.0 起算——與第一支應用的 3.x 無關，兩者各自演進。

1.0 的開發過程修掉幾個值得記住的問題（都是實際使用回報的，不是預想的）：
  - `build_matrix` 原本 `raise SystemExit`。那是 `BaseException`，背景執行緒的
    `except Exception` 接不到 → 執行緒無聲死掉、佇列永遠空著、UI 一直等，畫面全空
    而且沒有任何錯誤訊息。改為 `NoSamplesFound` / `BatchCancelled`（皆為 Exception）。
  - `Class` 分組原本只在 `from_gasprj` 時才讀，共識模式整組遺失，UI 欄位標題因此全是
    `?`。分組是**樣品的 metadata**，與區域從哪裡來無關，現在一律讀。
  - 分組要取 `Class` 的第一個 token：該欄存的是每個重複樣品的代號（`A 1-1`/`A 1-2`），
    照字面分會變成 13 組、每組 n=1，完全失去組間比較的意義。
  - `skip_detect`：找峰佔整批時間 **75%**（實測單檔 union-find 55.4 s、基線 16.8 s、
    其餘 1.8 s），而區域來自 `.gasprj` 時找峰只用來填 `n_det` 欄，可整段跳過
    （74 s → 1.5 s）。搭配共識模式會直接報錯——那時候區域正是從偵測到的峰長出來的。


**這是第二支應用的核心邏輯，與既有第一支應用完全隔離。**
`main.py` 與所有既有模組一律**只 import、不修改**；本模組的產物一律帶 `2` 尾綴，
不可能覆蓋第一支應用的任何檔案（見下方「產物與隔離」）。

為什麼需要這支
--------------
既有應用一次分析**一個** `.mea`：熱圖 → 找峰 → 逐峰比對。實際拆解
`GAS/Coffee-bean/rawbean-20260625.gasprj` 後發現 VOCal 的模型不同，而且差別是結構性的：

  - 一個 `.gasprj` 是**跨多個 `.mea` 的專案**（該檔 15 個），並用 `Class` 欄分成
    A/B/C/D/E 五組、每組 3 重複。
  - 它存了 **57 個 `MeasAreas`**：在 (漂移, 保留) 平面上**畫一次**、然後在**每一個檔案**
    上都量一次的固定方框。
  - 兩個座標本專案都已經有了：`RipRel` 就是我們的 `drift_relative`；`SpecNum` 就是我們的
    `rt_index`（實測：方框中心 × `(averages+1) × trigger_repetition` 能重現 VOCal 自己
    報的 `Rt[sec]`，22 個化合物比值 0.992–1.005）。
  - **VOCal 不逐檔重新找峰**。同一組座標在所有檔案上量，這才是分組比較的前提——各檔
    獨立找出來的峰清單彼此對不齊，就沒有辦法比較 A 組與 B 組。
  - **命名是選配的**：57 個區域只有 27 個有化合物名，其餘 30 個就叫 `area 6`、`area 7`…
    照樣每個樣品都量。

本模組要產出的就是那個矩陣：**每個區域 × 每個檔案**都有值，同時保留既有的自動找峰、
漂移/RI 座標正規化與 6 點 STD 校正。

產物與隔離（**改動前務必讀**）
------------------------------
三個隔離風險，各自的處理方式：

1. **扣過基線的資料絕不可寫回共用的 `.npz`。** 基線扣除只在記憶體內做。若把扣完的面
   寫回 `<base>.npz`，`main.py` 會把它當成原始資料載入而毫無跡象——這正是本專案一再
   防範的無聲污染。`.npz` 永遠是原始值，兩支應用共用（內容與哪支寫的無關），也省下
   重複約 480 MB。
2. **不寫任何第一支應用的逐檔產物。** 本模組**以函式呼叫** `peaks.detect_peaks()` /
   `select_from_maxima()`，**不**用 subprocess 跑 `peaks.py`——那支 CLI 會寫
   `_peaks.json` / `_peaks.csv` / `_maxima.npz` / `_bg.png` / `_bg.json` / `_overlay.png`，
   那些屬於第一支應用。本模組**讀** `_maxima.npz`（83 秒 → 4 毫秒的快路徑）但絕不寫。
3. **不寫任何東西進 `GAS/`。** `resolve_calibrations_cached(..., use_sidecar=False)`
   ——與 `main.py` 相同的呼叫——所以不會產生或更新 `_folder_calibration.json`。
   `.mea` 與 `.gasprj` 一律唯讀開啟。

本模組寫出的檔案（全部在 `results/`，全部帶 `2`）：

    <base>_peaks2.json            逐檔找峰結果 + 參數
    <folder>_areas2.json          區域定義 + 完整 provenance
    <folder>_area_matrix2.csv     矩陣本身

依賴：既有的 readGAS / peaks / rip / rules / calibration / identify / match / baseline。
"""

import argparse
import datetime
import glob
import json
import os
import re
import sys

import numpy as np

import baseline as _baseline
import calibration
import identify
import library
import match as match_mod
import peaks as peaks_mod
import readGAS
import rip as rip_mod
import rules

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(PROJECT_DIR, "results")
RULES_CONFIG = os.path.join(PROJECT_DIR, "rules_config.json")

# 參照系列：與 main.py 的 RI_SERIES 相同值，但**不 import main.py**（兩支 UI 保持獨立，
# 任一支壞掉不影響另一支）。這是刻意的重複，不是疏漏。
RI_SERIES = "ketone"

# --------------------------------------------------------------------------- #
# 群聚容差 —— 取自 VOCal 自己畫的方框尺寸中位數，不是憑感覺選的。
# 實測 rawbean-20260625.gasprj 的 57 個區域：
#     DriftRange   最小 0.0218  中位 0.0313  最大 0.0893
#     Elution 寬度 最小 62      中位 132     最大 577  （SpecNum）
#                  換算秒數     9.1          19.4      84.8
# 方框畫得寬是有理由的：GC 保留時間逐次量測本來就會漂，方框要吃得下這個漂移。
# 我們的方框改為**由資料決定**（群集的實際範圍 + 邊距），下限比照上面的中位數。
# --------------------------------------------------------------------------- #
DEFAULT_DRIFT_TOL = 0.03      # 群聚：|Δdrift_relative| ≤ 此值視為同一區域
DEFAULT_RT_TOL_S = 10.0       # 群聚：|Δretention_s| ≤ 此值視為同一區域
MIN_DRIFT_HALF = 0.02         # 方框半寬下限（漂移）
MIN_RT_HALF_S = 8.0           # 方框半寬下限（保留時間，秒）
DEFAULT_MIN_FILES = 2         # 至少在幾個檔案裡出現才算一個區域（共識過濾）


def log(msg):
    print(f"[{__import__('time').strftime('%H:%M:%S')}] {msg}", flush=True)


def _use_utf8_stdout():
    """Windows 主控台預設 cp950，印不出中文就整支崩掉。與既有模組同慣用法。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# .gasprj 讀取（唯讀）—— 專案成員、Class 分組、既有區域定義
# --------------------------------------------------------------------------- #
def read_gasprj_entries(path):
    r"""讀 `.gasprj` 的 `Project.Data.Entries`，回傳 [{basename, class, path}, ...]。

    `Path` 是**產生該專案那台機器**的絕對路徑（實測 `D:\GAS-TEST\...`），本機不存在，
    所以一律以 basename 對應本地檔案，不要拿原路徑去開檔。
    """
    with open(path, "r", encoding="utf-8-sig") as f:
        doc = json.load(f)
    out = []
    for e in (doc.get("Project", {}).get("Data", {}).get("Entries") or []):
        raw = (e.get("Path") or "").replace("\\", "/")
        if not raw:
            continue
        out.append({"basename": os.path.basename(raw),
                    "class": (e.get("Class") or "").strip(),
                    "original_path": e.get("Path")})
    return out


def read_gasprj_areas(path, rt_step_s):
    """讀 `.gasprj` 的 `MeasAreas`，轉成本模組的區域格式。

    VOCal 的座標系與我們的對應關係（實測驗證，見檔頭）：
        DriftValType   "RipRel"  → drift_relative，DriftCenter ± DriftRange
        ElutionValType "SpecNum" → rt_index，× rt_step_s 得秒數

    `rt_step_s` 必須由呼叫端從表頭算出（`(averages+1) × trigger_repetition / 1000`），
    本函式不猜——猜錯會讓整組方框在保留時間軸上平移而毫無跡象。

    只接受 `RipRel` + `SpecNum`；其他座標型別直接跳過並記在回傳的 report 裡，
    不臆測換算（同 `calibration.read_gasprj_ri_table()` 對 `ColNormisLog` 的處理）。
    """
    with open(path, "r", encoding="utf-8-sig") as f:
        doc = json.load(f)
    areas, skipped = [], []
    for i, a in enumerate(doc.get("MeasAreas", {}).get("Data") or []):
        if a.get("DriftValType") != "RipRel" or a.get("ElutionValType") != "SpecNum":
            skipped.append({"name": a.get("Name"), "drift_type": a.get("DriftValType"),
                            "elution_type": a.get("ElutionValType")})
            continue
        try:
            dc, dr = float(a["DriftCenter"]), float(a["DriftRange"])
            es, ee = float(a["ElutionStart"]), float(a["ElutionEnd"])
        except (KeyError, TypeError, ValueError):
            skipped.append({"name": a.get("Name"), "reason": "malformed numbers"})
            continue
        areas.append({
            "area_id": i + 1,
            "name": a.get("Name") or f"area {i + 1}",
            "drift_center": round(dc, 5),
            "drift_half": round(dr, 5),
            "rt_center_s": round((es + ee) / 2.0 * rt_step_s, 3),
            "rt_half_s": round(abs(ee - es) / 2.0 * rt_step_s, 3),
            "spec_start": int(es), "spec_end": int(ee),
            "source": "gasprj",
        })
    return areas, {"gasprj": os.path.basename(path), "n_areas": len(areas),
                   "n_skipped": len(skipped), "skipped": skipped,
                   "rt_step_s": rt_step_s}


def header_rt_step_s(header):
    """由表頭算每個 chunk 的保留時間間隔（秒）。

    `(averages + 1) × trigger_repetition` —— 少了 `+1` 整條 RT 軸會短 16.7% 且不會有
    任何錯誤訊息，見 `readGAS.RT_AXIS_VERSION` 的四條佐證。這裡不自己重寫公式的
    來源，只是套用同一條規則。
    """
    def num(key, default):
        m = re.search(r"-?\d+\.?\d*", header.get(key, "") or "")
        return float(m.group()) if m else default
    return (num("Chunk averages", 1.0) + 1.0) * num("Chunk trigger repetition", 30.0) / 1000.0


# --------------------------------------------------------------------------- #
# 階段 A：逐檔找峰（沿用既有管線，不改一行）
# --------------------------------------------------------------------------- #
def _npz_path(mea_path):
    return os.path.join(RESULTS_DIR, os.path.splitext(os.path.basename(mea_path))[0] + ".npz")


def _peaks2_path(mea_path):
    return os.path.join(RESULTS_DIR,
                        os.path.splitext(os.path.basename(mea_path))[0] + "_peaks2.json")


def ensure_npz(mea_path, verbose=True):
    """確保 `results/<base>.npz` 存在；缺就從 `.mea` 產生。回傳 npz 路徑。

    **`.npz` 是兩支應用共用的原始強度快取**，內容與哪支寫的無關（都是 `.mea` 的無損
    複本），所以共用是安全的，也省下重複約 480 MB。**扣過基線的資料永遠不會寫進來**
    ——見檔頭隔離風險第 1 點。
    """
    npz = _npz_path(mea_path)
    if os.path.exists(npz):
        return npz
    if verbose:
        log(f"  讀取 .mea（約 13 s）：{os.path.basename(mea_path)}")
    data, header, axes = readGAS.read_mea(mea_path)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    readGAS.export_npz(data, axes, npz)
    return npz


def detect_one(mea_path, rules_config, use_baseline=True, baseline_lam=None,
               baseline_stride=8, prom_frac=0.02, sigma=1.0, floor_pct=85.0,
               min_distance=3, reuse_cache=True, verbose=True):
    """對單一 `.mea` 跑既有找峰管線，回傳 (peaks, stats, meta)。

    與 `peaks.py` CLI 的差別**只在產物**：本函式以函式呼叫既有實作，不寫任何第一支
    應用的檔案（見檔頭隔離風險第 2 點）。演算法、規則、門檻順序完全相同。

    `reuse_cache=True` 時優先讀本模組自己的 `<base>_peaks2.json`（毫秒級）；沒有才
    真的跑偵測（約 83 s）。
    """
    cache = _peaks2_path(mea_path)
    if reuse_cache and os.path.exists(cache):
        with open(cache, "r", encoding="utf-8") as f:
            doc = json.load(f)
        if doc.get("params", {}).get("baseline_applied") == bool(use_baseline):
            return doc["peaks"], doc["stats"], doc["meta"]

    npz = ensure_npz(mea_path, verbose=verbose)
    intensity, drift_ms, retention_s, meta = peaks_mod.load_surface(npz)

    baseline_info = None
    if use_baseline:
        lam = baseline_lam if baseline_lam is not None else _baseline.DEFAULT_LAM
        if verbose:
            log(f"  基線扣除（AsLS, λ={lam:g}, stride={baseline_stride}）…")
        # **只在記憶體內**——絕不寫回 .npz
        intensity, baseline_info = _baseline.correct_rt_baseline(
            intensity, lam=lam, row_stride=baseline_stride)

    rip_index, rip_intensity = rip_mod.find_rip(intensity)
    half_w, excl_before, boundary = peaks_mod.pre_gate_params(rules_config)
    if verbose:
        log(f"  偵測中（RIP dt_index={rip_index}）…")
    pk, stats = peaks_mod.detect_peaks(
        intensity, sigma=sigma, floor_pct=floor_pct, prom_frac=prom_frac,
        min_distance=min_distance, rip_index=rip_index, rip_half_width=half_w,
        exclude_before_rip=excl_before, rip_boundary=boundary)
    peaks_mod.attach_coords(pk, drift_ms, retention_s)
    rip_mod.attach_drift_relative(pk, rip_index)
    rules.mark_rules(pk, rules_config,
                     context={"floor": stats.get("floor"), "rip_index": rip_index})
    for p in pk:
        p["active"] = p.get("rule_active", True)
        p.pop("_val_smooth", None)

    stats["rip_index"] = rip_index
    stats["rip_intensity"] = rip_intensity
    meta = dict(meta)
    meta["mea"] = os.path.abspath(mea_path)
    meta["n_rt"], meta["n_dt"] = int(intensity.shape[0]), int(intensity.shape[1])

    doc = {"source": os.path.abspath(mea_path),
           "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
           "params": {"sigma": sigma, "floor_pct": floor_pct, "prom_frac": prom_frac,
                      "min_distance": min_distance, "baseline_applied": bool(use_baseline),
                      "baseline": baseline_info},
           "stats": stats, "meta": meta, "peaks": pk}
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return pk, stats, meta


# --------------------------------------------------------------------------- #
# 階段 C：共識區域 —— 把各檔的峰群聚成「所有檔案共用的一組方框」
# --------------------------------------------------------------------------- #
def build_consensus_areas(per_file_peaks, drift_tol=DEFAULT_DRIFT_TOL,
                          rt_tol_s=DEFAULT_RT_TOL_S, min_files=DEFAULT_MIN_FILES,
                          min_drift_half=MIN_DRIFT_HALF, min_rt_half_s=MIN_RT_HALF_S,
                          active_only=True):
    """把多個檔案的峰群聚成共用區域，回傳 (areas, report)。

    **這是本模組唯一的新演算法**，其餘都是既有實作的組合。

    為什麼在 `(drift_relative, retention_s)` 空間做，而不是 `(dt_index, rt_index)`：
    同一批次各檔的 chunk 數並不相同（實測 Coffee-bean：20413 ×10、20414 ×4、
    另有一支只有 18372），所以 `rt_index` 不能直接跨檔比較；而 `rt_step` 相同、
    `drift_relative` 又已用各檔自己的 RIP 正規化過，兩者都是**跨檔可比**的物理量。
    這也正是 VOCal 用 RipRel 而不是原始漂移毫秒的理由。

    作法：依突出度降冪掃過所有峰，每顆峰若落在既有群集的容差內就併入，否則自成新群集。
    貪婪、單趟、O(n·k)，對這個規模（數十檔 × 數十峰）綽綽有餘，而且結果與輸入順序
    無關的程度足夠——因為排序鍵是突出度，最強的峰先定錨。

    **共識過濾**：只保留出現在 ≥ `min_files` 個檔案的群集。單檔雜訊因此被擋掉，而
    真實化合物在重複樣品裡本來就會重複出現。這一步是「跨檔」相對於「逐檔」真正多出來
    的資訊。

    **方框大小由資料決定**：群集在兩軸上的實際範圍 + 邊距，並套用下限
    （`min_drift_half` / `min_rt_half_s`，取自 VOCal 方框尺寸中位數）。這樣方框自然
    吃得下該化合物在這批樣品裡的保留時間漂移，不必手動調。

    參數
    ----
    per_file_peaks : dict[str, list[dict]]   {檔案 basename: 峰清單}
    active_only : bool  只用通過規則的峰（與第一支應用畫圈的集合一致）
    """
    pooled = []
    for fname, pks in per_file_peaks.items():
        for p in pks:
            if active_only and not p.get("active", True):
                continue
            if p.get("drift_relative") is None or p.get("retention_s") is None:
                continue
            pooled.append((float(p["prominence"]), fname, p))
    pooled.sort(key=lambda t: -t[0])

    clusters = []
    for _prom, fname, p in pooled:
        dr, rt = p["drift_relative"], p["retention_s"]
        for c in clusters:
            if abs(dr - c["dr_seed"]) <= drift_tol and abs(rt - c["rt_seed"]) <= rt_tol_s:
                c["members"].append((fname, p))
                c["files"].add(fname)
                break
        else:
            clusters.append({"dr_seed": dr, "rt_seed": rt,
                             "members": [(fname, p)], "files": {fname}})

    kept, dropped = [], 0
    for c in clusters:
        if len(c["files"]) < min_files:
            dropped += 1
            continue
        kept.append(c)
    # 依保留時間排序，讓區域編號沿著層析圖由早到晚——報表讀起來才自然
    kept.sort(key=lambda c: min(p["retention_s"] for _f, p in c["members"]))

    areas = []
    for i, c in enumerate(kept, start=1):
        drs = [p["drift_relative"] for _f, p in c["members"]]
        rts = [p["retention_s"] for _f, p in c["members"]]
        ris = [p.get("ri") for _f, p in c["members"] if p.get("ri") is not None]
        dr_c, rt_c = (min(drs) + max(drs)) / 2.0, (min(rts) + max(rts)) / 2.0
        dr_h = max((max(drs) - min(drs)) / 2.0, min_drift_half)
        rt_h = max((max(rts) - min(rts)) / 2.0, min_rt_half_s)
        areas.append({
            "area_id": i,
            "name": f"area {i}",                 # 比對階段可能改名；比對不到就維持
            "drift_center": round(dr_c, 5), "drift_half": round(dr_h, 5),
            "rt_center_s": round(rt_c, 3), "rt_half_s": round(rt_h, 3),
            "ri_center": round(sum(ris) / len(ris), 2) if ris else None,
            "n_files_detected": len(c["files"]),
            "detected_in": sorted(c["files"]),
            "max_prominence": round(max(p["prominence"] for _f, p in c["members"]), 2),
            "source": "consensus",
        })
    report = {"n_pooled_peaks": len(pooled), "n_clusters": len(clusters),
              "n_areas": len(areas), "n_dropped_below_min_files": dropped,
              "drift_tol": drift_tol, "rt_tol_s": rt_tol_s, "min_files": min_files,
              "active_only": active_only}
    return areas, report


# --------------------------------------------------------------------------- #
# 階段 D：在每一個檔案上量每一個區域 —— 矩陣本體
# --------------------------------------------------------------------------- #
def _box_slice(area, drift_rel_axis, retention_s, n_rt, n_dt):
    """把區域的物理座標換成該檔的索引範圍。回傳 (r0, r1, c0, c1) 或 None（完全落在檔外）。

    每個檔案各自換算：`drift_rel_axis` 用該檔自己的 RIP 正規化過，`retention_s` 是該檔
    自己的軸。所以同一個方框在不同檔案上可能落在**不同的索引**——這正是跨檔比較需要的，
    索引相同才是巧合，物理座標相同才是意義。
    """
    lo_d, hi_d = area["drift_center"] - area["drift_half"], area["drift_center"] + area["drift_half"]
    lo_t, hi_t = area["rt_center_s"] - area["rt_half_s"], area["rt_center_s"] + area["rt_half_s"]
    cols = np.nonzero((drift_rel_axis >= lo_d) & (drift_rel_axis <= hi_d))[0]
    rows = np.nonzero((retention_s >= lo_t) & (retention_s <= hi_t))[0]
    if cols.size == 0 or rows.size == 0:
        return None
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def measure_areas_in_file(mea_path, areas, floor=None, use_baseline=True,
                          baseline_lam=None, baseline_stride=8, verbose=True):
    """在單一檔案上量所有區域，回傳 {area_id: {volume, max, mean, n_px}}。

    `volume` = 方框內高於 floor 的強度總和（floor 以下夾到 0）。**定量要的是這個**，
    而它需要先扣掉傾斜的基線——`baseline.py` 的檔頭已經寫明：不扣基線的積分會把基座
    一起算進去，而 GC 的基線隨溫度程序上升，愈晚出的峰基座愈高。

    三個指標一起回傳（volume / max / mean），因為 VOCal 用哪一個沒有記在專案檔裡，
    先不替使用者決定；匯出時三個都寫，UI 可切換。
    """
    npz = ensure_npz(mea_path, verbose=verbose)
    intensity, drift_ms, retention_s, _meta = peaks_mod.load_surface(npz)
    if use_baseline:
        lam = baseline_lam if baseline_lam is not None else _baseline.DEFAULT_LAM
        intensity, _info = _baseline.correct_rt_baseline(
            intensity, lam=lam, row_stride=baseline_stride)   # 記憶體內，不寫回

    rip_index, _ = rip_mod.find_rip(intensity)
    drift_rel_axis = np.arange(intensity.shape[1], dtype=np.float64) / float(rip_index)
    if floor is None:
        floor = float(np.percentile(intensity, 85.0))

    n_rt, n_dt = intensity.shape
    surf = intensity.astype(np.float64)
    out = {}
    for a in areas:
        sl = _box_slice(a, drift_rel_axis, retention_s, n_rt, n_dt)
        if sl is None:
            # 方框完全落在這個檔的量測範圍外（例如某檔提早結束）。**記 None 而不是 0**
            # ——「沒量到」與「量到零」是兩件事，混為一談會讓下游把缺值當成真實的低訊號。
            out[a["area_id"]] = {"volume": None, "max": None, "mean": None,
                                 "n_px": 0, "out_of_range": True}
            continue
        r0, r1, c0, c1 = sl
        box = surf[r0:r1, c0:c1]
        above = np.clip(box - floor, 0.0, None)
        out[a["area_id"]] = {
            "volume": round(float(above.sum()), 2),
            "max": round(float(box.max()), 2),
            "mean": round(float(box.mean()), 2),
            "n_px": int(box.size), "out_of_range": False,
        }
    return out, {"rip_index": int(rip_index), "floor": round(float(floor), 2),
                 "shape": [int(n_rt), int(n_dt)]}


# --------------------------------------------------------------------------- #
# 階段 E：替區域命名（沿用既有比對）
# --------------------------------------------------------------------------- #
def name_areas(areas, mea_path, library_dir=None, preserve_names=False,
               ri_calibration=None,
               ri_tol=match_mod.DEFAULT_RI_TOLERANCE,
               drift_tol=match_mod.DEFAULT_DRIFTREL_TOLERANCE, verbose=True):
    """用既有的 `match.match_all()` 替每個區域找候選化合物，就地寫回 `name` / `cas`。

    比對的是**區域中心**（`drift_center`, `ri_center`），概念上等同拿該區域的代表峰去比。
    比對不到就維持 `area N` —— **這是刻意模仿 VOCal**：該專案 57 個區域也只有 27 個
    有名字，其餘照樣量測。命名是註記，不是量測的前提。

    找不到 library 資料夾時**不視為錯誤**：矩陣本身不需要名字，直接跳過命名。
    """
    data_dir = library.resolve_data_dir(explicit=library_dir)
    if data_dir is None:
        if verbose:
            log("  [info] 找不到 library 資料夾，區域維持 area N（矩陣不受影響）")
        return {"named": 0, "reason": "library dir unavailable"}
    header = identify.read_mea_header(mea_path)
    # 同 main.py：選庫極性跟著實際的 RI 尺標，不是表頭的管柱極性
    ril_rows, iml_rows, info = identify.load_libraries(
        data_dir, header, ri_calibration=ri_calibration)
    named = 0
    for a in areas:
        probe = {"ri": a.get("ri_center"),
                 "drift_relative": a.get("drift_center"),
                 "retention_s": a.get("rt_center_s")}
        r = match_mod.match_all(probe, ril_rows, iml_rows,
                                ri_tolerance=ri_tol, driftrel_tolerance=drift_tol)
        best = (r.get("combined_matches") or [None])[0]
        a["n_gc"] = len(r.get("gc_matches") or [])
        a["n_ims"] = len(r.get("ims_matches") or [])
        a["n_combined"] = len(r.get("combined_matches") or [])
        a["gc_dimension"] = r.get("gc_dimension")
        if best:
            hit = (best.get("Name") or best.get("NAME") or "").strip()
            a["matched_name"] = hit
            a["matched_cas"] = best.get("CAS")
            # `preserve_names=True`（由 .gasprj 匯入時）：VOCal 的名字是**操作者的判定**，
            # 不可被我們的比對蓋掉——那是別人的結論，我們的只是候選。兩者分欄並存，
            # 不一致時看得出來，這正是拿 .gasprj 做對照的意義。
            if not preserve_names:
                a["name"] = hit or a["name"]
                a["cas"] = best.get("CAS")
            else:
                a.setdefault("cas", None)
            named += 1
        else:
            a.setdefault("cas", None)
            a.setdefault("matched_name", None)
            a.setdefault("matched_cas", None)
    return {"named": named, "n_ril_rows": len(ril_rows), "n_iml_rows": len(iml_rows),
            "ril_files": info.get("ril_files"), "ril_strategy": info.get("ril_strategy"),
            "ri_scale_polarity": info.get("ri_scale_polarity"),
            "polarity_conflict": info.get("polarity_conflict")}


def attach_ri_to_areas(areas, ri_cal):
    """替區域中心補上 `ri_center`（就地）。由 `.gasprj` 匯入的區域沒有這個欄位。

    沒有 RI 的後果不只是報表少一欄：`match_all()` 看到 `ri=None` 會**退到保留時間比對**，
    而保留時間不跨儀器/管柱/方法轉移——命名品質會顯著變差。實測匯入 57 個區域時，
    補上 RI 前只命名到 10 個。
    """
    if not isinstance(ri_cal, dict) or ri_cal.get("mode") != "multi_point_loglinear":
        return 0
    rt_to_ri = calibration.make_rt_to_ri(ri_cal)
    n = 0
    for a in areas:
        ri, extrap = rt_to_ri(a.get("rt_center_s"))
        if ri is not None:
            a["ri_center"] = round(ri, 2)
            a["ri_extrapolated"] = bool(extrap)
            n += 1
    return n


def attach_detection_to_areas(areas, per_file_peaks, active_only=True):
    """記錄每個區域在哪些檔案裡**真的偵測到峰**（就地寫 `detected_in`）。

    與「有沒有量到值」是兩件不同的事：矩陣每一格都有值（那是重點），但只有部分格子
    底下真的有偵測到的峰。共識區域天生就有這個資訊；由 `.gasprj` 匯入的區域沒有，
    要在這裡補算，否則報表會顯示 0 而讓人以為哪裡都沒偵測到。
    """
    for a in areas:
        lo_d, hi_d = a["drift_center"] - a["drift_half"], a["drift_center"] + a["drift_half"]
        lo_t, hi_t = a["rt_center_s"] - a["rt_half_s"], a["rt_center_s"] + a["rt_half_s"]
        hits = []
        for fname, pks in per_file_peaks.items():
            for p in pks:
                if active_only and not p.get("active", True):
                    continue
                dr, rt = p.get("drift_relative"), p.get("retention_s")
                if dr is None or rt is None:
                    continue
                if lo_d <= dr <= hi_d and lo_t <= rt <= hi_t:
                    hits.append(fname)
                    break
        a["detected_in"] = sorted(hits)
        a["n_files_detected"] = len(hits)
    return areas


# --------------------------------------------------------------------------- #
# 全流程
# --------------------------------------------------------------------------- #
def _select_samples(folder, from_gasprj):
    """挑出要分析的樣品檔，回傳 (samples, class_of, excluded)。

    STD 是校正來源、BLK 是空白，兩者都不是樣品——與第一支應用把 STD 標成「不是樣品」
    的判斷一致。**STD 依表頭 `Sample=="STD"` 判定，不是檔名**（操作者會打錯檔名，
    見 `calibration.scan_folder_for_std`）。
    """
    mea_files = sorted(glob.glob(os.path.join(folder, "*.mea")))
    stds = set(calibration.scan_folder_for_std(folder))
    samples = [m for m in mea_files
               if m not in stds and "blk" not in os.path.basename(m).lower()]
    class_of = {}
    if from_gasprj:
        entries = read_gasprj_entries(from_gasprj)
        class_of = {e["basename"]: e["class"] for e in entries}
        order = [e["basename"] for e in entries]
        refd = [m for m in samples if os.path.basename(m) in class_of]
        if refd:
            # 專案有指名成員就照它，順序也照專案——報表欄位順序才和 VOCal 一致
            refd.sort(key=lambda m: order.index(os.path.basename(m)))
            samples = refd
    excluded = {"std": [os.path.basename(s) for s in sorted(stds)],
                "blank": [os.path.basename(m) for m in mea_files
                          if "blk" in os.path.basename(m).lower()],
                "not_in_gasprj": ([os.path.basename(m) for m in mea_files
                                   if os.path.basename(m) not in class_of]
                                  if from_gasprj else [])}
    return samples, class_of, excluded


class BatchCancelled(Exception):
    """使用者中止批次。**是 Exception 不是 BaseException**——背景執行緒的
    `except Exception` 必須接得住它，否則執行緒會無聲死掉、UI 永遠等不到訊息。
    這正是本模組原本用 `SystemExit` 踩到的坑（見 `build_matrix` 的 no-sample 分支）。
    """


class NoSamplesFound(Exception):
    """資料夾裡沒有樣品 `.mea`。同樣刻意是 Exception，理由同上。

    最常見的情境：使用者選到 `GAS/` 這種**只有子資料夾、沒有 .mea** 的上層目錄。
    這必須讓 UI 看得見並說清楚，不能只是靜靜地不動。
    """


def build_matrix(folder, from_gasprj=None, use_baseline=True, baseline_lam=None,
                 baseline_stride=8, min_files=DEFAULT_MIN_FILES,
                 drift_tol=DEFAULT_DRIFT_TOL, rt_tol_s=DEFAULT_RT_TOL_S,
                 library_dir=None, rules_config_path=None, limit=None,
                 skip_detect=False, progress=None, should_stop=None, verbose=True):
    """跑完整條流程，回傳可 JSON 序列化的 result dict。

    `progress(done, total, label)` 供 UI 顯示進度；CLI 傳 None。
    """
    folder = os.path.abspath(folder)
    samples, class_of, excluded = _select_samples(folder, from_gasprj)

    # `Class` 是**樣品分組**的 metadata（A/B/C/D/E × 3 重複），與「區域從哪裡來」是兩件
    # 無關的事。`from_gasprj=None`（共識模式）時原本整組分組標籤都會遺失，欄位標題因此
    # 全變成 `?`——實測 18 個檔案全中。只要資料夾裡有 .gasprj 就把分組讀出來，不論區域
    # 是不是用它的。
    if not class_of:
        for g in calibration.scan_folder_for_gasprj(folder):
            try:
                class_of = {e["basename"]: e["class"]
                            for e in read_gasprj_entries(g) if e.get("class")}
            except (OSError, ValueError):
                continue
            if class_of:
                if verbose:
                    log(f"  分組標籤取自 {os.path.basename(g)}（{len(class_of)} 筆）")
                break
    if limit:
        samples = samples[:int(limit)]
    if not samples:
        subs = [d for d in sorted(glob.glob(os.path.join(folder, "*")))
                if os.path.isdir(d) and glob.glob(os.path.join(d, "*.mea"))]
        msg = [str(folder), "", "這個資料夾裡沒有樣品 .mea（STD/BLK 已排除）。"]
        if subs:
            msg += ["", "但底下這些子資料夾有——請改選其中一個："]
            msg += ["    " + os.path.basename(d) for d in subs]
        raise NoSamplesFound("\n".join(msg))

    def _check_stop():
        if should_stop is not None and should_stop():
            raise BatchCancelled("使用者中止批次")

    rules_config = rules.load_config(rules_config_path or RULES_CONFIG)

    # 資料夾層級校正（RI + K0），與 main.py 相同的呼叫。
    # use_sidecar=False：不在 GAS/ 內產生或更新 _folder_calibration.json。
    if verbose:
        log("解析資料夾校正（RI + K0）…")
    resolved = calibration.resolve_calibrations_cached(
        folder, series_key=RI_SERIES, k0_series_key=RI_SERIES, use_sidecar=False)
    ri_cal, ri_mode, ri_detail = resolved["ri"]
    k0_profile, k0_mode, _k0d = resolved["k0"]
    if verbose:
        log(f"  ri_mode={ri_mode}  k0_mode={k0_mode}")

    # 找峰佔掉整批時間的 **75%**（實測單檔：union-find 55.4 s、基線 16.8 s、其餘 1.8 s）。
    # 而區域若是從 .gasprj 匯入的，找峰**只被用來填 n_det 那一欄**——方框本身完全不需要它。
    # 所以這個組合可以整段跳過，單檔 74 s → 約 19 s（含基線）或 2.5 s（不含）。
    # 共識模式不能跳：那時候區域正是從偵測到的峰長出來的。
    if skip_detect and not from_gasprj:
        raise ValueError("skip_detect 只能搭配 from_gasprj——共識區域必須先找峰")

    # `Class` 是手打的，實測 Coffee-bean 15 筆有 3 筆與檔名對不上。分組統計若建立在
    # 錯的標籤上，結論會錯而且看不出來——所以一定要講出來。只報不改（見函式說明）。
    class_warnings = check_class_labels([os.path.basename(m) for m in samples], class_of)
    if class_warnings and verbose:
        log(f"  ⚠ {len(class_warnings)}/{len(samples)} 個檔案的 Class 與檔名不一致：")
        for w in class_warnings[:5]:
            log(f"      {w['file']}  Class='{w['class']}'  檔名='{w['from_filename']}'")
        log("      （只回報不自動修正——哪一邊對是原始資料的問題）")

    total = len(samples)
    steps = total * (1 if skip_detect else 2)
    per_file_peaks, per_file_meta = {}, {}
    for i, m in enumerate(samples if not skip_detect else [], 1):
        b = os.path.basename(m)
        if progress:
            progress(i - 1, steps, f"detect {b}")
        _check_stop()
        if verbose:
            log(f"[{i}/{total}] {b}")
        pk, stats, meta = detect_one(
            m, rules_config, use_baseline=use_baseline, baseline_lam=baseline_lam,
            baseline_stride=baseline_stride, verbose=verbose)
        if isinstance(ri_cal, dict):
            calibration.attach_ri(pk, ri_cal)
        per_file_peaks[b] = pk
        per_file_meta[b] = {"stats": stats, "meta": meta, "class": class_of.get(b, "")}
        if verbose:
            log(f"    {len(pk)} 峰（{sum(1 for p in pk if p.get('active'))} 通過規則）")

    if from_gasprj:
        header = identify.read_mea_header(samples[0])
        areas, area_report = read_gasprj_areas(from_gasprj, header_rt_step_s(header))
        area_report["mode"] = "imported_from_gasprj"
        if verbose:
            log(f"由 .gasprj 匯入 {len(areas)} 個區域")
    else:
        areas, area_report = build_consensus_areas(
            per_file_peaks, drift_tol=drift_tol, rt_tol_s=rt_tol_s, min_files=min_files)
        area_report["mode"] = "consensus_from_detected_peaks"
        if verbose:
            log(f"共識區域：{area_report['n_pooled_peaks']} 峰 → "
                f"{area_report['n_clusters']} 群集 → {len(areas)} 區域")

    # 由 .gasprj 匯入的區域沒有 RI 也沒有偵測連結，兩者都要補——否則命名會退到
    # 保留時間比對，而報表的 n_files_detected 會一律顯示 0。
    n_ri = attach_ri_to_areas(areas, ri_cal)
    if skip_detect:
        # **None 不是 0**：沒有跑找峰就是「不知道」，不是「哪裡都沒偵測到」。
        # 標成 0 會讓人以為每個區域在每個樣品都沒東西。
        for a in areas:
            a["detected_in"] = None
            a["n_files_detected"] = None
    else:
        attach_detection_to_areas(areas, per_file_peaks)
    if verbose and n_ri:
        log(f"  區域中心補上 RI：{n_ri}/{len(areas)}")

    name_report = name_areas(areas, samples[0], library_dir=library_dir,
                             preserve_names=bool(from_gasprj),
                             ri_calibration=ri_cal, verbose=verbose)
    name_report["preserved_gasprj_names"] = bool(from_gasprj)
    if verbose:
        log(f"命名：{name_report.get('named', 0)}/{len(areas)} 個區域比對到化合物")

    matrix, measure_meta = {}, {}
    for i, m in enumerate(samples, 1):
        b = os.path.basename(m)
        if progress:
            progress((0 if skip_detect else total) + i - 1, steps, f"measure {b}")
        _check_stop()
        if verbose:
            log(f"量測 [{i}/{total}] {b}")
        vals, mm = measure_areas_in_file(
            m, areas, use_baseline=use_baseline, baseline_lam=baseline_lam,
            baseline_stride=baseline_stride, verbose=verbose)
        matrix[b] = vals
        measure_meta[b] = mm
    if progress:
        progress(steps, steps, "done")

    return {
        "app": "areas2",
        "folder": folder,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "files": [os.path.basename(m) for m in samples],
        "classes": {os.path.basename(m): class_of.get(os.path.basename(m), "")
                    for m in samples},
        "excluded": excluded,
        "class_warnings": class_warnings,
        "n_areas": len(areas), "n_files": len(samples),
        "areas": areas, "matrix": matrix,
        "provenance": {
            "ri_mode": ri_mode, "k0_mode": k0_mode,
            "ri_assumed_unverified": (bool(ri_cal.get("assumed_unverified"))
                                      if isinstance(ri_cal, dict) else None),
            "ri_caveat": ri_cal.get("ri_caveat") if isinstance(ri_cal, dict) else None,
            "ri_series": ri_cal.get("series_used") if isinstance(ri_cal, dict) else None,
            "ri_n_anchors": ri_cal.get("n_anchors") if isinstance(ri_cal, dict) else None,
            "ri_resolution": ri_detail,
            "skip_detect": bool(skip_detect),
            "baseline_applied": bool(use_baseline),
            "baseline_lam": ((baseline_lam if baseline_lam is not None
                              else _baseline.DEFAULT_LAM) if use_baseline else None),
            "area_selection": area_report,
            "naming": name_report,
            "per_file": per_file_meta,
            "measure": measure_meta,
        },
    }


# --------------------------------------------------------------------------- #
# 階段 F：輸出（全部帶 `2` 尾綴，不可能覆蓋第一支應用的產物）
# --------------------------------------------------------------------------- #
METRICS = ("volume", "max", "mean")


def result_paths(result):
    """回傳 (areas_json, matrix_csv) 兩個輸出路徑。以資料夾名為前綴。"""
    tag = os.path.basename(result["folder"].rstrip("/\\")) or "batch"
    safe = re.sub(r"[^\w.\-]+", "_", tag)
    return (os.path.join(RESULTS_DIR, f"{safe}_areas2.json"),
            os.path.join(RESULTS_DIR, f"{safe}_area_matrix2.csv"))


def write_matrix_csv(result, path, metric="volume"):
    """把矩陣寫成 CSV：一列一個區域，一欄一個檔案。

    前置欄位帶座標與身分（`drift_relative` / `rt_s` / `ri` / `name` / `cas`），這樣單看
    這個檔案就知道每一列是什麼、量在哪裡，不必回頭翻 JSON。

    量不到的格子寫空字串而不是 0 —— 「沒量到」與「量到零」是兩回事（見
    `measure_areas_in_file`），寫成 0 會讓下游把缺值當成真實的低訊號。
    """
    if metric not in METRICS:
        raise ValueError(f"metric 必須是 {METRICS} 之一，收到 {metric!r}")
    files = result["files"]
    classes = result.get("classes") or {}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        # utf-8-sig：Excel 開 CSV 沒有 BOM 會把中文與化合物名顯示成亂碼
        f.write("# metric=" + metric
                + f" ri_mode={result['provenance']['ri_mode']}"
                + f" baseline={result['provenance']['baseline_applied']}"
                + f" areas={result['n_areas']} files={result['n_files']}\n")
        f.write("class," + "," * 6 + ",".join(classes.get(b, "") for b in files) + "\n")
        f.write("area_id,name,cas,drift_relative,rt_s,ri,n_files_detected,"
                + ",".join(files) + "\n")
        for a in result["areas"]:
            row = [str(a["area_id"]),
                   '"' + str(a.get("name", "")).replace('"', "'") + '"',
                   str(a.get("cas") or ""),
                   f"{a['drift_center']:.5f}",
                   f"{a['rt_center_s']:.2f}",
                   ("" if a.get("ri_center") is None else f"{a['ri_center']:.1f}"),
                   str(a.get("n_files_detected", 0))]
            for b in files:
                cell = (result["matrix"].get(b) or {}).get(a["area_id"]) or {}
                v = cell.get(metric)
                row.append("" if v is None else f"{v:g}")
            f.write(",".join(row) + "\n")
    return path


def write_result_json(result, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return path


def matrix_as_rows(result, metric="volume"):
    """逐檔視圖：回傳 (headers, rows)。欄位標題用短檔名（見 short_file_label）。"""
    files = result["files"]
    headers = ["#", "name", "RI", "drift", "rt_s", "n_det"] + [
        short_file_label(b) for b in files]
    rows = []
    for a in result["areas"]:
        nd = a.get("n_files_detected")
        r = [a["area_id"], a.get("name", ""),
             "" if a.get("ri_center") is None else f"{a['ri_center']:.1f}",
             f"{a['drift_center']:.3f}", f"{a['rt_center_s']:.1f}",
             "—" if nd is None else nd]
        for b in files:
            cell = (result["matrix"].get(b) or {}).get(a["area_id"]) or {}
            v = cell.get(metric)
            r.append("" if v is None else f"{v:g}")
        rows.append(r)
    return headers, rows


def short_file_label(basename):
    """`260625_113647_E_1_1.mea` -> `E_1_1`。

    欄位標題塞不下完整檔名（實測 18 欄），而檔名前面的 `日期_時間_` 對辨識樣品毫無幫助
    ——同一批全部同一天。去掉前綴之後剩下的正是樣品代號。認不出格式就退回原檔名去尾綴，
    不要硬切（切錯會讓兩個不同樣品長得一樣）。
    """
    b = os.path.splitext(str(basename))[0]
    m = re.match(r"^\d{6}_\d{6}_(.+)$", b)
    return m.group(1) if m else b


def check_class_labels(files, class_of):
    """比對 `.gasprj` 的 `Class` 與檔名裡的樣品代號，回傳不一致清單。

    為什麼要查：`Class` 是**手打的**，實測 Coffee-bean 15 筆有 3 筆對不上——
    `260625_122837_E_1_2.mea` 被標成 `E 1-1`、`..._C_1_2.mea` 標成 `C 1-1`、
    `..._C_1_3.mea` 標成 `C 1-2`。分組統計如果建立在錯的標籤上，結論就是錯的，
    而且**看不出來**。

    **只回報，不自動修正**：哪一邊才對是原始資料的問題（可能是檔名打錯，也可能是
    Class 打錯），程式沒有依據能判斷。靜默改掉比留著錯更糟。

    比對方式：把兩邊都正規化成只留英數並轉小寫（`E 1-2` → `e12`，
    `260625_122837_E_1_2` → 取日期時間後的 `E_1_2` → `e12`），再比。
    """
    out = []
    for b in files:
        raw = (class_of.get(b) or "").strip()
        if not raw:
            continue
        from_name = short_file_label(b)
        a = re.sub(r"[^0-9a-z]", "", raw.lower())
        c = re.sub(r"[^0-9a-z]", "", from_name.lower())
        if a != c:
            out.append({"file": b, "class": raw, "from_filename": from_name})
    return out


def class_group_key(cls):
    """`"A 1-2"` -> `"A"`。取 Class 的**第一個 token** 當實驗組別。

    `.gasprj` 的 `Class` 存的是**每一個重複樣品**的代號（`A 1-1` / `A 1-2` / `A 1-3`），
    不是組別。照字面分組會得到 13 組、每組 n=1，完全失去「組間比較」的意義——而這批
    的設計就是 A/B/C/D/E 五組 × 3 重複。取第一個 token 才會還原成那五組。

    分不出來（空字串、或第一個 token 就是整串）時原樣回傳，不硬拆。
    """
    c = (cls or "").strip()
    if not c:
        return ""
    return c.split()[0]


def class_groups(result, collapse=True):
    """{組別: [檔名, ...]}，依 `result["files"]` 的順序。

    `collapse=True`（預設）用 `class_group_key()` 收成實驗組；`False` 則照 Class 原樣，
    保留每個重複樣品自己的代號。
    """
    classes = result.get("classes") or {}
    groups = {}
    for b in result.get("files", []):
        raw = (classes.get(b) or "").strip()
        key = class_group_key(raw) if collapse else raw
        groups.setdefault(key, []).append(b)
    return groups


def _cell(result, b, area_id, metric):
    return ((result["matrix"].get(b) or {}).get(area_id) or {}).get(metric)


def matrix_summary_rows(result, metric="volume"):
    """摘要視圖：身分欄 + **每個 Class 一欄**（該組的平均值）。

    為什麼是 Class 而不是逐檔：18 欄擠在一起沒辦法讀，而這個實驗的設計本來就是
    A/B/C/D/E 五組 × 3 重複——要比較的是**組與組**，不是檔與檔。逐檔的完整數值在
    雙擊列開的視窗裡，一格都沒少。

    沒有分組資訊時退回「全批平均 / 最大 / 最小」三欄，仍然可讀。
    """
    groups = class_groups(result)
    named = [g for g in groups if g]
    headers = ["#", "name", "RI", "drift", "rt_s", "n_det"]
    if named:
        keys = sorted(named)
        headers += [f"{g} (n={len(groups[g])})" for g in keys]
    else:
        keys = None
        headers += ["mean", "min", "max"]
    rows = []
    for a in result["areas"]:
        nd = a.get("n_files_detected")
        r = [a["area_id"], a.get("name", ""),
             "" if a.get("ri_center") is None else f"{a['ri_center']:.1f}",
             f"{a['drift_center']:.3f}", f"{a['rt_center_s']:.1f}",
             "—" if nd is None else nd]
        if keys is not None:
            for g in keys:
                vals = [v for v in (_cell(result, b, a["area_id"], metric)
                                    for b in groups[g]) if v is not None]
                r.append("" if not vals else f"{sum(vals) / len(vals):.4g}")
        else:
            vals = [v for v in (_cell(result, b, a["area_id"], metric)
                                for b in result["files"]) if v is not None]
            r += ["" if not vals else f"{sum(vals) / len(vals):.4g}",
                  "" if not vals else f"{min(vals):.4g}",
                  "" if not vals else f"{max(vals):.4g}"]
        rows.append(r)
    return headers, rows


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    _use_utf8_stdout()
    ap = argparse.ArgumentParser(
        description="第二支應用：跨檔案區域強度矩陣（VOCal MeasAreas 模式）")
    ap.add_argument("folder", help="含 .mea 的批次資料夾")
    ap.add_argument("--from-gasprj", default=None,
                    help="改用該 .gasprj 內建的 MeasAreas 區域定義，跳過共識群聚"
                         "（供與 VOCal 逐格對照）")
    ap.add_argument("--metric", choices=METRICS, default="volume",
                    help="CSV 寫哪個指標（三個都會算並存進 JSON）")
    ap.add_argument("--no-baseline", dest="baseline", action="store_false",
                    help="停用 AsLS 基線扣除（預設啟用；體積積分需要它）")
    ap.set_defaults(baseline=True)
    ap.add_argument("--baseline-lam", type=float, default=None)
    ap.add_argument("--baseline-stride", type=int, default=8)
    ap.add_argument("--min-files", type=int, default=DEFAULT_MIN_FILES,
                    help="區域至少要在幾個檔案裡出現（共識過濾）")
    ap.add_argument("--drift-tol", type=float, default=DEFAULT_DRIFT_TOL)
    ap.add_argument("--rt-tol", type=float, default=DEFAULT_RT_TOL_S)
    ap.add_argument("--library-dir", default=None)
    ap.add_argument("--rules-config", default=None)
    ap.add_argument("--limit", type=int, default=None,
                    help="只處理前 N 個檔案（開發時試跑用）")
    ap.add_argument("--skip-detect", action="store_true",
                    help="跳過找峰，直接量測（**只能配 --from-gasprj**）。找峰佔整批"
                         "時間 75%%（單檔 55 s），而方框來自 .gasprj 時它只被用來填"
                         " n_det 欄。單檔 74 s → 約 19 s（含基線）")
    args = ap.parse_args(argv)

    result = build_matrix(
        args.folder, from_gasprj=args.from_gasprj, use_baseline=args.baseline,
        baseline_lam=args.baseline_lam, baseline_stride=args.baseline_stride,
        min_files=args.min_files, drift_tol=args.drift_tol, rt_tol_s=args.rt_tol,
        library_dir=args.library_dir, rules_config_path=args.rules_config,
        limit=args.limit, skip_detect=args.skip_detect)

    js, csv = result_paths(result)
    write_result_json(result, js)
    write_matrix_csv(result, csv, metric=args.metric)

    prov = result["provenance"]
    print()
    print(f"矩陣：{result['n_areas']} 區域 × {result['n_files']} 檔案")
    print(f"  RI  : {prov['ri_mode']}"
          + (f"  ⚠ {prov['ri_caveat']}" if prov.get("ri_caveat") else ""))
    print(f"  K0  : {prov['k0_mode']}")
    print(f"  基線: {'AsLS λ=' + format(prov['baseline_lam'], 'g') if prov['baseline_applied'] else '未扣'}")
    print(f"  區域: {prov['area_selection']['mode']}"
          f"（命名 {prov['naming'].get('named', 0)}/{result['n_areas']}）")
    filled = sum(1 for b in result["files"] for a in result["areas"]
                 if (result["matrix"][b].get(a["area_id"]) or {}).get(args.metric) is not None)
    print(f"  格子: {filled}/{result['n_areas'] * result['n_files']} 有值"
          f"（metric={args.metric}）")
    print(f"  JSON: {js}")
    print(f"  CSV : {csv}")
    return result


if __name__ == "__main__":
    main()

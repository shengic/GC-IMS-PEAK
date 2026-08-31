"""第三支應用的邏輯層 —— headless、可 CLI 執行、沒有任何 Tk 相依。

**設計約束（來自實測，不是偏好）**

1. **只用函式呼叫，絕不 subprocess。** `main.py` 用
   `[sys.executable, Path(__file__).with_name("peaks.py"), ...]` 起子行程，那在
   打包成 exe 之後會變成「用你的 exe 再開一次 GUI」——`sys.executable` 是 exe，
   而 `peaks.py` 根本不在磁碟上。第二支應用（`areas2.py`）已經改掉，本模組跟隨。

2. **分組必須用「區域」，不能只比整張熱圖。** 實測過整張粗網格比對：C 組算出 0.49，
   而以區域為特徵是 0.883——大片背景會蓋過訊號。改用高變異數網格點有改善但仍不一致。
   所以偵測那一步（約 55 秒/檔，會快取）跑不掉。

3. **「沒量到」「沒掃描」「不相似」是三件事。** 一律用 `None` 表示前兩者，
   絕不用 `0` 或 `-1` 混充（同第二支應用 `n_det=None` vs `0` 的理由）。

實測基準（2026-08-31，三個資料夾 45 個檔）：以「最相似鄰居是否同組」為準達 **43/45**。
96% 仍然落在「可以建議、不可以替使用者決定」——UI 因此是 highlight + 使用者增刪。

Version: 1.0 — by Albert Sheng（第三支應用，2026-08-31）
"""
from __future__ import annotations

import os
import glob
import json
import re

import numpy as np

import calibration
import peaks as peaks_mod
import rip as rip_mod
import areas2
import match as match_mod


# --------------------------------------------------------------------------- #
# 樣品挑選
# --------------------------------------------------------------------------- #

#: 表頭 `Sample` 或檔名裡出現這些**詞**就視為空白樣品。
#: 用「詞」而不是子字串：`areas2._select_samples()` 寫的是 `"blk" in basename`，
#: 而 `FISH_MEAT_BLANK.mea`（表頭 `Sample='Fish meat blank'`）**兩個條件都不符合**
#: ——`blank` 裡沒有 `blk` 這個子字串。實測該檔因此被當成樣品混進鱸魚那批的矩陣。
BLANK_TOKENS = {"blk", "blank", "blanc", "leer", "blindwert"}

_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z]+")


def _tokens(text):
    return {t.lower() for t in _TOKEN_SPLIT.split(text or "") if t}


def is_blank(mea_path, header=None):
    """空白樣品？**看表頭的 `Sample`，檔名只是備援**。

    與 `calibration.scan_folder_for_std()` 判定 STD 的作法一致——操作者會打錯檔名，
    以檔名為準會讓判斷與實際量到的東西不一致。
    """
    header = header if header is not None else calibration._read_header_lite(mea_path)
    sample = (header.get("Sample") or "").strip()
    return bool(_tokens(sample) & BLANK_TOKENS) or bool(
        _tokens(os.path.basename(mea_path)) & BLANK_TOKENS)


def select_samples(folder):
    """挑出資料夾裡「是樣品」的 `.mea`。

    回傳 `(samples, excluded)`；`excluded` 逐項寫明理由，**讓 UI 顯示得出來**
    ——靜靜排掉檔案正是本專案一再防的那類問題。
    """
    mea_files = sorted(glob.glob(os.path.join(folder, "*.mea")))
    stds = set(calibration.scan_folder_for_std(folder))

    samples, excluded = [], []
    for m in mea_files:
        if m in stds:
            excluded.append({"file": m, "reason": "std",
                             "detail": "表頭 Sample=='STD'，是校正來源不是樣品"})
            continue
        try:
            header = calibration._read_header_lite(m)
        except Exception as exc:                       # 壞掉的表頭要看得見，不是消失
            excluded.append({"file": m, "reason": "unreadable_header",
                             "detail": f"{type(exc).__name__}: {exc}"})
            continue
        if is_blank(m, header):
            excluded.append({"file": m, "reason": "blank",
                             "detail": f"Sample={header.get('Sample')!r}"})
            continue
        samples.append(m)
    return samples, excluded


# --------------------------------------------------------------------------- #
# 快取狀態 —— 決定「現在就能比」還是「要先掃描」
# --------------------------------------------------------------------------- #

def cache_state(mea_paths):
    """每個檔目前的快取狀態，供 UI 決定灰階／可用。

    **「沒掃描」不可以看起來像「不相似」**，所以兩個階段分開回報：
    `npz` 是讀檔結果（約 13 秒），`peaks2` 是找峰結果（約 55 秒）。

    ⚠ **判斷找峰有沒有做過，看的必須是 `_peaks2.json`，不是 `_maxima.npz`。**
    這兩個是**不同應用的快取**：`_maxima.npz` 是第一支應用寫的，而本應用走的
    `areas2.detect_one()` 只認自己的 `_peaks2.json`，不會去讀前者。實測踩過：
    Coffee-bean 有 9 個檔有 `_maxima.npz`，於是估出「只剩 9 個要偵測」，實際上
    18 個全都重跑了——等待時間是預告的兩倍。**估錯時間本身就是一種無聲的錯誤數字**。
    `has_maxima` 仍然回報，但只是第一支應用的資訊，不計入待辦。
    """
    out = []
    for m in mea_paths:
        base = os.path.splitext(os.path.basename(m))[0]
        out.append({"file": m,
                    "has_npz": os.path.exists(areas2._npz_path(m)),
                    "has_peaks2": os.path.exists(areas2._peaks2_path(m)),
                    "has_maxima": os.path.exists(
                        os.path.join(areas2.RESULTS_DIR, base + "_maxima.npz"))})
    return out


def scan_cost(mea_paths, sec_per_read=13.0, sec_per_detect=55.0):
    """還要花多久才能全部參與比對（秒）。UI 拿去寫在「掃描其餘」按鈕上。

    只數本應用真的會做的工作——見 `cache_state()` 對 `_maxima.npz` 的警告。
    """
    st = cache_state(mea_paths)
    reads = sum(1 for s in st if not s["has_npz"])
    detects = sum(1 for s in st if not s["has_peaks2"])
    return {"n_need_read": reads, "n_need_detect": detects,
            "est_seconds": reads * sec_per_read + detects * sec_per_detect}


# --------------------------------------------------------------------------- #
# 特徵 —— 以區域為單位，不是整張圖
# --------------------------------------------------------------------------- #

def measure_profile(mea_path, areas, use_baseline=False, verbose=False):
    """量一個檔在所有區域上的 volume，回傳 `list[float | None]`。

    `None` = 方框落在這個檔的範圍外（沒量到），**不是 0**。
    """
    values, _info = areas2.measure_areas_in_file(
        mea_path, areas, use_baseline=use_baseline, verbose=verbose)
    return [(values.get(a["area_id"]) or {}).get("volume") for a in areas]


def similarity_matrix(profiles):
    """檔案兩兩相似度。

    做法與驗證時一致：`log10(x+1)` 壓縮動態範圍 → 每個區域跨檔標準化 → 相關係數。
    **先取 log 再標準化**：強度跨越好幾個數量級，不壓縮的話幾個大峰會主導整個相關係數。

    任一檔缺值的區域整欄捨棄——用 0 補會把「沒量到」當成「量到零」，那正是
    比較分組時最不能混的兩件事。
    """
    if len(profiles) < 3:
        # **兩個檔算出來的相似度一定是 ±1，與資料無關。**
        # 每個區域跨檔標準化時，n=2 會讓兩個值必然變成 +a 與 −a，於是相關係數恆為
        # −1（實測：隨機資料跑幾次都是 -1.000000）。那不是量測，是算式的產物。
        # `np.corrcoef` 對單一列更會回 0 維的 nan，之後 `corr[i, j]` 直接 IndexError。
        raise ValueError(
            "相似度至少要 3 個檔（目前 %d 個）。每個區域要跨檔標準化，而檔案只有"
            "兩個時每一欄都會被壓成 (+1, −1)，相關係數恆為 −1，與資料完全無關。"
            % len(profiles))
    X = np.array([[np.nan if v is None else float(v) for v in p] for p in profiles],
                 dtype=float)
    keep = ~np.isnan(X).any(axis=0)
    if keep.sum() < 3:
        raise ValueError(
            f"可用區域只剩 {int(keep.sum())} 個，不足以判斷相似度（至少要 3）。")
    X = np.log10(np.clip(X[:, keep], 0.0, None) + 1.0)
    # **先去掉每個檔的整體強度**（逐列置中）。進樣量、樣品濃度會讓整張圖等比例
    # 放大縮小，在 log 上就是加一個常數；不先移除的話，同一個標本只因為打得多一點
    # 就會被判成不像。實測：把一個檔的強度乘 2，相似度從 0.970 掉到 0.673。
    # 移除之後 0.989 → 0.989，完全不受影響。
    X = X - X.mean(1, keepdims=True)
    # 再逐「區域」標準化：讓每個區域等權，而不是被幾個大峰主導。這一步是鑑別力的
    # 來源——少了它，所有咖啡檔案都因為共同的基本輪廓而相關 0.9 以上，分不開。
    Z = (X - X.mean(0)) / (X.std(0) + 1e-12)
    return np.corrcoef(Z), int(keep.sum())


def suggest_partners(index, corr, files, threshold=None):
    """給定使用者點選的檔（`index`），回傳其餘檔依相似度排序。

    `threshold=None` 時不替使用者畫線，只排序並附上數值——**同組與否最終由使用者定**，
    實測自動判定 43/45，剩下的那幾個正是需要人看的。
    """
    order = sorted((j for j in range(len(files)) if j != index),
                   key=lambda j: corr[index, j], reverse=True)
    return [{"file": files[j], "r": float(corr[index, j]),
             "suggested": None if threshold is None else bool(corr[index, j] >= threshold)}
            for j in order]


def nearest_neighbour_check(corr, files, group_of):
    """回歸用的品質指標：每個檔的最相似鄰居是否同組。

    這正是 2026-08-31 量到 43/45 的那個指標；把它留在程式裡，任何人改動特徵或
    相似度算法之後都能立刻重測，不必重新發明評估方式。
    """
    rows, hit = [], 0
    for i, f in enumerate(files):
        j = max((k for k in range(len(files)) if k != i), key=lambda k: corr[i, k])
        ok = group_of(files[j]) == group_of(f)
        hit += ok
        rows.append({"file": f, "declared": group_of(f),
                     "nn": files[j], "nn_group": group_of(files[j]),
                     "r": float(corr[i, j]), "same_group": bool(ok)})
    return {"n": len(files), "hits": hit,
            "rate": hit / len(files) if files else None, "rows": rows}


# --------------------------------------------------------------------------- #
# 區域來源 —— 由資料自己長出來，**不需要 .gasprj**
# --------------------------------------------------------------------------- #

#: 一個位置要在幾成的檔案裡出現，才算這個標本的共同峰。
#: **寫成分數 2/3 而不是小數 0.67**——`0.67` 在 n=3 時要求 `ceil(2.01)=3`，也就是
#: 三個重複全部都要有；而 2/3 只要 2 個。差別在每個 3 的倍數上都會出現（n=6 要 5 vs 4、
#: n=15 要 11 vs 10），而且**不會有任何徵兆**，只會看到候選莫名其妙變少。
DEFAULT_MIN_FRACTION = 2.0 / 3.0


def required_files(n_files, min_fraction=DEFAULT_MIN_FRACTION):
    """`n_files` 個檔案中，至少要幾個出現才算共同峰。

    `- 1e-9` 是為了讓 2/3 這種除不盡的分數在 n=3、6、9… 時剛好通過，不會因為
    浮點數 0.6666…7 略小於 2/3 而被多要求一個檔。下限 2：單一檔案無法構成共識。
    """
    import math
    return max(2, math.ceil(min_fraction * n_files - 1e-9))


def consensus_regions(mea_paths, rules_config, min_fraction=DEFAULT_MIN_FRACTION,
                      use_baseline=False, active_only=True, formation_floor=2,
                      ri_calibration=None, progress=None, verbose=False):
    """對選定的檔案跑找峰，再跨檔群聚成共用區域。回傳 `(areas, per_file_peaks, report)`。

    **這是取代 `.gasprj` 方框的那條路。** `.gasprj` 只用來對照驗證，不參與流程——
    區域完全由這批檔案自己的峰決定，所以沒有 VOCal 專案檔的資料夾一樣能用。

    **門檻是「佔全組的幾成」，不是固定張數**——一組有幾個重複是資料決定的，不是程式。
    預設 2/3（見 `DEFAULT_MIN_FRACTION`）。只出現在少數檔案的位置多半是雜訊，而重複
    樣品裡真實的化合物本來就會重複出現，這一步正是「跨檔」相對於「逐檔」多出來的資訊。

    `active_only=True`：**只用使用者勾選的峰**。區域因此是從使用者的判斷長出來的，
    不是先固定好再要他接受。

    貴在找峰（約 55 秒/檔），但 `areas2.detect_one()` 會把結果快取成 `_peaks2.json`，
    第二次近乎即時。`progress` 是 `callable(done, total, path)`，給 UI 更新用。
    """
    per_file = {}
    total = len(mea_paths)
    for i, m in enumerate(mea_paths, 1):
        pk, _stats, _meta = detect_cached(
            m, rules_config, use_baseline=use_baseline, verbose=verbose)
        # **RI 必須在比對之前掛上去。** `detect_one()` 只找峰，不做第四階段；沒有
        # `peak["ri"]` 的話 `match.match_all()` 會**靜靜退回保留時間比對**——而保留
        # 時間不跨儀器/管柱/方法轉移，出來的候選是「RT 恰好相近」而不是同一個化合物。
        # 這正是 status.md 記載過的「GC 欄位掛著 RI 名義顯示秒數」那個坑。
        if ri_calibration:
            calibration.attach_ri(pk, ri_calibration)
        per_file[m] = pk
        if progress:
            progress(i, total, m)
    # **形成門檻與顯示門檻要分開。**
    # 用 `required_files()` 去形成區域的話，過不了門檻的根本不會被建出來，於是
    # 「不刪除、只標記」等於空話——使用者永遠看不到差一票的那些。所以這裡一律用
    # 絕對下限 `formation_floor`（預設 2：一個檔case無法「共識」），佔比門檻只在
    # `rank_areas()` 決定灰不灰。
    need = required_files(total, min_fraction)
    areas, report = areas2.build_consensus_areas(
        per_file, min_files=formation_floor, active_only=active_only)
    areas2.attach_detection_to_areas(areas, per_file, active_only=active_only)
    for a in areas:
        n = a.get("n_files_detected") or 0
        a["votes"] = n                       # 表格要顯示的票數
        a["votes_total"] = total
        a["vote_fraction"] = (n / total) if total else None
    report = dict(report)
    report["region_source"] = "consensus"        # ← provenance：不是 gasprj
    report["n_files"] = total
    report["min_fraction"] = min_fraction
    report["min_files_required"] = need          # 顯示門檻（灰不灰）
    report["formation_floor"] = formation_floor  # 形成門檻（建不建）
    return areas, per_file, report


# --------------------------------------------------------------------------- #
# 候選彙整 —— 本應用真正新增的一步
# --------------------------------------------------------------------------- #

def _field(row, *names):
    for n in names:
        v = row.get(n)
        if v not in (None, ""):
            return v
    return None


def peaks_in_area(area, per_file_peaks, active_only=True):
    """每個檔案落在這個區域裡的峰（有多顆時取突出度最大的）。

    回傳 `{檔案: 峰 | None}`——**沒偵測到就是 `None`，不是省略**。少一個 key 和
    「這個檔在這裡沒有峰」是兩種陳述，後者才是資訊。
    """
    lo_d, hi_d = area["drift_center"] - area["drift_half"], area["drift_center"] + area["drift_half"]
    lo_t, hi_t = area["rt_center_s"] - area["rt_half_s"], area["rt_center_s"] + area["rt_half_s"]
    out = {}
    for fname, pks in per_file_peaks.items():
        best = None
        for p in pks:
            if active_only and not p.get("active", True):
                continue
            dr, rt = p.get("drift_relative"), p.get("retention_s")
            if dr is None or rt is None:
                continue
            if lo_d <= dr <= hi_d and lo_t <= rt <= hi_t:
                if best is None or (p.get("prominence") or 0) > (best.get("prominence") or 0):
                    best = p
        out[fname] = best
    return out


def consolidate_area(area, per_file_peaks, ril_rows, iml_rows,
                     ri_tol=None, drift_tol=None, active_only=True):
    """把一個區域在各檔的候選化合物彙整成一張有支持度的清單。

    **與第一支應用的差別**：`main.py` 拿**一個檔的一顆峰**去比對，得到一長串候選
    （實測：身分確定的 2-butanone 得到 415 筆命中、56 個化合物），然後由人判斷。
    這裡改成拿**同一標本的每一個重複**各自比對，再看哪些候選**每個重複都出現**。

    容差窗邊緣的候選會因此被篩掉：各檔量到的 RI 略有不同，窗的中心跟著移動，
    坐在邊緣的化合物在某些檔命中、某些檔落空；坐在中心的則每個檔都在。

    回傳的 `ri_spread` 是**實測的重複變異**，這正是校準容差窗需要而目前沒有的數字
    （`status.md` open decision 4：±5 是佔位值，從未以量測校準過）。
    """
    kw = {}
    if ri_tol is not None:
        kw["ri_tolerance"] = ri_tol
    if drift_tol is not None:
        kw["driftrel_tolerance"] = drift_tol

    found = peaks_in_area(area, per_file_peaks, active_only=active_only)
    with_peak = {f: p for f, p in found.items() if p is not None}

    votes, measured_ri, measured_drift = {}, [], []
    dims_used = set()
    for fname, peak in with_peak.items():
        if peak.get("ri") is not None:
            measured_ri.append(float(peak["ri"]))
        if peak.get("drift_relative") is not None:
            measured_drift.append(float(peak["drift_relative"]))
        res = match_mod.match_all(peak, ril_rows, iml_rows, **kw)
        # **優先用 combined（GC 與 IMS 兩軸都同意）**。只看 gc_matches 等於只用 RI
        # 一個維度，而 `.ril` 有十幾萬列、±5 的窗口內永遠有上百個化合物——那不是
        # 鑑定，是數字上的巧合。兩軸同時吻合才把候選收斂到個位數。
        # combined 為空時退回 gc_matches，並在回傳裡標明用了哪一個，不靜默混用。
        rows = res.get("combined_matches") or []
        dim = "combined"
        if not rows:
            rows = res.get("gc_matches") or []
            dim = "gc_only"
        dims_used.add(dim)
        seen = {}
        for row in rows:
            cas = _field(row, "CAS", "CAS #", "cas")
            if not cas:
                continue
            seen.setdefault(cas, row)
        for cas, row in seen.items():
            v = votes.setdefault(cas, {"cas": cas,
                                       "name": _field(row, "Name", "NAME", "name"),
                                       "library_ri": _field(row, "RI", "ri"),
                                       "files": [], "deltas": []})
            v["files"].append(fname)
            lib_ri, pk_ri = v["library_ri"], peak.get("ri")
            if lib_ri is not None and pk_ri is not None:
                try:
                    v["deltas"].append(abs(float(lib_ri) - float(pk_ri)))
                except (TypeError, ValueError):
                    pass

    n_with_peak = len(with_peak)
    candidates = []
    for v in votes.values():
        n = len(v["files"])
        candidates.append({
            "cas": v["cas"], "name": v["name"], "library_ri": v["library_ri"],
            "n_support": n, "n_files_with_peak": n_with_peak,
            # 支持度的分母是「有偵測到峰的檔」，不是全部選取的檔——沒有峰的檔沒有投票權，
            # 把它算進分母會讓每個候選看起來都比實際弱。
            "support": (n / n_with_peak) if n_with_peak else None,
            "mean_abs_delta_ri": (sum(v["deltas"]) / len(v["deltas"])) if v["deltas"] else None,
            "files": sorted(v["files"]),
        })
    candidates.sort(key=lambda c: (-c["n_support"],
                                   c["mean_abs_delta_ri"] if c["mean_abs_delta_ri"] is not None else 9e9))

    def spread(xs):
        return (max(xs) - min(xs)) if len(xs) >= 2 else None

    return {
        "area_id": area.get("area_id"), "area_name": area.get("name"),
        "n_files_selected": len(per_file_peaks),
        "n_files_with_peak": n_with_peak,
        # 沒偵測到峰的檔要點名，不是靜靜消失
        "files_without_peak": sorted(f for f, p in found.items() if p is None),
        "ri_measured_mean": (sum(measured_ri) / len(measured_ri)) if measured_ri else None,
        "ri_spread": spread(measured_ri),
        "drift_spread": spread(measured_drift),
        "match_dimension": ("combined" if dims_used == {"combined"}
                            else ("gc_only" if dims_used == {"gc_only"}
                                  else ("mixed" if dims_used else None))),
        "candidates": candidates,
    }


# --------------------------------------------------------------------------- #
# 排序與呈現 —— 票多的排前面，票數用顏色分級
# --------------------------------------------------------------------------- #

#: 票數佔比 → 分級（0 最弱、4 最強）。**用佔比不用票數**：一組 3 個重複和一組 15 個
#: 重複，「2 票」的意義天差地遠，拿原始票數上色會讓兩種批次的顏色不能互相比較。
VOTE_TIERS = (
    (1.00, 4, "all"),        # 每個檔都有
    (0.85, 3, "most"),
    (None, 2, "quorum"),     # None = 用 min_fraction，隨組別大小變動
    (0.50, 1, "minority"),
    (0.00, 0, "rare"),
)


def vote_tier(votes, total, min_fraction=DEFAULT_MIN_FRACTION):
    """票數的分級與是否過門檻。UI 據此上色；顏色本身留在 UI 層決定。"""
    if not total:
        return {"tier": 0, "label": "rare", "fraction": None, "below_threshold": True}
    frac = votes / total
    need = required_files(total, min_fraction)
    for cut, tier, label in VOTE_TIERS:
        edge = (need / total) if cut is None else cut
        if frac >= edge - 1e-9:
            return {"tier": tier, "label": label, "fraction": frac,
                    "below_threshold": votes < need}
    return {"tier": 0, "label": "rare", "fraction": frac, "below_threshold": True}


def rank_areas(areas, total_files=None, min_fraction=DEFAULT_MIN_FRACTION):
    """票多的排前面。**過不了門檻的不刪除，只標記**。

    排序鍵是 (票數, 最大突出度)——票數相同時，訊號強的先看。

    不刪除是刻意的：使用者少勾一個檔就可能讓真實化合物掉到門檻以下，靜靜消失會讓人
    以為那裡本來就沒東西。同專案 `n_det=None` vs `0`、空白格 vs 0、`check_class_labels`
    只報不改，都是同一條原則。
    """
    total = total_files if total_files is not None else max(
        (a.get("votes_total") or 0) for a in areas) if areas else 0
    out = []
    for a in areas:
        votes = a.get("votes", a.get("n_files_detected") or 0)
        info = vote_tier(votes, total, min_fraction)
        b = dict(a)
        b.update({"votes": votes, "votes_total": total,
                  "vote_tier": info["tier"], "vote_label": info["label"],
                  "vote_fraction": info["fraction"],
                  "below_threshold": info["below_threshold"]})
        out.append(b)
    out.sort(key=lambda x: (-x["votes"], -(x.get("max_prominence") or 0.0)))
    return out


# --------------------------------------------------------------------------- #
# 找峰快取的正確性守門 —— 預處理與互動必須用同一組參數
# --------------------------------------------------------------------------- #

#: 指紋演算法的版本。**改變指紋的算法時要 +1。**
#: 版本不同的舊 sidecar 不算「參數變了」——那是工具變了，資料沒變。碰到不同版本時
#: 退回用 `_peaks2.json` 自己記的參數重新驗證，而不是讓全部檔案白跑一次偵測。
FINGERPRINT_VERSION = 2


def params_fingerprint(rules_config, sigma=1.0, floor_pct=85.0, prom_frac=0.02,
                       min_distance=3, use_baseline=False):
    """把「會改變找峰結果的所有東西」壓成一個指紋字串。

    **為什麼需要這個**：`areas2.detect_one()` 的快取只比對 `baseline_applied` 一項，
    `sigma` / `floor_pct` / `prom_frac` / `min_distance` 都不比，而 `rules_config`
    甚至沒有被記進 `_peaks2.json`——但 R004/R006 的參數會經由 `pre_gate_params()`
    改變哪些峰活得下來（那兩條是**門檻之前**就套用的強制規則）。

    後果很具體：用一組參數預處理、再用另一組互動執行，會靜靜沿用錯的峰，
    而且沒有任何徵兆。這正是本專案一再防的那類問題，所以這裡自己記一份指紋。
    """
    import hashlib
    import json as _json
    # **只有會改變「偵測結果」的規則參數該進指紋。**
    # R004/R006 是強制規則，在突出度門檻**之前**生效（`peaks.pre_gate_params()`），
    # 改了它們就會改變哪些峰活得下來 → 必須重跑偵測。
    # R001/R002/R003/R005 是選配規則，只**標記** `rule_active`、不改變偵測
    # （`rules.mark_rules()` 不移除任何峰），改了它們重新標記即可，重跑 55 秒的
    # 偵測是白費。把整份 config 丟進指紋會讓「調一下 R001」害整批重跑。
    try:
        rules_part = _json.dumps(peaks_mod.pre_gate_params(rules_config),
                                 default=str)
    except Exception:
        rules_part = _json.dumps(rules_config, sort_keys=True, ensure_ascii=False,
                                 default=str)
    blob = _json.dumps({"v": FINGERPRINT_VERSION, "sigma": sigma,
                        "floor_pct": floor_pct, "prom_frac": prom_frac,
                        "min_distance": min_distance,
                        "use_baseline": bool(use_baseline), "rules": rules_part},
                       sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _fingerprint_path(mea_path):
    base = os.path.splitext(os.path.basename(mea_path))[0]
    return os.path.join(areas2.RESULTS_DIR, base + "_peaks2_fp3.json")


def detect_cached(mea_path, rules_config, use_baseline=False, verbose=False, **kw):
    """`areas2.detect_one()` 加上參數指紋守門。

    指紋不合（或沒有指紋）就強制重跑，跑完把指紋寫進**本應用自己的** sidecar
    ——不動 `_peaks2.json` 本身，第二支應用照樣用得到它（隔離規則 2/4）。
    """
    fp = params_fingerprint(rules_config, use_baseline=use_baseline, **kw)
    fp_path = _fingerprint_path(mea_path)
    # **與 `peaks_are_current()` 走同一條判斷**，不要自己再比一次指紋。
    # 自己比的話會漏掉版本回退那一段：指紋演算法改版後，舊 sidecar 會被當成
    # 「參數變了」而整批重跑（實測 18 檔 × 55 秒 ≈ 16 分鐘），但 `peaks_are_current()`
    # 明明判定它們還是最新的。兩邊答案不一致，慢的那個贏。
    cache_ok = peaks_are_current(mea_path, rules_config,
                                 use_baseline=use_baseline, trust_existing=True,
                                 write=False, **kw)
    out = areas2.detect_one(mea_path, rules_config, use_baseline=use_baseline,
                            reuse_cache=cache_ok, verbose=verbose, **kw)
    os.makedirs(areas2.RESULTS_DIR, exist_ok=True)
    with open(fp_path, "w", encoding="utf-8") as f:
        json.dump({"fingerprint": fp, "version": FINGERPRINT_VERSION,
                   "mea": os.path.basename(mea_path)}, f, ensure_ascii=False)
    return out


def peaks_are_current(mea_path, rules_config, use_baseline=False,
                      trust_existing=False, write=True, **kw):
    """這個檔的找峰結果是否已存在**且**是用現在這組參數跑出來的。

    `trust_existing=True`：沒有指紋、但 `_peaks2.json` 自己記的那幾個參數都對得上時，
    就採信它並補寫指紋。**這是有風險的捷徑**——`rules_config` 從來沒被記進那個檔，
    而 R004/R006 的參數會改變結果，所以「參數對得上」只涵蓋一部分。留給使用者
    自己決定要不要用（`--trust-existing`），預設不採信、重跑。

    `write=False`：只回答問題，不寫任何檔。**`--dry-run` 必須用這個**——採信是有
    副作用的（會補寫指紋），而一個會改變狀態的 dry run 比沒有 dry run 更糟。
    """
    if not os.path.exists(areas2._peaks2_path(mea_path)):
        return False
    fp = params_fingerprint(rules_config, use_baseline=use_baseline, **kw)
    fp_path = _fingerprint_path(mea_path)
    if os.path.exists(fp_path):
        try:
            with open(fp_path, "r", encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            rec = {}
        if rec.get("fingerprint") == fp:
            return True
        if rec.get("version", 1) == FINGERPRINT_VERSION:
            return False          # 同版本卻不同指紋 = 參數真的變了，要重跑
        # 版本不同：工具的算法變了，不是使用者的參數變了。往下用記錄的參數重驗。
    if not trust_existing:
        return False
    try:
        with open(areas2._peaks2_path(mea_path), "r", encoding="utf-8") as f:
            rec = json.load(f).get("params", {})
    except (OSError, ValueError):
        return False
    want = {"sigma": kw.get("sigma", 1.0), "floor_pct": kw.get("floor_pct", 85.0),
            "prom_frac": kw.get("prom_frac", 0.02),
            "min_distance": kw.get("min_distance", 3),
            "baseline_applied": bool(use_baseline)}
    if any(rec.get(k) != v for k, v in want.items()):
        return False
    if write:
        os.makedirs(areas2.RESULTS_DIR, exist_ok=True)
        with open(fp_path, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": fp, "mea": os.path.basename(mea_path),
                       "adopted_without_rules_check": True}, f, ensure_ascii=False)
    return True


# --------------------------------------------------------------------------- #
# 「這顆峰算不算數」—— 規則的判定 vs 使用者的判定
# --------------------------------------------------------------------------- #

def effective_active(peak):
    """規則的判定，被使用者的明確選擇覆蓋。

    **兩個鍵不能合成一個**：`rule_active` 是規則說的，`user_active` 是使用者說的
    （`None` = 沒表示意見）。合成一個的話，規則一改就會把使用者手動的選擇沖掉，
    而且分不出「規則否決」與「使用者取消」——漏斗也就報不出正確的數字。
    使用者是最終裁決者（同第一支應用 `effective_active()`）。
    """
    user = peak.get("user_active")
    if user is None:
        return bool(peak.get("rule_active", True))
    return bool(user)


def is_rule_override(peak):
    """使用者把規則否決掉的峰救回來了 —— 值得在畫面上標出來（虛線環）。"""
    return peak.get("user_active") is True and not peak.get("rule_active", True)


def apply_effective(peaks):
    """把 `effective_active()` 的結果寫進 `active`，就地修改後回傳同一份清單。

    **必須做這一步**：`areas2.build_consensus_areas(active_only=True)` 與
    `attach_detection_to_areas()` 讀的是 `peak["active"]`。不同步的話，規則否決的峰
    照樣進共識——畫面上標成灰的、表格標成琥珀色，實際上還是被算進去了。
    """
    for p in peaks:
        p["active"] = effective_active(p)
    return peaks


# --------------------------------------------------------------------------- #
# monomer / dimer 配對 —— 把「同一個化合物的兩個訊號」認出來
# --------------------------------------------------------------------------- #

#: 配對的門檻。RT 幾乎相同、drift 明顯不同、跨檔強度高度同步。
#: 數值取自實測：Coffee-bean 上以 (6 s, 0.10, 0.90) 找回操作者標註的 4 對中的 3 對，
#: 另外找到 4 對他沒標的。放寬 r 會開始收進不相干的鄰居，收緊會漏掉真的配對。
MD_RT_TOL_S = 6.0
MD_MIN_DRIFT_GAP = 0.10
MD_MIN_CORR = 0.90


def find_monomer_dimer_pairs(areas, profiles, rt_tol_s=MD_RT_TOL_S,
                             min_drift_gap=MD_MIN_DRIFT_GAP,
                             min_corr=MD_MIN_CORR):
    """找出可能是同一化合物 monomer / dimer 的區域配對。

    **原理**：IMS 裡同一個化合物在低濃度多半是單分子離子（monomer），濃度高時兩個
    分子結合成雙分子離子（dimer）。兩者是同一種物質、同時離開 GC 管柱，所以**保留
    時間相同**；但 dimer 比較大比較重、漂移比較慢，所以**漂移時間明顯較長**。
    在熱圖上就是「同一高度、左右分開的兩點」。

    判定完全不靠資料庫，只靠這批檔案自己：
      1. 保留時間幾乎相同（`rt_tol_s`）
      2. 漂移差得夠開（`min_drift_gap`）——不然只是同一顆峰被切成兩半
      3. **跨檔強度高度同步**（`min_corr`）——同一個化合物的兩個訊號會一起漲一起落。
         這一條是關鍵，也是 LC-MS 那邊 CAMERA / RAMClust 用來分辨加成物的同一招。

    `profiles` 是「每個檔在每個區域量到的強度」（`measure_profile()` 的輸出），
    列＝檔案、欄＝區域。**至少要 3 個檔**，理由同 `similarity_matrix()`。

    回傳 `(kept, rejected)`。`monomer` / `dimer` 是 `areas` 的索引；**漂移較小的
    那個當 monomer**（dimer 較大較重、漂移較長，實測 4/4 都符合）。一個區域只會
    屬於一組配對，落選的放在 `rejected` 並附理由。

    ⚠ **這是提示，不是判定。** 兩個區域同步也可能只是同一個來源的兩種產物、或恰好
    共變。實測把它當比對條件（要求同一個化合物同時解釋兩邊）只讓候選少 27%，而且
    沒有還原出操作者標註的名字——所以這裡只回報配對，不去改比對結果。
    """
    n_files = len(profiles)
    if n_files < 3:
        raise ValueError(
            "配對需要至少 3 個檔（目前 %d 個）：判斷「兩個區域是否同步」靠的是跨檔"
            "相關係數，兩個檔時它恆為 ±1。" % n_files)

    X = np.array([[np.nan if v is None else float(v) for v in p] for p in profiles],
                 dtype=float)
    usable = ~np.isnan(X).any(axis=0)          # 任一檔缺值的區域不參與
    idx = [i for i, ok in enumerate(usable) if ok]
    if len(idx) < 2:
        return [], []
    # log10 壓縮動態範圍，理由同 similarity_matrix()；這裡**不做逐檔置中**——
    # 我們要的正是「兩個訊號一起漲落」，整體強度的共同變化是訊號不是雜訊。
    Xl = np.log10(np.clip(X[:, usable], 0.0, None) + 1.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(Xl.T)

    out = []
    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            ia, ib = idx[a], idx[b]
            A, B = areas[ia], areas[ib]
            if abs(A["rt_center_s"] - B["rt_center_s"]) > rt_tol_s:
                continue
            gap = abs(A["drift_center"] - B["drift_center"])
            if gap < min_drift_gap:
                continue
            r = corr[a, b]
            if not np.isfinite(r) or r < min_corr:
                continue
            # 漂移小的是 monomer：dimer 比較大比較重，走得比較慢
            mono, dim = (ia, ib) if A["drift_center"] <= B["drift_center"] else (ib, ia)
            out.append({
                "monomer": mono, "dimer": dim, "r": float(r),
                "rt_center_s": (A["rt_center_s"] + B["rt_center_s"]) / 2.0,
                "drift_monomer": areas[mono]["drift_center"],
                "drift_dimer": areas[dim]["drift_center"],
                "drift_gap": round(gap, 4),
            })
    # **一個區域只能屬於一組配對。** 一個化合物有一個 monomer 與一個 dimer；
    # 實測出現過 #16 同時和 #17 與 #14 配成對，那至少有一組是錯的（或其中之一是
    # trimer、或根本是鄰近的另一個化合物）。依相關係數由高而低貪婪配對，配過的
    # 就不再參與——不這樣做的話 `annotate_pairs()` 會後寫蓋掉先寫，而且「有幾個
    # 不同化合物」也會算錯。落選的配對留在 `rejected`，不靜靜丟掉。
    out.sort(key=lambda x: -x["r"])
    used, kept, rejected = set(), [], []
    for cand in out:
        if cand["monomer"] in used or cand["dimer"] in used:
            rejected.append(cand)
            continue
        used.add(cand["monomer"])
        used.add(cand["dimer"])
        kept.append(cand)
    for cand in rejected:
        cand["rejected_reason"] = "區域已配給相關性更高的另一組"
    return kept, rejected


def annotate_pairs(areas, pairs):
    """把配對結果就地寫回 `areas`（`md_role` / `md_partner` / `md_r`）。

    只加註記、不合併也不刪除任何區域——配對是提示不是判定，把兩個區域併成一個
    會讓「其實不是一對」的情況再也看不出來。
    """
    for a in areas:
        a.setdefault("md_role", None)
        a.setdefault("md_partner", None)
        a.setdefault("md_r", None)
    for p in pairs:
        areas[p["monomer"]].update({"md_role": "monomer", "md_partner": p["dimer"],
                                    "md_r": p["r"]})
        areas[p["dimer"]].update({"md_role": "dimer", "md_partner": p["monomer"],
                                  "md_r": p["r"]})
    return areas

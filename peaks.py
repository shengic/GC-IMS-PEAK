"""
peaks.py  —  GC-IMS 峰偵測（第一層：偵測 / 測量；measure-only v1）
Version: 3.1 — by Albert Sheng

變更記錄：
  3.1  — load_surface() 從 .npz 載入時改用 npz 內的 mea_source 當 meta["source"]，
         修正 RI 校正靜默失效（原本指向 .npz 自己，資料夾被解析成 results/，
         那裡沒有 STD，y 軸就無聲退回保留時間）；_bg.json 幾何新增
         rip_index / rip_drift_ms 供 UI 的軸說明使用；--ri-series 指定了卻
         拿不到校正時改對 stderr 發警告；移除熱圖右上角的軸標註。
  2.1  — 強制規則 R004/R006 移到突出度門檻**之前**（門檻是相對值，RIP 會把它
         墊高數倍並誤殺真峰）；抽出 select_from_maxima() 供 UI 規則面板共用；
         新增 <name>_maxima.npz 快取（25 萬筆原始極大，使規則可即時重算）；
         新增 write_bg() 與 --bg-only（只從 .npz 重畫背景圖，不做找峰）；
         規則改用 mark_rules()，被否決的峰保留在輸出中並標記 rule_active。
  ver.03 — 串接 rip.py（Identify Workflow 第一階段）：載入強度面後立即算 rip_index
           / rip_intensity（fail-fast），偵測完成後為每個峰補 drift_relative 欄位，
           並寫進 stats。R004 從此可用。
  ver.02 — 修復 write_overlay() 在真實尺寸矩陣上 fig.savefig() 時 OOM 崩潰的問題，
           同 readGAS.py ver.02 的修法（新增 _downsample_for_display()）。
  ver.01 — 初版。

依工作流文件 §3：以 **持續同調 / 突出度 (persistent homology / prominence)** 為主幹,
作用在原始 int16 強度面上（原始資料模式，非影像模式，故不需 colormap 反演）。

本版實作 workflow 步驟 [3]+[4]：
  [3] 候選極大值初選（union-find 掃描自然產生所有局部極大）
  [4] 對每候選測量：突出度 / 平坦度 / 邊界距離 / 是否飽和 / 座標
  —— **只測量、輕度過濾**，門檻校準（步驟 [5]）待人工峰清單到位再做。

輸入：
  - readGAS.py 匯出的 .npz（intensity + drift_ms + retention_s），或
  - 直接給 .mea（內部呼叫 readGAS.read_mea）

輸出（results/，與輸入同檔名 base）：
  - <name>_peaks.csv   ← 精簡：peak_id, retention_s, drift_ms, intensity
  - <name>_peaks.json  ← 完整：上述 + 突出度/平坦度/邊界/飽和/索引/drift_relative
                          + rule_active/active + 偵測參數與規則設定，
                          stats 內含 rip_index / rip_intensity 與候選漏斗各級數量
  - <name>_maxima.npz  ← 全部原始局部極大的快取（~2.4 MB），讓 UI 能在記憶體重跑
                          整條漏斗（含門檻重算）約 4 ms，不必重跑 83 秒的 union-find
  - <name>_bg.png      ← 無圈熱圖，互動畫布的背景（圈是 Canvas 原生物件）
  - <name>_bg.json     ← 資料區在 _bg.png 裡的 bbox 與軸範圍，UI 靠它擺放圓圈
  - <name>_overlay.png ← 熱圖疊上通過規則的峰（靜態佐證圖，供 VOCal 目視比對）

依賴：numpy, scipy, scikit-image, matplotlib
    pip install numpy scipy scikit-image matplotlib

用法：
    python peaks.py                          # 跳檔案總管（預設 results/，選 .npz 或 .mea）
    python peaks.py results/xxx.npz
    python peaks.py GAS/.../xxx.mea --sigma 1.0 --floor-pct 85 --prom-frac 0.02
    python peaks.py results/xxx.npz --top-n 200   # overlay 只標前 200 顯眼峰
"""

import argparse
import datetime
import json
import os
import sys

import numpy as np

import rip
import rules
from gas_utils import PROJECT_DIR, resolve_input_path

RESULTS_DIR = os.path.join(PROJECT_DIR, "results")
RULES_CONFIG = os.path.join(PROJECT_DIR, "rules_config.json")
SATURATION = 32767  # int16 滿格


# --------------------------------------------------------------------------- #
# 載入強度面
# --------------------------------------------------------------------------- #
def load_surface(path):
    """回傳 (intensity, drift_ms, retention_s, meta)。支援 .npz 與 .mea。

    meta 一定含 `rt_axis_version`：舊版 `.npz` 沒有這個欄位，視為版本 1，代表它的
    retention_s 是用少了 +1 的舊公式算的（比實際短 16.7%）。這裡只警告不阻擋——
    強度矩陣與漂移軸仍然正確，找峰照樣能跑，錯的只有保留時間；但不警告就會出現
    「同一個 results/ 底下新舊 RT 混用而毫無跡象」的情況。
    """
    import readGAS
    ext = os.path.splitext(path)[1].lower()
    if ext == ".npz":
        z = np.load(path)
        ver = int(z["rt_axis_version"]) if "rt_axis_version" in z.files else 1
        # source 要指回原始 .mea：RI 校正靠「該 .mea 所在資料夾裡的 STD」解析，
        # 指到 .npz 自己會讓資料夾變成 results/（沒有 STD）而讓 RI 靜默失效。
        # 舊的 .npz 沒有 mea_source，只能沿用 npz 路徑（行為同以往）。
        src = str(z["mea_source"]) if "mea_source" in z.files else ""
        meta = {"source": src or path, "rt_axis_version": ver}
        warn_if_stale_rt_axis(ver, path)
        return z["intensity"], z["drift_ms"], z["retention_s"], meta
    elif ext == ".mea":
        data, header, axes = readGAS.read_mea(path)
        return data, axes["drift_ms"], axes["retention_s"], {
            "source": path,
            "machine": header.get("Machine type"),
            "sample": header.get("Sample"),
            "rt_axis_version": axes.get("rt_axis_version", readGAS.RT_AXIS_VERSION),
        }
    raise ValueError(f"不支援的副檔名：{ext}（請給 .npz 或 .mea）")


def warn_if_stale_rt_axis(version, path=""):
    """版本落後就印警告，回傳 True 表示這份產物的 RT 軸已過時。"""
    import readGAS
    if version is None or version >= readGAS.RT_AXIS_VERSION:
        return False
    print(f"  ⚠ 這份產物的保留時間軸是舊版公式（rt_axis_version={version}，"
          f"現行={readGAS.RT_AXIS_VERSION}）：{os.path.basename(path)}")
    print(f"    其 retention_s 比實際短約 16.7%（少了 (averages+1) 的 +1）。"
          f"強度/漂移軸與找峰不受影響，RI 也不受影響，但 RT 相關輸出不可信。")
    print(f"    重新讀取原始 .mea 即可更新（會覆寫 .npz）。")
    return True


# --------------------------------------------------------------------------- #
# 突出度：union-find 持續同調（在 floor 之上的像素上計算）
# --------------------------------------------------------------------------- #
def compute_prominence(img, floor):
    """回傳 list[(pixel_index, value, prominence)]，每個局部極大一筆。

    淹水模型：由高往低處理像素；新極大誕生(born=峰高)，兩盆地相連時較矮者死亡
    (die=鞍點高)，prominence = born − die。只處理 > floor 的像素以加速
    （floor 以下視為海平面；未與更高峰相連者其 prominence = born − floor，為下界）。
    """
    H, W = img.shape
    flat = img.ravel()
    cand = np.nonzero(flat > floor)[0]
    if cand.size == 0:
        return []
    order = cand[np.argsort(flat[cand], kind="stable")[::-1]]  # 由高到低

    UNSET = -1
    parent = np.full(flat.size, UNSET, dtype=np.int64)
    birth_val = {}   # root pixel -> 誕生峰高
    birth_pix = {}   # root pixel -> 該峰的極大像素
    prom = {}        # 極大像素 -> 突出度

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:       # 路徑壓縮
            parent[x], x = root, parent[x]
        return root

    for p in order:
        r, c = divmod(p, W)
        roots = set()
        for dr in (-1, 0, 1):
            rr = r + dr
            if rr < 0 or rr >= H:
                continue
            base = rr * W
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                cc = c + dc
                if cc < 0 or cc >= W:
                    continue
                q = base + cc
                if parent[q] != UNSET:
                    roots.add(find(q))

        if not roots:                       # 新的局部極大誕生
            parent[p] = p
            birth_val[p] = flat[p]
            birth_pix[p] = p
        else:                               # 加入既有盆地（必要時合併）
            rs = sorted(roots, key=lambda x: birth_val[x], reverse=True)
            keep = rs[0]
            parent[p] = keep
            saddle = flat[p]
            for x in rs[1:]:                 # 較矮的峰在此鞍點死亡
                prom[birth_pix[x]] = birth_val[x] - saddle
                parent[x] = keep

    for root, bpix in birth_pix.items():     # 從未死亡的（含全域最高）→ 下界
        if find(root) == root and bpix not in prom:
            prom[bpix] = birth_val[root] - floor

    return [(pix, float(flat[pix]), float(prom[pix])) for pix in prom]


# --------------------------------------------------------------------------- #
# 平坦度（workflow §3.2）：頂部尺度 / 整峰尺度
# --------------------------------------------------------------------------- #
def flatness_score(img, r, c, value, floor, win=200):
    """以峰為種子做局部 flood，量頂部/整峰等效半徑比。尖峰→~0，plateau→~1。"""
    from skimage.segmentation import flood

    H, W = img.shape
    r0, r1 = max(0, r - win), min(H, r + win + 1)
    c0, c1 = max(0, c - win), min(W, c + win + 1)
    crop = img[r0:r1, c0:c1].astype(np.float64)
    seed = (r - r0, c - c0)

    height = max(value - floor, 1e-9)
    d_small = 0.02 * height      # 頂部 δ ≈ 峰高 2%
    d_large = 0.50 * height      # 整峰 δ ≈ 峰高 50%

    a_top = flood(crop, seed, tolerance=d_small).sum()
    m_whole = flood(crop, seed, tolerance=d_large)
    a_whole = m_whole.sum()

    r_top = np.sqrt(a_top / np.pi)
    r_whole = np.sqrt(a_whole / np.pi)
    flat = float(r_top / r_whole) if r_whole > 0 else 0.0
    # 整峰碰到視窗邊緣 → 半徑被截斷，平坦度僅為估計
    truncated = bool(m_whole[0, :].any() or m_whole[-1, :].any()
                     or m_whole[:, 0].any() or m_whole[:, -1].any())
    return flat, truncated


# --------------------------------------------------------------------------- #
# 偵測主流程
# --------------------------------------------------------------------------- #
def pre_gate_params(rules_config):
    """從 rules_config 抽出必須在突出度門檻之前生效的兩條規則的參數。

    R004 / R006 是強制規則（rules.mandatory_rules()），定義了峰編號的基準集合，
    所以不能等偵測完再篩——見 select_from_maxima() 的說明。CLI 與 UI 都呼叫這
    個函式，確保兩邊從同一份 config 抽出同樣的值。

    回傳 (rip_half_width, exclude_before_rip, rip_boundary)。
    """
    by_number = {e.get("rule_number"): e for e in rules_config}
    r004 = by_number.get("R004") or {}
    r006 = by_number.get("R006") or {}
    half_width = (float(r004.get("params", {}).get("half_width", 0.02))
                  if r004.get("enabled") else 0.0)
    exclude_before = bool(r006.get("enabled"))
    boundary = float(r006.get("params", {}).get("boundary", 1.0))
    return half_width, exclude_before, boundary


def select_from_maxima(pix, val_smooth, prom, val_raw, flatness, shape, floor,
                       prom_frac=0.02, min_prominence=0.0, min_distance=3,
                       top_n=0, rip_index=None, rip_half_width=0.0,
                       exclude_before_rip=False, rip_boundary=1.0, verbose=True):
    """原始局部極大 → 選出的峰清單。純選取，不做任何量測。

    detect_peaks() 與 main.py 的規則面板共用這一份實作：面板要能在使用者切換
    R004 / 改 half_width 時即時重算（含突出度門檻重算），而門檻是相對值，
    必須跟 CLI 用同一套算法，否則面板顯示的結果會與實際重跑偵測的結果不一致。

    參數皆為等長的一維 numpy 陣列（來自 <name>_maxima.npz）：
      pix        —— r*W + c 的扁平索引
      val_smooth —— 平滑後強度（flatness_score 用）
      prom       —— 突出度
      val_raw    —— 原始未平滑強度（回報值與飽和判定用）
      flatness   —— 平坦度，未量測者為 NaN；None 表示整批都未量測

    回傳 (peaks, stats)，peaks 尚未有 retention_s/drift_ms（由 attach_coords 補）。
    """
    H, W = int(shape[0]), int(shape[1])
    pix = np.asarray(pix)
    val_smooth = np.asarray(val_smooth, dtype=np.float64)
    prom = np.asarray(prom, dtype=np.float64)
    val_raw = np.asarray(val_raw)
    flat_arr = None if flatness is None else np.asarray(flatness, dtype=np.float64)

    n_raw_maxima = int(pix.size)
    stats = {"floor": float(floor), "n_raw_maxima": n_raw_maxima,
             "n_rip_excluded": 0, "rip_half_width": float(rip_half_width or 0.0),
             "n_after_prom": 0, "n_after_distance": 0, "n_final": 0,
             "max_prominence": 0.0, "prom_threshold": 0.0}
    if n_raw_maxima == 0:
        return [], stats

    # 門檻前的漂移軸裁切。drift_relative = dt_index / rip_index，所以條件都能
    # 直接換算成 dt_index 的範圍，不必先建 peak 字典（25 萬筆，太貴）。
    #   R004  排除 |drift_relative − 1| ≤ half_width      → RIP 柱狀帶本身
    #   R006  排除 drift_relative ≤ boundary (預設 1.0)   → 比 RIP 快，非分析物
    # 兩者同時啟用時聯集為單邊切 drift_relative > 1 + half_width。
    #
    # 這兩刀必須落在 max_prominence 之前（見本函式 docstring）：門檻是相對值，
    # 而被切掉的區域正是全圖最強訊號所在，留著會把門檻墊高數倍。
    keep = np.ones(n_raw_maxima, dtype=bool)
    if rip_index:
        cols = pix % W
        cut_hi = 0.0        # dt_index ≤ cut_hi 一律剔除
        if exclude_before_rip:
            cut_hi = max(cut_hi, rip_index * float(rip_boundary))
        if rip_half_width and rip_half_width > 0:
            lo = rip_index * (1.0 - rip_half_width)
            hi = rip_index * (1.0 + rip_half_width)
            keep &= ~((cols >= lo) & (cols <= hi))
            cut_hi = max(cut_hi, hi) if exclude_before_rip else cut_hi
        if cut_hi > 0:
            keep &= cols > cut_hi
        stats["n_rip_excluded"] = int(n_raw_maxima - int(keep.sum()))
        stats["drift_cut_dt_index"] = round(float(cut_hi), 2)
        if verbose and stats["n_rip_excluded"]:
            print(f"  門檻前裁切：R004 half_width={rip_half_width}"
                  f"{' + R006 boundary=' + str(rip_boundary) if exclude_before_rip else ''}"
                  f" → dt_index > {cut_hi:.1f} 才保留，剔除 {stats['n_rip_excluded']}")
    if not keep.any():
        return [], stats

    # 突出度門檻是相對值 → max_prominence 必須在 RIP 帶剔除之後才算
    max_prom = float(prom[keep].max())
    thresh = max(float(min_prominence), float(prom_frac) * max_prom)
    keep &= prom >= thresh
    stats["max_prominence"] = round(max_prom, 2)
    stats["prom_threshold"] = round(thresh, 2)
    stats["n_after_prom"] = int(keep.sum())
    if verbose:
        print(f"  突出度門檻 {thresh:.1f} (=max({min_prominence}, {prom_frac}×{max_prom:.0f})) "
              f"→ 保留 {stats['n_after_prom']}")

    order = np.flatnonzero(keep)
    order = order[np.argsort(-prom[order], kind="stable")]   # 依突出度降冪

    # 近距離去重（平滑後仍可能有貼很近的雙極大）：保留突出度高者
    if min_distance and min_distance > 0:
        occupied = np.zeros((H, W), dtype=bool)
        md = int(min_distance)
        chosen = []
        for idx in order:
            r, c = divmod(int(pix[idx]), W)
            r0, r1 = max(0, r - md), min(H, r + md + 1)
            c0, c1 = max(0, c - md), min(W, c + md + 1)
            if occupied[r0:r1, c0:c1].any():
                continue
            occupied[r, c] = True
            chosen.append(idx)
        order = np.array(chosen, dtype=int)
    stats["n_after_distance"] = int(order.size)
    if verbose:
        print(f"  min_distance={min_distance} 去重 → {order.size} 個峰")

    if top_n and top_n > 0:
        order = order[:int(top_n)]

    peaks = []
    for i, idx in enumerate(order, start=1):
        r, c = divmod(int(pix[idx]), W)
        raw_i = int(val_raw[idx])
        flat = None
        if flat_arr is not None and not np.isnan(flat_arr[idx]):
            flat = round(float(flat_arr[idx]), 4)
        peaks.append({
            "peak_id": i,
            "rt_index": int(r),
            "dt_index": int(c),
            "intensity": raw_i,                  # 回報原始（未平滑）強度
            "prominence": round(float(prom[idx]), 2),
            "flatness": flat,
            "edge_dist": int(min(r, H - 1 - r, c, W - 1 - c)),
            "saturated": bool(abs(raw_i) >= SATURATION),
            "rank": i,
            "_maxima_index": int(idx),           # 回指 _maxima.npz，供量測補值
            "_val_smooth": float(val_smooth[idx]),
        })
    stats["n_final"] = len(peaks)
    return peaks, stats


def detect_peaks(intensity, sigma=1.0, floor_pct=85.0, prom_frac=0.02,
                 min_prominence=0.0, min_distance=3, top_n=0, flat_win=200,
                 rip_index=None, rip_half_width=0.0, exclude_before_rip=False,
                 rip_boundary=1.0, return_maxima=False):
    """rip_index / rip_half_width 給定時，在算 max_prominence 之前先把 RIP 柱狀帶
    的候選極大剔除（即 R004 的偵測層前置版本）。

    理由：突出度門檻是相對值（prom_frac × max_prominence），而 RIP 本身通常就是
    全圖最大突出度（實測 A_1_3：RIP prom=5052，非 RIP 最大僅 1545），會把門檻墊高
    2～3 倍，誤殺真實分析物峰。R004 若只接在偵測之後，圈雖然消失，被墊高的門檻
    誤殺的峰救不回來——所以這一刀必須落在 max_prominence 之前。
    rip_half_width=0（預設）時完全不啟用，行為與 ver.03 相同。
    """
    from scipy.ndimage import gaussian_filter

    H, W = intensity.shape
    work = intensity.astype(np.float32)
    if sigma and sigma > 0:
        work = gaussian_filter(work, sigma=sigma)   # 輕度平滑：破壞 int16 同值平台、抑雜訊

    floor = float(np.percentile(work, floor_pct))
    print(f"  平滑 σ={sigma}，floor=第{floor_pct}百分位={floor:.1f}")

    raw = compute_prominence(work, floor)
    print(f"  union-find 找到 {len(raw)} 個局部極大（floor 之上）")
    if not raw:
        empty = {"floor": floor, "n_raw_maxima": 0}
        return ([], empty, None) if return_maxima else ([], empty)

    # 拆成平行陣列：select_from_maxima() 與 _maxima.npz 快取共用同一份表示
    pix = np.fromiter((m[0] for m in raw), dtype=np.int64, count=len(raw))
    val_smooth = np.fromiter((m[1] for m in raw), dtype=np.float32, count=len(raw))
    prom = np.fromiter((m[2] for m in raw), dtype=np.float32, count=len(raw))
    rr, cc = np.divmod(pix, W)
    val_raw = intensity[rr, cc].astype(np.int32)
    flat_arr = np.full(len(raw), np.nan, dtype=np.float32)

    peaks, stats = select_from_maxima(
        pix, val_smooth, prom, val_raw, flat_arr, (H, W), floor,
        prom_frac=prom_frac, min_prominence=min_prominence,
        min_distance=min_distance, top_n=top_n,
        rip_index=rip_index, rip_half_width=rip_half_width,
        exclude_before_rip=exclude_before_rip, rip_boundary=rip_boundary)

    # 平坦度只對選中的峰量測（每筆要做兩次 flood fill，不可能對 25 萬個都算）
    for p in peaks:
        flat, trunc = flatness_score(
            work, p["rt_index"], p["dt_index"], p.pop("_val_smooth"), floor, win=flat_win)
        p["flatness"] = round(flat, 4)
        p["flatness_truncated"] = trunc
        flat_arr[p["_maxima_index"]] = flat

    if return_maxima:
        maxima = {"pix": pix, "val_smooth": val_smooth, "prom": prom,
                  "val_raw": val_raw, "flatness": flat_arr}
        return peaks, stats, maxima
    return peaks, stats


# --------------------------------------------------------------------------- #
# 輸出
# --------------------------------------------------------------------------- #
def attach_coords(peaks, drift_ms, retention_s):
    for p in peaks:
        p["retention_s"] = round(float(retention_s[p["rt_index"]]), 4)
        p["drift_ms"] = round(float(drift_ms[p["dt_index"]]), 5)
    return peaks


def write_csv(peaks, path):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("peak_id,retention_s,drift_ms,intensity\n")
        for p in peaks:
            f.write(f"{p['peak_id']},{p['retention_s']},{p['drift_ms']},{p['intensity']}\n")
    print(f"  CSV  → {path}  ({len(peaks)} 峰)")


def write_json(peaks, path, params, stats, meta, shape, geom=None):
    import readGAS
    doc = {
        "source": meta.get("source"),
        "machine": meta.get("machine"),
        "sample": meta.get("sample"),
        # 跟著峰一起走，讓 UI/報告能判斷這批峰的 retention_s 是哪一版軸算的
        "rt_axis_version": meta.get("rt_axis_version", readGAS.RT_AXIS_VERSION),
        "detected_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "matrix_shape": [int(shape[0]), int(shape[1])],
        "detection_params": params,
        "stats": stats,
        "canvas_geometry": geom,   # 資料區在 _bg.png 裡的位置，UI 畫圈用
        "n_peaks": len(peaks),
        "peaks": peaks,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"  JSON → {path}")


def write_bg(intensity, drift_ms, retention_s, out_base, rip_index=None,
             cmap="viridis", figsize=(8, 9), dpi=150, ri_calibration=None):
    """Render the circle-free background image + its geometry sidecar.

    Everything this needs (intensity + both axes) lives in the `.npz`, so the
    display can be rebuilt without going back to the `.mea` — ~5 s instead of
    the ~13 s a full re-read costs.

    The geometry goes to `<base>_bg.json` rather than `_peaks.json` because
    whoever writes the PNG must write the matching geometry: a bg-only render
    produces no peaks file, and geometry that disagrees with the image it
    describes would put every circle in the wrong place.

    ri_calibration 提供時，背景圖 y 軸標成 RI（顯示層 relabel）；幾何仍以
    retention_s 記錄，故 Canvas 圈位置不受影響。
    """
    geom = write_overlay(intensity, drift_ms, retention_s, [], out_base + "_bg.png",
                         cmap=cmap, figsize=figsize, dpi=dpi, show=False,
                         rip_index=rip_index, draw_circles=False,
                         title="GC-IMS heatmap", ri_calibration=ri_calibration)
    with open(out_base + "_bg.json", "w", encoding="utf-8") as f:
        json.dump(geom, f, ensure_ascii=False, indent=2)
    print(f"  GEOM → {out_base}_bg.json")
    return geom


def write_maxima_npz(path, maxima, shape, floor, rip_index, drift_ms, retention_s):
    """快取 compute_prominence() 的全部原始局部極大（未經任何篩選）。

    存在的理由：突出度門檻是相對值，R004 的 half_width 一改就會改變
    max_prominence，進而改變「有哪些峰存在」——若不快取，UI 的規則面板每切一次
    R004 就得重跑 83 秒的 union-find。有了這份快取，main.py 可以在記憶體裡重跑
    整條漏斗（含門檻重算），五條規則全部即時。

    ~25 萬筆 × (int64 + 3×float32/int32) ≈ 4 MB，另含兩軸（自足，不必回頭讀 .npz）。
    """
    np.savez_compressed(
        path,
        pix=maxima["pix"], val_smooth=maxima["val_smooth"], prom=maxima["prom"],
        val_raw=maxima["val_raw"], flatness=maxima["flatness"],
        shape=np.array(shape, dtype=np.int64), floor=np.float64(floor),
        rip_index=np.int64(rip_index), drift_ms=drift_ms, retention_s=retention_s,
    )
    size_mb = os.path.getsize(path) / 1e6
    print(f"  MAXIMA → {path}  ({maxima['pix'].size:,} 筆, {size_mb:.1f} MB)")


def load_maxima_npz(path):
    """讀回 write_maxima_npz() 的快取，回傳 dict。找不到檔案時回傳 None。"""
    if not os.path.exists(path):
        return None
    with np.load(path) as z:
        return {k: z[k] for k in z.files}


def _downsample_for_display(img, figsize, dpi, margin=1.5):
    """把矩陣降到接近實際輸出像素數（留 margin 倍餘裕避免鋸齒）再拿去畫圖。

    與 readGAS.py 的同名函式邏輯完全一致（兩處各自獨立維護一份，避免
    引入跨檔案 import 依賴；若未來要重構，這兩份應合併成共用工具函式）。
    臭蟲背景：fig.savefig() 光柵化整個未降採樣的矩陣時，記憶體會暴增到
    系統上限被 OOM killer 砍掉，已用真實資料重現、定位到 savefig() 這一步。
    """
    target_h = max(1, int(figsize[1] * dpi * margin))
    target_w = max(1, int(figsize[0] * dpi * margin))
    step_r = max(1, img.shape[0] // target_h)
    step_c = max(1, img.shape[1] // target_w)
    if step_r == 1 and step_c == 1:
        return img
    return img[::step_r, ::step_c]


def write_overlay(intensity, drift_ms, retention_s, peaks, path,
                  cmap="viridis", mark_n=0, figsize=(8, 9), dpi=150, show=False,
                  rip_index=None, draw_circles=True, title=None, ri_calibration=None):
    """Render overlay heatmap with peak circles.

    橫軸使用 drift_relative（本次使用者決策，見 readGAS.plot_heatmap 註解）。
    rip_index 未提供時就地呼叫 rip.find_rip() 算；scatter 紅圈用 peak 內建的
    drift_relative 欄位（由 rip.attach_drift_relative 補上）。

    draw_circles=False 時只畫熱圖不畫圈，供 main.py 的互動畫布當背景——圈在那邊
    是 Canvas 原生物件（可個別 toggle），不能烤進像素裡。

    回傳 geom dict：資料區在 PNG 裡的確切位置與座標範圍。沒有這個，UI 只能猜
    matplotlib 的邊界，峰的圈就會對不準（既有 highlight_peak_on_overlay() 正是
    因為假設資料區佔滿整張 PNG 而畫偏）。
    """
    import matplotlib
    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sub = intensity[::max(1, intensity.shape[0] // 1000),
                    ::max(1, intensity.shape[1] // 1000)]
    vmin, vmax = np.percentile(sub, [1.0, 99.5])

    if rip_index is None:
        rip_index, _ = rip.find_rip(intensity)
    drift_norm = drift_ms / drift_ms[rip_index]
    drift_label = "Drift relative to RIP (RIP at 1.0)"

    # 畫圖前降到接近輸出解析度，避免 savefig() 光柵化整個原始矩陣爆記憶體
    img_ds = _downsample_for_display(intensity, figsize, dpi)

    # 第四階段：校準就緒 → 沿 y 把顯示影像重採樣成「均勻於 RI」，讓 RI 軸**線性
    # （非 log 刻度）**。x 軸 drift 正規化不動；scatter 與幾何 y 座標一律改用 RI。
    y_extent = (float(retention_s[0]), float(retention_s[-1]))
    y_label = "Retention time [s]"
    y_axis_kind = "retention_s"
    ri_fn = None
    if ri_calibration is not None:
        try:
            import calibration as _cal
            row_rts = np.linspace(float(retention_s[0]), float(retention_s[-1]),
                                  img_ds.shape[0])
            warped, ri_lo, ri_hi = _cal.warp_rows_to_ri(img_ds, row_rts, ri_calibration)
            if ri_lo is not None:
                img_ds = warped
                y_extent = (ri_lo, ri_hi)
                y_label = "Retention Index (RI)"
                y_axis_kind = "ri"
                ri_fn = _cal.make_rt_to_ri(ri_calibration)
        except Exception as _e:
            print(f"  [warn] RI warp skipped ({_e})")

    # constrained_layout：視窗縮放時自動重排
    fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)
    ax.imshow(img_ds, aspect="auto", origin="lower",
              extent=[drift_norm[0], drift_norm[-1], y_extent[0], y_extent[1]],
              cmap=cmap, vmin=vmin, vmax=vmax, interpolation="nearest")
    shown = peaks[:mark_n] if mark_n and mark_n > 0 else peaks
    if draw_circles:
        xs = [p.get("drift_relative", p["drift_ms"] / drift_ms[rip_index]) for p in shown]
        ys = ([ri_fn(p["retention_s"])[0] for p in shown] if ri_fn is not None
              else [p["retention_s"] for p in shown])
        ax.scatter(xs, ys, s=28, facecolors="none", edgecolors="red", linewidths=0.8)
    ax.set_xlabel(drift_label)
    ax.set_ylabel(y_label)

    # 每 0.5 一格 major tick（跟 readGAS.plot_heatmap 一致）
    from matplotlib.ticker import MultipleLocator, FormatStrFormatter
    ax.xaxis.set_major_locator(MultipleLocator(0.5))
    ax.xaxis.set_minor_locator(MultipleLocator(0.1))
    ax.xaxis.set_major_formatter(FormatStrFormatter("%.1f"))


    ax.set_title(title if title is not None
                 else f"Detected {len(shown)} peaks (red = detected)")
    fig.savefig(path, dpi=dpi)

    # constrained_layout 的軸位置要等 draw 之後才定案；savefig 已觸發一次 draw。
    # bbox 為 figure fraction（y 由下往上），UI 換算成像素時要翻轉 y。
    bbox = ax.get_position()
    geom = {
        "png_size": [int(round(figsize[0] * dpi)), int(round(figsize[1] * dpi))],
        "axes_bbox": [round(bbox.x0, 6), round(bbox.y0, 6),
                      round(bbox.width, 6), round(bbox.height, 6)],
        "xlim": [float(v) for v in ax.get_xlim()],
        "ylim": [float(v) for v in ax.get_ylim()],
        # y 軸座標種類：'ri' 時 UI 要用 peak['ri'] 擺圈，'retention_s' 時用保留時間
        "y_axis": y_axis_kind,
        # RIP 跟著幾何一起走，UI 的「ⓘ 軸說明」才能講出跟這張圖一致的正規化基準
        # ——否則得回頭載 .npz 重算，還可能算出跟畫面不同的值。
        "rip_index": int(rip_index),
        "rip_drift_ms": float(drift_ms[rip_index]),
    }
    print(f"  PNG  -> {path}  (marked {len(shown) if draw_circles else 0} peaks)")
    if show:
        plt.show()
    else:
        plt.close(fig)
    return geom


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def _use_utf8_stdout():
    """Windows 主控台預設 cp950，印不出 µ / 中文以外的字元就整支崩掉（實際發生過：
    print_summary 的 'µs' 讓 readGAS.py 在 cp950 下 UnicodeEncodeError）。

    同時修掉一個更隱蔽的問題：main.py 以 encoding="utf-8" 讀子行程的 stdout，子行程
    卻用 cp950 寫，狀態列的中文訊息因此一直是亂碼。兩邊統一成 utf-8。
    calibration.py 與 test/ 早就用這個慣用法，這裡補齊。
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass                      # 舊版 Python 或已被重導向 → 沿用原設定


def main():
    _use_utf8_stdout()
    ap = argparse.ArgumentParser(description="GC-IMS 峰偵測（突出度，measure-only）")
    ap.add_argument("path", nargs="?", default=None,
                    help="輸入 .npz 或 .mea（省略則跳檔案總管）")
    ap.add_argument("--sigma", type=float, default=1.0, help="高斯平滑 σ (預設 1.0；0=不平滑)")
    ap.add_argument("--floor-pct", type=float, default=85.0,
                    help="海平面 floor = 第幾百分位強度 (預設 85)")
    ap.add_argument("--prom-frac", type=float, default=0.02,
                    help="突出度門檻 = 此比例 × 最大突出度 (預設 0.02)")
    ap.add_argument("--min-prominence", type=float, default=0.0,
                    help="突出度絕對下限 (預設 0)")
    ap.add_argument("--min-distance", type=int, default=3,
                    help="兩峰最小像素間距，去重用 (預設 3)")
    ap.add_argument("--top-n", type=int, default=0,
                    help="只保留突出度前 N 個峰 (預設 0=全部)")
    ap.add_argument("--baseline", action="store_true",
                    help="沿保留時間方向做 AsLS 基線扣除（預設關閉）。實測對最終峰集"
                         "影響很小，但會大幅降低候選池雜訊；峰體積積分需要它")
    ap.add_argument("--baseline-lam", type=float, default=None,
                    help="基線平滑度 λ（預設見 baseline.DEFAULT_LAM，由本批最寬的"
                         "峰決定；太小會把寬峰當基線吃掉）")
    ap.add_argument("--baseline-stride", type=int, default=8,
                    help="每 n 列取樣求基線再內插回去 (預設 8，全圖約 16s；1 則約 107s)")
    ap.add_argument("--bg-only", action="store_true",
                    help="只從 .npz 重畫互動畫布的背景圖 (<名>_bg.png + _bg.json)，"
                         "不做找峰。約 5 秒，用於 .npz 還在但圖檔被刪掉的情況")
    ap.add_argument("--rules-config", default=None,
                    help=f"rules_config.json 路徑 (預設 {RULES_CONFIG}；缺檔 → 內建預設)")
    ap.add_argument("--mark-n", type=int, default=0,
                    help="overlay 只標前 N 個峰 (預設 0=全部)")
    ap.add_argument("--cmap", default="viridis", help="overlay 配色 (預設 viridis)")
    ap.add_argument("--figsize", default="8x9", metavar="WxH",
                    help="overlay 圖/視窗大小（英吋），如 14x11 (預設 8x9)")
    ap.add_argument("--dpi", type=int, default=150, help="overlay 存檔 dpi (預設 150)")
    ap.add_argument("--show", action="store_true",
                    help="存檔後也開可縮放視窗顯示 overlay")
    ap.add_argument("--ri-series", default=None,
                    help="背景圖/疊圈圖 y 軸改標成 RI（第四階段），值為參照系列（如"
                         " ketone），從本檔資料夾的 STD 解析校準；省略維持保留時間軸。"
                         "x 軸 drift 正規化不受影響")
    ap.add_argument("--ri-start-carbon", type=int, default=None)
    args = ap.parse_args()
    if args.baseline_lam is None:
        # 預設值放在 baseline.py，這裡不複製一份數字——λ 是量測出來的，
        # 兩處各寫一個就會有一處先過時
        import baseline as _bl
        args.baseline_lam = _bl.DEFAULT_LAM

    try:
        fw, fh = (float(x) for x in args.figsize.lower().split("x"))
        figsize = (fw, fh)
    except ValueError:
        print(f"--figsize 格式應為 WxH（如 14x11），收到 {args.figsize!r}，改用 8x9")
        figsize = (8, 9)

    path = resolve_input_path(
        args.path, title="選擇 .npz 或 .mea",
        initialdir=RESULTS_DIR if os.path.isdir(RESULTS_DIR) else None,
        filetypes=[("矩陣/原始檔", "*.npz *.mea"), ("所有檔案", "*.*")])

    print(f"載入：{path}")
    intensity, drift_ms, retention_s, meta = load_surface(path)

    # 保留時間方向的基線扣除（選用）。**預設關閉**：實測對最終峰集影響很小
    # （47→46 顆、六個錨點全數保留、突出度門檻 98.5→98.1），因為突出度本來就是
    # 相對量，緩慢變化的基線會把峰頂與鞍點抬起相同的量而互相抵銷。開啟後真正改變
    # 的是候選池——floor 由 218.6 降到 64.2、原始局部極大由 27.7 萬降到 19.2 萬。
    # 對日後的峰體積積分（定量）則是必要前處理：不扣基線的積分會把基座一起算進去。
    baseline_info = None
    if getattr(args, "baseline", False):
        import baseline as _bl
        print(f"基線扣除（AsLS, λ={args.baseline_lam:g}, stride={args.baseline_stride}）…")
        intensity, baseline_info = _bl.correct_rt_baseline(
            intensity, lam=args.baseline_lam, row_stride=args.baseline_stride)
        print(f"  基線中位 {baseline_info['baseline_median']:.1f}，"
              f"最大 {baseline_info['baseline_max']:.0f}")
    print(f"強度面 shape={intensity.shape} dtype={intensity.dtype}")

    # 第一階段：RIP 正規化（先做，fail-fast；且必須逐檔案獨立算，
    # 見 GC-IMS_Identify_Workflow.md §第一階段 point 6）
    rip_index, rip_intensity = rip.find_rip(intensity)
    print(f"RIP：dt_index={rip_index}  drift={drift_ms[rip_index]:.4f} ms  I={rip_intensity}")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(RESULTS_DIR, base)

    # 第四階段：若要 RI y 軸，從原始 .mea 所在資料夾解析 STD 校準（STD 在 GAS/<batch>/）
    ri_calibration = None
    if args.ri_series:
        try:
            import calibration as _cal
            src = path if path.lower().endswith(".mea") else (meta or {}).get("source")
            folder = os.path.dirname(os.path.abspath(src)) if src else None
            if folder:
                ri_calibration, ri_mode, _ = _cal.resolve_ri_calibration(
                    folder, series_key=args.ri_series, start_carbon=args.ri_start_carbon)
                print(f"[RI] source={ri_mode}, series={args.ri_series}")
                if ri_calibration is None:
                    # 使用者明確要了 --ri-series 卻沒拿到，y 軸會默默退回保留時間，
                    # 產出的圖與 _bg.json 跟「有 RI」的那批不同座標系卻長得一樣。
                    print(f"[warn] 指定了 --ri-series {args.ri_series} 但在 {folder} "
                          f"找不到可用的 STD；y 軸退回保留時間（秒）", file=sys.stderr)
            else:
                print("[warn] 找不到原始 .mea 資料夾，RI 軸略過")
        except Exception as _e:
            print(f"[warn] RI 校準解析失敗（{_e}）；維持保留時間軸")

    if args.bg_only:
        write_bg(intensity, drift_ms, retention_s, out, rip_index=rip_index,
                 cmap=args.cmap, figsize=figsize, dpi=args.dpi,
                 ri_calibration=ri_calibration)
        print(f"完成：僅重畫背景圖，未執行找峰。")
        return

    # 第七階段：規則庫。R004（排除 RIP 帶）需在突出度門檻之前生效，故它的
    # half_width 先抽出來餵給 detect_peaks；其餘規則在偵測後才套用。
    rules_config = rules.load_config(args.rules_config or RULES_CONFIG)
    rip_half_width, exclude_before_rip, rip_boundary = pre_gate_params(rules_config)

    params = {"sigma": args.sigma, "floor_pct": args.floor_pct,
              "prom_frac": args.prom_frac, "min_prominence": args.min_prominence,
              "min_distance": args.min_distance, "top_n": args.top_n,
              "rules_config": rules_config}
    print("偵測中...")
    peaks, stats, maxima = detect_peaks(
        intensity, sigma=args.sigma, floor_pct=args.floor_pct,
        prom_frac=args.prom_frac, min_prominence=args.min_prominence,
        min_distance=args.min_distance, top_n=args.top_n,
        rip_index=rip_index, rip_half_width=rip_half_width,
        exclude_before_rip=exclude_before_rip, rip_boundary=rip_boundary,
        return_maxima=True)
    attach_coords(peaks, drift_ms, retention_s)
    rip.attach_drift_relative(peaks, rip_index)
    stats["rip_index"] = rip_index
    stats["rip_intensity"] = rip_intensity

    # 偵測後套用其餘規則。用 mark_rules 而非 apply_rules：被規則否決的峰仍要
    # 留在輸出裡（UI 以「未選取」樣態顯示，見 workflow §第八階段），否則重開
    # 程式後那些峰就不見了，使用者也無從得知規則做了什麼。
    # peak_id 不重編號：編號基準線是強制規則套用後的集合，可選規則只標記。
    rules_report = rules.mark_rules(
        peaks, rules_config, context={"floor": stats["floor"], "rip_index": rip_index})
    for p in peaks:
        p["active"] = p["rule_active"]   # 初始狀態；使用者的手動決定存在側車檔
    stats["n_before_rules"] = len(peaks)
    stats["n_after_rules"] = rules_report["n_out"]
    stats["rules_report"] = rules_report
    print(f"規則庫：{len(peaks)} 個峰中有 {rules_report['n_out']} 個通過"
          f"（啟用 {sum(1 for e in rules_config if e.get('enabled'))} 條規則）")
    if rules_report.get("rip_missing_warning"):
        print("  ⚠ R004 啟用但峰缺 drift_relative，該規則實際未生效")

    # _overlay.png 是給 VOCal 目視比對與報告用的靜態圖，只畫通過規則的峰；
    # 互動畫布會把未通過的也畫出來（半透明），那是 main.py 的事。
    active_peaks = [p for p in peaks if p["active"]]

    print("輸出：")
    write_csv(peaks, out + "_peaks.csv")
    write_maxima_npz(out + "_maxima.npz", maxima, intensity.shape, stats["floor"],
                     rip_index, drift_ms, retention_s)
    # 圈是 Canvas 原生物件（可個別 toggle），所以互動畫布要的是「沒有圈」的背景圖；
    # _overlay.png 維持有圈，供 CLI 目視比對與第十一階段報告使用。
    write_overlay(intensity, drift_ms, retention_s, active_peaks, out + "_overlay.png",
                  cmap=args.cmap, mark_n=args.mark_n,
                  figsize=figsize, dpi=args.dpi, show=args.show,
                  rip_index=rip_index, ri_calibration=ri_calibration)
    geom = write_bg(intensity, drift_ms, retention_s, out, rip_index=rip_index,
                    cmap=args.cmap, figsize=figsize, dpi=args.dpi,
                    ri_calibration=ri_calibration)
    # 有無扣基線的 _peaks.json 外觀完全一樣，強度與突出度卻是不同基準——不記下來
    # 就無從分辨，正是 rt_axis_version 要防的那種靜默混用。
    params = dict(params)
    params["baseline"] = baseline_info      # None 表示未扣
    write_json(peaks, out + "_peaks.json", params, stats, meta, intensity.shape,
               geom=geom)
    print(f"完成：基準 {len(peaks)} 個峰，其中 {len(active_peaks)} 個通過規則。"
          f"請開 {base}_overlay.png 與 VOCal 圖目視比對。")


if __name__ == "__main__":
    main()

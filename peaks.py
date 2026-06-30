"""
peaks.py  —  GC-IMS 峰偵測（第一層：偵測 / 測量；measure-only v1）
Version: ver.01 — by Albert Sheng

依工作流文件 §3：以 **持續同調 / 突出度 (persistent homology / prominence)** 為主幹，
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
  - <name>_peaks.json  ← 完整：上述 + 突出度/平坦度/邊界/飽和/索引 + 偵測參數與來源
  - <name>_overlay.png ← 熱圖疊上偵測到的峰（佐證圖）

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

import numpy as np

from gas_utils import PROJECT_DIR, resolve_input_path

RESULTS_DIR = os.path.join(PROJECT_DIR, "results")
SATURATION = 32767  # int16 滿格


# --------------------------------------------------------------------------- #
# 載入強度面
# --------------------------------------------------------------------------- #
def load_surface(path):
    """回傳 (intensity, drift_ms, retention_s, meta)。支援 .npz 與 .mea。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".npz":
        z = np.load(path)
        return z["intensity"], z["drift_ms"], z["retention_s"], {"source": path}
    elif ext == ".mea":
        import readGAS
        data, header, axes = readGAS.read_mea(path)
        return data, axes["drift_ms"], axes["retention_s"], {"source": path,
                                                             "machine": header.get("Machine type"),
                                                             "sample": header.get("Sample")}
    raise ValueError(f"不支援的副檔名：{ext}（請給 .npz 或 .mea）")


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
def detect_peaks(intensity, sigma=1.0, floor_pct=85.0, prom_frac=0.02,
                 min_prominence=0.0, min_distance=3, top_n=0, flat_win=200):
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
        return [], {"floor": floor, "n_raw_maxima": 0}

    max_prom = max(p[2] for p in raw)
    thresh = max(min_prominence, prom_frac * max_prom)
    kept = [m for m in raw if m[2] >= thresh]
    kept.sort(key=lambda m: m[2], reverse=True)     # 依突出度排序
    print(f"  突出度門檻 {thresh:.1f} (=max({min_prominence}, {prom_frac}×{max_prom:.0f})) "
          f"→ 保留 {len(kept)}")

    # 近距離去重（平滑後仍可能有貼很近的雙極大）：保留突出度高者
    selected = []
    if min_distance and min_distance > 0:
        occupied = np.zeros((H, W), dtype=bool)
        md = int(min_distance)
        for pix, val, pr in kept:
            r, c = divmod(pix, W)
            r0, r1 = max(0, r - md), min(H, r + md + 1)
            c0, c1 = max(0, c - md), min(W, c + md + 1)
            if occupied[r0:r1, c0:c1].any():
                continue
            occupied[r, c] = True
            selected.append((pix, val, pr))
    else:
        selected = kept
    print(f"  min_distance={min_distance} 去重 → {len(selected)} 個峰")

    if top_n and top_n > 0:
        selected = selected[:top_n]

    # 量測每個峰的平坦度 / 邊界 / 飽和，組成紀錄
    peaks = []
    for i, (pix, val, pr) in enumerate(selected, start=1):
        r, c = divmod(pix, W)
        flat, trunc = flatness_score(work, r, c, val, floor, win=flat_win)
        edge = int(min(r, H - 1 - r, c, W - 1 - c))
        sat = bool(abs(int(intensity[r, c])) >= SATURATION)
        peaks.append({
            "peak_id": i,
            "rt_index": int(r),
            "dt_index": int(c),
            "intensity": int(intensity[r, c]),   # 回報原始（未平滑）強度
            "prominence": round(pr, 2),
            "flatness": round(flat, 4),
            "flatness_truncated": trunc,
            "edge_dist": edge,
            "saturated": sat,
            "rank": i,
        })
    stats = {"floor": floor, "n_raw_maxima": len(raw),
             "n_after_prom": len(kept), "n_final": len(peaks),
             "max_prominence": round(max_prom, 2), "prom_threshold": round(thresh, 2)}
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


def write_json(peaks, path, params, stats, meta, shape):
    doc = {
        "source": meta.get("source"),
        "machine": meta.get("machine"),
        "sample": meta.get("sample"),
        "detected_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "matrix_shape": [int(shape[0]), int(shape[1])],
        "detection_params": params,
        "stats": stats,
        "n_peaks": len(peaks),
        "peaks": peaks,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"  JSON → {path}")


def write_overlay(intensity, drift_ms, retention_s, peaks, path,
                  cmap="viridis", mark_n=0, figsize=(8, 9), dpi=150, show=False):
    import matplotlib
    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sub = intensity[::max(1, intensity.shape[0] // 1000),
                    ::max(1, intensity.shape[1] // 1000)]
    vmin, vmax = np.percentile(sub, [1.0, 99.5])

    # constrained_layout：視窗縮放時自動重排
    fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)
    ax.imshow(intensity, aspect="auto", origin="lower",
              extent=[drift_ms[0], drift_ms[-1], retention_s[0], retention_s[-1]],
              cmap=cmap, vmin=vmin, vmax=vmax, interpolation="nearest")
    shown = peaks[:mark_n] if mark_n and mark_n > 0 else peaks
    xs = [p["drift_ms"] for p in shown]
    ys = [p["retention_s"] for p in shown]
    ax.scatter(xs, ys, s=28, facecolors="none", edgecolors="red", linewidths=0.8)
    ax.set_xlabel("Drift time [ms]")
    ax.set_ylabel("Retention time [s]")
    ax.set_title(f"Detected {len(shown)} peaks (red = detected)")
    fig.savefig(path, dpi=dpi)
    print(f"  PNG  -> {path}  (marked {len(shown)} peaks)")
    if show:
        plt.show()
    else:
        plt.close(fig)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
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
    ap.add_argument("--mark-n", type=int, default=0,
                    help="overlay 只標前 N 個峰 (預設 0=全部)")
    ap.add_argument("--cmap", default="viridis", help="overlay 配色 (預設 viridis)")
    ap.add_argument("--figsize", default="8x9", metavar="WxH",
                    help="overlay 圖/視窗大小（英吋），如 14x11 (預設 8x9)")
    ap.add_argument("--dpi", type=int, default=150, help="overlay 存檔 dpi (預設 150)")
    ap.add_argument("--show", action="store_true",
                    help="存檔後也開可縮放視窗顯示 overlay")
    args = ap.parse_args()

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
    print(f"強度面 shape={intensity.shape} dtype={intensity.dtype}")

    params = {"sigma": args.sigma, "floor_pct": args.floor_pct,
              "prom_frac": args.prom_frac, "min_prominence": args.min_prominence,
              "min_distance": args.min_distance, "top_n": args.top_n}
    print("偵測中...")
    peaks, stats = detect_peaks(
        intensity, sigma=args.sigma, floor_pct=args.floor_pct,
        prom_frac=args.prom_frac, min_prominence=args.min_prominence,
        min_distance=args.min_distance, top_n=args.top_n)
    attach_coords(peaks, drift_ms, retention_s)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(RESULTS_DIR, base)
    print("輸出：")
    write_csv(peaks, out + "_peaks.csv")
    write_json(peaks, out + "_peaks.json", params, stats, meta, intensity.shape)
    write_overlay(intensity, drift_ms, retention_s, peaks, out + "_overlay.png",
                  cmap=args.cmap, mark_n=args.mark_n,
                  figsize=figsize, dpi=args.dpi, show=args.show)
    print(f"完成：共偵測 {len(peaks)} 個峰。請開 {base}_overlay.png 與 VOCal 圖目視比對。")


if __name__ == "__main__":
    main()

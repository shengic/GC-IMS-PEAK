"""
readGAS.py  —  G.A.S. FlavourSpec® .mea 解析 + 繪製 2D 熱圖
Version: ver.01 — by Albert Sheng

依據 test_readGAS.py 對實檔 (260210_103116_FM_1.mea) 的探測結果寫成。
**不需 gc-ims-tools / h5py**：檔案已確認是「ASCII 表頭 + 原始 int16-LE 資料區」（未壓縮），
維度與軸校正全部從表頭推得，自己解析即可，依賴最小（numpy + matplotlib）。

格式（探測確認）
----------------
  - 檔案 = ASCII key=value 表頭 (~5.5 KB) + 檔尾 (n_rt × n_dt × 2) bytes 的 int16-LE 資料。
  - Chunks count        → n_rt：保留時間(RT)譜數（每個 chunk = 某 RT 的整條漂移譜）
  - Chunk sample count  → n_dt：每譜的漂移時間(DT)取樣點數
  - 資料 row-major → reshape(n_rt, n_dt)：row = RT、col = DT。

軸校正（皆由表頭推得）
----------------------
  - 漂移 DT：Chunk sample rate=150kHz → 每點 1/150000 s；4500 點 = 30 ms (=trigger repetition)。
  - 保留 RT：每 chunk 間隔 = Chunk averages × Chunk trigger repetition (= 6 × 30ms = 180ms)。

顯示慣例（與專案 workflow 文件一致）：X 軸 = 漂移時間(DT)、Y 軸 = 保留時間(RT)。
本版畫「原始漂移時間 ms」，尚未做 RIP 相對正規化（屬後續 I3/I4 校正步驟）。

選檔：不給路徑就跳出檔案總管（預設開在 GAS/），與 test_readGAS.py 共用 gas_utils。

用法
----
    python readGAS.py                         # 跳檔案總管選 .mea
    python readGAS.py "檔案.mea"
    python readGAS.py "檔案.mea" --cmap jet    # 仿 VOCal 配色 (預設 viridis)
    python readGAS.py "檔案.mea" --save out.png --no-show   # 批次存圖不開視窗
    python readGAS.py "檔案.mea" --rt-unit min --log         # RT 用分鐘、強度取 log
"""

import argparse
import os
import re
import time

import numpy as np

from gas_utils import PROJECT_DIR, resolve_input_path

RESULTS_DIR = os.path.join(PROJECT_DIR, "results")


def log(msg):
    """帶時間戳的即時訊息（立即 flush，避免被緩衝住看起來像卡住）。"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _ascii(s):
    """移除非 ASCII 字元，避免 matplotlib 預設字型無法顯示而出現方框。"""
    return s.encode("ascii", "ignore").decode("ascii").strip()


def progress(done, total, t0, label="進度"):
    """單行動態刷新的進度列（百分比 + 已用時 + 預估剩餘）。"""
    frac = done / total if total else 1.0
    elapsed = time.time() - t0
    eta = (elapsed / frac - elapsed) if frac > 0 else 0.0
    bar_n = int(frac * 30)
    bar = "█" * bar_n + "·" * (30 - bar_n)
    print(f"\r  {label} |{bar}| {done:,}/{total:,} ({frac*100:5.1f}%) "
          f"已用 {elapsed:4.0f}s 剩約 {eta:4.0f}s", end="", flush=True)
    if done >= total:
        print()  # 收尾換行


def default_csv_path(mea_path):
    """由輸入 .mea 檔名推得輸出路徑：results/<同檔名>.csv。"""
    base = os.path.splitext(os.path.basename(mea_path))[0]
    return os.path.join(RESULTS_DIR, base + ".csv")


# --------------------------------------------------------------------------- #
# 表頭解析
# --------------------------------------------------------------------------- #
def parse_header(raw, max_scan=32768):
    """從檔頭擷取 ASCII key=value 表頭（遇到二進位資料即停）。回傳 dict。"""
    text = raw[:max_scan].decode("latin-1", errors="replace")
    header = {}
    for line in text.split("\n"):
        # 出現非表頭的控制字元 → 表頭結束、進入二進位資料區
        if any(ord(c) < 32 and c not in "\t\r" for c in line):
            break
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip()
            if k:
                header[k] = v
    return header


def hnum(header, key, default=None):
    """從表頭值取出第一個數字（自動忽略單位，如 '150 [kHz]' → 150.0）。"""
    if key not in header:
        return default
    m = re.search(r"-?\d+\.?\d*", header[key])
    return float(m.group()) if m else default


# --------------------------------------------------------------------------- #
# .mea 讀取
# --------------------------------------------------------------------------- #
def read_mea(path):
    """讀取 G.A.S. .mea，回傳 (data, header, axes)。

    data : np.ndarray int16, shape (n_rt, n_dt)，row=保留時間、col=漂移時間。
    axes : dict，含 drift_ms / retention_s 兩條物理軸與相關幾何資訊。
    """
    size = os.path.getsize(path)
    log(f"讀取檔案中（{size/1024/1024:.1f} MB）...")
    t0 = time.time()
    with open(path, "rb") as f:
        raw = f.read()
    log(f"讀檔完成（{time.time()-t0:.1f}s），解析表頭...")

    header = parse_header(raw)

    n_rt = hnum(header, "Chunks count")
    n_dt = hnum(header, "Chunk sample count")
    if n_rt is None or n_dt is None:
        raise ValueError("表頭缺少 'Chunks count' 或 'Chunk sample count'，無法決定維度。")
    n_rt, n_dt = int(n_rt), int(n_dt)

    itemsize = 2  # int16
    expected = n_rt * n_dt * itemsize
    header_size = size - expected
    if header_size < 0:
        raise ValueError(
            f"尺寸不符：檔案 {size:,}B < 預期資料 {expected:,}B "
            f"({n_rt}×{n_dt}×{itemsize})。dtype 或維度判斷有誤。"
        )

    log(f"解析資料矩陣 reshape({n_rt}, {n_dt})（表頭 {header_size:,}B）...")
    data = np.frombuffer(raw, dtype="<i2", count=n_rt * n_dt, offset=header_size)
    data = data.reshape(n_rt, n_dt)
    log("矩陣就緒。")

    # ---- 軸校正（全部由表頭推得） ----
    sample_rate_khz = hnum(header, "Chunk sample rate", 150.0)      # kHz
    trig_rep_ms = hnum(header, "Chunk trigger repetition", 30.0)    # ms
    averages = hnum(header, "Chunk averages", 1.0)                  # 次

    dt_step_ms = 1.0 / sample_rate_khz                 # 每漂移取樣點時間 (ms)
    rt_step_ms = averages * trig_rep_ms                # 每個 chunk 的保留時間間隔 (ms)

    axes = {
        "drift_ms": np.arange(n_dt) * dt_step_ms,              # 0..~30 ms
        "retention_s": np.arange(n_rt) * rt_step_ms / 1000.0,  # 秒
        "n_rt": n_rt,
        "n_dt": n_dt,
        "header_size": header_size,
        "dt_step_ms": dt_step_ms,
        "rt_step_ms": rt_step_ms,
    }
    return data, header, axes


# --------------------------------------------------------------------------- #
# 摘要列印
# --------------------------------------------------------------------------- #
def print_summary(path, data, header, axes):
    print("=" * 68)
    print(f"檔案：{path}")
    print(f"機型：{header.get('Machine type', '?')}   "
          f"樣品：{header.get('Sample', '?')}   "
          f"程式：{header.get('Class', '?')}")
    print(f"表頭大小：{axes['header_size']:,} bytes")
    print(f"資料矩陣：shape=({axes['n_rt']}, {axes['n_dt']})  "
          f"(保留時間 × 漂移時間)  dtype=int16")
    print(f"漂移時間軸：0 ~ {axes['drift_ms'][-1]:.3f} ms   "
          f"(每點 {axes['dt_step_ms']*1000:.3f} µs，共 {axes['n_dt']} 點)")
    print(f"保留時間軸：0 ~ {axes['retention_s'][-1]:.1f} s "
          f"(~{axes['retention_s'][-1]/60:.1f} min)   "
          f"(每譜 {axes['rt_step_ms']:.0f} ms，共 {axes['n_rt']} 譜)")
    print(f"強度統計：min={data.min()}  max={data.max()}  "
          f"mean={data.mean():.1f}  std={data.std():.1f}")
    print("=" * 68)


# --------------------------------------------------------------------------- #
# CSV 匯出
# --------------------------------------------------------------------------- #
def export_csv(data, axes, path, fmt="long", downsample=1, rt_unit="s"):
    """匯出可繪圖的 CSV。

    fmt="long"：三欄 tidy 格式 drift,retention,intensity（每個格點一列，最通用）。
                注意：完整 8571×4500 ≈ 3850 萬列、約 0.7–1 GB；建議搭配 --csv-downsample。
    fmt="wide"：矩陣格式，第一列為各漂移時間、第一欄為各保留時間，交點為強度（檔案小很多）。
    downsample：兩軸各取每第 N 點，N=10 → 資料量縮為 1/100。
    """
    ds = max(1, int(downsample))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    drift = axes["drift_ms"][::ds]
    if rt_unit == "min":
        rt = axes["retention_s"][::ds] / 60.0
        rt_name = "retention_min"
    else:
        rt = axes["retention_s"][::ds]
        rt_name = "retention_s"
    sub = data[::ds, ::ds]
    n_rt, n_dt = sub.shape
    log(f"匯出 CSV（{fmt}，downsample={ds}）：{n_rt}×{n_dt} = {n_rt*n_dt:,} 格點 → {path}")
    t0 = time.time()
    step = max(1, n_rt // 200)   # 約每 0.5% 更新一次進度列

    if fmt == "wide":
        # 第一列：左上角留空 + 漂移時間；之後每列：保留時間 + 該列強度
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(f"{rt_name}\\drift_ms," + ",".join(f"{d:.5f}" for d in drift) + "\n")
            for i in range(n_rt):
                row = ",".join(str(v) for v in sub[i])
                f.write(f"{rt[i]:.4f},{row}\n")
                if i % step == 0 or i == n_rt - 1:
                    progress(i + 1, n_rt, t0, label="寫入列")
    else:  # long / tidy
        # 逐保留時間列分塊寫出，記憶體不會因 3850 萬列而爆掉
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(f"drift_ms,{rt_name},intensity\n")
            for i in range(n_rt):
                block = np.column_stack((
                    drift,
                    np.full(n_dt, rt[i], dtype=np.float64),
                    sub[i].astype(np.int64),
                ))
                np.savetxt(f, block, fmt=("%.5f", "%.4f", "%d"), delimiter=",")
                if i % step == 0 or i == n_rt - 1:
                    progress(i + 1, n_rt, t0, label="寫入列")
    log(f"CSV 完成（{time.time()-t0:.1f}s）：{path}")


def export_npz(data, axes, path):
    """匯出無損、緊湊的 .npz（強度矩陣 + 兩軸），供峰偵測管線快速載入。

    重新載入：
        z = np.load(path)
        intensity = z["intensity"]      # shape (n_rt, n_dt), int16
        drift_ms = z["drift_ms"]; retention_s = z["retention_s"]
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    log(f"壓縮存檔 .npz 中（{data.shape}）...")
    t0 = time.time()
    np.savez_compressed(
        path,
        intensity=data,
        drift_ms=axes["drift_ms"],
        retention_s=axes["retention_s"],
    )
    log(f"已存無損矩陣（{time.time()-t0:.1f}s）：{path}  dtype={data.dtype}")


# --------------------------------------------------------------------------- #
# 繪圖
# --------------------------------------------------------------------------- #
def plot_heatmap(data, axes, header, cmap="viridis", clip=(1.0, 99.5),
                 rt_unit="s", log_scale=False, save=None, show=True,
                 figsize=(8, 9), dpi=150):
    try:
        import matplotlib
    except ImportError:
        log("略過熱圖：未安裝 matplotlib（CSV/npz 已完成）。"
            "如需熱圖請 `pip install matplotlib`。")
        return
    log("渲染熱圖中（資料量大，請稍候）...")
    t0 = time.time()
    if not show:
        matplotlib.use("Agg")  # 無視窗環境也能存檔
    import matplotlib.pyplot as plt

    drift = axes["drift_ms"]
    if rt_unit == "min":
        rt = axes["retention_s"] / 60.0
        rt_label = "Retention time [min]"
    else:
        rt = axes["retention_s"]
        rt_label = "Retention time [s]"

    img = data.astype(np.float32)
    intensity_label = "Intensity (int16, raw)"
    if log_scale:
        # 取 log 壓抑 RIP 等極端值；先平移到正值域
        img = np.log1p(img - img.min())
        intensity_label = "log1p(Intensity - min)"

    # 對比度：百分位裁切（在子取樣上算以加速），避免飽和點把色階拉爆
    sub = img[::max(1, img.shape[0] // 1000), ::max(1, img.shape[1] // 1000)]
    vmin, vmax = np.percentile(sub, clip)

    # constrained_layout 會在視窗縮放時自動重排，避免固定版面卡死
    fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)
    im = ax.imshow(
        img,
        aspect="auto",      # 隨視窗大小自由縮放（非鎖死長寬比）
        origin="lower",
        extent=[drift[0], drift[-1], rt[0], rt[-1]],
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        interpolation="nearest",
    )
    ax.set_xlabel("Drift time [ms]")
    ax.set_ylabel(rt_label)
    sample = _ascii(header.get("Sample", ""))
    machine = _ascii(header.get("Machine type", ""))
    ax.set_title(f"GC-IMS  {sample}  ({machine})")
    fig.colorbar(im, ax=ax, label=intensity_label)

    if save:
        fig.savefig(save, dpi=dpi)
        log(f"已存圖：{save}  (色階 {clip[0]}–{clip[1]} pct = {vmin:.4g} ~ {vmax:.4g})")
    log(f"渲染完成（{time.time()-t0:.1f}s）" + ("，開啟視窗中..." if show else "。"))
    if show:
        plt.show()
    else:
        plt.close(fig)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="G.A.S. .mea 解析 + 2D 熱圖（無需 gc-ims-tools）")
    ap.add_argument("path", nargs="?", default=None,
                    help=".mea 檔路徑（省略則跳出檔案總管選檔）")
    ap.add_argument("--cmap", default="viridis",
                    help="matplotlib colormap，仿 VOCal 可用 jet/turbo (預設 viridis)")
    ap.add_argument("--rt-unit", choices=("s", "min"), default="s",
                    help="保留時間軸單位 (預設 s)")
    ap.add_argument("--clip", nargs=2, type=float, default=(1.0, 99.5),
                    metavar=("LO", "HI"), help="色階百分位裁切 (預設 1 99.5)")
    ap.add_argument("--log", action="store_true",
                    help="強度取 log1p（RIP 太強蓋住小峰時用）")
    ap.add_argument("--save", default=None,
                    help="熱圖存檔路徑 (png)（預設 results/<名>_heatmap.png）")
    ap.add_argument("--no-show", action="store_true", help="不開視窗顯示")
    ap.add_argument("--figsize", default="8x9", metavar="WxH",
                    help="圖/視窗起始大小（英吋），如 12x10 (預設 8x9)")
    ap.add_argument("--dpi", type=int, default=150, help="存檔解析度 dpi (預設 150)")
    ap.add_argument("--csv", default=None,
                    help="CSV 輸出路徑（預設 results/<mea檔名>.csv）")
    ap.add_argument("--csv-format", choices=("long", "wide"), default="long",
                    help="long=三欄 drift,retention,intensity；wide=矩陣格式 (預設 long)")
    ap.add_argument("--csv-downsample", type=int, default=1, metavar="N",
                    help="CSV 兩軸各取每第 N 點以縮小檔案 (預設 1=完整全解析度)")
    ap.add_argument("--no-npz", dest="npz", action="store_false",
                    help="不要另存 .npz（預設會存無損矩陣 results/<名>.npz，峰偵測用）")
    ap.set_defaults(npz=True)
    args = ap.parse_args()

    try:
        fw, fh = (float(x) for x in args.figsize.lower().split("x"))
        figsize = (fw, fh)
    except ValueError:
        log(f"--figsize 格式應為 WxH（如 12x10），收到 {args.figsize!r}，改用預設 8x9")
        figsize = (8, 9)

    path = resolve_input_path(args.path, title="選擇要解析的 .mea 檔")

    data, header, axes = read_mea(path)
    print_summary(path, data, header, axes)

    csv_path = args.csv or default_csv_path(path)
    export_csv(data, axes, csv_path, fmt=args.csv_format,
               downsample=args.csv_downsample, rt_unit=args.rt_unit)

    if args.npz:
        export_npz(data, axes, os.path.splitext(csv_path)[0] + ".npz")

    # 預設把熱圖存到 results/<名>_heatmap.png（--save 可改路徑）
    heatmap_path = args.save or (os.path.splitext(csv_path)[0] + "_heatmap.png")
    plot_heatmap(data, axes, header,
                 cmap=args.cmap, clip=tuple(args.clip), rt_unit=args.rt_unit,
                 log_scale=args.log, save=heatmap_path, show=not args.no_show,
                 figsize=figsize, dpi=args.dpi)


if __name__ == "__main__":
    main()

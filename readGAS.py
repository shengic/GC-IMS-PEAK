"""
readGAS.py  —  G.A.S. FlavourSpec® .mea 解析 + 繪製 2D 熱圖
Version: 3.1 — by Albert Sheng

變更記錄：
  3.1  — axes 新增 source（原始 .mea 絕對路徑），export_npz() 存成 mea_source，讓
         下游只拿到 .npz 時仍能回頭找同資料夾的 STD 做 RI 校正；移除熱圖
         右上角的軸標註。
  2.1  — 全解析度 CSV 改為可選（--write-csv）。真實尺寸每檔 0.8–1.5 GB，而管線
         裡沒有任何程式讀它——同樣的資料在 .npz 裡無損且只有約 30 MB。
  ver.02 — 修復 plot_heatmap() 在真實尺寸矩陣（如 20413x3150）上 fig.savefig()
           時 OOM 崩潰的問題：新增 _downsample_for_display()，畫圖前先降到接近
           輸出解析度再交給 imshow()。已用真實資料驗證（見 workflow 文件 draft.11）。
  ver.01 — 初版。

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
import sys
import re
import time

import numpy as np

from gas_utils import PROJECT_DIR, resolve_input_path

RESULTS_DIR = os.path.join(PROJECT_DIR, "results")

# --------------------------------------------------------------------------- #
# 保留時間軸公式的版本號。**只有在 retention_s 的物理意義改變時才 +1**，用途是讓
# 舊版寫出的產物能被偵測到，而不是跟新版靜默混用——RT 差 16.7% 卻沒有任何錯誤訊息，
# 是最難察覺的一種資料污染。
#
#   1 : rt_step_ms = averages × trigger_repetition        （**錯誤**，2026-08-12 之前）
#   2 : rt_step_ms = (averages + 1) × trigger_repetition  （現行）
#
# 為什麼是 +1（三條獨立證據，2026-08-12）：
#   (a) 方法總長：STD（avg=6, trig=21ms, 20413 chunks）舊式算出 42.87 min，新式
#       50.01 min；參考檔 FM_1（avg=6, trig=30ms, 8571 chunks）舊式 25.70 min，
#       新式 1799.91 s = 29.9985 min。兩個不同方法，新式都落在整數分鐘上。
#   (b) 經理提供的標準品對照表 kintonemixed-C4-C9.xlsx 的 Rt[sec]：舊式與其系統性
#       差 16.7%，新式殘差降到 −0.48%~+0.97%（峰頂判定差異的量級）。
#   (c) 獨立實作 gc-ims-tools 0.1.10（Food Chemistry 2022）的 Spectrum.read_mea()
#       用的是 (chunk_averages + 1)。同一支 .mea 實測交叉比對：強度矩陣
#       20413×3150 逐格完全相同、漂移軸一致，**只有保留時間軸差 7/6**。
#
# 推測的物理意義：`Chunk averages` 記的是「首次之外額外平均幾次」，故每個 chunk
# 實際佔 (N+1) 個 trigger 週期。**此為推測**——只有 2 個檔案佐證，不排除某些韌體
# 版本語意不同；若日後發現反例，改的是這裡並把版本號推到 3。
#
# 不受影響：強度矩陣、漂移軸、找峰、以及 **RI**（RI 在 log10(RT) 空間內插，錨點與
# 查詢值同時平移 log10(7/6)，分段線性內插對 x 平移不變——實測差 ~1e-13）。
# --------------------------------------------------------------------------- #
RT_AXIS_VERSION = 2


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
    # (averages + 1)：見檔頭 RT_AXIS_VERSION 的三條證據。舊版少了 +1，使整條
    # 保留時間軸被壓縮成實際的 6/7（差 16.7%）。
    rt_step_ms = (averages + 1.0) * trig_rep_ms         # 每個 chunk 的保留時間間隔 (ms)

    axes = {
        "drift_ms": np.arange(n_dt) * dt_step_ms,              # 0..~30 ms
        "retention_s": np.arange(n_rt) * rt_step_ms / 1000.0,  # 秒
        "n_rt": n_rt,
        "n_dt": n_dt,
        "header_size": header_size,
        "dt_step_ms": dt_step_ms,
        "rt_step_ms": rt_step_ms,
        # 供下游把「這份資料用哪一版 RT 軸算的」一路帶進 .npz / _peaks.json
        "rt_axis_version": RT_AXIS_VERSION,
        "sample_rate_khz": sample_rate_khz,
        "trig_rep_ms": trig_rep_ms,
        "averages": averages,
        # 原始 .mea 的絕對路徑。第四階段的 RI 校正是靠「這個檔所在資料夾裡的 STD」
        # 解析的，所以下游只拿到 .npz 時仍必須知道它是從哪個 .mea 來的，否則資料夾
        # 會被解析成 results/（那裡沒有 STD），RI 校正就靜默失效。
        "source": os.path.abspath(path),
    }
    return data, header, axes


# --------------------------------------------------------------------------- #
# 摘要列印
# --------------------------------------------------------------------------- #
def print_summary(path, data, header, axes):
    print("=" * 68)
    print(f"檔案：{path}")
    print(f"機型：{_ascii(header.get('Machine type', '?'))}   "
          f"樣品：{_ascii(header.get('Sample', '?'))}   "
          f"程式：{_ascii(header.get('Class', '?'))}")
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
        # 沒有這個欄位的 .npz 一律視為版本 1（舊的、錯的 RT 軸）——見 RT_AXIS_VERSION
        rt_axis_version=np.int32(axes.get("rt_axis_version", RT_AXIS_VERSION)),
        # 原始 .mea 路徑，供下游回頭找同資料夾的 STD 做 RI 校正（見 read_mea 的註解）
        mea_source=np.array(str(axes.get("source", ""))),
    )
    log(f"已存無損矩陣（{time.time()-t0:.1f}s）：{path}  dtype={data.dtype}")


# --------------------------------------------------------------------------- #
# 繪圖
# --------------------------------------------------------------------------- #
def _downsample_for_display(img, figsize, dpi, margin=1.5):
    """把矩陣降到接近實際輸出像素數（留 margin 倍餘裕避免鋸齒）再拿去畫圖。

    臭蟲背景：ax.imshow() 呼叫本身不會立即耗記憶體，但 fig.savefig() 光柵化時，
    Agg 後端會處理『整個』傳入陣列，不會自動略過超出輸出解析度的多餘資料點。
    對真實尺寸的 GC-IMS 矩陣（例如 20413×3150 ≈ 6400 萬點，遠超一張 8x9 吋
    150dpi 圖片實際的 ~1200×1350 像素），savefig() 會在光柵化那一刻記憶體暴增
    到系統上限被 OOM killer 砍掉（已用真實資料重現、定位到 savefig() 這一步，
    不是臆測）。修法：畫圖前先降到略高於輸出解析度即可，不影響肉眼可辨的圖片品質。
    座標軸範圍（extent）用原始陣列的頭尾值即可，降採樣不影響物理範圍，
    只影響中間取樣密度，所以這裡只需要回傳降採樣後的矩陣本身。
    """
    target_h = max(1, int(figsize[1] * dpi * margin))
    target_w = max(1, int(figsize[0] * dpi * margin))
    step_r = max(1, img.shape[0] // target_h)
    step_c = max(1, img.shape[1] // target_w)
    if step_r == 1 and step_c == 1:
        return img   # 資料量本來就不大，不需要降採樣
    return img[::step_r, ::step_c]


def plot_heatmap(data, axes, header, cmap="viridis", clip=(1.0, 99.5),
                 rt_unit="s", log_scale=False, save=None, show=True,
                 figsize=(8, 9), dpi=150, ri_calibration=None):
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

    # RIP 正規化橫軸（本次使用者決策：熱圖橫軸統一顯示 drift_relative 而非
    # 原始 drift_ms）。以 RT=0 這列的 RIP 位置作為 x=1 的基準。此為顯示層
    # 決策，不改資料本身；drift_ms 仍存進 .npz 供下游模組使用。
    import rip as _rip
    rip_index = None
    try:
        rip_index, _ = _rip.find_rip(data)
        drift_norm = drift / drift[rip_index]
        drift_label = "Drift relative to RIP (RIP at 1.0)"
    except Exception as _e:
        log(f"[warn] RIP 定位失敗（{_e}），退回原始 drift_ms 座標")
        drift_norm = drift
        drift_label = "Drift time [ms]"

    img = data.astype(np.float32)
    intensity_label = "Intensity (int16, raw)"
    if log_scale:
        # 取 log 壓抑 RIP 等極端值；先平移到正值域
        img = np.log1p(img - img.min())
        intensity_label = "log1p(Intensity - min)"

    # 對比度：百分位裁切（在子取樣上算以加速），避免飽和點把色階拉爆
    sub = img[::max(1, img.shape[0] // 1000), ::max(1, img.shape[1] // 1000)]
    vmin, vmax = np.percentile(sub, clip)

    # 畫圖前降到接近輸出解析度，避免 savefig() 光柵化整個原始矩陣爆記憶體
    img_ds = _downsample_for_display(img, figsize, dpi)
    if img_ds.shape != img.shape:
        log(f"顯示前降採樣：{img.shape} -> {img_ds.shape}（避免 savefig 爆記憶體）")

    # 第四階段：校準就緒 → 沿 y 把顯示影像重採樣成「均勻於 RI」，讓 RI 軸**線性
    # （非 log 刻度）**。x 軸 drift 正規化不動；RI 模式下 rt_unit(min) 不再適用。
    y_extent = (rt[0], rt[-1])
    y_label = rt_label
    if ri_calibration is not None:
        try:
            import calibration as _cal
            rt_s = axes["retention_s"]
            row_rts = np.linspace(float(rt_s[0]), float(rt_s[-1]), img_ds.shape[0])
            warped, ri_lo, ri_hi = _cal.warp_rows_to_ri(img_ds, row_rts, ri_calibration)
            if ri_lo is not None:
                img_ds = warped
                y_extent = (ri_lo, ri_hi)
                y_label = "Retention Index (RI)"
            else:
                log("[info] RI 校正非絕對模式（無 series），y 軸維持保留時間")
        except Exception as _e:
            log(f"[warn] RI warp 略過（{_e}）；維持保留時間軸")

    # constrained_layout 會在視窗縮放時自動重排，避免固定版面卡死
    fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)
    im = ax.imshow(
        img_ds,
        aspect="auto",      # 隨視窗大小自由縮放（非鎖死長寬比）
        origin="lower",
        extent=[drift_norm[0], drift_norm[-1], y_extent[0], y_extent[1]],
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        interpolation="nearest",
    )
    ax.set_xlabel(drift_label)
    ax.set_ylabel(y_label)

    # 每 0.5 一格 major tick，避免自動選擇只在整數上打刻度（範圍多為 0-3）
    from matplotlib.ticker import MultipleLocator, FormatStrFormatter
    ax.xaxis.set_major_locator(MultipleLocator(0.5))
    ax.xaxis.set_minor_locator(MultipleLocator(0.1))
    ax.xaxis.set_major_formatter(FormatStrFormatter("%.1f"))

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
                    help="CSV 輸出路徑（給定即代表要寫；預設不寫）")
    ap.add_argument("--write-csv", action="store_true",
                    help="另存全解析度長表 CSV 到 results/<名>.csv。"
                         "預設不寫：真實尺寸資料約 0.8–1.5 GB／檔，"
                         "而管線本身只讀 .npz（同資料無損、約 30 MB），沒有東西讀這份 CSV")
    ap.add_argument("--csv-format", choices=("long", "wide"), default="long",
                    help="long=三欄 drift,retention,intensity；wide=矩陣格式 (預設 long)")
    ap.add_argument("--csv-downsample", type=int, default=1, metavar="N",
                    help="CSV 兩軸各取每第 N 點以縮小檔案 (預設 1=完整全解析度)")
    ap.add_argument("--no-npz", dest="npz", action="store_false",
                    help="不要另存 .npz（預設會存無損矩陣 results/<名>.npz，峰偵測用）")
    ap.set_defaults(npz=True)
    ap.add_argument("--ri-series", default=None,
                    help="把熱圖 y 軸從保留時間改標成保留指數 RI（第四階段）。值為參照"
                         "系列（如 n_alkane/custom），會從本檔資料夾的 STD 解析校正；"
                         "省略則維持保留時間軸。x 軸 drift 正規化不受影響")
    ap.add_argument("--ri-start-carbon", type=int, default=None,
                    help="--ri-series n_alkane 的起始碳數（覆寫暫定 C6）")
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

    # 輸出檔名共同的字首。以前是從 csv_path 反推的，導致「不寫 CSV」也得先算出
    # 一個 CSV 路徑；改成獨立變數，CSV 才能真正變成可選。
    base_out = os.path.splitext(args.csv or default_csv_path(path))[0]

    if args.csv or args.write_csv:
        export_csv(data, axes, base_out + ".csv", fmt=args.csv_format,
                   downsample=args.csv_downsample, rt_unit=args.rt_unit)
    else:
        log("略過全解析度 CSV（--write-csv 可開啟）——.npz 已含相同資料且無損")

    if args.npz:
        export_npz(data, axes, base_out + ".npz")

    # 第四階段：若要 RI 軸，從本檔資料夾解析 STD 校正（顯示層用）
    ri_calibration = None
    if args.ri_series:
        try:
            import calibration as _cal
            folder = os.path.dirname(os.path.abspath(path))
            dims = _cal.extract_registry_dims(header)
            ri_calibration, ri_mode, _ = _cal.resolve_ri_calibration(
                folder, dims=dims, series_key=args.ri_series,
                start_carbon=args.ri_start_carbon)
            log(f"[RI] 校正來源={ri_mode}，series={args.ri_series}"
                + ("（假設未驗證）" if isinstance(ri_calibration, dict)
                   and ri_calibration.get("assumed_unverified") else ""))
        except Exception as _e:
            log(f"[warn] RI 校正解析失敗（{_e}）；熱圖維持保留時間軸")

    # 預設把熱圖存到 results/<名>_heatmap.png（--save 可改路徑）
    heatmap_path = args.save or (base_out + "_heatmap.png")
    plot_heatmap(data, axes, header,
                 cmap=args.cmap, clip=tuple(args.clip), rt_unit=args.rt_unit,
                 log_scale=args.log, save=heatmap_path, show=not args.no_show,
                 figsize=figsize, dpi=args.dpi, ri_calibration=ri_calibration)


if __name__ == "__main__":
    main()


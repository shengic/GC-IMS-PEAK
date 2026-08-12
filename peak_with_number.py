#!/usr/bin/env python3
"""
peak_with_number.py — Render a GC-IMS overlay with peak circles AND peak-id numbers.
Version: 3.1 — by Albert Sheng

Changelog:
  3.1 — 移除熱圖右上角的軸標註（改由 UI 的「ⓘ 軸說明」呈現）。
  ver.02 — Fixed the OOM crash in write_overlay_numbered(): now downsamples the
           intensity matrix before passing to ax.imshow(), matching the fix
           already applied to readGAS.py / peaks.py ver.02. Reuses
           peaks._downsample_for_display() since this module already hard-depends
           on peaks for load_surface / RESULTS_DIR.
  ver.01 — 初版。

This is a *display-only* helper for main.py. It does NOT run peak detection;
it reuses the intensity surface (from the .npz produced by readGAS.py) and the
peaks list (from <name>_peaks.json produced by peaks.py), then draws each peak as
a red circle with its peak_id number next to it — all in matplotlib DATA
coordinates, so the number always sits exactly on its circle regardless of axes
margins or the y-axis orientation.

peaks.py is intentionally left unchanged.

Inputs:
  path            .npz or .mea (used only to locate/load the intensity surface)

Options:
  --peaks-json    override path to <name>_peaks.json (default: results/<name>_peaks.json)
  --out           override output PNG path (default: results/<name>_overlay_numbered.png)
  --figsize WxH   figure size in inches (default 8x9)
  --dpi           output dpi (default 150)
  --cmap          colormap (default viridis)

Output:
  results/<name>_overlay_numbered.png
"""

import os
import sys
import json
import argparse

import numpy as np

# Reuse peaks.py's loader so behavior stays consistent (does not modify peaks.py).
import peaks as _peaks

RESULTS_DIR = _peaks.RESULTS_DIR


def load_peaks_json(json_path):
    """Return the peaks list from a <name>_peaks.json file (utf-8, handles CJK paths)."""
    with open(json_path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    return doc.get("peaks", [])


def write_overlay_numbered(intensity, drift_ms, retention_s, peaks, path,
                           cmap="viridis", figsize=(8, 9), dpi=150,
                           ri_calibration=None):
    """Render heatmap + red circles + white peak-id numbers, all in data coords."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe

    # Same contrast scaling as peaks.py write_overlay for visual parity.
    sub = intensity[::max(1, intensity.shape[0] // 1000),
                    ::max(1, intensity.shape[1] // 1000)]
    vmin, vmax = np.percentile(sub, [1.0, 99.5])

    # Horizontal axis normalized to RIP (matches readGAS.plot_heatmap and
    # peaks.write_overlay per user's display decision). Scatter/annotate use
    # each peak's own drift_relative when available; fall back to computed value.
    import rip as _rip
    rip_index, _ = _rip.find_rip(intensity)
    drift_norm = drift_ms / drift_ms[rip_index]
    drift_label = "Drift relative to RIP (RIP at 1.0)"

    # Downsample before imshow — see peaks.py._downsample_for_display for the
    # OOM background.
    img_ds = _peaks._downsample_for_display(intensity, figsize, dpi)

    # Stage 4: resample rows to be uniform in RI so the RI axis is LINEAR (not
    # log). x-axis drift normalization untouched; circles/numbers use RI too.
    y_extent = (float(retention_s[0]), float(retention_s[-1]))
    y_label = "Retention time [s]"
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
                ri_fn = _cal.make_rt_to_ri(ri_calibration)
        except Exception as _e:
            print(f"[warn] RI warp skipped ({_e})")

    fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)
    ax.imshow(img_ds, aspect="auto", origin="lower",
              extent=[drift_norm[0], drift_norm[-1], y_extent[0], y_extent[1]],
              cmap=cmap, vmin=vmin, vmax=vmax, interpolation="nearest")

    def _peak_x(p):
        return p.get("drift_relative", p["drift_ms"] / drift_ms[rip_index])

    def _peak_y(p):
        return ri_fn(p["retention_s"])[0] if ri_fn is not None else p["retention_s"]

    xs = [_peak_x(p) for p in peaks]
    ys = [_peak_y(p) for p in peaks]
    ax.scatter(xs, ys, s=28, facecolors="none", edgecolors="red", linewidths=0.8)

    # White number with a thin black outline so it reads on any colormap.
    outline = [pe.withStroke(linewidth=2.0, foreground="black")]
    for p in peaks:
        ax.annotate(
            str(p.get("peak_id", "")),
            xy=(_peak_x(p), _peak_y(p)),
            xytext=(3, 3), textcoords="offset points",
            color="white", fontsize=7, fontweight="bold",
            ha="left", va="bottom", path_effects=outline,
        )

    ax.set_xlabel(drift_label)
    ax.set_ylabel(y_label)

    from matplotlib.ticker import MultipleLocator, FormatStrFormatter
    ax.xaxis.set_major_locator(MultipleLocator(0.5))
    ax.xaxis.set_minor_locator(MultipleLocator(0.1))
    ax.xaxis.set_major_formatter(FormatStrFormatter("%.1f"))


    ax.set_title(f"Detected {len(peaks)} peaks (red = detected, numbered)")
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path


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
    ap = argparse.ArgumentParser(
        description="Render GC-IMS overlay with peak circles and id numbers.")
    ap.add_argument("path", help="輸入 .npz 或 .mea（用來載入強度面）")
    ap.add_argument("--peaks-json", default=None,
                    help="peaks.json 路徑（預設 results/<名>_peaks.json）")
    ap.add_argument("--out", default=None,
                    help="輸出 PNG 路徑（預設 results/<名>_overlay_numbered.png）")
    ap.add_argument("--figsize", default="8x9", metavar="WxH",
                    help="圖大小（英吋），如 14x11 (預設 8x9)")
    ap.add_argument("--dpi", type=int, default=150, help="輸出 dpi (預設 150)")
    ap.add_argument("--cmap", default="viridis", help="配色 (預設 viridis)")
    ap.add_argument("--ri-series", default=None,
                    help="把 y 軸從保留時間改標成保留指數 RI（第四階段）；值為參照系列"
                         "（如 n_alkane/custom），會從原始 .mea 資料夾的 STD 解析校正。"
                         "x 軸 drift 正規化不受影響")
    ap.add_argument("--ri-start-carbon", type=int, default=None,
                    help="--ri-series n_alkane 的起始碳數（覆寫暫定 C6）")
    args = ap.parse_args()

    try:
        fw, fh = (float(x) for x in args.figsize.lower().split("x"))
        figsize = (fw, fh)
    except ValueError:
        print(f"--figsize 格式應為 WxH，收到 {args.figsize!r}，改用 8x9")
        figsize = (8, 9)

    path = args.path
    base = os.path.splitext(os.path.basename(path))[0]

    # Prefer the already-generated npz surface (fast) over re-reading a .mea.
    npz_candidate = os.path.join(RESULTS_DIR, base + ".npz")
    surface_path = npz_candidate if os.path.exists(npz_candidate) else path

    print(f"載入強度面：{surface_path}")
    intensity, drift_ms, retention_s, _meta = _peaks.load_surface(surface_path)

    peaks_json = args.peaks_json or os.path.join(RESULTS_DIR, base + "_peaks.json")
    if not os.path.exists(peaks_json):
        print(f"錯誤：找不到 peaks.json：{peaks_json}", file=sys.stderr)
        sys.exit(1)
    peaks = load_peaks_json(peaks_json)
    print(f"讀入 {len(peaks)} 個峰")

    # Stage 4: resolve RI calibration from the ORIGINAL .mea's folder (STDs live
    # in GAS/<batch>/, not results/). Prefer the .mea path; else the surface meta.
    ri_calibration = None
    if args.ri_series:
        try:
            import calibration as _cal
            src = path if path.lower().endswith(".mea") else (_meta or {}).get("source")
            folder = os.path.dirname(os.path.abspath(src)) if src else None
            if folder:
                ri_calibration, ri_mode, _ = _cal.resolve_ri_calibration(
                    folder, series_key=args.ri_series,
                    start_carbon=args.ri_start_carbon)
                print(f"[RI] source={ri_mode}, series={args.ri_series}")
            else:
                print("[warn] 找不到原始 .mea 資料夾，RI 軸略過")
        except Exception as _e:
            print(f"[warn] RI 校正解析失敗（{_e}）；維持保留時間軸")

    out = args.out or os.path.join(RESULTS_DIR, base + "_overlay_numbered.png")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    write_overlay_numbered(intensity, drift_ms, retention_s, peaks, out,
                           cmap=args.cmap, figsize=figsize, dpi=args.dpi,
                           ri_calibration=ri_calibration)
    print(f"完成：{out}")


if __name__ == "__main__":
    main()

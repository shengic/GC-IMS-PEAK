#!/usr/bin/env python3
"""
peak_with_number.py — Render a GC-IMS overlay with peak circles AND peak-id numbers.

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
                           cmap="viridis", figsize=(8, 9), dpi=150):
    """Render heatmap + red circles + white peak-id numbers, all in data coords."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe

    # Same contrast scaling as peaks.py write_overlay for visual parity.
    sub = intensity[::max(1, intensity.shape[0] // 1000),
                    ::max(1, intensity.shape[1] // 1000)]
    vmin, vmax = np.percentile(sub, [1.0, 99.5])

    fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)
    ax.imshow(intensity, aspect="auto", origin="lower",
              extent=[drift_ms[0], drift_ms[-1], retention_s[0], retention_s[-1]],
              cmap=cmap, vmin=vmin, vmax=vmax, interpolation="nearest")

    xs = [p["drift_ms"] for p in peaks]
    ys = [p["retention_s"] for p in peaks]
    ax.scatter(xs, ys, s=28, facecolors="none", edgecolors="red", linewidths=0.8)

    # White number with a thin black outline so it reads on any colormap.
    outline = [pe.withStroke(linewidth=2.0, foreground="black")]
    for p in peaks:
        ax.annotate(
            str(p.get("peak_id", "")),
            xy=(p["drift_ms"], p["retention_s"]),
            xytext=(3, 3), textcoords="offset points",
            color="white", fontsize=7, fontweight="bold",
            ha="left", va="bottom", path_effects=outline,
        )

    ax.set_xlabel("Drift time [ms]")
    ax.set_ylabel("Retention time [s]")
    ax.set_title(f"Detected {len(peaks)} peaks (red = detected, numbered)")
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path


def main():
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

    out = args.out or os.path.join(RESULTS_DIR, base + "_overlay_numbered.png")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    write_overlay_numbered(intensity, drift_ms, retention_s, peaks, out,
                           cmap=args.cmap, figsize=figsize, dpi=args.dpi)
    print(f"完成：{out}")


if __name__ == "__main__":
    main()

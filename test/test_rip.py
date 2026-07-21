"""
test_rip.py — Smoke test for rip.py (第一階段 RIP 正規化)。

雙用途：`pytest test/test_rip.py`（正式測試）或 `python test/test_rip.py`
（獨立除錯腳本，含詳細 print 輸出）。
"""
import os
import sys
import numpy as np

# Windows cp950 主控台救援
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 把專案根加進 sys.path，讓 pytest 與獨立執行都能 import
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import readGAS
import rip

MEA = os.path.join(PROJECT_ROOT, "GAS", "嘉義大學＿咖啡發酵", "260625_141215_STD.mea")


def test_rip_smoke():
    print(f"Loading {MEA} ...")
    data, header, axes = readGAS.read_mea(MEA)
    drift_ms = axes["drift_ms"]
    retention_s = axes["retention_s"]
    print(f"  matrix shape = {data.shape}  (rt, dt)")
    print(f"  drift_ms  range = {drift_ms[0]:.4f} .. {drift_ms[-1]:.4f} ms  ({len(drift_ms)} pts)")
    print(f"  retention range = {retention_s[0]:.2f} .. {retention_s[-1]:.2f} s")
    print(f"  Chunk sample rate = {header.get('Chunk sample rate')}")

    # ---- 1. find_rip ----
    rip_index, rip_intensity = rip.find_rip(data, start=200)
    rip_ms = float(drift_ms[rip_index])
    print()
    print(f"[find_rip] rip_index = {rip_index}  -> drift = {rip_ms:.4f} ms  intensity = {rip_intensity}")

    # 交叉核對：RT=0 這列跳前 start 之後的 top-5 高點，find_rip 應回傳其中最高者
    row0 = data[0, :]
    top5_after_start = (np.argsort(row0[200:])[-5:][::-1] + 200).tolist()
    print("  RT=0 row top-5 after skipping first 200:")
    for idx in top5_after_start:
        marker = "  <- find_rip picked" if int(idx) == rip_index else ""
        print(f"    idx={idx:5d}  drift={drift_ms[idx]:.4f} ms  I={row0[idx]}{marker}")

    assert rip_index == int(top5_after_start[0]), "find_rip 應回傳 top-5 的第 1 名"
    print("  [OK] find_rip picked the argmax of RT=0 row (after skipping first 200)")

    # 順便印絕對 top-5（含前 200 點），確認「跳前 200」是否排除了更高的假 RIP
    absolute_top5 = np.argsort(row0)[-5:][::-1].tolist()
    print("  RT=0 row absolute top-5 (including the first 200):")
    for idx in absolute_top5:
        note = "  (in the first 200, skipped)" if idx < 200 else ""
        print(f"    idx={idx:5d}  drift={drift_ms[idx]:.4f} ms  I={row0[idx]}{note}")

    # ---- 2. attach_drift_relative 數學驗證 ----
    fake_peaks = [
        {"peak_id": 1, "dt_index": rip_index},        # 1.0
        {"peak_id": 2, "dt_index": rip_index * 2},    # 2.0
        {"peak_id": 3, "dt_index": rip_index // 2},   # ~0.5
        {"peak_id": 4, "dt_index": 100},              # << RIP
    ]
    rip.attach_drift_relative(fake_peaks, rip_index)
    print()
    print("[attach_drift_relative] fake-peak sanity:")
    for p in fake_peaks:
        print(f"  peak_id={p['peak_id']}  dt_index={p['dt_index']:5d}  drift_relative={p['drift_relative']}")

    assert fake_peaks[0]["drift_relative"] == 1.0, "peak at RIP position must give drift_relative == 1.0"
    assert fake_peaks[1]["drift_relative"] == 2.0, "peak at 2*RIP must give drift_relative == 2.0"
    print()
    print("[OK] all assertions passed")


if __name__ == "__main__":
    test_rip_smoke()

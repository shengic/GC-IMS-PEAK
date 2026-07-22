"""
test_select_from_maxima.py  —  peaks.select_from_maxima() 的選取語意

依專案慣例，這個檔案同時是：
  - pytest 測試（test_select_from_maxima_smoke）
  - 可執行的除錯腳本（python test/test_select_from_maxima.py）

鎖住的意圖（不是「程式現在怎麼跑」，而是「為什麼必須這樣跑」）：

  R004（排除 RIP 柱狀帶）必須在突出度門檻**之前**生效。
  門檻是相對值 thresh = prom_frac × max_prominence，而 RIP 通常就是全圖最大
  突出度（實測 A_1_3：RIP 5052 vs 非 RIP 最大 1545），把它留在候選集裡會把門檻
  墊高 2～3 倍，誤殺真實分析物峰。

  若有人日後把 R004 改回「偵測完再篩掉」，圈看起來一樣會消失，但被墊高的門檻
  誤殺的弱峰救不回來——test_r004_before_gate_lowers_threshold() 會因此失敗。
  這正是這個檔案存在的理由。

不依賴任何真實資料檔，fresh clone 可直接跑。
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import peaks as P


W = 100           # 漂移軸寬度
H = 10            # 保留時間軸高度
RIP_INDEX = 50    # drift_relative = dt_index / 50


def _maxima(entries):
    """entries: [(row, col, prominence), ...] → select_from_maxima() 要的平行陣列。"""
    pix = np.array([r * W + c for r, c, _ in entries], dtype=np.int64)
    prom = np.array([p for _, _, p in entries], dtype=np.float32)
    # 強度取一個與突出度有關但不相等的值，確保程式不會把兩者混用
    val_raw = (prom + 200).astype(np.int32)
    val_smooth = val_raw.astype(np.float32)
    flatness = np.full(len(entries), np.nan, dtype=np.float32)
    return pix, val_smooth, prom, val_raw, flatness


# RIP 柱狀帶上的巨峰、一個真實峰、一個弱峰
ENTRIES = [
    (1, RIP_INDEX, 1000.0),   # RIP 本身
    (3, 20, 100.0),           # 真實分析物峰
    (5, 80, 5.0),             # 弱峰：只有在門檻降下來之後才活得下來
]


def _select(rip_half_width, prom_frac=0.02):
    pix, val_smooth, prom, val_raw, flat = _maxima(ENTRIES)
    return P.select_from_maxima(
        pix, val_smooth, prom, val_raw, flat, (H, W), floor=0.0,
        prom_frac=prom_frac, min_prominence=0.0, min_distance=0, top_n=0,
        rip_index=RIP_INDEX, rip_half_width=rip_half_width, verbose=False,
    )


def test_r004_off_keeps_rip_and_inflated_threshold():
    """R004 關閉時：RIP 留在候選集，門檻被它墊高，弱峰被誤殺。"""
    peaks, stats = _select(rip_half_width=0.0)
    assert stats["n_rip_excluded"] == 0
    assert stats["max_prominence"] == 1000.0
    assert stats["prom_threshold"] == 20.0          # 0.02 × 1000
    cols = {p["dt_index"] for p in peaks}
    assert RIP_INDEX in cols, "R004 關閉時 RIP 應該留著"
    assert 80 not in cols, "門檻 20 應該砍掉 prominence=5 的弱峰"


def test_r004_before_gate_lowers_threshold():
    """R004 開啟時：RIP 在算 max_prominence 之前就被剔除，門檻因此降下來。

    這是整個設計的重點。若 R004 改成偵測後才套用，max_prominence 仍會是 1000、
    門檻仍是 20，弱峰（prominence=5）就救不回來，本測試會失敗。
    """
    peaks, stats = _select(rip_half_width=0.02)
    assert stats["n_rip_excluded"] == 1
    assert stats["max_prominence"] == 100.0, "max_prominence 必須排除 RIP 後才算"
    assert stats["prom_threshold"] == 2.0            # 0.02 × 100
    cols = {p["dt_index"] for p in peaks}
    assert RIP_INDEX not in cols, "RIP 不該出現在結果裡"
    assert 80 in cols, "門檻降到 2 之後，弱峰必須被救回來"
    assert 20 in cols


def test_rip_band_width_is_relative_to_rip_index():
    """帶寬是 drift_relative 的比例，不是絕對取樣點數。"""
    pix, val_smooth, prom, val_raw, flat = _maxima(
        [(0, 50, 500.0), (1, 52, 400.0), (2, 60, 300.0)])
    # half_width=0.05 → dt_index ∈ [47.5, 52.5]，涵蓋 50 與 52 但不含 60
    _peaks, stats = P.select_from_maxima(
        pix, val_smooth, prom, val_raw, flat, (H, W), floor=0.0,
        prom_frac=0.02, min_prominence=0.0, min_distance=0, top_n=0,
        rip_index=RIP_INDEX, rip_half_width=0.05, verbose=False)
    assert stats["n_rip_excluded"] == 2


def test_empty_maxima_does_not_crash():
    """空輸入要回傳空清單與完整 stats 骨架，不能拋例外（UI 會直接吃這個回傳）。"""
    empty = np.array([], dtype=np.int64)
    peaks, stats = P.select_from_maxima(
        empty, empty.astype(np.float32), empty.astype(np.float32),
        empty.astype(np.int32), None, (H, W), floor=0.0, verbose=False)
    assert peaks == []
    assert stats["n_raw_maxima"] == 0
    assert stats["n_final"] == 0


def test_select_from_maxima_smoke():
    """整體煙霧測試：兩種模式都能跑完並產出結構正確的峰。"""
    for hw in (0.0, 0.02):
        peaks, stats = _select(rip_half_width=hw)
        assert stats["n_final"] == len(peaks)
        for p in peaks:
            assert {"peak_id", "rt_index", "dt_index", "intensity",
                    "prominence", "edge_dist", "saturated"} <= set(p)


if __name__ == "__main__":
    def sep(title):
        print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")

    sep("[1] R004 關閉 —— RIP 留著，門檻被墊高")
    pk, st = _select(0.0)
    print(f"  max_prominence={st['max_prominence']}  threshold={st['prom_threshold']}")
    for p in pk:
        print(f"    dt_index={p['dt_index']:3d}  prominence={p['prominence']}")

    sep("[2] R004 開啟 —— RIP 在門檻之前被剔除")
    pk, st = _select(0.02)
    print(f"  n_rip_excluded={st['n_rip_excluded']}")
    print(f"  max_prominence={st['max_prominence']}  threshold={st['prom_threshold']}")
    for p in pk:
        print(f"    dt_index={p['dt_index']:3d}  prominence={p['prominence']}")

    sep("[3] 差異")
    print("  門檻由 20.0 降為 2.0，prominence=5 的弱峰因此被救回來。")
    print("  這就是 R004 必須排在突出度門檻之前的原因。")

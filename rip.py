"""
rip.py  —  GC-IMS Identify Workflow 第一階段：RIP 正規化
Version: 3.1 — by Albert Sheng

依 GC-IMS_Identify_Workflow.md §第一階段（draft.13 定案）：
  - find_rip(intensity, start=200) → (rip_index, rip_intensity)
    取 intensity[0, :]（RT=0 那一列），跳過前 start 個取樣點後取最大值索引。
    對照 VOCal 反編譯：CleanMEA.getRIP()（MEA2Images.jar）。
  - attach_drift_relative(peaks, rip_index) → 為每個峰補上 drift_relative = dt_index / rip_index

本模組刻意保持純函式介面：
  - 不讀檔、不寫檔、不吃路徑
  - 由 identify.py（未寫）或 peaks.py CLI 呼叫串接
  - rip_index 必須逐檔案獨立算，禁止在批次迴圈外快取（見 workflow §第一階段 point 6）

依賴：numpy
"""

import numpy as np


def find_rip(intensity, start=200):
    """
    定位 RIP（Reactant Ion Peak）在漂移軸上的取樣點索引。

    參數
    ----
    intensity : ndarray (H, W)
        readGAS.py 產出的原始強度矩陣。第 0 列（RT=0）用於定位 RIP。
    start : int
        跳過漂移軸前 start 個取樣點後才找最大值。預設 200，沿用 VOCal
        CleanMEA.getRIP() 的原值。[待決策] 是否依 sample_rate_khz 等比例
        調整——目前不調，維持與 VOCal 一致的行為。

    回傳
    ----
    (rip_index, rip_intensity) : (int, int)
        rip_index      —— RIP 在漂移軸上的絕對取樣點索引（含 start 偏移）
        rip_intensity  —— RIP 位置的原始強度值（int，供 stats 佐證用）

    Raises
    ------
    ValueError
        當 intensity 非二維、寬度不足 start、或 RT=0 這列全為零時。
    """
    arr = np.asarray(intensity)
    if arr.ndim != 2:
        raise ValueError(f"intensity 必須是二維陣列，收到 shape={arr.shape}")

    W = arr.shape[1]
    if W <= start:
        raise ValueError(
            f"漂移軸寬度 {W} 不足以跳過前 {start} 個取樣點；"
            f"請確認 intensity 方向正確（[rt, dt]）"
        )

    row0 = arr[0, start:]
    rel_idx = int(np.argmax(row0))
    rip_index = start + rel_idx
    rip_intensity = int(arr[0, rip_index])

    return rip_index, rip_intensity


def attach_drift_relative(peaks, rip_index):
    """
    為每個峰補上 drift_relative = dt_index / rip_index 欄位，就地修改。

    參數
    ----
    peaks : list[dict]
        peaks.py detect_peaks() 產出的峰清單，每筆需含 "dt_index"。
    rip_index : int
        find_rip() 回傳的 rip_index。

    回傳
    ----
    peaks : list[dict]
        同傳入的清單（就地修改後回傳，方便鏈式呼叫）。每筆多一個 drift_relative 欄位，
        四捨五入到小數第 5 位。

    Raises
    ------
    ValueError
        當 rip_index <= 0 時（會導致除以零或負值）。
    """
    if rip_index <= 0:
        raise ValueError(f"rip_index 必須為正整數，收到 {rip_index}")

    for p in peaks:
        p["drift_relative"] = round(p["dt_index"] / rip_index, 5)
    return peaks

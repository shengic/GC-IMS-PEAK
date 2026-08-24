# Stage 4（RT→RI 校準）程式碼片段彙整 —— 供 Claude Code CLI 參考

**Version: 3.3 — by Albert Sheng**

**性質聲明**：以下都是 claude.ai 對話中討論、驗證過邏輯的**片段/雛型**，不是完整可直接執行的模組——變數命名、錯誤處理、與既有 `readGAS.py`/`peaks.py` 的實際介面銜接，都還需要 CLI 依專案現況調整。請配合 `Stage4_RT-RI_briefing_for_CLI.md` 跟 `GC-IMS_Identify_Workflow.md`（draft.18）第四階段一起讀，後者是設計權威版本，這份只是程式碼骨架集中存放。

---

## 1. `.mea` 二進位讀取（已用兩支真實檔案交叉驗證）

```python
import numpy as np

def load_mea_matrix(path, n_rt, n_dt):
    """
    n_rt, n_dt 應從表頭 'Chunks count' / 'Chunk sample count' 讀出，不要寫死。
    偏移量 +2 已用 260625_141215_STD.mea 與 260625_012251_STD.mea 交叉驗證。
    """
    with open(path, 'rb') as f:
        raw = f.read()
    idx = raw.find(b'nom Drift Tube Length')
    nl = raw.find(b'\n', idx)
    data_start = nl + 2  # +1 換行本身，+1 額外對齊位元組
    n = n_rt * n_dt
    arr = np.frombuffer(raw[data_start:data_start + n*2], dtype='<i2').astype(np.int32)
    return arr.reshape(n_rt, n_dt)

def parse_header(path):
    with open(path, 'rb') as f:
        raw = f.read(20000)  # 表頭通常在前幾千 bytes 內
    idx = raw.find(b'nom Drift Tube Length')
    nl = raw.find(b'\n', idx)
    text = raw[:nl+1].decode('latin-1')
    header = {}
    for line in text.split('\n'):
        if '=' in line:
            k, _, v = line.partition('=')
            header[k.strip()] = v.strip().strip('"')
    return header
```

## 2. RIP 定位（`find_rip`，沿用既有邏輯，未變動）

```python
def find_rip(matrix, start=200):
    row0 = matrix[0, :]
    return row0[start:].argmax() + start
```

## 3. STD 峰偵測（baseline-corrected，排除 RIP 附近窗口）

```python
from scipy.signal import find_peaks
from scipy.ndimage import median_filter

def detect_std_anchors(matrix, rip_idx, rt_step_s, exclude=20,
                        baseline_window=301, prominence=100, distance=15):
    mask = np.ones(matrix.shape[1], dtype=bool)
    mask[rip_idx-exclude:rip_idx+exclude] = False
    profile = matrix[:, mask].max(axis=1).astype(float)
    baseline = median_filter(profile, size=baseline_window, mode='nearest')
    resid = profile - baseline
    peak_idx, props = find_peaks(resid, prominence=prominence, distance=distance)

    anchors = []
    for p, prom in zip(peak_idx, props['prominences']):
        row = matrix[p, :].copy()
        row[~mask] = -99999
        dt_idx = row.argmax()
        anchors.append({
            "rt_idx": int(p),
            "rt_sec": p * rt_step_s,
            "intensity": int(matrix[p, dt_idx]),
            "prominence": float(prom),
            "dt_rel": dt_idx / rip_idx,
        })
    return anchors
```

## 4. 六點篩選：用 DT_rel 間距均勻度剔除拖尾偽影

**[本輪對話定量驗證，非反編譯]**——這不是通用演算法，是這次實際案例用的判斷方法，套用到別的 STD 檔案時，門檻/邏輯可能需要調整：

```python
def select_clean_anchors(candidates, ambiguous_group, target_n=6):
    """
    candidates: 已確定乾淨的候選點（依 RT 排序）
    ambiguous_group: 待判斷的候選點清單（例如同一個 RT 窗口內的雙峰）
    用 DT_rel 間距標準差最小化，決定 ambiguous_group 裡哪個該納入
    """
    best = None
    best_std = float('inf')
    for candidate in ambiguous_group:
        trial = sorted(candidates + [candidate], key=lambda a: a['rt_sec'])
        dt_rel = np.array([a['dt_rel'] for a in trial])
        std = np.diff(dt_rel).std()
        if std < best_std:
            best_std = std
            best = candidate
    return sorted(candidates + [best], key=lambda a: a['rt_sec']), best_std

# 本次實際結果：
#   candidates = 282.0/400.3/521.8/697.0/949.0 五個乾淨點
#   ambiguous_group = [334.3s (dt_rel=1.234), 347.9s (dt_rel=1.104，與282.0完全重疊)]
#   -> 選中 334.3，DT_rel 間距 std=0.0034；347.9 那組是 0.0797，差 23 倍
```

## 5. `series_key` 可插拔架構（化合物身分未確認前的過渡設計）

```python
# reference_series.py
REFERENCE_SERIES = {
    "n_alkane": {
        "assumed": True,
        "ri_formula": lambda carbon_n: 100 * carbon_n,
        "assumed_start_carbon": 6,
        "note": "假設正構烷烴，起始碳數未知暫定C6，兩項皆未經化合物身分驗證",
    },
    "ketone": {"assumed": False, "ri_values": None, "note": "待對照表"},
    "custom":        {"assumed": False, "ri_values": None, "note": "使用者直接指定"},
}

def assign_ri(sorted_rt_list, series_key):
    series = REFERENCE_SERIES[series_key]
    if series_key == "n_alkane":
        start_n = series["assumed_start_carbon"]
        return [series["ri_formula"](start_n + i) for i in range(len(sorted_rt_list))]
    if series["ri_values"] is not None:
        assert len(series["ri_values"]) == len(sorted_rt_list), "錨點數與對照表長度不符"
        return series["ri_values"]
    raise ValueError(f"'{series_key}' 尚無可用資料")
```

## 6. `single_point_relative` 降級模式（建議優先實作，不需化合物身分）

```python
def build_single_point_reference(anchors_rt):
    return {
        "mode": "single_point_relative",
        "anchor_rt_seconds": sorted(anchors_rt),
        "n_anchors": len(anchors_rt),
        "known_ri_available": False,
        "warning": "此為單日單點相對參照，非跨儀器可比對的絕對 RI 校準",
    }

def build_ri_calibration(anchors_rt, series_key="n_alkane"):
    """有 series_key 時才產生絕對 RI 值，否則走 single_point_relative"""
    from scipy.interpolate import interp1d
    anchors_rt = sorted(anchors_rt)
    ri_values = assign_ri(anchors_rt, series_key)
    log_rt = np.log10(anchors_rt)
    interp_fn = interp1d(log_rt, ri_values, kind='linear',
                          bounds_error=False, fill_value='extrapolate')
    series = REFERENCE_SERIES[series_key]
    return {
        "mode": "series_calibrated",
        "series_used": series_key,
        "assumed_unverified": series.get("assumed", False),
        "interp_fn": interp_fn,
        "n_anchors": len(anchors_rt),
    }
```

## 7. 套用到其他樣品檔案（RT → RI 轉換本身）

```python
def apply_calibration(peak_rt_seconds, calibration):
    """
    peak_rt_seconds: 單一峰的 RT（秒）
    calibration: build_ri_calibration() 或 single_point_relative 的回傳值
    """
    if calibration["mode"] == "single_point_relative":
        anchors = np.array(calibration["anchor_rt_seconds"])
        # 相對位置：該峰落在哪兩個錨點之間、佔比多少（不換算絕對 RI）
        idx = np.searchsorted(anchors, peak_rt_seconds)
        if idx == 0 or idx == len(anchors):
            return {"relative_position": None, "note": "超出錨點涵蓋範圍，外插不可信"}
        lo, hi = anchors[idx-1], anchors[idx]
        frac = (peak_rt_seconds - lo) / (hi - lo)
        return {"relative_position": (idx-1, frac), "ri_value": None}

    if calibration["mode"] == "series_calibrated":
        log_rt = np.log10(peak_rt_seconds)
        ri = float(calibration["interp_fn"](log_rt))
        return {
            "ri_value": ri,
            "assumed_unverified": calibration["assumed_unverified"],
        }
```

## 8. 品質前置過濾（`Status` 欄位，已用 012251 實例驗證必要性）

```python
def std_quality_check(header, min_peaks=5, min_top_intensity=2000, n_anchors_found=None, top_intensity=None):
    """
    不要只看 Status=="doubtful" 這個布林標籤本身（本輪發現兩支檔案都標 doubtful，
    但品質差異巨大）——用實際找到的峰數/強度做判斷，Status/Status comment 當輔助解釋。
    """
    reasons = []
    if n_anchors_found is not None and n_anchors_found < min_peaks:
        reasons.append(f"峰數不足 ({n_anchors_found} < {min_peaks})")
    if top_intensity is not None and top_intensity < min_top_intensity:
        reasons.append(f"訊號強度過低 ({top_intensity} < {min_top_intensity})")
    return (len(reasons) == 0), reasons, header.get("Status comment", "")
```

## 9. 批次資料夾解析邏輯（設計稿為三層；**實作已是四層**）

> **[2026-08-24, v3.3 更新]** 本節的程式碼是**設計稿**，實際實作見
> `calibration.resolve_ri_calibration()`，且已多一層：
>
> ```
> (a)  batch_own_std           資料夾內有可用的 STD
> (a2) vocal_project_table     ← 新增：讀該資料夾 .gasprj 的 RI_Normalization 表
> (b)  borrowed_from_registry  同 instrument|column|method 的別批校正
> (c)  unavailable
> ```
>
> (a2) 排在 (b) 之前：`.gasprj` 是**本批次自己**的尺標，registry 是別批的。
> 實測四個 `GAS/` 資料夾，兩個走 (a)、兩個走 (a2)。另注意 **(b) 目前不可能觸發**
> ——沒有 registry 檔、production code 從未呼叫 `save_registry()`、且 `main.py`
> 不傳 `dims`（見 `status.md` open decision 8）。

```python
def scan_folder_for_std(folder_path):
    import glob
    std_files = []
    for f in glob.glob(f"{folder_path}/*.mea"):
        header = parse_header(f)
        if header.get("Sample", "").upper() == "STD":
            std_files.append(f)
    return std_files

def resolve_ri_calibration(folder_path, instrument, column, method,
                            registry_path="ri_calibration_registry.json"):
    std_files = scan_folder_for_std(folder_path)
    # ...對每支 std_files 跑 detect_std_anchors + std_quality_check，過濾不合格者...
    if std_files:
        return build_calibration_from_std(std_files), "batch_own_std"  # 待實作

    registry = load_registry(registry_path)  # 待實作：JSON 讀寫
    key = f"{instrument}|{column}|{method}"
    if key in registry:
        entry = registry[key]
        days_gap = (today() - entry["built_date"]).days
        return entry["calibration"], f"borrowed_from_registry(days_gap={days_gap})"

    return None, "unavailable"
```

## 10. 資料夾層級快取（避免每次點檔案都重跑）

```python
import os, json

class FolderCalibrationCache:
    _session_cache = {}

    @classmethod
    def resolve(cls, folder_path, instrument, column, method):
        if folder_path in cls._session_cache:
            return cls._session_cache[folder_path]

        sidecar = os.path.join(folder_path, "_folder_calibration.json")
        if os.path.exists(sidecar) and cls._is_fresh(sidecar, folder_path):
            result = json.load(open(sidecar))
        else:
            calibration, mode = resolve_ri_calibration(folder_path, instrument, column, method)
            result = {"calibration": calibration, "mode": mode}
            json.dump(result, open(sidecar, "w"))

        cls._session_cache[folder_path] = result
        return result

    @staticmethod
    def _is_fresh(sidecar, folder_path):
        import glob
        sidecar_mtime = os.path.getmtime(sidecar)
        mea_files = glob.glob(f"{folder_path}/*.mea")
        return all(os.path.getmtime(f) < sidecar_mtime for f in mea_files)
```

---

## 本次實測得到的具體數字（141215_STD.mea，draft.18 版本）

> **⚠ 以下 RT 為修正前的舊保留時間軸**（`averages×trigger`，是實際值的 6/7）。2026-08-12 已修正為 `(averages+1)×trigger`；換算表見 `ketone_RI_provenance.md` §0.0。`RT_STEP_S = 0.126` 應為 **0.147**，`ANCHORS_RT_SECONDS` 的新軸值為 `[389.7, 467.0, 609.5, 813.4, 1107.2, 1523.4]`（且 C9 已確認在 1523.4，非原本以為的缺席）。本節保留原值作為當時的紀錄。

```python
RIP_IDX = 680
RT_STEP_S = 0.126
ANCHORS_RT_SECONDS = [282.0, 334.3, 400.3, 521.8, 697.0, 949.0]  # 6點，347.9已排除
ANCHORS_DT_REL      = [1.104, 1.234, 1.356, 1.487, 1.613, 1.737]
```

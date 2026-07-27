"""
dt_convert.py  —  GC-IMS Identify Workflow 第二階段：K0 換算
Version: 3.0 — by Albert Sheng

依 GC-IMS_Identify_Workflow.md §第二階段（draft.13 定案）：
  - 雙模式設計，明確標記 provenance：
      standard_based    首選；用已知 K0 標準品現場校準反推 instrument_constant
                        後代入公式，L/T/P 個別不確定性被整體校準吸收
      raw_parameters    退路；直接讀表頭四常數代入原公式，帶已知殘留誤差
      unavailable       兩者皆無 → 回傳 None + 標記原因，不靜默造假
  - 每個峰的 k0_value 必須連同 k0_mode 一起輸出，供下游區分數值可信度

明確的未解決狀態聲明（workflow §第二階段最後）：
  此問題未完全解決，兩個關鍵前提尚未到位：
    (a) 是否有可行的 K0 校準標準品或方法 → 決定能否啟用 standard_based
    (b) raw_parameters 模式下 T/P 對應哪個表頭欄位仍需人工確認
  在這兩點解決之前，任何非 standard_based 模式產出的 k0_value 都應視為
  暫定值。本模組刻意不猜哪個 Start temp / EPC pressure 是「對的」——
  raw_parameters 模式要求呼叫者顯式指定 T/P 來源欄位，避免預設值誤導。

confirmed 表頭欄位（本輪 draft.09 實測 260625_141215_STD.mea 驗證）：
  L → 'nom Drift Tube Length'        (µm → cm 轉換內建)
  U → 'nom Drift Potential Difference' (V, 直接使用)
  sample_rate_khz → 'Chunk sample rate' (kHz, 直接使用)

unresolved 表頭欄位（不猜）：
  T → 'Start temp 1'..'Start temp 6' 有六個候選，本輪實測值 45/60/80/80/45/off
  P → 'Start pressure EPC IMS' 是否等於管內實際壓力尚未確認
      （另有 'Start ambient pressure' ~ 100 kPa 亦為候選）

依賴：僅 Python stdlib + readGAS.hnum 共用工具（本模組不 import readGAS，僅
      在內部小型 helper 中複製 hnum 的邏輯，避免循環依賴）
"""

import json
import os
import re


# --------------------------------------------------------------------------- #
# 數學核心：兩種 K0 換算公式（純函式）
# --------------------------------------------------------------------------- #
def k0_from_instrument_constant(dt_raw, sample_rate_khz, instrument_constant, U):
    """
    standard_based 模式：用已知 K0 標準品校準反推的 instrument_constant，
    合併吸收掉個別 L/T/P 不確定性。

    參數
    ----
    dt_raw : float  漂移軸取樣點索引（peak["dt_index"]）
    sample_rate_khz : float  逐檔案讀取，禁止全域快取（見 rip.py §第一階段 point 7）
    instrument_constant : float  校準過的儀器常數（校準時就把 ah × L² 整團解出）
    U : float  漂移電位差（V）

    回傳
    ----
    K0 : float  reduced mobility (cm²/V/s)
    """
    t_d_s = (dt_raw / sample_rate_khz) / 1000.0   # kHz → s
    return instrument_constant / (t_d_s * U)


def k0_from_raw_params(dt_raw, sample_rate_khz, L_cm, T_C, P_mbar, U):
    """
    raw_parameters 模式：直接用表頭四常數代入標準 K0 公式。

    ⚠ 本模式產出的 K0 帶有已知殘留誤差——L/T/P 為儀器標稱值/控制器設定值，
    非實測物理狀態；跟外部資料庫（別的實驗室、別的機台）比對時可信度有限。
    僅在無法取得 K0 校準標準品時作退路使用，並在 _peaks.json 標明
    k0_mode="raw_parameters"。

    參數（**單位必須符合以下要求，否則結果無意義**）
    ----
    dt_raw : float           漂移軸取樣點索引
    sample_rate_khz : float  取樣率（kHz）
    L_cm : float             漂移管長度（cm）— 若表頭給 µm，先除以 10000
    T_C : float              漂移管溫度（°C）
    P_mbar : float           漂移管壓力（mbar）— 若表頭給 kPa，先乘以 10
    U : float                漂移電位差（V）

    回傳
    ----
    K0 : float  reduced mobility (cm²/V/s) — 帶已知殘留誤差
    """
    t_d_s = (dt_raw / sample_rate_khz) / 1000.0
    ah = (273.0 / (273.0 + T_C)) * (P_mbar / 1013.0)
    K0 = ah * L_cm ** 2 / (t_d_s * U)
    return 1.0 / K0


# --------------------------------------------------------------------------- #
# 表頭解析（confirmed 欄位）
# --------------------------------------------------------------------------- #
def _hnum(header, key, default=None):
    """從表頭字串取第一個數字（同 readGAS.hnum；此處本地複製，避免循環 import）。"""
    if key not in header:
        return default
    m = re.search(r"-?\d+\.?\d*", header[key])
    return float(m.group()) if m else default


def extract_confirmed_params(header):
    """
    從 .mea 表頭抽出**已確認**的儀器常數。回傳 dict：
        L_cm            由 'nom Drift Tube Length' µm 換算
        U_V             'nom Drift Potential Difference'
        sample_rate_khz 'Chunk sample rate'

    找不到欄位 → 對應值為 None（呼叫者需處理）。**故意不含 T/P**（unresolved）。
    """
    L_um = _hnum(header, "nom Drift Tube Length")
    U_V = _hnum(header, "nom Drift Potential Difference")
    srate = _hnum(header, "Chunk sample rate")
    return {
        "L_cm": (L_um / 10000.0) if L_um is not None else None,   # µm → cm
        "U_V": U_V,
        "sample_rate_khz": srate,
    }


# --------------------------------------------------------------------------- #
# calibration_profile.json
# --------------------------------------------------------------------------- #
VALID_MODES = {"standard_based", "raw_parameters", "unavailable"}


def load_calibration_profile(path):
    """
    讀取 calibration_profile.json 並做欄位健全性檢查。回傳 dict。

    格式範例（workflow §第二階段）：
        {
          "profile_name": "...",
          "k0_calibration": {
            "mode": "standard_based",
            "instrument_constant": 12345.6,
            "calibrated_from": {"compound": "...", "known_k0": 1.85, "date": "..."}
          }
        }

    Raises
    ------
    FileNotFoundError, json.JSONDecodeError 原樣冒出
    ValueError                             mode 不在允許集合，或 standard_based
                                           缺 instrument_constant
    """
    with open(path, "r", encoding="utf-8") as f:
        profile = json.load(f)
    k0cal = profile.get("k0_calibration", {})
    mode = k0cal.get("mode")
    if mode not in VALID_MODES:
        raise ValueError(f"k0_calibration.mode 必須為 {VALID_MODES} 之一，收到 {mode!r}")
    if mode == "standard_based" and k0cal.get("instrument_constant") is None:
        raise ValueError("standard_based 模式必須提供 k0_calibration.instrument_constant")
    return profile


def default_profile_unavailable(reason="no calibration profile provided"):
    """建立最簡的 unavailable profile（找不到 profile 檔案時的預設）。"""
    return {
        "profile_name": "default_unavailable",
        "k0_calibration": {"mode": "unavailable", "reason": reason},
    }


# --------------------------------------------------------------------------- #
# 分派：依 profile.mode 選公式並算 K0
# --------------------------------------------------------------------------- #
def compute_k0(dt_raw, header, profile, raw_TP=None):
    """
    對單一峰的 dt_raw 算 K0，依 profile.k0_calibration.mode 分派。

    參數
    ----
    dt_raw : float
        該峰的漂移軸取樣點索引。
    header : dict
        .mea 表頭 dict（readGAS.read_mea 回傳的第二個元素）。
    profile : dict
        load_calibration_profile() 產出，或 default_profile_unavailable()。
    raw_TP : dict | None
        raw_parameters 模式**必要**：{"T_C": float, "P_mbar": float}。
        呼叫者需明確指定 T/P 來源欄位（本模組不預設哪個 Start temp / EPC
        pressure 是「對的」，避免誤導）。傳 None 時 raw_parameters 模式會
        回傳 (None, "raw_parameters_missing_TP", reason)。

    回傳
    ----
    (k0_value, k0_mode, reason) : (float|None, str, str|None)
        k0_value : 算出的 K0（cm²/V/s），unavailable / 缺欄位時為 None
        k0_mode  : 實際使用的模式標籤，寫進 _peaks.json 做 provenance
                   {"standard_based", "raw_parameters", "unavailable",
                    "raw_parameters_missing_TP", "missing_header_fields"}
        reason   : k0_value is None 時的原因說明，用於 log 或 UI 顯示；
                   有效算出時為 None
    """
    mode = profile.get("k0_calibration", {}).get("mode", "unavailable")
    params = extract_confirmed_params(header)

    if mode == "unavailable":
        return None, "unavailable", profile["k0_calibration"].get("reason", "mode=unavailable")

    if params["sample_rate_khz"] is None or params["U_V"] is None:
        return None, "missing_header_fields", (
            f"header 缺 sample_rate_khz={params['sample_rate_khz']} 或 U_V={params['U_V']}"
        )

    if mode == "standard_based":
        ic = profile["k0_calibration"]["instrument_constant"]
        k0 = k0_from_instrument_constant(dt_raw, params["sample_rate_khz"], ic, params["U_V"])
        return k0, "standard_based", None

    if mode == "raw_parameters":
        if params["L_cm"] is None:
            return None, "missing_header_fields", "header 缺 nom Drift Tube Length"
        if raw_TP is None or raw_TP.get("T_C") is None or raw_TP.get("P_mbar") is None:
            return None, "raw_parameters_missing_TP", (
                "raw_parameters 模式需呼叫者顯式指定 T_C 與 P_mbar 來源"
                "（workflow §第二階段列為 unresolved）"
            )
        k0 = k0_from_raw_params(
            dt_raw, params["sample_rate_khz"],
            params["L_cm"], raw_TP["T_C"], raw_TP["P_mbar"], params["U_V"],
        )
        return k0, "raw_parameters", None

    # 理論上到不了這裡（load_calibration_profile 已驗過），但保守處理
    return None, "unavailable", f"unknown mode: {mode!r}"


def attach_k0(peaks, header, profile, raw_TP=None):
    """
    對峰清單每個 peak 就地加上 k0_value / k0_mode / k0_reason 三欄位。
    回傳原清單（方便鏈式呼叫）。

    設計理由：k0_mode 是 provenance 標記，必須跟 k0_value 綁在一起，下游
    比對（.iml 那條路、R004）應該同時檢查 mode，不同模式產生的 K0 值不應
    混為一談比較（workflow §第二階段結論）。
    """
    for p in peaks:
        k0, mode, reason = compute_k0(p["dt_index"], header, profile, raw_TP)
        p["k0_value"] = k0
        p["k0_mode"] = mode
        if reason is not None:
            p["k0_reason"] = reason
    return peaks


# --------------------------------------------------------------------------- #
# calibration_profile 查找（依機型/管柱/方法組合）
# --------------------------------------------------------------------------- #
def find_profile_path(profiles_dir, machine=None, column_name=None):
    """
    在 profiles_dir 底下找符合 machine + column_name 的 profile 檔案。

    workflow §第二階段的檔名慣例（範例）：
        FlavourSpec_5H4-00123_FS-SE-54-CB-1_COFFEE-40RAW.json

    這裡採簡單策略：檔名同時含 machine 與 column_name 兩個子字串（忽略大小寫）
    的第一個匹配即回傳；找不到 → None。**[待決策]** 這是暫定策略；若之後
    需要更嚴謹（match 儀器序號、method 版本），改成 profile 檔內 metadata
    欄位比對。

    回傳
    ----
    str | None
    """
    if not os.path.isdir(profiles_dir):
        return None
    m = (machine or "").lower()
    c = (column_name or "").lower()
    if not (m or c):
        return None
    for fname in sorted(os.listdir(profiles_dir)):
        if not fname.lower().endswith(".json"):
            continue
        low = fname.lower()
        m_ok = (not m) or (m in low)
        c_ok = (not c) or (c in low)
        if m_ok and c_ok:
            return os.path.join(profiles_dir, fname)
    return None

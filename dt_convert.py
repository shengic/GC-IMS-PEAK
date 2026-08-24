"""
dt_convert.py  —  GC-IMS Identify Workflow 第二階段：K0 換算
Version: 3.3 — by Albert Sheng

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

✅ **T/P 欄位對應已解決（2026-08-12，VOCal 反編譯確認）**
  先前列為 unresolved：六個 `Start temp` 不知哪個是漂移管、壓力該用 EPC 還是 ambient。
  反編譯 `reporter.jar` 的 `BufferedMEA.java:313`（**未混淆的原廠程式碼**）：

      k0Faktor = 273.0/(273.0 + startT1) * (10.0*(ambientPressure + EPC1_Start_Pressure)/1013.0)

  對應到表頭：
      T → **'Start temp 1'**（六個裡的第一個）
      P → **10 x ('Start ambient pressure' + 'Start pressure EPC IMS')**  [kPa→mbar]

  **關鍵在於壓力是「兩者相加」**，不是二選一——絕對壓力 = 環境壓力 + EPC 錶壓。
  這就是為什麼單獨看 EPC 的 1.45 kPa 太小、單獨看 ambient 的 100.4 kPa 又缺一點。
  實測 260625_141215_STD：T=45C、P=10x(100.393+1.450)=1018.4 mbar，代入後 RIP 的
  K0=1.98 cm^2/V/s，落在反應離子 H+(H2O)n 的文獻範圍 2.0-2.3。
  由 extract_raw_tp() 實作；compute_k0() 在呼叫端未給 raw_TP 時會自動取用。

⚠ **但 raw_parameters 的 K0 仍不足以做比對**（標稱值 vs 實際狀態的殘留誤差已量化）：
  拿六個已確認身分的酮對照原廠庫，raw_parameters 算出的 1/K0 系統性偏高 **+3.5%**
  （六點一致，+3.2%~+3.6%）。而相鄰同系物的 1/K0 間距僅約 0.061——偏差 0.026 是間距的
  **43%**，容許窗開到足以吸收偏差，就會同時收進隔壁碳數的化合物。
  **結論：K0 比對必須走 standard_based。** 校準品已在手（STD 的六個酮，身分由經理
  對照表確認、K0 由原廠庫提供），見 calibration.derive_k0_instrument_constant()：
  六點解出的 instrument_constant CV=0.13%，校準後殘差 <0.25%（間距的 4% 以內）。

✅ **已解決（2026-08-12）：兩個模式一律回傳 K0 本身**
  舊版的 k0_from_raw_params() 回傳 1/K0 而 k0_from_instrument_constant() 回傳 K0，
  兩者卻同寫進 peak["k0_value"]——同一台機器、同一顆峰，兩個模式的數字互為倒數。
  外部佐證：gc-ims-tools 0.1.10（Food Chemistry 2022）的 Spectrum.calc_reduced_mobility()
  用
      K0 = L² · T₀ · p / (dt · Ud · T · p₀)      # 預設 L = 5.3 cm
  引 Ahrens & Zimmermann, Anal Bioanal Chem 413, 1009–1016 (2021)。展開即
  (T₀/T)·(p/p₀)·L²/(t·U)，與本模組同形，而**它回傳 K0，不是倒數**。（其預設 L=5.3 cm
  也與本專案自表頭讀出的值一致，順帶驗證了 'nom Drift Tube Length' 的解析。）
  故本模組統一為「一律回傳 K0」，取倒數的動作移到比對端**顯式**處理：
  match.match_k0() 依 DtMode 判斷庫值是 K0 還是 1/K0，換算到 K0 空間再比。
  舊行為的來源是 GC-IMS_Identify_Workflow.md §第二階段設計稿裡的 `return 1.0 / K0`，
  該處已加更正註記。

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
        與 k0_from_raw_params() 同單位、同慣例（皆為 K0 本身）。
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
        與 k0_from_instrument_constant() 同單位、同慣例（皆為 K0 本身）。
        2026-08-12 前此處回傳 1.0/K0，造成兩個模式互為倒數；見檔頭說明。
        庫值若為 1/K0（.iml 的 DtMode），由 match.match_k0() 換算，不在此處取倒數。
    """
    t_d_s = (dt_raw / sample_rate_khz) / 1000.0
    ah = (273.0 / (273.0 + T_C)) * (P_mbar / 1013.0)
    return ah * L_cm ** 2 / (t_d_s * U)


# --------------------------------------------------------------------------- #
# 表頭解析（confirmed 欄位）
# --------------------------------------------------------------------------- #
def _hnum(header, key, default=None):
    """從表頭字串取第一個數字（同 readGAS.hnum；此處本地複製，避免循環 import）。"""
    if key not in header:
        return default
    m = re.search(r"-?\d+\.?\d*", header[key])
    return float(m.group()) if m else default


def extract_raw_tp(header):
    """從表頭抽出 raw_parameters 模式需要的 T / P。

    欄位對應由 VOCal `reporter.jar` 的 `BufferedMEA.java:313` 反編譯確認（見檔頭）：
        T_C    = 'Start temp 1'
        P_mbar = 10 × ('Start ambient pressure' + 'Start pressure EPC IMS')

    壓力是**相加**（絕對 = 環境 + EPC 錶壓），不是二選一——這是先前卡住的關鍵。
    VOCal 對壓力欄位接受多個別名，此處一併嘗試，取第一個找得到的。

    回傳
    ----
    (raw_tp, detail) : (dict|None, dict)
        raw_tp — {"T_C": float, "P_mbar": float}；任一項缺失則為 None
        detail — 實際採用的欄位名與原始值，供 provenance
    """
    t_c = _hnum(header, "Start temp 1")
    amb = next((_hnum(header, k) for k in
                ("Start ambient pressure", "EPC ambient pressure")
                if _hnum(header, k) is not None), None)
    epc = next((_hnum(header, k) for k in
                ("Start pressure EPC IMS", "EPC IMS pressure", "EPC1 pressure")
                if _hnum(header, k) is not None), None)
    detail = {"T_field": "Start temp 1", "T_C": t_c,
              "ambient_kPa": amb, "epc_kPa": epc,
              "formula": "P_mbar = 10 x (ambient + EPC)",
              "source": "VOCal BufferedMEA.java:313 (decompiled 2026-08-12)"}
    if t_c is None or amb is None or epc is None:
        detail["missing"] = [n for n, v in
                             (("Start temp 1", t_c), ("ambient pressure", amb),
                              ("EPC pressure", epc)) if v is None]
        return None, detail
    p_mbar = 10.0 * (amb + epc)
    detail["P_mbar"] = p_mbar
    return {"T_C": t_c, "P_mbar": p_mbar}, detail


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
            # 2026-08-12 起改為自動取用：欄位對應已由 VOCal 反編譯確認，不再是猜測，
            # 所以「要求呼叫者顯式指定」這道防呆已無意義（見檔頭）。表頭真的缺欄位
            # 才失敗——那時仍明確標記，不靜默造假。
            raw_TP, tp_detail = extract_raw_tp(header)
            if raw_TP is None:
                return None, "raw_parameters_missing_TP", (
                    f"表頭缺少 T/P 欄位：{tp_detail.get('missing')}"
                    f"（需 'Start temp 1' + ambient/EPC 壓力，見 extract_raw_tp）"
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

"""
reference_series.py  —  第四階段 RT→RI 校正的「參照系列定義」（可插拔）
Version: 3.0 — by Albert Sheng

calibration.py 的核心邏輯**只依賴這裡的介面**，不假設任何特定化合物系列——
換系列（烷烴 → 甲基酮 → 自訂對照表）只要換 series_key 或填資料，不動校正演算法。

**兩層獨立假設，各自標記（本輪對話討論，非反編譯驗證）**
  第一層：「這支 STD 是正構烷烴系列」                 → assumed=True
  第二層：「起始碳數是 C6」（光靠矩陣算不出起點）    → assumed_start_carbon
  兩者出錯機率獨立，所以分開標記；任何用 assumed=True 系列算出的 RI，其
  assumed_unverified 旗標必須一路傳進 _peaks.json，下游不得當成已驗證事實。

RI=100·n 只在等差同系物下成立，且還需要知道「每個峰是第幾個碳（n）」——
依 RT 升冪只能定相對順序，起始碳數仍是未知數。故 n_alkane 為暫定假設，
供程式流程先打通；拿到化合物證書/對照表後改用 custom 填入實測 RI 即可。

**[draft.19] 本專案這支 STD 已確認為 C4–C9 甲基酮同系物（2-butanone→2-nonanone），
非烷烴** —— 所以 n_alkane 對本資料是「錯的系列」（甲基酮 RI 非整百，套 100·n 會系統性
錯誤）。正確系列是 methyl_ketone。n_alkane 保留作為「等差同系物」的通用機制示範與測試
用途，不代表本 STD 的真實系列。

**[methyl_ketone_RI_provenance.md] methyl_ketone 的六點 RI 已填入（借用值）**：
[589.4, 688.6, 784.2, 892.2, 996.5, 1095.6]，借自鱸魚專案 .gasprj、與外部估計交叉比對，
`confidence="borrowed_cross_referenced"`、`assumed=True`（assumed_unverified 維持 True）。
只借 RI(Y 軸)，Rt(X 軸) 用本批 STD 實測值。因此 methyl_ketone 現在可產生**絕對 RI**，
但下游必須帶著 assumed_unverified 標記，升級成 verified 的條件見 provenance 文件。
"""


REFERENCE_SERIES = {
    "n_alkane": {
        "assumed": True,                # [待決策，本輪假設，未經化合物身分驗證]
        "kind": "arithmetic",           # RI 為等差級數
        "ri_formula": lambda carbon_n: 100.0 * carbon_n,
        "assumed_start_carbon": 6,      # [待決策，第二層假設，獨立於第一層]
        "note": ("假設 STD 為正構烷烴系列，起始碳數未知暫定 C6；"
                 "兩項皆未經化合物身分驗證，僅供程式流程先行打通"),
    },
    "methyl_ketone": {
        # 借用值、非本批次自建、未逐一查證 → assumed_unverified 維持 True
        "assumed": True,
        "kind": "table",
        "members": ["2-butanone", "2-pentanone", "2-hexanone",
                    "2-heptanone", "2-octanone", "2-nonanone"],
        "carbon_numbers": [4, 5, 6, 7, 8, 9],
        # 只借 RI（Y 軸）；Rt（X 軸）用本批 STD 實測的六個峰，兩邊不可混用
        "ri_values": [589.4, 688.6, 784.2, 892.2, 996.5, 1095.6],
        "confidence": "borrowed_cross_referenced",   # 非 "verified"、非本批 "self_calibrated"
        "provenance": ("borrowed_from Auto_Project_Backup.gasprj（海洋大學鱸魚專案），"
                       "非本批次自建；與外部非極性管柱估計值交叉比對誤差<11 RI 單位"
                       "（來源未逐篇查證）；ML-TEST20260629.gasprj 的 909.6/1183.7 因校正"
                       "曲線出現負值外插判不可信、不採用"),
        "note": ("[methyl_ketone_RI_provenance.md] 借用鱸魚專案 .gasprj 六點 RI；只借"
                 " RI(Y 軸)，Rt(X 軸) 用本批 STD 實測值。assumed_unverified 維持 True，"
                 "升級成 verified 的條件見該 provenance 文件"),
    },
    "custom": {
        "assumed": False,               # 使用者直接給每個錨點已知 RI，不靠同系規則
        "kind": "table",
        "ri_values": None,
        "note": "直接指定各錨點 RI 值，繞過任何系列假設（最可信）",
    },
}


def list_series():
    """回傳目前註冊的系列名稱清單。"""
    return sorted(REFERENCE_SERIES)


def series_is_assumed(series_key):
    """該系列是否為未經驗證的假設（provenance 用）。"""
    return bool(REFERENCE_SERIES[series_key].get("assumed", False))


def series_confidence(series_key):
    """回傳系列的信心等級標籤（如 methyl_ketone 的 'borrowed_cross_referenced'）；
    未定義則 None。用於把 provenance 一路帶進校正表與 _peaks.json。"""
    return REFERENCE_SERIES.get(series_key, {}).get("confidence")


def assign_ri(n_anchors, series_key="n_alkane", start_carbon=None, ri_values=None):
    """
    依「保留時間由小到大」的錨點數，回傳對齊的 RI 清單。

    參數
    ----
    n_anchors : int         乾淨錨點數（依 RT 升冪）
    series_key : str         REFERENCE_SERIES 的 key
    start_carbon : int|None  僅 n_alkane 用；覆寫 assumed_start_carbon
    ri_values : list|None     僅 table 類（methyl_ketone/custom）用；直接提供各
                              錨點 RI；覆寫定義檔內的 ri_values

    回傳
    ----
    list[float] : 長度 n_anchors 的 RI，與升冪錨點一一對應

    Raises
    ------
    KeyError    series_key 未註冊
    ValueError  table 類無可用 ri_values，或長度與錨點數不符
    """
    series = REFERENCE_SERIES[series_key]
    kind = series.get("kind")

    if kind == "arithmetic":
        start = start_carbon if start_carbon is not None else series["assumed_start_carbon"]
        formula = series["ri_formula"]
        return [formula(start + i) for i in range(n_anchors)]

    # table 類：優先用呼叫端提供的 ri_values，其次定義檔內的
    vals = ri_values if ri_values is not None else series.get("ri_values")
    if vals is None:
        raise ValueError(f"'{series_key}' 尚無可用 ri_values，無法指派 RI")
    if len(vals) != n_anchors:
        raise ValueError(
            f"'{series_key}' 的 ri_values 長度 {len(vals)} 與錨點數 {n_anchors} 不符")
    return list(vals)

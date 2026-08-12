"""
reference_series.py  —  第四階段 RT→RI 校正的「參照系列定義」（可插拔）
Version: 3.1 — by Albert Sheng

calibration.py 的核心邏輯**只依賴這裡的介面**，不假設任何特定化合物系列——
換系列（烷烴 → 酮 → 自訂對照表）只要換 series_key 或填資料，不動校正演算法。

**兩層獨立假設，各自標記（本輪對話討論，非反編譯驗證）**
  第一層：「這支 STD 是正構烷烴系列」                 → assumed=True
  第二層：「起始碳數是 C6」（光靠矩陣算不出起點）    → assumed_start_carbon
  兩者出錯機率獨立，所以分開標記；任何用 assumed=True 系列算出的 RI，其
  assumed_unverified 旗標必須一路傳進 _peaks.json，下游不得當成已驗證事實。

RI=100·n 只在等差同系物下成立，且還需要知道「每個峰是第幾個碳（n）」——
依 RT 升冪只能定相對順序，起始碳數仍是未知數。故 n_alkane 為暫定假設，
供程式流程先打通；拿到化合物證書/對照表後改用 custom 填入實測 RI 即可。

**[draft.19 / draft.23 更正] 本專案這支 STD 為 C4–C9 的「酮」，非烷烴** —— 所以
n_alkane 對本資料是「錯的系列」（酮的 RI 非整百，套 100·n 會系統性錯誤）。正確系列是
`ketone`。n_alkane 保留作為「等差同系物」的通用機制示範與測試用途，不代表本 STD 的
真實系列。

**[draft.23 → draft.24 的兩次修正，請一起讀]**
draft.23：使用者指出材料是「酮」不是「甲基酮」，系列由 `methyl_ketone` 改名為 `ketone`，
成員標籤改為不宣稱結構的 `C4 ketone`…`C9 ketone`。
draft.24：經理提供 `kintonemixed-C4-C9.xlsx`，六列各帶 CAS，**確認組成就是 2-alkanone
系列**（2-butanone…2-nonanone）。使用者裁決以該檔為準，故成員標籤改回具體化合物名並
補上 CAS/Formula/MW。系列 key 維持 `ketone`（對應混標品名）。

**[draft.24] RI 數值來源已由「借用」改為「本批對照表」**：
現值 [916.8372, 987.12244, 1087.4615, 1181.3636, 1293.7333, 1392.9] 來自上述 xlsx，
取代先前借自鱸魚專案 .gasprj 的 [589.4, 688.6, 784.2, 892.2, 996.5, 1095.6]。

⚠ **仍未解決（assumed_unverified 因此維持 True）：管柱極性存疑**。新舊兩組差值為
+289~+327（六點高度一致，均值 +302）；2-butanone 的 916.84 幾乎正中 NIST WebBook
的**極性管柱（DB-Wax）**值 917–950，而本批管柱是 FS-SE-54-CB-1 / POLARITY: np
（**非極性**，該條件下 2-butanone 應在 589 那個量級）。若對照表確為極性管柱數值，
套用到本批會讓所有 RI 系統性偏高約 300。使用者已知悉並裁決採用本組數值。

**[draft.24] `dt_values` 已成為錨點指派的依據（不再只是參考資料）**。
`calibration.match_anchors_by_dt()` 拿本表的 `dt_values` 去 STD 的全部偵測峰裡配對
（容差 ±0.01），碳數指派因此有外部依據，不再靠間距均勻度推論：

    C4 1.23938 → RT 389.7    C5 1.35521 → RT 467.0    C6 1.49035 → RT 609.5
    C7 1.61390 → RT 813.4    C8 1.73359 → RT 1107.2   C9 1.85714 → RT 1523.4

六個全中、RT 與 DT_rel 皆嚴格遞增、平均 |Δ| = 0.0027。

**為什麼必須換掉舊的 `select_homolog_ladder()`**：它挑的是
[329.6, 389.7, 467.0, 609.5, 813.4, 1107.2]——多納入不屬於此序列的 329.6 s（DT_rel 1.104，
全圖最強峰），並漏掉 RT 1523.4 s 的 C9，整條碳數指派因此錯位一格。而且它的**兩個評分
準則都指向錯誤答案**：錯誤組合的間距標準差 0.0034 反而優於正確組合的 0.0046，且含最強峰
故突出度總和更大。間距均勻度分不出「真正的同系物階梯」與「恰好等距的混合物」。

⚠ 因此 **`dt_values` 現在是承重資料**：改動它會直接改變錨點指派，進而改變每個峰的 RI。
本表與原檔的逐格一致性由 `test_ketone_std_table_is_a_faithful_transcription()` 把關。
完整分析見 `ketone_RI_provenance.md`。
"""


# --------------------------------------------------------------------------- #
# 本批 STD 標準品對照表 —— 逐列硬編碼自 kintonemixed-C4-C9.xlsx（經理提供）
#
# **依原檔列序原樣抄錄**（該檔第 1 列是 C9，碳數遞減），並保留 count 欄，方便日後
# 拿原檔逐列核對。下游要的「依碳數遞增」順序由下方 _col() 現算，不另外手打一份平行
# 陣列——六組平行陣列各自手動維護，正是最容易悄悄錯位又沒人發現的地方。
#
# 硬編碼而不是讀檔的理由：這是六列固定的參照資料，不是會變動的輸入。讓校準相依於
# 一個必須存在於特定路徑的 .xlsx（還要多一個 openpyxl 相依）只會增加壞掉的方式；
# 原檔仍應留在專案內當來源憑證，見 ketone_RI_provenance.md。
#
# compound 欄原檔大小寫不一致（2-nonanone / 2-Octanone / 2-Butanone…），此處**原樣**
# 保留以利核對；對外的 members 統一轉小寫（標準寫法）。
# --------------------------------------------------------------------------- #
KETONE_STD_TABLE = [
    # count | Compound     | CAS#     | Formula | MW    | RI        | Rt[sec]  | Dt[a.u.] | Comment
    {"count": 1, "compound": "2-nonanone",  "cas": "C821556", "formula": "C9H18O",
     "mw": 142.2, "carbon": 9, "ri": 1392.9,     "rt_s": 1530.783, "dt": 1.85714, "comment": "Dimer"},
    {"count": 2, "compound": "2-Octanone",  "cas": "C111137", "formula": "C8H16O",
     "mw": 128.2, "carbon": 8, "ri": 1293.7333, "rt_s": 1108.319, "dt": 1.73359, "comment": "Dimer"},
    {"count": 3, "compound": "2-heptanone", "cas": "C110430", "formula": "C7H14O",
     "mw": 114.2, "carbon": 7, "ri": 1181.3636, "rt_s": 810.582,  "dt": 1.6139,  "comment": "Dimer"},
    {"count": 4, "compound": "2-Hexanone",  "cas": "C591786", "formula": "C6H12O",
     "mw": 100.2, "carbon": 6, "ri": 1087.4615, "rt_s": 609.409,  "dt": 1.49035, "comment": "Dimer"},
    {"count": 5, "compound": "2-Pentanone", "cas": "C107879", "formula": "C5H10O",
     "mw": 86.1,  "carbon": 5, "ri": 987.12244, "rt_s": 462.552,  "dt": 1.35521, "comment": "Dimer"},
    {"count": 6, "compound": "2-Butanone",  "cas": "C78933",  "formula": "C4H8O",
     "mw": 72.1,  "carbon": 4, "ri": 916.8372,  "rt_s": 388.118,  "dt": 1.23938, "comment": "Dimer"},
]

# --------------------------------------------------------------------------- #
# 六個酮的參考 K0 —— 硬編碼自 G.A.S. 原廠基準庫 GAS BASE 3H_IMS K0.iml
#
# **與上方 KETONE_STD_TABLE 是不同來源**：那張表來自經理的 xlsx（本批標準品），
# 這組來自原廠隨附的 IMS 基準庫。分開存放，provenance 才不會糊在一起。
#
# 庫中每個化合物有兩列，`DtMode == "1/K0"`，存的是**逆約化遷移率**：
#   較小者 = 單體（monomer，遷移快、K0 大、1/K0 小）
#   較大者 = 二聚體（dimer）
# 本批 STD 偵測到的六個錨點是**二聚體**（經理對照表 Comment 欄皆為 Dimer），
# 故 K0 校準用 dimer 那一欄；monomer 一併存下供日後 M/D 配對使用。
#
# 用途：`calibration.derive_k0_instrument_constant()` 拿這組已知 K0 反推本台儀器的
# instrument_constant，讓 dt_convert 從 raw_parameters（標稱值，實測偏 +3.5%）升級到
# standard_based。實測六點解出的 IC 一致性 CV=0.13%、校準後殘差 <0.25%。
# --------------------------------------------------------------------------- #
KETONE_INV_K0_REFERENCE = {
    # compound: (monomer 1/K0, dimer 1/K0)
    "2-butanone":  (0.51032, 0.60131),
    "2-pentanone": (0.54034, 0.66138),
    "2-hexanone":  (0.57253, 0.72503),
    "2-heptanone": (0.60778, 0.78697),
    "2-octanone":  (0.64262, 0.84814),
    "2-nonanone":  (0.67844, 0.90713),
}
KETONE_K0_REFERENCE_SOURCE = "GAS BASE 3H_IMS K0.iml (G.A.S. official base library), DtMode=1/K0"


# 依碳數遞增（= 依 RT 遞增，同系物）——所有對外欄位都由這裡導出，順序保證一致
_KETONE_ASC = sorted(KETONE_STD_TABLE, key=lambda r: r["carbon"])


def _col(field):
    """取 KETONE_STD_TABLE 的某一欄，依碳數遞增排序。"""
    return [r[field] for r in _KETONE_ASC]


REFERENCE_SERIES = {
    "n_alkane": {
        "assumed": True,                # [待決策，本輪假設，未經化合物身分驗證]
        "kind": "arithmetic",           # RI 為等差級數
        "ri_formula": lambda carbon_n: 100.0 * carbon_n,
        "assumed_start_carbon": 6,      # [待決策，第二層假設，獨立於第一層]
        "note": ("假設 STD 為正構烷烴系列，起始碳數未知暫定 C6；"
                 "兩項皆未經化合物身分驗證，僅供程式流程先行打通"),
    },
    "ketone": {
        # [draft.24] 數值來源改為經理提供的 kintonemixed-C4-C9.xlsx（本批標準品自己的
        # 對照表，非借用）。身分因此**確認**：六個成員逐一附 CAS，核對無誤。
        # assumed 仍為 True，但理由已完全不同——不再是「不知道是什麼化合物」，
        # 而是「不確定這組 RI 是在哪種極性的管柱上量的」，見 confidence 與 provenance。
        "assumed": True,
        "kind": "table",
        # 以下全部由 KETONE_STD_TABLE 導出，不手打第二份——改資料只改上面那張表
        "source_table": KETONE_STD_TABLE,
        "source_file": "kintonemixed-C4-C9.xlsx",
        "members": [c.lower() for c in _col("compound")],
        "cas_numbers": _col("cas"),
        "formulas": _col("formula"),
        "molecular_weights": _col("mw"),
        "carbon_numbers": _col("carbon"),
        # 取代先前借自鱸魚專案 .gasprj 的 [589.4, 688.6, 784.2, 892.2, 996.5, 1095.6]
        # （新舊差值高度一致的 +302，見下方 provenance）
        "ri_values": _col("ri"),
        # 漂移值（相對 RIP，與峰的 drift_relative 同單位）與該次量測的保留時間。
        # ⚠ **dt_values 是承重資料**：calibration.match_anchors_by_dt() 用它決定
        # 哪顆峰是哪個化合物，改動它會直接改變每個峰的 RI。
        "dt_values": _col("dt"),
        "dt_ion_form": "Dimer",      # 對照表 Comment 欄；單體漂移值更小，表中未列
        "rt_values_source_run": _col("rt_s"),
        # 二聚體的參考 1/K0（原廠基準庫），依碳數遞增，與 dt_values 一一對應。
        # 供 calibration.derive_k0_instrument_constant() 反推 instrument_constant。
        "inv_k0_values": [KETONE_INV_K0_REFERENCE[c.lower()][1] for c in _col("compound")],
        "inv_k0_source": KETONE_K0_REFERENCE_SOURCE,
        "confidence": "supplier_table_column_polarity_unverified",
        # 給 UI 直接顯示的一句話。**訊息跟著資料走，不要寫死在 main.py**——
        # 2026-08-12 就發生過一次：assumed 旗標的意義從「借用值」改成「管柱極性存疑」，
        # 但 UI 仍寫死 "assumed/borrowed"，於是顯示了已經不成立的理由。
        "caveat_short": "RI 可能為極性管柱尺標，本批為非極性 SE-54，恐整體偏高約 300",
        "provenance": (
            "來源 kintonemixed-C4-C9.xlsx（經理提供，本批標準品對照表），六列各含 "
            "CAS/Formula/MW/RI/Rt[sec]/Dt[a.u.]，Comment 皆為 Dimer。CAS 逐一核對"
            "正確（78-93-3 / 107-87-9 / 591-78-6 / 110-43-0 / 111-13-7 / 821-55-6），"
            "化合物身分至此確認為 2-alkanone 系列。"
            "⚠ **未解決：管柱極性存疑**——這組 RI 比先前借自 Auto_Project_Backup.gasprj"
            "（非極性）的六點系統性高約 +302 RI 單位（289–327，跨六點高度一致），"
            "而 2-butanone 的 916.84 幾乎正中 NIST WebBook 的**極性管柱（DB-Wax）**"
            "值 917–950；本批管柱表頭為 FS-SE-54-CB-1 / POLARITY: np（非極性）。"
            "若該表確為極性管柱數值，套用在本批非極性管柱上會讓所有 RI 系統性偏高約 300。"
            "使用者已知悉此疑慮並裁決採用本組數值。"
        ),
        "note": ("[ketone_RI_provenance.md] 身分已由經理對照表確認（CAS 核對無誤）；"
                 "RI 改用該表數值。assumed_unverified 維持 True，指向的是管柱極性疑慮"
                 "（非化合物身分）。錨點指派已改用本表 dt_values 配對，不再靠間距啟發式"
                 "——見 provenance 文件第 3 節"),
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
    """回傳系列的信心等級標籤（如 ketone 的 'borrowed_cross_referenced'）；
    未定義則 None。用於把 provenance 一路帶進校正表與 _peaks.json。"""
    return REFERENCE_SERIES.get(series_key, {}).get("confidence")


def series_caveat(series_key):
    """回傳該系列給使用者看的一句話警語（未定義則 None）。

    與 series_confidence() 分工：confidence 是給程式判讀的標籤，caveat 是給人看的
    白話。UI 顯示走這一個，才不會在 provenance 改變時留下過期的措辭。
    """
    return REFERENCE_SERIES.get(series_key, {}).get("caveat_short")


def assign_ri(n_anchors, series_key="n_alkane", start_carbon=None, ri_values=None):
    """
    依「保留時間由小到大」的錨點數，回傳對齊的 RI 清單。

    參數
    ----
    n_anchors : int         乾淨錨點數（依 RT 升冪）
    series_key : str         REFERENCE_SERIES 的 key
    start_carbon : int|None  僅 n_alkane 用；覆寫 assumed_start_carbon
    ri_values : list|None     僅 table 類（ketone/custom）用；直接提供各
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

"""
library.py  —  GC-IMS Identify Workflow 第三階段：.ril / .iml 資料庫讀取
Version: 3.3 — by Albert Sheng

變更記錄：
  3.3  — 新增 infer_polarity_from_column_name()：舊版 `GC Column` 表頭沒有
         `POLARITY:` 欄位（實測 GAS/藝妓咖啡 全批），極性因此為 None，
         select_ril_paths() 的極性退路整條不啟動、回傳 0 筆 .ril 而毫無跡象。
         推測只在表頭沒寫時補位，顯式值一律優先，來源記在 polarity_source。

依 GC-IMS_Identify_Workflow.md §第三階段（draft.13 定案）：
  - .ril（21 欄，Tab-separated，CAS/NAME/Formula/RI/...）
  - .iml（16 欄，Tab-separated，Name/CAS/Formula/MW/RI/Rt[sec]/Dt[a.u.]/...）
  - 每一筆 row 需附加 source_file（僅檔名，不含路徑），供第六階段輸出與第十階段
    面板做候選溯源
  - 選檔策略：優先用 .mea 表頭的 GC Column 型號名稱做精確/模糊匹配，退路依極性
    載入該極性合併檔（AVERAGE LOW POLAR / DB WAX 等）

真實資料摸底：
  - 兩行 // 註解為表頭，跳過
  - 空值兩種：字串 "null"（GAS 3H_IMS.iml、AVERAGE LOW POLAR.ril）與 ""（NIST 系列）
  - AVERAGE LOW POLAR.ril 為 22 欄（多一欄尾巴 null），其餘測試檔皆為 21/16 欄

已知 caveat（不修）：
  - .iml 舊格式（如 GAS 3H_IMS.iml）把 device tag 與 timestamp 拆成兩欄，
    而 workflow §第三階段的 16-col schema 是依 K0 檔（GAS BASE 3H_IMS K0.iml）
    佈局。這造成舊檔的後半段欄位（p/T/L/U/RIP/DtMode/EditEvent）會偏移一欄。
    前 7 欄（Name/CAS/Formula/MW/RI/Rt/Dt）在兩種格式都對齊，比對用的都是
    這幾欄，所以第五階段匹配不會出錯；只有在存取 DtMode/RIP 這類 metadata
    欄位時要留意可能失真——如需嚴格對齊，需先偵測檔案版本再套用不同 schema，
    目前設計為單一 schema + 已知 caveat。

本模組刻意保持：
  - 只讀不寫、只解析不比對（比對留給第五階段 match.py）
  - list[dict] 為主資料型態，每 row 一 dict，鍵名對應 workflow 定義的欄位名
  - 數值欄位（RI/MW/Rt/Dt/p/T/L/U/RIP）盡量解析成 float；解析失敗 → None，
    不 raise，把原始字串放到 `<field>_raw` 供除錯，避免一筆髒資料拖垮整檔載入

依賴：僅 Python stdlib
"""

import os
import re


# --------------------------------------------------------------------------- #
# 資料夾解析：實現優先鏈
#   1. 明確傳入路徑
#   2. GCIMS_LIBRARY_DIR 環境變數
#   3. <PROJECT_ROOT>/library_data/          ← 首選預設
#   4. <PROJECT_ROOT>/VOCal Release .../_portable/data/  ← 向後相容 fallback
#   5. None（呼叫者需啟用檔案總管讓使用者選）
#
# 專案根目錄推導：本模組所在資料夾（library.py 位於 <PROJECT_ROOT>/）
# --------------------------------------------------------------------------- #
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LIBRARY_DIR_NAME = "library_data"
LEGACY_VOCAL_SUBPATH = os.path.join(
    "VOCal Release 0.4.31.412", "_portable", "data"
)


def _dir_has_library_files(path):
    """判定一個資料夾是否已放入 .ril 或 .iml。用於挑選候選時的存在性檢查。"""
    if not path or not os.path.isdir(path):
        return False
    try:
        for f in os.listdir(path):
            fl = f.lower()
            if fl.endswith(".ril") or fl.endswith(".iml"):
                return True
    except OSError:
        return False
    return False


def resolve_data_dir(explicit=None):
    """
    依優先鏈找出 .ril / .iml 資料夾實際路徑。找不到任何可用資料夾 → 回 None，
    呼叫者（通常是 UI）應據此觸發 filedialog.askdirectory() 讓使用者手動選。

    參數
    ----
    explicit : str | None
        使用者/UI 明確指定的路徑。給就直接回，不做存在性檢查（存在與否交由
        呼叫者判斷；這裡尊重呼叫者的顯式意圖）。

    回傳
    ----
    str | None
    """
    if explicit:
        return explicit

    env = os.environ.get("GCIMS_LIBRARY_DIR")
    if env and _dir_has_library_files(env):
        return env

    default = os.path.join(PROJECT_ROOT, DEFAULT_LIBRARY_DIR_NAME)
    if _dir_has_library_files(default):
        return default

    legacy = os.path.join(PROJECT_ROOT, LEGACY_VOCAL_SUBPATH)
    if _dir_has_library_files(legacy):
        return legacy

    return None


RIL_COLUMNS = [
    "CAS", "NAME", "Formula", "RI",
    "ColumnType", "ColumnPolarity", "ColumnName", "ColumnLength",
    "CarrierGas", "Substrate", "ColumnDiameter", "PhaseThickness",
    "DataType", "ProgramType",
    "StartT", "EndT", "HeatRate", "StartTime", "EndTime", "Programm",
    "LiteratureIndex",
]

IML_COLUMNS = [
    "Name", "CAS", "Formula", "MW", "RI", "Rt[sec]", "Dt[a.u.]",
    "Command", "DeviceTimestamp",
    "p(IMS)", "T(IMS)", "L(IMS)", "U(IMS)",
    "RIP[a.u.]", "DtMode", "EditEvent",
]

# 這些欄位嘗試解析成 float；解析失敗 → None + <field>_raw 保留原字串
RIL_NUMERIC = {"RI"}
IML_NUMERIC = {"MW", "RI", "Rt[sec]", "Dt[a.u.]",
               "p(IMS)", "T(IMS)", "L(IMS)", "U(IMS)", "RIP[a.u.]"}

# 空值哨兵：讀進來的字串若在此集合，視為 None
_NULL_SENTINELS = {"", "null", "NULL", "None"}


def _clean(value):
    """把 raw 字串正規化：去除前後空白 + 掉引號；空/null 哨兵 → None。"""
    if value is None:
        return None
    s = value.strip().strip('"')
    if s in _NULL_SENTINELS:
        return None
    return s


def _try_float(value):
    """None → None；不能轉 → None。回傳 (parsed, raw_string_or_None)。"""
    if value is None:
        return None, None
    try:
        return float(value), None
    except (ValueError, TypeError):
        return None, value


def _parse_row(raw_line, columns, numeric_fields, source_file):
    """
    把一行 tab-separated 字串解析成 dict。
    - 欄位對齊 columns；多的丟棄、少的補 None
    - numeric_fields 內的欄位嘗試 float，失敗保留原字串於 <field>_raw
    - 附加 source_file
    - 回傳 dict；若整列全空/全 null，回傳 None（呼叫端跳過）
    """
    fields = raw_line.split("\t")
    row = {}
    any_value = False
    for i, name in enumerate(columns):
        raw = fields[i] if i < len(fields) else None
        cleaned = _clean(raw)
        if cleaned is not None:
            any_value = True
        if name in numeric_fields:
            parsed, kept_raw = _try_float(cleaned)
            row[name] = parsed
            if kept_raw is not None:
                row[f"{name}_raw"] = kept_raw
        else:
            row[name] = cleaned
    if not any_value:
        return None
    row["source_file"] = source_file
    return row


def _load_tab_file(path, columns, numeric_fields):
    """
    共用讀檔骨幹：utf-8、跳過 // 註解、逐行 parse。
    回傳 list[dict]，每 row 附加 source_file = os.path.basename(path)。
    """
    source_file = os.path.basename(path)
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line or line.startswith("//"):
                continue
            row = _parse_row(line, columns, numeric_fields, source_file)
            if row is not None:
                rows.append(row)
    return rows


def load_ril(path):
    """讀取一個 .ril（Retention Index 資料庫），回傳 list[dict]。

    每 row 含 21 個 workflow 定義欄位 + source_file。RI 欄位嘗試 float。
    """
    return _load_tab_file(path, RIL_COLUMNS, RIL_NUMERIC)


def load_iml(path):
    """讀取一個 .iml（IMS Drift Time 資料庫），回傳 list[dict]。

    每 row 含 16 個 workflow 定義欄位 + source_file。MW/RI/Rt/Dt/p/T/L/U/RIP
    嘗試 float。

    注意：`DtMode` 欄位原始檔案內容常見為 "1/K0"（含斜線與尾隨空白），呼叫端
    需自行 strip 後與第二階段的 k0_mode 做對應，不可直接字串等值比較。
    """
    return _load_tab_file(path, IML_COLUMNS, IML_NUMERIC)


# --------------------------------------------------------------------------- #
# .mea 表頭 GC Column 欄位解析（workflow §第三階段第 3 點）
# --------------------------------------------------------------------------- #
# 固定相名稱 → 極性。**只在表頭沒有 `POLARITY:` 欄位時**當退路用。
#
# 為什麼需要：`.mea` 的 `GC Column` 有至少兩種排版。新版帶顯式極性
#     'FS-SE-54-CB-1, L: 30.00m, ID: 0.53mm, FT: 0.50µm, POLARITY: np'
# 舊版（實測 GAS/藝妓咖啡 全批 14 檔）沒有：
#     'FS-SE54-CB-0.5, 15m x 0,32ID'
# 後者讓 polarity=None，於是 select_ril_paths() 的極性退路整條不啟動，回傳
# strategy='none' 與 **0 筆 .ril**——RI 維度連庫都沒有，卻沒有任何訊息。
#
# 對應關係不是猜的：SE-54 在**同一台儀器自己的表頭**裡就標成 `POLARITY: np`
# （見上面的新版字串），所以這裡把 SE-54 判為 np 是照著儀器自己的講法，不是本
# 專案的化學判斷。其餘項目沿用 select_ril_paths() 既有的關鍵字慣例。
#
# 比對在**去掉分隔符後**的字串上做（'FS-SE54-CB-0.5' → 'fsse54cb05'），因為同一
# 種固定相在不同檔案裡寫成 SE-54 / SE54 / SE 54 都有。
_PHASE_POLARITY = (
    # (正規化後的關鍵字, 極性)  —— 由具體到一般，第一個命中者為準
    ("wax", "p"), ("carbowax", "p"), ("peg", "p"), ("ffap", "p"), ("innowax", "p"),
    ("se54", "np"), ("se30", "np"), ("db5", "np"), ("hp5", "np"), ("rtx5", "np"),
    ("ov1", "np"), ("ov101", "np"), ("zb5", "np"), ("bpx5", "np"), ("cpsil", "np"),
    ("db1", "np"), ("silox", "np"),
)


def _normalize_phase(text):
    """去掉分隔符與空白並轉小寫，讓 SE-54 / SE54 / SE 54 都能比對到同一個 key。"""
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def infer_polarity_from_column_name(column_name):
    """由管柱型號名稱推測極性，回傳 'np' / 'p' / None（認不出來就 None）。

    **只在表頭沒有顯式 `POLARITY:` 時才該呼叫**——顯式值一律優先，這裡是退路。
    認不出來就回 None，不亂猜：寧可讓上游看到「沒有極性」，也不要憑一個猜出來的
    極性去載入錯誤固定相的 RI 庫（RI 是管柱相依量，載錯庫等於比對錯的尺標）。
    """
    norm = _normalize_phase(column_name)
    if not norm:
        return None
    for key, pol in _PHASE_POLARITY:
        if key in norm:
            return pol
    return None


def parse_gc_column_header(gc_column_value):
    """
    解析 .mea 表頭 'GC Column' 欄位，取出五個子資訊。

    輸入格式（實測範例，兩種排版都要吃）:
        'FS-SE-54-CB-1, L: 30.00m, ID: 0.53mm, FT: 0.50µm, POLARITY: np'
        'FS-SE54-CB-0.5, 15m x 0,32ID'                     ← 無 POLARITY 欄位

    回傳
    ----
    dict，包含以下鍵（找不到即為 None）：
        column_name    型號名稱（第一個逗號前的整段）
        length         長度字串（e.g. '30.00m'）
        inner_diameter 內徑字串（e.g. '0.53mm'）
        film_thickness 膜厚字串（e.g. '0.50µm'）
        polarity       極性字串，正規化為小寫（e.g. 'np' / 'p'）；表頭沒寫時退而
                       由 infer_polarity_from_column_name() 推得
        polarity_source 極性哪來的：'header'（表頭明寫）/
                       'inferred_from_column_name'（由型號推得）/ None（兩者皆無）

    **顯式優先、推測補位、來源可辨識**：表頭有寫就用表頭的，不覆蓋；沒寫才推。
    `polarity_source` 讓下游能區分兩者——沿用本專案 k0_mode / ri_mode 的慣例，
    值可以是推出來的，但不能讓人以為它是量到的。

    Raises
    ------
    ValueError
        當輸入不是字串時。字串內部格式問題 → 回傳部分 None 而非 raise，
        以容忍不同版本 .mea 的排版差異。
    """
    if not isinstance(gc_column_value, str):
        raise ValueError(f"gc_column_value 必須是字串，收到 {type(gc_column_value)}")

    parts = [p.strip() for p in gc_column_value.split(",")]
    result = {
        "column_name": None,
        "length": None,
        "inner_diameter": None,
        "film_thickness": None,
        "polarity": None,
        "polarity_source": None,
    }
    if not parts:
        return result
    # 第一段預設為型號名稱（無 "KEY:" 前綴）
    if parts[0] and ":" not in parts[0]:
        result["column_name"] = parts[0]
        parts = parts[1:]
    # 其餘段落用 "KEY: value" 抽取
    for seg in parts:
        if ":" not in seg:
            continue
        key, _, val = seg.partition(":")
        key = key.strip().upper()
        val = val.strip()
        if key == "L":
            result["length"] = val
        elif key == "ID":
            result["inner_diameter"] = val
        elif key == "FT":
            result["film_thickness"] = val
        elif key == "POLARITY":
            result["polarity"] = val.lower()
            result["polarity_source"] = "header"

    # 表頭沒寫極性才推——顯式值一律優先（見 docstring）
    if result["polarity"] is None:
        inferred = infer_polarity_from_column_name(result["column_name"])
        if inferred:
            result["polarity"] = inferred
            result["polarity_source"] = "inferred_from_column_name"
    return result


# --------------------------------------------------------------------------- #
# 選檔策略（workflow §第三階段第 3 點）
# --------------------------------------------------------------------------- #
def _list_files(data_dir, ext):
    """回傳 data_dir 底下所有指定副檔名的檔案完整路徑（不遞迴子目錄）。"""
    ext = ext.lower()
    if not os.path.isdir(data_dir):
        return []
    return [
        os.path.join(data_dir, f)
        for f in sorted(os.listdir(data_dir))
        if f.lower().endswith(ext)
    ]


def _column_name_matches(filename, column_name):
    """簡易 substring 比對：column_name 忽略大小寫是否為 filename 的子字串。

    [待決策] workflow 第三階段第 3 點提到「管柱型號名稱字串不保證與 .ril 檔名
    完全一致」，可能需要模糊比對或人工對照表。目前僅做寬鬆 substring 匹配作為
    第一版；若之後發現誤配率高，改成 rapidfuzz.ratio() 或維護一份對照表。
    """
    if not column_name:
        return False
    return column_name.lower() in filename.lower()


def select_ril_paths(data_dir, column_name=None, polarity=None):
    """
    依 workflow §第三階段策略挑 .ril 檔案。

    優先：以 column_name 對檔名做（寬鬆）substring 匹配，回傳所有匹配路徑
    退路：找不到匹配時，回傳極性合併檔（AVERAGE LOW POLAR / DB WAX 等）

    參數
    ----
    data_dir : str
        含 .ril 檔案的資料夾。
    column_name : str | None
        .mea 表頭 GC Column 解析出的型號名稱（e.g. 'FS-SE-54-CB-1'）。
    polarity : str | None
        'np'（非極性）/ 'p'（極性）。找不到 column 匹配時退回這個。

    回傳
    ----
    (paths, strategy) : (list[str], str)
        strategy ∈ {'column_name', 'polarity_fallback', 'none'}，供上游記錄
        選檔決策時的來源理由，寫進 identify 結果或 UI 溯源資訊。
    """
    all_files = _list_files(data_dir, ".ril")
    if column_name:
        hits = [p for p in all_files if _column_name_matches(os.path.basename(p), column_name)]
        if hits:
            return hits, "column_name"
    if polarity:
        pol = polarity.lower()
        # workflow §第三階段舉例：np → AVERAGE LOW POLAR / HP-5 / DB-5；
        #                        p  → DB WAX / HP WAX / Carbowax
        if pol == "np":
            keywords = ("low polar", "hp-5", "db-5")
        elif pol == "p":
            keywords = ("wax", "carbowax")
        else:
            keywords = ()
        hits = [
            p for p in all_files
            if any(k in os.path.basename(p).lower() for k in keywords)
        ]
        if hits:
            return hits, "polarity_fallback"
    return [], "none"


def select_iml_paths(data_dir, column_name=None, polarity=None):
    """
    依 workflow §第三階段策略挑 .iml 檔案。

    邏輯同 select_ril_paths()。**注意**：workflow 講的 drift gas 交叉核對是
    **row-level 篩選**（比對 .iml row 內 `Command` 欄的 `[+][N2]` 標記與
    .mea 表頭的 `Drift Gas` 值），不是檔案層級——.iml 檔名不含 gas 資訊，
    在此做檔名關鍵字比對只會造成 false negative。row-level 篩選見
    filter_iml_rows_by_drift_gas()。

    回傳
    ----
    (paths, strategy)
        同 select_ril_paths()。
    """
    all_files = _list_files(data_dir, ".iml")
    if column_name:
        hits = [p for p in all_files if _column_name_matches(os.path.basename(p), column_name)]
        if hits:
            return hits, "column_name"
    if polarity:
        pol = polarity.lower()
        if pol == "np":
            keywords = ("low polar", "hp-5", "db-5", "silox", "se")
        elif pol == "p":
            keywords = ("wax", "carbowax")
        else:
            keywords = ()
        hits = [
            p for p in all_files
            if any(k in os.path.basename(p).lower() for k in keywords)
        ]
        if hits:
            return hits, "polarity_fallback"
    return [], "none"


# --------------------------------------------------------------------------- #
# .ril 相位家族分類 + RI 尺標極性偵測
#
# 為什麼需要這一段（實際踩到的錯，不是預防性設計）：
# 比對是拿「峰的 RI」對「庫的 RI」。**兩者必須在同一套尺標上**，否則 ±5 的容許窗命中的
# 是「另一個化合物的 RI 恰好等於本峰的 RI」——錯得穩定，不是錯得隨機。
#
# 本專案實際發生過：峰的 RI 來自供應商對照表（證據指向**極性**尺標），而 `.ril` 依表頭
# `POLARITY: np` 載入**非極性**庫。拿 STD 自己的 2-butanone（身分百分之百確定）去查，
# 回來 176 個候選、**沒有一個是 2-butanone**，最接近的是 α-pinene 與各種吡嗪；而
# 2-butanone 就在同一批檔案裡，RI 549–622，離查詢值 318。
#
# 解法**不是**替使用者決定該用哪一套 RI 值——那是化學問題，不是程式問題。解法是讓選庫
# 跟著「實際在用的尺標」走：拿校正系列裡**已知身分的化合物**（CAS + RI），去 library_data
# 查它們在極性/非極性兩個相位家族的值，看落在哪一邊。這樣不論使用者選哪套 RI 值，
# 查詢與參考永遠在同一套尺標上。
# --------------------------------------------------------------------------- #
RIL_FAMILY_KEYWORDS = {
    # 非極性（甲基/苯基聚矽氧烷家族）
    "np": ("low polar", "db-5", "hp-5", "hp-1", "ov-101", "ov-1", "ps-089",
           "se-5", "se-3", "cp-sil", "zb-5", "rtx-5", "non-polar", "nonpolar"),
    # 極性（PEG / wax 家族）
    "p": ("wax", "carbowax", "innowax", "peg", "ffap"),
}


def ril_family(filename):
    """由 `.ril` 檔名判斷它屬於哪個相位家族：'np' / 'p' / None（認不出來）。

    先比對極性關鍵字再比非極性——`"NIST2020 RI DB-Wax.ril"` 同時含 `db-` 與 `wax`，
    順序反過來會把它誤判成非極性。
    """
    low = os.path.basename(str(filename)).lower()
    if any(k in low for k in RIL_FAMILY_KEYWORDS["p"]):
        return "p"
    if any(k in low for k in RIL_FAMILY_KEYWORDS["np"]):
        return "np"
    return None


def _family_reference(data_dir, cas_wanted):
    """{正規化 CAS: {'np': 中位 RI, 'p': 中位 RI}}，只收兩個家族都有值的 CAS。"""
    import statistics
    want = {re.sub(r"[^0-9]", "", c or "") for c in cas_wanted}
    want.discard("")
    acc = {}
    for path in _list_files(data_dir, ".ril"):
        fam = ril_family(path)
        if fam is None:
            continue
        for row in load_ril(path):
            cas = re.sub(r"[^0-9]", "", row.get("CAS") or "")
            if cas in want and row.get("RI") is not None:
                acc.setdefault(cas, {"np": [], "p": []})[fam].append(row["RI"])
    return {c: {k: statistics.median(v) for k, v in d.items() if v}
            for c, d in acc.items()}


def detect_ri_scale_polarity(cas_numbers, ri_values, data_dir):
    """判斷一組「已知化合物 + 其 RI」屬於哪個相位家族的尺標。

    作法：每個化合物在 `library_data` 裡查它在非極性/極性兩家族的中位 RI，看給定的
    RI 離哪邊近，投一票。**用的是使用者自己的 library_data，不是外部文獻**——這既是
    最相關的參照，也讓判斷可以被重跑驗證。

    回傳 (polarity, detail)：polarity ∈ {'np', 'p', None}。票數不足或兩邊票數相同時
    回 None——**寧可回「不知道」也不要猜**，猜錯會讓選庫整批用錯相位。

    參數
    ----
    cas_numbers : list[str]   化合物 CAS（與 ri_values 一一對應）
    ri_values   : list[float] 該尺標下的 RI
    data_dir    : str         .ril 所在資料夾
    """
    detail = {"n_probes": 0, "votes": {"np": 0, "p": 0}, "per_compound": []}
    if not cas_numbers or not ri_values or not os.path.isdir(data_dir or ""):
        detail["reason"] = "缺 CAS/RI 或找不到 library 資料夾"
        return None, detail
    ref = _family_reference(data_dir, cas_numbers)
    for cas, ri in zip(cas_numbers, ri_values):
        key = re.sub(r"[^0-9]", "", cas or "")
        r = ref.get(key)
        if not r or len(r) < 2 or ri is None:
            continue
        vote = "np" if abs(ri - r["np"]) < abs(ri - r["p"]) else "p"
        detail["votes"][vote] += 1
        detail["n_probes"] += 1
        detail["per_compound"].append(
            {"cas": key, "ri": ri, "lib_np": round(r["np"], 1),
             "lib_p": round(r["p"], 1), "vote": vote})
    np_v, p_v = detail["votes"]["np"], detail["votes"]["p"]
    if detail["n_probes"] < 2 or np_v == p_v:
        detail["reason"] = f"票數不足或平手（np={np_v}, p={p_v}）→ 不猜"
        return None, detail
    return ("np" if np_v > p_v else "p"), detail


# 載流氣體標記的別名表。key 為正規化後的氣體名，value 為該氣體在 .iml row 裡
# 可能出現的標記字串。用於「這一列是不是別種氣體量的」這個判斷。
_DRIFT_GAS_ALIASES = {
    "nitrogen":      ("[n2]", "[nitrogen]"),
    "n2":            ("[n2]", "[nitrogen]"),
    "air":           ("[air]",),
    "synthetic air": ("[air]",),
}


def _gas_aliases(drift_gas):
    """回傳某氣體名對應的標記別名 tuple（未登記者退回 '[<名稱>]'）。"""
    gas = drift_gas.strip().lower()
    return _DRIFT_GAS_ALIASES.get(gas, (f"[{gas}]",))


def _row_gas_blob(row):
    """把一列裡可能藏氣體標記的兩個欄位合成小寫字串。

    workflow 文件說標記在 `Command` 欄，但實測真實檔案（GAS BASE 3H_IMS K0.iml、
    002 TST GAS 2020.iml）實際存於 `DeviceTimestamp` 欄，故兩欄都看。
    """
    return ((row.get("Command") or "") + " " + (row.get("DeviceTimestamp") or "")).lower()


def filter_iml_rows_by_drift_gas(rows, drift_gas, keep_untagged=True):
    """
    Row-level 篩選：排除「明確標記為其他載流氣體」的 .iml row。

    workflow §第三階段第 3 點：.mea 表頭 `Drift Gas: nitrogen` 應與 .iml row 的
    `[+][N2]` / `[+][nitrogen]` 標記交叉核對，避免拿不同載流氣體下量出的漂移值
    做比對。

    **`keep_untagged=True`（預設）是刻意的保守語意，不是漏寫**：實測 library_data/
    的 1003 筆 .iml row 中，201 筆 `DtMode=="RIPrel"` 只有 162 筆帶氣體標記——其餘
    39 筆來自欄位會偏移的舊格式檔（見本模組檔頭「已知 caveat」），它們是「沒有記錄
    氣體」而不是「記錄了別種氣體」。若採嚴格語意（只留標記相符者），這 39 筆漂移候選
    會被靜默丟掉，等於用缺漏的 metadata 去否定實際可用的資料，正是本專案一貫要避免的
    false negative。故預設只擋「有標記且標記為別種氣體」者；要嚴格模式再顯式關掉。

    參數
    ----
    rows : list[dict]
        load_iml() / load_iml_many() 產出的清單。
    drift_gas : str
        .mea 表頭 'Drift Gas' 欄位值，e.g. 'nitrogen' / 'air'。若為 None
        或空字串則不篩選，原樣回傳。
    keep_untagged : bool
        True（預設）→ 無任何已知氣體標記的 row 予以保留（保守）。
        False           → 只保留標記與 drift_gas 相符者（嚴格，會丟掉未標記者）。

    回傳
    ----
    list[dict]
        篩選後的 rows。
    """
    if not drift_gas:
        return list(rows)
    wanted = _gas_aliases(drift_gas)
    # 「別種氣體」= 所有已登記別名扣掉本次要的那些
    known = {a for aliases in _DRIFT_GAS_ALIASES.values() for a in aliases}
    other = known - set(wanted)

    out = []
    for r in rows:
        blob = _row_gas_blob(r)
        if any(a in blob for a in wanted):
            out.append(r)                      # 明確相符 → 一定留
        elif not keep_untagged:
            continue                           # 嚴格模式：沒相符就丟
        elif not any(a in blob for a in other):
            out.append(r)                      # 保守模式：沒標到別種氣體 → 留
    return out


# --------------------------------------------------------------------------- #
# 高階便利函式：一次讀多檔並合併
# --------------------------------------------------------------------------- #
def load_ril_many(paths):
    """讀多個 .ril，合併成單一 list[dict]。source_file 隨每 row 標明來源。"""
    out = []
    for p in paths:
        out.extend(load_ril(p))
    return out


def load_iml_many(paths):
    """讀多個 .iml，合併成單一 list[dict]。source_file 隨每 row 標明來源。"""
    out = []
    for p in paths:
        out.extend(load_iml(p))
    return out

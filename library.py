"""
library.py  —  GC-IMS Identify Workflow 第三階段：.ril / .iml 資料庫讀取
Version: 3.0 — by Albert Sheng

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
def parse_gc_column_header(gc_column_value):
    """
    解析 .mea 表頭 'GC Column' 欄位，取出五個子資訊。

    輸入格式（實測範例）:
        'FS-SE-54-CB-1, L: 30.00m, ID: 0.53mm, FT: 0.50µm, POLARITY: np'

    回傳
    ----
    dict，包含以下鍵（找不到即為 None）：
        column_name    型號名稱（第一個逗號前的整段）
        length         長度字串（e.g. '30.00m'）
        inner_diameter 內徑字串（e.g. '0.53mm'）
        film_thickness 膜厚字串（e.g. '0.50µm'）
        polarity       極性字串，正規化為小寫（e.g. 'np' / 'p'）

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


def filter_iml_rows_by_drift_gas(rows, drift_gas):
    """
    Row-level 篩選：保留載流氣體標記符合的 .iml row。

    workflow §第三階段第 3 點：.mea 表頭 `Drift Gas: nitrogen` 應與 .iml
    row 的 `[+][N2]` / `[+][nitrogen]` 標記交叉核對，避免載入不同載流氣體
    下量出的 K0 值做比對。

    **實測校正**：workflow 文件說標記在 `Command` 欄，但真實檔案（GAS BASE
    3H_IMS K0.iml）標記實際存於 `DeviceTimestamp` 欄。此函式同時檢查兩欄，
    兼容不同檔案變體。

    參數
    ----
    rows : list[dict]
        load_iml() / load_iml_many() 產出的清單。
    drift_gas : str
        .mea 表頭 'Drift Gas' 欄位值，e.g. 'nitrogen' / 'air'。若為 None
        或空字串則不篩選，原樣回傳。

    回傳
    ----
    list[dict]
        篩選後的 rows。兩個候選欄位都不含相關標記者被排除。
    """
    if not drift_gas:
        return list(rows)
    gas = drift_gas.strip().lower()
    aliases = {
        "nitrogen": ("[n2]", "[nitrogen]"),
        "n2":       ("[n2]", "[nitrogen]"),
        "air":      ("[air]",),
        "synthetic air": ("[air]",),
    }.get(gas, (f"[{gas}]",))
    out = []
    for r in rows:
        blob = ((r.get("Command") or "") + " " + (r.get("DeviceTimestamp") or "")).lower()
        if any(a in blob for a in aliases):
            out.append(r)
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

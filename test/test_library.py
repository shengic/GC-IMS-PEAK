"""
test_library.py — Smoke test for library.py（第三階段 .ril/.iml 讀取）。

雙用途：`pytest test/test_library.py` 或 `python test/test_library.py`。
"""
import os
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import library

# 透過 resolve_data_dir() 抓資料庫路徑，優先 library_data/，退路走舊 VOCal 資料夾。
# 兩者皆不存在 → skip 整支測試（此測依真實 .ril/.iml 檔跑）。
VOCAL_DATA = library.resolve_data_dir()


def sep(title):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def test_library_smoke():
    if VOCAL_DATA is None:
        import pytest
        pytest.skip("library.resolve_data_dir() 找不到 library_data/ 或舊 VOCal "
                    "資料夾；請先把 .ril / .iml 放進 <PROJECT_ROOT>/library_data/")
    print(f"resolve_data_dir() -> {VOCAL_DATA}")

    # ---- load_ril ----
    sep("[1] load_ril: NIST2020 RI SE-52.ril (應為 21 欄結構)")
    ril = library.load_ril(os.path.join(VOCAL_DATA, "NIST2020 RI SE-52.ril"))
    print(f"  rows = {len(ril)}")
    r = ril[0]
    print(f"  第 1 row keys: {list(r.keys())[:8]}...")
    print(f"    CAS={r['CAS']}  NAME={r['NAME']}  Formula={r['Formula']}")
    print(f"    RI={r['RI']} (type {type(r['RI']).__name__})  ColumnName={r['ColumnName']}")
    print(f"    ColumnPolarity={r['ColumnPolarity']}  LiteratureIndex={r['LiteratureIndex']}")
    print(f"    source_file={r['source_file']}")
    assert isinstance(r["RI"], float), "RI 應解析成 float"
    assert r["source_file"] == "NIST2020 RI SE-52.ril"

    sep("[2] load_ril: AVERAGE LOW POLAR.ril (22 欄，第 22 欄應被丟棄)")
    ril2 = library.load_ril(os.path.join(VOCAL_DATA, "AVERAGE LOW POLAR.ril"))
    print(f"  rows = {len(ril2)}")
    r2 = ril2[0]
    print(f"    CAS={r2['CAS']}  RI={r2['RI']}  ColumnPolarity={r2['ColumnPolarity']}")
    print(f"    source_file={r2['source_file']}")
    # "null" 字串必須被視為 None
    assert r2["ColumnPolarity"] is None, "'null' 字串必須解析成 None"

    # ---- load_iml ----
    sep("[3] load_iml: GAS BASE 3H_IMS K0.iml (16 欄，K0 schema)")
    iml = library.load_iml(os.path.join(VOCAL_DATA, "GAS BASE 3H_IMS K0.iml"))
    print(f"  rows = {len(iml)}")
    m = iml[0]
    print(f"    Name={m['Name']}  CAS={m['CAS']}  MW={m['MW']}")
    print(f"    RI={m['RI']}  Rt[sec]={m['Rt[sec]']}  Dt[a.u.]={m['Dt[a.u.]']}")
    print(f"    p(IMS)={m['p(IMS)']}  T(IMS)={m['T(IMS)']}  L(IMS)={m['L(IMS)']}  U(IMS)={m['U(IMS)']}")
    print(f"    RIP[a.u.]={m['RIP[a.u.]']}  DtMode={m['DtMode']!r}  source_file={m['source_file']}")
    assert isinstance(m["Dt[a.u.]"], float)
    assert m["DtMode"] and "1/K0" in m["DtMode"], "DtMode 應含 '1/K0'（可能尾隨空白）"

    sep("[4] load_iml: GAS 3H_IMS.iml (舊格式；前 7 欄仍對齊，後半段偏移)")
    iml2 = library.load_iml(os.path.join(VOCAL_DATA, "GAS 3H_IMS.iml"))
    print(f"  rows = {len(iml2)}")
    m2 = iml2[0]
    # 關鍵匹配欄位（Name/CAS/Formula/MW/RI/Rt/Dt）在兩種格式都對齊
    print(f"    Name={m2['Name']}  Rt[sec]={m2['Rt[sec]']}  Dt[a.u.]={m2['Dt[a.u.]']}")
    assert m2["Name"] == "Styrene"
    assert m2["Rt[sec]"] == 21.52
    assert m2["Dt[a.u.]"] == 1.7703
    # 後半段（p/T/L/U/RIP/DtMode/EditEvent）在此舊檔會偏移，不做斷言

    # ---- parse_gc_column_header ----
    sep("[5] parse_gc_column_header: 實測 .mea 表頭範例")
    example = "FS-SE-54-CB-1, L: 30.00m, ID: 0.53mm, FT: 0.50µm, POLARITY: np"
    parsed = library.parse_gc_column_header(example)
    print(f"  input: {example}")
    for k, v in parsed.items():
        print(f"    {k}: {v!r}")
    assert parsed == {
        "column_name": "FS-SE-54-CB-1",
        "length": "30.00m",
        "inner_diameter": "0.53mm",
        "film_thickness": "0.50µm",
        "polarity": "np",
        "polarity_source": "header",
    }

    # ---- select_ril_paths ----
    sep("[6] select_ril_paths: column_name 精確匹配")
    hits, strat = library.select_ril_paths(VOCAL_DATA, column_name="SE-52")
    print(f"  strategy={strat}  hits={len(hits)}")
    for p in hits:
        print(f"    {os.path.basename(p)}")
    assert strat == "column_name"
    assert any("SE-52" in os.path.basename(p) for p in hits)

    sep("[7] select_ril_paths: 找不到 column → 極性退路 np")
    hits, strat = library.select_ril_paths(VOCAL_DATA,
                                           column_name="NOT-A-REAL-COLUMN-XYZ",
                                           polarity="np")
    print(f"  strategy={strat}  hits={len(hits)}")
    for p in hits[:5]:
        print(f"    {os.path.basename(p)}")
    print(f"    ... ({len(hits)} total)")
    assert strat == "polarity_fallback"
    assert hits, "np 極性退路至少要找到 AVERAGE LOW POLAR / HP-5 / DB-5 之一"

    sep("[8] select_ril_paths: 極性 p 退路")
    hits, strat = library.select_ril_paths(VOCAL_DATA,
                                           column_name=None, polarity="p")
    print(f"  strategy={strat}  hits={len(hits)}")
    for p in hits[:5]:
        print(f"    {os.path.basename(p)}")
    print(f"    ... ({len(hits)} total)")
    assert strat == "polarity_fallback"
    assert hits, "p 極性退路至少要找到 wax/carbowax 相關檔案"

    sep("[9] select_iml_paths: column_name 匹配（drift_gas 不在檔案層級）")
    hits, strat = library.select_iml_paths(VOCAL_DATA, column_name="3H_IMS")
    print(f"  strategy={strat}  hits={len(hits)}")
    for p in hits:
        print(f"    {os.path.basename(p)}")
    assert strat == "column_name"
    assert hits

    sep("[9b] filter_iml_rows_by_drift_gas: row-level 篩選 [+][N2] 標記")
    all_iml = library.load_iml_many(hits)
    filtered_n2 = library.filter_iml_rows_by_drift_gas(all_iml, "nitrogen")
    filtered_air = library.filter_iml_rows_by_drift_gas(all_iml, "air")
    print(f"  total rows                 = {len(all_iml)}")
    print(f"  drift_gas='nitrogen' kept  = {len(filtered_n2)}")
    print(f"  drift_gas='air' kept       = {len(filtered_air)}")
    if filtered_n2:
        print(f"  sample kept Command: {filtered_n2[0].get('Command')!r}")
    # 至少 K0 檔（有 [+][N2] 標記）的 row 應被 nitrogen 保留
    assert len(filtered_n2) >= 1, "nitrogen 篩選應保留至少一 row（K0 檔的 [+][N2] 標記）"
    # 空 drift_gas 原樣回傳
    same = library.filter_iml_rows_by_drift_gas(all_iml, "")
    assert len(same) == len(all_iml)

    sep("[10] load_ril_many / load_iml_many 合併，source_file 帶到每 row")
    hits_ril, _ = library.select_ril_paths(VOCAL_DATA, column_name="SE-52")
    merged = library.load_ril_many(hits_ril[:2])
    print(f"  merged {len(hits_ril[:2])} 檔 → {len(merged)} rows")
    sources = set(r["source_file"] for r in merged)
    print(f"  distinct source_files: {sources}")
    assert len(sources) >= 1

    print()
    print("[OK] all library.py smoke checks passed")


# --------------------------------------------------------------------------- #
# 極性推測：舊版 GC Column 排版沒有 POLARITY 欄位（實測 GAS/藝妓咖啡 全批）
# --------------------------------------------------------------------------- #
def test_polarity_inferred_when_header_omits_it():
    """舊排版 'FS-SE54-CB-0.5, 15m x 0,32ID' 沒有 POLARITY:，要能推出 np。

    後果不是「少一個欄位」而已：polarity=None 會讓 select_ril_paths() 的極性退路
    整條不啟動，回傳 strategy='none' 與 0 筆 .ril——RI 維度連庫都沒有，而畫面上
    沒有任何跡象。實測該資料夾原本 0 筆，推出極性後 13 個檔、117k 列。
    """
    parsed = library.parse_gc_column_header("FS-SE54-CB-0.5, 15m x 0,32ID")
    assert parsed["column_name"] == "FS-SE54-CB-0.5"
    assert parsed["polarity"] == "np"
    assert parsed["polarity_source"] == "inferred_from_column_name"


def test_explicit_polarity_is_never_overwritten_by_inference():
    """表頭明寫的極性優先，且來源標記要能分辨兩者。

    這條是防呆：推測只該補位、不該蓋掉量到的東西。若哪天推測表寫錯，明寫的值
    仍必須贏——不然錯誤會擴散到本來正確的批次。
    """
    parsed = library.parse_gc_column_header("SOME-WAX-COLUMN, POLARITY: np")
    assert parsed["polarity"] == "np"          # 表頭說 np，即使名字裡有 'wax'
    assert parsed["polarity_source"] == "header"


def test_unknown_phase_infers_nothing():
    """認不出的固定相回 None，不亂猜。

    載錯極性的 .ril 等於拿另一種固定相的尺標比 RI，比「沒有庫」更糟——後者看得
    出來，前者會產出看似合理的數字。
    """
    parsed = library.parse_gc_column_header("MYSTERY-PHASE-9000, 10m")
    assert parsed["polarity"] is None
    assert parsed["polarity_source"] is None
    assert library.infer_polarity_from_column_name("MYSTERY-PHASE-9000") is None


def test_phase_name_separators_do_not_matter():
    """SE-54 / SE54 / SE 54 是同一種固定相，寫法不該改變結果。"""
    for name in ("FS-SE-54-CB-1", "FS-SE54-CB-0.5", "FS SE 54"):
        assert library.infer_polarity_from_column_name(name) == "np", name
    for name in ("DB-WAX", "HP-INNOWAX", "Carbowax 20M"):
        assert library.infer_polarity_from_column_name(name) == "p", name


if __name__ == "__main__":
    test_library_smoke()


# --------------------------------------------------------------------------- #
# RI 尺標極性偵測（2026-08-24）—— 選庫要跟著實際的尺標走，不是跟著表頭
# --------------------------------------------------------------------------- #
def test_ril_family_puts_db_wax_on_the_polar_side():
    """`NIST2020 RI DB-Wax.ril` 同時含 `db-` 與 `wax`，必須判成極性。

    關鍵字比對順序寫反就會把整批 wax 檔誤判成非極性——而那正是選庫的依據。
    """
    assert library.ril_family("NIST2020 RI DB-Wax.ril") == "p"
    assert library.ril_family("NIST2020 RI DB-5.ril") == "np"
    assert library.ril_family("AVERAGE LOW POLAR.ril") == "np"
    assert library.ril_family("NIST2014 carbowax 20m.ril") == "p"
    assert library.ril_family("MERGED NIST2020 RI SNP.ril") is None   # 認不出就 None


def test_detect_ri_scale_polarity_follows_the_values_not_a_hardcoded_side():
    """同一組化合物、兩套 RI 值 → 兩個不同答案。**程式不替使用者選邊。**

    比對是拿峰的 RI 對庫的 RI，兩者必須同尺標。實測踩過的錯：峰的 RI 用供應商對照表
    （極性尺標），庫卻依表頭 `POLARITY: np` 載入非極性——拿 STD 自己的 2-butanone
    去查，176 個候選裡沒有 2-butanone，而它就在同一批檔案裡（RI 549–622）。
    """
    if VOCAL_DATA is None:
        import pytest
        pytest.skip("需要真實的 library_data")
    import reference_series as rs
    k = rs.REFERENCE_SERIES["ketone"]
    pol_supplier, d1 = library.detect_ri_scale_polarity(
        k["cas_numbers"], k["ri_values"], VOCAL_DATA)
    pol_nonpolar, d2 = library.detect_ri_scale_polarity(
        k["cas_numbers"],
        [589.4, 688.6, 784.2, 892.2, 996.5, 1095.6], VOCAL_DATA)
    assert pol_supplier == "p", d1
    assert pol_nonpolar == "np", d2
    assert d1["n_probes"] == 6 and d2["n_probes"] == 6


def test_detect_ri_scale_refuses_to_guess_when_evidence_is_thin():
    """探針太少或平手 → 回 None，退回表頭極性。

    猜錯的代價是整批用錯相位的庫，比「不知道」嚴重得多。
    """
    if VOCAL_DATA is None:
        import pytest
        pytest.skip("需要真實的 library_data")
    pol, d = library.detect_ri_scale_polarity(["C78933"], [916.8], VOCAL_DATA)
    assert pol is None and "不猜" in d["reason"]
    pol, d = library.detect_ri_scale_polarity([], [], VOCAL_DATA)
    assert pol is None
    pol, d = library.detect_ri_scale_polarity(["C78933"], [916.8], "no/such/dir")
    assert pol is None

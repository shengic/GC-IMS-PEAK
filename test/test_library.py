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


if __name__ == "__main__":
    test_library_smoke()

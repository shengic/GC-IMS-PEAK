"""
test_match.py — Smoke test for match.py（第五階段容許窗比對）。

雙用途：`pytest test/test_match.py` 或 `python test/test_match.py`。
用合成 library rows + 假峰驗證邏輯本身。
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

import match


def sep(t):
    print()
    print("=" * 60)
    print(t)
    print("=" * 60)


def fake_ril():
    """.ril rows：只有 RI，沒 Rt/Dt/DtMode"""
    return [
        {"CAS": "C001", "NAME": "compA", "RI": 1000, "source_file": "ril_A.ril"},
        {"CAS": "C002", "NAME": "compB", "RI": 1005, "source_file": "ril_A.ril"},
        {"CAS": "C003", "NAME": "compC", "RI": 1200, "source_file": "ril_B.ril"},
        {"CAS": "C001", "NAME": "compA-dup", "RI": 1010, "source_file": "ril_B.ril"},  # 同 CAS 二筆
    ]


def fake_iml():
    """.iml rows：有 RI、Rt[sec]、Dt[a.u.]、DtMode"""
    return [
        {"CAS": "C001", "Name": "compA", "RI": 1002, "Rt[sec]": 100.0,
         "Dt[a.u.]": 1.50, "DtMode": "1/K0 ", "source_file": "iml_X.iml"},
        {"CAS": "C002", "Name": "compB", "RI": 1005, "Rt[sec]": 110.0,
         "Dt[a.u.]": 1.60, "DtMode": "1/K0", "source_file": "iml_X.iml"},
        {"CAS": "C099", "Name": "compZ", "RI": None,  "Rt[sec]": 90.0,
         "Dt[a.u.]": 1.48, "DtMode": "1/K0", "source_file": "iml_X.iml"},
        # DtMode 不是 K0 → K0 比對應跳過
        {"CAS": "C010", "Name": "compY", "RI": 1000, "Rt[sec]": 105.0,
         "Dt[a.u.]": 1.50, "DtMode": "ms", "source_file": "iml_Y.iml"},
        # RIPrel（相對 RIP 漂移）rows：RI=None 不影響 RI 比對，供 drift_rel 用
        {"CAS": "C001", "Name": "compA-rip", "RI": None, "Dt[a.u.]": 1.12,
         "DtMode": "RIPrel", "source_file": "iml_R.iml"},
        {"CAS": "C077", "Name": "compR", "RI": None, "Dt[a.u.]": 1.15,
         "DtMode": "RIPrel", "source_file": "iml_R.iml"},
    ]


def test_match_smoke():
    ril = fake_ril()
    iml = fake_iml()

    sep("[1] match_ri: peak_ri=1003, tol=10 → 命中 RI 在 993~1013 的 row")
    hits = match.match_ri(1003, ril + iml, tolerance=10)
    print(f"  {len(hits)} hits, sorted by delta_ri:")
    for h in hits:
        print(f"    CAS={h['CAS']} RI={h['RI']} delta_ri={h['delta_ri']} src={h['source_file']}")
    # ril: C001(1000, dt=3), C002(1005, dt=2), C001-dup(1010, dt=7); iml: C001(1002, dt=1), C002(1005, dt=2), compY(1000, dt=3)
    # C003(1200) 太遠 → 不進；compZ(RI=None) 跳過
    assert len(hits) == 6
    assert hits[0]["delta_ri"] == 1     # iml C001
    assert all(h.get("delta_ri", 999) <= 10 for h in hits)
    assert all("ri" in h["match_dimensions"] for h in hits)

    sep("[2] match_ri: peak_ri=None → 回空")
    assert match.match_ri(None, ril + iml, 10) == []

    sep("[3] match_rt: peak_retention_s=102, tol=5 → 命中 iml Rt 在 97~107 的")
    hits = match.match_rt(102.0, iml, tolerance=5)
    print(f"  {len(hits)} hits:")
    for h in hits:
        print(f"    CAS={h['CAS']} Rt={h['Rt[sec]']} delta_rt={h['delta_rt']}")
    # C001(100, dt=2), compZ(90, dt=12→out), compY(105, dt=3)
    assert len(hits) == 2
    assert set(h["CAS"] for h in hits) == {"C001", "C010"}

    sep("[4] match_k0: 庫值 DtMode='1/K0' → 換算到 K0 空間再比")
    # 2026-08-12：dt_convert 兩個模式一律回傳 K0，而 .iml 的 DtMode 實測為 "1/K0"
    # （存逆值），故 match_k0 內部取倒數換算。庫值 1.50/1.60/1.48 對應的 K0 分別是
    # 0.6667 / 0.6250 / 0.6757。以 peak_k0=0.670、tol=0.02 查詢：
    #   C001 K0=0.6667  Δ0.0033 → 命中
    #   C099 K0=0.6757  Δ0.0057 → 命中
    #   C002 K0=0.6250  Δ0.0450 → 超差
    #   compY DtMode="ms"       → 被 DtMode filter 剔除
    hits = match.match_k0(0.670, iml, tolerance=0.02)
    print(f"  {len(hits)} hits:")
    for h in hits:
        print(f"    CAS={h['CAS']} Dt={h['Dt[a.u.]']} (1/K0) -> K0={h['k0_library_value']:.4f} "
              f"DtMode={h['DtMode']!r} delta_k0={h['delta_k0']:.4f}")
    got_cas = set(h["CAS"] for h in hits)
    assert got_cas == {"C001", "C099"}, f"expected {{C001, C099}}, got {got_cas}"
    # 換算確實發生：庫值原樣保留，另存換算後的 K0
    c001 = [h for h in hits if h["CAS"] == "C001"][0]
    assert c001["Dt[a.u.]"] == 1.50
    assert abs(c001["k0_library_value"] - 1 / 1.50) < 1e-12

    sep("[4b] 若庫值直接存 K0（DtMode='K0'）則不取倒數")
    direct = [{"CAS": "D1", "Name": "direct", "Dt[a.u.]": 0.670, "DtMode": "K0"}]
    h = match.match_k0(0.670, direct, tolerance=0.001)
    assert len(h) == 1 and abs(h[0]["k0_library_value"] - 0.670) < 1e-12
    # 反面：同一筆若標成 1/K0，換算後是 1.49，就不該命中 0.670
    inv = [dict(direct[0], DtMode="1/K0")]
    assert match.match_k0(0.670, inv, tolerance=0.001) == []

    sep("[5] match_k0: peak_k0=None → 回空")
    assert match.match_k0(None, iml, 0.05) == []

    sep("[6] combine_by_cas: RI 與 K0 都命中同 CAS")
    gc_hits = match.match_ri(1003, ril + iml, tolerance=10)
    ims_hits = match.match_k0(0.670, iml, tolerance=0.02)
    combined = match.combine_by_cas(gc_hits, ims_hits)
    print(f"  gc={len(gc_hits)}  ims={len(ims_hits)}  combined={len(combined)}")
    for c in combined:
        print(f"    CAS={c['CAS']} dims={c['match_dimensions']}  "
              f"delta_ri={c.get('delta_ri')} delta_k0={c.get('delta_k0')}  "
              f"gc_src={c['source_file_gc']} ims_src={c['source_file_ims']}")
    # C001 兩邊都命中 → 進 combined
    # C099 只在 ims 命中，沒進 gc → 不進 combined
    cas_in_combined = set(c["CAS"] for c in combined)
    assert "C001" in cas_in_combined
    assert "C099" not in cas_in_combined
    # match_dimensions 應同時含 ri 與 k0
    c001 = [c for c in combined if c["CAS"] == "C001"][0]
    assert "ri" in c001["match_dimensions"]
    assert "k0" in c001["match_dimensions"]
    assert c001["source_file_gc"] and c001["source_file_ims"]

    sep("[7] match_all: 峰同時有 ri 與 k0 → 三分支都有輸出")
    peak = {"ri": 1003, "retention_s": 100.0, "k0_value": 0.670, "k0_mode": "standard_based"}
    r = match.match_all(peak, ril, iml, ri_tolerance=10, k0_tolerance=0.02)
    print(f"  gc={len(r['gc_matches'])} (dim={r['gc_dimension']})")
    print(f"  ims={len(r['ims_matches'])}")
    print(f"  combined={len(r['combined_matches'])}")
    assert r["gc_dimension"] == "ri"
    assert len(r["gc_matches"]) > 0
    assert len(r["ims_matches"]) > 0
    assert len(r["combined_matches"]) > 0

    sep("[8] match_all: 峰無 ri 但有 retention_s → gc 走 RT 退路")
    peak = {"ri": None, "retention_s": 102.0, "k0_value": 0.670, "k0_mode": "raw_parameters"}
    r = match.match_all(peak, ril, iml, rt_tolerance=5, k0_tolerance=0.02)
    print(f"  gc_dimension={r['gc_dimension']}  gc={len(r['gc_matches'])}  ims={len(r['ims_matches'])}")
    assert r["gc_dimension"] == "rt"
    assert all(h.get("delta_rt") is not None for h in r["gc_matches"])

    sep("[9] match_all: 峰無 K0（unavailable 模式）→ ims 空、combined 空")
    peak = {"ri": 1003, "retention_s": 100.0, "k0_value": None, "k0_mode": "unavailable"}
    r = match.match_all(peak, ril, iml, ri_tolerance=10)
    print(f"  gc={len(r['gc_matches'])}  ims={len(r['ims_matches'])}  combined={len(r['combined_matches'])}")
    assert len(r["gc_matches"]) > 0
    assert r["ims_matches"] == []
    assert r["combined_matches"] == []

    sep("[11] match_drift_rel: drift_relative=1.13, tol=0.05 → 只看 DtMode==RIPrel")
    hits = match.match_drift_rel(1.13, iml, tolerance=0.05)
    got = set(h["CAS"] for h in hits)
    print(f"  {len(hits)} hits: {got}")
    # C001 RIPrel(1.12, d=0.01)、C077 RIPrel(1.15, d=0.02) 命中；1/K0 與 ms 的不算
    assert got == {"C001", "C077"}, got
    assert all("drift_rel" in h["match_dimensions"] for h in hits)
    assert all(h.get("delta_drift_rel") is not None for h in hits)
    assert match.match_drift_rel(None, iml, 0.05) == []

    sep("[12] match_all IMS：無 K0 但有 drift_relative → 走 drift_rel，可有 combined")
    peak = {"ri": 1003, "drift_relative": 1.12, "k0_value": None}
    r = match.match_all(peak, ril, iml, ri_tolerance=10, driftrel_tolerance=0.05)
    print(f"  ims_dim={r['ims_dimension']} ims={len(r['ims_matches'])} combined={len(r['combined_matches'])}")
    assert r["ims_dimension"] == "drift_rel"
    assert len(r["ims_matches"]) > 0
    # C001 兩軸都命中（RI 1002 + drift 1.12）→ combined，且 dims 同含 ri 與 drift_rel
    c001 = [c for c in r["combined_matches"] if c["CAS"] == "C001"]
    assert c001, "C001 should be a combined GC+IMS match"
    assert "ri" in c001[0]["match_dimensions"] and "drift_rel" in c001[0]["match_dimensions"]

    sep("[10] source_file 溯源：candidate dict 帶 source_file，不需比對邏輯處理")
    # workflow §第三階段第 4 點：source_file 隨 dict 淺拷貝自動傳遞
    hits = match.match_ri(1000, ril, tolerance=15)
    assert all("source_file" in h for h in hits)
    assert set(h["source_file"] for h in hits) == {"ril_A.ril", "ril_B.ril"}

    print()
    print("[OK] all match.py smoke checks passed")


if __name__ == "__main__":
    test_match_smoke()

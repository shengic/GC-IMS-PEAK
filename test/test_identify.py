"""
test_identify.py — Smoke test for identify.py（第六階段整合）。

雙用途：`pytest test/test_identify.py` 或 `python test/test_identify.py`。
依賴真實 .mea 檔 + library_data/，缺任一則 skip。
"""
import os
import sys
import json
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import identify
import library


MEA = os.path.join(PROJECT_ROOT, "GAS", "嘉義大學＿咖啡發酵", "260625_141215_STD.mea")


def sep(t):
    print()
    print("=" * 60)
    print(t)
    print("=" * 60)


def _make_peaks_doc():
    """製造一份精簡的 _peaks.json 內容（不跑 detect_peaks，節省 83 秒）。

    包含五顆假峰，模擬 peaks.py + rip 整合後的輸出格式。dt_index 涵蓋
    RIP 附近（680）與遠離（1500）兩種情況；drift_relative 已算好。
    """
    return {
        "source": MEA,
        "machine": "FlavourSpec®",
        "sample": "STD",
        "matrix_shape": [20413, 3150],
        "stats": {
            "floor": 218.6,
            "rip_index": 680,
            "rip_intensity": 4969,
        },
        "n_peaks": 5,
        "peaks": [
            {"peak_id": 1, "rt_index": 1000, "dt_index": 680,  "intensity": 5000,
             "prominence": 4900, "flatness": 0.2, "edge_dist": 500, "saturated": False,
             "retention_s": 126.0, "drift_ms": 4.53, "drift_relative": 1.0},   # 落 RIP 帶
            {"peak_id": 2, "rt_index": 2000, "dt_index": 1500, "intensity": 4000,
             "prominence": 3500, "flatness": 0.3, "edge_dist": 400, "saturated": False,
             "retention_s": 252.0, "drift_ms": 10.0, "drift_relative": 2.206},
            {"peak_id": 3, "rt_index": 3000, "dt_index": 900,  "intensity": 3500,
             "prominence": 3000, "flatness": 0.25, "edge_dist": 300, "saturated": False,
             "retention_s": 378.0, "drift_ms": 6.0, "drift_relative": 1.324},
            {"peak_id": 4, "rt_index": 4000, "dt_index": 1200, "intensity": 3000,
             "prominence": 2500, "flatness": 0.2, "edge_dist": 200, "saturated": False,
             "retention_s": 504.0, "drift_ms": 8.0, "drift_relative": 1.765},
            {"peak_id": 5, "rt_index": 5000, "dt_index": 2000, "intensity": 2000,
             "prominence": 1500, "flatness": 0.4, "edge_dist": 100, "saturated": False,
             "retention_s": 630.0, "drift_ms": 13.3, "drift_relative": 2.941},
        ],
    }


def test_identify_smoke():
    if not os.path.exists(MEA):
        import pytest
        pytest.skip(f"缺 .mea 測試檔 {MEA}")
    if library.resolve_data_dir() is None:
        import pytest
        pytest.skip("library.resolve_data_dir() 找不到資料夾")

    peaks_doc = _make_peaks_doc()

    # resolve_k0=False 是刻意的：本段鎖的是「**沒有** K0 校準時的行為」，而自從
    # K0 可從資料夾的 STD 自動解出之後，預設路徑已經拿得到 standard_based。少了這個
    # 參數，下面那些 k0 斷言測到的就不再是它們原本要守的那件事。
    # 預設行為（K0 自動解析）由 [1b] 另外驗。
    sep("[1] identify() 無 K0 校準（resolve_k0=False、default rules）")
    result = identify.identify(peaks_doc, MEA, resolve_k0=False)
    print(f"  n_in={result['n_peaks_in']}  n_out={result['n_peaks_out']}")
    print(f"  k0_mode_summary={result['k0_mode_summary']}")
    print(f"  ril_files={result['library_summary']['ril_files'][:3]}...")
    print(f"  iml_files={result['library_summary']['iml_files']}")
    print(f"  rules_report={result['rules_summary']}")
    print(f"  gc_dimensions_used={result['library_summary']['gc_dimensions_used']}")

    # default_config 只開 R004 → RIP 帶（peak_id=1）應被剔
    kept_ids = [p["peak_id"] for p in result["peaks"]]
    assert 1 not in kept_ids, "R004 應剔除 peak_id=1（落 RIP 帶）"
    # unavailable 模式 → 所有 k0_value 都是 None
    for p in result["peaks"]:
        assert p["k0_value"] is None
        assert p["k0_mode"] == "unavailable"
    # 每顆峰應有 matches 三分支
    for p in result["peaks"]:
        assert "gc_matches" in p["matches"]
        assert "ims_matches" in p["matches"]
        assert "combined_matches" in p["matches"]
        # k0_mode=unavailable 鎖住的是「K0 那條路不准被走」，**不是**「IMS 維度沒結果」。
        # match_drift_rel()（免 K0 的 RIPrel 漂移路線）加入後，沒有 K0 校準的峰仍可
        # 靠 drift_relative 在 IMS 軸上命中——這正是該功能存在的目的。
        # 本斷言先前寫成 ims_matches == []，那是把「K0 不可用」誤當成「IMS 不可用」；
        # 它當時會過只是因為 CLI 的 .iml 被管柱/極性篩掉、沒載到會命中的檔案，
        # 而 UI 早就載入全部 .iml——同一批資料兩條路徑結果不同。兩邊統一之後這個
        # 誤解就浮出來了。
        assert p["matches"]["ims_dimension"] != "k0", "無 K0 校準時不得走 K0 分支"
        assert all("k0" not in h.get("match_dimensions", [])
                   for h in p["matches"]["ims_matches"])

    sep("[1b] identify() 預設：K0 自資料夾 STD 自動解析")
    auto = identify.identify(peaks_doc, MEA)
    print(f"  k0_mode_summary={auto['k0_mode_summary']}")
    # 這批資料夾裡有可用的 STD，且 reference_series 的 ketone 帶 dt_values +
    # inv_k0_values，故應解出 standard_based 而非退回 unavailable。
    assert auto["k0_mode_summary"].get("standard_based"), (
        "資料夾內有可用 STD 時，K0 應自動解出 standard_based，"
        f"實得 {auto['k0_mode_summary']}")
    for p in auto["peaks"]:
        assert p["k0_mode"] == "standard_based"
        assert isinstance(p["k0_value"], float) and p["k0_value"] > 0, (
            "standard_based 模式下每顆峰都應有正的 K0 值")

    sep("[2] identify() 用 standard_based profile 模擬有校準常數")
    profile = {"profile_name": "test",
               "k0_calibration": {"mode": "standard_based", "instrument_constant": 20.0}}
    result2 = identify.identify(peaks_doc, MEA, profile=profile)
    for p in result2["peaks"]:
        assert p["k0_value"] is not None
        assert p["k0_mode"] == "standard_based"
    print(f"  standard_based k0 values: {[round(p['k0_value'], 4) for p in result2['peaks']]}")

    sep("[3] identify() 帶 --raw-tp 走 raw_parameters 模式")
    profile_raw = {"k0_calibration": {"mode": "raw_parameters"}}
    result3 = identify.identify(peaks_doc, MEA,
                                profile=profile_raw,
                                raw_tp={"T_C": 45.0, "P_mbar": 1003.93})
    for p in result3["peaks"]:
        assert p["k0_mode"] == "raw_parameters"
        assert p["k0_value"] is not None
    print(f"  raw_parameters k0 values: {[round(p['k0_value'], 4) for p in result3['peaks']]}")

    sep("[4] identify() 未給 raw_tp → 自動由表頭抽 T/P（2026-08-12 起）")
    # T/P 欄位對應已由 VOCal 反編譯確認（Start temp 1；壓力 = ambient + EPC 之和），
    # 所以呼叫端不必再手動指定。缺欄位才會退回 raw_parameters_missing_TP。
    result4 = identify.identify(peaks_doc, MEA, profile=profile_raw)  # no raw_tp
    modes = set(p["k0_mode"] for p in result4["peaks"])
    print(f"  k0 modes seen: {modes}")
    assert modes == {"raw_parameters"}
    assert all(p["k0_value"] is not None for p in result4["peaks"])

    sep("[5] rules_config 覆寫（關 R004、開 R001 threshold=2000）→ RIP 帶 peak 存活但弱峰被剔")
    my_rules = [
        {"rule_number": "R001", "enabled": True, "params": {"threshold": 2000}},
        {"rule_number": "R004", "enabled": False, "params": {}},
    ]
    result5 = identify.identify(peaks_doc, MEA, rules_config=my_rules)
    ids5 = [p["peak_id"] for p in result5["peaks"]]
    # 保留 prom >= 2000 者：id 1(4900), 2(3500), 3(3000), 4(2500) — 5(1500) 剔
    assert 1 in ids5 and 5 not in ids5
    print(f"  kept: {ids5}")

    print()
    print("[OK] all identify.py smoke checks passed")


def test_identify_stage4_ri():
    """第四階段 RT→RI 整合進 identify() 管線。"""
    if not os.path.exists(MEA):
        import pytest
        pytest.skip(f"缺 .mea 測試檔 {MEA}")
    if library.resolve_data_dir() is None:
        import pytest
        pytest.skip("library.resolve_data_dir() 找不到資料夾")

    import calibration as cal
    peaks_doc = _make_peaks_doc()

    sep("[R1] 自動解析：從批次資料夾三層解析（結構一致性，不假設特定結果）")
    res = identify.identify(peaks_doc, MEA)               # resolve_ri 預設 True
    ri_sum = res["ri_calibration_summary"]
    print(f"  ri_mode={ri_sum['ri_mode']}  ri_mode_summary={res['ri_mode_summary']}")
    assert ri_sum["ri_mode"] in {"batch_own_std", "borrowed_from_registry", "unavailable"}
    assert "ri_mode_summary" in res and "ri_source_summary" in res
    for p in res["peaks"]:
        assert "ri_mode" in p and "ri_source" in p
        # provenance 一致性：有絕對 RI ⇔ multi_point_loglinear；其餘 ri 應為 None
        if p["ri"] is not None:
            assert p["ri_mode"] == "multi_point_loglinear"
        elif p["ri_mode"] == "single_point_relative":
            assert p.get("ri_relative") is not None

    sep("[R2] 顯式相對校正 → 峰帶 ri_relative、ri=None、provenance 標記")
    rel = cal.build_calibration([100, 200, 300, 400, 500])   # series None → 相對
    res2 = identify.identify(peaks_doc, MEA, ri_calibration=rel, resolve_ri=False)
    for p in res2["peaks"]:
        assert p["ri"] is None
        assert p["ri_mode"] == "single_point_relative"
        assert p["ri_source"] == "provided"
        assert p["ri_relative"] is not None
    print(f"  ri_mode_summary={res2['ri_mode_summary']}")

    sep("[R3] 顯式 n_alkane 絕對校正 → 峰帶 ri 值 + assumed_unverified 一路傳遞")
    absol = cal.build_calibration([100, 200, 300, 400, 500], series_key="n_alkane")
    res3 = identify.identify(peaks_doc, MEA, ri_calibration=absol, resolve_ri=False)
    assert res3["ri_calibration_summary"]["assumed_unverified"] is True
    for p in res3["peaks"]:
        assert p["ri"] is not None
        assert p["ri_mode"] == "multi_point_loglinear"
        assert p["ri_assumed_unverified"] is True
    print(f"  RI 值: {[round(p['ri'], 1) for p in res3['peaks']]}")

    sep("[R4] --no-ri（resolve_ri=False 且無校正）→ RI unavailable")
    res4 = identify.identify(peaks_doc, MEA, resolve_ri=False)
    for p in res4["peaks"]:
        assert p["ri_mode"] == "unavailable"
    print("  RI 正確標記 unavailable")

    print()
    print("[OK] all identify.py Stage-4 RI checks passed")


if __name__ == "__main__":
    test_identify_smoke()
    test_identify_stage4_ri()

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

    sep("[1] identify() 全預設（unavailable K0、default rules）")
    result = identify.identify(peaks_doc, MEA)
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
        # unavailable → ims 空
        assert p["matches"]["ims_matches"] == []

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

    sep("[4] identify() 缺 raw_tp 時 raw_parameters 應標記 missing_TP")
    result4 = identify.identify(peaks_doc, MEA, profile=profile_raw)  # no raw_tp
    modes = set(p["k0_mode"] for p in result4["peaks"])
    print(f"  k0 modes seen: {modes}")
    assert "raw_parameters_missing_TP" in modes

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


if __name__ == "__main__":
    test_identify_smoke()

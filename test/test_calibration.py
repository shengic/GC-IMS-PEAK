"""
test_calibration.py — Smoke test for calibration.py + reference_series.py
（第四階段 RT→RI 轉換）。

雙用途：`pytest test/test_calibration.py` 或 `python test/test_calibration.py`。
使用 workflow §第四階段實測的真實 STD 峰資料（141215 的 7 峰、012251 的 3 峰）。
"""
import datetime
import json
import math
import os
import sys
import tempfile
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import calibration as cal
import reference_series as rs


def sep(t):
    print()
    print("=" * 60)
    print(t)
    print("=" * 60)


# workflow §第四階段第 5 點：141215_STD 實測 7 個候選峰（含 DT_rel＝drift_relative）
STD_141215 = [
    {"retention_s": 282.0, "intensity": 4937, "drift_relative": 1.104},
    {"retention_s": 334.3, "intensity": 2500, "drift_relative": 1.234},
    {"retention_s": 347.9, "intensity": 2025, "drift_relative": 1.104},  # DT_rel 與 282 同值
    {"retention_s": 400.3, "intensity": 3230, "drift_relative": 1.356},
    {"retention_s": 521.8, "intensity": 3189, "drift_relative": 1.487},
    {"retention_s": 697.0, "intensity": 2932, "drift_relative": 1.613},
    {"retention_s": 949.0, "intensity": 2213, "drift_relative": 1.737},
]
HDR_141215 = {"Status": "doubtful", "Status comment": "FlowGClow"}

# workflow §第四階段第 6 點：012251_STD 只 3 個峰、應排除
STD_012251 = [
    {"retention_s": 330.0, "intensity": 900},
    {"retention_s": 342.0, "intensity": 700},
    {"retention_s": 412.0, "intensity": 800},
]
HDR_012251 = {"Status": "doubtful",
              "Status comment": "FlowGClow;SeptumDurability;NoValidSnapshot"}


def test_relative_single_point_math():
    sep("[1] relative_single_point: log10(Rt/Rt_ref) 純函式")
    v = cal.relative_single_point(949.0, 949.0)
    assert abs(v) < 1e-12                       # 與參考點同 → 0
    v = cal.relative_single_point(282.0, 949.0)
    assert abs(v - (math.log10(282.0) - math.log10(949.0))) < 1e-12
    print(f"  282 vs ref949 → {v:.4f}（負值＝比參考早洗出）")


def test_anchor_selection_excludes_doublet():
    sep("[2] select_anchor_peaks: 5 乾淨錨點 + 隔離 334.3/347.9 雙峰")
    sel = cal.select_anchor_peaks(STD_141215)
    clean = [round(p["retention_s"], 1) for p in sel["clean_anchors"]]
    amb = [[round(p["retention_s"], 1) for p in g] for g in sel["ambiguous_groups"]]
    print(f"  clean = {clean}")
    print(f"  ambiguous = {amb}")
    assert clean == [282.0, 400.3, 521.8, 697.0, 949.0]
    assert amb == [[334.3, 347.9]]


def test_doublet_resolved_to_six_anchors():
    sep("[2b] draft.18 §4：DT_rel 均勻度把雙峰解析成 6 錨點（334.3 納入、347.9 剔除）")
    c, _ = cal.build_from_std_peaks(STD_141215, HDR_141215)
    clean = c["anchor_selection"]["clean_rt_s"]
    res = c["anchor_selection"]["doublet_resolution"]
    print(f"  clean(6)={clean}")
    print(f"  resolution={res}")
    assert clean == [282.0, 334.3, 400.3, 521.8, 697.0, 949.0]   # 6 點
    assert res[0]["chosen"] == 334.3
    assert res[0]["excluded_as_artifact"] == [347.9]             # DT_rel 與 282 同值


def test_default_is_relative_mode():
    sep("[3] 預設 series_key=None → single_point_relative（第 9 點，優先於烷烴假設）")
    c, _ = cal.build_from_std_peaks(STD_141215, HDR_141215)
    print(f"  mode={c['mode']}  known_ri_available={c['known_ri_available']}")
    assert c["mode"] == "single_point_relative"
    assert c["known_ri_available"] is False
    assert c["reference_rt_s"] == 949.0
    assert c["anchor_selection"]["n_clean_anchors"] == 6
    pk = [dict(p) for p in STD_141215]
    cal.attach_ri(pk, c)
    assert all(p["ri"] is None for p in pk)                 # 相對模式不指派絕對 RI
    assert all(p["ri_known_available"] is False for p in pk)
    assert pk[0]["ri_relative"] is not None


def test_alkane_upgrade_is_marked_assumed():
    sep("[4] --series n_alkane → 絕對 RI，且 assumed_unverified 隨每峰傳遞")
    c, _ = cal.build_from_std_peaks(STD_141215, HDR_141215, series_key="n_alkane")
    assert c["mode"] == "multi_point_loglinear"
    assert c["known_ri_available"] is True
    assert c["assumed_unverified"] is True
    # 雙峰解析後 6 錨點 → C6..C11
    assert c["ri_values"] == [600.0, 700.0, 800.0, 900.0, 1000.0, 1100.0]
    print(f"  anchor RI = {c['ri_values']}（假設 C6 起算，6 錨點）")
    pk = [dict(p) for p in STD_141215]
    cal.attach_ri(pk, c)
    # 每個錨點自身內插回自己的 RI（334.3 現在是錨點 → 700）
    anchor_ri = {282.0: 600, 334.3: 700, 400.3: 800, 521.8: 900, 697.0: 1000, 949.0: 1100}
    for p in pk:
        if p["retention_s"] in anchor_ri:
            assert abs(p["ri"] - anchor_ri[p["retention_s"]]) < 1e-6
        assert p["ri_assumed_unverified"] is True         # provenance 一路帶著走
    print("  每峰皆帶 ri_assumed_unverified=True")


def test_custom_series_not_assumed():
    sep("[5] --series custom + 實測 RI → known 且非假設（assumed_unverified=False）")
    c, _ = cal.build_from_std_peaks(
        STD_141215, HDR_141215, series_key="custom",
        ri_values=[601.2, 651.0, 702.5, 803.1, 905.9, 1004.4])   # 6 值對齊 6 錨點
    assert c["mode"] == "multi_point_loglinear"
    assert c["known_ri_available"] is True
    assert c["assumed_unverified"] is False
    print(f"  custom RI = {c['ri_values']}  assumed_unverified={c['assumed_unverified']}")


def test_012251_rejected_by_quality_gate():
    sep("[6] 012251：品質前置過濾應排除（第 6 點）")
    c, _ = cal.build_from_std_peaks(STD_012251, HDR_012251, reference_n_anchors=5)
    print(f"  mode={c['mode']}  usable={c['std_quality']['usable']}")
    print(f"  reasons={c['std_quality']['reasons']}")
    assert c["mode"] == "unavailable"
    assert c["std_quality"]["usable"] is False


def test_loglinear_interpolation_kovats():
    sep("[7] multi_point_loglinear: log10(Rt) 分段線性內插/外插")
    c = cal.build_calibration([282.0, 400.3, 521.8, 697.0, 949.0],
                              series_key="n_alkane")
    fn = cal.get_interp_fn(c)
    # 兩錨點之間手算 Kovats log 形式核對
    rt = 450.0
    lo, hi = 400.3, 521.8
    expect = 700 + 100 * (math.log10(rt) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
    got = float(fn(math.log10(rt)))
    print(f"  RT=450 → RI={got:.3f}  手算={expect:.3f}")
    assert abs(got - expect) < 1e-6


def test_methyl_ketone_borrowed_ri_is_absolute_but_flagged():
    sep("[9] methyl_ketone_RI_provenance.md：借用 6 點 RI → 絕對模式 + assumed_unverified")
    mk = rs.REFERENCE_SERIES["methyl_ketone"]
    assert mk["carbon_numbers"] == [4, 5, 6, 7, 8, 9]
    assert mk["members"][0] == "2-butanone" and mk["members"][-1] == "2-nonanone"
    assert mk["ri_values"] == [589.4, 688.6, 784.2, 892.2, 996.5, 1095.6]  # 借用值已填
    assert rs.series_is_assumed("methyl_ketone") is True         # 借用未驗證
    assert rs.series_confidence("methyl_ketone") == "borrowed_cross_referenced"
    # 模板只借 RI(Y)，Rt(X) 用本批 STD 實測值
    tmpl = cal.template_from_series(
        [282.0, 334.3, 400.3, 521.8, 697.0, 949.0], "methyl_ketone")
    assert tmpl[0]["ri"] == 589.4 and tmpl[-1]["ri"] == 1095.6
    # 釘定 6 峰 + 借用 RI → 絕對 RI，但 provenance 全程帶著
    c, _ = cal.build_from_std_peaks(STD_141215, HDR_141215,
                                    series_key="methyl_ketone", expected_anchors=tmpl)
    print(f"  mode={c['mode']}  assumed={c['assumed_unverified']}  conf={c.get('ri_confidence')}")
    assert c["mode"] == "multi_point_loglinear"
    assert c["assumed_unverified"] is True
    assert c["ri_confidence"] == "borrowed_cross_referenced"
    # 套到樣品峰：ri 有值、且每峰帶 ri_confidence
    pk = [dict(p) for p in STD_141215]
    cal.attach_ri(pk, c)
    assert all(p["ri"] is not None for p in pk)
    assert all(p["ri_confidence"] == "borrowed_cross_referenced" for p in pk)


def test_interp_ALGORITHM_only_rt450_NOT_calibration_validation():
    # draft.21 line 55 / 第16點：此測試只驗「log10 轉換 + 區間搜尋 + 加權內插」三步驟
    # 寫對沒有；RI 值 400..900 是純示範算術，**不是真實甲基酮校準值**，不得被誤讀為
    # 校準已驗證（真實 RI 仍待補，見 workflow §4 第 13 點）。
    sep("[10] 內插『演算法』ground truth（非校準驗證！示範 RI）：RT=450 → RI≈644.1")
    rts = [282.0, 334.3, 400.3, 521.8, 697.0, 949.0]
    ri = [400, 500, 600, 700, 800, 900]              # 示範算術值，非真實 RI
    c = cal.build_calibration(rts, series_key="custom", ri_values=ri)
    got, extrap = cal.make_rt_to_ri(c)(450)          # 走共用工廠
    print(f"  RT=450 → RI={got:.1f}（手算 644.1）extrapolated={extrap}")
    assert abs(got - 644.1) < 0.2                     # 0.1 為 md 手算捨入誤差
    assert extrap is False


def test_shared_rt_to_ri_factory_used_everywhere():
    sep("[12] draft.21 清單第7點：attach_ri 與 ri_yticks 共用同一 RT→RI 工廠（不各自為政）")
    c = cal.build_calibration([282.0, 400.3, 521.8, 697.0, 949.0],
                              series_key="custom", ri_values=[600, 700, 800, 900, 1000])
    r2r = cal.make_rt_to_ri(c)
    # (a) attach_ri 的 RI/旗標必須逐一等於共用工廠
    p = [{"retention_s": 450.0}, {"retention_s": 100.0}, {"retention_s": 1500.0}]
    cal.attach_ri(p, c)
    for pk in p:
        ri_s, ex_s = r2r(pk["retention_s"])
        assert abs(pk["ri"] - ri_s) < 1e-9
        assert pk["ri_extrapolated"] == ex_s
    # (b) ri_yticks 的軸刻度也走同一工廠：r2r(刻度RT位置) ≈ 該刻度 RI 標籤
    rt_pos, labels = cal.ri_yticks(c, 200, 1100, step=100)
    for pos, lab in zip(rt_pos, labels):
        assert abs(r2r(pos)[0] - float(lab.rstrip("*"))) < 2.0
    print("  attach_ri 與 ri_yticks 對同一 RT 得到同一 RI ✓")


def test_extrapolate_and_flag():
    sep("[11] draft.20 決策：錨點外『外插 + 標記』——RI 值與 ri_extrapolated 兩份資訊都留")
    c = cal.build_calibration([282.0, 400.3, 521.8, 697.0, 949.0],
                              series_key="custom", ri_values=[600, 700, 800, 900, 1000])
    peaks = [{"retention_s": 450.0},    # 範圍內 → 內插
             {"retention_s": 100.0},    # < 首錨 282 → 外插
             {"retention_s": 1500.0}]   # > 末錨 949 → 外插
    cal.attach_ri(peaks, c)
    for p in peaks:
        print(f"  RT={p['retention_s']:>7}  RI={p['ri']:.1f}  extrapolated={p['ri_extrapolated']}")
    assert peaks[0]["ri_extrapolated"] is False
    assert peaks[1]["ri_extrapolated"] is True and peaks[1]["ri"] is not None   # 值仍保留
    assert peaks[2]["ri_extrapolated"] is True and peaks[2]["ri"] is not None


def test_reference_series_assign():
    sep("[8] reference_series.assign_ri: 系列可插拔 + 起始碳數可覆寫")
    assert rs.assign_ri(5, "n_alkane") == [600, 700, 800, 900, 1000]
    assert rs.assign_ri(3, "n_alkane", start_carbon=8) == [800, 900, 1000]
    assert rs.series_is_assumed("n_alkane") is True
    assert rs.series_is_assumed("custom") is False
    # methyl_ketone 現在有 6 點借用 RI（methyl_ketone_RI_provenance.md）
    assert rs.assign_ri(6, "methyl_ketone") == [589.4, 688.6, 784.2, 892.2, 996.5, 1095.6]
    try:
        rs.assign_ri(3, "methyl_ketone")           # 錨點數不符（6≠3）→ ValueError
        assert False, "應丟 ValueError"
    except ValueError:
        print("  methyl_ketone 錨點數不符時正確丟 ValueError")


# --------------------------------------------------------------------------- #
# 純合成資料：證明「邏輯」與任何特定 STD 批次無關（不援引 282/334/949 等實測值）
# --------------------------------------------------------------------------- #
def _syn(rts, intensity=3000):
    """把一串保留時間做成峰 dict（強度相同且高於品質門檻 → 只驗分群邏輯）。"""
    return [{"retention_s": float(r), "intensity": intensity} for r in rts]


def test_syn_doublet_grouping_general():
    sep("[S1] 合成：任何過近的相鄰峰都被雙雙隔離，不限特定批次")
    # 100/105 相距 5 < gap；其餘間距皆足夠 → 兩者一起進 ambiguous，都不當錨點
    sel = cal.select_anchor_peaks(_syn([100, 105, 300, 500, 900]))
    clean = [p["retention_s"] for p in sel["clean_anchors"]]
    amb = [[p["retention_s"] for p in g] for g in sel["ambiguous_groups"]]
    print(f"  clean={clean}  ambiguous={amb}")
    assert clean == [300.0, 500.0, 900.0]
    assert amb == [[100.0, 105.0]]                 # 兩個都被隔離（無法判定誰是真錨）


def test_syn_multiple_doublets_and_triplet():
    sep("[S2] 合成：多組近距群組 + 三連峰都能一併隔離")
    sel = cal.select_anchor_peaks(_syn([50, 55, 200, 400, 405, 410, 800]))
    clean = [p["retention_s"] for p in sel["clean_anchors"]]
    amb = [[p["retention_s"] for p in g] for g in sel["ambiguous_groups"]]
    print(f"  clean={clean}  ambiguous={amb}")
    assert clean == [200.0, 800.0]
    assert amb == [[50.0, 55.0], [400.0, 405.0, 410.0]]


def test_syn_variable_anchor_count():
    sep("[S3] 合成：錨點數不寫死 —— 2 個、8 個都能建曲線")
    c2 = cal.build_calibration([120, 640], series_key="n_alkane")
    assert c2["mode"] == "multi_point_loglinear" and c2["n_anchors"] == 2
    assert c2["ri_values"] == [600.0, 700.0]
    c8 = cal.build_calibration(list(range(100, 900, 100)), series_key="n_alkane")
    assert c8["n_anchors"] == 8
    assert c8["ri_values"][-1] == 600.0 + 100 * 7
    print(f"  2 錨點 RI={c2['ri_values']} ; 8 錨點末值 RI={c8['ri_values'][-1]}")


def test_syn_gap_threshold_is_tunable():
    sep("[S4] 合成：doublet_gap_s 是可調參數，非批次寫死的常數")
    peaks = _syn([100, 130, 400])                  # 100↔130 相距 30
    tight = cal.select_anchor_peaks(peaks, doublet_gap_s=20)   # 30 ≥ 20 → 都算乾淨
    wide = cal.select_anchor_peaks(peaks, doublet_gap_s=50)    # 30 < 50 → 100/130 成雙峰
    assert len(tight["clean_anchors"]) == 3
    assert len(wide["clean_anchors"]) == 1 and len(wide["ambiguous_groups"]) == 1
    print("  同一組峰，門檻不同 → 分群不同，證明是參數不是硬編碼")


def test_syn_quality_gate_general():
    sep("[S5] 合成：品質門檻對任意資料生效（少於 min_anchors → 不可用）")
    c, _ = cal.build_from_std_peaks(_syn([500]), header={})     # 只 1 個峰
    assert c["mode"] == "unavailable"
    assert c["std_quality"]["usable"] is False
    c2, _ = cal.build_from_std_peaks(_syn([200, 400, 600]), header={})
    assert c2["mode"] == "single_point_relative"               # 3 個 → 可用、走相對
    print(f"  1 峰→{c['mode']} ; 3 峰→{c2['mode']}")


def test_syn_doublet_resolution_general():
    sep("[S6] 合成：DT_rel 解析邏輯通用 —— 重複 DT_rel 剔為偽影、其餘挑最均勻者")
    # 乾淨錨點 DT_rel 遞增 1.1/1.3/1.5；雙峰在 200/210：
    #   210 的 DT_rel=1.1 與 100 那顆重複 → 判偽影剔除
    #   200 的 DT_rel=1.2 落在階梯上 → 納入
    peaks = [
        {"retention_s": 100, "intensity": 5000, "drift_relative": 1.1},
        {"retention_s": 200, "intensity": 3000, "drift_relative": 1.2},
        {"retention_s": 210, "intensity": 2000, "drift_relative": 1.1},
        {"retention_s": 400, "intensity": 3000, "drift_relative": 1.3},
        {"retention_s": 600, "intensity": 3000, "drift_relative": 1.5},
    ]
    c, _ = cal.build_from_std_peaks(peaks, header={})
    clean = c["anchor_selection"]["clean_rt_s"]
    res = c["anchor_selection"]["doublet_resolution"]
    print(f"  clean={clean}  resolution={res}")
    assert clean == [100.0, 200.0, 400.0, 600.0]         # 210 剔除、200 納入
    assert res[0]["chosen"] == 200.0
    assert res[0]["excluded_as_artifact"] == [210.0]


def test_syn_ri_yticks_axis_relabel():
    sep("[S8] ri_yticks：絕對模式回 (RT位置, RI標記)；相對模式回 None")
    cabs = cal.build_calibration([100, 200, 300, 400, 500], series_key="n_alkane")
    ticks = cal.ri_yticks(cabs, 100, 500, step=100)
    assert ticks is not None
    rt_pos, labels = ticks
    print(f"  labels={labels}  rt_pos≈{[round(x) for x in rt_pos]}")
    assert labels == ["600", "700", "800", "900", "1000"]     # 錨點範圍內 → 無星號
    assert all(rt_pos[i] < rt_pos[i + 1] for i in range(len(rt_pos) - 1))  # RT 遞增
    assert abs(rt_pos[0] - 100) < 5 and abs(rt_pos[-1] - 500) < 5          # 落在錨點上
    # 外插 + 標記（draft.20）：更寬 RT 範圍 → 錨點外刻度帶 '*'
    _, labels2 = cal.ri_yticks(cabs, 50, 700, step=100)
    print(f"  extrapolated labels={labels2}")
    assert any(l.endswith("*") for l in labels2)              # 有外插刻度被標記
    assert any(not l.endswith("*") for l in labels2)          # 範圍內的不標記
    # 相對模式沒有絕對 RI → 不能標 RI 軸
    crel = cal.build_calibration([100, 200, 300])              # series None → 相對
    assert cal.ri_yticks(crel, 100, 300) is None


def test_pin_to_six_ketone_anchors():
    sep("[P1] 釘定：7 個候選 → 對齊 6 個甲基酮模板，多的 347.9 被忽略")
    expected = cal.template_from_series(
        [282.0, 334.3, 400.3, 521.8, 697.0, 949.0], "methyl_ketone")
    assert [e["label"] for e in expected][:2] == ["2-butanone", "2-pentanone"]
    c, _ = cal.build_from_std_peaks(STD_141215, HDR_141215,
                                    series_key="methyl_ketone",
                                    expected_anchors=expected)
    asel = c["anchor_selection"]
    print(f"  anchor_mode={asel['mode']}  n={asel['n_clean_anchors']}  rt={asel['clean_rt_s']}")
    assert asel["mode"] == "pinned"
    assert asel["n_clean_anchors"] == 6                 # 固定為模板數，347.9 被丟
    assert 347.9 not in asel["clean_rt_s"]
    # methyl_ketone 已有借用 RI → 絕對模式（provenance 帶 assumed_unverified）
    assert c["mode"] == "multi_point_loglinear"
    assert c["assumed_unverified"] is True


def test_pin_absolute_when_template_has_ri():
    sep("[P2] 釘定 + custom 模板帶 RI → 絕對 RI，assumed_unverified=False（使用者實測值）")
    expected = cal.template_from_series(
        [282.0, 334.3, 400.3, 521.8, 697.0, 949.0], "custom",
        ri_values=[600, 700, 800, 900, 1000, 1100])     # custom＝使用者直接給值、非假設
    c, _ = cal.build_from_std_peaks(STD_141215, HDR_141215,
                                    series_key="custom",
                                    expected_anchors=expected)
    assert c["mode"] == "multi_point_loglinear"
    assert c["assumed_unverified"] is False
    assert c["ri_values"] == [600, 700, 800, 900, 1000, 1100]


def test_syn_pin_general_and_missing():
    sep("[P3] 合成：釘定通用 —— 多的忽略、模板容差內找不到 → 明確標 missing")
    detected = _syn([100, 105, 300, 500, 900])
    # 模板要 100/300/700；700 在 detected 容差內沒有 → missing
    expected = [{"rt_s": 100, "label": "a"}, {"rt_s": 300, "label": "b"},
                {"rt_s": 700, "label": "c"}]
    pinned, report = cal.pin_anchors(detected, expected, tol_s=20)
    got = [p["retention_s"] for p in pinned]
    print(f"  pinned={got}  report={report}")
    assert got == [100.0, 300.0]                        # 105/500/900 忽略
    assert report[2]["matched_rt"] is None and "缺" in report[2]["note"]


def test_syn_no_dt_rel_stays_quarantined():
    sep("[S7] 合成：無 DT_rel 時不硬猜，雙峰維持隔離（不納入錨點）")
    peaks = _syn([100, 108, 300, 500])                   # 無 drift_relative
    c, _ = cal.build_from_std_peaks(peaks, header={})
    clean = c["anchor_selection"]["clean_rt_s"]
    print(f"  clean={clean}  resolution={c['anchor_selection']['doublet_resolution']}")
    assert clean == [300.0, 500.0]                        # 100/108 雙峰未解析、被隔離


# --------------------------------------------------------------------------- #
# 批次資料夾三層解析 + registry + 快取（draft.18 §12 / code-ref §9–10）
# --------------------------------------------------------------------------- #
def _make_mea(path, sample, method="COFFEE-40RAW"):
    """造一個最小 .mea：ASCII 表頭數行 + 終止控制字元 + 假二進位。"""
    lines = [
        f"Sample = {sample}",
        "Machine type = FlavourSpec",
        "Machine serial = 5H4-00123",
        "GC Column = FS-SE-54",
        f"Program = Name=`{method}`|Avges=6",
        "nom Drift Tube Length = 53000",
    ]
    blob = ("\n".join(lines) + "\n").encode("latin-1") + b"\x00\x00BIN"
    with open(path, "wb") as f:
        f.write(blob)


DIMS = {"instrument": "FlavourSpec 5H4-00123", "column": "FS-SE-54", "method": "COFFEE-40RAW"}


def test_scan_folder_for_std_by_header():
    sep("[R1] scan_folder_for_std：依表頭 Sample=='STD' 找檔，非檔名")
    with tempfile.TemporaryDirectory() as d:
        _make_mea(os.path.join(d, "a_sample.mea"), "FM_1")
        _make_mea(os.path.join(d, "z_std.mea"), "STD")     # 檔名不含慣例也能認出
        _make_mea(os.path.join(d, "b_blank.mea"), "BLK")
        stds = cal.scan_folder_for_std(d)
        print(f"  found = {[os.path.basename(x) for x in stds]}")
        assert [os.path.basename(x) for x in stds] == ["z_std.mea"]


def test_resolve_tier_a_batch_own_std():
    sep("[R2] 三層(a)：批次內有可用 STD → ri_mode=batch_own_std")
    cal.clear_session_cache()
    with tempfile.TemporaryDirectory() as d:
        _make_mea(os.path.join(d, "run_std.mea"), "STD")
        c, mode, detail = cal.resolve_ri_calibration(
            d, std_peaks_loader=lambda p: (STD_141215, HDR_141215))
        print(f"  ri_mode={mode}  cal.mode={c['mode']}  anchors={c['anchor_selection']['n_clean_anchors']}")
        assert mode == "batch_own_std"
        assert c["ri_mode"] == "batch_own_std"
        assert c["mode"] == "single_point_relative"        # 預設無系列 → 相對
        assert c["anchor_selection"]["n_clean_anchors"] == 6


def test_resolve_tier_b_borrowed_with_days_gap():
    sep("[R3] 三層(b)：無 STD 但 registry 命中 → borrowed_from_registry + days_gap")
    with tempfile.TemporaryDirectory() as d:
        _make_mea(os.path.join(d, "only_sample.mea"), "FM_1")    # 無 STD
        stored = cal.build_calibration([282, 400, 520, 700, 950], series_key="n_alkane")
        reg_path = os.path.join(d, "registry.json")
        cal.save_registry(reg_path, {
            cal.registry_key(**DIMS): {"built_date": "2026-06-01", "calibration": stored}})
        c, mode, detail = cal.resolve_ri_calibration(
            d, dims=DIMS, registry_path=reg_path,
            today_date=datetime.date(2026, 7, 27))
        print(f"  ri_mode={mode}  days_gap={detail['days_gap']}  note={c['ri_confidence_note']}")
        assert mode == "borrowed_from_registry"
        assert detail["days_gap"] == 56
        assert "days_gap=56" in c["ri_confidence_note"]


def test_resolve_tier_b_days_gap_exceeded_is_unavailable():
    sep("[R4] 三層(b)：days_gap 超過上限 → 視同不可用")
    with tempfile.TemporaryDirectory() as d:
        _make_mea(os.path.join(d, "only_sample.mea"), "FM_1")
        stored = cal.build_calibration([282, 400, 520, 700, 950], series_key="n_alkane")
        reg_path = os.path.join(d, "registry.json")
        cal.save_registry(reg_path, {
            cal.registry_key(**DIMS): {"built_date": "2026-06-01", "calibration": stored}})
        c, mode, detail = cal.resolve_ri_calibration(
            d, dims=DIMS, registry_path=reg_path, max_days_gap=30,
            today_date=datetime.date(2026, 7, 27))
        print(f"  ri_mode={mode}  note_b={detail.get('note_b')}")
        assert mode == "unavailable" and c is None


def test_resolve_tier_c_unavailable():
    sep("[R5] 三層(c)：無 STD、registry 也無 → unavailable")
    cal.clear_session_cache()
    with tempfile.TemporaryDirectory() as d:
        _make_mea(os.path.join(d, "only_sample.mea"), "FM_1")
        c, mode, detail = cal.resolve_ri_calibration(d, dims=DIMS,
                                                     registry_path=os.path.join(d, "none.json"))
        print(f"  ri_mode={mode}")
        assert mode == "unavailable" and c is None


def test_resolve_tier_a_all_std_unusable_falls_to_registry():
    sep("[R6] 三層：STD 存在但皆不可用 → 落到 registry 借用")
    with tempfile.TemporaryDirectory() as d:
        _make_mea(os.path.join(d, "bad_std.mea"), "STD")
        # loader 回訊號缺失的壞 STD（只 1 峰、弱）→ 不可用
        bad = [{"retention_s": 400, "intensity": 500}]
        stored = cal.build_calibration([282, 400, 520, 700, 950], series_key="n_alkane")
        reg_path = os.path.join(d, "registry.json")
        cal.save_registry(reg_path, {
            cal.registry_key(**DIMS): {"built_date": "2026-07-01", "calibration": stored}})
        c, mode, detail = cal.resolve_ri_calibration(
            d, dims=DIMS, registry_path=reg_path,
            std_peaks_loader=lambda p: (bad, {}),
            today_date=datetime.date(2026, 7, 27))
        print(f"  ri_mode={mode}  note_a={detail.get('note_a')}")
        assert mode == "borrowed_from_registry"


def test_folder_cache_sidecar_and_session():
    sep("[R7] §10 快取：寫 sidecar + session 命中")
    cal.clear_session_cache()
    with tempfile.TemporaryDirectory() as d:
        _make_mea(os.path.join(d, "run_std.mea"), "STD")
        loader = lambda p: (STD_141215, HDR_141215)
        r1 = cal.resolve_ri_calibration_cached(d, std_peaks_loader=loader)
        sidecar = os.path.join(d, "_folder_calibration.json")
        assert os.path.exists(sidecar)                     # 寫了 sidecar
        r2 = cal.resolve_ri_calibration_cached(d, std_peaks_loader=loader)
        assert r2 is r1                                    # session 命中同一物件
        cal.clear_session_cache()
        r3 = cal.resolve_ri_calibration_cached(d, std_peaks_loader=loader)
        assert r3[1] == "batch_own_std"                    # 清 session 後從 sidecar 讀
        print("  sidecar 建立、session 命中、清後由 sidecar 復原 ✓")


def test_syn_warp_rows_to_ri_linear():
    sep("[S9] warp_rows_to_ri：RT 均勻列 → RI 均勻列（RI 軸線性，非 log）")
    import numpy as np
    c = cal.build_calibration([282, 334.3, 400.3, 521.8, 697, 949],
                              series_key="methyl_ketone")
    r2r = cal.make_rt_to_ri(c)
    row_rts = np.linspace(300, 940, 200)             # 均勻於 RT，落在錨點範圍內
    img = np.array([[r2r(t)[0]] for t in row_rts])   # 每列值＝該列的 RI，方便驗線性
    w, lo, hi = cal.warp_rows_to_ri(img, row_rts, c)
    assert w.shape == img.shape and lo is not None
    steps = np.diff(w[:, 0])
    assert np.all(steps > 0)                          # 單調
    assert steps.std() / steps.mean() < 0.05         # 近乎等距 → RI 軸線性
    print(f"  RI extent {lo:.0f}..{hi:.0f}, step CV={steps.std()/steps.mean():.4f}")
    # 相對模式沒有絕對 RI → 不重採樣
    w2, lo2, _ = cal.warp_rows_to_ri(img, row_rts, cal.build_calibration([100, 200, 300]))
    assert lo2 is None and w2 is img


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\n✓ 全部通過")


if __name__ == "__main__":
    _run_all()

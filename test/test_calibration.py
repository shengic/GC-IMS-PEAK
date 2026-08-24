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


# workflow §第四階段第 5 點：141215_STD 的 7 個候選峰（含 DT_rel＝drift_relative）
#
# ⚠ **這組是修正前保留時間軸的歷史快照**（rt_step 少了 +1，見 readGAS.RT_AXIS_VERSION），
# 而且是比 _STD_REAL 更早一次偵測的結果（強度略有出入：282.0/4937 vs 282.5/4936）。
# 刻意**不**換算成新軸：它的用途是驗證 select_anchor_peaks() / resolve_anchor_doublets() /
# select_homolog_ladder() 這幾條**已降為退路**的啟發式路徑，那些邏輯看的是 DT_rel 間距
# 與 RT 相對間隔，換算後所有斷言都要跟著改，徒增風險而不增加涵蓋率。
# 描述「本批 STD 現況」的資料請用下方的 _STD_REAL（新軸、已對齊經理對照表）。
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


def test_ketone_std_table_is_a_faithful_transcription():
    """[draft.24] KETONE_STD_TABLE 是 kintonemixed-C4-C9.xlsx 的逐列硬編碼。

    硬編碼的參照資料最大的風險是「抄錯了沒人發現」，所以這裡驗的是**內部一致性**
    ——每一列自己的欄位彼此對得起來，而且六列構成一條合理的同系物序列。這些檢查
    不需要原檔在場（原檔可能不在，或被 Excel 鎖住），但任何一處手滑都會被抓到：

      - 分子式的碳數要等於 carbon 欄（C9H18O → 9）
      - 分子式要符合酮的通式 CnH2nO
      - MW 由分子式算出來要對得上（±0.15）
      - 依碳數遞增排序後，RI / Rt / Dt 三者都必須嚴格遞增（同系物的物理必然）
    """
    import re
    t = rs.KETONE_STD_TABLE
    assert len(t) == 6
    assert [r["count"] for r in t] == [1, 2, 3, 4, 5, 6], "應保留原檔列序"

    for r in t:
        m = re.fullmatch(r"C(\d+)H(\d+)O", r["formula"])
        assert m, f"{r['compound']} 的分子式格式異常：{r['formula']}"
        n_c, n_h = int(m.group(1)), int(m.group(2))
        assert n_c == r["carbon"], f"{r['compound']}：分子式碳數 {n_c} ≠ carbon {r['carbon']}"
        assert n_h == 2 * n_c, f"{r['compound']}：不符合酮通式 CnH2nO"
        # 12.011*C + 1.008*H + 15.999*O
        mw_calc = 12.011 * n_c + 1.008 * n_h + 15.999
        assert abs(mw_calc - r["mw"]) < 0.15, \
            f"{r['compound']}：MW {r['mw']} 與分子式算出的 {mw_calc:.2f} 不符"

    asc = sorted(t, key=lambda r: r["carbon"])
    assert [r["carbon"] for r in asc] == [4, 5, 6, 7, 8, 9]
    for field in ("ri", "rt_s", "dt"):
        vals = [r[field] for r in asc]
        assert all(vals[i] < vals[i + 1] for i in range(5)), \
            f"{field} 未隨碳數嚴格遞增（同系物應遞增）：{vals}"

    # 對外欄位確實由該表導出、且順序一致（不是另一份手打的平行陣列）
    k = rs.REFERENCE_SERIES["ketone"]
    assert k["ri_values"] == [r["ri"] for r in asc]
    assert k["dt_values"] == [r["dt"] for r in asc]
    assert k["members"] == [r["compound"].lower() for r in asc]
    assert k["source_file"] == "kintonemixed-C4-C9.xlsx"


def test_ketone_identity_confirmed_by_supplier_table():
    """[draft.24] 身分由經理對照表（kintonemixed-C4-C9.xlsx）確認，CAS 逐一核對。

    draft.23 曾把成員改成不宣稱結構的 'C4 ketone'…（因為當時「2-alkanone」只是本專案
    的推論）。draft.24 拿到帶 CAS 的對照表後，該推論獲得外部證實，故改回具體化合物名。
    這個測試鎖住的是：**身分宣告必須有 CAS 撐著**——六個成員名與六個 CAS 一一對應且
    數量相符，才不會退回成「憑階梯形狀猜化合物」的狀態。
    """
    k = rs.REFERENCE_SERIES["ketone"]
    assert k["members"] == ["2-butanone", "2-pentanone", "2-hexanone",
                            "2-heptanone", "2-octanone", "2-nonanone"]
    # CAS 是身分宣告的依據，缺一不可
    assert k["cas_numbers"] == ["C78933", "C107879", "C591786",
                                "C110430", "C111137", "C821556"]
    for field in ("members", "cas_numbers", "formulas",
                  "molecular_weights", "carbon_numbers", "ri_values", "dt_values"):
        assert len(k[field]) == 6, f"{field} 長度必須為 6"
    # 身分已確認，但 assumed 仍為 True——指向的是管柱極性疑慮，不是化合物身分
    assert rs.series_is_assumed("ketone") is True
    assert "polarity" in rs.series_confidence("ketone")


# --------------------------------------------------------------------------- #
# draft.24：用對照表 Dt 指派錨點（取代間距啟發式）
# --------------------------------------------------------------------------- #
# 141215_STD 的真實偵測峰（節錄，含足以構成陷阱的干擾峰）。寫成字面值而非讀
# results/，測試才不依賴 gitignore 掉的資料。
#
# ⚠ RT 為**修正後的保留時間軸**（rt_index × 0.147 s，即 (averages+1)×trigger）。
# 2026-08-12 之前這些值是 6/7 倍（282.5 / 334.0 / … / 1305.7），若在舊產物裡看到
# 那組數字，那是修正前的軸，不是不同的峰——見 readGAS.RT_AXIS_VERSION。
# DT_rel 不受該修正影響，本測試的配對邏輯也只看 DT_rel。
_STD_REAL = [
    {"retention_s": 329.6,  "drift_relative": 1.104, "intensity": 4936},  # 最強峰，非錨點
    {"retention_s": 389.7,  "drift_relative": 1.234, "intensity": 2498},  # C4
    {"retention_s": 400.3,  "drift_relative": 1.104, "intensity": 2071},  # 同 DT_rel 干擾
    {"retention_s": 467.0,  "drift_relative": 1.356, "intensity": 3230},  # C5
    {"retention_s": 609.5,  "drift_relative": 1.487, "intensity": 3189},  # C6
    {"retention_s": 813.4,  "drift_relative": 1.613, "intensity": 2927},  # C7
    {"retention_s": 1107.2, "drift_relative": 1.737, "intensity": 2213},  # C8
    {"retention_s": 1523.4, "drift_relative": 1.854, "intensity": 1028},  # C9（啟發式漏掉）
    {"retention_s": 1526.0, "drift_relative": 1.406, "intensity": 1188},
]


def test_dt_match_finds_all_six_including_the_one_the_ladder_missed():
    """Dt 配對必須挑出正確的六點，而且**不能**選中那顆最強的干擾峰。

    這是 draft.24 的核心修正。啟發式挑的是 [329.6,389.7,467.0,609.5,813.4,1107.2]——它把
    全圖最強峰（329.6, DT_rel 1.104）當成 C4，並漏掉 RT 1523.4 的 C9。Dt 配對有外部依據，
    不受「哪顆比較強」「間距像不像等差」影響。
    """
    dt = rs.REFERENCE_SERIES["ketone"]["dt_values"]
    matched, rep = cal.match_anchors_by_dt(_STD_REAL, dt, tol=0.01)
    rts = [round(p["retention_s"], 1) for p in matched]
    assert rts == [389.7, 467.0, 609.5, 813.4, 1107.2, 1523.4]
    assert 329.6 not in rts, "全圖最強峰不屬於此序列，不得被選為錨點"
    assert rep["n_matched"] == 6 and rep["missing_indices"] == []
    assert rep["rt_monotonic_with_carbon"] is True
    assert rep["mean_abs_delta"] < 0.006
    # 碳數序位要跟著 RT 走（第 i 個錨點 = 第 i 個化合物）
    assert [p["_dt_index"] for p in matched] == [0, 1, 2, 3, 4, 5]


def test_dt_match_beats_the_spacing_heuristic_on_real_data():
    """同一份資料，兩種挑法給出不同答案——證明換掉啟發式是有意義的，不是重構。"""
    dt = rs.REFERENCE_SERIES["ketone"]["dt_values"]
    by_dt, _ = cal.match_anchors_by_dt(_STD_REAL, dt, tol=0.01)
    by_ladder, _ = cal.select_homolog_ladder(_STD_REAL, len(dt))
    a = [round(p["retention_s"], 1) for p in by_dt]
    b = [round(p["retention_s"], 1) for p in by_ladder]
    assert a != b, "若兩者一致，代表資料或啟發式變了，本測試的前提要重新檢視"
    assert 329.6 in b and 1523.4 not in b, "啟發式會納入最強干擾峰並漏掉 C9"


def test_dt_match_partial_hit_keeps_ri_aligned():
    """只配到一部分時，RI 必須跟著配到的化合物走，不能整組錯位。"""
    dt = rs.REFERENCE_SERIES["ketone"]["dt_values"]
    ri = rs.REFERENCE_SERIES["ketone"]["ri_values"]
    subset = [p for p in _STD_REAL if round(p["retention_s"], 1) in (467.0, 813.4, 1523.4)]
    c, _ = cal.build_from_std_peaks(subset, header={}, series_key="ketone")
    assert c["mode"] == "multi_point_loglinear"
    assert c["n_anchors"] == 3
    # C5 / C7 / C9 → ri_values 的第 1、3、5 項
    assert c["ri_values"] == [ri[1], ri[3], ri[5]]


def test_dt_match_falls_back_when_series_has_no_dt():
    """沒有 dt_values 的系列（n_alkane）必須仍走原本的階梯啟發式，不得報錯。"""
    c, _ = cal.build_from_std_peaks(_STD_REAL, header={}, series_key="n_alkane")
    assert c["anchor_selection"]["mode"] == "dynamic"
    assert c["anchor_selection"]["dt_match"] is None


def test_build_from_std_peaks_uses_dt_match_for_ketone():
    """整合：ketone 系列預設就該走 dt_matched，且錨點自身內插回自己的 RI。"""
    c, _ = cal.build_from_std_peaks(_STD_REAL, header={}, series_key="ketone")
    a = c["anchor_selection"]
    assert a["mode"] == "dt_matched"
    assert a["n_clean_anchors"] == 6
    r2r = cal.make_rt_to_ri(c)
    for rt, ri in zip(a["clean_rt_s"], c["ri_values"]):
        assert abs(r2r(rt)[0] - ri) < 1e-6


def test_ketone_dt_values_expose_the_anchor_off_by_one():
    """[draft.24] 對照表 Dt 首度揭露錯位時的算術證據——保留為迴歸護欄。

    這是發現問題那一輪的計算：只拿 Dt 對「啟發式已挑出的六個錨點」，就看得出整體
    錯位一格。程式現已改用 match_anchors_by_dt()，但這個算術仍然成立，且它是唯一
    記錄「舊挑法錯在哪」的地方——若有人日後想改回間距啟發式，這裡的 45 倍差距是
    現成的反證。
    """
    dt = rs.REFERENCE_SERIES["ketone"]["dt_values"]          # C4..C9
    proj = [1.104, 1.234, 1.356, 1.487, 1.613, 1.737]        # 141215_STD 實測 6 錨點

    def mean_abs(a, b):
        return sum(abs(x - y) for x, y in zip(a, b)) / len(a)

    as_is = mean_abs(proj, dt)              # 現行假設：6 錨點 = C4..C9
    shifted = mean_abs(proj[1:], dt[:5])    # 位移一格：第 2..6 個 = C4..C8
    assert as_is > 0.1, "現行指派應該明顯對不上（若變小，代表有人改了資料或指派）"
    assert shifted < 0.01, "位移一格後應該逐點吻合"
    assert as_is / shifted > 20, "兩種指派的優劣必須是壓倒性的，不是勉強勝出"


def test_ketone_borrowed_ri_is_absolute_but_flagged():
    sep("[9] ketone_RI_provenance.md：對照表 6 點 RI → 絕對模式 + assumed_unverified")
    mk = rs.REFERENCE_SERIES["ketone"]
    assert mk["carbon_numbers"] == [4, 5, 6, 7, 8, 9]
    assert mk["ri_values"] == [916.8372, 987.12244, 1087.4615,
                               1181.3636, 1293.7333, 1392.9]   # 經理對照表
    assert rs.series_is_assumed("ketone") is True                # 管柱極性未驗證
    assert rs.series_confidence("ketone") == "supplier_table_column_polarity_unverified"
    # 模板只取 RI(Y)，Rt(X) 用本批 STD 實測值
    tmpl = cal.template_from_series(
        [282.0, 334.3, 400.3, 521.8, 697.0, 949.0], "ketone")
    assert tmpl[0]["ri"] == 916.8372 and tmpl[-1]["ri"] == 1392.9
    # 釘定 6 峰 + 借用 RI → 絕對 RI，但 provenance 全程帶著
    c, _ = cal.build_from_std_peaks(STD_141215, HDR_141215,
                                    series_key="ketone", expected_anchors=tmpl)
    print(f"  mode={c['mode']}  assumed={c['assumed_unverified']}  conf={c.get('ri_confidence')}")
    assert c["mode"] == "multi_point_loglinear"
    assert c["assumed_unverified"] is True
    assert c["ri_confidence"] == "supplier_table_column_polarity_unverified"
    # 套到樣品峰：ri 有值、且每峰帶 ri_confidence
    pk = [dict(p) for p in STD_141215]
    cal.attach_ri(pk, c)
    assert all(p["ri"] is not None for p in pk)
    assert all(p["ri_confidence"] == "supplier_table_column_polarity_unverified" for p in pk)


def test_interp_ALGORITHM_only_rt450_NOT_calibration_validation():
    # draft.21 line 55 / 第16點：此測試只驗「log10 轉換 + 區間搜尋 + 加權內插」三步驟
    # 寫對沒有；RI 值 400..900 是純示範算術，**不是真實的酮校準值**，不得被誤讀為
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
    # ketone 現在有 6 點對照表 RI（ketone_RI_provenance.md）
    assert rs.assign_ri(6, "ketone") == [916.8372, 987.12244, 1087.4615,
                                         1181.3636, 1293.7333, 1392.9]
    try:
        rs.assign_ri(3, "ketone")                  # 錨點數不符（6≠3）→ ValueError
        assert False, "應丟 ValueError"
    except ValueError:
        print("  ketone 錨點數不符時正確丟 ValueError")


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
    sep("[P1] 釘定：7 個候選 → 對齊 6 個酮的模板，多的 347.9 被忽略")
    expected = cal.template_from_series(
        [282.0, 334.3, 400.3, 521.8, 697.0, 949.0], "ketone")
    assert [e["label"] for e in expected][:2] == ["2-butanone", "2-pentanone"]
    c, _ = cal.build_from_std_peaks(STD_141215, HDR_141215,
                                    series_key="ketone",
                                    expected_anchors=expected)
    asel = c["anchor_selection"]
    print(f"  anchor_mode={asel['mode']}  n={asel['n_clean_anchors']}  rt={asel['clean_rt_s']}")
    assert asel["mode"] == "pinned"
    assert asel["n_clean_anchors"] == 6                 # 固定為模板數，347.9 被丟
    assert 347.9 not in asel["clean_rt_s"]
    # ketone 已有借用 RI → 絕對模式（provenance 帶 assumed_unverified）
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
                              series_key="ketone")
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


# --------------------------------------------------------------------------- #
# K0 校準：用 STD 已知化合物反推 instrument_constant（Stage 2 standard_based）
# --------------------------------------------------------------------------- #
_STD_K0 = [  # dt_index 為 141215_STD 實測；drift_relative 供認峰
    {"dt_index": 839,  "drift_relative": 1.234, "intensity": 2498, "retention_s": 389.7},
    {"dt_index": 922,  "drift_relative": 1.356, "intensity": 3230, "retention_s": 467.0},
    {"dt_index": 1011, "drift_relative": 1.487, "intensity": 3189, "retention_s": 609.5},
    {"dt_index": 1097, "drift_relative": 1.613, "intensity": 2927, "retention_s": 813.4},
    {"dt_index": 1181, "drift_relative": 1.737, "intensity": 2213, "retention_s": 1107.2},
    {"dt_index": 1261, "drift_relative": 1.854, "intensity": 1028, "retention_s": 1523.4},
]
_HDR_K0 = {"Start temp 1": "45 [°C]", "Start ambient pressure": "100.393 [kPa]",
           "Start pressure EPC IMS": "1.450 [kPa]", "Chunk sample rate": "150 [kHz]",
           "nom Drift Tube Length": "53000 [µm]",
           "nom Drift Potential Difference": "2700 [V]"}


def test_k0_instrument_constant_is_consistent_across_anchors():
    """六個獨立化合物必須解出**同一個** instrument_constant。

    這是整個 standard_based 校準成立的前提：若各點解出的 IC 分散，代表偏差不是單一
    乘法因子（認錯峰／STD 有問題／參考 K0 與本儀器不同源），那時給出平均值只是製造
    假的精確度——所以 max_cv 超標要回 usable=False，而不是硬給一個數。
    """
    s = rs.REFERENCE_SERIES["ketone"]
    r = cal.derive_k0_instrument_constant(
        _STD_K0, s["dt_values"], s["inv_k0_values"], 150.0, 2700.0)
    assert r["usable"] is True
    assert r["n_anchors"] == 6
    assert r["cv"] < 0.01, f"六點解出的 IC 應高度一致，實得 CV={r['cv']:.3%}"
    assert 24 < r["instrument_constant"] < 26


def test_k0_calibration_removes_the_raw_parameters_bias():
    """校準後殘差必須遠小於同系物間距，否則 K0 比對沒有分辨力。"""
    import dt_convert as dtc
    s = rs.REFERENCE_SERIES["ketone"]
    prof, _ = cal.build_k0_profile_from_std(_STD_K0, _HDR_K0, series_key="ketone")
    assert prof["k0_calibration"]["mode"] == "standard_based"

    gap = 0.061          # 相鄰同系物 1/K0 平均間距（實測）
    worst_cal = worst_raw = 0.0
    for p, inv_ref in zip(_STD_K0, s["inv_k0_values"]):
        cal_k0 = dtc.compute_k0(p["dt_index"], _HDR_K0, prof)[0]
        raw_k0 = dtc.compute_k0(p["dt_index"], _HDR_K0,
                                {"k0_calibration": {"mode": "raw_parameters"}})[0]
        worst_cal = max(worst_cal, abs(1 / cal_k0 - inv_ref))
        worst_raw = max(worst_raw, abs(1 / raw_k0 - inv_ref))
    assert worst_cal < 0.1 * gap, f"校準後殘差應 <10% 間距，實得 {worst_cal / gap:.0%}"
    assert worst_raw > 0.3 * gap, "raw_parameters 的偏差本就大到不可比對；若變小，前提要重查"


def test_k0_derivation_rejects_inconsistent_anchors():
    """參考 K0 亂掉時必須拒絕校準，不得回一個平均值。"""
    s = rs.REFERENCE_SERIES["ketone"]
    bad = list(s["inv_k0_values"])
    bad[0] *= 1.5                       # 其中一點明顯不同源
    r = cal.derive_k0_instrument_constant(_STD_K0, s["dt_values"], bad, 150.0, 2700.0)
    assert r["usable"] is False and "離散" in r["reason"]


def test_extract_raw_tp_sums_the_two_pressures():
    """T/P 欄位對應（VOCal 反編譯確認）：壓力是 ambient + EPC 的**和**，不是二選一。"""
    import dt_convert as dtc
    tp, det = dtc.extract_raw_tp(_HDR_K0)
    assert tp["T_C"] == 45.0
    assert abs(tp["P_mbar"] - 10.0 * (100.393 + 1.450)) < 1e-9
    assert tp["P_mbar"] > 1000, "取單一欄位會得到 1004 或 14.5，都不對"
    assert det["T_field"] == "Start temp 1"
    # 缺欄位要明確失敗，不猜
    tp2, det2 = dtc.extract_raw_tp({"Start temp 1": "45"})
    assert tp2 is None and det2["missing"]


def test_ri_and_k0_resolve_from_the_same_std(tmp_path):
    """同一批次的 RI 與 K0 必須出自同一支 STD。

    這是分開解析時最容易出現、卻完全沒有徵兆的錯誤：兩邊各自掃資料夾、各自挑 STD，
    挑到不同支時兩個校正都「成功」，也都不會報錯，只是後續每一次比對都建立在兩組
    不一致的基準上。`resolve_calibrations_cached()` 因此把 RI 選中的檔名傳給 K0。
    """
    folder = tmp_path / "batch"
    folder.mkdir()
    # STD 是靠表頭 Sample=='STD' 認的，不是檔名，所以要寫真的表頭
    for name in ("260101_000000_STD.mea", "260102_000000_STD.mea"):
        (folder / name).write_text("Sample = STD\nChunk sample rate = 150 [kHz]\n",
                                   encoding="latin-1")

    good = _STD_K0                       # 六個乾淨錨點
    def loader(mea_path):
        base = os.path.basename(mea_path)
        # 第一支只有兩顆峰（RI 不可用），第二支才是好的 → RI 必然選第二支
        return (good if base.startswith("260102") else good[:2]), dict(_HDR_K0)

    cal.clear_session_cache()
    out = cal.resolve_calibrations_cached(
        str(folder), series_key="ketone", k0_series_key="ketone",
        std_peaks_loader=loader, use_sidecar=False)

    ri_cal, ri_mode, _ = out["ri"]
    _, _, k0_detail = out["k0"]
    assert ri_mode == "batch_own_std"
    assert ri_cal["std_file"] == "260102_000000_STD.mea"
    assert k0_detail["std_used"] == ri_cal["std_file"], (
        "K0 必須沿用 RI 選中的 STD；掃描順序的第一支是 260101，"
        "若這裡是 260101 就代表 prefer_std 沒生效")


# --------------------------------------------------------------------------- #
# .gasprj RI 表：資料夾沒有 STD 時的 RI 來源（2026-08-24）
# --------------------------------------------------------------------------- #
def _write_gasprj(path, values, is_log=True):
    """最小 .gasprj：只需要 RI_Normalization，真檔還有一堆無關區塊。"""
    import json as _json
    path.write_text(_json.dumps({
        "ObjectType": "LAV Project",
        "RI_Normalization": {
            "ColNormName": "RI normalization",
            "ColNormisLog": is_log,
            "Values": [{"ColNormY": y, "ColNormX": x} for x, y in values],
        },
    }), encoding="utf-8")


def test_gasprj_table_becomes_a_calibration(tmp_path):
    """沒有 STD 的資料夾，改由 .gasprj 的 RI_Normalization 取得 RI。"""
    import math
    vals = [(math.log10(rt), ri) for rt, ri in
            ((100.0, 600.0), (200.0, 750.0), (400.0, 900.0), (800.0, 1050.0))]
    _write_gasprj(tmp_path / "proj.gasprj", vals)

    c, mode, detail = cal.resolve_ri_calibration(str(tmp_path))
    assert mode == "vocal_project_table"
    assert c["mode"] == "multi_point_loglinear"
    assert c["n_anchors"] == 4
    # 出處與不確定性都要帶著走
    assert c["assumed_unverified"] is True
    assert c["ri_confidence"] == "vocal_project_table_anchors_not_recoverable"
    assert c["gasprj_source"] == "proj.gasprj"
    # 錨點之間內插得回原值
    rt_to_ri = cal.make_rt_to_ri(c)
    ri, extrap = rt_to_ri(200.0)
    assert abs(ri - 750.0) < 1e-6 and not extrap


def test_std_outranks_gasprj(tmp_path, monkeypatch):
    """同時有 STD 與 .gasprj → 一定走 STD。

    STD 那條路的錨點是本專案自己認得、可驗證的；.gasprj 的原始錨點不可考。
    順序寫死在 resolve_ri_calibration()，這條測試擋住有人把它掉過來。
    """
    import math
    _write_gasprj(tmp_path / "proj.gasprj",
                  [(math.log10(rt), ri) for rt, ri in
                   ((100.0, 600.0), (400.0, 900.0), (800.0, 1050.0))])
    (tmp_path / "std.mea").write_bytes(b'Sample = "STD"\r\nChunks count = 10\r\n')

    series = rs.REFERENCE_SERIES["ketone"]
    std_peaks = [{"retention_s": rt, "drift_relative": dt, "intensity": 5000,
                  "active": True}
                 for rt, dt in zip((389.7, 467.0, 609.5, 813.4, 1107.2, 1523.4),
                                   series["dt_values"])]
    monkeypatch.setattr(cal, "_default_std_peaks_loader",
                        lambda p, **k: (std_peaks, {"Sample": "STD"}))

    c, mode, _ = cal.resolve_ri_calibration(str(tmp_path), series_key="ketone")
    assert mode == "batch_own_std"
    assert c["n_anchors"] == 6
    assert "gasprj_source" not in c


def test_gasprj_negative_RI_extrapolation_is_trimmed_at_the_kovats_floor(tmp_path):
    """短保留時間端 VOCal 外插出的負 RI 必須被截掉。

    實測 GAS/藝妓咖啡：203 點中最低 RI = −631。RI 是 Kovats 尺標，甲烷 = 100
    **依定義**是下限，低於它的不是「很小的 RI」而是沒有意義的外插值。截斷用這條
    定義線，不是調出來的門檻；砍掉幾點記在 meta 裡，不靜默丟資料。
    """
    import math
    vals = ([(math.log10(rt), ri) for rt, ri in
             ((9.0, -631.0), (11.0, -17.0), (14.0, 95.0))]        # 外插垃圾
            + [(math.log10(rt), ri) for rt, ri in
               ((20.0, 250.0), (100.0, 650.0), (900.0, 1095.0))])  # 真實區段
    p = tmp_path / "proj.gasprj"
    _write_gasprj(p, vals)

    rts, ris, meta = cal.read_gasprj_ri_table(str(p))
    assert meta["n_points_raw"] == 6
    assert meta["n_points_dropped_below_kovats_floor"] == 3
    assert min(ris) >= cal.KOVATS_RI_FLOOR
    assert abs(rts[0] - 20.0) < 1e-9
    # 被截掉的區域仍能查詢，但必須標成外插——不刪資料、只標不確定
    c = cal.build_calibration(rts, "vocal_project_table", ri_values=ris)
    _, extrap = cal.make_rt_to_ri(c)(10.0)
    assert extrap is True


def test_gasprj_non_log_x_axis_is_refused(tmp_path):
    """ColNormisLog=false → 拒絕載入，不自行臆測換算。

    那代表 X 存的是 Rt 本身而非 log10(Rt)。猜錯不會報錯，只會產生整條錯位但看起來
    正常的 RI——本專案手上沒有 false 的樣本可驗證，所以不寫沒驗證過的分支。
    """
    _write_gasprj(tmp_path / "proj.gasprj",
                  [(100.0, 600.0), (400.0, 900.0)], is_log=False)
    c, mode, detail = cal.resolve_ri_calibration(str(tmp_path))
    assert mode == "unavailable" and c is None
    assert "ColNormisLog" in detail["gasprj"]["rejected"][0]["reason"]


def test_gasprj_records_that_the_table_is_resampled_not_original_anchors(tmp_path):
    """等距格點要被認出來並記進 provenance。

    VOCal 存的是重採樣後的曲線（實測 log10(Rt) 等距 0.01、203 點），不是原始錨點。
    不記這件事，這張表看起來會像一組高解析度的實測錨點。
    """
    import math
    x0 = math.log10(20.0)
    vals = [(x0 + 0.01 * i, 250.0 + 5.0 * i) for i in range(40)]
    p = tmp_path / "proj.gasprj"
    _write_gasprj(p, vals)
    _, _, meta = cal.read_gasprj_ri_table(str(p))
    assert meta["resampled_uniform_grid"] is True
    assert abs(meta["log_rt_step"] - 0.01) < 1e-9


def test_folder_without_std_or_gasprj_stays_unavailable(tmp_path):
    """兩者皆無 → RI 仍是 unavailable，不能生出數字來。"""
    (tmp_path / "sample.mea").write_bytes(b'Sample = "A_1"\r\nChunks count = 10\r\n')
    c, mode, _ = cal.resolve_ri_calibration(str(tmp_path))
    assert mode == "unavailable" and c is None

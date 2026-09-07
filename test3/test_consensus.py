"""test_consensus.py — 票數門檻、排序與候選彙整的測試。

這一支測的是第三支應用**唯一真正新增的邏輯**：把同一標本的多個重複測量彙整成
一張帶支持度的化合物清單。其餘都是既有模組的組合。

幾條是實際踩過的錯：

- 門檻寫成小數 `0.67`，於是 n=3 時要求 3/3（2/3 = 66.7% 差 0.3% 不過）
- 用顯示門檻去**形成**區域，未達門檻的根本沒被建出來，「不刪除只標記」變成空話
- 只彙整 `gc_matches`，於是每個位置上百個候選；`combined` 才收斂到個位數
"""
import json
import math
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import areas2  # noqa: E402
from compound_consensus import logic as L  # noqa: E402


# --------------------------------------------------------------------------- #
# 門檻是佔比，而且必須寫成分數
# --------------------------------------------------------------------------- #
def test_two_thirds_of_three_is_two_not_three():
    """n=3 要 2 個，不是 3 個。

    回歸測試：門檻寫成小數 `0.67` 時 `ceil(0.67×3)=3`，也就是三個重複全部都要有。
    2/3 = 66.67% 只差 0.33% 就被擋掉，而且**每個 3 的倍數都會出現**
    （n=6 要 5 vs 4、n=15 要 11 vs 10），完全沒有徵兆。
    """
    assert L.required_files(3) == 2
    assert math.ceil(0.67 * 3) == 3, "這行記錄的是「錯的寫法會怎樣」"


@pytest.mark.parametrize("n,want", [(3, 2), (4, 3), (5, 4), (6, 4), (9, 6),
                                    (12, 8), (15, 10), (18, 12)])
def test_required_files_scales_with_group_size(n, want):
    """門檻跟著這一組有幾個檔走，不是固定張數。"""
    assert L.required_files(n) == want


def test_required_files_never_below_two():
    """一個檔案無法構成「共識」。"""
    assert L.required_files(1) == 2
    assert L.required_files(2) == 2


def test_threshold_is_configurable():
    assert L.required_files(4, min_fraction=0.5) == 2
    assert L.required_files(4, min_fraction=1.0) == 4


# --------------------------------------------------------------------------- #
# 票數分級與排序
# --------------------------------------------------------------------------- #
def test_vote_tier_uses_fraction_not_raw_count():
    """一組 3 個重複和一組 15 個重複，「2 票」的意義天差地遠。

    拿原始票數上色會讓兩種批次的顏色不能互相比較。
    """
    small = L.vote_tier(2, 3)
    big = L.vote_tier(2, 15)
    assert small["tier"] > big["tier"]
    assert small["below_threshold"] is False       # 2/3 過門檻
    assert big["below_threshold"] is True          # 2/15 差得遠


def test_unanimous_is_the_top_tier():
    assert L.vote_tier(3, 3)["label"] == "all"
    assert L.vote_tier(15, 15)["tier"] == 4


def test_rank_areas_sorts_by_votes_then_prominence():
    areas = [
        {"area_id": 1, "votes": 2, "votes_total": 3, "max_prominence": 900.0},
        {"area_id": 2, "votes": 3, "votes_total": 3, "max_prominence": 100.0},
        {"area_id": 3, "votes": 3, "votes_total": 3, "max_prominence": 800.0},
    ]
    out = L.rank_areas(areas, total_files=3)
    assert [a["area_id"] for a in out] == [3, 2, 1], "票多的先，同票時訊號強的先"


def test_below_threshold_areas_are_marked_not_removed():
    """未達門檻的**保留**，只加標記。

    少勾一個檔就可能讓真實化合物掉到門檻以下；靜靜消失會讓人以為那裡本來就沒東西。
    同專案 `n_det=None` vs `0`、空白格 vs 0 的原則。
    """
    areas = [{"area_id": i, "votes": v, "votes_total": 6, "max_prominence": 1.0}
             for i, v in enumerate([6, 4, 3, 2], start=1)]
    out = L.rank_areas(areas, total_files=6)
    assert len(out) == 4, "一個都不能少"
    assert [a["below_threshold"] for a in out] == [False, False, True, True]


# --------------------------------------------------------------------------- #
# 區域裡的峰
# --------------------------------------------------------------------------- #
def _area(dc=1.20, dh=0.02, rt=400.0, rh=8.0):
    return {"area_id": 1, "name": "area 1", "drift_center": dc, "drift_half": dh,
            "rt_center_s": rt, "rt_half_s": rh}


def _peak(dr, rt, prom=100.0, ri=None, active=True):
    return {"drift_relative": dr, "retention_s": rt, "prominence": prom,
            "ri": ri, "active": active}


def test_peaks_in_area_reports_none_for_files_without_a_peak():
    """沒偵測到就記 `None`，**不是省略這個 key**。

    少一個 key 和「這個檔在這裡沒有峰」是兩種陳述，後者才是資訊。
    """
    per_file = {"a.mea": [_peak(1.20, 400.0)],
                "b.mea": [_peak(1.80, 900.0)]}      # 落在方框外
    got = L.peaks_in_area(_area(), per_file)
    assert set(got) == {"a.mea", "b.mea"}
    assert got["a.mea"] is not None
    assert got["b.mea"] is None


def test_peaks_in_area_takes_the_strongest_when_several_fit():
    per_file = {"a.mea": [_peak(1.19, 398.0, prom=10.0),
                          _peak(1.21, 402.0, prom=90.0)]}
    got = L.peaks_in_area(_area(), per_file)
    assert got["a.mea"]["prominence"] == 90.0


def test_peaks_in_area_honours_the_user_selection():
    """使用者取消勾選的峰不算數——區域是從他的判斷長出來的。"""
    per_file = {"a.mea": [_peak(1.20, 400.0, active=False)]}
    assert L.peaks_in_area(_area(), per_file, active_only=True)["a.mea"] is None
    assert L.peaks_in_area(_area(), per_file, active_only=False)["a.mea"] is not None


# --------------------------------------------------------------------------- #
# 候選彙整
# --------------------------------------------------------------------------- #
class _FakeMatch:
    """把 `match.match_all()` 換掉，好單獨測彙整邏輯。"""

    def __init__(self, by_ri):
        self.by_ri = by_ri
        self.calls = 0

    def __call__(self, peak, ril, iml, **kw):
        self.calls += 1
        rows = self.by_ri.get(round(peak.get("ri") or 0, 1), {})
        return {"combined_matches": rows.get("combined", []),
                "gc_matches": rows.get("gc", []),
                "ims_matches": [], "gc_dimension": "ri", "ims_dimension": "drift_rel"}


def _row(cas, name, ri):
    return {"CAS": cas, "Name": name, "RI": ri}


def test_support_counts_files_and_denominator_excludes_files_without_a_peak(
        monkeypatch):
    """支持度的分母是「有偵測到峰的檔」，不是全部選取的檔。

    沒有峰的檔沒有投票權；把它算進分母會讓每個候選看起來都比實際弱。
    """
    fake = _FakeMatch({
        900.0: {"combined": [_row("78-93-3", "2-butanone", 908.0)]},
        901.0: {"combined": [_row("78-93-3", "2-butanone", 908.0)]},
    })
    monkeypatch.setattr(L.match_mod, "match_all", fake)
    per_file = {"a.mea": [_peak(1.20, 400.0, ri=900.0)],
                "b.mea": [_peak(1.20, 401.0, ri=901.0)],
                "c.mea": [_peak(1.90, 900.0, ri=1200.0)]}     # 方框外
    out = L.consolidate_area(_area(), per_file, [], [])
    assert out["n_files_selected"] == 3
    assert out["n_files_with_peak"] == 2
    assert out["files_without_peak"] == ["c.mea"]
    top = out["candidates"][0]
    assert top["n_support"] == 2
    assert top["n_files_with_peak"] == 2
    assert top["support"] == 1.0, "2/2，不是 2/3"


def test_candidates_are_ranked_by_support_then_delta(monkeypatch):
    fake = _FakeMatch({
        900.0: {"combined": [_row("1", "everywhere", 900.5),
                             _row("2", "once-only", 902.0)]},
        901.0: {"combined": [_row("1", "everywhere", 900.5)]},
    })
    monkeypatch.setattr(L.match_mod, "match_all", fake)
    per_file = {"a.mea": [_peak(1.20, 400.0, ri=900.0)],
                "b.mea": [_peak(1.20, 401.0, ri=901.0)]}
    out = L.consolidate_area(_area(), per_file, [], [])
    names = [c["name"] for c in out["candidates"]]
    assert names[0] == "everywhere", "2/2 要排在 1/2 前面"
    assert out["candidates"][0]["n_support"] == 2
    assert out["candidates"][1]["n_support"] == 1


def test_combined_matches_are_preferred_over_gc_only(monkeypatch):
    """兩軸都同意才算數；只有 RI 對上時退回並**標明**。

    回歸測試：只彙整 `gc_matches` 時每個位置上百個候選（實測中位數 105），
    改用 `combined` 之後降到 3。證據強度差一個數量級，不能混為一談。
    """
    fake = _FakeMatch({900.0: {"combined": [_row("1", "solid", 900.0)],
                               "gc": [_row("1", "solid", 900.0),
                                      _row("2", "noise", 901.0)]}})
    monkeypatch.setattr(L.match_mod, "match_all", fake)
    per_file = {"a.mea": [_peak(1.20, 400.0, ri=900.0)]}
    out = L.consolidate_area(_area(), per_file, [], [])
    assert out["match_dimension"] == "combined"
    assert [c["name"] for c in out["candidates"]] == ["solid"]


def test_falls_back_to_gc_only_and_says_so(monkeypatch):
    fake = _FakeMatch({900.0: {"combined": [],
                               "gc": [_row("1", "a", 900.0), _row("2", "b", 901.0)]}})
    monkeypatch.setattr(L.match_mod, "match_all", fake)
    per_file = {"a.mea": [_peak(1.20, 400.0, ri=900.0)]}
    out = L.consolidate_area(_area(), per_file, [], [])
    assert out["match_dimension"] == "gc_only"
    assert len(out["candidates"]) == 2


def test_ri_spread_is_reported_for_tolerance_calibration(monkeypatch):
    """回報實測的重複變異——這正是校準容差窗需要而目前沒有的數字。

    `status.md` open decision 4：±5 是佔位值，從未以量測校準過。
    """
    fake = _FakeMatch({})
    monkeypatch.setattr(L.match_mod, "match_all", fake)
    per_file = {"a.mea": [_peak(1.20, 400.0, ri=900.0)],
                "b.mea": [_peak(1.20, 401.0, ri=900.8)],
                "c.mea": [_peak(1.20, 399.0, ri=900.4)]}
    out = L.consolidate_area(_area(), per_file, [], [])
    assert out["ri_measured_mean"] == pytest.approx(900.4, abs=0.05)
    assert out["ri_spread"] == pytest.approx(0.8, abs=1e-6)


def test_single_measurement_has_no_spread(monkeypatch):
    """一個點算不出變異——回 `None`，不是 0。0 會被誤讀成「完全一致」。"""
    monkeypatch.setattr(L.match_mod, "match_all", _FakeMatch({}))
    per_file = {"a.mea": [_peak(1.20, 400.0, ri=900.0)]}
    out = L.consolidate_area(_area(), per_file, [], [])
    assert out["ri_spread"] is None


# --------------------------------------------------------------------------- #
# 參數指紋
# --------------------------------------------------------------------------- #
def test_fingerprint_changes_with_rules_and_detection_params():
    """`areas2` 的快取只比對 baseline；規則與門檻參數要靠這個指紋把關。"""
    rc = [{"rule_number": "R004", "enabled": True, "params": {"half_width": 0.02}}]
    base = L.params_fingerprint(rc)
    changed_rule = L.params_fingerprint(
        [{"rule_number": "R004", "enabled": True, "params": {"half_width": 0.05}}])
    assert base != changed_rule, "R004 的參數會改變哪些峰活得下來"
    assert base != L.params_fingerprint(rc, prom_frac=0.05)
    assert base != L.params_fingerprint(rc, use_baseline=True)
    assert base == L.params_fingerprint(rc), "同樣的輸入要得到同樣的指紋"


def _cfg(half_width=0.02, boundary=1.0, r001=0):
    return [{"rule_number": "R001", "enabled": True, "params": {"threshold": r001}},
            {"rule_number": "R004", "enabled": True,
             "params": {"half_width": half_width}},
            {"rule_number": "R006", "enabled": True,
             "params": {"boundary": boundary}}]


def test_fingerprint_is_order_insensitive_for_rules():
    """規則的順序不該影響指紋——順序不改變找峰的結果。

    否則只是把 `rules_config.json` 裡兩條規則對調，就會讓所有快取失效、
    白跑好幾十分鐘的偵測。
    """
    a = _cfg()
    assert L.params_fingerprint(a) == L.params_fingerprint(list(reversed(a)))


def test_mandatory_rule_params_change_the_fingerprint():
    """R004/R006 在突出度門檻**之前**生效，改了就會改變哪些峰活得下來。"""
    base = L.params_fingerprint(_cfg())
    assert L.params_fingerprint(_cfg(half_width=0.05)) != base, "R004"
    assert L.params_fingerprint(_cfg(boundary=1.2)) != base, "R006"


def test_optional_rule_params_do_not_change_the_fingerprint():
    """選配規則只**標記**，不改變偵測——改它們不該害整批重跑 55 秒/檔。

    `rules.mark_rules()` 不移除任何峰，所以 R001/R002/R003/R005 換了參數只要重新
    標記即可。把整份 config 丟進指紋會讓「調一下 R001」白白重跑一整批。
    """
    assert L.params_fingerprint(_cfg(r001=999)) == L.params_fingerprint(_cfg())


def test_fingerprint_version_lets_old_sidecars_be_revalidated(tmp_path,
                                                              monkeypatch):
    """指紋演算法改版時，舊 sidecar 不該被當成「參數變了」。

    那是**工具**變了、資料沒變。當成參數變會讓所有檔案白跑一次偵測。
    """
    import areas2
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(tmp_path))
    mea = str(tmp_path / "x.mea")
    (tmp_path / "x_peaks2.json").write_text(json.dumps(
        {"params": {"sigma": 1.0, "floor_pct": 85.0, "prom_frac": 0.02,
                    "min_distance": 3, "baseline_applied": False}}),
        encoding="utf-8")
    cfg = _cfg()
    # 舊版本、指紋對不上的 sidecar
    (tmp_path / "x_peaks2_fp3.json").write_text(json.dumps(
        {"fingerprint": "stale", "version": L.FINGERPRINT_VERSION - 1}),
        encoding="utf-8")
    assert L.peaks_are_current(mea, cfg, trust_existing=True, write=False) is True

    # 同版本卻對不上 = 參數真的變了，必須重跑
    (tmp_path / "x_peaks2_fp3.json").write_text(json.dumps(
        {"fingerprint": "stale", "version": L.FINGERPRINT_VERSION}),
        encoding="utf-8")
    assert L.peaks_are_current(mea, cfg, trust_existing=True, write=False) is False


# --------------------------------------------------------------------------- #
# 相似度：比的是強度，而且不可以被「打多打少」左右
# --------------------------------------------------------------------------- #
def test_similarity_is_invariant_to_overall_intensity():
    """同一個標本只因為進樣量不同，不可以被判成不像。

    進樣量／濃度會讓整張圖等比例放大，在 log 上是加一個常數。實測未先移除時：
    把一個檔的強度乘 2，相似度從 0.970 掉到 0.673——那是量的問題，不是身分的問題。

    **不變性不是「數學上完全相等」**：`log10(x+1)` 的那個 `+1` 讓等比例縮放不再是
    加常數。真實的區域體積在 1e4~1e6 量級，`+1` 可以忽略（實測差 4e-6）；
    這裡用真實量級測，容差就設在那個尺度上。
    """
    base = [[9.9e5, 4.6e6, 2.0e6, 1.3e6, 3.4e5],
            [1.0e6, 5.1e6, 1.9e6, 1.3e6, 4.1e5],
            [5.8e4, 5.3e6, 2.2e6, 8.4e5, 1.4e5]]
    c0, _ = L.similarity_matrix(base)
    c1, _ = L.similarity_matrix([[v * 2.0 for v in base[0]]] + base[1:])
    assert c1[0, 1] == pytest.approx(c0[0, 1], abs=1e-4)
    assert c1[0, 2] == pytest.approx(c0[0, 2], abs=1e-4)
    # 沒有先去掉整體強度的話，這個差會是 0.3 的量級，不是 1e-4
    assert abs(c1[0, 1] - c0[0, 1]) < 1e-3


def test_similarity_still_separates_different_profiles():
    """去掉整體強度之後仍然要分得開——不能為了不變性把鑑別力賠掉。"""
    a = [10.0, 900.0, 3.0, 50.0, 200.0]
    b = [11.0, 880.0, 3.2, 52.0, 195.0]        # 與 a 幾乎一樣
    c = [900.0, 5.0, 700.0, 3.0, 8.0]          # 完全不同的輪廓
    corr, _ = L.similarity_matrix([a, b, c])
    assert corr[0, 1] > corr[0, 2], "相像的要比不相像的高"


# --------------------------------------------------------------------------- #
# monomer / dimer 配對
# --------------------------------------------------------------------------- #
def _md_areas():
    """兩組區域：一組真的成對（同 RT、drift 分開），一組只是鄰居。"""
    return [
        {"area_id": 1, "rt_center_s": 370.0, "drift_center": 1.100},   # monomer
        {"area_id": 2, "rt_center_s": 371.5, "drift_center": 1.320},   # dimer
        {"area_id": 3, "rt_center_s": 900.0, "drift_center": 1.200},   # 無關
    ]


def _md_profiles():
    """1 與 2 跨檔同步漲落（同一化合物）；3 走自己的路。"""
    return [[100.0, 40.0, 900.0],
            [300.0, 120.0, 880.0],
            [900.0, 350.0, 910.0],
            [50.0, 20.0, 895.0]]


def test_finds_a_pair_that_moves_together():
    """同 RT、drift 分開、跨檔同步 —— 就是 monomer/dimer 的樣子。"""
    kept, _rej = L.find_monomer_dimer_pairs(_md_areas(), _md_profiles())
    assert len(kept) == 1
    p = kept[0]
    assert (p["monomer"], p["dimer"]) == (0, 1)
    assert p["r"] > 0.9


def test_lower_drift_is_the_monomer():
    """dimer 比較大比較重、漂移比較長——實測 4 對操作者標註全部符合。"""
    kept, _ = L.find_monomer_dimer_pairs(_md_areas(), _md_profiles())
    assert kept[0]["drift_monomer"] < kept[0]["drift_dimer"]


def test_same_drift_is_not_a_pair():
    """漂移差太小的多半只是同一顆峰被切成兩半，不是 monomer/dimer。"""
    areas = [{"area_id": 1, "rt_center_s": 370.0, "drift_center": 1.100},
             {"area_id": 2, "rt_center_s": 371.0, "drift_center": 1.120},
             {"area_id": 3, "rt_center_s": 900.0, "drift_center": 1.200}]
    kept, _ = L.find_monomer_dimer_pairs(areas, _md_profiles())
    assert kept == []


def test_far_apart_in_rt_is_not_a_pair():
    """保留時間差很多就不是同一個物質——它們不會同時離開管柱。"""
    areas = [{"area_id": 1, "rt_center_s": 370.0, "drift_center": 1.100},
             {"area_id": 2, "rt_center_s": 500.0, "drift_center": 1.320},
             {"area_id": 3, "rt_center_s": 900.0, "drift_center": 1.200}]
    kept, _ = L.find_monomer_dimer_pairs(areas, _md_profiles())
    assert kept == []


def test_uncorrelated_regions_are_not_a_pair():
    """位置對得上但跨檔不同步，就不是同一個化合物的兩個訊號。"""
    areas = _md_areas()
    profiles = [[100.0, 900.0, 900.0],
                [300.0, 30.0, 880.0],
                [900.0, 500.0, 910.0],
                [50.0, 700.0, 895.0]]
    kept, _ = L.find_monomer_dimer_pairs(areas, profiles)
    assert kept == []


def test_each_region_belongs_to_at_most_one_pair():
    """一個化合物只有一個 monomer 與一個 dimer。

    回歸測試：實測出現過同一個區域同時配給兩個對象，而 `annotate_pairs()` 是後寫
    蓋前寫——先配到的那組就此消失，「有幾個不同化合物」也跟著算錯。
    """
    areas = [{"area_id": 1, "rt_center_s": 399.0, "drift_center": 1.242},
             {"area_id": 2, "rt_center_s": 399.0, "drift_center": 1.344},
             {"area_id": 3, "rt_center_s": 399.5, "drift_center": 1.394}]
    profiles = [[100.0, 95.0, 90.0], [300.0, 290.0, 280.0],
                [900.0, 880.0, 870.0], [50.0, 48.0, 46.0]]
    kept, rejected = L.find_monomer_dimer_pairs(areas, profiles)
    used = [i for p in kept for i in (p["monomer"], p["dimer"])]
    assert len(used) == len(set(used)), "同一個區域不可以出現在兩組配對裡"
    assert rejected, "落選的要留著並附理由，不可以靜靜丟掉"
    assert all("rejected_reason" in r for r in rejected)


def test_annotate_writes_roles_without_merging():
    """只加註記，不合併也不刪除——配對是提示，不是判定。"""
    areas = _md_areas()
    kept, _ = L.find_monomer_dimer_pairs(areas, _md_profiles())
    L.annotate_pairs(areas, kept)
    assert len(areas) == 3, "區域數量不可以改變"
    assert areas[0]["md_role"] == "monomer" and areas[0]["md_partner"] == 1
    assert areas[1]["md_role"] == "dimer" and areas[1]["md_partner"] == 0
    assert areas[2]["md_role"] is None


def test_pairing_needs_at_least_three_files():
    """同步與否靠跨檔相關係數，兩個檔時它恆為 ±1——與 similarity_matrix 同理。"""
    with pytest.raises(ValueError, match="至少 3 個檔"):
        L.find_monomer_dimer_pairs(_md_areas(), _md_profiles()[:2])


# --------------------------------------------------------------------------- #
# 候選要帶著自己的維度（GC / IMS / 兩者）
# --------------------------------------------------------------------------- #
def _area_at(rt=400.0, dr=1.10):
    return {"area_id": 1, "name": "A", "rt_center_s": rt, "drift_center": dr,
            "rt_half_s": 10.0, "drift_half": 0.05}


def _pk(rt=400.0, dr=1.10, ri=900.0):
    return {"rt_index": 10, "dt_index": 20, "retention_s": rt,
            "drift_relative": dr, "ri": ri, "intensity": 500,
            "rule_active": True, "active": True}


def test_best_dimension_prefers_two_axes_over_one(monkeypatch):
    """證據強度排序：兩軸都對上 > 只有 GC > 只有 IMS。"""
    assert L.best_dimension({"gc_only", "combined"}) == "combined"
    assert L.best_dimension({"ims_only", "gc_only"}) == "gc_only"
    assert L.best_dimension({"ims_only"}) == "ims_only"
    assert L.best_dimension(set()) is None


def test_ims_only_hits_are_reported_instead_of_no_candidates(monkeypatch):
    """RI 沒對上但漂移對上時要回報 IMS 候選，不是「（無候選）」。

    漂移對上而 RI 落空是一個**有內容的**結果。原本兩層都空就回空清單，
    等於把它跟「什麼都沒找到」混為一談。
    """
    import match as match_mod
    monkeypatch.setattr(match_mod, "match_all", lambda p, r, i, **k: {
        "combined_matches": [], "gc_matches": [],
        "ims_matches": [{"Name": "Hexanal", "CAS": "66-25-1",
                         "Dt[a.u.]": 1.10, "delta_drift_rel": 0.002,
                         "match_dimensions": ["drift_rel"]}]})
    out = L.consolidate_area(_area_at(), {"a.mea": [_pk()]}, [], [])
    assert out["match_dimension"] == "ims_only"
    assert len(out["candidates"]) == 1
    assert out["candidates"][0]["name"] == "Hexanal"
    assert out["candidates"][0]["dimension"] == "ims_only"


def test_each_candidate_carries_the_dimension_it_came_from(monkeypatch):
    """兩軸都對上的候選標 combined，同一區域裡只有 RI 的標 gc_only。"""
    import match as match_mod
    monkeypatch.setattr(match_mod, "match_all", lambda p, r, i, **k: {
        "combined_matches": [{"Name": "Acetone", "CAS": "67-64-1", "RI": 842.0}],
        "gc_matches": [{"Name": "Other", "CAS": "999-99-9", "RI": 841.0}],
        "ims_matches": []})
    out = L.consolidate_area(_area_at(), {"a.mea": [_pk()]}, [], [])
    # combined 有東西時就只用 combined——這是既有行為，不因為新增維度標籤而改變
    assert out["match_dimension"] == "combined"
    assert [c["name"] for c in out["candidates"]] == ["Acetone"]
    assert out["candidates"][0]["dimension"] == "combined"


def test_dimension_labels_are_defined_once(monkeypatch):
    """標籤集中在 `logic.DIMENSION_LABEL`，UI 不各自寫死。"""
    assert L.DIMENSION_LABEL["combined"] == "GC+IMS"
    assert L.DIMENSION_LABEL["gc_only"] == "GC"
    assert L.DIMENSION_LABEL["ims_only"] == "IMS"
    for key in L.DIMENSION_RANK:
        assert key in L.DIMENSION_LABEL, "每個維度都要有畫面標籤"


# --------------------------------------------------------------------------- #
# 使用者在熱圖上的勾選必須真的影響共識
#
# 使用者的提問（2026-09-07）:「在熱圖上選/不選峰你會記住吧?因為要找共同峰時
# 應該要拿目前選取的峰去彙整」。答案本來是**不會**——選取存了、但彙整沒用到。
# --------------------------------------------------------------------------- #
def _two_peaks():
    return [{"rt_index": 1, "dt_index": 2, "retention_s": 400.0,
             "drift_relative": 1.10, "ri": 900.0, "intensity": 900,
             "prominence": 300, "active": True, "rule_active": True},
            {"rt_index": 3, "dt_index": 4, "retention_s": 800.0,
             "drift_relative": 1.30, "ri": 1100.0, "intensity": 800,
             "prominence": 200, "active": True, "rule_active": True}]


def test_on_peaks_hook_runs_before_regions_are_built(monkeypatch, tmp_path):
    """`on_peaks` 要在建區域**之前**跑,而且改動要算數。"""
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr(L, "detect_cached",
                        lambda m, rc, **k: (_two_peaks(), {}, {}))
    seen = []

    def hook(mea, pk):
        seen.append((mea, len(pk)))
        for p in pk:                     # 全部關掉
            p["active"] = False

    areas, per_file, _rep = L.consensus_regions(
        ["a.mea", "b.mea"], {}, on_peaks=hook, verbose=False)
    assert [n for _m, n in seen] == [2, 2], "每個檔都要經過鉤子"
    assert all(not p["active"] for pk in per_file.values() for p in pk)
    assert areas == [], "全部關掉之後不該有任何共識區域"


def test_a_peak_the_user_switched_off_is_excluded_from_consensus(
        monkeypatch, tmp_path):
    """使用者關掉的峰不可以進共識。

    **回歸測試,兩個原因疊在一起**:
    1. `state.load()` 只寫 `user_active`,而 `build_consensus_areas(active_only=True)`
       讀的是 `active`——中間沒有 `apply_effective()` 就完全沒有效果。
    2. 先跑一次 `consensus_regions()`、對回傳的 `per_file` 套用選取、再跑第二次
       是**沒有用的**:第二次重新偵測並產生全新的 dict,剛套上的選取整批被丟掉。

    兩個都不會報錯,畫面上也看不出來——關掉的峰照樣被算進共識。
    """
    from compound_consensus import state as state_mod
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr(L, "detect_cached",
                        lambda m, rc, **k: (_two_peaks(), {}, {}))

    # 使用者在兩個檔裡都把第一顆峰關掉
    for mea in ("a.mea", "b.mea"):
        state_mod.save(mea, [{"rt_index": 1, "dt_index": 2, "user_active": False},
                             {"rt_index": 3, "dt_index": 4, "user_active": None}])

    def apply_choices(mea, pk):
        state_mod.load(mea, pk)
        L.apply_effective(pk)            # ← 少了這一行，整件事無聲失效

    areas, per_file, _rep = L.consensus_regions(
        ["a.mea", "b.mea"], {}, active_only=True, on_peaks=apply_choices,
        verbose=False)

    for pk in per_file.values():
        assert pk[0]["active"] is False, "使用者關掉的峰仍然是 active"
        assert pk[1]["active"] is True
    # 只剩第二顆峰的位置能形成區域
    assert len(areas) == 1, [a.get("drift_center") for a in areas]
    assert areas[0]["drift_center"] == pytest.approx(1.30, abs=0.02)


def test_state_load_alone_does_not_reach_the_active_flag(monkeypatch, tmp_path):
    """把「為什麼需要 apply_effective」釘成可執行的事實。

    這一條刻意測**壞掉的**用法:只呼叫 `state.load()` 而不 `apply_effective()`,
    `active` 不會變。哪天 `load()` 自己開始寫 `active` 了,這條會失敗並提醒我們
    移除呼叫端的那一行。
    """
    from compound_consensus import state as state_mod
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(tmp_path))
    state_mod.save("a.mea", [{"rt_index": 1, "dt_index": 2, "user_active": False}])
    pk = _two_peaks()
    state_mod.load("a.mea", pk)
    assert pk[0]["user_active"] is False
    assert pk[0]["active"] is True, "load() 不碰 active——這正是要 apply_effective 的理由"
    L.apply_effective(pk)
    assert pk[0]["active"] is False


def test_detect_cached_returns_fresh_objects_so_mutation_must_happen_inside(
        monkeypatch, tmp_path):
    """兩次 `consensus_regions()` 不共用峰物件——所以選取只能在鉤子裡套。

    這是上面那條「第二次呼叫會丟掉選取」的根因,單獨釘一條。
    """
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr(L, "detect_cached",
                        lambda m, rc, **k: (_two_peaks(), {}, {}))
    _a, per_file_1, _r = L.consensus_regions(["a.mea"], {}, verbose=False)
    for p in per_file_1["a.mea"]:
        p["active"] = False              # 在外面改
    _a, per_file_2, _r = L.consensus_regions(["a.mea"], {}, verbose=False)
    assert per_file_2["a.mea"][0] is not per_file_1["a.mea"][0]
    assert per_file_2["a.mea"][0]["active"] is True, \
        "第二次是全新的峰,外面的改動不會帶過來"


# --------------------------------------------------------------------------- #
# 這一組的體檢 —— 是哪一個檔在拖後腿
# --------------------------------------------------------------------------- #
def _corr(mat):
    import numpy as np
    return np.array(mat)


def test_group_stats_names_the_file_dragging_the_group_down():
    """離群者要**指名道姓**,不能只給一個最低值。

    原本只在按下彙整時跳「組內最低相似度只有 +0.31,還要繼續嗎」——時機錯了
    (人已經按下去),而且沒說是哪個檔,使用者只能整組重來。
    """
    files = ["a.mea", "b.mea", "c.mea", "d.mea"]
    corr = _corr([[1.00, 0.95, 0.93, 0.31],
                  [0.95, 1.00, 0.94, 0.33],
                  [0.93, 0.94, 1.00, 0.30],
                  [0.31, 0.33, 0.30, 1.00]])
    st = L.group_stats(set(files), corr, files, min_fraction=0.5)
    assert st["outlier"] == "d.mea"
    assert st["outlier_mean_r"] == pytest.approx(0.313, abs=0.01)
    assert st["rest_min_r"] == pytest.approx(0.93, abs=0.01)
    assert st["verdict"] == "mixed", "最低 0.30 < 0.50，整組就是混到了"


def test_outlier_is_the_one_low_against_everyone_not_one_unlucky_pair():
    """一對低不算離群——真正不同組的檔對**每一個人**都低。

    只看最低那一對的話,兩個各自正常但彼此剛好不像的檔會被誤指。
    """
    files = ["a.mea", "b.mea", "c.mea", "d.mea"]
    # a↔b 偏低(0.62),但 a、b 對其他人都高;d 才是對誰都低的那個
    corr = _corr([[1.00, 0.62, 0.95, 0.70],
                  [0.62, 1.00, 0.96, 0.71],
                  [0.95, 0.96, 1.00, 0.69],
                  [0.70, 0.71, 0.69, 1.00]])
    st = L.group_stats(set(files), corr, files, min_fraction=0.5)
    assert st["min_pair"] == ("a.mea", "b.mea"), "最低的一對確實是 a↔b"
    assert st["outlier"] == "d.mea", "但離群的是對誰都低的 d"


def test_group_stats_says_unknown_before_the_scan():
    """還沒掃描就沒有相似度——要講「不知道」,不是假裝一致。"""
    st = L.group_stats({"a.mea", "b.mea"}, None, [], min_fraction=0.5)
    assert st["verdict"] == "unknown"
    assert st["mean_r"] is None
    assert st["n"] == 2


def test_group_stats_reports_members_with_no_similarity_yet():
    """組裡有沒掃到的檔要點名,不是靜靜不算進去。"""
    files = ["a.mea", "b.mea"]
    corr = _corr([[1.0, 0.9], [0.9, 1.0]])
    st = L.group_stats({"a.mea", "b.mea", "z.mea"}, corr, files, min_fraction=0.5)
    assert st["missing_r"] == ["z.mea"]
    assert st["n"] == 3, "分母仍然是整組"
    assert st["n_with_r"] == 2


def test_group_stats_converts_the_threshold_into_a_file_count():
    """門檻要換算成「幾個檔」——1/2 與 2/3 的差別足以改變要不要多收一個檔。"""
    files = ["%d.mea" % i for i in range(5)]
    import numpy as np
    corr = np.full((5, 5), 0.95)
    st_half = L.group_stats(set(files), corr, files, min_fraction=0.5)
    st_two3 = L.group_stats(set(files), corr, files, min_fraction=2 / 3)
    assert st_half["required"] == 3
    assert st_two3["required"] == 4
    assert st_half["verdict"] == "ok"


def test_group_weak_threshold_matches_the_confirmation_dialog():
    """畫面上的判定與彙整前的確認對話框要用**同一個**門檻。

    兩處各寫一個數字的話,畫面說「可以彙整」而按下去卻跳警告。
    """
    assert L.GROUP_WEAK_R == 0.50
    assert L.GROUP_GOOD_R == 0.80

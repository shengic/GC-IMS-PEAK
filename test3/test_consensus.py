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

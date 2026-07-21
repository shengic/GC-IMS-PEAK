"""
test_rules.py — Smoke test for rules.py（第七階段規則引擎 + 五條內建規則）。

雙用途：`pytest test/test_rules.py` 或 `python test/test_rules.py`。
用合成假峰驗證邏輯本身，不需真實 .mea。
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

import rules


def sep(t):
    print()
    print("=" * 60)
    print(t)
    print("=" * 60)


def fake_peaks():
    """製造涵蓋各種情境的假峰清單（context floor=200 時）：
    - id 1: 健康。prom=1000, I=1300 → ratio=1000/1100=0.91 → 全部通過
    - id 2: 弱突出度（會被 R001 剔）
    - id 3: 高 flatness plateau（會被 R003 剔）
    - id 4: 落在 RIP 帶（drift_relative=1.0，會被 R004 剔）
    - id 5: 駝峰。prom=100, I=3000 → ratio=0.036 → 會被 R005 剔
    - id 6: 健康，但無 drift_relative（R004 保守放行）
    """
    return [
        dict(peak_id=1, prominence=1000, flatness=0.2, drift_relative=1.6,  intensity=1300),
        dict(peak_id=2, prominence=30,   flatness=0.3, drift_relative=1.5,  intensity=800),
        dict(peak_id=3, prominence=900,  flatness=0.9, drift_relative=1.7,  intensity=1200),
        dict(peak_id=4, prominence=800,  flatness=0.2, drift_relative=1.00, intensity=1100),
        dict(peak_id=5, prominence=100,  flatness=0.2, drift_relative=1.5,  intensity=3000),
        dict(peak_id=6, prominence=800,  flatness=0.2,                       intensity=1100),
    ]


def ids(peaks):
    return [p["peak_id"] for p in peaks]


def test_rules_smoke():
    sep("[1] list_rules(): 五條內建規則都要在")
    rs = rules.list_rules()
    for r in rs:
        print(f"  {r['rule_number']}  {r['name']}  ({r['type']})")
    assert {r["rule_number"] for r in rs} == {"R001", "R002", "R003", "R004", "R005"}

    # 型態核對
    types = {r["rule_number"]: r["type"] for r in rs}
    assert types["R001"] == rules.RULE_TYPE_PER_PEAK
    assert types["R002"] == rules.RULE_TYPE_BATCH
    assert types["R003"] == rules.RULE_TYPE_PER_PEAK
    assert types["R004"] == rules.RULE_TYPE_PER_PEAK
    assert types["R005"] == rules.RULE_TYPE_PER_PEAK_WITH_CONTEXT

    sep("[2] R001 min_prominence 單獨啟用，threshold=50")
    cfg = [{"rule_number": "R001", "enabled": True, "params": {"threshold": 50}}]
    kept, rep = rules.apply_rules(fake_peaks(), cfg)
    print(f"  kept ids = {ids(kept)}  report = {rep}")
    # id=2 prom=30 應被剔，其他保留
    assert 2 not in ids(kept)
    assert {1, 3, 4, 5, 6}.issubset(set(ids(kept)))

    sep("[3] R003 max_flatness 單獨啟用，threshold=0.5")
    cfg = [{"rule_number": "R003", "enabled": True, "params": {"threshold": 0.5}}]
    kept, rep = rules.apply_rules(fake_peaks(), cfg)
    print(f"  kept ids = {ids(kept)}")
    # id=3 flatness=0.9 應被剔
    assert 3 not in ids(kept)

    sep("[4] R004 exclude_rip_band 啟用，half_width=0.02")
    cfg = [{"rule_number": "R004", "enabled": True, "params": {"half_width": 0.02}}]
    kept, rep = rules.apply_rules(fake_peaks(), cfg)
    print(f"  kept ids = {ids(kept)}  rip_missing_warning = {rep['rip_missing_warning']}")
    # id=4 落在 RIP 帶（dr=1.00），應被剔
    # id=6 無 drift_relative，保守放行
    assert 4 not in ids(kept)
    assert 6 in ids(kept)
    assert rep["rip_missing_warning"] is False   # 至少有一峰有 drift_relative

    sep("[4b] R004 但所有峰都無 drift_relative → rip_missing_warning=True")
    peaks_no_dr = [dict(p, drift_relative=None) for p in fake_peaks()]
    for p in peaks_no_dr:
        p.pop("drift_relative", None)
    kept, rep = rules.apply_rules(peaks_no_dr, cfg)
    print(f"  n_out = {rep['n_out']}  rip_missing_warning = {rep['rip_missing_warning']}")
    assert rep["rip_missing_warning"] is True
    assert rep["n_out"] == len(peaks_no_dr)   # 保守放行 → 全部留

    sep("[5] R005 min_relative_steepness，context 提供 floor=200")
    cfg = [{"rule_number": "R005", "enabled": True, "params": {"min_ratio": 0.3}}]
    kept, rep = rules.apply_rules(fake_peaks(), cfg, context={"floor": 200})
    print(f"  kept ids = {ids(kept)}")
    # id=5: prom=100/(3000-200)=0.036 → 剔（駝峰）
    # id=2: prom=30/(800-200)=0.05 → 也剔（R005 本來就同時抓弱峰駝峰）
    # id=1,3,4,6 都有 ratio > 0.3 → 留
    assert 5 not in ids(kept)
    assert 1 in ids(kept) and 6 in ids(kept)

    sep("[5b] R005 但 context 未提供 floor → 保守放行")
    kept, rep = rules.apply_rules(fake_peaks(), cfg)   # 無 context
    print(f"  n_out = {rep['n_out']}")
    assert rep["n_out"] == len(fake_peaks())

    sep("[6] R002 max_candidates 批次型，top_n=2，依 prominence 排序取前 2")
    cfg = [{"rule_number": "R002", "enabled": True, "params": {"top_n": 2}}]
    kept, rep = rules.apply_rules(fake_peaks(), cfg)
    print(f"  kept ids (依 prom 序) = {ids(kept)}")
    # 前 2 名 prom = 1000(id1), 900(id3)
    assert ids(kept) == [1, 3]

    sep("[7] 組合：R001 + R004 + R005 + R002，全部同時開")
    cfg = [
        {"rule_number": "R001", "enabled": True, "params": {"threshold": 50}},
        {"rule_number": "R003", "enabled": True, "params": {"threshold": 0.5}},
        {"rule_number": "R004", "enabled": True, "params": {"half_width": 0.02}},
        {"rule_number": "R005", "enabled": True, "params": {"min_ratio": 0.3}},
        {"rule_number": "R002", "enabled": True, "params": {"top_n": 3}},
    ]
    kept, rep = rules.apply_rules(fake_peaks(), cfg, context={"floor": 200})
    print(f"  kept ids = {ids(kept)}")
    print(f"  report applied = {rep['applied']}")
    # 逐峰剩 id=1,6 (id2 R001, id3 R003, id4 R004, id5 R005 全刷掉)
    # 批次 R002 top_n=3，但只剩 2 → 全保留
    assert set(ids(kept)) == {1, 6}
    assert rep["n_in"] == 6

    sep("[8] Unknown rule number 應被 skip 且記入 report")
    cfg = [{"rule_number": "R999", "enabled": True, "params": {}}]
    kept, rep = rules.apply_rules(fake_peaks(), cfg)
    print(f"  unknown = {rep['unknown_rule_numbers']}")
    assert "R999" in rep["unknown_rule_numbers"]
    assert rep["n_out"] == rep["n_in"]

    sep("[9] enabled=False 的規則跳過")
    cfg = [{"rule_number": "R001", "enabled": False, "params": {"threshold": 10000}}]
    kept, rep = rules.apply_rules(fake_peaks(), cfg)
    print(f"  n_out = {rep['n_out']} (thr=10000 若啟用會剃光，disabled 應全留)")
    assert rep["n_out"] == len(fake_peaks())

    sep("[10] default_config() / load_config() / save_config() round-trip")
    tmp = os.path.join(os.path.dirname(__file__), "_tmp_rules_config.json")
    d = rules.default_config()
    print(f"  default has {len(d)} rules")
    rules.save_config(tmp, d)
    loaded = rules.load_config(tmp)
    assert loaded == d
    os.remove(tmp)

    # 找不到檔案回 default
    loaded = rules.load_config("does_not_exist.json")
    assert loaded == d

    sep("[11] register_rule 保護：重覆編號應 raise")
    try:
        @rules.register_rule("R001", "duplicate", "should fail")
        def _dup(peak, params):
            return True
        raise AssertionError("重覆編號應 raise ValueError")
    except ValueError as e:
        print(f"  [OK] ValueError raised: {e}")

    print()
    print("[OK] all rules.py smoke checks passed")


if __name__ == "__main__":
    test_rules_smoke()

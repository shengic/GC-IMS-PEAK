"""test_preprocess.py — 預處理（`compound_consensus/preprocess.py`）的測試。

同樣以實際踩過的錯為主：

- 選 `GAS/` 得到 0 個樣品（它底下只有子資料夾，一個 `.mea` 都沒有）
- `--dry-run` 竟然會寫檔（採信既有快取有副作用）
- 估時把第一支應用的 `_maxima.npz` 當成「找過峰了」，於是預告 8 分鐘、實際跑 16 分鐘
"""
import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import areas2  # noqa: E402
import rules as rules_mod  # noqa: E402
from compound_consensus import logic as L  # noqa: E402
from compound_consensus import preprocess as P  # noqa: E402

GAS = os.path.join(PROJECT_ROOT, "GAS")


@pytest.fixture()
def rc():
    return rules_mod.load_config(os.path.join(PROJECT_ROOT, "rules_config.json"))


def _mea(path, sample="A 1-1"):
    """寫一個表頭足夠讓 `_read_header_lite` 讀出 Sample 的假 .mea。"""
    path.write_text("Sample = %s\nMachine serial = TEST\n" % sample, encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------- #
# 資料夾解析 —— 選到只有子資料夾的上層
# --------------------------------------------------------------------------- #
def test_parent_folder_resolves_to_subfolders_with_mea(tmp_path):
    """選一個自己沒有 `.mea` 的上層要往下找，不是回 0 個樣品。

    回歸測試：`GAS/` 底下一個 `.mea` 都沒有（66 個檔全在 4 個子資料夾裡），
    選它會得到 0 個樣品、看起來像程式壞了。第二支應用也踩過同一個坑。
    """
    (tmp_path / "batch1").mkdir()
    (tmp_path / "batch2").mkdir()
    (tmp_path / "empty").mkdir()
    _mea(tmp_path / "batch1" / "a.mea")
    _mea(tmp_path / "batch2" / "b.mea")

    got = P.resolve_folders(str(tmp_path))
    assert sorted(os.path.basename(g) for g in got) == ["batch1", "batch2"]
    assert not any("empty" in g for g in got)


def test_folder_with_mea_resolves_to_itself(tmp_path):
    """自己就有 `.mea` 的資料夾不要再往下鑽。"""
    _mea(tmp_path / "a.mea")
    (tmp_path / "sub").mkdir()
    _mea(tmp_path / "sub" / "b.mea")
    assert P.resolve_folders(str(tmp_path)) == [str(tmp_path)]


def test_folder_with_no_mea_anywhere_returns_nothing(tmp_path):
    assert P.resolve_folders(str(tmp_path)) == []


@pytest.mark.skipif(not os.path.isdir(GAS), reason="需要真實的 GAS/")
def test_real_gas_folder_resolves_to_its_batches():
    got = P.resolve_folders(GAS)
    assert len(got) >= 2, "GAS/ 底下應該有多個批次資料夾"
    assert all(os.path.isdir(g) for g in got)


# --------------------------------------------------------------------------- #
# dry run 不可以有副作用
# --------------------------------------------------------------------------- #
def test_dry_run_writes_nothing(tmp_path, monkeypatch, rc):
    """`--dry-run` 一個檔都不能寫。

    回歸測試：採信既有快取（`trust_existing`）會**補寫指紋**，而 `plan()` 在
    dry run 時也會跑——於是一個「只是看看」的指令改變了狀態。會改變狀態的 dry run
    比沒有 dry run 更糟。
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(results))
    folder = tmp_path / "batch"
    folder.mkdir()
    mea = _mea(folder / "a.mea")
    (results / "a.npz").write_bytes(b"")
    (results / "a_peaks2.json").write_text(json.dumps(
        {"params": {"sigma": 1.0, "floor_pct": 85.0, "prom_frac": 0.02,
                    "min_distance": 3, "baseline_applied": False}}), encoding="utf-8")

    before = sorted(p.name for p in results.iterdir())
    P.plan([str(folder)], True, rc, trust_existing=True, write=False)
    assert sorted(p.name for p in results.iterdir()) == before, "dry run 不可以寫檔"

    P.plan([str(folder)], True, rc, trust_existing=True, write=True)
    assert any(p.name.endswith("_fp3.json") for p in results.iterdir()), \
        "非 dry run 時才補寫指紋"


# --------------------------------------------------------------------------- #
# 該做什麼 / 不該做什麼
# --------------------------------------------------------------------------- #
def test_plan_skips_what_is_already_current(tmp_path, monkeypatch, rc):
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(results))
    folder = tmp_path / "batch"
    folder.mkdir()
    mea = _mea(folder / "a.mea")
    (results / "a.npz").write_bytes(b"")
    (results / "a_peaks2.json").write_text("{}", encoding="utf-8")
    (results / "a_peaks2_fp3.json").write_text(json.dumps(
        {"fingerprint": L.params_fingerprint(rc)}), encoding="utf-8")

    items, est = P.plan([str(folder)], True, rc)
    assert items == [] and est == 0


def test_force_redoes_everything(tmp_path, monkeypatch, rc):
    """`--force` 要連已經有的也重做——但那是**選項**，不是預設。"""
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(results))
    folder = tmp_path / "batch"
    folder.mkdir()
    _mea(folder / "a.mea")
    (results / "a.npz").write_bytes(b"")
    (results / "a_peaks2.json").write_text("{}", encoding="utf-8")
    (results / "a_peaks2_fp3.json").write_text(json.dumps(
        {"fingerprint": L.params_fingerprint(rc)}), encoding="utf-8")

    assert P.plan([str(folder)], True, rc)[0] == []
    items, _est = P.plan([str(folder)], True, rc, force=True)
    assert len(items) == 1
    _mea_path, need_npz, need_pk, _ = items[0]
    assert need_npz and need_pk


def test_changing_rules_invalidates_the_cache(tmp_path, monkeypatch, rc):
    """規則參數變了就要重跑——這是不能靠 `areas2` 的快取判斷的部分。

    `areas2.detect_one()` 的快取只比對 `baseline_applied`；`rules_config` 甚至沒被
    記進 `_peaks2.json`，但 R004/R006 的參數會經 `pre_gate_params()` 改變結果。
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(results))
    folder = tmp_path / "batch"
    folder.mkdir()
    _mea(folder / "a.mea")
    (results / "a.npz").write_bytes(b"")
    (results / "a_peaks2.json").write_text("{}", encoding="utf-8")
    (results / "a_peaks2_fp3.json").write_text(json.dumps(
        {"fingerprint": L.params_fingerprint(rc)}), encoding="utf-8")

    assert P.plan([str(folder)], True, rc)[0] == [], "參數沒變就不用重跑"

    changed = [dict(r) for r in rc]
    for r in changed:
        if r.get("rule_number") == "R004":
            r["params"] = dict(r.get("params", {}), half_width=0.05)
    assert L.params_fingerprint(changed) != L.params_fingerprint(rc)
    assert len(P.plan([str(folder)], True, changed)[0]) == 1, "R004 改了就要重跑"


def test_counts_aggregate_across_subfolders(tmp_path, monkeypatch, rc):
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(results))
    for sub, names in (("b1", ["a", "b"]), ("b2", ["c"])):
        d = tmp_path / sub
        d.mkdir()
        for n in names:
            _mea(d / ("%s.mea" % n))
    (results / "a.npz").write_bytes(b"")

    total, npz, pk, excluded, per = P._counts(str(tmp_path), rc)
    assert total == 3
    assert npz == 1
    assert pk == 0
    assert len(per) == 2, "要逐資料夾回報，不是只給總數"


def test_std_and_blank_are_excluded_from_preprocessing(tmp_path, monkeypatch, rc):
    """STD 與空白不是樣品，不該花 55 秒去找峰。"""
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(results))
    folder = tmp_path / "batch"
    folder.mkdir()
    _mea(folder / "a.mea", sample="A 1-1")
    _mea(folder / "blk.mea", sample="Fish meat blank")

    total, _npz, _pk, excluded, _per = P._counts(str(folder), rc)
    assert total == 1, "只有一個是樣品"
    assert any(e["reason"] == "blank" for e in excluded)


# --------------------------------------------------------------------------- #
# 壞掉的 .mea —— 一定要報出來，不能靜靜算成「完成」
# --------------------------------------------------------------------------- #
def test_unreadable_mea_is_reported_not_raised(tmp_path, monkeypatch, rc):
    """讀不動的 `.mea` 要回一個「失敗」字串，而不是把整批炸掉。

    回歸測試：`GAS/藝妓咖啡/FREE_GG_250422_171557_GG_2.mea` 大小正確（77 MB）
    但內容 97.9% 是 0x00/0xFF，表頭整段不見了——跨磁碟機搬移時的複製殘骸。
    `_work()` 要把它變成一筆可以顯示的失敗紀錄；圖形介面才有東西可以講。
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(results))
    # 表頭缺 `Chunks count`，正是那個壞檔的症狀
    bad = _mea(tmp_path / "broken.mea")

    name, status = P._work((bad, True, False, rc))

    assert name == "broken.mea"
    assert status != "OK"
    assert status.startswith("失敗"), status
    assert "ValueError" in status, "要帶著例外型別，不然看不出是哪一類問題"


def test_failed_file_stays_pending_next_run(tmp_path, monkeypatch, rc):
    """失敗的檔不會被記成做過——下一輪還是待處理，計數不會假裝是 14/14。"""
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(results))
    folder = tmp_path / "batch"
    folder.mkdir()
    bad = _mea(folder / "broken.mea")

    P._work((bad, True, False, rc))

    total, npz, _pk, _ex, _per = P._counts(str(folder), rc)
    assert total == 1
    assert npz == 0, "失敗的檔不該留下 .npz，也不該被算成已完成"

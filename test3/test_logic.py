"""第三支應用邏輯層的測試。

慣例同 `test/` 與 `test2/`：需要真實 `.mea` 的測試在檔案不存在時 `skip`，
所以在沒有資料的乾淨 clone 上也能跑。
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import areas2
from compound_consensus import logic as L

GAS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "GAS")


# --------------------------------------------------------------------------- #
# 空白樣品判定 —— 這是本模組存在的其中一個理由，鎖死它
# --------------------------------------------------------------------------- #

def test_blank_detected_from_the_word_not_a_substring():
    """`FISH_MEAT_BLANK` 必須算空白。

    這是回歸測試：`areas2._select_samples()` 用的是 `"blk" in basename.lower()`，
    而 `blank` 裡**沒有** `blk` 這個子字串，所以該檔會被當成樣品混進矩陣
    （實測鱸魚那批確實混進去了）。
    """
    assert L._tokens("Fish meat blank") & L.BLANK_TOKENS
    assert L._tokens("BLK") & L.BLANK_TOKENS
    assert L._tokens("Blindwert") & L.BLANK_TOKENS


def test_ordinary_sample_names_are_not_blanks():
    """不能誤殺：一般樣品代號不可以被當成空白。"""
    for name in ("A 1-1", "GR-1", "NHM15_2", "EHM30-3", "Blackberry", "C 1-2"):
        assert not (L._tokens(name) & L.BLANK_TOKENS), name


# --------------------------------------------------------------------------- #
# 相似度 —— 缺值不可以被當成 0
# --------------------------------------------------------------------------- #

def test_missing_values_drop_the_region_instead_of_becoming_zero():
    """`None` 代表沒量到。補 0 會讓「沒量到」變成「量到零」——比較分組時意義完全不同。

    構造：三個檔在區域 0 完全一致，區域 1 有一個檔缺值。若把 None 當 0，
    區域 1 會製造出巨大的假差異；正確作法是整欄捨棄。
    """
    # 三個檔要彼此不同：全部一樣的話逐列置中之後每列都是常數，`np.corrcoef`
    # 會除以 0 而回 nan——那是測試資料退化，不是被測的行為。
    profiles = [[10.0, 100.0, 30.0, 40.0],
                [12.0, None, 28.0, 44.0],
                [90.0, 100.0, 8.0, 15.0]]
    corr, n_used = L.similarity_matrix(profiles)
    assert n_used == 3, "帶 None 的那一欄應該被捨棄"
    assert corr.shape == (3, 3)


def test_similarity_refuses_when_too_few_regions_survive():
    """可用區域太少就明講，不要回一個看起來正常的數字。"""
    with pytest.raises(ValueError, match="不足以判斷相似度"):
        L.similarity_matrix([[1.0, None], [None, 2.0], [3.0, 4.0]])


def test_identical_profiles_correlate_perfectly():
    a = [1.0, 10.0, 100.0, 1000.0, 5.0]
    corr, _ = L.similarity_matrix([a, a, [5.0, 1.0, 700.0, 3.0, 90.0]])
    assert corr[0, 1] == pytest.approx(1.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# 建議與評估
# --------------------------------------------------------------------------- #

def test_suggest_partners_is_ranked_and_excludes_self():
    files = ["a", "b", "c", "d"]
    corr = np.array([[1.0, 0.2, 0.9, 0.5],
                     [0.2, 1.0, 0.1, 0.3],
                     [0.9, 0.1, 1.0, 0.4],
                     [0.5, 0.3, 0.4, 1.0]])
    out = L.suggest_partners(0, corr, files)
    assert [o["file"] for o in out] == ["c", "d", "b"]
    assert "a" not in [o["file"] for o in out]
    assert all(o["suggested"] is None for o in out), "沒給門檻就不要替使用者畫線"
    out2 = L.suggest_partners(0, corr, files, threshold=0.6)
    assert [o["suggested"] for o in out2] == [True, False, False]


def test_nearest_neighbour_check_counts_correctly():
    files = ["A1", "A2", "B1", "B2"]
    corr = np.array([[1.0, 0.9, 0.1, 0.2],
                     [0.9, 1.0, 0.3, 0.1],
                     [0.1, 0.3, 1.0, 0.8],
                     [0.2, 0.1, 0.8, 1.0]])
    res = L.nearest_neighbour_check(corr, files, lambda f: f[0])
    assert res["hits"] == 4 and res["rate"] == 1.0


# --------------------------------------------------------------------------- #
# 需要真實資料
# --------------------------------------------------------------------------- #

def _folder(name):
    p = os.path.join(GAS, name)
    if not os.path.isdir(p):
        pytest.skip(f"需要真實資料夾 {name}")
    return p


def test_std_and_blank_are_excluded_with_a_visible_reason():
    """排除必須說得出理由——靜靜少掉檔案正是本專案防的那類問題。"""
    folder = _folder("Coffee-bean")
    samples, excluded = L.select_samples(folder)
    reasons = {os.path.basename(e["file"]): e["reason"] for e in excluded}
    assert reasons.get("260625_141215_STD.mea") == "std"
    assert reasons.get("260625_021447_BLK.mea") == "blank"
    assert all(e["detail"] for e in excluded), "每一項排除都要有可顯示的說明"
    assert not any("STD" in os.path.basename(s) for s in samples)


def test_fish_meat_blank_is_excluded_in_the_real_folder():
    """鱸魚那批的空白叫 `Fish meat blank`，是這條規則真正要擋的檔。"""
    folder = _folder("海洋大學 水產養殖系 郭裔培助理教授 鱸魚")
    samples, excluded = L.select_samples(folder)
    names = [os.path.basename(s) for s in samples]
    assert "260210_095729_FISH_MEAT_BLANK.mea" not in names
    assert any(e["reason"] == "blank" for e in excluded)


def test_cache_state_distinguishes_the_two_stages():
    """「讀過檔」與「找過峰」是兩件事，UI 要分別顯示。"""
    folder = _folder("Coffee-bean")
    samples, _ = L.select_samples(folder)
    st = L.cache_state(samples[:3])
    assert {"file", "has_npz", "has_peaks2", "has_maxima"} <= set(st[0])
    cost = L.scan_cost(samples[:3])
    assert cost["est_seconds"] >= 0


def test_scan_cost_counts_peaks2_not_maxima(tmp_path, monkeypatch):
    """估時要數本應用真的會做的工作。

    回歸測試：`_maxima.npz` 是**第一支應用**的快取，而 `areas2.detect_one()` 只認
    `_peaks2.json`。原本拿 `has_maxima` 當「找過峰」，於是 Coffee-bean 估出 9 個待偵測、
    實際跑了 18 個——等待時間是預告的兩倍。估錯時間也是一種無聲的錯誤數字。
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(results))
    mea = str(tmp_path / "x.mea")
    (results / "x.npz").write_bytes(b"")
    (results / "x_maxima.npz").write_bytes(b"")      # 第一支的快取，幫不上忙

    cost = L.scan_cost([mea])
    assert cost["n_need_read"] == 0, "npz 已存在"
    assert cost["n_need_detect"] == 1, "有 _maxima.npz 也還是要重找峰"

    (results / "x_peaks2.json").write_text("{}", encoding="utf-8")
    assert L.scan_cost([mea])["n_need_detect"] == 0

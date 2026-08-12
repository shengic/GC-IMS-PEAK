#!/usr/bin/env python3
"""
test_baseline.py — 基線扣除（AsLS）的性質測試

這裡鎖的不是「跑得動」，是三件會靜默出錯的事：
  1. 帶狀求解器必須等於參考的稀疏解（我們為了速度換了解法，不能換掉正確性）
  2. 峰高相對於**自身局部基線**必須保留（基線扣除最怕悄悄把訊號一起削掉）
  3. λ 的下限由**本批資料最寬的峰**決定，不能沿用外部預設

也可直接跑：python test/test_baseline.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import baseline  # noqa: E402


def _synthetic(n=3000, seed=1, peaks=((400, 10, 300), (1500, 9, 80),
                                      (2200, 14, 500))):
    """已知真值：斜的、尾端上翹的基線 + 若干高斯峰 + 雜訊。"""
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=float)
    base = 40 + 0.03 * t + 60 * np.exp((t - n) / 600)
    sig = np.zeros(n)
    for c, w, h in peaks:
        sig += h * np.exp(-0.5 * ((t - c) / w) ** 2)
    return t, base, sig, base + sig + rng.normal(0, 1.5, n)


def test_banded_solver_matches_the_sparse_reference():
    """我們用帶狀求解器換取速度；它必須給出與稀疏解相同的基線。

    gc-ims-tools 用 `spsolve`（一般稀疏解），本專案改用 `solveh_banded`——因為
    W + λDᵀD 是五對角對稱正定，帶狀解是 O(n)，在 20413 列上差距是數量級。
    換解法就必須證明沒換掉答案，否則「更快」是拿正確性換來的。
    """
    from scipy import sparse
    from scipy.sparse.linalg import spsolve

    def reference(y, lam, p=1e-3, niter=20):
        L = len(y)
        D = sparse.diags([1, -2, 1], [0, -1, -2], shape=(L, L - 2),
                         dtype=float).tocsc()
        w = np.ones(L)
        for _ in range(niter):
            W = sparse.spdiags(w, 0, L, L)
            z = spsolve((W + lam * D.dot(D.transpose())).tocsc(), w * y)
            w = p * (y > z) + (1 - p) * (y < z)
        return z

    for n, lam in ((300, 1e5), (1200, 1e7)):
        _, _, _, y = _synthetic(n=n)
        ref = reference(y, lam)
        ours = baseline.asls(y, lam=lam)
        rel = np.abs(ref - ours).max() / np.median(np.abs(ref))
        assert rel < 1e-5, f"n={n} lam={lam:g}: 相對誤差 {rel:.2e}"


def test_peak_height_above_local_baseline_is_preserved():
    """扣完之後，峰相對於自身局部基線的高度必須不變。

    這是本模組的核心保證。量的是「峰頂 − 峰旁背景」而非峰頂絕對值——絕對值本來就
    該降（基線被扣掉了），會出事的是**相對高度**被削掉而沒人發現。
    """
    peaks = ((400, 10, 300), (1500, 9, 80), (2200, 14, 500))
    t, true_base, _, y = _synthetic(peaks=peaks)
    corrected = y - baseline.asls(y)

    for c, w, h in peaks:
        i = int(c)
        # 峰旁 ±（6σ 外）的背景，兩邊各取一段中位數
        left = slice(max(0, i - 12 * int(w)), max(1, i - 6 * int(w)))
        before = y[i] - np.median(y[left])
        after = corrected[i] - np.median(corrected[left])
        assert abs(after - before) / before < 0.10, (
            f"峰 @{i} 相對高度變了 {before:.1f} → {after:.1f}")


def test_flat_input_is_left_alone():
    """沒有基線可扣時不該亂動資料——扣一個不存在的東西是最容易被忽略的失效。"""
    n = 2000
    t = np.arange(n, dtype=float)
    y = 100 + 200 * np.exp(-0.5 * ((t - 1000) / 12) ** 2)
    corrected = y - baseline.asls(y)
    assert corrected[1000] > 0.98 * 200, "平坦基線上峰高不該掉"
    assert abs(np.median(corrected[:600])) < 2.0, "平坦區應被壓到接近 0"


def test_lambda_floor_is_set_by_the_widest_real_peak():
    """λ 太小會把寬峰當基線吃掉——這是選 λ 的唯一硬條件。

    本批實測最寬的真實峰 σ≈222 列（見 baseline.py 檔頭的表）。這裡守住兩件事：
    專案預設的 λ 對該寬度是安全的，而外部借來的 1e7 **不是**——後者正是不能照抄
    別人預設值的理由，留在測試裡當證據。
    """
    n = 20413
    t = np.arange(n, dtype=float)
    base = 40 + 0.006 * t + 80 * np.exp((t - n) / 2500)
    y = base + 400 * np.exp(-0.5 * ((t - 10000) / 222.0) ** 2)

    keep_default = (y - baseline.asls(y, lam=baseline.DEFAULT_LAM))[10000] / 400
    keep_borrowed = (y - baseline.asls(y, lam=1e7))[10000] / 400

    assert keep_default > 0.97, (
        f"預設 λ={baseline.DEFAULT_LAM:g} 只保留寬峰 {keep_default:.1%}")
    assert keep_borrowed < 0.80, (
        "gc-ims-tools 的 1e7 若在此資料上也安全，本檔頭那段選 λ 的理由就該重寫："
        f"實得 {keep_borrowed:.1%}")


def test_row_stride_does_not_change_the_baseline():
    """降採樣求基線是為了速度；基線是被 λ 強制平滑的低頻量，形狀不該因此改變。"""
    rng = np.random.default_rng(3)
    n_rt, n_dt = 4000, 12
    t = np.arange(n_rt, dtype=float)
    surf = np.empty((n_rt, n_dt))
    for j in range(n_dt):
        surf[:, j] = (30 + 0.02 * t + 400 * np.exp(-0.5 * ((t - 1800) / 40) ** 2)
                      + rng.normal(0, 1.0, n_rt))

    full, _ = baseline.correct_rt_baseline(surf, row_stride=1)
    fast, info = baseline.correct_rt_baseline(surf, row_stride=8)
    assert info["row_stride"] == 8
    diff = np.abs(full.astype(float) - fast.astype(float))
    assert np.median(diff) < 1.0, f"降採樣改變了結果，中位差 {np.median(diff):.2f}"


def test_correct_rt_baseline_does_not_mutate_input():
    """呼叫端常要拿原始面與扣完的面對照，就地修改會讓那件事做不到。"""
    _, _, _, y = _synthetic(n=500)
    surf = np.tile(y[:, None], (1, 4))
    before = surf.copy()
    baseline.correct_rt_baseline(surf, row_stride=1)
    assert np.array_equal(surf, before), "correct_rt_baseline 就地改了輸入"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
    print("all baseline checks passed")

#!/usr/bin/env python3
"""
baseline.py  —  保留時間方向的基線扣除（非對稱最小平方，AsLS）
Version: 3.2 — by Albert Sheng

為什麼需要這個
--------------
本專案原本只有「取 85 百分位當 floor」這一種背景處理。那是一條**水平**線，扣不掉
**斜的**東西——而 GC-IMS 的保留時間方向本來就會漂：溫度程序升溫使管柱流失
（column bleed）隨時間增加，基線跟著抬高。後果是晚出峰坐在較高的基座上，
`prominence = 峰高 − 鞍點高` 雖然是相對量、不受整體平移影響，但**基線在一顆峰的
寬度內若有斜率**，鞍點會被抬起，突出度就被系統性低估——愈晚出的峰被壓得愈多。

演算法（Eilers & Boelens 2005, Asymmetric Least Squares Smoothing）
------------------------------------------------------------------
對每一條漂移管道（固定 drift index，沿 RT 取一整條），求 z 最小化

    Σ wᵢ(yᵢ − zᵢ)² + λ Σ (Δ²zᵢ)²

第一項要求貼合資料、第二項（二階差分）要求平滑；λ 愈大基線愈硬。關鍵在權重 w 每輪
重算：**資料點高於目前基線時只給極小的權重 p**（預設 1e-3），低於時給 1−p。所以峰
（在基線之上）幾乎不參與擬合，基線只跟著谷底走——這正是「不把峰一起削掉」的機制。

與 gc-ims-tools 的關係
----------------------
演算法與參數預設取自 `ims/utils.py:asymcorr`（gc-ims-tools 0.1.10，Food Chemistry
2022），**只借演算法，不引入該套件**——它會連帶拉進 scikit-learn / seaborn / h5py /
dtwalign / PyWavelets 等 11 個直接依賴，而我們要的只有這十幾行。本檔另外做了兩件
原版沒有的事：改用帶狀求解器（見 `_solve_banded_pentadiag`），以及對整批資料處理時
的降採樣加速（見 `correct_rt_baseline`）。

**這個模組不改變任何既有行為**：`peaks.py` 需明確傳 `--baseline` 才會啟用，
產物並記下用了什麼參數，避免有無扣基線的結果被混在一起而無從分辨。
"""

import numpy as np

__all__ = ["asls", "correct_rt_baseline", "DEFAULT_LAM", "DEFAULT_P"]

# --------------------------------------------------------------------------- #
# λ **不是照抄來的**。gc-ims-tools 用 1e7；在本專案的資料上那個值會把寬峰吃掉。
#
# λ 決定「基線允許多彎」。峰若比基線還平滑，就會被當成基線扣掉——所以 λ 的下限由
# **本批資料裡最寬的真實峰**決定，不能沿用別人的預設。實測 260625_141215_STD 前 12
# 強的峰在 RT 方向的等效 σ：中位 63 列，最寬 **222 列**（rt_index 2242，也就是那顆
# 尚未確認身分的最強峰）。
#
# 在真實列數 n=20413 上掃 λ，合成一條「線性漂移 + 尾端上翹」的基線再疊上該寬度的峰：
#
#   λ       σ=63 保留   σ=222 保留   基線殘差(中位/最大)
#   1e7       97.9%        63.2%        0.00 / 1.09     ← gc-ims-tools 預設，寬峰掉 37%
#   1e9       99.9%        97.2%        0.06 / 7.99
#   1e10     100.0%        99.4%        0.23 / 17.99
#   1e11     100.0%        99.9%        0.44 / 32.59    ← 採用
#   1e12     100.2%       100.0%        3.33 / 51.60
#
# 取 1e11：寬峰幾乎不動，基線殘差中位 0.44（基線量級約 100）。往上 λ 換得的峰保留
# 已經見底，換來的卻是基線跟不上尾端上翹。
#
# **兩個前提，變了就要重測**：(a) 峰寬——換管柱或換溫度程序會改變；(b) RT 軸的列數
# ——λ 的效果與取樣密度有關，同樣的 λ 在 n=6000 與 n=20413 上行為不同（實測過）。
# --------------------------------------------------------------------------- #
DEFAULT_LAM = 1e11
DEFAULT_P = 1e-3
DEFAULT_NITER = 20


def _pentadiag_bands(n, lam):
    """回傳 λ·DᵀD 的上三角帶狀表示（供 `scipy.linalg.solveh_banded`）。

    D 是二階差分算子，DᵀD 為五對角。直接用稀疏矩陣 spsolve 也對，但帶狀求解器
    對這種結構是 O(n)，在 20000 列的真實資料上差距是數量級——而正確性由
    `test_baseline.py` 對照稀疏解驗證，不是靠這裡的推導。
    """
    main = np.full(n, 6.0)
    main[0] = main[-1] = 1.0
    main[1] = main[-2] = 5.0
    off1 = np.full(n - 1, -4.0)
    off1[0] = off1[-1] = -2.0
    off2 = np.ones(n - 2)

    ab = np.zeros((3, n))
    ab[2] = main                    # 對角線
    ab[1, 1:] = off1                # 上一條
    ab[0, 2:] = off2                # 上兩條
    return ab * lam


def asls(y, lam=DEFAULT_LAM, p=DEFAULT_P, niter=DEFAULT_NITER, _bands=None):
    """單條訊號的基線，回傳**基線本身**（不是扣完的結果）。

    回基線而非扣完的訊號，是為了讓呼叫端能檢查、能疊圖、也能決定要不要夾住負值——
    扣完才回傳的話這些都做不到。
    """
    from scipy.linalg import solveh_banded

    y = np.asarray(y, dtype=np.float64)
    n = y.size
    if n < 5:                       # 太短無法定義二階差分
        return np.zeros_like(y)

    bands = _pentadiag_bands(n, lam) if _bands is None else _bands
    w = np.ones(n)
    z = y
    for _ in range(niter):
        ab = bands.copy()
        ab[2] += w                  # W + λDᵀD，只有對角線隨迭代改變
        z = solveh_banded(ab, w * y, lower=False)
        w = p * (y > z) + (1.0 - p) * (y < z)
    return z


def correct_rt_baseline(intensity, lam=DEFAULT_LAM, p=DEFAULT_P,
                        niter=DEFAULT_NITER, row_stride=1, clip_negative=True,
                        progress=None):
    """沿保留時間（axis 0）逐一漂移管道扣基線，回傳 (corrected, info)。

    參數
    ----
    row_stride : int
        >1 時先每 n 列取樣一次求基線，再線性內插回全解析度。基線本身是被 λ 強制
        平滑的低頻量，降採樣求解不會損失它的形狀，但成本降為 1/n。真實資料
        （20413 列 × 3150 管道）用 stride=8 從數分鐘降到十幾秒。
    clip_negative : bool
        扣完把負值夾到 0。強度是計數，負值沒有物理意義，且下游的 floor 百分位與
        突出度都預設非負。

    **不就地修改輸入**——呼叫端常常要拿原始面與扣完的面對照。
    """
    a = np.asarray(intensity, dtype=np.float64)
    n_rt, n_dt = a.shape
    stride = max(1, int(row_stride))

    rows = np.arange(0, n_rt, stride)
    if rows[-1] != n_rt - 1:        # 保證涵蓋末列，否則尾端要外插
        rows = np.append(rows, n_rt - 1)
    sub = a[rows, :]
    bands = _pentadiag_bands(rows.size, lam)

    base_sub = np.empty_like(sub)
    for j in range(n_dt):
        base_sub[:, j] = asls(sub[:, j], lam=lam, p=p, niter=niter, _bands=bands)
        if progress and (j % 256 == 0):
            progress(j, n_dt)

    if stride == 1:
        base = base_sub
    else:
        full = np.arange(n_rt)
        base = np.empty_like(a)
        for j in range(n_dt):
            base[:, j] = np.interp(full, rows, base_sub[:, j])

    out = a - base
    if clip_negative:
        np.clip(out, 0, None, out=out)

    info = {
        "method": "asls",
        "lam": lam, "p": p, "niter": niter,
        "row_stride": stride,
        "clip_negative": bool(clip_negative),
        "baseline_median": float(np.median(base)),
        "baseline_max": float(base.max()),
        # λ 不是抄來的：gc-ims-tools 用 1e7，在本專案資料上會把 σ≈222 列的寬峰
        # 吃掉約 37%。現值由本批最寬的真實峰量測決定，見 baseline.py 檔頭。
        "reference": "Eilers & Boelens 2005 (AsLS); algorithm via gc-ims-tools "
                     "0.1.10, lam re-measured for this project",
    }
    return out.astype(intensity.dtype, copy=False), info

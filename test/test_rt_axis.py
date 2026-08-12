"""
test_rt_axis.py — 保留時間軸公式與版本標記（2026-08-12 修正）

依專案慣例，同時是 pytest 測試與可執行的除錯腳本。

鎖住的意圖：

1. **`rt_step_ms` 必須是 `(averages + 1) × trigger_repetition`，不是 `averages ×`。**
   舊公式讓整條保留時間軸被壓縮成實際的 6/7（短 16.7%）。三條獨立證據見
   `readGAS.RT_AXIS_VERSION` 的註解；其中最強的一條是拿 `gc-ims-tools` 0.1.10
   讀同一支 `.mea` 交叉比對——強度矩陣 20413×3150 逐格相同、漂移軸一致，**只有
   保留時間軸差 7/6**。

2. **舊產物必須可被偵測，不能靜默混用。** RT 差 16.7% 卻沒有任何錯誤訊息，是最難
   察覺的一種資料污染：找峰照跑、圖照畫、RI 還是對的（見第 3 點），只有 retention_s
   悄悄錯掉。所以 `.npz` 要帶 `rt_axis_version`，缺欄位一律視為版本 1。

3. **RI 不受此修正影響。** RI 在 `log10(RT)` 空間分段線性內插，錨點與查詢值同時平移
   `log10(7/6)`，而分段線性內插對 x 的平移不變。這一點必須有測試，否則日後有人會
   誤以為「RT 錯了所以 RI 也要重算」而去動已經正確的東西。

不依賴任何真實資料檔（自製最小 .mea）。
"""
import os
import struct
import sys

import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import calibration as cal
import peaks as P
import readGAS
import reference_series as rs


AVERAGES = 6
TRIG_MS = 21
N_RT, N_DT = 5, 4


def _make_mea(path, averages=AVERAGES, trig=TRIG_MS, n_rt=N_RT, n_dt=N_DT):
    """最小合成 .mea：ASCII 表頭 + int16-LE 資料區，維度取小值以便逐格核對。"""
    header = (
        f'Chunks count = {n_rt}\n'
        f'Chunk sample count = {n_dt}\n'
        f'Chunk sample rate = 150 [kHz]\n'
        f'Chunk trigger repetition = {trig} [ms]\n'
        f'Chunk averages = {averages}\n'
        f'Machine type = "FlavourSpec"\n'
        f'Sample = "SYN"\n'
    ).encode("latin-1")
    data = np.arange(n_rt * n_dt, dtype="<i2")
    with open(path, "wb") as f:
        f.write(header)
        f.write(data.tobytes())
    return data.reshape(n_rt, n_dt)


# --------------------------------------------------------------------------- #
# 1. 公式本身
# --------------------------------------------------------------------------- #
def test_rt_step_includes_the_plus_one(tmp_path):
    mea = str(tmp_path / "syn.mea")
    expected = _make_mea(mea)
    data, header, axes = readGAS.read_mea(mea)

    np.testing.assert_array_equal(data, expected)          # 資料區定位仍正確
    assert axes["rt_step_ms"] == (AVERAGES + 1) * TRIG_MS, \
        "rt_step 必須是 (averages+1)×trigger，少了 +1 會讓 RT 短 16.7%"
    assert axes["rt_step_ms"] != AVERAGES * TRIG_MS        # 明確排除舊公式
    # 軸本身：第 i 個 chunk 的保留時間 = i × rt_step
    assert axes["retention_s"][1] == (AVERAGES + 1) * TRIG_MS / 1000.0
    # 漂移軸不受此修正影響
    assert axes["dt_step_ms"] == 1.0 / 150.0


def test_old_formula_would_be_short_by_one_seventh(tmp_path):
    """把差異量化下來：舊/新 = 6/7。日後若有人「順手改回去」，這裡會指出後果。"""
    mea = str(tmp_path / "syn.mea")
    _make_mea(mea)
    _, _, axes = readGAS.read_mea(mea)
    old_total = N_RT * AVERAGES * TRIG_MS / 1000.0
    new_total = axes["retention_s"][-1] + axes["rt_step_ms"] / 1000.0
    assert abs(old_total / new_total - AVERAGES / (AVERAGES + 1)) < 1e-12


# --------------------------------------------------------------------------- #
# 2. 版本標記與偵測
# --------------------------------------------------------------------------- #
def test_npz_carries_the_axis_version(tmp_path):
    mea = str(tmp_path / "syn.mea")
    _make_mea(mea)
    _, _, axes = readGAS.read_mea(mea)
    npz = str(tmp_path / "syn.npz")
    readGAS.export_npz(np.zeros((N_RT, N_DT), dtype="<i2"), axes, npz)
    with np.load(npz) as z:
        assert "rt_axis_version" in z.files
        assert int(z["rt_axis_version"]) == readGAS.RT_AXIS_VERSION


def test_npz_without_version_is_treated_as_the_old_axis(tmp_path):
    """舊 .npz 沒有這個欄位——必須當成版本 1 並警告，不能預設成現行版。"""
    npz = str(tmp_path / "legacy.npz")
    np.savez_compressed(npz, intensity=np.zeros((N_RT, N_DT), dtype="<i2"),
                        drift_ms=np.arange(N_DT, dtype=float),
                        retention_s=np.arange(N_RT, dtype=float))
    _i, _d, _r, meta = P.load_surface(npz)
    assert meta["rt_axis_version"] == 1
    assert P.warn_if_stale_rt_axis(meta["rt_axis_version"], npz) is True
    assert P.warn_if_stale_rt_axis(readGAS.RT_AXIS_VERSION, npz) is False


def test_fresh_npz_round_trips_without_warning(tmp_path):
    mea = str(tmp_path / "syn.mea")
    _make_mea(mea)
    _, _, axes = readGAS.read_mea(mea)
    npz = str(tmp_path / "syn.npz")
    readGAS.export_npz(np.zeros((N_RT, N_DT), dtype="<i2"), axes, npz)
    _i, _d, _r, meta = P.load_surface(npz)
    assert meta["rt_axis_version"] == readGAS.RT_AXIS_VERSION
    assert P.warn_if_stale_rt_axis(meta["rt_axis_version"], npz) is False


def test_mea_path_reports_current_version(tmp_path):
    mea = str(tmp_path / "syn.mea")
    _make_mea(mea)
    _i, _d, _r, meta = P.load_surface(mea)
    assert meta["rt_axis_version"] == readGAS.RT_AXIS_VERSION


# --------------------------------------------------------------------------- #
# 3. RI 對這個修正免疫
# --------------------------------------------------------------------------- #
def test_ri_is_invariant_under_the_rt_axis_correction():
    """同一顆物理峰，在新舊兩種 RT 軸下算出的 RI 必須相同。

    理由（也是不該因為 RT 修正而重算 RI 的原因）：RI 在 log10(RT) 空間內插，錨點與
    查詢值同時平移 log10(7/6)，分段線性內插對 x 平移不變。若哪天有人把內插改成
    非平移不變的形式（例如混入絕對 RT 項），這個測試會失敗——那正是該被擋下的改動。
    """
    dt = rs.REFERENCE_SERIES["ketone"]["dt_values"]
    old = [{"retention_s": rt, "drift_relative": d, "intensity": 3000}
           for rt, d in zip([334.0, 400.3, 522.4, 697.2, 949.0, 1305.7], dt)]
    k = (AVERAGES + 1) / AVERAGES
    new = [dict(p, retention_s=p["retention_s"] * k) for p in old]

    c_old, _ = cal.build_from_std_peaks(old, header={}, series_key="ketone")
    c_new, _ = cal.build_from_std_peaks(new, header={}, series_key="ketone")
    f_old, f_new = cal.make_rt_to_ri(c_old), cal.make_rt_to_ri(c_new)

    for rt in (350.0, 600.0, 900.0, 1200.0, 200.0, 2000.0):   # 含錨點範圍外
        ri_old, ex_old = f_old(rt)
        ri_new, ex_new = f_new(rt * k)
        assert abs(ri_new - ri_old) < 1e-9, f"RT={rt}: RI 應不變"
        assert ex_old == ex_new, f"RT={rt}: 外插旗標也應不變"


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    def sep(t):
        print(f"\n{'=' * 68}\n{t}\n{'=' * 68}")

    with tempfile.TemporaryDirectory() as d:
        sep("[1] 合成 .mea 的軸")
        mea = os.path.join(d, "syn.mea")
        _make_mea(mea)
        _, _, axes = readGAS.read_mea(mea)
        print(f"  averages={AVERAGES} trig={TRIG_MS}ms")
        print(f"  舊公式 rt_step = {AVERAGES * TRIG_MS} ms")
        print(f"  新公式 rt_step = {axes['rt_step_ms']:g} ms   (版本 {axes['rt_axis_version']})")
        print(f"  比值 = {AVERAGES / (AVERAGES + 1):.6f}  (= 6/7)")

    sep("[2] RI 不受影響")
    test_ri_is_invariant_under_the_rt_axis_correction()
    print("  新舊軸下同一顆峰的 RI 完全相同（差 < 1e-9），外插旗標亦同。")

"""
test_dt_convert.py — Smoke test for dt_convert.py（第二階段 K0 換算）。

雙用途：`pytest test/test_dt_convert.py` 或 `python test/test_dt_convert.py`。
使用真實 .mea 表頭 + 合成 profile / 假峰。
"""
import os
import sys
import json
import tempfile
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import readGAS
import dt_convert as dtc


def sep(t):
    print()
    print("=" * 60)
    print(t)
    print("=" * 60)


def test_both_modes_return_the_same_quantity():
    """兩個模式必須回傳同一個物理量（K0），不能互為倒數。

    2026-08-12 之前 raw_parameters 回傳 1/K0、standard_based 回傳 K0，兩者卻同寫進
    peak["k0_value"]——同一顆峰在不同模式下差一次取倒數，而且沒有任何地方會報錯。
    這裡用「讓兩個模式在物理上等價」的方式檢查：把 raw_parameters 的
    ah×L² 當成 instrument_constant 餵給 standard_based，兩者應得到同一個數字。
    """
    dt_raw, srate, U = 680, 150, 2700
    L, T_C, P = 5.3, 45.0, 1013.0
    ah = (273.0 / (273.0 + T_C)) * (P / 1013.0)

    raw = dtc.k0_from_raw_params(dt_raw, srate, L, T_C, P, U)
    std = dtc.k0_from_instrument_constant(dt_raw, srate, ah * L**2, U)
    assert abs(raw - std) < 1e-9, "同一組物理條件下，兩個模式必須給出同一個 K0"
    assert abs(raw / std - 1.0) < 1e-9, "尤其不能相差一次取倒數"


def test_dt_convert_smoke():
    # ---- 數學核心 ----
    sep("[1] k0_from_instrument_constant: 純函式數學檢查")
    # 選 dt_raw=680（前面 rip 驗過的 RIP 位置）、srate=150 kHz、
    # instrument_constant 隨意選 20（純數學驗證）、U=2700 V
    k0 = dtc.k0_from_instrument_constant(680, 150, 20, 2700)
    # t_d_s = 680/150/1000 = 0.0045333 s
    # K0 = 20 / (0.0045333 * 2700) = 20/12.24 = 1.6339
    expected = 20 / (680 / 150 / 1000 * 2700)
    print(f"  K0 = {k0}  expected = {expected}")
    assert abs(k0 - expected) < 1e-9

    sep("[2] k0_from_raw_params: 純函式數學檢查（L=5.3cm, T=45°C, P=1013mbar, U=2700V）")
    k0 = dtc.k0_from_raw_params(680, 150, 5.3, 45, 1013, 2700)
    # t_d_s = 0.0045333
    # ah = (273/318) * (1013/1013) = 0.8585
    # K0 = 0.8585 * 5.3² / (0.0045333 * 2700) = 0.8585 * 28.09 / 12.24 = 1.9711
    # 2026-08-12 起回傳 K0 本身（不再是 1/K0）——與 k0_from_instrument_constant()
    # 統一慣例。外部佐證：gc-ims-tools 的 calc_reduced_mobility() 同形且回傳 K0。
    t = 680 / 150 / 1000
    ah = (273 / 318) * (1013 / 1013)
    expected = ah * 5.3**2 / (t * 2700)
    print(f"  K0 = {k0}  expected = {expected}")
    assert abs(k0 - expected) < 1e-9
    assert k0 > 1.0, "K0 應為 ~1.97 量級；若得到 ~0.51 代表又回傳了倒數"

    # ---- 表頭欄位抽取 ----
    sep("[3] extract_confirmed_params: 真實 260625_141215_STD.mea")
    _, header, _ = readGAS.read_mea(
        os.path.join(PROJECT_ROOT, "GAS", "嘉義大學＿咖啡發酵", "260625_141215_STD.mea")
    )
    params = dtc.extract_confirmed_params(header)
    print(f"  L_cm = {params['L_cm']} (from 'nom Drift Tube Length' µm → cm)")
    print(f"  U_V = {params['U_V']} (from 'nom Drift Potential Difference')")
    print(f"  sample_rate_khz = {params['sample_rate_khz']} (from 'Chunk sample rate')")
    assert params["L_cm"] == 5.3   # 53000 µm / 10000
    assert params["U_V"] == 2700
    assert params["sample_rate_khz"] == 150

    # ---- profile I/O ----
    sep("[4] calibration_profile.json load/validate")
    with tempfile.TemporaryDirectory() as tmpdir:
        # standard_based OK
        good = {"profile_name": "test", "k0_calibration": {
            "mode": "standard_based", "instrument_constant": 20.0,
            "calibrated_from": {"compound": "test", "known_k0": 1.85, "date": "2026-07-17"},
        }}
        path = os.path.join(tmpdir, "good.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(good, f)
        loaded = dtc.load_calibration_profile(path)
        assert loaded["k0_calibration"]["mode"] == "standard_based"
        print("  standard_based profile 通過驗證")

        # standard_based 缺 instrument_constant → ValueError
        bad1 = {"profile_name": "bad", "k0_calibration": {"mode": "standard_based"}}
        path = os.path.join(tmpdir, "bad1.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(bad1, f)
        try:
            dtc.load_calibration_profile(path)
            raise AssertionError("應 raise ValueError")
        except ValueError as e:
            print(f"  [OK] 缺 instrument_constant 觸發 ValueError: {e}")

        # invalid mode → ValueError
        bad2 = {"k0_calibration": {"mode": "garbage"}}
        path = os.path.join(tmpdir, "bad2.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(bad2, f)
        try:
            dtc.load_calibration_profile(path)
            raise AssertionError("應 raise ValueError")
        except ValueError as e:
            print(f"  [OK] 非法 mode 觸發 ValueError: {e}")

    # ---- compute_k0 分派 ----
    sep("[5] compute_k0: unavailable 模式回 None + 標記原因")
    profile = dtc.default_profile_unavailable("no standard yet")
    k0, mode, reason = dtc.compute_k0(680, header, profile)
    print(f"  k0={k0}  mode={mode}  reason={reason}")
    assert k0 is None
    assert mode == "unavailable"

    sep("[6] compute_k0: standard_based，用 header + 假 instrument_constant")
    profile = {"k0_calibration": {"mode": "standard_based", "instrument_constant": 20.0}}
    k0, mode, reason = dtc.compute_k0(680, header, profile)
    print(f"  k0={k0}  mode={mode}")
    assert mode == "standard_based"
    # 對照純函式
    expected = dtc.k0_from_instrument_constant(680, 150, 20.0, 2700)
    assert abs(k0 - expected) < 1e-9

    sep("[7] compute_k0: raw_parameters 未傳 T/P → 自動由表頭抽出（2026-08-12 起）")
    # 舊行為是拒絕執行並標 raw_parameters_missing_TP。那道防呆的存在理由是「不知道
    # 六個 Start temp / 兩個壓力欄位哪個才對」——VOCal 反編譯確認後這不再是猜測，
    # 所以改為自動取用。**但表頭真的缺欄位時仍必須明確失敗**，見 [7b]。
    profile = {"k0_calibration": {"mode": "raw_parameters"}}
    k0, mode, reason = dtc.compute_k0(680, header, profile)
    print(f"  k0={k0}  mode={mode}  reason={reason}")
    assert mode == "raw_parameters"
    assert k0 is not None
    tp, _ = dtc.extract_raw_tp(header)
    expected = dtc.k0_from_raw_params(680, 150, 5.3, tp["T_C"], tp["P_mbar"], 2700)
    assert abs(k0 - expected) < 1e-9, "自動抽出的 T/P 必須與 extract_raw_tp 一致"
    assert 1.8 < k0 < 2.5, "RIP 的 K0 應在反應離子的 2.0-2.3 量級"

    sep("[7b] compute_k0: 表頭真的缺 T/P → 仍明確失敗，不猜")
    stripped = {k: v for k, v in header.items()
                if k not in ("Start temp 1", "Start ambient pressure",
                             "EPC ambient pressure", "Start pressure EPC IMS",
                             "EPC IMS pressure", "EPC1 pressure")}
    k0b, modeb, reasonb = dtc.compute_k0(680, stripped, profile)
    print(f"  k0={k0b}  mode={modeb}")
    assert k0b is None and modeb == "raw_parameters_missing_TP"

    sep("[8] compute_k0: raw_parameters 呼叫者顯式指定 T=45, P=100393/100=1003.93")
    profile = {"k0_calibration": {"mode": "raw_parameters"}}
    raw_TP = {"T_C": 45.0, "P_mbar": 1003.93}   # 呼叫者的責任
    k0, mode, reason = dtc.compute_k0(680, header, profile, raw_TP=raw_TP)
    print(f"  k0={k0}  mode={mode}")
    assert mode == "raw_parameters"
    expected = dtc.k0_from_raw_params(680, 150, 5.3, 45.0, 1003.93, 2700)
    assert abs(k0 - expected) < 1e-9

    # ---- attach_k0 ----
    sep("[9] attach_k0: 對假峰清單就地寫 k0_value / k0_mode / k0_reason")
    peaks = [{"peak_id": i, "dt_index": v} for i, v in enumerate([680, 1000, 1500], start=1)]
    profile = {"k0_calibration": {"mode": "standard_based", "instrument_constant": 20.0}}
    dtc.attach_k0(peaks, header, profile)
    for p in peaks:
        print(f"  peak_id={p['peak_id']}  k0_value={p['k0_value']:.4f}  k0_mode={p['k0_mode']}")
        assert p["k0_mode"] == "standard_based"
        assert isinstance(p["k0_value"], float)
        assert "k0_reason" not in p

    sep("[10] attach_k0: unavailable 模式 → k0_value=None + k0_reason 有值")
    peaks2 = [{"peak_id": 1, "dt_index": 680}]
    dtc.attach_k0(peaks2, header, dtc.default_profile_unavailable())
    print(f"  {peaks2[0]}")
    assert peaks2[0]["k0_value"] is None
    assert peaks2[0]["k0_mode"] == "unavailable"
    assert "k0_reason" in peaks2[0]

    # ---- find_profile_path ----
    sep("[11] find_profile_path: 檔名策略 machine + column_name substring")
    with tempfile.TemporaryDirectory() as tmpdir:
        # 布置幾個假 profile 檔
        for fname in [
            "FlavourSpec_5H4-00123_FS-SE-54-CB-1_COFFEE-40RAW.json",
            "OtherMachine_XX-YY_Method.json",
        ]:
            with open(os.path.join(tmpdir, fname), "w") as f:
                f.write("{}")
        hit = dtc.find_profile_path(tmpdir, machine="FlavourSpec", column_name="FS-SE-54")
        print(f"  hit = {os.path.basename(hit) if hit else None}")
        assert hit and "FlavourSpec" in hit

        miss = dtc.find_profile_path(tmpdir, machine="NonExistent")
        assert miss is None

    print()
    print("[OK] all dt_convert.py smoke checks passed")


if __name__ == "__main__":
    test_dt_convert_smoke()

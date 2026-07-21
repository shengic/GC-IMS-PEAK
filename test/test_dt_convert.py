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
    # K0_raw = 0.8585 * 5.3² / (0.0045333 * 2700) = 0.8585 * 28.09 / 12.24 = 1.9711
    # returned = 1/K0_raw = 0.5073   ← 注意公式回傳 1/K0，符合 workflow 範例
    t = 680 / 150 / 1000
    ah = (273 / 318) * (1013 / 1013)
    Kraw = ah * 5.3**2 / (t * 2700)
    expected = 1 / Kraw
    print(f"  K0 = {k0}  expected = {expected}")
    assert abs(k0 - expected) < 1e-9

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

    sep("[7] compute_k0: raw_parameters 但未傳 T/P → 拒絕 + 明確標記")
    profile = {"k0_calibration": {"mode": "raw_parameters"}}
    k0, mode, reason = dtc.compute_k0(680, header, profile)
    print(f"  k0={k0}  mode={mode}  reason={reason}")
    assert k0 is None
    assert mode == "raw_parameters_missing_TP"
    assert "unresolved" in reason or "T" in reason

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

"""
test_areas2.py — 第二支應用（areas2.py）的測試。

雙用途：`pytest test/test_areas2.py` 或 `python test/test_areas2.py`。

**最重要的一條是隔離**：第二支應用不得寫入任何屬於第一支應用的檔案。那不是風格問題
——扣過基線的資料若寫回共用的 `.npz`，`main.py` 會把它當原始值載入而毫無跡象，正是
本專案一再防範的無聲污染。`test_baseline_never_written_back_to_npz` 直接擋這件事。
"""
import json
import os
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import areas2


# --------------------------------------------------------------------------- #
# 共識群聚
# --------------------------------------------------------------------------- #
def _peak(dr, rt, prom=100.0, active=True, ri=None):
    return {"drift_relative": dr, "retention_s": rt, "prominence": prom,
            "active": active, "ri": ri}


def test_peaks_within_tolerance_merge_into_one_area():
    """三個檔案在同一位置各有一顆峰 → 一個區域，且知道它出現在 3 個檔案。"""
    per_file = {
        "a.mea": [_peak(1.20, 300.0)],
        "b.mea": [_peak(1.21, 303.0)],      # 在 0.03 / 10 s 容差內
        "c.mea": [_peak(1.19, 297.0)],
    }
    areas, rep = areas2.build_consensus_areas(per_file, min_files=2)
    assert len(areas) == 1
    assert areas[0]["n_files_detected"] == 3
    assert sorted(areas[0]["detected_in"]) == ["a.mea", "b.mea", "c.mea"]
    assert rep["n_pooled_peaks"] == 3


def test_peaks_beyond_tolerance_stay_separate():
    """漂移或保留時間任一超出容差就是不同區域——不可以被併成一個。"""
    per_file = {
        "a.mea": [_peak(1.20, 300.0), _peak(1.60, 300.0)],   # 漂移差 0.40
        "b.mea": [_peak(1.20, 301.0), _peak(1.60, 301.0)],
        "c.mea": [_peak(1.20, 900.0)],                       # 保留時間差 600 s
    }
    areas, _ = areas2.build_consensus_areas(per_file, min_files=1)
    centres = sorted((round(a["drift_center"], 2), round(a["rt_center_s"])) for a in areas)
    assert centres == [(1.20, 300), (1.20, 900), (1.60, 300)]


def test_min_files_drops_single_file_noise():
    """只在一個檔案出現的峰被共識過濾擋掉——這正是跨檔相對於逐檔多出來的資訊。"""
    per_file = {
        "a.mea": [_peak(1.20, 300.0), _peak(1.90, 800.0)],   # 後者只有 a 有
        "b.mea": [_peak(1.20, 302.0)],
        "c.mea": [_peak(1.20, 301.0)],
    }
    areas, rep = areas2.build_consensus_areas(per_file, min_files=2)
    assert len(areas) == 1
    assert rep["n_dropped_below_min_files"] == 1
    assert round(areas[0]["drift_center"], 2) == 1.20


def test_box_is_sized_from_the_data_but_never_below_the_floor():
    """方框由群集實際範圍決定，並套用下限。

    下限取自 VOCal 自己畫的方框中位數；沒有下限的話，三個幾乎重疊的峰會產生一個
    細到量不到東西的方框。
    """
    tight = {"a.mea": [_peak(1.200, 300.0)], "b.mea": [_peak(1.201, 300.5)]}
    areas, _ = areas2.build_consensus_areas(tight, min_files=2)
    assert areas[0]["drift_half"] == pytest.approx(areas2.MIN_DRIFT_HALF)
    assert areas[0]["rt_half_s"] == pytest.approx(areas2.MIN_RT_HALF_S)

    wide = {"a.mea": [_peak(1.10, 280.0)], "b.mea": [_peak(1.16, 320.0)]}
    areas, _ = areas2.build_consensus_areas(wide, min_files=2, drift_tol=0.1, rt_tol_s=60)
    assert areas[0]["drift_half"] == pytest.approx(0.03)      # (1.16-1.10)/2
    assert areas[0]["rt_half_s"] == pytest.approx(20.0)       # (320-280)/2


def test_inactive_peaks_are_excluded_by_default():
    """被規則否決的峰不參與區域定義，與第一支應用畫圈的集合保持一致。"""
    per_file = {"a.mea": [_peak(1.2, 300.0, active=False)],
                "b.mea": [_peak(1.2, 301.0, active=False)]}
    areas, _ = areas2.build_consensus_areas(per_file, min_files=1)
    assert areas == []
    areas, _ = areas2.build_consensus_areas(per_file, min_files=1, active_only=False)
    assert len(areas) == 1


# --------------------------------------------------------------------------- #
# 方框量測
# --------------------------------------------------------------------------- #
def test_box_measurement_matches_a_hand_computed_sum():
    """volume = 方框內高於 floor 的總和，逐格核對。"""
    n_rt, n_dt = 10, 10
    surf = np.zeros((n_rt, n_dt), dtype=np.float64)
    surf[4:7, 4:7] = 10.0                       # 3×3 = 9 格，各 10
    drift_axis = np.arange(n_dt) / 5.0          # rip_index = 5 → drift_rel 0..1.8
    retention = np.arange(n_rt) * 10.0          # 0..90 s
    area = {"area_id": 1, "drift_center": 1.0, "drift_half": 0.25,
            "rt_center_s": 50.0, "rt_half_s": 15.0}
    sl = areas2._box_slice(area, drift_axis, retention, n_rt, n_dt)
    r0, r1, c0, c1 = sl
    box = surf[r0:r1, c0:c1]
    assert box.shape == (3, 3)                  # rows 4..6, cols 4..6
    assert float(np.clip(box - 0.0, 0, None).sum()) == 90.0


def test_box_entirely_outside_the_file_reports_none_not_zero():
    """量不到要回 None。

    寫成 0 會讓下游把「這個檔案根本沒量到這段保留時間」當成「量到零訊號」——兩件事
    在比較群組時的意義完全不同。
    """
    n_rt, n_dt = 10, 10
    drift_axis = np.arange(n_dt) / 5.0
    retention = np.arange(n_rt) * 10.0          # 只到 90 s
    area = {"area_id": 1, "drift_center": 1.0, "drift_half": 0.25,
            "rt_center_s": 5000.0, "rt_half_s": 10.0}     # 遠在範圍外
    assert areas2._box_slice(area, drift_axis, retention, n_rt, n_dt) is None


# --------------------------------------------------------------------------- #
# .gasprj 匯入
# --------------------------------------------------------------------------- #
def _write_gasprj(path, areas, entries=()):
    path.write_text(json.dumps({
        "ObjectType": "LAV Project",
        "Project": {"Data": {"Entries": list(entries)}},
        "MeasAreas": {"Path": "", "Data": list(areas)},
    }), encoding="utf-8")


def test_gasprj_areas_round_trip(tmp_path):
    """DriftCenter/Range 與 ElutionStart/End 要照實轉成本模組的座標。"""
    p = tmp_path / "p.gasprj"
    _write_gasprj(p, [{"Name": "2-Butanone", "DriftCenter": 1.2748,
                       "DriftRange": 0.0223, "DriftValType": "RipRel",
                       "ElutionStart": 2524, "ElutionEnd": 2595,
                       "ElutionValType": "SpecNum"}])
    step = 7 * 21 / 1000.0                      # (averages+1) x trigger = 0.147 s
    areas, rep = areas2.read_gasprj_areas(str(p), step)
    assert rep["n_areas"] == 1 and rep["n_skipped"] == 0
    a = areas[0]
    assert a["name"] == "2-Butanone"
    assert a["drift_center"] == pytest.approx(1.2748)
    assert a["drift_half"] == pytest.approx(0.0223)
    assert a["rt_center_s"] == pytest.approx((2524 + 2595) / 2 * step, abs=1e-3)
    assert a["rt_half_s"] == pytest.approx((2595 - 2524) / 2 * step, abs=1e-3)


def test_gasprj_unknown_coordinate_types_are_skipped_not_guessed(tmp_path):
    """非 RipRel/SpecNum 的座標一律跳過並記錄，不臆測換算。

    猜錯會讓整組方框在軸上平移而毫無跡象——與 `calibration.read_gasprj_ri_table()`
    對 `ColNormisLog=false` 的處理同一個原則。
    """
    p = tmp_path / "p.gasprj"
    _write_gasprj(p, [
        {"Name": "ok", "DriftCenter": 1.2, "DriftRange": 0.02,
         "DriftValType": "RipRel", "ElutionStart": 100, "ElutionEnd": 200,
         "ElutionValType": "SpecNum"},
        {"Name": "odd", "DriftCenter": 1.2, "DriftRange": 0.02,
         "DriftValType": "Milliseconds", "ElutionStart": 100, "ElutionEnd": 200,
         "ElutionValType": "Seconds"},
    ])
    areas, rep = areas2.read_gasprj_areas(str(p), 0.147)
    assert rep["n_areas"] == 1 and rep["n_skipped"] == 1
    assert rep["skipped"][0]["name"] == "odd"


def test_gasprj_entries_are_read_by_basename(tmp_path):
    """`Path` 是產生該專案那台機器的絕對路徑，本機不存在——只能用 basename 對應。"""
    p = tmp_path / "p.gasprj"
    _write_gasprj(p, [], entries=[
        {"Path": "D:\\GAS-TEST\\x\\260623_144213_A_1_1.mea", "Class": "A 1-1"},
        {"Path": "D:\\GAS-TEST\\x\\260624_140019_B_1_1.mea", "Class": "B 1-1"},
    ])
    ents = areas2.read_gasprj_entries(str(p))
    assert [e["basename"] for e in ents] == ["260623_144213_A_1_1.mea",
                                             "260624_140019_B_1_1.mea"]
    assert [e["class"] for e in ents] == ["A 1-1", "B 1-1"]


def test_rt_step_uses_the_averages_plus_one_formula():
    """`(averages + 1) x trigger`。少了 +1 整條 RT 軸短 16.7% 且不會有錯誤訊息。"""
    h = {"Chunk averages": "6", "Chunk trigger repetition": "21 [ms]"}
    assert areas2.header_rt_step_s(h) == pytest.approx(0.147)
    h2 = {"Chunk averages": "6", "Chunk trigger repetition": "30 [ms]"}
    assert areas2.header_rt_step_s(h2) == pytest.approx(0.21)


# --------------------------------------------------------------------------- #
# 隔離：不可寫入第一支應用的任何檔案
# --------------------------------------------------------------------------- #
def test_baseline_never_written_back_to_npz(tmp_path, monkeypatch):
    """扣過基線的強度面**絕不可**寫回共用的 `.npz`。

    這是兩支應用共用 `results/` 最危險的一條：若寫回去，`main.py` 之後會把扣過基線的
    資料當成原始值載入，圖與峰都變了卻沒有任何跡象。本測試把 `export_npz` 換成會爆炸的
    版本，確保量測路徑一次都沒呼叫它。
    """
    import readGAS
    called = []
    monkeypatch.setattr(readGAS, "export_npz",
                        lambda *a, **k: called.append(a) or (_ for _ in ()).throw(
                            AssertionError("areas2 不應在已有 .npz 時再寫入")))

    npz = tmp_path / "fake.npz"
    n_rt, n_dt = 60, 40
    inten = np.zeros((n_rt, n_dt), dtype=np.int16)
    inten[0, 20] = 5000                     # RIP：第 0 列的最大值
    inten[30, 25] = 900
    np.savez_compressed(npz, intensity=inten,
                        drift_ms=np.arange(n_dt) * 0.00667,
                        retention_s=np.arange(n_rt) * 0.147,
                        rt_axis_version=np.int32(2), mea_source=np.array(""))
    monkeypatch.setattr(areas2, "_npz_path", lambda p: str(npz))
    monkeypatch.setattr(areas2.rip_mod, "find_rip", lambda a, start=200: (20, 5000))

    area = {"area_id": 1, "drift_center": 25 / 20, "drift_half": 0.3,
            "rt_center_s": 30 * 0.147, "rt_half_s": 2.0}
    vals, meta = areas2.measure_areas_in_file(str(npz), [area], use_baseline=False,
                                              verbose=False)
    assert called == []
    assert vals[1]["volume"] is not None
    # .npz 內容原封不動
    with np.load(npz) as z:
        assert int(z["intensity"].max()) == 5000


def test_output_paths_all_carry_the_2_suffix(tmp_path):
    """產物一律帶 `2`，不可能與第一支應用的檔名相撞。"""
    result = {"folder": str(tmp_path / "MyBatch"), "files": [], "areas": [],
              "matrix": {}, "n_areas": 0, "n_files": 0,
              "provenance": {"ri_mode": "x", "baseline_applied": False}}
    js, csv = areas2.result_paths(result)
    assert os.path.basename(js) == "MyBatch_areas2.json"
    assert os.path.basename(csv) == "MyBatch_area_matrix2.csv"
    assert "_peaks.json" not in js and "_maxima.npz" not in csv


def test_peaks2_cache_name_does_not_collide_with_app_one():
    """逐檔快取叫 `_peaks2.json`，不是第一支應用的 `_peaks.json`。"""
    p = areas2._peaks2_path("/tmp/260623_144213_A_1_1.mea")
    assert os.path.basename(p) == "260623_144213_A_1_1_peaks2.json"


# --------------------------------------------------------------------------- #
# 矩陣輸出
# --------------------------------------------------------------------------- #
def _fake_result(tmp_path):
    return {
        "app": "areas2", "folder": str(tmp_path / "B"),
        "files": ["a.mea", "b.mea"], "classes": {"a.mea": "A 1", "b.mea": "B 1"},
        "n_areas": 2, "n_files": 2,
        "areas": [
            {"area_id": 1, "name": "2-Butanone", "cas": "C78933",
             "drift_center": 1.2748, "rt_center_s": 378.0, "ri_center": 916.8,
             "n_files_detected": 2},
            {"area_id": 2, "name": "area 2", "cas": None,
             "drift_center": 1.1039, "rt_center_s": 300.0, "ri_center": None,
             "n_files_detected": 1},
        ],
        "matrix": {
            "a.mea": {1: {"volume": 1234.5, "max": 90.0, "mean": 5.0},
                      2: {"volume": 10.0, "max": 3.0, "mean": 1.0}},
            "b.mea": {1: {"volume": 2000.0, "max": 120.0, "mean": 7.0},
                      2: {"volume": None, "max": None, "mean": None}},
        },
        "provenance": {"ri_mode": "batch_own_std", "baseline_applied": True},
    }


def test_csv_has_one_row_per_area_and_one_column_per_file(tmp_path):
    r = _fake_result(tmp_path)
    out = tmp_path / "m.csv"
    areas2.write_matrix_csv(r, str(out), metric="volume")
    lines = out.read_text(encoding="utf-8-sig").strip().split("\n")
    assert lines[0].startswith("# metric=volume")
    assert lines[1].startswith("class,")
    hdr = lines[2].split(",")
    assert hdr[:7] == ["area_id", "name", "cas", "drift_relative", "rt_s", "ri",
                       "n_files_detected"]
    assert hdr[7:] == ["a.mea", "b.mea"]
    assert len(lines) == 3 + 2                       # 3 表頭列 + 2 個區域


def test_unmeasured_cell_is_blank_not_zero(tmp_path):
    """量不到的格子寫空字串。0 會被當成真實的低訊號。"""
    r = _fake_result(tmp_path)
    out = tmp_path / "m.csv"
    areas2.write_matrix_csv(r, str(out), metric="volume")
    rows = out.read_text(encoding="utf-8-sig").strip().split("\n")
    last = rows[-1].split(",")
    assert last[-2] == "10"          # a.mea 有值
    assert last[-1] == ""            # b.mea 量不到 → 空，不是 0


def test_matrix_as_rows_matches_the_csv_shape(tmp_path):
    r = _fake_result(tmp_path)
    headers, rows = areas2.matrix_as_rows(r, metric="max")
    # 欄位標題用短檔名（`260625_113647_E_1_1.mea` -> `E_1_1`）——18 欄擠在一起時
    # 完整檔名根本看不完，而前面的日期時間對辨識樣品沒有幫助。
    assert headers[-2:] == ["a", "b"]
    assert len(rows) == 2
    assert rows[0][-2:] == ["90", "120"]
    assert rows[1][-1] == ""


def test_unknown_metric_is_refused(tmp_path):
    with pytest.raises(ValueError):
        areas2.write_matrix_csv(_fake_result(tmp_path), str(tmp_path / "x.csv"),
                                metric="nonsense")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# --------------------------------------------------------------------------- #
# 匯入的區域要補 RI 與偵測連結；VOCal 的名字不可被覆蓋
# --------------------------------------------------------------------------- #
def test_imported_areas_get_an_ri_centre():
    """由 .gasprj 匯入的區域沒有 RI，要用校正表補上。

    不補的後果不只是報表少一欄：`match_all()` 看到 ri=None 會退到保留時間比對，
    命名品質顯著變差（實測 57 個區域只命名到 10 個）。
    """
    cal = areas2.calibration.build_calibration(
        [100.0, 400.0, 900.0], "custom", ri_values=[600.0, 900.0, 1100.0])
    areas = [{"area_id": 1, "rt_center_s": 400.0, "drift_center": 1.2,
              "drift_half": 0.02, "rt_half_s": 8.0}]
    n = areas2.attach_ri_to_areas(areas, cal)
    assert n == 1
    assert areas[0]["ri_center"] == pytest.approx(900.0, abs=0.01)
    assert areas[0]["ri_extrapolated"] is False


def test_attach_ri_is_a_no_op_without_an_absolute_calibration():
    """沒有絕對 RI 時不可硬塞數字——維持沒有 RI，讓下游知道。"""
    areas = [{"area_id": 1, "rt_center_s": 400.0}]
    assert areas2.attach_ri_to_areas(areas, {"mode": "unavailable"}) == 0
    assert areas2.attach_ri_to_areas(areas, None) == 0
    assert "ri_center" not in areas[0]


def test_detection_linkage_counts_files_with_a_peak_inside_the_box():
    """`n_files_detected` 要反映真的有峰的檔案數，不是一律 0。"""
    areas = [{"area_id": 1, "drift_center": 1.20, "drift_half": 0.03,
              "rt_center_s": 300.0, "rt_half_s": 10.0}]
    per_file = {
        "a.mea": [_peak(1.21, 302.0)],          # 在框內
        "b.mea": [_peak(1.20, 299.0)],          # 在框內
        "c.mea": [_peak(1.90, 800.0)],          # 在框外
    }
    areas2.attach_detection_to_areas(areas, per_file)
    assert areas[0]["n_files_detected"] == 2
    assert areas[0]["detected_in"] == ["a.mea", "b.mea"]


def test_gasprj_area_names_are_never_overwritten_by_our_match():
    """`preserve_names=True` 時 VOCal 的名字必須原封不動。

    那是操作者的判定，我們的比對只是候選。蓋掉它等於把別人的結論換成自己的猜測，
    而且會讓「拿 .gasprj 對照」這件事失去意義——兩邊都變成我們的答案就沒得比。
    實測曾把 VOCal 的 `1- butanol` 改寫成 `6-Methyl-5-hepten-2-on`。
    """
    class _FakeMatch:
        DEFAULT_RI_TOLERANCE = 5.0
        DEFAULT_DRIFTREL_TOLERANCE = 0.05

        @staticmethod
        def match_all(peak, ril, iml, **kw):
            return {"combined_matches": [{"Name": "SomethingElse", "CAS": "C999"}],
                    "gc_matches": [], "ims_matches": [], "gc_dimension": "ri"}

    import types
    orig_match, orig_lib, orig_ident = areas2.match_mod, areas2.library, areas2.identify
    try:
        areas2.match_mod = _FakeMatch
        areas2.library = types.SimpleNamespace(resolve_data_dir=lambda explicit=None: "x")
        areas2.identify = types.SimpleNamespace(
            read_mea_header=lambda p: {},
            load_libraries=lambda d, h: ([], [], {"ril_files": [], "ril_strategy": "t"}))

        areas = [{"area_id": 1, "name": "1- butanol", "drift_center": 1.18,
                  "rt_center_s": 725.0, "ri_center": 1144.9}]
        areas2.name_areas(areas, "x.mea", preserve_names=True, verbose=False)
        assert areas[0]["name"] == "1- butanol"          # VOCal 的名字留著
        assert areas[0]["matched_name"] == "SomethingElse"  # 我們的候選另存
        assert areas[0]["matched_cas"] == "C999"

        areas = [{"area_id": 1, "name": "area 1", "drift_center": 1.18,
                  "rt_center_s": 725.0, "ri_center": 1144.9}]
        areas2.name_areas(areas, "x.mea", preserve_names=False, verbose=False)
        assert areas[0]["name"] == "SomethingElse"       # 共識區域則採用比對結果
    finally:
        areas2.match_mod, areas2.library, areas2.identify = orig_match, orig_lib, orig_ident


# --------------------------------------------------------------------------- #
# 背景執行緒的錯誤回報（實際使用回報：畫面全空、沒有任何訊息）
# --------------------------------------------------------------------------- #
def test_no_samples_raises_a_catchable_exception(tmp_path):
    """空資料夾必須丟 **Exception**，不是 BaseException。

    這是真實踩到的坑：原本是 `raise SystemExit`，而 SystemExit 繼承 BaseException，
    背景執行緒的 `except Exception` 接不到——執行緒無聲死掉、佇列永遠空著、UI 一直
    等下去，畫面全空且**沒有任何錯誤訊息**。使用者回報的「矩陣視窗一片空白、按什麼
    都沒反應」就是這個。
    """
    assert issubclass(areas2.NoSamplesFound, Exception)
    assert not issubclass(areas2.NoSamplesFound, SystemExit)
    with pytest.raises(areas2.NoSamplesFound):
        areas2.build_matrix(str(tmp_path), verbose=False)
    # 背景執行緒常見的攔截寫法必須接得住
    try:
        areas2.build_matrix(str(tmp_path), verbose=False)
    except Exception as e:
        assert isinstance(e, areas2.NoSamplesFound)
    else:
        pytest.fail("should have raised")


def test_no_samples_message_points_at_the_subfolders_that_do_have_mea(tmp_path):
    """選到只有子資料夾的上層目錄（例如 `GAS/`）時，要直接指出該去哪裡。

    檔案選擇器預設就開在 `GAS/`，而 `GAS/` 底下沒有 .mea——這是最容易踩到的一步，
    訊息必須把出路講出來，不能只說「找不到」。
    """
    (tmp_path / "batch_A").mkdir()
    (tmp_path / "batch_A" / "x.mea").write_bytes(b"Sample = \"S\"\r\n")
    (tmp_path / "empty_dir").mkdir()
    with pytest.raises(areas2.NoSamplesFound) as ei:
        areas2.build_matrix(str(tmp_path), verbose=False)
    msg = str(ei.value)
    assert "batch_A" in msg
    assert "empty_dir" not in msg          # 沒有 .mea 的子資料夾不該被推薦


def test_cancellation_is_a_catchable_exception_and_stops_the_batch(tmp_path, monkeypatch):
    """Stop 要能真的停下來，而且是可攔截的 Exception。"""
    assert issubclass(areas2.BatchCancelled, Exception)
    (tmp_path / "a.mea").write_bytes(b"Sample = \"S\"\r\nChunks count = 10\r\n")
    (tmp_path / "b.mea").write_bytes(b"Sample = \"S\"\r\nChunks count = 10\r\n")
    monkeypatch.setattr(areas2.calibration, "resolve_calibrations_cached",
                        lambda *a, **k: {"ri": (None, "unavailable", {}),
                                         "k0": (None, "unavailable", {})})
    seen = []

    def fake_detect(path, *a, **k):
        seen.append(path)
        return [], {"floor": 0.0}, {}
    monkeypatch.setattr(areas2, "detect_one", fake_detect)

    with pytest.raises(areas2.BatchCancelled):
        areas2.build_matrix(str(tmp_path), should_stop=lambda: True, verbose=False)
    assert seen == []                       # 第一個檔案之前就停住了


# --------------------------------------------------------------------------- #
# 快速模式：跳過找峰（找峰佔整批時間 75%）
# --------------------------------------------------------------------------- #
def test_skip_detect_requires_gasprj_areas(tmp_path):
    """共識模式不能跳找峰——那時候區域正是從偵測到的峰長出來的。

    讓它靜靜地跑出一個空矩陣會比報錯糟得多。
    """
    (tmp_path / "a.mea").write_bytes(b'Sample = "S"\r\nChunks count = 10\r\n')
    with pytest.raises(ValueError, match="from_gasprj"):
        areas2.build_matrix(str(tmp_path), skip_detect=True, verbose=False)


def test_skip_detect_reports_unknown_detection_not_zero(tmp_path, monkeypatch):
    """跳過找峰時 `n_files_detected` 要是 None，不是 0。

    0 的意思是「每個樣品在這個區域都沒偵測到峰」，那是一項強烈的陳述；
    None 的意思是「沒去看」。兩者混為一談會讓人對資料下錯結論。
    """
    p = tmp_path / "p.gasprj"
    _write_gasprj(p, [{"Name": "x", "DriftCenter": 1.2, "DriftRange": 0.02,
                       "DriftValType": "RipRel", "ElutionStart": 100,
                       "ElutionEnd": 200, "ElutionValType": "SpecNum"}],
                  entries=[{"Path": "d:/x/a.mea", "Class": "A"}])
    (tmp_path / "a.mea").write_bytes(
        b'Sample = "S"\r\nChunks count = 10\r\nChunk averages = 6\r\n'
        b'Chunk trigger repetition = 21\r\n')
    monkeypatch.setattr(areas2.calibration, "resolve_calibrations_cached",
                        lambda *a, **k: {"ri": (None, "unavailable", {}),
                                         "k0": (None, "unavailable", {})})
    monkeypatch.setattr(areas2, "name_areas", lambda *a, **k: {"named": 0})
    called = []
    monkeypatch.setattr(areas2, "detect_one",
                        lambda *a, **k: called.append(1) or ([], {}, {}))
    monkeypatch.setattr(areas2, "measure_areas_in_file",
                        lambda *a, **k: ({1: {"volume": 5.0, "max": 1.0,
                                              "mean": 1.0, "n_px": 4}}, {}))
    r = areas2.build_matrix(str(tmp_path), from_gasprj=str(p), skip_detect=True,
                            verbose=False)
    assert called == []                              # 真的沒跑找峰
    assert r["areas"][0]["n_files_detected"] is None
    assert r["areas"][0]["detected_in"] is None
    assert r["provenance"]["skip_detect"] is True
    # 矩陣照樣是滿的——那才是這支應用的重點
    assert r["matrix"]["a.mea"][1]["volume"] == 5.0

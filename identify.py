"""
identify.py  —  GC-IMS Identify Workflow 第六階段：整合輸出
Version: 3.0 — by Albert Sheng

依 GC-IMS_Identify_Workflow.md §第六階段：
  輸入：_peaks.json（來自 peaks.py，已含 drift_relative 由 rip 整合）
       + 原始 .mea 檔（用來讀表頭 L/U/sample_rate；只讀前 32KB）
       + 選定的 .ril/.iml 檔（library.select_*）
       + calibration_profile.json（可選，缺 → 走 K0 unavailable 模式）
       + rules_config.json（可選，缺 → 用 default_config）
       + 批次資料夾內的 STD.mea（可選；第四階段 RT→RI，缺 → RI unavailable）

  管線：peaks_json → header → k0 attach（§2）→ ri attach（§4）→ rules filter（§7）
       → library select（§3）→ per-peak match_all（§5）→ 輸出 _peaks_identified.json

  輸出：_peaks_identified.json，每顆峰附三分支候選（gc/ims/combined），每筆
       候選帶 source_file provenance。彙總資訊放在 top-level 欄位以供 UI 顯示：
       - k0_mode_summary
       - library_summary（實際用了哪幾份 .ril/.iml + 哪個 gc dimension）
       - rules_summary（多少峰通過規則、rip_missing_warning 等）

Usage:
    python identify.py <peaks_json>
       --mea <path>              原始 .mea（預設由 peaks_json.source 推導）
       --profile <path>          calibration_profile.json（預設 unavailable）
       --library-dir <path>      覆寫 library.resolve_data_dir()
       --rules-config <path>     rules_config.json（預設用 rules.default_config()）
       --raw-tp T_C,P_mbar       raw_parameters 模式必要
       --ri-tol / --rt-tol / --k0-tol  容許窗寬度覆寫

依賴：本專案的 rip / library / rules / dt_convert / match / readGAS 六個模組。
"""

import argparse
import datetime
import json
import os
import sys

import readGAS
import library
import rules
import dt_convert
import calibration
import match


# --------------------------------------------------------------------------- #
# I/O helpers
# --------------------------------------------------------------------------- #
def load_peaks_json(path):
    """讀 _peaks.json，回傳完整 doc（含 stats/peaks 等）。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_mea_header(path):
    """只讀 .mea 表頭區（前 32KB），避免載入整份 ~120MB 矩陣。

    利用 readGAS.parse_header 的既有實作，該函式本來就只掃前 32KB。
    """
    with open(path, "rb") as f:
        raw = f.read(32768)
    return readGAS.parse_header(raw)


def parse_raw_tp_arg(arg):
    """解析 --raw-tp 'T_C,P_mbar' 字串，回傳 {'T_C':..., 'P_mbar':...} 或 None。"""
    if not arg:
        return None
    try:
        t_str, p_str = arg.split(",")
        return {"T_C": float(t_str), "P_mbar": float(p_str)}
    except (ValueError, AttributeError):
        raise SystemExit(f"--raw-tp 格式應為 'T_C,P_mbar'，收到 {arg!r}")


# --------------------------------------------------------------------------- #
# 選庫檔
# --------------------------------------------------------------------------- #
def select_library_files(data_dir, header):
    """
    依 .mea 表頭的 GC Column / Drift Gas 從 data_dir 挑 .ril 與 .iml 檔。

    回傳
    ----
    (ril_paths, iml_paths, strategy_dict)
        strategy_dict 含各分支的 strategy 標籤（"column_name"/"polarity_fallback"/
        "none"），供輸出 provenance。
    """
    gc_col = header.get("GC Column", "")
    drift_gas = header.get("Drift Gas", "")
    parsed = library.parse_gc_column_header(gc_col) if gc_col else {
        "column_name": None, "polarity": None,
    }

    ril_paths, ril_strategy = library.select_ril_paths(
        data_dir, column_name=parsed["column_name"], polarity=parsed["polarity"],
    )
    iml_paths, iml_strategy = library.select_iml_paths(
        data_dir, column_name=parsed["column_name"], polarity=parsed["polarity"],
    )
    return ril_paths, iml_paths, {
        "gc_column_header": gc_col,
        "drift_gas_header": drift_gas,
        "parsed_column_name": parsed["column_name"],
        "parsed_polarity": parsed["polarity"],
        "ril_strategy": ril_strategy,
        "iml_strategy": iml_strategy,
    }


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def identify(peaks_doc, mea_path,
             profile=None, rules_config=None, raw_tp=None,
             library_dir=None,
             ri_calibration=None, resolve_ri=True, ri_series_key=None,
             ri_registry_path="ri_calibration_registry.json", ri_max_days_gap=None,
             ri_tolerance=match.DEFAULT_RI_TOLERANCE,
             rt_tolerance=match.DEFAULT_RT_TOLERANCE,
             k0_tolerance=match.DEFAULT_K0_TOLERANCE):
    """
    對 peaks_doc（load_peaks_json 產物）跑整套第一~五階段管線。

    不動 peaks_doc（做淺拷貝再修改）。回傳新的 identified_doc。

    參數
    ----
    peaks_doc : dict     from load_peaks_json()
    mea_path : str       原始 .mea 檔路徑（用來讀表頭）
    profile : dict|None  calibration_profile.json 內容；None → unavailable
    rules_config : list  rules_config.json 內容；None → rules.default_config()
    raw_tp : dict|None   {'T_C':..., 'P_mbar':...}，raw_parameters 模式必要
    library_dir : str|None  覆寫 library.resolve_data_dir()
    ri_calibration : dict|None  預先解好的 RI 校正表（calibration.resolve_ri_calibration
                     或 build_from_std_peaks 產物）；None 且 resolve_ri=True 時，
                     自動從 .mea 所在資料夾解析（三層：batch_own_std/registry/unavailable）
    resolve_ri : bool    ri_calibration 為 None 時，是否自動從資料夾解析（False → RI unavailable）
    ri_series_key : str|None  自動解析時的參照系列（None → single_point_relative 相對模式）
    ri_registry_path : str    registry 借用來源
    ri_max_days_gap : int|None  借用校正的天數上限，超過視同不可用
    """
    peaks = [dict(p) for p in peaks_doc.get("peaks", [])]
    stats = peaks_doc.get("stats", {})

    # ---- 讀表頭 ----
    header = read_mea_header(mea_path)

    # ---- 第二階段：K0 換算 ----
    if profile is None:
        profile = dt_convert.default_profile_unavailable(
            "no calibration profile provided to identify.py"
        )
    dt_convert.attach_k0(peaks, header, profile, raw_TP=raw_tp)

    # ---- 第四階段：RT→RI 校正 ----
    ri_detail = {}
    if ri_calibration is None and resolve_ri:
        folder = os.path.dirname(os.path.abspath(mea_path))
        dims = calibration.extract_registry_dims(header)
        ri_calibration, ri_mode, ri_detail = calibration.resolve_ri_calibration(
            folder, dims=dims, series_key=ri_series_key,
            registry_path=ri_registry_path, max_days_gap=ri_max_days_gap,
        )
    else:
        ri_mode = (ri_calibration.get("ri_mode", "provided")
                   if isinstance(ri_calibration, dict) else "unavailable")

    if ri_calibration is None:
        for p in peaks:
            p["ri"] = None
            p["ri_mode"] = "unavailable"
            p["ri_source"] = "unavailable"
    else:
        calibration.attach_ri(peaks, ri_calibration)
        calibration.stamp_ri_provenance(peaks, ri_calibration, ri_mode)

    # ---- 第七階段：規則篩選 ----
    if rules_config is None:
        rules_config = rules.default_config()
    rules_context = {
        "floor": stats.get("floor"),
        "rip_index": stats.get("rip_index"),
    }
    filtered_peaks, rules_report = rules.apply_rules(
        peaks, rules_config, context=rules_context,
    )

    # ---- 第三階段：選庫檔 ----
    resolved_lib_dir = library.resolve_data_dir(explicit=library_dir)
    if resolved_lib_dir is None:
        raise SystemExit(
            "找不到 library 資料夾（library_data/ 空或不存在，"
            "且未指定 --library-dir）；先放入 .ril/.iml 或明確指定路徑"
        )
    ril_paths, iml_paths, select_info = select_library_files(resolved_lib_dir, header)
    ril_rows = library.load_ril_many(ril_paths)
    iml_rows = library.load_iml_many(iml_paths)

    # ---- 第五階段：逐峰比對 ----
    gc_dims = []
    for p in filtered_peaks:
        r = match.match_all(
            p, ril_rows, iml_rows,
            ri_tolerance=ri_tolerance,
            rt_tolerance=rt_tolerance,
            k0_tolerance=k0_tolerance,
        )
        p["matches"] = r
        gc_dims.append(r["gc_dimension"])

    # ---- 彙總 ----
    def _tally(key):
        out = {}
        for p in filtered_peaks:
            out[p.get(key, "unknown")] = out.get(p.get(key, "unknown"), 0) + 1
        return out

    k0_mode_summary = _tally("k0_mode")

    identified_doc = {
        "source_peaks_json": peaks_doc.get("source"),
        "source_mea": mea_path,
        "identified_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "n_peaks_in": len(peaks),
        "n_peaks_out": len(filtered_peaks),
        "k0_mode_summary": k0_mode_summary,
        "ri_mode_summary": _tally("ri_mode"),
        "ri_source_summary": _tally("ri_source"),
        "ri_calibration_summary": {
            "ri_mode": ri_mode,
            "known_ri_available": bool(ri_calibration.get("known_ri_available"))
                                  if isinstance(ri_calibration, dict) else False,
            "assumed_unverified": bool(ri_calibration.get("assumed_unverified"))
                                  if isinstance(ri_calibration, dict) else False,
            "series_used": ri_calibration.get("series_used")
                           if isinstance(ri_calibration, dict) else None,
            "n_anchors": ri_calibration.get("n_anchors")
                         if isinstance(ri_calibration, dict) else 0,
            "resolution": ri_detail,
        },
        "rules_summary": rules_report,
        "library_summary": {
            "resolved_data_dir": resolved_lib_dir,
            "ril_files": [os.path.basename(p) for p in ril_paths],
            "iml_files": [os.path.basename(p) for p in iml_paths],
            "n_ril_rows": len(ril_rows),
            "n_iml_rows": len(iml_rows),
            "selection": select_info,
            "gc_dimensions_used": {d: gc_dims.count(d) for d in set(gc_dims)},
        },
        "match_tolerances": {
            "ri": ri_tolerance, "rt": rt_tolerance, "k0": k0_tolerance,
        },
        "peaks": filtered_peaks,
    }
    return identified_doc


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="GC-IMS Identify pipeline（第一～五階段整合）")
    ap.add_argument("peaks_json", help="peaks.py 產出的 <name>_peaks.json 路徑")
    ap.add_argument("--mea", default=None,
                    help="原始 .mea 路徑（省略 → 從 peaks_json.source 推導）")
    ap.add_argument("--profile", default=None,
                    help="calibration_profile.json 路徑（省略 → unavailable 模式）")
    ap.add_argument("--library-dir", default=None,
                    help="覆寫 library.resolve_data_dir()")
    ap.add_argument("--rules-config", default=None,
                    help="rules_config.json 路徑（省略 → default_config）")
    ap.add_argument("--raw-tp", default=None,
                    help="raw_parameters 模式必要，格式 'T_C,P_mbar'，e.g. '45,1013'")
    ap.add_argument("--ri-series", default=None, choices=calibration.rs.list_series(),
                    help="第四階段參照系列（省略 → single_point_relative 相對模式）")
    ap.add_argument("--ri-registry", default="ri_calibration_registry.json",
                    help="RI 校正 registry（無 STD 時借用來源）")
    ap.add_argument("--ri-max-days-gap", type=int, default=None,
                    help="借用校正的天數上限，超過視同不可用")
    ap.add_argument("--no-ri", action="store_true",
                    help="停用第四階段 RT→RI（RI 直接 unavailable，走 RT 退路）")
    ap.add_argument("--ri-tol", type=float, default=match.DEFAULT_RI_TOLERANCE)
    ap.add_argument("--rt-tol", type=float, default=match.DEFAULT_RT_TOLERANCE)
    ap.add_argument("--k0-tol", type=float, default=match.DEFAULT_K0_TOLERANCE)
    ap.add_argument("--out", default=None,
                    help="輸出 JSON 路徑（省略 → results/<name>_peaks_identified.json）")
    args = ap.parse_args()

    peaks_doc = load_peaks_json(args.peaks_json)

    # 推導 .mea 路徑
    mea_path = args.mea or peaks_doc.get("source")
    if not mea_path or not os.path.exists(mea_path):
        raise SystemExit(f"找不到 .mea 檔：{mea_path!r}；請用 --mea 明確指定")

    # 載入 profile（若指定）
    profile = None
    if args.profile:
        profile = dt_convert.load_calibration_profile(args.profile)

    # 載入 rules_config（若指定）
    rules_config = None
    if args.rules_config:
        rules_config = rules.load_config(args.rules_config)

    raw_tp = parse_raw_tp_arg(args.raw_tp)

    identified = identify(
        peaks_doc, mea_path,
        profile=profile, rules_config=rules_config, raw_tp=raw_tp,
        library_dir=args.library_dir,
        resolve_ri=not args.no_ri, ri_series_key=args.ri_series,
        ri_registry_path=args.ri_registry, ri_max_days_gap=args.ri_max_days_gap,
        ri_tolerance=args.ri_tol,
        rt_tolerance=args.rt_tol,
        k0_tolerance=args.k0_tol,
    )

    # 決定輸出路徑
    if args.out:
        out_path = args.out
    else:
        base = os.path.splitext(os.path.basename(args.peaks_json))[0]
        if base.endswith("_peaks"):
            base = base[:-len("_peaks")]
        out_dir = os.path.dirname(args.peaks_json) or "."
        out_path = os.path.join(out_dir, f"{base}_peaks_identified.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(identified, f, ensure_ascii=False, indent=2)

    # 精簡摘要到 stdout
    print(f"完成：{out_path}")
    print(f"  峰數: {identified['n_peaks_in']} → {identified['n_peaks_out']} (規則篩選後)")
    print(f"  k0 模式: {identified['k0_mode_summary']}")
    ri_sum = identified["ri_calibration_summary"]
    print(f"  RI 來源: {ri_sum['ri_mode']} | 模式: {identified['ri_mode_summary']}"
          + (f" | ⚠ 假設未驗證 series={ri_sum['series_used']}" if ri_sum["assumed_unverified"] else ""))
    print(f"  library: {len(identified['library_summary']['ril_files'])} .ril, "
          f"{len(identified['library_summary']['iml_files'])} .iml"
          f" ({identified['library_summary']['n_ril_rows']} + "
          f"{identified['library_summary']['n_iml_rows']} rows)")
    if identified["rules_summary"].get("rip_missing_warning"):
        print("  ⚠ rules_summary.rip_missing_warning = True（R004 因缺 drift_relative 無法運作）")


if __name__ == "__main__":
    main()

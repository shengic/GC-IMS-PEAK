"""
identify.py  —  GC-IMS Identify Workflow 第六階段：整合輸出
Version: 3.3 — by Albert Sheng

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
import glob
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

    **兩個維度的選檔規則刻意不同**：
      - `.ril`（GC/RI 維度）**綁 GC 管柱**——RI 是管柱相依的量，拿別的固定相量出的
        RI 來比對沒有意義，故走 column_name 精確匹配、退路依極性。
      - `.iml`（IMS 漂移維度）**不綁 GC 管柱**——`DtMode=="RIPrel"` 的漂移值是相對
        RIP 的無因次比值，屬儀器層級的量，與樣品跑哪根管柱無關。用管柱名去篩 `.iml`
        會把大量可用的漂移候選誤擋掉，所以一律載入全部 `.iml`；單位混用的風險由
        `match.match_drift_rel()` / `match_k0()` 的 `DtMode` 過濾擋住，不靠檔名。

    這個規則以前只實作在 UI（main.py 自己 glob 全部 `.iml`），CLI 走的卻是
    `select_iml_paths()` 的管柱/極性篩選——同一批資料兩條路徑得到不同的 IMS 候選。
    現在統一在這裡，兩邊共用同一份決策。

    回傳
    ----
    (ril_paths, iml_paths, strategy_dict)
        strategy_dict 含各分支的 strategy 標籤（"column_name"/"polarity_fallback"/
        "none"/"all"），供輸出 provenance。
    """
    gc_col = header.get("GC Column", "")
    drift_gas = header.get("Drift Gas", "")
    parsed = library.parse_gc_column_header(gc_col) if gc_col else {
        "column_name": None, "polarity": None, "polarity_source": None,
    }

    ril_paths, ril_strategy = library.select_ril_paths(
        data_dir, column_name=parsed["column_name"], polarity=parsed["polarity"],
    )
    # IMS 漂移不綁管柱 → 全載（見上方 docstring）
    iml_paths = sorted(glob.glob(os.path.join(data_dir, "*.iml")))
    iml_strategy = "all (IMS drift is instrument-relative, not GC-column-specific)"

    return ril_paths, iml_paths, {
        "gc_column_header": gc_col,
        "drift_gas_header": drift_gas,
        "parsed_column_name": parsed["column_name"],
        "parsed_polarity": parsed["polarity"],
        # 極性是表頭明寫的還是由型號推的（見 library.parse_gc_column_header）。
        # 推來的極性決定了載哪些 .ril，也就決定了 RI 拿什麼尺標比——必須可追溯。
        "parsed_polarity_source": parsed.get("polarity_source"),
        "ril_strategy": ril_strategy,
        "iml_strategy": iml_strategy,
    }


def load_libraries(data_dir, header):
    """
    選檔 + 讀檔 + 載流氣體交叉核對，一次做完。**CLI 與 UI 共用這一份**，避免兩邊
    的比對候選集悄悄分歧。

    載流氣體核對（workflow §第三階段第 3 點）採保守語意：只排除「明確標記為別種
    氣體」的 row，未標記者保留——實測 library_data/ 有 39 筆 RIPrel row 因舊格式
    欄位偏移而沒有氣體標記，嚴格篩選會把它們靜默丟掉（見
    `library.filter_iml_rows_by_drift_gas` 的說明）。

    回傳
    ----
    (ril_rows, iml_rows, info)
        info 為 select_library_files() 的 strategy_dict 再加上載入/篩選後的計數。
    """
    ril_paths, iml_paths, info = select_library_files(data_dir, header)
    ril_rows = library.load_ril_many(ril_paths)
    iml_rows_all = library.load_iml_many(iml_paths)

    drift_gas = header.get("Drift Gas", "")
    iml_rows = library.filter_iml_rows_by_drift_gas(iml_rows_all, drift_gas)

    info = dict(info)
    info["ril_files"] = [os.path.basename(p) for p in ril_paths]
    info["iml_files"] = [os.path.basename(p) for p in iml_paths]
    info["n_iml_rows_before_gas_filter"] = len(iml_rows_all)
    info["n_iml_rows_after_gas_filter"] = len(iml_rows)
    info["drift_gas_filter"] = (
        f"excluded rows explicitly tagged as a gas other than {drift_gas!r}"
        if drift_gas else "not applied (no 'Drift Gas' in header)"
    )
    return ril_rows, iml_rows, info


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def identify(peaks_doc, mea_path,
             profile=None, rules_config=None, raw_tp=None,
             library_dir=None,
             ri_calibration=None, resolve_ri=True, ri_series_key=None,
             ri_registry_path="ri_calibration_registry.json", ri_max_days_gap=None,
             resolve_k0=True, k0_series_key="ketone",
             ri_tolerance=match.DEFAULT_RI_TOLERANCE,
             rt_tolerance=match.DEFAULT_RT_TOLERANCE,
             k0_tolerance=match.DEFAULT_K0_TOLERANCE,
             driftrel_tolerance=match.DEFAULT_DRIFTREL_TOLERANCE):
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

    # ---- 資料夾層級校正解析（RI 與 K0 一次解出）----
    # 兩者讀同一支 STD 的同一份 _peaks.json，分開解會掃兩次資料夾，且一旦各自挑到
    # 不同 STD 就會產生「同批次的 RI 與 K0 出處不一致」這種無徵兆的錯誤。
    ri_detail, k0_detail, k0_mode = {}, {}, None
    # 呼叫端自帶的校正一律優先，不被自動解析覆蓋
    ri_mode = ("provided" if isinstance(ri_calibration, dict) else "unavailable")
    need_ri = ri_calibration is None and resolve_ri
    need_k0 = profile is None and resolve_k0
    if need_ri or need_k0:
        folder = os.path.dirname(os.path.abspath(mea_path))
        dims = calibration.extract_registry_dims(header)
        resolved = calibration.resolve_calibrations_cached(
            folder, dims=dims, series_key=ri_series_key,
            registry_path=ri_registry_path, max_days_gap=ri_max_days_gap,
            k0_series_key=k0_series_key,
        )
        if need_ri:
            ri_calibration, ri_mode, ri_detail = resolved["ri"]
        if need_k0:
            profile, k0_mode, k0_detail = resolved["k0"]

    # ---- 第二階段：K0 換算 ----
    if profile is None:
        profile = dt_convert.default_profile_unavailable(
            "資料夾內沒有可用的 STD，且未以 --profile 指定校正檔"
            if resolve_k0 else "no calibration profile provided to identify.py"
        )
    dt_convert.attach_k0(peaks, header, profile, raw_TP=raw_tp)

    # ---- 第四階段：RT→RI 校正 ----
    if isinstance(ri_calibration, dict):
        ri_mode = ri_calibration.get("ri_mode", ri_mode)

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
    ril_rows, iml_rows, select_info = load_libraries(resolved_lib_dir, header)

    # ---- 第五階段：逐峰比對 ----
    gc_dims, ims_dims = [], []
    for p in filtered_peaks:
        r = match.match_all(
            p, ril_rows, iml_rows,
            ri_tolerance=ri_tolerance,
            rt_tolerance=rt_tolerance,
            k0_tolerance=k0_tolerance,
            driftrel_tolerance=driftrel_tolerance,
        )
        p["matches"] = r
        gc_dims.append(r["gc_dimension"])
        ims_dims.append(r["ims_dimension"])

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
            "ril_files": select_info["ril_files"],
            "iml_files": select_info["iml_files"],
            "n_ril_rows": len(ril_rows),
            "n_iml_rows": len(iml_rows),
            "selection": select_info,
            "gc_dimensions_used": {d: gc_dims.count(d) for d in set(gc_dims)},
            "ims_dimensions_used": {d: ims_dims.count(d) for d in set(ims_dims)},
        },
        "match_tolerances": {
            "ri": ri_tolerance, "rt": rt_tolerance, "k0": k0_tolerance,
            "drift_rel": driftrel_tolerance,
        },
        "peaks": filtered_peaks,
    }
    return identified_doc


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _use_utf8_stdout():
    """Windows 主控台預設 cp950，印不出 µ / 中文以外的字元就整支崩掉（實際發生過：
    print_summary 的 'µs' 讓 readGAS.py 在 cp950 下 UnicodeEncodeError）。

    同時修掉一個更隱蔽的問題：main.py 以 encoding="utf-8" 讀子行程的 stdout，子行程
    卻用 cp950 寫，狀態列的中文訊息因此一直是亂碼。兩邊統一成 utf-8。
    calibration.py 與 test/ 早就用這個慣用法，這裡補齊。
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass                      # 舊版 Python 或已被重導向 → 沿用原設定


def main():
    _use_utf8_stdout()
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
    ap.add_argument("--drift-tol", type=float, default=match.DEFAULT_DRIFTREL_TOLERANCE,
                    help="IMS 漂移（RIPrel）容許窗半寬；K0 休眠時實際用的就是這個")
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
        driftrel_tolerance=args.drift_tol,
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

"""預先產生 `.npz` 與找峰結果，讓介面點下去就有反應。

**兩個階段的代價差四倍，不要混為一談：**

| 產物 | 內容 | 每檔 | 誰需要它 |
|---|---|---|---|
| `<base>.npz` | 原始強度矩陣 + 兩條軸 + `rt_axis_version` + `mea_source` | ~13 s | 畫熱圖 |
| `<base>_peaks2.json` | **找峰結果** | ~55 s | 選峰、分組、彙整 |

`.npz` 裡**沒有峰**。只做 `.npz` 的話，介面點一個檔仍然要現場跑 55 秒的偵測。

**參數指紋**：找峰結果取決於 `rules_config` 與 `prom_frac` 等參數，而
`areas2.detect_one()` 的快取只比對 baseline 一項。所以這裡一律走
`logic.detect_cached()`，它會另外記一份指紋；預處理與互動用的參數不同時會自動重跑，
不會靜靜沿用錯的峰。

用法：

    python -m compound_consensus.preprocess GAS/Coffee-bean --peaks
    python -m compound_consensus.preprocess --all --peaks --jobs 4
    python -m compound_consensus.preprocess --all --peaks --dry-run

**唯讀 `GAS/`**：只讀 `.mea`，所有產物都寫在 `results/`（隔離規則 3）。
以函式呼叫既有模組，不跑任何 subprocess——那在打包成 exe 之後會壞掉。

Version: 1.0 — by Albert Sheng（第三支應用，2026-08-31）
"""
import argparse
import concurrent.futures as cf
import glob
import os
import sys
import time

import areas2
import rules as rules_mod

from . import logic as L

SEC_PER_READ = 13.0
SEC_PER_DETECT = 55.0

#: 預設並行數。**不是核心數**：每個工作行程都要載入一份 20413×3150 的矩陣
#: （int16 約 129 MB，運算過程中的 float 複本更大），開太多會換到硬碟上反而更慢。
#: 22 核的機器上 4 個行程實測仍是 CPU 飽和。要更快再用 --jobs 自己調。
DEFAULT_JOBS = 4


def _fmt(seconds):
    return "%.0f 秒" % seconds if seconds < 90 else "%.1f 分" % (seconds / 60.0)


def _work(task):
    """在工作行程裡跑。回傳 (檔名, 狀態字串)。

    模組層級函式，因為 Windows 的 spawn 需要 picklable 的目標。
    """
    mea, need_npz, need_peaks, rules_config = task
    name = os.path.basename(mea)
    try:
        if need_npz:
            areas2.ensure_npz(mea, verbose=False)
        if need_peaks:
            L.detect_cached(mea, rules_config, use_baseline=False, verbose=False)
        return name, "OK"
    except Exception as exc:
        # 一個檔壞掉不該讓整批停下來——但**一定要報出來**，不能靜靜跳過。
        return name, "失敗 %s: %s" % (type(exc).__name__, exc)


def plan(folders, with_peaks, rules_config, trust_existing=False, write=True,
         force=False):
    """列出還缺什麼。**先算清楚再開始**，不要讓使用者盲等。"""
    items = []
    for folder in folders:
        samples, excluded = L.select_samples(folder)
        for e in excluded:
            print("  跳過 %-38s (%s: %s)"
                  % (os.path.basename(e["file"])[:38], e["reason"], e["detail"]))
        for m in samples:
            # force：連既有的也重做。平常不需要——參數換過時指紋本來就會自動重跑，
            # force 是給「檔案壞了」「就是想重算一次」這種情況用的。
            need_npz = force or not os.path.exists(areas2._npz_path(m))
            need_pk = with_peaks and (force or not L.peaks_are_current(
                m, rules_config, use_baseline=False,
                trust_existing=trust_existing, write=write))
            if need_npz or need_pk:
                items.append((m, need_npz, need_pk, rules_config))
    est = sum(SEC_PER_READ * it[1] + SEC_PER_DETECT * it[2] for it in items)
    return items, est


def run(folders, with_peaks=False, dry_run=False, jobs=DEFAULT_JOBS,
        trust_existing=False, force=False):
    rules_config = rules_mod.load_config("rules_config.json")
    # dry run 絕不寫檔——採信既有快取會補寫指紋，那是副作用
    items, est = plan(folders, with_peaks, rules_config, trust_existing,
                      write=not dry_run, force=force)
    if not items:
        print("沒有要做的事——全部都是最新的。")
        return 0

    n_npz = sum(1 for it in items if it[1])
    n_pk = sum(1 for it in items if it[2])
    print("待處理 %d 檔：需讀檔 %d、需找峰 %d　序列預估 %s"
          % (len(items), n_npz, n_pk, _fmt(est)))
    if jobs > 1:
        print("  以 %d 個行程並行，樂觀估計約 %s" % (jobs, _fmt(est / jobs)))
    if dry_run:
        for mea, a, b, _ in items:
            print("   %-42s %s%s" % (os.path.basename(mea)[:42],
                                     "npz " if a else "", "peaks" if b else ""))
        return 0

    t0 = time.time()
    done = failed = 0
    if jobs <= 1:
        results = (_work(it) for it in items)
        for i, (name, status) in enumerate(results, 1):
            done += status == "OK"
            failed += status != "OK"
            el = time.time() - t0
            print("[%d/%d] %-42s %-10s 已用 %s，剩約 %s"
                  % (i, len(items), name[:42], status, _fmt(el),
                     _fmt(el / i * (len(items) - i))), flush=True)
    else:
        with cf.ProcessPoolExecutor(max_workers=jobs) as pool:
            futs = {pool.submit(_work, it): it for it in items}
            for i, fut in enumerate(cf.as_completed(futs), 1):
                name, status = fut.result()
                done += status == "OK"
                failed += status != "OK"
                el = time.time() - t0
                print("[%d/%d] %-42s %-10s 已用 %s，剩約 %s"
                      % (i, len(items), name[:42], status, _fmt(el),
                         _fmt(el / i * (len(items) - i))), flush=True)
    print("完成：成功 %d、失敗 %d，共 %s" % (done, failed, _fmt(time.time() - t0)))
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
# 圖形介面 —— 不帶參數執行就是這個
# --------------------------------------------------------------------------- #

def resolve_folders(path):
    """把使用者選的資料夾解析成「真的裝著 .mea 的那些資料夾」。

    **`GAS/` 本身一個 .mea 都沒有**——66 個檔全在它底下的 4 個子資料夾裡。選到它
    只會得到 0 個樣品，看起來像程式壞了。所以：自己有 .mea 就用自己，否則往下找
    有 .mea 的子資料夾。第二支應用踩過同一個坑（`status2.md`：按 Run batch 沒反應，
    原因是檔案選擇器預設開在 `GAS/`）。
    """
    if glob.glob(os.path.join(path, "*.mea")):
        return [path]
    found = []
    for root, _dirs, files in os.walk(path):
        if any(f.lower().endswith(".mea") for f in files):
            found.append(root)
    return sorted(found)


def _counts(folders, rules_config, trust_existing=True):
    """進度：(樣品數, 有 npz, 找峰是最新的, 排除清單, 逐資料夾明細)。

    `folders` 可以是單一路徑或多個路徑；單一路徑會先經過 `resolve_folders()`。
    """
    if isinstance(folders, str):
        folders = resolve_folders(folders)
    tot = npz = pk = 0
    excluded, per_folder = [], []
    for folder in folders:
        samples, ex = L.select_samples(folder)
        a = sum(1 for m in samples if os.path.exists(areas2._npz_path(m)))
        b = sum(1 for m in samples
                if L.peaks_are_current(m, rules_config, use_baseline=False,
                                       trust_existing=trust_existing, write=False))
        tot += len(samples); npz += a; pk += b
        excluded.extend(ex)
        per_folder.append((folder, len(samples), a, b))
    return tot, npz, pk, excluded, per_folder


def gui():
    """選一個資料夾，做 npz 與找峰，顯示做了幾個。就這樣。"""
    import queue as _queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, ttk

    rules_config = rules_mod.load_config("rules_config.json")
    root = tk.Tk()
    root.title("預處理 — 產生 .npz 與找峰結果")
    root.geometry("620x300")
    q = _queue.Queue()
    state = {"folder": None, "folders": [], "running": False}

    root.geometry("680x420")
    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)
    lbl_folder = ttk.Label(frm, text="尚未選資料夾", foreground="#555")
    lbl_folder.pack(anchor="w")
    lbl_counts = ttk.Label(frm, font=("Segoe UI", 11), text="")
    lbl_counts.pack(anchor="w", pady=8)
    lbl_note = ttk.Label(frm, foreground="#777", wraplength=640, justify="left", text="")
    lbl_note.pack(anchor="w")
    txt = tk.Text(frm, height=6, font=("Consolas", 9), state="disabled",
                  background="#f7f7f7", relief="flat")
    txt.pack(fill="x", pady=6)
    bar = ttk.Progressbar(frm, mode="determinate")
    bar.pack(fill="x", pady=10)
    lbl_now = ttk.Label(frm, foreground="#333", text="")
    lbl_now.pack(anchor="w")

    row = ttk.Frame(frm)
    row.pack(fill="x", pady=8)
    btn_pick = ttk.Button(row, text="選資料夾")
    btn_pick.pack(side="left")
    btn_go = ttk.Button(row, text="開始", state="disabled")
    btn_go.pack(side="left", padx=6)
    ttk.Label(row, text="並行").pack(side="left", padx=(12, 2))
    jobs_var = tk.StringVar(value=str(DEFAULT_JOBS))
    ttk.Combobox(row, textvariable=jobs_var, width=4, state="readonly",
                 values=["1", "2", "4", "6", "8"]).pack(side="left")
    force_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(row, text="重跑（連已經有的也重做）", variable=force_var,
                    command=lambda: refresh()).pack(side="left", padx=(14, 0))
    btn_quit = ttk.Button(row, text="結束")
    btn_quit.pack(side="right")

    def quit_app():
        """處理中就先問一聲。**已經做完的檔不會白費**——每個檔各自寫出自己的
        `.npz` / `_peaks2.json`，中斷只會丟掉正在處理中的那一個。"""
        if state["running"]:
            from tkinter import messagebox
            if not messagebox.askyesno(
                    "還在處理中",
                    "目前正在處理檔案。現在結束會中斷還沒做完的部分。\n\n"
                    "已經完成的檔都已經寫出來了，不會白費——"
                    "下次再跑會從沒做的地方繼續。\n\n確定要結束嗎？"):
                return
        root.destroy()

    btn_quit.config(command=quit_app)
    # 視窗右上角的 X 走同一條路，行為才一致
    root.protocol("WM_DELETE_WINDOW", quit_app)

    def refresh():
        f = state["folder"]
        if not f:
            return
        folders = resolve_folders(f)
        n, n_npz, n_pk, excluded, per = _counts(folders, rules_config)
        state["folders"] = folders
        if force_var.get():          # 重跑 = 全部都算待處理
            n_npz = n_pk = 0
        head = ("%d 個樣品　|　已有 .npz：%d / %d　|　找峰已完成：%d / %d"
                % (n, n_npz, n, n_pk, n))
        if len(per) > 1:
            head += "　（%d 個子資料夾）" % len(per)
        lbl_counts.config(text=head)
        txt.config(state="normal")
        txt.delete("1.0", "end")
        for folder, a, b, c in per:
            txt.insert("end", "%-30s 樣品 %2d　npz %2d/%-2d　找峰 %2d/%-2d\n"
                       % (os.path.basename(os.path.normpath(folder))[:30],
                          a, b, a, c, a))
        txt.config(state="disabled")
        todo_npz, todo_pk = n - n_npz, n - n_pk
        est = todo_npz * SEC_PER_READ + todo_pk * SEC_PER_DETECT
        skipped = ("　排除 " + "、".join(
            "%s(%s)" % (os.path.basename(e["file"]), e["reason"]) for e in excluded)
        ) if excluded else ""
        lbl_note.config(
            text=("待處理：讀檔 %d、找峰 %d，序列約 %s%s"
                  % (todo_npz, todo_pk, _fmt(est), skipped)) if (todo_npz or todo_pk)
            else "全部都是最新的，不用做。勾「重跑」可以強制重算。" + skipped)
        btn_go.config(state="normal" if (todo_npz or todo_pk) and not state["running"]
                      else "disabled")

    def pick():
        start = os.path.join(os.getcwd(), "GAS")
        d = filedialog.askdirectory(title="選含 .mea 的資料夾",
                                    initialdir=start if os.path.isdir(start) else None)
        if not d:
            return
        state["folder"] = d
        lbl_folder.config(text=d)
        bar.config(value=0)
        lbl_now.config(text="")
        refresh()

    def worker(folder, jobs, force):
        try:
            items, _est = plan(folder, True, rules_config, trust_existing=True,
                               write=True, force=force)
            q.put(("total", len(items)))
            if jobs <= 1:
                for i, it in enumerate(items, 1):
                    q.put(("one", (i, _work(it))))
            else:
                with cf.ProcessPoolExecutor(max_workers=jobs) as pool:
                    futs = [pool.submit(_work, it) for it in items]
                    for i, fut in enumerate(cf.as_completed(futs), 1):
                        q.put(("one", (i, fut.result())))
            q.put(("done", None))
        except BaseException as exc:
            # 背景執行緒不可以無聲死掉——UI 會永遠等下去
            q.put(("error", "%s: %s" % (type(exc).__name__, exc)))

    def go():
        state["running"] = True
        btn_go.config(state="disabled")
        btn_pick.config(state="disabled")
        bar.config(value=0, maximum=1)
        threading.Thread(target=worker,
                         args=(state["folders"], int(jobs_var.get()),
                               force_var.get()),
                         daemon=True).start()

    def drain():
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "total":
                    bar.config(maximum=max(payload, 1), value=0)
                    lbl_now.config(text="開始，共 %d 個檔…" % payload)
                elif kind == "one":
                    i, (name, status) = payload
                    bar.config(value=i)
                    lbl_now.config(text="[%d/%d] %s  %s" % (i, bar["maximum"],
                                                            name, status))
                    refresh()
                elif kind == "error":
                    lbl_now.config(text=payload, foreground="#c62828")
                    state["running"] = False
                    btn_pick.config(state="normal")
                    refresh()
                elif kind == "done":
                    state["running"] = False
                    btn_pick.config(state="normal")
                    lbl_now.config(text="完成。")
                    refresh()
        except _queue.Empty:
            pass
        root.after(120, drain)

    btn_pick.config(command=pick)
    btn_go.config(command=go)
    root.after(120, drain)
    root.mainloop()


def main(argv=None):
    ap = argparse.ArgumentParser(description="預先產生 .npz 與找峰結果")
    ap.add_argument("folders", nargs="*", help="含 .mea 的資料夾")
    ap.add_argument("--all", action="store_true",
                    help="改走 GAS/ 底下每一個子資料夾")
    ap.add_argument("--peaks", action="store_true",
                    help="連找峰一起做（每檔約 55 秒，但介面點下去就有反應）")
    ap.add_argument("--jobs", type=int, default=DEFAULT_JOBS,
                    help="並行行程數（預設 %d；記憶體吃緊就調小）" % DEFAULT_JOBS)
    ap.add_argument("--trust-existing", action="store_true",
                    help="採信既有的 _peaks2.json（若它記的參數對得上）。"
                         "省下重跑，但 rules_config 無法驗證——它從沒被記進那個檔")
    ap.add_argument("--force", action="store_true",
                    help="連已經有的也重做（平常不需要：參數換過時指紋會自動重跑）")
    ap.add_argument("--dry-run", action="store_true", help="只列出要做什麼，不動手")
    args = ap.parse_args(argv)

    if not args.folders and not args.all:
        gui()                      # 不帶參數 = 開圖形介面
        return 0

    folders = list(args.folders)
    if args.all:
        # GAS/ 本身只有子資料夾、沒有 .mea——直接選它會得到 0 個樣品。
        folders += [d for d in sorted(glob.glob(os.path.join("GAS", "*")))
                    if os.path.isdir(d)]
    missing = [f for f in folders if not os.path.isdir(f)]
    if missing:
        ap.error("找不到資料夾：%s" % "、".join(missing))
    # 選到只有子資料夾的上層（例如 GAS/）時自動往下找，不要靜靜回 0 個樣品
    resolved = []
    for f in folders:
        sub = resolve_folders(f)
        if not sub:
            print("  %s 底下找不到任何 .mea" % f)
        resolved.extend(sub)
    folders = sorted(set(resolved))
    if not folders:
        ap.error("這些路徑底下都沒有 .mea")
    return run(folders, with_peaks=args.peaks, dry_run=args.dry_run,
               jobs=max(1, args.jobs), trust_existing=args.trust_existing,
               force=args.force)


if __name__ == "__main__":
    sys.exit(main())

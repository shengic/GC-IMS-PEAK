"""
main2.py  —  第二支應用的 Tk 介面：跨檔案區域強度矩陣
Version: 1.0 — by Albert Sheng

**這是第二支應用，與 main.py 完全獨立。** 本檔不 import main.py——兩支 UI 互不相依。
所有邏輯在 `areas2.py`，本檔只負責畫面與執行緒。完整說明見 `Area_Matrix2.md`。

它做什麼
--------
第一支應用一次看**一個** `.mea`；本支一次看**一整批**：在所有檔案上量同一組區域，
產出「區域 × 檔案」的強度矩陣。各檔獨立找出來的峰彼此對不齊，就沒辦法比較 A 組與
B 組；把同一組座標套到每個檔案上，每一格才有可比的數字——**包含沒偵測到峰的格子**，
那正是「這個樣品這裡沒有東西」這項資訊本身。

使用回報修掉的問題（1.0 開發期間）
----------------------------------

1. **選到沒有 .mea 的上層資料夾時毫無反應。** 檔案選擇器預設開在 `GAS/`，而 `GAS/`
   底下只有子資料夾、沒有 `.mea`，於是 Run batch 靜靜地變灰、按了沒事。現在會
   **明講**，並直接列出底下有資料的子資料夾讓人一鍵切過去。
2. **背景執行緒會無聲死掉。** `build_matrix` 原本用 `raise SystemExit`，那是
   `BaseException`，`except Exception` 接不到——執行緒死了、佇列永遠空著、UI 一直等，
   畫面全空且沒有任何錯誤。改用 `areas2.NoSamplesFound`（Exception），並把
   worker 的攔截改成 `BaseException`，任何死法都會回報。
3. **跑起來看不出在動。** 單一檔案要 90 秒以上，而進度只在檔案交界更新一次。三個
   對策：(a) **Progress log 面板攔 stdout**，所以連 `peaks.py` / `readGAS.py` 的
   `print()` 都看得到（那才是最花時間的部分，而既有模組一律不修改，攔 stdout 是唯一
   接得到的方式）；(b) 狀態列每秒更新經過秒數——union-find 那一步要跑約 50 秒且中間
   完全不輸出，沒有這個就跟當掉沒兩樣；(c) `files: 1/2/5` 讓第一次試跑只要一兩分鐘。

4. **欄位標題全是 `?`。** 兩件事疊在一起：`Class` 原本只在「用 .gasprj 區域」時才讀，
   共識模式整組遺失；而標題寫成「class + 換行 + 檔名」，但 ttk.Treeview 的 heading
   **只畫一行**，第二行整個看不見——於是只剩下空 class 的 `?`。
5. **字太小、18 欄讀不了。** 字型放大（Treeview 不吃 `font=`，要走 `ttk.Style`），
   並加上 **summary 視圖**：每個實驗組一欄，逐檔完整數值留在雙擊列開的視窗裡。
6. **批次太慢。** 加上 Fast 模式——找峰佔整批時間 **75%**（實測單檔 union-find 55.4 s），
   而方框來自 `.gasprj` 時它只用來填 `n_det` 欄，可以整段跳過。
"""

import os
import queue
import sys
import threading
import time
import traceback
from tkinter import (
    filedialog, messagebox, ttk, BooleanVar, Button, Checkbutton, END, Frame,
    Label, StringVar, Text, Tk, Toplevel, TclError,
)
from tkinter.ttk import Combobox, PanedWindow, Progressbar, Treeview

import areas2

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
GAS_DIR = os.path.join(PROJECT_DIR, "GAS")
RESULTS_DIR = os.path.join(PROJECT_DIR, "results")

BG = "white"
FONT = ("Georgia", 10)
FONT_S = ("Georgia", 9)
FONT_B = ("Georgia", 11, "bold")
MONO = ("Consolas", 9)
# 表格自己的字型要另外設：ttk.Treeview 不吃 font= 參數，得走 Style。
TREE_FONT = ("Segoe UI", 10)
TREE_HEAD_FONT = ("Segoe UI", 10, "bold")
TREE_ROW_H = 24

# 每個選項旁邊都要有一句白話說明。這幾個開關的意思不是自明的——實際使用時
# 「baseline 是什麼」「metric 是什麼」正是最先被問到的兩件事。
HELP_BASELINE = "扣掉隨溫度上升的傾斜基線。體積積分需要它（每檔 +16 秒）"
HELP_METRIC = "每一格顯示什麼：volume=方框內訊號總和（定量用）· max=峰高 · mean=平均"
HELP_GASPRJ = "改用 VOCal 畫好的方框（僅供對照驗證，非正常流程）"
HELP_SKIP = ("跳過找峰直接量測：單檔 74 → 1.5 秒（不扣基線）或約 19 秒（扣基線）。"
             "找峰佔整批時間 75%，而方框來自 .gasprj 時它只用來填 n_det 欄。"
             "代價：n_det 會是「—」。需先勾上面那項")


class _StdoutTee:
    """把背景工作的 stdout 同時送到原本的主控台**和** UI 的訊息面板。

    為什麼需要：`areas2.log` 換掉之後只接得到 areas2 自己的訊息（約十來行），但真正
    讓人乾等的是 `peaks.py` 的偵測（約 83 秒）與 `readGAS.py` 的讀檔，它們用的是普通
    `print()`，只會出現在主控台。使用者盯著視窗完全看不出在動——回報的「不知道有沒有
    在跑」就是這一段。攔 stdout 是唯一不必改動既有模組就能把那些訊息接過來的方式
    （既有 `.py` 一律不修改）。

    **只把整行推進佇列**，Tk 的更新一律交給主執行緒（見 `_poll`）——背景執行緒直接碰
    widget 會讓 Tk 隨機崩掉。

    `\\r` 也當作行結束：`readGAS.progress()` 用 `\\r` 原地刷新進度列，不切開的話整條
    進度列會累積成一行超長字串。
    """

    def __init__(self, orig, q):
        self.orig, self.q, self.buf = orig, q, ""

    def write(self, s):
        try:
            if self.orig is not None:
                self.orig.write(s)
        except Exception:
            pass
        self.buf += s.replace("\r\n", "\n").replace("\r", "\n")
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                self.q.put(("log", line.rstrip()))

    def flush(self):
        try:
            if self.orig is not None:
                self.orig.flush()
        except Exception:
            pass

    def isatty(self):
        return False


class AreaMatrixApp:
    def __init__(self, root):
        self.root = root
        self.root.title("GC-IMS 區域矩陣（第二支應用） — batch area matrix")
        try:
            w = int(root.winfo_screenwidth() * 0.88)
            h = int(root.winfo_screenheight() * 0.88)
            root.geometry(f"{w}x{h}")
        except TclError:
            root.geometry("1500x900")
        root.configure(bg=BG)

        self.folder = None
        self.gasprj = None
        self.result = None
        self.worker = None
        self.q = queue.Queue()
        self.stop_flag = threading.Event()
        self.metric = StringVar(value="volume")
        # **預設關閉**：本專案的目標是取代 VOCal，不是依賴它。這個選項只在想跟
        # VOCal 逐格對照時才打開。
        self.use_gasprj = BooleanVar(value=False)
        self.use_baseline = BooleanVar(value=True)
        self.skip_detect = BooleanVar(value=False)
        # 摘要視圖（每個 Class 一欄）是預設：18 個檔案逐欄擠在一起沒辦法讀，
        # 而這個實驗要比的本來就是組與組。逐檔完整數值在雙擊列開的視窗裡。
        self.view = StringVar(value="summary")
        self.limit = StringVar(value="all")

        self._build_ui()

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        style = ttk.Style()
        style.configure("Treeview", font=TREE_FONT, rowheight=TREE_ROW_H)
        style.configure("Treeview.Heading", font=TREE_HEAD_FONT)

        bar = Frame(self.root, bg=BG)
        bar.pack(side="top", fill="x", padx=8, pady=(6, 2))
        Button(bar, text="1. Browse batch folder", command=self.on_browse,
               font=FONT).pack(side="left")
        self.run_btn = Button(bar, text="2. Run batch", command=self.on_run,
                              font=FONT_B, fg="#0a5", state="disabled")
        self.run_btn.pack(side="left", padx=6)
        self.stop_btn = Button(bar, text="Stop", command=self.on_stop,
                               font=FONT, state="disabled")
        self.stop_btn.pack(side="left")
        Label(bar, text="   files:", bg=BG, font=FONT).pack(side="left")
        Combobox(bar, textvariable=self.limit, width=6, state="readonly",
                 values=("all", "1", "2", "5")).pack(side="left")
        Label(bar, text="(start with 2 to try it quickly)", bg=BG, fg="gray45",
              font=FONT_S).pack(side="left", padx=(4, 0))
        self.export_btn = Button(bar, text="Export CSV", command=self.on_export,
                                 font=FONT, state="disabled")
        self.export_btn.pack(side="right")
        vcb = Combobox(bar, textvariable=self.view, width=10, state="readonly",
                       values=("summary", "all files"))
        vcb.pack(side="right", padx=(0, 8))
        vcb.bind("<<ComboboxSelected>>", lambda e: self._fill_table())
        Label(bar, text="view:", bg=BG, font=FONT).pack(side="right")

        opt = Frame(self.root, bg=BG)
        opt.pack(side="top", fill="x", padx=8)
        Checkbutton(opt, text="Baseline (AsLS)", bg=BG, font=FONT,
                    variable=self.use_baseline).grid(row=0, column=0, sticky="w")
        Label(opt, text=HELP_BASELINE, bg=BG, fg="gray45",
              font=FONT_S).grid(row=0, column=1, sticky="w", padx=(2, 0))
        Label(opt, text="metric:", bg=BG, font=FONT).grid(row=1, column=0, sticky="w")
        cb = Combobox(opt, textvariable=self.metric, values=list(areas2.METRICS),
                      width=8, state="readonly")
        cb.grid(row=1, column=0, sticky="e")
        cb.bind("<<ComboboxSelected>>", lambda e: self._fill_table())
        Label(opt, text=HELP_METRIC, bg=BG, fg="gray45",
              font=FONT_S).grid(row=1, column=1, sticky="w", padx=(2, 0))
        self.gasprj_chk = Checkbutton(opt, text="Compare with .gasprj areas", bg=BG,
                                      font=FONT, variable=self.use_gasprj,
                                      state="disabled")
        self.gasprj_chk.grid(row=2, column=0, sticky="w")
        self.gasprj_help = Label(opt, text=HELP_GASPRJ, bg=BG, fg="gray45", font=FONT_S)
        self.gasprj_help.grid(row=2, column=1, sticky="w", padx=(2, 0))
        self.skip_chk = Checkbutton(opt, text="Fast: skip peak detection", bg=BG,
                                    font=FONT, variable=self.skip_detect,
                                    state="disabled")
        self.skip_chk.grid(row=3, column=0, sticky="w")
        Label(opt, text=HELP_SKIP, bg=BG, fg="#a60", font=FONT_S
              ).grid(row=3, column=1, sticky="w", padx=(2, 0))
        # 只在「用 .gasprj 方框」時才有意義：共識模式的區域正是從偵測到的峰長出來的
        self.use_gasprj.trace_add("write", lambda *a: self._sync_skip_state())

        self.folder_label = Label(self.root, text="(no folder selected)", bg=BG,
                                  anchor="w", fg="gray30", font=FONT)
        self.folder_label.pack(side="top", fill="x", padx=10, pady=(4, 0))

        foot = Frame(self.root, bg=BG)
        foot.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        self.status = Label(foot, text="Step 1: pick a folder that contains .mea files.",
                            bg=BG, anchor="w", font=FONT)
        self.status.pack(side="left", fill="x", expand=True)
        self.progress = Progressbar(foot, mode="determinate", length=260)
        self.progress.pack(side="right")

        pane = PanedWindow(self.root, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=8, pady=6)

        left = Frame(pane, bg=BG)
        pane.add(left, weight=1)
        Label(left, text="Files in batch", bg=BG, font=FONT_B).pack(pady=4)
        self.file_tree = Treeview(left, columns=("class",), height=14)
        self.file_tree.heading("#0", text="File")
        self.file_tree.heading("class", text="Class")
        self.file_tree.column("#0", width=210)
        self.file_tree.column("class", width=60, anchor="center")
        self.file_tree.pack(fill="both", expand=True)
        # 圖例：顏色不自明，沒有這一列使用者得自己猜琥珀跟橘差在哪
        legend = Frame(left, bg=BG)
        legend.pack(fill="x", pady=(2, 0))
        for txt, col in (("detecting", "#fff3cd"), ("measuring", "#ffd8a8"),
                         ("done", "#d7f0d7")):
            Label(legend, text="  ", bg=col, relief="solid", bd=1).pack(side="left", padx=(6, 2))
            Label(legend, text=txt, bg=BG, fg="gray40", font=FONT_S).pack(side="left")

        right = PanedWindow(pane, orient="vertical")
        pane.add(right, weight=4)

        top = Frame(right, bg=BG)
        right.add(top, weight=3)
        self.matrix_label = Label(top, text="Area × file matrix — (run a batch first)",
                                  bg=BG, font=FONT_B)
        self.matrix_label.pack(pady=4)
        holder = Frame(top, bg=BG)
        holder.pack(fill="both", expand=True)
        vs = ttk.Scrollbar(holder, orient="vertical")
        hs = ttk.Scrollbar(holder, orient="horizontal")
        self.tree = Treeview(holder, show="headings",
                             yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.config(command=self.tree.yview)
        hs.config(command=self.tree.xview)
        vs.pack(side="right", fill="y")
        hs.pack(side="bottom", fill="x")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self.on_area_detail)

        bot = Frame(right, bg=BG)
        right.add(bot, weight=1)
        Label(bot, text="Progress log", bg=BG, font=FONT_B).pack(pady=(4, 0))
        lf = Frame(bot, bg=BG)
        lf.pack(fill="both", expand=True)
        lsb = ttk.Scrollbar(lf, orient="vertical")
        self.logbox = Text(lf, height=8, font=MONO, bg="#fbfbfb", wrap="none",
                           yscrollcommand=lsb.set)
        lsb.config(command=self.logbox.yview)
        lsb.pack(side="right", fill="y")
        self.logbox.pack(side="left", fill="both", expand=True)
        self._log("Ready. Step 1: Browse batch folder → pick a folder with .mea files.")

    MAX_LOG_LINES = 4000

    def _sync_skip_state(self):
        """Fast 模式只在有 .gasprj 方框時可選；取消勾選上面那項就一併關掉。"""
        on = bool(self.use_gasprj.get()) and bool(self.gasprj)
        try:
            self.skip_chk.config(state="normal" if on else "disabled")
        except TclError:
            return
        if not on:
            self.skip_detect.set(False)

    def _mark_file(self, label):
        """依進度訊息替左側檔案清單上色，讓人一眼看出現在算到哪一個檔案。

        `label` 由 `areas2.build_matrix` 的 progress 回呼給，格式是 `"detect <名>"` /
        `"measure <名>"` / `"done"`。**檔名比對用 basename**，因為 progress 傳的就是
        basename（見 `build_matrix`）。

        顏色的意義是「階段」不是「順序」：偵測（琥珀）是慢的那一步、量測（橘）快得多，
        分開標色才看得出來卡在哪一段——只用一種「處理中」顏色的話，使用者會以為
        第二輪又從頭跑了一次。
        """
        items = getattr(self, "file_items", None)
        if not items:
            return
        try:
            phase, _, name = str(label).partition(" ")
            if phase == "detect":
                # 前面的檔案都偵測完了
                for b, iid in items.items():
                    if "detecting" in self.file_tree.item(iid, "tags"):
                        self.file_tree.item(iid, tags=("detected",))
                if name in items:
                    self.file_tree.item(items[name], tags=("detecting",))
                    self.file_tree.see(items[name])
            elif phase == "measure":
                for b, iid in items.items():
                    if "measuring" in self.file_tree.item(iid, "tags"):
                        self.file_tree.item(iid, tags=("done",))
                if name in items:
                    self.file_tree.item(items[name], tags=("measuring",))
                    self.file_tree.see(items[name])
            elif phase == "done":
                # **只收尾正在跑的那一個**，不要把整張清單塗綠。用 `files: 3` 只跑三個
                # 檔案時，其餘 12 個根本沒被處理——全部標成 done 會讓人以為整批都跑完了。
                for iid in items.values():
                    if "measuring" in self.file_tree.item(iid, "tags"):
                        self.file_tree.item(iid, tags=("done",))
        except TclError:
            pass

    def _clear_file_marks(self):
        for iid in (getattr(self, "file_items", None) or {}).values():
            try:
                self.file_tree.item(iid, tags=())
            except TclError:
                pass

    def _log(self, msg):
        try:
            self.logbox.insert(END, msg.rstrip() + "\n")
            # 15 個檔案跑下來訊息上千行；不設上限的話 Text widget 會越滾越慢。
            # 砍前面而不是清空——正在看的是最新那幾行。
            n = int(self.logbox.index("end-1c").split(".")[0])
            if n > self.MAX_LOG_LINES:
                self.logbox.delete("1.0", f"{n - self.MAX_LOG_LINES + 500}.0")
            self.logbox.see(END)
        except TclError:
            pass

    # ------------------------------------------------------------------ #
    # 選資料夾
    # ------------------------------------------------------------------ #
    def on_browse(self):
        initial = self.folder or (GAS_DIR if os.path.isdir(GAS_DIR) else None)
        folder = filedialog.askdirectory(
            title="Select the batch folder that CONTAINS .mea files",
            initialdir=initial)
        if not folder:
            return
        self._load_folder(folder)

    def _load_folder(self, folder):
        self.folder = folder
        self.folder_label.config(text=folder)
        gs = areas2.calibration.scan_folder_for_gasprj(folder)
        self.gasprj = gs[0] if gs else None
        self.gasprj_chk.config(state="normal" if self.gasprj else "disabled")
        if not self.gasprj:
            self.use_gasprj.set(False)
        self._sync_skip_state()
        self.gasprj_help.config(
            text=HELP_GASPRJ + (f"  —  {os.path.basename(self.gasprj)}"
                                if self.gasprj else "  —  此資料夾沒有 .gasprj"))
        self._populate_files()

    def _populate_files(self):
        self.file_tree.delete(*self.file_tree.get_children())
        try:
            samples, class_of, excluded = areas2._select_samples(self.folder, self.gasprj)
        except Exception as e:
            messagebox.showerror("Cannot read folder", str(e))
            return
        # basename -> tree item：跑批次時要能立刻找到「現在算到哪一個」那一列並上色
        self.file_items = {}
        for m in samples:
            b = os.path.basename(m)
            self.file_items[b] = self.file_tree.insert(
                "", "end", text=b, values=(class_of.get(b, ""),))
        # 兩個階段分開上色：偵測是慢的那一步（約 83 秒），量測快得多。看到顏色從
        # 琥珀變橘再變綠，就知道現在卡在哪一個檔案的哪一個階段。
        self.file_tree.tag_configure("detecting", background="#fff3cd")   # 琥珀＝偵測中
        self.file_tree.tag_configure("measuring", background="#ffd8a8")   # 橘＝量測中
        self.file_tree.tag_configure("detected", background="#e8f0fe")    # 淡藍＝已偵測
        self.file_tree.tag_configure("done", background="#d7f0d7")        # 綠＝完成

        if not samples:
            # 這是 2.1 修的第一個問題：以前只是把按鈕變灰，使用者按了沒事也不知道為什麼。
            self.run_btn.config(state="disabled")
            subs = [d for d in sorted(os.listdir(self.folder))
                    if os.path.isdir(os.path.join(self.folder, d))
                    and any(f.lower().endswith(".mea")
                            for f in os.listdir(os.path.join(self.folder, d)))]
            self.status.config(text="⚠ No .mea files directly in this folder — "
                                    "pick one of its subfolders instead.")
            self._log(f"⚠ {self.folder} contains no .mea files.")
            if subs:
                self._log("   Subfolders that DO contain .mea:")
                for d in subs:
                    self._log(f"      {d}")
                if messagebox.askyesno(
                        "No .mea in this folder",
                        f"{self.folder}\n\nhas no .mea files directly inside it.\n\n"
                        f"These subfolders do:\n\n  "
                        + "\n  ".join(subs)
                        + "\n\nOpen the first one now?"):
                    self._load_folder(os.path.join(self.folder, subs[0]))
            return

        n_std = len(excluded.get("std") or [])
        n_blk = len(excluded.get("blank") or [])
        self.run_btn.config(state="normal")
        self.status.config(
            text=f"Step 2: Run batch.   {len(samples)} sample .mea"
                 + (f"  (excluded {n_std} STD, {n_blk} blank)" if n_std or n_blk else "")
                 + (f"  ·  {os.path.basename(self.gasprj)} present"
                    if self.gasprj else "  ·  no .gasprj"))
        self._log(f"Loaded {len(samples)} sample .mea from {self.folder}"
                  + (f"  (excluded {n_std} STD, {n_blk} blank)" if n_std or n_blk else ""))

    # ------------------------------------------------------------------ #
    # 執行
    # ------------------------------------------------------------------ #
    def on_run(self):
        if not self.folder or self.worker and self.worker.is_alive():
            return
        n_all = len(self.file_tree.get_children())
        lim = None if self.limit.get() == "all" else int(self.limit.get())
        n = min(n_all, lim or n_all)

        self.stop_flag.clear()
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.export_btn.config(state="disabled")
        self.progress.config(value=0, maximum=max(1, n * 2))
        self.q = queue.Queue()
        self.logbox.delete("1.0", END)
        self._log(f"=== Running {n} file(s) — first pass detect, second pass measure ===")
        self._log(f"    baseline={self.use_baseline.get()}  "
                  f"gasprj_areas={self.use_gasprj.get()}")
        self._log("    First time is ~1.5-2 min per file; cached runs are fast.")

        gasprj = self.gasprj if self.use_gasprj.get() else None
        baseline = self.use_baseline.get()
        skip = self.skip_detect.get() and bool(gasprj)
        folder = self.folder
        q = self.q

        # 不再另外攔 `areas2.log`：它本身就是 print()，已經被下面的 stdout tee 接走。
        # 兩邊都接會讓每一行重複出現兩次（實測過）。
        def progress(done, total, label):
            q.put(("progress", done, total, label))

        def work():
            # stdout 一併攔下來：偵測/讀檔那些最花時間的步驟用的是普通 print()，
            # 不攔就只會出現在主控台，視窗上看不到任何動靜。
            orig_stdout = sys.stdout
            try:
                sys.stdout = _StdoutTee(orig_stdout, q)
                res = areas2.build_matrix(
                    folder, from_gasprj=gasprj, use_baseline=baseline, limit=lim,
                    skip_detect=skip, progress=progress,
                    should_stop=self.stop_flag.is_set, verbose=True)
                q.put(("done", res))
            except areas2.BatchCancelled:
                q.put(("cancelled", None))
            except areas2.NoSamplesFound as e:
                q.put(("nosamples", str(e)))
            except BaseException as e:
                # **BaseException 而不是 Exception**：SystemExit / KeyboardInterrupt 之類
                # 一樣要回報。原本用 Exception，執行緒因此會無聲死掉、UI 永遠空白。
                q.put(("error", f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"))
            finally:
                sys.stdout = orig_stdout

        self._run_t0 = time.time()
        self._last_label = "starting…"
        self._clear_file_marks()
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        self.root.after(100, self._poll)
        self._tick()

    def _tick(self):
        """每秒更新一次經過秒數。

        找峰的 union-find 那一步要跑約 50 秒且**中間完全不輸出**，畫面因此看起來像
        當掉了——使用者回報的「不知道有沒有在跑」主要就是這一段。有一個一直在動的
        秒數，至少能一眼確定它還活著。
        """
        if self.worker and self.worker.is_alive():
            el = int(time.time() - self._run_t0)
            self.status.config(text=f"{self._last_label}   —   {el // 60}:{el % 60:02d} elapsed")
            self.root.after(1000, self._tick)

    def on_stop(self):
        if self.worker and self.worker.is_alive():
            self.stop_flag.set()
            self.stop_btn.config(state="disabled")
            self.status.config(text="Stopping after the current file…")
            self._log("Stop requested — will halt after the current file.")

    def _finish(self, msg, ok=True):
        self.run_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status.config(text=msg)
        self._log(msg)

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._log(msg[1])
                elif kind == "progress":
                    _, done, total, label = msg
                    self.progress.config(value=done, maximum=max(1, total))
                    self._last_label = f"[{done}/{total}] {label}"
                    self._mark_file(label)
                elif kind == "done":
                    self.result = msg[1]
                    self.progress.config(value=self.progress["maximum"])
                    try:
                        self._fill_table()
                        self._save_outputs()
                    except Exception as e:
                        # 顯示層出錯時要講出來，不然表格空白又沒有任何線索
                        self._finish(f"Computed, but display/export failed: {e}", ok=False)
                        messagebox.showerror("Display failed", traceback.format_exc()[:2000])
                        return
                    self.export_btn.config(state="normal")
                    self.run_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    return
                elif kind == "cancelled":
                    self.progress.config(value=0)
                    self._finish("Batch stopped by user — no results written.")
                    return
                elif kind == "nosamples":
                    self.progress.config(value=0)
                    self._finish("No sample .mea in that folder — see dialog.", ok=False)
                    messagebox.showwarning("No .mea files found", msg[1])
                    return
                elif kind == "error":
                    self.progress.config(value=0)
                    self._finish("Batch failed — see dialog.", ok=False)
                    messagebox.showerror("Batch failed", msg[1][:2000])
                    return
        except queue.Empty:
            pass
        if self.worker and not self.worker.is_alive() and self.q.empty():
            # 保險絲：執行緒沒了又什麼都沒留下（理論上不該發生，因為攔的是
            # BaseException）。與其讓 UI 永遠空轉，不如明講。
            self._finish("Worker stopped without reporting a result "
                         "(unexpected — please report).", ok=False)
            return
        self.root.after(100, self._poll)

    def _save_outputs(self):
        js, csv = areas2.result_paths(self.result)
        areas2.write_result_json(self.result, js)
        areas2.write_matrix_csv(self.result, csv, metric=self.metric.get())
        p = self.result["provenance"]
        caveat = f"   ⚠ {p['ri_caveat']}" if p.get("ri_caveat") else ""
        self._log(f"Wrote {js}")
        self._log(f"Wrote {csv}")
        self.status.config(
            text=f"✓ {self.result['n_areas']} areas × {self.result['n_files']} files"
                 f"   ri_mode={p['ri_mode']}  k0_mode={p['k0_mode']}"
                 f"  areas={p['area_selection']['mode']}{caveat}")

    # ------------------------------------------------------------------ #
    # 矩陣表格
    # ------------------------------------------------------------------ #
    ID_COLS = ("#", "name", "RI", "drift", "rt_s", "n_det")

    def _fill_table(self):
        if not self.result:
            return
        summary = self.view.get() == "summary"
        if summary:
            headers, rows = areas2.matrix_summary_rows(
                self.result, metric=self.metric.get())
        else:
            headers, rows = areas2.matrix_as_rows(
                self.result, metric=self.metric.get())
        self.tree.delete(*self.tree.get_children())
        self.tree.config(columns=headers)
        for h in headers:
            # **標題不能塞換行**：ttk.Treeview 的 heading 只畫一行，第二行整個看不見。
            # 原本用 f"{class}\n{filename}"，於是畫面上只剩下 class——而共識模式沒有
            # class，就變成一整排 `?`（使用者回報的就是這個）。
            self.tree.heading(h, text=h)
            self.tree.column(h, anchor="center", width=(
                190 if h == "name" else 62 if h in self.ID_COLS else 92))
        self.tree.tag_configure("named", background="#eef7ee")
        for r in rows:
            named = not str(r[1]).lower().startswith("area ")
            self.tree.insert("", "end", values=r, tags=("named",) if named else ())
        n_groups = len([g for g in areas2.class_groups(self.result) if g])
        what = (f"{n_groups} class groups (mean)" if summary and n_groups
                else "batch mean/min/max" if summary
                else f"{self.result['n_files']} files")
        self.matrix_label.config(
            text=f"Area × file matrix — {len(rows)} areas × {what}   "
                 f"(metric: {self.metric.get()} · double-click a row for every file)")

    def on_area_detail(self, event):
        if not self.result:
            return
        sel = self.tree.selection()
        if not sel:
            return
        try:
            aid = int(self.tree.item(sel[0], "values")[0])
        except (ValueError, IndexError):
            return
        area = next((a for a in self.result["areas"] if a["area_id"] == aid), None)
        if area is None:
            return

        win = Toplevel(self.root)
        win.title(f"Area {aid} — {area.get('name', '')}")
        win.geometry("580x540")
        win.configure(bg=BG)

        bar = Frame(win, bg=BG)
        bar.pack(side="bottom", fill="x", padx=8, pady=8)
        Button(bar, text="Close", command=win.destroy, font=FONT,
               width=10).pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())

        hdr = Frame(win, bg=BG)
        hdr.pack(fill="x", padx=10, pady=(10, 4))
        fields = [
            ("Name", area.get("name")),
            ("CAS", area.get("cas") or "—"),
            ("Drift rel. RIP", f"{area['drift_center']:.4f} ± {area['drift_half']:.4f}"),
            ("Retention", f"{area['rt_center_s']:.1f} ± {area['rt_half_s']:.1f} s"),
            ("RI", "—" if area.get("ri_center") is None else
             f"{area['ri_center']:.1f}{'*' if area.get('ri_extrapolated') else ''}"),
            ("Detected in", f"{area.get('n_files_detected', 0)} of "
                            f"{self.result['n_files']} files"),
            ("Candidates", f"GC {area.get('n_gc', 0)} · IMS {area.get('n_ims', 0)}"
                           f" · both {area.get('n_combined', 0)}"),
        ]
        if area.get("matched_name") and area.get("matched_name") != area.get("name"):
            fields.append(("Our match", f"{area['matched_name']} "
                                        f"({area.get('matched_cas') or '—'})"))
        for i, (k, v) in enumerate(fields):
            Label(hdr, text=f"{k}:", bg=BG, fg="gray30", font=FONT,
                  anchor="w").grid(row=i, column=0, sticky="w")
            Label(hdr, text=str(v), bg=BG, font=FONT,
                  anchor="w").grid(row=i, column=1, sticky="w", padx=(10, 0))

        Label(win, text="Per-file values", bg=BG, font=FONT_B).pack(pady=(10, 2))
        frame = Frame(win, bg=BG)
        frame.pack(fill="both", expand=True, padx=10)
        cols = ("file", "class", "volume", "max", "mean", "peak")
        t = Treeview(frame, columns=cols, show="headings", height=10)
        for c, w in zip(cols, (170, 55, 90, 70, 70, 50)):
            t.heading(c, text=c)
            t.column(c, width=w, anchor="center")
        sb = ttk.Scrollbar(frame, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        classes = self.result.get("classes") or {}
        det = set(area.get("detected_in") or [])
        for b in self.result["files"]:
            cell = (self.result["matrix"].get(b) or {}).get(aid) or {}

            def fmt(k):
                v = cell.get(k)
                return "" if v is None else f"{v:g}"
            t.insert("", "end", values=(b, classes.get(b, ""), fmt("volume"),
                                        fmt("max"), fmt("mean"),
                                        "✓" if b in det else ""))

    # ------------------------------------------------------------------ #
    def on_export(self):
        if not self.result:
            return
        _js, default_csv = areas2.result_paths(self.result)
        path = filedialog.asksaveasfilename(
            title="Export matrix as CSV", defaultextension=".csv",
            initialfile=os.path.basename(default_csv), initialdir=RESULTS_DIR,
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        areas2.write_matrix_csv(self.result, path, metric=self.metric.get())
        self._finish(f"Exported ({self.metric.get()}) → {path}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    root = Tk()
    AreaMatrixApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

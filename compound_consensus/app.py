"""第三支應用的 Tk 介面。

**三個模式（radio），決定「點一個 .mea 是什麼意思」**：

| 模式 | 點檔案的意思 | 右側面板 |
|---|---|---|
| 1 熱圖選峰（預設） | 看這個檔的熱圖，增刪紅圈 | 該檔的峰 |
| 2 選同組 | 把這個檔的建議同組整組帶進來；再點別的檔＝加入/移出 | 該組成員與相似度 |
| 3 共識化合物 | 檔案面板不變 | 化合物名稱與票數 |

用模式而不是「單擊 vs 雙擊」是有理由的：Tk 一定先送單擊才送雙擊，所以
「雙擊＝加入這組」會順帶觸發一次載入熱圖——白跑一次偵測與繪圖，而且看得到卡頓。

**沒有任何 subprocess。** 第一支應用用 `[sys.executable, "peaks.py", ...]` 起子行程，
那在打包成 exe 之後會變成用 exe 再開一次 GUI。這裡一律函式呼叫。

Version: 1.0 — by Albert Sheng（第三支應用，2026-08-31）
"""
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

import areas2
import calibration
import identify
import library
import peaks as peaks_mod
import rip as rip_mod
import rules as rules_mod

from . import logic as L
from . import state as state_mod

#: Treeview 沒有原生核取方塊，用字元代替（同第一支應用 CHECK_ON / CHECK_OFF）
CHECK_ON = "☑"
CHECK_OFF = "☐"

#: 校正系列，與第一、二支應用一致（`areas2.RI_SERIES`）。
RI_SERIES = areas2.RI_SERIES

MODE_HEATMAP, MODE_GROUP, MODE_COMPOUND = "heatmap", "group", "compound"

#: 票數分級 → 底色。**依票數佔比而非原始票數**（見 logic.VOTE_TIERS）：一組 3 個
#: 重複和一組 15 個重複，「2 票」的意義天差地遠。
TIER_BG = {4: "#c8e6c9", 3: "#dcedc8", 2: "#fff9c4", 1: "#ffe0b2", 0: "#eeeeee"}
TIER_FG = {4: "#1b5e20", 3: "#33691e", 2: "#f57f17", 1: "#e65100", 0: "#9e9e9e"}

#: 建議同組的門檻。**只用來建議，不用來決定**——實測自動分組 43/45 = 96%，
#: 剩下的那幾個正是需要人看的，所以使用者一定要能自己增刪。
SUGGEST_R = 0.80


class ConsensusApp:
    def __init__(self, root):
        self.root = root
        root.title("GC-IMS 化合物共識 — 第三支應用")
        # 寫死 1600x980 會超出小螢幕、視窗一半跑到畫面外。改成照螢幕大小開，
        # 再交給 zoomed 最大化（同第一支應用的作法）。
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        root.geometry("%dx%d+0+0" % (sw, max(400, sh - 60)))
        # **下限**：狀態列的文字在掃描時每個檔都變一次，pack 會把寬度變化一路傳到
        # 最上層；面板還空著時「自然大小」很小，視窗就會縮成一小塊，掃完文字穩定
        # 下來才彈回去。實際回報過。minsize 讓它不管怎樣都縮不下去。
        root.minsize(min(1100, sw - 40), min(700, sh - 120))
        try:
            root.state("zoomed")
        except tk.TclError:
            pass                       # 某些視窗管理員不支援，退回上面的尺寸

        self.folder = None
        self.files = []
        self.group = set()
        self.current = None
        self.peaks = []
        self.geom = None
        self.photo = None
        self.img_orig = None        # 未縮放的背景圖，縮放一律從它重算
        self.fit_scale = 1.0        # 「整張塞進畫布」的比例
        self.zoom = 1.0             # 使用者的縮放倍率（相對 fit_scale）
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.highlight_id = None    # 黃色選取環（與紅圈分開的獨立物件）
        self.highlighted = None     # 目前選取的峰 index
        self._floor = None          # 規則重新標記需要的 context
        self._rip_index = None
        self._pan_last = None
        self._press = None
        self.circles = {}
        self.corr = None
        self.corr_files = []
        self.consolidated = []
        self.ri_note = ""
        self.rules_config = rules_mod.load_config("rules_config.json")
        self.mode = tk.StringVar(value=MODE_HEATMAP)
        self.busy = None            # None 或「掃描」/「彙整」，給結束確認用
        self.ri_cal = None          # 資料夾的 RI 校正；圈的 y 座標與比對都要它
        self.q = queue.Queue()

        self._build()
        self.root.after(100, self._drain)

    # ------------------------------------------------------------------ UI
    def _build(self):
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=6, pady=4)
        ttk.Button(bar, text="1. 選資料夾", command=self.pick_folder).pack(side="left")
        self.btn_scan = ttk.Button(bar, text="2. 掃描全部（找峰）",
                                   command=self.scan_group, state="disabled")
        self.btn_scan.pack(side="left", padx=4)
        self.btn_cons = ttk.Button(bar, text="3. Consolidate",
                                   command=self.consolidate, state="disabled")
        self.btn_cons.pack(side="left")
        ttk.Label(bar, text="門檻").pack(side="left", padx=(16, 2))
        self.frac = tk.StringVar(value="2/3")
        ttk.Combobox(bar, textvariable=self.frac, width=6, state="readonly",
                     values=["1/2", "2/3", "3/4", "1/1"]).pack(side="left")
        ttk.Button(bar, text="Rules", command=self.open_rules).pack(side="left",
                                                                    padx=(16, 0))
        ttk.Button(bar, text="結束", command=self.quit_app).pack(side="right")
        # 固定寬度 + 不隨內容伸縮：文字長度不可以決定版面寬度
        self.status = ttk.Label(bar, text="請先選一個含 .mea 的資料夾",
                                foreground="#555", width=70, anchor="w")
        self.status.pack(side="left", padx=12, fill="x", expand=True)
        # 視窗右上角的 X 走同一條路，行為才一致
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        pane = ttk.PanedWindow(self.root, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=6, pady=4)
        self._build_left(pane)
        self._build_mid(pane)
        self._build_right(pane)

    # --------------------------------------------------------------- Rules
    def open_rules(self):
        """看得到「哪些規則正在作用、它們做掉了什麼」。

        **選配規則（R001/R002/R003/R005）可以現場切換**：它們只呼叫
        `rules.mark_rules()` 標記 `rule_active`，不改變偵測結果，所以重新標記加
        重畫就好（毫秒級）。

        **強制規則（R004/R006）的開關鎖住、參數可改**：它們在突出度門檻**之前**
        生效（`peaks.pre_gate_params()`），被它們擋掉的候選根本不在峰清單裡——不是
        「被標記為不要」，所以不能關掉。但參數可以調，調了會移動峰編號的基準集合，
        因此必須重跑偵測（每檔約 55 秒）；這裡不自己重跑，靠參數指紋在下次載入或
        掃描時發現並重做。鎖開關而不隱藏，才看得出它們一直在跑——同第一支應用。
        """
        win = tk.Toplevel(self.root)
        win.title("Rules — 這些規則正在挑峰")
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = min(560, sw - 60), min(620, sh - 100)
        win.geometry("%dx%d+%d+%d" % (w, h, max(0, sw - w - 20), 40))
        win.transient(self.root)
        outer = ttk.Frame(win, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="規則", font=("TkDefaultFont", 11, "bold")).pack(
            anchor="w")
        body = ttk.Frame(outer)
        body.pack(fill="x", pady=(4, 10))

        saved = {e.get("rule_number"): e for e in self.rules_config}
        self._rule_vars = {}
        for row, rule in enumerate(rules_mod.list_rules()):
            rn = rule["rule_number"]
            entry = saved.get(rn) or {"enabled": False, "params": {}}
            locked = bool(rule.get("mandatory"))
            var = tk.BooleanVar(value=True if locked else bool(entry.get("enabled")))
            chk = ttk.Checkbutton(
                body, variable=var,
                text="%s  %s%s" % (rn, rule["name"],
                                   "   （強制，一律作用）" if locked else ""),
                command=self._on_rules_changed)
            if locked:
                chk.state(["disabled"])
            chk.grid(row=row, column=0, sticky="w", pady=2)

            # 參數可以改——**連強制規則的參數也可以**。R004/R006 本身不能關掉
            # （它們定義峰編號的基準集合），但它們的參數是可調的，改了會移動基準、
            # 讓峰重新編號，所以必須重跑偵測。第一支應用也是這樣：鎖開關、不鎖參數。
            pframe = ttk.Frame(body)
            pframe.grid(row=row, column=1, sticky="w", padx=(12, 0))
            pvars = {}
            for name, value in (entry.get("params") or {}).items():
                ttk.Label(pframe, text="%s =" % name).pack(side="left")
                sv = tk.StringVar(value=str(value))
                ent = ttk.Entry(pframe, textvariable=sv, width=9)
                ent.pack(side="left", padx=(2, 10))
                ent.bind("<KeyRelease>", lambda _e: self._on_rules_changed())
                ent.bind("<FocusOut>", lambda _e: self._on_rules_changed())
                pvars[name] = {"var": sv, "entry": ent, "orig": value}
            self._rule_vars[rn] = {"on": var, "locked": locked, "params": pvars}
        body.columnconfigure(1, weight=1)

        ttk.Label(outer, text="選取漏斗", font=("TkDefaultFont", 11, "bold")).pack(
            anchor="w")
        self._rules_funnel = tk.Text(outer, height=10, wrap="none",
                                     font=("Consolas", 9), relief="solid",
                                     borderwidth=1)
        self._rules_funnel.pack(fill="both", expand=True, pady=(4, 8))
        ttk.Label(outer, foreground="#666", wraplength=w - 40, justify="left",
                  text="切換選配規則會立刻重新標記並重畫（毫秒級）——它們只標記，"
                       "不改變偵測。強制規則鎖住：改它們要重跑每個檔約 55 秒的偵測，"
                       "請改 rules_config.json 之後重新掃描。"
                  ).pack(anchor="w", pady=(0, 8))
        btns = ttk.Frame(outer)
        btns.pack(fill="x")
        ttk.Button(btns, text="存回 rules_config.json",
                   command=self._save_rules).pack(side="left")
        ttk.Button(btns, text="關閉", command=win.destroy).pack(side="right")
        self._rules_win = win
        self._on_rules_changed()

    @staticmethod
    def _coerce(text, original):
        """把輸入的字串轉回原本的型別。**轉不動就丟 ValueError**，不要默默用 0。

        型別要跟著原值走：`top_n` 是整數、`half_width` 是小數，全都當 float 會讓
        `rules_config.json` 存出 `top_n: 0.0` 這種東西。
        """
        t = (text or "").strip()
        if isinstance(original, bool):
            return t.lower() in ("1", "true", "yes", "on")
        if isinstance(original, int) and not isinstance(original, bool):
            return int(float(t))
        return float(t)

    def _on_rules_changed(self):
        """套用面板上的規則設定。

        選配規則只要重新標記（毫秒級）；**強制規則的參數改了就得重跑偵測**——
        它們在突出度門檻之前生效，會改變哪些峰活得下來。這裡不自己重跑，而是靠
        參數指紋：下次載入或掃描時 `peaks_are_current()` 會發現對不上並重做。
        """
        if not getattr(self, "_rule_vars", None):
            return
        bad, pre_gate_changed = [], False
        before = peaks_mod.pre_gate_params(self.rules_config)
        for entry in self.rules_config:
            rn = entry.get("rule_number")
            spec = self._rule_vars.get(rn)
            if not spec:
                continue
            if not spec["locked"]:
                entry["enabled"] = bool(spec["on"].get())
            for name, pv in spec["params"].items():
                try:
                    value = self._coerce(pv["var"].get(), pv["orig"])
                except (TypeError, ValueError):
                    # 無效的值不寫進 config，也不靜靜忽略——把輸入框標紅
                    bad.append("%s.%s" % (rn, name))
                    pv["entry"].configure(foreground="#c62828")
                    continue
                pv["entry"].configure(foreground="")
                entry.setdefault("params", {})[name] = value
        pre_gate_changed = peaks_mod.pre_gate_params(self.rules_config) != before

        report = None
        if self.peaks:
            report = rules_mod.mark_rules(
                self.peaks, self.rules_config,
                context={"floor": self._floor, "rip_index": self._rip_index})
            L.apply_effective(self.peaks)      # 規則變了，active 要跟著更新
            self._render_canvas()
            self._fill_peak_table()
        self._write_funnel(report, bad=bad, pre_gate_changed=pre_gate_changed)
        if pre_gate_changed:
            self._rules_dirty = True

    def _save_rules(self):
        """存回 `rules_config.json`。

        `rules.save_config()` 會強制 R004/R006 保持啟用——強制規則不能靠手改設定檔
        繞過（第一支應用的規定，這裡沿用同一個函式，不自己寫檔）。
        """
        try:
            rules_mod.save_config("rules_config.json", self.rules_config)
        except Exception as exc:
            messagebox.showerror("存檔失敗", "%s: %s" % (type(exc).__name__, exc))
            return
        self.status.config(text="規則已存回 rules_config.json")

    def _write_funnel(self, report, bad=None, pre_gate_changed=False):
        box = getattr(self, "_rules_funnel", None)
        if box is None or not box.winfo_exists():
            return
        lines = []
        if not self.peaks:
            lines.append("尚未載入任何檔——先在模式 1 點一個 .mea。")
        else:
            n = len(self.peaks)
            by_rule = sum(1 for p in self.peaks
                          if not p.get("rule_active", True)
                          and p.get("user_active") is not True)
            by_hand = sum(1 for p in self.peaks if p.get("user_active") is False)
            rescued = sum(1 for p in self.peaks if L.is_rule_override(p))
            lines += [
                "檔案            %s" % (os.path.basename(self.current or "—")),
                "偵測到的峰      %d" % n,
                "被選配規則否決  %d" % by_rule,
                "被你手動取消    %d" % by_hand,
                "被你救回來      %d" % rescued,
                "目前納入共識    %d" % sum(1 for p in self.peaks
                                            if L.effective_active(p)),
                "",
                "註：R004/R006 擋掉的候選**不在上面的數字裡**——它們在突出度門檻",
                "    之前就被排除，從來沒有成為「峰」。",
            ]
            if report:
                lines += ["", "mark_rules: n_in=%s n_out=%s"
                          % (report.get("n_in"), report.get("n_out"))]
        if bad:
            lines += ["", "⚠ 這些值看不懂，沒有套用：" + "、".join(bad)]
        if pre_gate_changed or getattr(self, "_rules_dirty", False):
            lines += ["", "⚠ R004/R006 的參數改過了。它們在突出度門檻之前生效，",
                      "   所以峰要重新偵測才會反映——下次載入或掃描時會自動重跑",
                      "   （每檔約 55 秒），因為參數指紋已經對不上了。"]
        box.config(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", "\n".join(lines))
        box.config(state="disabled")

    def quit_app(self):
        """有背景工作在跑就先問一聲。

        **峰的選取不會白費**——每次勾選當下就寫進 `_peaks_state3.json`，不是等到
        關閉才存。會丟掉的只有正在跑的那一次掃描或彙整。
        """
        if self.busy:
            if not messagebox.askyesno(
                    "還在處理中",
                    "目前正在%s。現在結束會中斷它。\n\n"
                    "已經勾選的峰都已經存好了（每次勾選當下就寫檔），"
                    "已找完峰的檔也都有快取，下次再跑不用重來。\n\n確定要結束嗎？"
                    % self.busy):
                return
        self.root.destroy()

    def _build_left(self, pane):
        left = ttk.Frame(pane)

        box = ttk.LabelFrame(left, text="點一個 .mea 的意思是")
        box.pack(fill="x", pady=(0, 4))
        for val, txt in ((MODE_HEATMAP, "1. 看熱圖、選峰"),
                         (MODE_GROUP, "2. 選同標本這一組"),
                         (MODE_COMPOUND, "3. 看共識化合物")):
            ttk.Radiobutton(box, text=txt, value=val, variable=self.mode,
                            command=self.on_mode_change).pack(anchor="w", padx=6)

        self.tree_files = ttk.Treeview(left, columns=("g", "r"),
                                       show="tree headings", height=22)
        self.tree_files.heading("#0", text="檔案")
        self.tree_files.heading("g", text="組 ✓")
        self.tree_files.heading("r", text="相似度")
        self.tree_files.column("#0", width=240)
        self.tree_files.column("g", width=52, anchor="center")
        self.tree_files.column("r", width=132, anchor="center")
        self.tree_files.pack(fill="both", expand=True)
        # 組欄的方塊在**任何模式**都可以點——看起來能點就要能點（同 On 欄）。
        # 不必為了加一個檔進這一組而特地切到模式 2。
        self.tree_files.bind("<Button-1>", self.on_files_click, add="+")
        self.tree_files.bind("<<TreeviewSelect>>", self.on_file_click)
        self.tree_files.tag_configure("ingroup", background="#e3f2fd")
        self.hint = ttk.Label(left, foreground="#777", wraplength=270,
                              justify="left", text="")
        self.hint.pack(anchor="w")
        pane.add(left, weight=1)

    def _build_mid(self, pane):
        mid = ttk.Frame(pane)
        # 底色用白：熱圖 PNG 四周本來就是白的，保持長寬比時多出來的空白就看不見。
        # 用深色會在圖的下方留一條明顯的黑帶，看起來像畫錯了。
        self.canvas = tk.Canvas(mid, bg="white", highlightthickness=0,
                                width=700, height=600)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<MouseWheel>", self.on_wheel)              # Windows / macOS
        self.canvas.bind("<Button-4>", lambda e: self.on_wheel(e, delta=120))
        self.canvas.bind("<Button-5>", lambda e: self.on_wheel(e, delta=-120))
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Configure>", self.on_canvas_resize)
        self.canvas.bind("<Double-1>", self.on_canvas_double)
        pane.add(mid, weight=3)

    def _build_right(self, pane):
        right = ttk.Frame(pane)
        self.right_title = ttk.Label(right, text="峰（勾 = 納入共識）")
        self.right_title.pack(anchor="w")
        self.right_holder = ttk.Frame(right)
        self.right_holder.pack(fill="both", expand=True)

        # --- 面板 A：逐檔的峰
        self.pane_peaks = ttk.Frame(self.right_holder)
        self.tree_peaks = ttk.Treeview(self.pane_peaks,
                                       columns=("n", "on", "dr", "ri", "int"),
                                       show="headings")
        # `#` 是圈上的編號——沒有它就對不出表格哪一列是圖上哪一個圈。
        # 不放 RT s：y 軸顯示的是 RI，兩個保留時間欄位並排只會佔位子。
        for col, txt, wid in (("n", "#", 44), ("on", "On", 44),
                              ("dr", "Drift rel", 84), ("ri", "RI", 76),
                              ("int", "強度", 80)):
            self.tree_peaks.heading(col, text=txt)
            self.tree_peaks.column(col, width=wid, anchor="center")
        self.tree_peaks.pack(fill="both", expand=True)
        # 點 On 那一欄就切換——核取方塊看起來可以點，就必須真的可以點。
        # 用 identify_column 判斷點在哪一欄（同第一支應用 on_peak_tree_click）。
        self.tree_peaks.bind("<Button-1>", self.on_peaks_click, add="+")
        self.tree_peaks.bind("<<TreeviewSelect>>", self.on_peak_select)
        self.tree_peaks.bind("<space>", self.toggle_peak)
        self.tree_peaks.bind("<Double-1>", self.toggle_peak)
        self.tree_peaks.tag_configure("off", foreground="#bbbbbb")
        # 被規則否決的峰不消失，只是看起來不一樣——使用者才看得到規則做了什麼
        self.tree_peaks.tag_configure("byrule", foreground="#c98a00")

        # --- 面板 B：這一組的成員
        self.pane_group = ttk.Frame(self.right_holder)
        self.tree_group = ttk.Treeview(self.pane_group, columns=("r", "st"),
                                       show="tree headings")
        self.tree_group.heading("#0", text="組員")
        self.tree_group.heading("r", text="相似度")
        self.tree_group.heading("st", text="狀態")
        self.tree_group.column("#0", width=220)
        self.tree_group.column("r", width=70, anchor="e")
        self.tree_group.column("st", width=90, anchor="center")
        self.tree_group.pack(fill="both", expand=True)

        # --- 面板 C：共識化合物（名稱 + 票數）
        self.pane_cmpd = ttk.Frame(self.right_holder)
        self.tree_cmpd = ttk.Treeview(self.pane_cmpd,
                                      columns=("votes", "dim", "n", "rt"),
                                      show="tree headings")
        self.tree_cmpd.heading("#0", text="化合物")
        self.tree_cmpd.heading("votes", text="票數")
        self.tree_cmpd.heading("dim", text="維度")
        self.tree_cmpd.heading("n", text="候選")
        self.tree_cmpd.heading("rt", text="RT s")
        self.tree_cmpd.column("#0", width=250)
        self.tree_cmpd.column("votes", width=60, anchor="center")
        self.tree_cmpd.column("dim", width=64, anchor="center")
        self.tree_cmpd.column("n", width=50, anchor="center")
        self.tree_cmpd.column("rt", width=70, anchor="e")
        self.tree_cmpd.pack(fill="both", expand=True)
        self.tree_cmpd.bind("<Double-1>", self.show_candidates)
        for tier, bg in TIER_BG.items():
            self.tree_cmpd.tag_configure("t%d" % tier, background=bg,
                                         foreground=TIER_FG[tier])

        self.right_note = ttk.Label(right, foreground="#777", wraplength=300,
                                    justify="left", text="")
        self.right_note.pack(anchor="w")
        pane.add(right, weight=2)
        self.on_mode_change()

    # ------------------------------------------------------------- 模式
    def on_mode_change(self):
        m = self.mode.get()
        for p in (self.pane_peaks, self.pane_group, self.pane_cmpd):
            p.pack_forget()
        if m == MODE_HEATMAP:
            self.pane_peaks.pack(fill="both", expand=True)
            self.right_title.config(text="峰（勾 = 納入共識）")
            self.hint.config(text="點任一檔：載入它的熱圖與峰。"
                                  "點紅圈或表格列可增刪選取。")
            self.right_note.config(text="")
        elif m == MODE_GROUP:
            self.pane_group.pack(fill="both", expand=True)
            self.right_title.config(text="這一組（同一標本）")
            self.hint.config(text="點一個檔：把它的建議同組整組帶進來。"
                                  "再點其他檔＝加入/移出。建議只是建議——"
                                  "實測自動分組 96%，剩下的靠你判斷。")
            self._fill_group_panel()
        else:
            self.pane_cmpd.pack(fill="both", expand=True)
            self.right_title.config(text="共識化合物（票數）")
            self.hint.config(text="檔案面板不變。雙擊右側任一列："
                                  "該位置的所有候選化合物。")
            self._fill_compound_panel()

    # ------------------------------------------------------------- 資料夾
    def pick_folder(self):
        start = os.path.join(os.getcwd(), "GAS")
        d = filedialog.askdirectory(title="選含 .mea 的資料夾",
                                    initialdir=start if os.path.isdir(start) else None)
        if not d:
            return
        samples, excluded = L.select_samples(d)
        if not samples:
            messagebox.showwarning(
                "沒有樣品",
                "%s\n\n這個資料夾裡沒有可用的 .mea。\n"
                "注意 GAS/ 本身只有子資料夾，請選它底下的批次資料夾。" % d)
            return
        self.folder, self.files, self.group = d, samples, set()
        self.corr, self.corr_files, self.consolidated = None, [], []
        self.current = None
        self._refresh_files()
        note = "、".join("%s=%s" % (os.path.basename(e["file"]), e["reason"])
                        for e in excluded)
        cost = L.scan_cost(samples)
        self.status.config(
            text="%d 個樣品%s　待找峰 %d 檔 ≈ %.0f 分"
                 % (len(samples), ("（排除 %s）" % note) if note else "",
                    cost["n_need_detect"], cost["est_seconds"] / 60.0))
        self.btn_scan.config(state="normal")
        # **RI 校正要先解出來**：背景圖的 y 軸是 RI（`_bg.json` 的 y_axis=="ri"），
        # 而 `detect_one()` 只找峰、不掛 RI。沒有 RI 的峰畫不出圈——實測 28 顆峰
        # 全部 ri=None，畫面上一個圈都沒有卻毫無錯誤訊息。解校正可能要跑 STD 的
        # 偵測，所以放背景。
        self.ri_cal = None
        threading.Thread(target=self._calib_worker, args=(d,), daemon=True).start()

    def _calib_worker(self, folder):
        try:
            cal = calibration.resolve_calibrations_cached(
                folder, series_key=RI_SERIES, k0_series_key=RI_SERIES,
                use_sidecar=False) or {}
            ri_cal, ri_mode = (cal.get("ri") or (None, "unavailable", None))[:2]
            self.q.put(("calibration", (ri_cal, ri_mode)))
        except BaseException as exc:
            self.q.put(("status", "RI 校正解不出來：%s: %s"
                        % (type(exc).__name__, exc)))

    def _refresh_files(self):
        # 相似度是「相對於目前點選的那個檔」，標題要講出來，否則一整欄數字沒有基準
        self.tree_files.heading(
            "r", text=("相似度" if not self.current
                       else "相似度 vs %s" % os.path.basename(self.current)[7:-4]))
        self.tree_files.delete(*self.tree_files.get_children())
        for f in self.files:
            ing = f in self.group
            # 空白欄位不解釋自己。相似度要先掃描（每個檔都得先找峰）才算得出來，
            # 什麼都不顯示會讓人以為這一欄壞了。
            if self.corr is None:
                r = "按「2. 掃描」後才有"
            elif self.current is None:
                r = "點一個檔比對"
            elif self.current not in self.corr_files:
                r = "本檔不在掃描範圍"
            elif f not in self.corr_files:
                # 只掃描了一組時，組外的檔沒有數字可比——講清楚是「沒掃到」，
                # 不是「不相似」。
                r = "不在掃描範圍"
            else:
                i = self.corr_files.index(self.current)
                j = self.corr_files.index(f)
                r = "—（本檔）" if i == j else "%+.2f" % self.corr[i, j]
            self.tree_files.insert("", "end", iid=f, text=os.path.basename(f),
                                   values=(CHECK_ON if ing else "", r),
                                   tags=("ingroup",) if ing else ())
        self.btn_cons.config(state="normal" if len(self.group) >= 2 else "disabled")

    # --------------------------------------------------------- 點檔案
    def on_files_click(self, event):
        """點「組」欄＝加入/移出這一組；點其他欄＝照目前模式處理。

        回 "break" 才不會同時觸發選取事件（模式 1 會白跑一次載入熱圖）。
        """
        if self.tree_files.identify_region(event.x, event.y) != "cell":
            return None
        if self.tree_files.identify_column(event.x) != "#1":     # #1 = 組欄
            return None
        row = self.tree_files.identify_row(event.y)
        if not row:
            return None
        if row in self.group:
            self.group.discard(row)
        else:
            self.group.add(row)
        self._refresh_files()
        self._fill_group_panel()
        self.status.config(text="這一組 %d 個檔%s"
                                % (len(self.group),
                                   "（至少要 2 個）" if len(self.group) < 2 else ""))
        return "break"

    def on_file_click(self, _e=None):
        sel = self.tree_files.selection()
        if not sel:
            return
        path = sel[0]
        m = self.mode.get()
        if m == MODE_HEATMAP:
            self.current = path
            self._load_file(path)
        elif m == MODE_GROUP:
            self._group_click(path)
        else:
            self.current = path            # 檔案面板不變，右側維持化合物清單

    def _group_click(self, path):
        """模式 2：第一次點＝帶入建議同組；之後每點一個＝加入/移出。"""
        if not self.group:
            self.current = path
            self.group = set(self._suggest_group(path))
            n = len(self.group)
            msg = ("依相似度帶入 %d 個檔" % n) if n > 1 else \
                  "還沒算過相似度（先按「2. 掃描」），目前只帶入這一個檔"
        else:
            if path in self.group:
                self.group.discard(path)
                msg = "移出 %s" % os.path.basename(path)
            else:
                self.group.add(path)
                msg = "加入 %s" % os.path.basename(path)
        self._refresh_files()
        self._fill_group_panel()
        # 組員可能是剛加進來、還沒掃描過的檔。**先講清楚代價**，不要等到按
        # Consolidate 才卡住 55 秒/檔而且沒有任何徵兆。
        cost = (L.scan_cost(sorted(self.group)) if self.group
                else {"n_need_detect": 0, "est_seconds": 0})
        pend = ("　⚠ 其中 %d 個尚未找峰，彙整時會現場跑（約 %.0f 分）"
                % (cost["n_need_detect"], cost["est_seconds"] / 60.0)
                ) if cost["n_need_detect"] else ""
        self.status.config(
            text="%s　這一組 %d 個檔%s%s"
                 % (msg, len(self.group),
                    "（至少要 2 個）" if len(self.group) < 2 else "", pend))

    def _suggest_group(self, path):
        """相似度夠高的檔＝建議同組。沒算過相似度就只回它自己，不猜。"""
        if self.corr is None or path not in self.corr_files:
            return [path]
        i = self.corr_files.index(path)
        return [f for j, f in enumerate(self.corr_files)
                if j == i or self.corr[i, j] >= SUGGEST_R]

    def _fill_group_panel(self):
        self.tree_group.delete(*self.tree_group.get_children())
        for f in sorted(self.group):
            r = ""
            if (self.corr is not None and self.current in self.corr_files
                    and f in self.corr_files):
                i = self.corr_files.index(self.current)
                j = self.corr_files.index(f)
                r = "—" if i == j else "%+.2f" % self.corr[i, j]
            scanned = os.path.exists(areas2._peaks2_path(f))
            self.tree_group.insert("", "end", text=os.path.basename(f),
                                   values=(r, "已找峰" if scanned else "待找峰"))
        self.right_note.config(
            text="票數只有在同一標本的重複測量之間才有意義。不同標本混在一起時，"
                 "真實化合物會因為只出現在其中幾個檔而被判為未達門檻。")

    # --------------------------------------------------------------- 單檔
    def _load_file(self, path):
        """沒有 `.npz` / 找峰結果就**當場生**，但放到背景做。

        直接在主執行緒跑會讓視窗凍住約 68 秒（讀檔 13 + 找峰 55），使用者看到的是
        「按了沒反應」——第二支應用正是這樣被回報過。所以：立刻在狀態列說明要做什麼、
        要多久，然後開背景執行緒，做完再畫。
        """
        need_npz = not os.path.exists(areas2._npz_path(path))
        need_pk = not L.peaks_are_current(path, self.rules_config,
                                          use_baseline=False, trust_existing=True)
        if need_npz or need_pk:
            if self.busy:
                self.status.config(
                    text="正在%s，請等它跑完再點檔案。" % self.busy)
                return
            est = (13 if need_npz else 0) + (55 if need_pk else 0)
            self.busy = "準備 %s" % os.path.basename(path)
            self.status.config(
                text="%s 還沒處理過，現在產生%s%s（約 %d 秒）…"
                     % (os.path.basename(path),
                        " .npz" if need_npz else "",
                        " 找峰" if need_pk else "", est))
            self.canvas.delete("all")
            self.tree_peaks.delete(*self.tree_peaks.get_children())
            threading.Thread(target=self._prepare_worker, args=(path,),
                             daemon=True).start()
            return
        self._show_loaded(path)

    def _prepare_worker(self, path):
        # 背景執行緒不可以無聲死掉——UI 會永遠等下去（第二支應用踩過）
        try:
            areas2.ensure_npz(path, verbose=False)
            L.detect_cached(path, self.rules_config, use_baseline=False,
                            verbose=False)
            self.q.put(("prepared", path))
        except BaseException as exc:
            self.q.put(("error", "%s: %s" % (type(exc).__name__, exc)))
        finally:
            self.q.put(("busy_clear", None))

    def _show_loaded(self, path):
        pk, _stats, _meta = areas2.detect_one(path, self.rules_config,
                                              use_baseline=False, verbose=False)
        self._floor = _stats.get("floor")
        self._rip_index = _stats.get("rip_index")
        if self.ri_cal:
            calibration.attach_ri(pk, self.ri_cal)   # 沒有 RI 就畫不出圈
        self.peaks = L.apply_effective(state_mod.load(path, pk))
        self._render(path)
        self._fill_peak_table()
        self._write_funnel(None)
        n_on = sum(1 for p in self.peaks if L.effective_active(p))
        self.status.config(text="%s：%d 個峰，已選 %d"
                                % (os.path.basename(path), len(self.peaks), n_on))

    def _render(self, path):
        base = os.path.splitext(os.path.basename(path))[0]
        png = os.path.join(areas2.RESULTS_DIR, base + "_bg.png")
        js = os.path.join(areas2.RESULTS_DIR, base + "_bg.json")
        if not (os.path.exists(png) and os.path.exists(js)):
            # 第一支應用的背景圖不存在就自己畫一張，**用不同檔名**（隔離規則 2：
            # 不寫第一支的產物）。函式呼叫，不是 subprocess。
            inten, dms, rts, _meta = peaks_mod.load_surface(areas2._npz_path(path))
            ripi, _ = rip_mod.find_rip(inten)
            out = os.path.join(areas2.RESULTS_DIR, base + "_c3")
            peaks_mod.write_bg(inten, dms, rts, out, rip_index=ripi)
            png, js = out + "_bg.png", out + "_bg.json"
        with open(js, encoding="utf-8") as f:
            self.geom = json.load(f)
        self.img_orig = Image.open(png)
        self.zoom, self.pan_x, self.pan_y = 1.0, 0.0, 0.0
        self._fit()
        self._render_canvas()

    # ------------------------------------------------------- 縮放與平移
    def _fit(self):
        """算出「整張塞進畫布」的比例，並置中。

        置中很重要：保持長寬比一定會多出空白，不置中的話空白全部堆在下方，
        看起來像圖被切掉了。
        """
        if self.img_orig is None:
            return
        cw = max(self.canvas.winfo_width(), 50)
        ch = max(self.canvas.winfo_height(), 50)
        self.fit_scale = min(cw / self.img_orig.width, ch / self.img_orig.height)
        eff = self.fit_scale * self.zoom
        self.pan_x = (cw - self.img_orig.width * eff) / 2.0
        self.pan_y = (ch - self.img_orig.height * eff) / 2.0

    def _render_canvas(self):
        if self.img_orig is None:
            return
        eff = self.fit_scale * self.zoom
        w = max(1, int(self.img_orig.width * eff))
        h = max(1, int(self.img_orig.height * eff))
        # 放大時用 NEAREST：LANCZOS 在大倍率下每次重繪都要重採樣幾百萬像素，
        # 滾輪會變得很鈍。縮小時畫質才重要，用 LANCZOS。
        resample = Image.LANCZOS if eff < 1.0 else Image.NEAREST
        self.photo = ImageTk.PhotoImage(self.img_orig.resize((w, h), resample))
        self.canvas.delete("all")
        self.highlight_id = None
        self.canvas.create_image(self.pan_x, self.pan_y, anchor="nw",
                                 image=self.photo)
        self._draw_circles()
        self._reapply_highlight()

    def on_canvas_double(self, event):
        """雙擊**空白處**回到全圖；雙擊峰上則不動（那是要切換它）。"""
        if self._peak_at(event.x, event.y) is not None:
            return
        self.zoom = 1.0
        self._fit()
        self._render_canvas()
        self.status.config(text="回到全圖（100%）")

    def on_canvas_resize(self, _event=None):
        """畫布大小變了就重新配合——沒有這個，拉動分隔線之後圖不會跟著調整。"""
        if self.img_orig is None:
            return
        if abs(self.zoom - 1.0) < 1e-9:      # 還沒手動縮放過就重新 fit
            self._fit()
        self._render_canvas()

    def on_wheel(self, event, delta=None):
        """滾輪縮放，並讓游標下的那個點不動（Google Maps 那種）。"""
        if self.img_orig is None:
            return
        d = delta if delta is not None else event.delta
        step = 0.1 if abs(d) < 240 else 0.2
        z_new = self.zoom + step if d > 0 else self.zoom - step
        z_new = max(0.2, min(z_new, 12.0))
        if abs(z_new - self.zoom) < 1e-9:
            return
        eff_old = self.fit_scale * self.zoom
        eff_new = self.fit_scale * z_new
        ix = (event.x - self.pan_x) / eff_old      # 游標對應的原圖像素
        iy = (event.y - self.pan_y) / eff_old
        self.pan_x = event.x - ix * eff_new
        self.pan_y = event.y - iy * eff_new
        self.zoom = z_new
        self._render_canvas()
        self.status.config(text="縮放 %.0f%%（滾輪縮放、拖曳平移、雙擊空白處回到全圖）"
                                % (self.zoom * 100))

    def on_press(self, event):
        self._pan_last = (event.x, event.y)
        self._press = (event.x, event.y)

    def on_drag(self, event):
        if self._pan_last is None:
            return
        dx, dy = event.x - self._pan_last[0], event.y - self._pan_last[1]
        self.pan_x += dx
        self.pan_y += dy
        self._pan_last = (event.x, event.y)
        self._render_canvas()

    def on_release(self, event):
        """放開時判斷這是「點」還是「拖」。

        不分辨的話，每次拖曳平移結束都會誤觸到底下的峰、把它切掉——而使用者根本
        沒有要點它。
        """
        self._pan_last = None
        if self._press is None:
            return
        moved = abs(event.x - self._press[0]) + abs(event.y - self._press[1])
        self._press = None
        if moved > 4:
            return                     # 這是拖曳，不是點選
        i = self._peak_at(event.x, event.y)
        if i is not None:
            self._toggle_index(i)

    def _peak_at(self, cx, cy, radius=10):
        """畫布座標最近的那顆峰（在半徑內）。"""
        best, bestd = None, radius ** 2
        for i, p in enumerate(self.peaks):
            xy = self._canvas_xy(p)
            if xy is None:
                continue
            d = (xy[0] - cx) ** 2 + (xy[1] - cy) ** 2
            if d <= bestd:
                best, bestd = i, d
        return best

    def _xy(self, peak):
        """峰的資料座標 → 背景圖裡的像素座標。

        `axes_bbox` 是 [x0, y0, 寬, 高] 的**圖形比例**，原點在左下；Canvas 的 y 向下。
        直接假設資料區佔滿整張 PNG 會讓每個圈都偏掉——第一支應用踩過這個坑
        （matplotlib 光左邊就留了 8.5% 邊界）。
        """
        g = self.geom
        if not g:
            return None
        dr = peak.get("drift_relative")
        yval = peak.get("ri") if g.get("y_axis") == "ri" else peak.get("retention_s")
        if dr is None or yval is None:
            return None
        pw, ph = g["png_size"]
        x0, y0, bw, bh = g["axes_bbox"]
        xmin, xmax = g["xlim"]
        ymin, ymax = g["ylim"]
        if xmax == xmin or ymax == ymin:
            return None
        fx = x0 + (dr - xmin) / (xmax - xmin) * bw
        fy = y0 + (yval - ymin) / (ymax - ymin) * bh
        return fx * pw, (1.0 - fy) * ph      # 原圖像素，未套用縮放/平移

    def _canvas_xy(self, peak):
        """原圖像素 → 目前畫布座標（套用縮放與平移）。"""
        xy = self._xy(peak)
        if xy is None:
            return None
        eff = self.fit_scale * self.zoom
        return self.pan_x + xy[0] * eff, self.pan_y + xy[1] * eff

    def _draw_circles(self):
        for cid, tid in self.circles.values():
            self.canvas.delete(cid)
            self.canvas.delete(tid)
        self.circles.clear()
        r = 7
        for i, p in enumerate(self.peaks):
            xy = self._canvas_xy(p)
            if xy is None:
                continue
            x, y = xy
            # **用 effective_active，不是 active**：規則否決的峰要跟著變灰，
            # 否則表格標成琥珀色、圈卻還是紅的，兩邊講不同的話。
            on = L.effective_active(p)
            col = "#ff3b30" if on else "#888888"
            dash = (3, 2) if L.is_rule_override(p) else None
            cid = self.canvas.create_oval(x - r, y - r, x + r, y + r,
                                          outline=col, width=2,
                                          **({"dash": dash} if dash else {}))
            tid = self.canvas.create_text(x + r + 4, y - r - 2, text=str(i + 1),
                                          fill=col, anchor="w",
                                          font=("Segoe UI", 8, "bold"))
            # 不綁 tag_bind：點擊統一由 on_release 判斷，否則「拖曳平移」放開時
            # 會誤觸底下的峰。
            self.circles[i] = (cid, tid)

    def _fill_peak_table(self):
        self.tree_peaks.delete(*self.tree_peaks.get_children())
        for i, p in enumerate(self.peaks):
            on = L.effective_active(p)
            by_rule = not p.get("rule_active", True)
            self.tree_peaks.insert(
                "", "end", iid=str(i),
                values=(i + 1,
                        CHECK_ON if on else CHECK_OFF,
                        self._fmt(p.get("drift_relative"), 3),
                        self._fmt(p.get("ri"), 1),
                        self._fmt(p.get("intensity"), 0)),
                tags=(("off",) if not on else ()) + (("byrule",) if by_rule else ()))

    @staticmethod
    def _fmt(v, nd):
        return "—" if v is None else ("%.*f" % (nd, v))

    def _toggle_index(self, i):
        p = self.peaks[i]
        # 寫 `user_active`：使用者的判定覆蓋規則的判定，而且兩者分得開——
        # 規則之後再改，使用者這一次的決定仍然算數。
        p["user_active"] = not L.effective_active(p)
        L.apply_effective(self.peaks)
        state_mod.save(self.current, self.peaks)
        self.highlighted = i
        self._render_canvas()          # 保留目前的縮放與平移
        self._fill_peak_table()
        self._write_funnel(None)       # 手動增刪也要反映在漏斗上
        self.tree_peaks.selection_set(str(i))
        self.tree_peaks.see(str(i))
        n_on = sum(1 for q in self.peaks if L.effective_active(q))
        self.status.config(text="%s：%d 個峰，已選 %d"
                                % (os.path.basename(self.current), len(self.peaks),
                                   n_on))

    def on_peaks_click(self, event):
        """點 `On` 欄＝切換；點其他欄＝只是選取（畫黃環）。

        回 "break" 讓 Treeview 不要再改選取狀態，否則切換的同時又觸發一次選取事件。
        """
        if self.tree_peaks.identify_region(event.x, event.y) != "cell":
            return None
        if self.tree_peaks.identify_column(event.x) != "#2":      # #2 = On 欄
            return None
        row = self.tree_peaks.identify_row(event.y)
        if row == "":
            return None
        self._toggle_index(int(row))
        return "break"

    def toggle_peak(self, _e=None):
        sel = self.tree_peaks.focus()
        if sel != "":
            self._toggle_index(int(sel))
        return "break"

    def on_peak_select(self, _e=None):
        sel = self.tree_peaks.focus()
        if sel == "":
            return
        self._highlight(int(sel))

    def _highlight(self, index):
        """在選取的峰上畫**黃色**環，與第一支應用一致。

        原本是把紅圈加粗——紅色加粗在一堆紅圈裡幾乎看不出來，而且被選到的峰若是
        灰的（未勾選）就完全沒有回饋。黃環是獨立物件，疊在最上層。
        """
        if self.highlight_id is not None:
            try:
                self.canvas.delete(self.highlight_id)
            except Exception:
                pass
            self.highlight_id = None
        self.highlighted = index
        if index is None or index >= len(self.peaks):
            return
        xy = self._canvas_xy(self.peaks[index])
        if xy is None:
            return
        x, y = xy
        rr = 12
        self.highlight_id = self.canvas.create_oval(
            x - rr, y - rr, x + rr, y + rr, outline="yellow", width=3, fill="")
        self.canvas.tag_raise(self.highlight_id)

    def _reapply_highlight(self):
        """重繪之後把黃環放回去——縮放/平移後它必須還黏在同一顆峰上。"""
        idx = getattr(self, "highlighted", None)
        if idx is not None:
            self._highlight(idx)

    # -------------------------------------------------------------- 掃描
    def scan_group(self):
        """掃描**整個資料夾**，不是只掃已勾選的那一組。

        相似度是拿來「幫你決定哪些檔同組」的，所以必須涵蓋所有候選檔——只掃已經
        選好的那一組是循環論證：你得先知道答案才掃得到答案。實測回報過：勾了一個檔
        再按掃描，只有那一個被處理，相似度整欄變成「不在掃描範圍」。
        """
        targets = list(self.files)
        if len(targets) < 3:
            messagebox.showwarning(
                "檔案太少",
                "這個資料夾只有 %d 個樣品，算不出有意義的相似度。\n\n"
                "每個區域要跨檔標準化，而檔案只有兩個時每一欄都會被壓成 (+1, −1)，"
                "相關係數恆為 −1，與資料完全無關；至少要 3 個檔。\n\n"
                "還是可以用模式 1 逐檔看熱圖、選峰。" % len(targets))
            return
        self.busy = "掃描（找峰）"
        self.btn_scan.config(state="disabled")
        threading.Thread(target=self._scan_worker, args=(targets,),
                         daemon=True).start()

    def _scan_worker(self, targets):
        # `except BaseException`：`SystemExit` 之類不是 Exception，只攔 Exception 的話
        # 背景執行緒會無聲死掉、UI 永遠等不到訊息（第二支應用踩過這個坑）。
        try:
            for i, m in enumerate(targets, 1):
                self.q.put(("status", "找峰 %d/%d：%s"
                            % (i, len(targets), os.path.basename(m))))
                areas2.detect_one(m, self.rules_config, use_baseline=False,
                                  verbose=False)
            # **相似度也要在這條背景執行緒算完。**
            # 原本是丟回主執行緒算，而它要跑 consensus_regions + 逐檔量測——
            # 熱門情況下 13 秒、冷的要好幾分鐘，整個視窗會凍住變成「沒有回應」。
            if len(targets) < 3:
                self.q.put(("status",
                            "掃描完成（%d 檔）。相似度至少要 3 個檔才有意義。"
                            % len(targets)))
                return
            self.q.put(("status", "掃描完成（%d 檔），計算相似度中…" % len(targets)))
            areas, _per_file, _rep = L.consensus_regions(
                targets, self.rules_config, active_only=False, verbose=False)
            self.q.put(("status", "量測 %d 個共識區域…" % len(areas)))
            profs = []
            for k, m in enumerate(targets, 1):
                profs.append(L.measure_profile(m, areas))
                self.q.put(("status", "量測 %d/%d：%s"
                            % (k, len(targets), os.path.basename(m))))
            corr, n_used = L.similarity_matrix(profs)
            self.q.put(("corr", (corr, list(targets), len(areas), n_used)))
        except BaseException as exc:
            self.q.put(("error", "%s: %s" % (type(exc).__name__, exc)))
        finally:
            self.q.put(("enable_scan", None))

    # --------------------------------------------------------- Consolidate
    def consolidate(self):
        grp = sorted(self.group)
        if len(grp) < 2:
            messagebox.showwarning("這一組太小", "至少要選 2 個檔才談得上共識。")
            return
        # **同標本才有意義。** 組內相似度太低就先問清楚，不要靜靜產出一張很薄的清單。
        if self.corr is not None and all(g in self.corr_files for g in grp):
            idx = [self.corr_files.index(g) for g in grp]
            rs = [self.corr[i, j] for a, i in enumerate(idx) for j in idx[a + 1:]]
            if rs and min(rs) < 0.5:
                if not messagebox.askyesno(
                        "這些看起來不像同一個標本",
                        "組內最低相似度只有 %+.2f。\n\n"
                        "票數只有在同一標本的重複測量之間才有意義——不同標本混在一起，"
                        "真實化合物會因為只出現在其中幾個檔而被判為未達門檻。\n\n"
                        "還是要繼續嗎？" % min(rs)):
                    return
        self.status.config(text="彙整中…")
        self.busy = "彙整"
        threading.Thread(target=self._cons_worker, args=(grp,), daemon=True).start()

    def _cons_worker(self, grp):
        try:
            parts = self.frac.get().split("/")
            frac = float(parts[0]) / float(parts[1])
            # `series_key` 不能省：少了它 RI 會退回 `single_point_relative`（ri=None），
            # 下游比對就**無聲**改用保留時間，而保留時間不跨儀器/管柱/方法轉移。
            # `use_sidecar=False`：不寫任何東西進 GAS/（隔離規則 3）。
            header = calibration._read_header_lite(grp[0])
            cal = calibration.resolve_calibrations_cached(
                os.path.dirname(grp[0]), series_key=RI_SERIES,
                k0_series_key=RI_SERIES, use_sidecar=False) or {}
            # `cal["ri"]` 是 3-tuple `(校正, 模式, 細節)`，不是 dict。
            ri_cal, ri_mode = (cal.get("ri") or (None, "unavailable", None))[:2]
            data_dir = library.resolve_data_dir()      # 不能是 None，會 TypeError
            if not data_dir:
                self.q.put(("error", "找不到 library_data/，無法比對化合物。"))
                return
            ril, iml, _info = identify.load_libraries(
                data_dir, header, ri_calibration=ri_cal)

            def _prog(done, total, path):
                self.q.put(("status", "彙整前找峰 %d/%d：%s"
                            % (done, total, os.path.basename(path))))

            areas, per_file, _rep = L.consensus_regions(
                grp, self.rules_config, min_fraction=frac, active_only=True,
                ri_calibration=ri_cal, progress=_prog, verbose=False)
            for m in grp:                       # 套用使用者的勾選
                state_mod.load(m, per_file[m])
            areas, per_file, _rep = L.consensus_regions(
                grp, self.rules_config, min_fraction=frac, active_only=True,
                ri_calibration=ri_cal, verbose=False)
            ranked = L.rank_areas(areas, total_files=len(grp), min_fraction=frac)

            out = []
            for a in ranked:
                c = L.consolidate_area(a, per_file, ril, iml)
                c.update({"votes": a["votes"], "votes_total": a["votes_total"],
                          "vote_tier": a["vote_tier"], "below": a["below_threshold"],
                          "rt": a["rt_center_s"], "dr": a["drift_center"],
                          "ri": a.get("ri_center")})
                out.append(c)
            self.q.put(("consolidated", {
                "rows": out, "ri_mode": ri_mode,
                "assumed": bool((ri_cal or {}).get("assumed_unverified"))}))
        except BaseException as exc:
            self.q.put(("error", "%s: %s" % (type(exc).__name__, exc)))

    def _show_consolidated(self, rows, ri_mode="?", assumed=False):
        self.consolidated = rows
        # provenance 跟著走：3/3 支持的錯答案看起來會比 1/3 更有說服力，所以 RI 是
        # 哪一層來的、是否未經驗證，必須一直看得見。
        self.ri_note = "ri_mode=%s%s" % (
            ri_mode, "　⚠ assumed_unverified" if assumed else "")
        n_pass = sum(1 for r in rows if not r["below"])
        self.status.config(text="共識區域 %d 個，達門檻 %d 個　%s"
                                % (len(rows), n_pass, self.ri_note))
        self.mode.set(MODE_COMPOUND)       # 算完直接切到模式 3
        self.on_mode_change()

    def _fill_compound_panel(self):
        self.tree_cmpd.delete(*self.tree_cmpd.get_children())
        if not self.consolidated:
            self.right_note.config(
                text="還沒有結果 —— 用模式 2 選好這一組之後按「3. Consolidate」。")
            return
        for i, r in enumerate(self.consolidated):
            top = r["candidates"][0] if r["candidates"] else None
            name = (top["name"] or "?") if top else "（無候選）"
            self.tree_cmpd.insert(
                "", "end", iid=str(i), text=name,
                values=("%d/%d" % (r["votes"], r["votes_total"]),
                        # gc_only = IMS 那一軸沒對上，只用 RI；候選會多一個數量級，
                        # 證據強度完全不同，必須看得出來。
                        "2D" if r.get("match_dimension") == "combined" else "RI",
                        len(r["candidates"]), "%.1f" % r["rt"]),
                tags=("t%d" % r["vote_tier"],))
        self.right_note.config(
            text="底色＝票數佔比；灰＝未達門檻（保留顯示不刪除）。維度 2D＝GC 與 IMS "
                 "兩軸都同意，RI＝只有 RI 對上（候選通常多一個數量級，證據弱得多）。"
                 + ("　" + self.ri_note if self.ri_note else ""))

    def show_candidates(self, _e=None):
        sel = self.tree_cmpd.focus()
        if sel == "":
            return
        r = self.consolidated[int(sel)]
        w = tk.Toplevel(self.root)
        w.title("候選化合物 — RT %.1fs / drift %.3f" % (r["rt"], r["dr"]))
        w.geometry("860x540")
        ttk.Label(w, font=("Segoe UI", 10, "bold"),
                  text="票數 %d/%d　有峰的檔 %d　RI 重複變異 %s"
                       % (r["votes"], r["votes_total"], r["n_files_with_peak"],
                          self._fmt(r["ri_spread"], 2))
                  ).pack(anchor="w", padx=8, pady=6)
        if r["files_without_peak"]:
            ttk.Label(w, foreground="#c62828",
                      text="這些檔在這個位置沒有選取的峰：" + "、".join(
                          os.path.basename(x) for x in r["files_without_peak"])
                      ).pack(anchor="w", padx=8)
        t = ttk.Treeview(w, columns=("s", "name", "cas", "ri", "d"), show="headings")
        for col, txt, wid in (("s", "支持", 70), ("name", "化合物", 320),
                              ("cas", "CAS", 110), ("ri", "庫 RI", 80),
                              ("d", "|ΔRI|", 80)):
            t.heading(col, text=txt)
            t.column(col, width=wid, anchor="w" if col == "name" else "center")
        t.pack(fill="both", expand=True, padx=8, pady=6)
        for c in r["candidates"]:
            t.insert("", "end", values=(
                "%d/%d" % (c["n_support"], c["n_files_with_peak"]),
                c["name"] or "—", c["cas"],
                c["library_ri"] if c["library_ri"] is not None else "—",
                self._fmt(c["mean_abs_delta_ri"], 2)))
        ttk.Label(w, foreground="#777", wraplength=820, justify="left",
                  text=("支持 = 有幾個重複的候選清單裡出現這個化合物。"
                        "彙整提高的是可靠度，不是真值——RI 尺標的既有疑慮"
                        "（status.md open decision 3a）照樣成立，三個重複會一致地"
                        "指向同一個答案，對錯都一樣一致。")
                  ).pack(anchor="w", padx=8, pady=4)

    # ------------------------------------------------------------- 佇列
    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "status":
                    self.status.config(text=payload)
                elif kind == "error":
                    self.busy = None
                    self.status.config(text=payload)
                    messagebox.showerror("出錯了", payload)
                elif kind == "enable_scan":
                    self.busy = None
                    self.btn_scan.config(state="normal")
                elif kind == "busy_clear":
                    self.busy = None
                elif kind == "calibration":
                    self.ri_cal, mode = payload
                    n = (self.ri_cal or {}).get("n_anchors")
                    self.status.config(text="RI 校正：%s%s" % (
                        mode, ("，%d 個錨點" % n) if n else ""))
                    if self.current and self.mode.get() == MODE_HEATMAP:
                        self._load_file(self.current)   # 重畫，這次圈才出得來
                elif kind == "prepared":
                    # 使用者可能在等待期間又點了別的檔——只畫他現在看的那一個，
                    # 否則畫面會跳回舊檔而且看不出為什麼。
                    if payload == self.current:
                        self._show_loaded(payload)
                    else:
                        self.status.config(
                            text="%s 已備妥（你已切到別的檔）"
                                 % os.path.basename(payload))
                    self._fill_group_panel()
                elif kind == "corr":
                    corr, files, n_areas, n_used = payload
                    self.corr, self.corr_files = corr, files
                    self._refresh_files()
                    self.status.config(
                        text="相似度已算出（%d 個共識區域，用了 %d 維）。"
                             "切到模式 2 點一個檔，建議同組就會整組帶進來。"
                             % (n_areas, n_used))
                elif kind == "consolidated":
                    self.busy = None
                    self._show_consolidated(payload["rows"], payload["ri_mode"],
                                            payload["assumed"])
        except queue.Empty:
            pass
        self.root.after(100, self._drain)


def main():
    root = tk.Tk()
    ConsensusApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

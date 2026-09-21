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

Version: 1.1 — by Albert Sheng（第三支應用，2026-09-07）
"""
import copy
import json
import os
import queue
import re
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
from . import rules_store
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
SUGGEST_R = L.GROUP_GOOD_R

#: 基準欄的單選標記。用 ◉/○ 而不是核取方塊：形狀本身就在說「這是單選」，
#: 而「組 ✓」是複選——兩欄並排時形狀不同才不會被當成同一種東西。
BASE_ON = "◉"
BASE_OFF = "○"

#: 檔案面板的「狀態」。**三態不是兩態**：只看 `.npz` 的話，有 `.npz` 卻還沒找峰的
#: 檔會顯示成就緒，但點下去仍要等 55 秒——「就緒」的意思必須是「點下去就有」。
#: `.npz` 約 13 秒（畫熱圖用），找峰約 55 秒（選峰、分組、彙整都要）。
#:
#: **文字 + 顏色，兩個都給。**
#:
#: 先做成 🟢🟡🔴，實測在 Windows 的 Tk 裡退回**單色字形**，三種狀態長得一模一樣
#: （使用者回報「每個檔看起來都一樣」）。ttk 的 tag 只能整列上色，染不了單一格，
#: 所以那條路也不通。**但 item 的 `image` 可以**——自己畫一顆彩色圓點當圖，放在
#: 檔名前面，那是真的顏色而且不依賴任何字形（見 `_dot()`）。
#:
#: 文字仍然保留：顏色不該是唯一的資訊來源（色覺差異、螢幕擷圖、黑白列印）。
READY_GREEN = "就緒"     # .npz 與找峰都在，點下去立刻有反應
READY_AMBER = "待找峰"   # 只有 .npz，還要找峰（約 55 秒）
READY_RED = "未處理"     # 兩者都沒有（約 68 秒）

#: 狀態 → 圓點顏色
READY_COLOUR = {READY_GREEN: "#2e7d32", READY_AMBER: "#f9a825",
                READY_RED: "#c62828"}

#: 欄位說明放這裡，不放進格子裡。格子裡塞整句話會把欄寬撐開，而且一欄長短不一的
#: 句子讀起來像錯誤訊息——使用者回報過「按 2 掃描才有」這種寫法不專業。
LEGEND = ("基準 ◉　點一列的「基準」欄＝以它為比較對象，相似度夠高的檔會自動被選"
          "進這一組。**一次只有一個基準**，換一個就整組重挑。\n"
          "狀態　檔名前的圓點：綠 = 就緒（點下去立刻有）、黃 = 待找峰"
          "（有 .npz，還要約 55 秒）、紅 = 未處理（約 68 秒）。"
          "點狀態欄可強制重做。\n"
          "相似度　**選了基準就會自動算**（跨檔比對；峰都備好時約十幾秒，"
          "還有檔沒找峰會先問過）。\n"
          "[自訂規則] = 這個檔有自己的規則（其餘沿用預設）")


class ConsensusApp:
    def __init__(self, root):
        self.root = root
        root.title("GC-IMS 化合物共識 — 第三支應用")
        # 寫死 1600x980 會超出小螢幕、視窗一半跑到畫面外。改成照螢幕大小開，
        # 再交給 zoomed 最大化（同第一支應用的作法）。
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        # **開起來就是整個螢幕**。原本留了 60 px 給工作列，結果在工作列自動隱藏或
        # 多螢幕的機器上就是一個沒理由的缺口；下面的 `zoomed` 本來就會讓視窗管理員
        # 自己避開工作列，這裡給滿即可。
        root.geometry("%dx%d+0+0" % (sw, sh))
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
        #: 相似度的比較基準。**一次只有一個**——相似度是相對量，兩個基準就沒有
        #: 一欄數字可以並排比較了。`None` = 還沒選。
        self.base = None
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
        #: **預設**規則，適用於所有沒有自己主張的 `.mea`。逐檔覆寫在
        #: `results/<base>_rules3.json`，由 `rules_store` 管理。
        #: 預設本身放 `results/_rules3_default.json`——不寫共用的
        #: `rules_config.json`，那會順手改掉前兩支應用的行為。
        # **每次啟動都從乾淨的起點開始**（使用者要求）。規則是「這一輪想怎麼看」
        # 的臨時設定，跟同標本分組一樣不跨執行保留。要長期改變預設，用面板上的
        # 「存成共用預設」寫回 rules_config.json。
        rules_store.reset_session()
        self.shared_rules = rules_mod.load_config("rules_config.json")
        self.rules_config, self.rules_src = rules_store.load_default(
            self.shared_rules)
        #: 規則面板正在編輯的那一份，以及它是誰的（`None` = 預設）。
        #: 面板就地改 dict，所以這一定要是**深拷貝**，否則改 A 檔會動到 B 檔。
        self.cur_rules = copy.deepcopy(self.rules_config)
        self.cur_rules_src = "default"
        #: 規則面板的套用範圍。**預設是「全部檔案」**——使用者的要求原話：
        #: 「改規則預設套用到所有 mea，但仍可改單一 mea 的規則」。改成只影響
        #: 目前這個檔的話，調一次參數要逐檔重調 18 次。
        self.rules_all = tk.BooleanVar(value=True)
        self.mode = tk.StringVar(value=MODE_HEATMAP)
        self.busy = None            # None 或「掃描」/「彙整」，給結束確認用
        self._prog_win = None       # 進度視窗（只在有背景工作時存在）
        self._prog_bar = None
        self._prog_lbl = None
        #: 畫面上那份彙整結果是否已過期。**預設 True**：還沒算過就是過期。
        self._cons_dirty = True
        self.ri_cal = None          # 資料夾的 RI 校正；圈的 y 座標與比對都要它
        # --- 逐峰化合物比對（與第一支應用的 GC×IMS / GC / IMS / ▶ 四欄同語意）
        self.ril = None             # `.ril` 列（GC，以 RI 為主鍵）
        self.iml = None             # `.iml` 列（IMS，帶漂移）
        self.lib_info = None
        self.lib_state = "idle"     # idle / loading / ready / unavailable
        #: `(檔名, rt_index, dt_index)` → `match_all()` 的結果。**鍵不含 peak_id**：
        #: 那是突出度排名，規則參數一改就重新編號，快取會黏到別顆峰上去（第一支
        #: 應用踩過，`state.py` 開頭有同一條註記）。
        self.match_cache = {}
        self._match_wins = {}       # 一顆峰只開一個候選視窗，不疊窗
        self.q = queue.Queue()

        self._build()
        self.root.after(100, self._drain)

    # ------------------------------------------------------------------ UI
    def _build(self):
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=6, pady=4)
        # **只留「選資料夾」一顆。**
        # 原本工具列上有「2. 掃描全部」與「3. Consolidate」，而左邊又有三個編號
        # 模式——同一條流程被寫成兩套編號，使用者得自己猜哪個先。現在改成模式
        # 本身就是動作：切到模式 2 就算相似度，切到模式 3 就彙整。
        ttk.Button(bar, text="選資料夾", command=self.pick_folder).pack(side="left")
        ttk.Label(bar, text="門檻").pack(side="left", padx=(16, 2))
        # 預設 1/2：使用者要的是「超過一半的重複都看得到才算數」。要更嚴格
        # （2/3、3/4、全數）就在這裡改，門檻一律以**佔比**計算，不是原始票數。
        self.frac = tk.StringVar(value="1/2")
        cb = ttk.Combobox(bar, textvariable=self.frac, width=6, state="readonly",
                          values=["1/2", "2/3", "3/4", "1/1"])
        cb.pack(side="left")
        # 門檻換了，畫面上那份彙整就過期了——標成 dirty，下次進模式 3 會重算。
        cb.bind("<<ComboboxSelected>>", lambda _e: self._mark_dirty())
        ttk.Button(bar, text="Rules", command=self.open_rules).pack(side="left",
                                                                    padx=(16, 0))
        ttk.Button(bar, text="結束", command=self.quit_app).pack(side="right")
        # 固定寬度 + 不隨內容伸縮：文字長度不可以決定版面寬度
        self.status = ttk.Label(bar, text="請先選一個含 .mea 的資料夾",
                                foreground="#555", width=70, anchor="w")
        self.status.pack(side="left", padx=12, fill="x", expand=True)
        # 視窗右上角的 X 走同一條路，行為才一致
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        self.pane = ttk.PanedWindow(self.root, orient="horizontal")
        pane = self.pane
        pane.pack(fill="both", expand=True, padx=6, pady=4)
        self._build_left(pane)
        self._build_mid(pane)
        self._build_right(pane)
        # 分隔線要等視窗真的有寬度之後才擺得準——建構當下 winfo_width() 還是 1。
        self.root.after_idle(self._layout_panes)

    def _layout_panes(self):
        """開場就把右側面板拉到「所有欄位都看得見」的寬度。

        **權重不夠用。** `pane.add(weight=...)` 只決定**多出來的**空間怎麼分，
        起始寬度看子元件的請求寬度，而三個面板加起來超過視窗時右側就被壓掉，
        最後三欄（GC / IMS / ▶）連標題都看不到，要手動拉分隔線才出得來。
        所以這裡直接指定兩條分隔線的位置。
        """
        pane = getattr(self, "pane", None)
        if pane is None or not pane.winfo_exists():
            return
        total = pane.winfo_width()
        if total <= 1:                      # 還沒佈局好，等下一輪
            self.root.after(50, self._layout_panes)
            return
        left_w = 430                        # 檔名 + 三個窄欄
        right_w = getattr(self, "peaks_min_w", 640)
        # 中間至少留 320 給熱圖；視窗太窄時寧可壓右邊也不要讓熱圖消失
        if total - left_w - right_w < 320:
            right_w = max(260, total - left_w - 320)
        try:
            pane.sashpos(0, left_w)
            pane.sashpos(1, max(left_w + 320, total - right_w))
        except tk.TclError:
            pass                            # 面板數不足或尚未 mapped

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
        # **面板編輯的是誰的規則,一定要寫出來。** 逐檔覆寫之後,兩個檔同一組看起來
        # 一樣的參數卻給出不同的峰數是完全可能的——不說是哪一份,使用者只會覺得
        # 程式壞了。這與 ri_mode / k0_mode 是同一條原則。
        scope = ttk.Frame(outer)
        scope.pack(fill="x", pady=(2, 2))
        ttk.Label(scope, text="套用範圍：").pack(side="left")
        # **預設「全部檔案」**：使用者的要求是改規則預設套到所有 mea。
        # 切成「只有這個檔」才寫逐檔覆寫。
        ttk.Radiobutton(scope, text="全部檔案", value=True,
                        variable=self.rules_all,
                        command=self._on_scope_change).pack(side="left", padx=(0, 10))
        self._rb_one = ttk.Radiobutton(scope, text="只有目前這個檔", value=False,
                                       variable=self.rules_all,
                                       command=self._on_scope_change)
        self._rb_one.pack(side="left")
        self._rules_scope = ttk.Label(outer, foreground="#555", justify="left")
        self._rules_scope.pack(anchor="w", pady=(2, 6))
        body = ttk.Frame(outer)
        body.pack(fill="x", pady=(4, 10))

        saved = {e.get("rule_number"): e for e in self._panel_rules()}
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
        # 這一顆是使用者要的：拿目前這個檔的規則，套到**選取的每一個檔**上。
        self.btn_apply_all = ttk.Button(btns, text="套用到選取的檔",
                                        command=self._apply_rules_to_group)
        self.btn_apply_all.pack(side="left")
        # 沒有這一顆的話，一個檔一旦有了自訂規則就再也回不去預設
        self.btn_reset_rules = ttk.Button(btns, text="本檔改回預設",
                                          command=self._reset_rules_for_current)
        self.btn_reset_rules.pack(side="left", padx=6)
        ttk.Button(btns, text="存成共用預設（三支都改）",
                   command=self._save_rules).pack(side="left", padx=6)
        ttk.Button(btns, text="關閉", command=win.destroy).pack(side="right")
        self._rules_win = win
        self._sync_rules_scope()
        self._on_rules_changed()

    def _panel_rules(self):
        """面板正在編輯的那一份。

        **預設是「全部檔案」**（改預設），只有把範圍切成「只有這個檔」才會變成
        逐檔覆寫。沒載入任何檔時一律是預設。
        """
        if self.rules_all.get() or not self.current:
            return self.rules_config
        return self.cur_rules

    def _on_scope_change(self):
        """切換套用範圍＝換一份編輯對象，輸入框的值要跟著重填。"""
        self._rebuild_rules_panel()

    def _sync_rules_scope(self):
        """把「這是誰的規則」寫在面板上，並依情況啟用/停用按鈕。"""
        lbl = getattr(self, "_rules_scope", None)
        if lbl is None or not lbl.winfo_exists():
            return
        n_grp = len(self.group)
        n_custom = sum(1 for f in self.files if rules_store.has_override(f))
        if self.rules_all.get() or not self.current:
            pinned = ("　⚠ 另有 %d 個檔已自訂，不受影響——要把它們拉回來，"
                      "在那個檔按「本檔改回預設」。" % n_custom) if n_custom else ""
            lbl.config(text="編輯對象：**預設規則**，套用到所有沒有自訂的 .mea。%s"
                            % pinned)
        else:
            lbl.config(
                text="編輯對象：%s %s——改動只影響這一個檔。\n"
                     "要讓整組一致，按「套用到選取的檔」。"
                     % (os.path.basename(self.current),
                        "（已自訂）" if self.cur_rules_src == "custom"
                        else "（目前沿用預設）"))
        rb = getattr(self, "_rb_one", None)
        if rb is not None and rb.winfo_exists():
            # 沒載入檔案就沒有「這個檔」可以選
            rb.config(state="normal" if self.current else "disabled")
        for btn, ok in ((getattr(self, "btn_apply_all", None),
                         bool(self.current) and n_grp >= 1),
                        (getattr(self, "btn_reset_rules", None),
                         bool(self.current)
                         and rules_store.has_override(self.current))):
            if btn is not None and btn.winfo_exists():
                btn.config(state="normal" if ok else "disabled")
        if getattr(self, "btn_apply_all", None) is not None \
                and self.btn_apply_all.winfo_exists():
            self.btn_apply_all.config(text="套用到選取的檔（%d）" % n_grp)

    def _apply_rules_to_group(self):
        """把目前這個檔的規則寫成**選取的每一個檔**的覆寫。

        會改變偵測結果（R004/R006 在突出度門檻之前生效），所以被套到的檔參數指紋
        會對不上、下次載入或掃描時自動重跑——這裡不偷偷跑，先講清楚要花多久。
        """
        if not self.current:
            return
        grp = sorted(self.group)
        if not grp:
            messagebox.showinfo("還沒選檔", "先在左邊勾選要套用的 .mea。")
            return
        others = [m for m in grp if m != self.current]
        if not messagebox.askyesno(
                "套用規則",
                "把 %s 的規則套到選取的 %d 個檔嗎？\n\n"
                "其中 %d 個檔會被覆寫（來源檔本身不算）。\n"
                "規則會改變偵測結果，這些檔下次載入或掃描時會重跑找峰"
                "（每檔約 55 秒）。\n\n原始的 .mea 不會被更動。"
                % (os.path.basename(self.current), len(grp), len(others))):
            return
        rules_store.save_override(self.current, self.cur_rules)
        n = rules_store.apply_to_all(grp, self.cur_rules, skip={self.current})
        self.cur_rules_src = "custom"
        self._sync_rules_scope()
        self._refresh_files()          # 燈號會因為指紋對不上而變黃
        self.status.config(
            text="已把 %s 的規則套到 %d 個檔（另有 %d 個檔的指紋因此失效，"
                 "下次載入會重跑找峰）。"
                 % (os.path.basename(self.current), n + 1, n))

    def _reset_rules_for_current(self):
        """這個檔改回跟著預設走。"""
        if not self.current:
            return
        if not rules_store.clear_override(self.current):
            return
        self._load_rules_for_current()
        self._rebuild_rules_panel()
        self._refresh_files()
        self.status.config(text="%s 已改回預設規則。"
                                % os.path.basename(self.current))

    def _rebuild_rules_panel(self):
        """規則來源換了就把面板重開——輸入框的值是建構時填的，不會自己跟上。"""
        win = getattr(self, "_rules_win", None)
        if win is not None and win.winfo_exists():
            win.destroy()
            self.open_rules()

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
        # **改的是面板的編輯對象**——有載入檔案時是那個檔自己的那一份，不是預設。
        # 直接改 `self.rules_config` 會讓「只想調這個檔」變成偷偷改掉所有檔。
        cfg = self._panel_rules()
        before = peaks_mod.pre_gate_params(cfg)
        # **只在真的改了東西時才寫檔。** `open_rules()` 最後會呼叫本函式一次，
        # 以前那一次就會替目前的檔寫出一份覆寫——於是「打開規則面板看一眼」
        # 等於把那個檔釘成自訂，清單上冒出 ⚙ 而使用者什麼都沒改。實際回報過。
        snapshot = copy.deepcopy(cfg)
        for entry in cfg:
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
        pre_gate_changed = peaks_mod.pre_gate_params(cfg) != before

        # 改動立刻落地。不存的話，關掉面板或切換檔案就沒了，而使用者剛看到峰的
        # 標記變了，會以為已經生效。
        if not bad and cfg != snapshot:
            if self.rules_all.get() or not self.current:
                rules_store.save_default(cfg)      # 全部檔案（不碰 rules_config.json）
                self.rules_src = "app3"
                # 目前這個檔若沒有自訂，它跟著預設走——面板那一份也要同步
                if self.current and self.cur_rules_src != "custom":
                    self.cur_rules = copy.deepcopy(cfg)
            else:
                rules_store.save_override(self.current, cfg)
                self.cur_rules_src = "custom"
            self._sync_rules_scope()

        report = None
        if self.peaks:
            report = rules_mod.mark_rules(
                self.peaks, cfg,
                context={"floor": self._floor, "rip_index": self._rip_index})
            L.apply_effective(self.peaks)      # 規則變了，active 要跟著更新
            self._render_canvas()
            self._fill_peak_table()
        self._write_funnel(report, bad=bad, pre_gate_changed=pre_gate_changed)
        if pre_gate_changed:
            self._rules_dirty = True

    def _save_rules(self):
        """把面板上這一份寫回**三支共用的** `rules_config.json`。

        平時不需要按：改「全部檔案」的規則已經存進本應用自己的預設
        （`results/_rules3_default.json`），關掉再開仍然在。這一顆是**明確地**
        讓 `main.py` 與 `main2.py` 也一起改——所以要先問。

        `rules.save_config()` 會強制 R004/R006 保持啟用——強制規則不能靠手改設定檔
        繞過（第一支應用的規定，這裡沿用同一個函式，不自己寫檔）。
        """
        cfg = self._panel_rules()
        if not messagebox.askyesno(
                "存成共用預設",
                "要把這一份規則寫回 rules_config.json 嗎？\n\n"
                "那是**三支應用共用**的設定檔——main.py 與 main2.py 下次執行時\n"
                "也會用這一份。\n\n"
                "只想影響第三支的話不必按這顆：改動已經存在本應用自己的預設裡了。"):
            return
        try:
            rules_mod.save_config("rules_config.json", cfg)
        except Exception as exc:
            messagebox.showerror("存檔失敗", "%s: %s" % (type(exc).__name__, exc))
            return
        self.shared_rules = copy.deepcopy(cfg)
        n_custom = sum(1 for f in self.files if rules_store.has_override(f))
        self.status.config(
            text="已寫回 rules_config.json（三支共用）。%s"
                 % ("有 %d 個檔另有自訂規則，不受影響。" % n_custom if n_custom
                    else "所有檔都沿用預設。"))
        self._refresh_files()

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
        # 模式**就是**流程的三個步驟——沒有另一組編號按鈕。切過去就開始做那件事。
        self._mode_rb = {}
        for val, txt in ((MODE_HEATMAP, "1. 看熱圖、選峰"),
                         (MODE_GROUP, "2. 選擇相似 mea"),
                         (MODE_COMPOUND, "3. 看共識化合物")):
            rb = ttk.Radiobutton(box, text=txt, value=val, variable=self.mode,
                                 command=self.on_mode_change)
            rb.pack(anchor="w", padx=6)
            self._mode_rb[val] = rb

        # **基準欄要在最前面。** 相似度是「相對於某個檔」的量，沒有基準就沒有意義。
        # 原本的基準是**隱含**的（最後點過的那個檔），使用者看不到自己在跟誰比，
        # 而且在模式 1 點檔案看熱圖會順手換掉整欄的比較對象。改成明確的單選。
        self.tree_files = ttk.Treeview(left, columns=("b", "g", "st", "r"),
                                       show="tree headings", height=22)
        self.tree_files.heading("#0", text="檔案")
        self.tree_files.heading("b", text="基準")
        self.tree_files.heading("g", text="組 ✓")
        self.tree_files.heading("st", text="狀態")
        self.tree_files.heading("r", text="相似度")
        self.tree_files.column("#0", width=210)
        self.tree_files.column("b", width=42, anchor="center")
        self.tree_files.column("g", width=44, anchor="center")
        # 狀態放**文字**：emoji 在 Windows 的 Tk 裡退回單色字形，三種狀態長得
        # 一模一樣（使用者實測回報）；而 ttk 的 tag 只能整列上色，染不了單一格。
        self.tree_files.column("st", width=64, anchor="center")
        self.tree_files.column("r", width=80, anchor="center")
        self.tree_files.pack(fill="both", expand=True)
        # 組欄的方塊在**任何模式**都可以點——看起來能點就要能點（同 On 欄）。
        # 不必為了加一個檔進這一組而特地切到模式 2。
        self.tree_files.bind("<Button-1>", self.on_files_click, add="+")
        self.tree_files.bind("<<TreeviewSelect>>", self.on_file_click)
        self.tree_files.tag_configure("ingroup", background="#e3f2fd")
        # 基準那一列加粗——一欄數字全都相對於它，得一眼看得出是誰。
        # 放在 ingroup 之後設定：基準通常也在組裡，兩個 tag 同時套用時
        # 背景走 ingroup、字體走 isbase，各管一個屬性就不會互相蓋掉。
        self.tree_files.tag_configure("isbase", font=("TkDefaultFont", 9, "bold"))
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
        self.tree_peaks = ttk.Treeview(
            self.pane_peaks,
            columns=("n", "on", "dr", "ri", "int", "gc_ims", "gc", "ims", "trig"),
            show="headings")
        # `#` 是圈上的編號——沒有它就對不出表格哪一列是圖上哪一個圈。
        # 不放 RT s：y 軸顯示的是 RI，兩個保留時間欄位並排只會佔位子。
        #
        # **GC×IMS / GC / IMS / ▶ 四欄與第一支應用同語意**（`main.py`
        # `PEAK_TABLE_COLUMNS`）：沒有它們，逐檔挑峰時看不到這顆峰對到什麼化合物，
        # 等於閉著眼睛選。GC 欄的標題會隨實際用到的維度改成 `GC (RT s)`，理由見
        # `_sync_gc_heading()`。
        # 寬度總和就是這個面板的**自然寬度**（見 `_layout_panes`）。加到九欄之後
        # 右側面板一開始塞不下，最後三欄（GC / IMS / ▶）被切掉，要手動拉分隔線
        # 才看得到——使用者實際回報過。所以這裡壓緊，`_layout_panes()` 再保證
        # 開場就給得起這個寬度。`minwidth` 讓使用者往回拉時也不會把欄壓成 0。
        for col, txt, wid, anc in (("n", "#", 38, "center"), ("on", "On", 38, "center"),
                                   ("dr", "Drift rel", 70, "center"),
                                   ("ri", "RI", 62, "center"),
                                   ("int", "強度", 62, "center"),
                                   ("gc_ims", "GC×IMS", 140, "w"),
                                   ("gc", "GC (RI)", 92, "center"),
                                   ("ims", "IMS", 84, "center"),
                                   ("trig", "▶", 26, "center")):
            self.tree_peaks.heading(col, text=txt)
            self.tree_peaks.column(col, width=wid, minwidth=wid, anchor=anc,
                                   stretch=(col == "gc_ims"))
        #: 峰表塞得下所有欄位需要的寬度（含捲軸與框線的餘裕）
        self.peaks_min_w = 38 + 38 + 70 + 62 + 62 + 140 + 92 + 84 + 26 + 28
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

        # --- 面板 B：這一組的體檢
        #
        # **不再列成員名單**。使用者指出那是多餘的：組員與相似度兩欄跟左側檔案面板
        # 一字不差，而成員身分左邊已經用 ☑ 和底色說過兩次了。改成放左邊看不到的東西
        # ——這一組**像不像同一個標本**、是**哪一個檔**在拖後腿、門檻換算成幾個檔。
        # 原本這些只在按下彙整時跳一個對話框，那是錯的時機（人已經按下去了），
        # 而且沒說是哪個檔，使用者只能整組重來。
        self.pane_group = ttk.Frame(self.right_holder)
        self.group_txt = tk.Text(self.pane_group, height=14, wrap="word",
                                 relief="flat", background="#f7f7f7",
                                 font=("Segoe UI", 10), state="disabled",
                                 padx=10, pady=8)
        self.group_txt.tag_configure("h", font=("Segoe UI", 11, "bold"))
        self.group_txt.tag_configure("k", foreground="#666")
        self.group_txt.tag_configure("warn", foreground="#c62828")
        self.group_txt.tag_configure("ok", foreground="#2e7d32")
        self.group_txt.tag_configure("dim", foreground="#999")
        self.group_txt.pack(fill="both", expand=True)

        # --- 面板 C：共識化合物（名稱 + 票數）
        self.pane_cmpd = ttk.Frame(self.right_holder)
        # **座標要看得見。** 每一列講的是熱圖上的一個位置，而位置就是
        # （x = 相對 RIP 的漂移，y = RI）——少了它們，使用者沒辦法把這一列對回
        # 圖上哪一個圈。只給 RT s 更糟：熱圖的 y 軸畫的是 RI，不是保留時間。
        # `可能數` = 這個位置在容差窗內有幾個化合物對得上。**數字越大越不確定**。
        # 原本叫「候選」，使用者指出那與直覺相反——「候選多」會被讀成「證據多」，
        # 而它其實是「分不出來」的度量。1 跟 38 的意義天差地遠。
        self.tree_cmpd = ttk.Treeview(
            self.pane_cmpd, columns=("votes", "dim", "n", "dr", "ri", "rt"),
            show="tree headings")
        for col, txt, wid, anc in (("#0", "化合物", 210, "w"),
                                   ("votes", "票數", 52, "center"),
                                   ("dim", "維度", 64, "center"),
                                   ("n", "可能數", 54, "center"),
                                   # 值一律置中；只有化合物名稱靠左——名稱長度差
                                   # 很多，置中之後每一列的起點都不一樣，掃讀很累。
                                   ("dr", "Drift rel", 68, "center"),
                                   ("ri", "RI", 62, "center"),
                                   ("rt", "RT s", 62, "center")):
            self.tree_cmpd.heading(col, text=txt)
            self.tree_cmpd.column(col, width=wid, anchor=anc,
                                  stretch=(col == "#0"))
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
    # --------------------------------------------------------- 進度視窗
    def _progress_open(self, title, text=""):
        """跑得久的工作要有進度視窗——狀態列一行字太容易被忽略。

        用**不確定式**（跑馬燈）而不是百分比：底下幾個工作的耗時差好幾個數量級
        （比對十幾秒、找峰每檔 55 秒），硬給一個百分比只會給錯的期待。
        視窗不給關閉鈕：工作跑完會自己消失，中途關掉它並不會停止背景執行緒，
        留一個關得掉但沒有作用的鈕比不留更糟。
        """
        if self._prog_win is not None and self._prog_win.winfo_exists():
            self._progress_update(text)
            return
        win = tk.Toplevel(self.root)
        win.title(title)
        win.transient(self.root)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", lambda: None)
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)
        self._prog_lbl = ttk.Label(frm, text=text or "處理中…", width=52,
                                   anchor="w", wraplength=380, justify="left")
        self._prog_lbl.pack(anchor="w")
        self._prog_bar = ttk.Progressbar(frm, mode="indeterminate", length=380)
        self._prog_bar.pack(fill="x", pady=(10, 4))
        self._prog_bar.start(12)
        ttk.Label(frm, foreground="#777",
                  text="跑完會自動關閉。").pack(anchor="w")
        self._prog_win = win
        self.root.update_idletasks()
        # 疊在主視窗中央——預設位置會跑到螢幕左上角，看起來像另一個程式
        try:
            x = self.root.winfo_rootx() + (self.root.winfo_width() - 420) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - 140) // 3
            win.geometry("+%d+%d" % (max(0, x), max(0, y)))
        except tk.TclError:
            pass

    def _progress_update(self, text):
        lbl = getattr(self, "_prog_lbl", None)
        if lbl is not None and lbl.winfo_exists() and text:
            lbl.config(text=text)

    def _progress_close(self):
        win = self._prog_win
        self._prog_win = None
        if win is not None and win.winfo_exists():
            try:
                self._prog_bar.stop()
            except (tk.TclError, AttributeError):
                pass
            win.destroy()
        # 放掉 widget 參照：留著的話 Python 端的包裝物件會活到 root 銷毀之後，
        # 它的 `__del__` 再去碰已經沒有的 Tcl 直譯器就會噴 RuntimeError。
        self._prog_bar = None
        self._prog_lbl = None

    def _sync_mode_buttons(self):
        """模式 2/3 在還沒有基準之前是灰的。

        沒有基準就沒有相似度，沒有相似度就挑不出同組，挑不出同組就談不上共識——
        三件事是有順序的。灰掉比讓使用者點進一個空面板好：空面板看起來像壞了。
        """
        rbs = getattr(self, "_mode_rb", None)
        if not rbs:
            return
        for val, need in ((MODE_GROUP, bool(self.base)),
                          (MODE_COMPOUND, bool(self.base) and len(self.group) >= 2)):
            rb = rbs.get(val)
            if rb is not None and rb.winfo_exists():
                rb.config(state="normal" if need else "disabled")

    def _mark_dirty(self):
        """畫面上的彙整結果過期了——下次進模式 3 要重算。

        沒有這個旗標的話，模式之間切來切去每次都重跑一輪比對（十幾秒起跳）；
        有了它就只在**真的變了**的時候重算：改組員、改門檻、改規則、改選峰。
        """
        self._cons_dirty = True

    def on_mode_change(self):
        m = self.mode.get()
        self._sync_mode_buttons()
        # 灰掉的模式不該被選中（例如挑空了這一組之後）——退回模式 1，不要停在
        # 一個沒有內容的面板上。
        rb = getattr(self, "_mode_rb", {}).get(m)
        if rb is not None and str(rb.cget("state")) == "disabled":
            self.mode.set(MODE_HEATMAP)
            m = MODE_HEATMAP
            self.status.config(text="先在「基準」欄點一個檔，模式 2/3 才有東西可看。")
        for p in (self.pane_peaks, self.pane_group, self.pane_cmpd):
            p.pack_forget()
        # 熱圖的唯讀標記跟著模式走——切模式要重畫，否則標記留在上一個模式
        self.root.after_idle(self._refresh_badge)
        if m == MODE_HEATMAP:
            self.pane_peaks.pack(fill="both", expand=True)
            self.right_title.config(text="峰（勾 = 納入共識）")
            self.hint.config(text="點任一檔：載入它的熱圖與峰。"
                                  "點紅圈或表格列可增刪選取。\n" + LEGEND)
            self.right_note.config(text="")
        elif m == MODE_GROUP:
            self.pane_group.pack(fill="both", expand=True)
            self.right_title.config(text="相似的 mea（同一標本）")
            self.hint.config(text="相似度以「基準」欄那個檔（◉）為比較對象；"
                                  "夠高的已自動選進來，用「組 ✓」增刪。"
                                  "建議只是建議——實測自動分組 96%，"
                                  "剩下的靠你判斷。\n" + LEGEND)
            self._fill_group_panel()
            # 切進來就把相似度算出來——這個模式的整個用途就是看那一欄數字
            if self.corr is None and self.base:
                self._start_similarity(reason="模式 2")
        else:
            self.pane_cmpd.pack(fill="both", expand=True)
            self.right_title.config(text="共識化合物（票數）")
            self.hint.config(text="檔案面板不變。雙擊右側任一列："
                                  "該位置所有可能的化合物。\n" + LEGEND)
            self._fill_compound_panel()
            # 切進來就彙整。**只在真的過期時重算**，否則模式之間切來切去
            # 每次都白跑一輪比對。
            if getattr(self, "_cons_dirty", True) or not self.consolidated:
                self.consolidate()

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
        # 相似度是「相對於**基準**」，標題要講出是誰，否則一整欄數字沒有參照點。
        # 改用 self.base 而不是 self.current：後者是「正在看哪個熱圖」，在模式 1
        # 點檔案看圖會順手換掉整欄的比較對象——使用者看不出為什麼數字全變了。
        self.tree_files.heading(
            "r", text=("相似度（未設基準）" if not self.base
                       else "相似度 vs %s" % self._short_name(self.base)))
        self.tree_files.delete(*self.tree_files.get_children())
        for f in self.files:
            ing = f in self.group
            # 空白欄位不解釋自己，但**格子裡也不放整句話**——一欄字串長度不一的
            # 句子既不專業也把欄寬撐開。格子只放短標籤，解釋放在下方 hint。
            if self.corr is None:
                # **不要寫「待掃描」**：那和「狀態」欄、和「2. 掃描全部」按鈕
                # 撞在一起，已經找過峰的檔看到它會以為峰沒找完（使用者回報過）。
                # 這一欄講的是「跨檔相似度還沒算」，是另一件事。
                # 沒有基準就先請他選一個；選了但還沒算完就是「計算中」——
                # 「未計算」聽起來像要自己去做什麼，其實程式正在跑。
                r = "選基準" if self.base is None else "計算中"
            elif self.base is None:
                r = "選基準"
            elif self.base not in self.corr_files or f not in self.corr_files:
                # 只掃描了一組時，組外的檔沒有數字可比——講清楚是「沒掃到」，
                # 不是「不相似」。
                r = "未掃到"
            else:
                i = self.corr_files.index(self.base)
                j = self.corr_files.index(f)
                r = "基準" if i == j else "%+.2f" % self.corr[i, j]
            # **未選也要畫 `☐`**，不能留空白：空格子看不出這一欄可以點，使用者
            # 只好繞去模式 2。與峰表的 `On` 欄同一個道理。
            #
            # 底色（`ingroup`）不能取代方塊：ttk 的**選取色會蓋掉 tag 的底色**，
            # 所以正在看的那一列即使在組裡也看不到藍底——那時只剩方塊在說話。
            # 有自訂規則的檔要標出來：兩個檔同一組看起來一樣的參數卻給出不同的
            # 峰數是完全可能的，不標的話那看起來就是程式壞了。
            name = os.path.basename(f)
            if rules_store.has_override(f):
                # 用文字不用符號：⚙ 在這個字級下看起來像一朵花,使用者問過那是什麼。
                # 一個標記如果要人猜,它就沒有在傳達訊息。
                name += "   [自訂規則]"
            is_base = (f == self.base)
            tags = []
            if ing:
                tags.append("ingroup")
            if is_base:
                tags.append("isbase")
            state = self._ready_light(f)
            self.tree_files.insert(
                "", "end", iid=f, text=name,
                # 彩色圓點放在檔名前面（#0 欄的 image）——這是 ttk 的 Treeview
                # 唯一給得出「一格顏色」的地方，而且不依賴字形。
                image=self._ready_dot(state),
                values=(BASE_ON if is_base else BASE_OFF,
                        CHECK_ON if ing else CHECK_OFF, state, r),
                tags=tuple(tags))
        self._sync_mode_buttons()
        self._sync_rules_scope()

    def _regenerate(self, mea):
        """點狀態燈＝**不管有沒有都重做一次** `.npz` 與找峰。

        先問一聲：這要花約 68 秒，而且多半是誤點（狀態欄就在組欄旁邊）。
        只刪 `results/` 底下自己的產物，**不碰 `GAS/`**（隔離規則 3）。
        """
        name = os.path.basename(mea)
        if self.busy:
            self.status.config(text="正在%s，請等它跑完。" % self.busy)
            return
        if not messagebox.askyesno(
                "重新產生",
                "要重新產生 %s 的 .npz 與找峰結果嗎？\n\n"
                "不管現在有沒有都會重做一次，約 68 秒。\n"
                "原始的 .mea 不會被更動。" % name):
            return
        self.busy = "重新產生 %s" % name
        self._progress_open("重新產生", "%s：重讀 .mea 並重新找峰（約 68 秒）…" % name)
        self.status.config(text="重新產生 %s（約 68 秒）…" % name)
        threading.Thread(target=self._regen_worker, args=(mea,),
                         daemon=True).start()

    def _regen_worker(self, mea):
        try:
            npz = areas2._npz_path(mea)
            # `ensure_npz()` 沒有 force 參數——存在就直接回傳。要重做只能先移掉。
            if os.path.exists(npz):
                os.remove(npz)
            areas2.ensure_npz(mea, verbose=False)
            # `reuse_cache=False` 強制重跑偵測，不採信 `_peaks2.json`
            areas2.detect_one(mea, self._rules_for(mea), use_baseline=False,
                              reuse_cache=False, verbose=False)
            L.detect_cached(mea, self._rules_for(mea), use_baseline=False,
                            verbose=False)          # 補寫參數指紋
            self.q.put(("regenerated", mea))
        except BaseException as exc:
            self.q.put(("error", "重新產生 %s 失敗：%s: %s"
                        % (os.path.basename(mea), type(exc).__name__, exc)))
        finally:
            self.q.put(("busy_clear", None))

    # ------------------------------------------------------- 逐檔規則
    def _rules_for(self, mea):
        """這個檔實際要用的規則：有自訂就用自訂，沒有就用預設。

        **每一個會影響偵測的呼叫都必須走這裡。** 傳錯 config 不會報錯——
        `params_fingerprint()` 會忠實地替錯的參數記一份指紋，於是快取無聲地對應到
        別人的設定。這是逐檔規則唯一的風險點。

        正在面板上編輯的那個檔要回**面板上的那一份**，否則使用者改了參數、按下
        重新產生，跑的還是磁碟上的舊值。
        """
        if mea and mea == self.current:
            return self.cur_rules
        cfg, _src = rules_store.effective(mea, self.rules_config)
        return cfg

    def _load_rules_for_current(self):
        """切換檔案時把面板要編輯的那一份換掉。"""
        self.cur_rules, self.cur_rules_src = rules_store.effective(
            self.current, self.rules_config)

    @staticmethod
    def _short_name(path):
        """欄位標題用的短名：拿掉 G.A.S. 的 `YYMMDD_HHMMSS_` 前綴與副檔名。

        原本寫死 `basename[7:-4]`，那假設了檔名一定是那個格式——名字短一點就切成
        空字串（標題變成「相似度 vs 」，什麼都沒說）。這裡只在真的看到那個前綴時
        才拿掉，否則原樣保留。
        """
        stem = os.path.splitext(os.path.basename(path))[0]
        m = re.match(r"^\d{6}_\d{6}_(.+)$", stem)
        return m.group(1) if m else stem

    def set_base(self, mea):
        """把這個檔設成比較基準，並依相似度重新挑出這一組。

        **一次只有一個基準**：相似度是相對量，兩個基準就沒有一欄數字可以並排比較。
        換基準＝整組重挑，不是累加——使用者要的是「換一個標本看看」，不是把兩個
        標本的組員混在一起。

        還沒算相似度時仍然可以設基準（欄位會照樣標出來），只是挑不了組——那時
        就講清楚要先按「2. 掃描全部」，不要靜靜什麼都不做。
        """
        self.base = mea
        name = os.path.basename(mea)
        if self.corr is None or mea not in self.corr_files:
            # 挑不了整組，但**基準本身一定要進去**——否則使用者點了基準卻發現
            # 一個檔都沒選上，看起來像沒有反應。
            self.group = {mea}
            self._refresh_files()
            self._fill_group_panel()
            # **選了基準就把相似度算出來。** 使用者的期待很合理：「我指定了比較
            # 對象，那一欄就該有數字」。原本只是叫他去按另一顆按鈕——而所需的東西
            # （每個檔的峰）多半早就備好了，那一步只是還沒有人去按。
            self._start_similarity(reason="基準：%s" % name)
            return
        picked = self._suggest_group(mea)
        self.group = set(picked)
        self._refresh_files()
        self._fill_group_panel()
        self.status.config(
            text="基準：%s　→　自動選了 %d 個檔（相似度 ≥ %.2f）。"
                 "不滿意就點「組 ✓」自己增刪。" % (name, len(picked), SUGGEST_R))

    def _dot(self, colour, size=11):
        """畫一顆實心圓點當圖示。**這是這個元件唯一拿得到的「一格顏色」。**

        emoji 會退回單色字形，tag 只能染整列——但 Treeview 的 item 支援 `image`，
        而 `PhotoImage` 可以就地畫，不必附任何圖檔。

        **一定要留住參照**（`self._dots`）：Tk 的圖片物件被 Python 回收之後，
        畫面上那張圖會直接消失，而且不會有任何錯誤——典型的「圖不見了」災情。
        """
        img = tk.PhotoImage(width=size, height=size)
        c = size / 2.0 - 0.5
        rad = size / 2.0 - 1.0
        for y in range(size):
            # 只塗圓內的那一段，圓外**完全不碰**——`PhotoImage` 一開始就是透明的，
            # 這樣圓點才會浮在該列自己的底色上（選取藍、同組淡藍都行）。
            # 想用 `{}` 表示「這一格不塗」是行不通的：Tcl 會回 can't parse color ""。
            dy = abs(y - c)
            if dy > rad:
                continue
            half = (rad * rad - dy * dy) ** 0.5
            x0, x1 = int(round(c - half)), int(round(c + half))
            # 一次塗一整段，不要逐點呼叫（逐點是 size² 次 Tcl 往返）
            img.put("{%s}" % " ".join([colour] * max(1, x1 - x0 + 1)), to=(x0, y))
        return img

    def _ready_dot(self, state):
        """狀態對應的圓點圖，建一次之後重用。"""
        if not hasattr(self, "_dots"):
            self._dots = {}
        if state not in self._dots:
            colour = READY_COLOUR.get(state)
            self._dots[state] = self._dot(colour) if colour else ""
        return self._dots[state]

    def _ready_light(self, mea):
        """這個檔點下去會不會馬上有反應。綠＝會，黃＝要找峰，紅＝連 `.npz` 都沒有。"""
        if not os.path.exists(areas2._npz_path(mea)):
            return READY_RED
        if not L.peaks_are_current(mea, self._rules_for(mea), use_baseline=False,
                                   trust_existing=True, write=False):
            return READY_AMBER
        return READY_GREEN

    # --------------------------------------------------------- 點檔案
    def on_files_click(self, event):
        """點「組」欄＝加入/移出這一組；點其他欄＝照目前模式處理。

        回 "break" 才不會同時觸發選取事件（模式 1 會白跑一次載入熱圖）。
        """
        if self.tree_files.identify_region(event.x, event.y) != "cell":
            return None
        col = self.tree_files.identify_column(event.x)
        row_id = self.tree_files.identify_row(event.y)
        if col == "#1":                     # #1 = 基準欄 → 設為比較基準
            if row_id:
                self.set_base(row_id)
            return "break"
        if col == "#3":                     # #3 = 狀態欄 → 重新產生
            if row_id:
                self._regenerate(row_id)
            return "break"
        if col != "#2":                     # #2 = 組欄
            return None
        row = self.tree_files.identify_row(event.y)
        if not row:
            return None
        if row in self.group:
            self.group.discard(row)
        else:
            self.group.add(row)
        self._mark_dirty()
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
        """模式 2：還沒有基準時第一次點＝設為基準並帶入同組；之後每點一個＝加入/移出。

        **與「基準」欄是同一個動作，只是兩個入口。** 那一欄讓這件事看得見、
        而且在任何模式都做得到；這裡保留是因為模式 2 的說明就是這樣寫的。
        """
        if not self.group:
            self.set_base(path)          # 設基準會順便挑出整組並更新狀態列
            return
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

    def _group_stats(self):
        """這一組的體檢 + 還要找幾個檔的峰（後者要看 `results/`，所以不在 logic 裡）。"""
        parts = self.frac.get().split("/")
        frac = float(parts[0]) / float(parts[1])
        st = L.group_stats(self.group, self.corr, self.corr_files, min_fraction=frac)
        st["n_pending"] = sum(1 for f in self.group
                              if not os.path.exists(areas2._peaks2_path(f)))
        return st

    def _fill_group_panel(self):
        st = self._group_stats()
        t = self.group_txt
        t.config(state="normal")
        t.delete("1.0", "end")

        def line(text, tag=None):
            t.insert("end", text + "\n", (tag,) if tag else ())

        if not st["n"]:
            line("還沒選任何檔。", "dim")
            line("")
            line("點左邊任一個 .mea：相似度 ≥ %.2f 的會整組帶進來，"
                 "再點其他檔可以加入或移出。" % L.GROUP_GOOD_R, "dim")
            t.config(state="disabled")
            self.right_note.config(text="")
            return

        line("這一組 %d 個檔" % st["n"], "h")
        line("")
        short = lambda p: os.path.basename(p)          # noqa: E731

        if st["mean_r"] is None:
            # 相似度要先掃描才算得出來——空白不解釋自己
            line("相似度  尚未計算", "k")
            line("　按「2. 掃描全部（找峰）」之後才有。", "dim")
        else:
            line("最低相似度  %+.2f" % st["min_r"], "k")
            line("　└ %s ↔ %s" % (short(st["min_pair"][0]),
                                  short(st["min_pair"][1])), "dim")
            line("平均相似度  %+.2f" % st["mean_r"], "k")
        if st["missing_r"]:
            line("　%d 個檔還沒掃到，沒有相似度可比。" % len(st["missing_r"]), "dim")
        line("")

        # **門檻換算成「幾個檔」**。1/2 與 2/3 在 5 個檔時是 3 vs 4，在 18 個檔時
        # 是 9 vs 12——差別足以改變要不要多收一個邊緣的檔，而這個數字原本要等到
        # 彙整完才看得到。
        line("門檻 %s → %d 個檔中要有 %d 個看得到才算數"
             % (self.frac.get(), st["n"], st["required"]), "k")
        if st["n_pending"]:
            line("待找峰 %d 個檔（約 %d 秒）" % (st["n_pending"],
                                              st["n_pending"] * 55), "k")
        line("")

        if st["verdict"] == "mixed":
            line("⚠ 這些看起來不是同一個標本", "warn")
            line("　最低只有 %+.2f。票數只有在同一標本的重複測量之間才有意義——"
                 "不同標本混在一起，真實化合物會因為只出現在其中幾個檔"
                 "而被判為未達門檻。" % st["min_r"], "warn")
        elif st["verdict"] == "outlier":
            line("⚠ 有一個檔明顯不同組", "warn")
            line("　%s 對其他成員平均只有 %+.2f，而其餘兩兩相比皆 ≥ %+.2f。"
                 "拿掉它再彙整。"
                 % (short(st["outlier"]), st["outlier_mean_r"],
                    st["rest_min_r"]), "warn")
        elif st["verdict"] == "ok":
            line("✓ 組內一致，可以彙整", "ok")
        t.config(state="disabled")
        self.right_note.config(text="")

    # --------------------------------------------------------------- 單檔
    def _load_file(self, path):
        """沒有 `.npz` / 找峰結果就**當場生**，但放到背景做。

        直接在主執行緒跑會讓視窗凍住約 68 秒（讀檔 13 + 找峰 55），使用者看到的是
        「按了沒反應」——第二支應用正是這樣被回報過。所以：立刻在狀態列說明要做什麼、
        要多久，然後開背景執行緒，做完再畫。
        """
        need_npz = not os.path.exists(areas2._npz_path(path))
        need_pk = not L.peaks_are_current(path, self._rules_for(path),
                                          use_baseline=False, trust_existing=True)
        if need_npz or need_pk:
            if self.busy:
                self.status.config(
                    text="正在%s，請等它跑完再點檔案。" % self.busy)
                return
            est = (13 if need_npz else 0) + (55 if need_pk else 0)
            self.busy = "準備 %s" % os.path.basename(path)
            self._progress_open(
                "準備檔案",
                "%s 還沒處理過，正在產生%s%s（約 %d 秒）…"
                % (os.path.basename(path), " .npz" if need_npz else "",
                   " 找峰" if need_pk else "", est))
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
            L.detect_cached(path, self._rules_for(path), use_baseline=False,
                            verbose=False)
            self.q.put(("prepared", path))
        except BaseException as exc:
            self.q.put(("error", "%s: %s" % (type(exc).__name__, exc)))
        finally:
            self.q.put(("busy_clear", None))

    def _show_loaded(self, path):
        # **走 `L.detect_cached()` 而不是 `areas2.detect_one()`**：後者命中快取時
        # 直接回傳，不會用現在這份規則重新標記，於是改了選配規則（R001/R002…）
        # 表格照樣列出全部的峰。使用者實際回報過。
        pk, _stats, _meta = L.detect_cached(path, self._rules_for(path),
                                            use_baseline=False, verbose=False)
        self._floor = _stats.get("floor")
        self._rip_index = _stats.get("rip_index")
        if self.ri_cal:
            calibration.attach_ri(pk, self.ri_cal)   # 沒有 RI 就畫不出圈
        self.peaks = L.apply_effective(state_mod.load(path, pk))
        # 規則面板要跟著換到這個檔的那一份——輸入框的值是建構時填的，不會自己跟上
        self._load_rules_for_current()
        self._rebuild_rules_panel()
        self._render(path)
        self._fill_peak_table()
        self._autofill_matches()       # 四個比對欄自己填，不必逐顆按 ▶
        self._write_funnel(None)
        n_on = sum(1 for p in self.peaks if L.effective_active(p))
        self.status.config(text="%s：%d 個峰，已選 %d"
                                % (os.path.basename(path), len(self.peaks), n_on))

    # ------------------------------------------------- 逐峰化合物比對
    def _ensure_libraries(self, notify=False):
        """載入 `.ril` / `.iml` 一次，之後重用。回傳 True 表示現在就可以比對。

        `library_data/` 可能不存在或是空的——那時**不重試**（記成 unavailable），
        否則每換一個檔就再失敗一次，狀態列被洗掉而使用者不知道真正的原因。
        """
        if self.lib_state == "ready":
            return True
        if self.lib_state == "loading":
            return False
        if self.lib_state == "unavailable":
            if notify:
                messagebox.showwarning(
                    "沒有化合物庫",
                    "找不到 library_data/ 或裡面沒有 .ril / .iml，無法比對化合物。")
            return False
        self.lib_state = "loading"
        threading.Thread(target=self._lib_worker, args=(self.current,),
                         daemon=True).start()
        return False

    def _lib_worker(self, mea):
        try:
            data_dir = library.resolve_data_dir()   # 不能是 None，會 TypeError
            if not data_dir:
                self.q.put(("lib_fail", "找不到 library_data/。"))
                return
            header = calibration._read_header_lite(mea) if mea else {}
            # `ri_calibration` 決定選哪一份庫的極性——拿峰的 RI 對庫的 RI，兩者
            # 不同尺標時 ±5 命中的是「另一個化合物的 RI 恰好等於本峰的 RI」。
            ril, iml, info = identify.load_libraries(
                data_dir, header, ri_calibration=self.ri_cal)
            self.q.put(("lib_ready", (ril, iml, info)))
        except BaseException as exc:
            self.q.put(("lib_fail", "%s: %s" % (type(exc).__name__, exc)))

    def _cache_key(self, path, p):
        return (os.path.basename(path), p.get("rt_index"), p.get("dt_index"))

    def _apply_cached_matches(self, path, peaks):
        for p in peaks:
            res = self.match_cache.get(self._cache_key(path, p))
            if res is not None:
                p["matches"] = res

    def _autofill_matches(self):
        """自動補上四個比對欄——不必逐顆按 ▶。

        先套快取（換規則重畫時免重算），剩下的丟背景比對。**在主執行緒比對會凍住
        視窗**：一顆峰在十幾萬列的庫裡找 ±5 的窗，幾十顆峰累積起來是好幾秒。
        """
        if not self.peaks or not self.current:
            return
        self._apply_cached_matches(self.current, self.peaks)
        todo = [p for p in self.peaks
                if self._cache_key(self.current, p) not in self.match_cache]
        if not todo:
            self._refresh_peak_rows()
            return
        if not self._ensure_libraries():
            return                      # 庫還在載，載完 _drain 會再叫一次
        threading.Thread(target=self._match_worker,
                         args=(self.current, todo, self.ril, self.iml),
                         daemon=True).start()

    def _match_worker(self, path, targets, ril, iml):
        try:
            import match as match_mod
            out = [(self._cache_key(path, p), match_mod.match_all(p, ril, iml))
                   for p in targets]
            self.q.put(("matched", (path, out)))
        except BaseException as exc:
            self.q.put(("status", "比對化合物失敗：%s: %s"
                        % (type(exc).__name__, exc)))

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
        self._draw_readonly_badge()

    def _refresh_badge(self):
        """只換標記，不重繪整張圖（重繪會丟掉縮放時的視覺連續性）。"""
        if self.img_orig is None:
            return
        self.canvas.delete("readonly")
        self._draw_readonly_badge()

    def _draw_readonly_badge(self):
        """模式 2/3 在熱圖左上角標「唯讀」。

        光是讓點擊沒有反應是不夠的——那和「壞掉了」看起來一模一樣。要嘛能點，
        要嘛講明為什麼不能點。
        """
        if self.img_orig is None or self.mode.get() == MODE_HEATMAP:
            return
        txt = "唯讀（模式 %s）—— 要改選取請切到模式 1"
        txt = txt % ("2" if self.mode.get() == MODE_GROUP else "3")
        self.canvas.create_rectangle(8, 8, 12 + 7.0 * len(txt), 30,
                                     fill="#fff3cd", outline="#e0a800",
                                     tags=("readonly",))
        self.canvas.create_text(14, 19, text=txt, anchor="w", fill="#7a5c00",
                                font=("Segoe UI", 9), tags=("readonly",))

    def on_canvas_double(self, event):
        """雙擊**空白處**回到全圖；雙擊峰上則不動（那是要切換它）。"""
        if (self._peak_at(event.x, event.y) is not None
                and self.mode.get() == MODE_HEATMAP):
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
        if i is None:
            return
        # **模式 2 / 3 的熱圖是唯讀的。** 那兩個模式的右側面板不是峰表，點掉一顆峰
        # 完全沒有回饋——選取被改了而畫面上看不出來，等到彙整才發現票數不對。
        # 縮放與平移照舊：那只是看，不改任何狀態。
        if self.mode.get() != MODE_HEATMAP:
            self.status.config(
                text="熱圖在這個模式是唯讀的。要增刪選取請切到「1. 看熱圖、選峰」。")
            return
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
            # **圈只有兩種樣子：選了＝實線紅，沒選＝實線灰。**
            # 曾經第三種：使用者把規則否決的峰救回來時畫紅色虛線。使用者要求拿掉
            # ——熱圖上分不出「這是什麼意思」，反而像畫錯了。那個區別沒有消失，
            # 它在表格裡（`byrule` 標成琥珀色），那裡有欄位標題可以解釋自己。
            col = "#ff3b30" if on else "#888888"
            cid = self.canvas.create_oval(x - r, y - r, x + r, y + r,
                                          outline=col, width=2)
            tid = self.canvas.create_text(x + r + 4, y - r - 2, text=str(i + 1),
                                          fill=col, anchor="w",
                                          font=("Segoe UI", 8, "bold"))
            # 不綁 tag_bind：點擊統一由 on_release 判斷，否則「拖曳平移」放開時
            # 會誤觸底下的峰。
            self.circles[i] = (cid, tid)

    def _fill_peak_table(self):
        self.tree_peaks.delete(*self.tree_peaks.get_children())
        for i, p in enumerate(self.peaks):
            self.tree_peaks.insert("", "end", iid=str(i),
                                   values=self._peak_row(i, p),
                                   tags=self._peak_tags(p))
        self._sync_gc_heading()

    def _peak_row(self, i, p):
        on = L.effective_active(p)
        return (i + 1,
                CHECK_ON if on else CHECK_OFF,
                self._fmt(p.get("drift_relative"), 3),
                self._fmt(p.get("ri"), 1),
                self._fmt(p.get("intensity"), 0),
                self._cell_gc_ims(p), self._cell_gc(p), self._cell_ims(p),
                # ▶ 只在這顆峰還留著時才可以按——取消勾選的峰不參與共識，
                # 開它的候選視窗只會讓人以為它還算數（同第一支應用）。
                "▶" if on else " ")

    @staticmethod
    def _peak_tags(p):
        on = L.effective_active(p)
        return (("off",) if not on else ()) + (
            ("byrule",) if not p.get("rule_active", True) else ())

    def _refresh_peak_rows(self):
        """比對結果回來之後就地重畫每一列，不重建表格（保留選取與捲動位置）。"""
        for iid in self.tree_peaks.get_children():
            i = int(iid)
            if i < len(self.peaks):
                self.tree_peaks.item(iid, values=self._peak_row(i, self.peaks[i]),
                                     tags=self._peak_tags(self.peaks[i]))
        self._sync_gc_heading()

    # ---- 四個比對欄的格子（與 main.py `_cell_value` 同語意） ----
    @staticmethod
    def _cell_gc_ims(p):
        """兩軸都同意的第一名。**這一欄才是鑑定**，GC/IMS 兩欄各自只是一個維度。"""
        comb = (p.get("matches") or {}).get("combined_matches") or []
        if not comb:
            return "—" if p.get("matches") else "…"
        name = comb[0].get("Name") or comb[0].get("NAME") or "?"
        return name if len(comb) == 1 else "%s（+%d）" % (name, len(comb) - 1)

    @staticmethod
    def _cell_gc(p):
        """最接近的庫值 + Δ，好跟這顆峰自己的 RI 欄並排看。名字在 ▶ 裡。"""
        hits = (p.get("matches") or {}).get("gc_matches")
        if not hits:
            return "—" if p.get("matches") else "…"
        best = hits[0]
        # **RT 退路的值是秒、不是 RI**。帶單位，格子自己就分得出來；欄位標題也會
        # 跟著改（`_sync_gc_heading`），兩處一致。
        if "rt" in best.get("match_dimensions", []):
            val, delta, unit = best.get("Rt[sec]"), best.get("delta_rt"), " s"
        else:
            val, delta, unit = best.get("RI"), best.get("delta_ri"), ""
        if val is None:
            return "—"
        return "%g%s%s" % (val, unit, "" if delta is None else " (Δ%.2f)" % delta)

    @staticmethod
    def _cell_ims(p):
        hits = (p.get("matches") or {}).get("ims_matches")
        if not hits:
            return "—" if p.get("matches") else "…"
        best = hits[0]
        val = best.get("Dt[a.u.]")
        delta = (best.get("delta_drift_rel")
                 if "drift_rel" in best.get("match_dimensions", [])
                 else best.get("delta_k0"))
        if val is None:
            return "—"
        return "%.3f%s" % (val, "" if delta is None else " (Δ%.3f)" % delta)

    def _gc_dimension(self):
        dims = {(p.get("matches") or {}).get("gc_dimension") for p in self.peaks}
        return "ri" if "ri" in dims else ("rt" if "rt" in dims else None)

    def _sync_gc_heading(self):
        """GC 欄標題要講實際用了哪個維度。

        標題寫死 "GC (RI)" 但沒有 STD 時格子裡是庫的 `Rt[sec]`（秒），使用者會
        把秒當成 RI 讀。這與 `mea_source`、`rt_axis_version`、`k0_mode` 是同一件事：
        **不讓降級無聲發生**。第一支應用 `_sync_gc_column_heading()` 同樣處理。
        """
        dim = self._gc_dimension()
        try:
            self.tree_peaks.heading(
                "gc", text={"ri": "GC (RI)", "rt": "GC (RT s)"}.get(dim, "GC (RI)"))
        except tk.TclError:
            return
        if dim == "rt":
            self.status.config(
                text="⚠ 本資料夾無 RI 校正，GC 欄是拿保留時間比庫的 Rt[sec]"
                     "——不跨儀器/管柱/方法轉移。")

    @staticmethod
    def _fmt(v, nd):
        return "—" if v is None else ("%.*f" % (nd, v))

    def _toggle_index(self, i):
        p = self.peaks[i]
        # 寫 `user_active`：使用者的判定覆蓋規則的判定，而且兩者分得開——
        # 規則之後再改，使用者這一次的決定仍然算數。
        #
        # **但與規則一致時要存回 `None`（沒意見），不是存一個明確的 True/False。**
        # 不這樣做的話，「點一下開、再點一下關」會留下一筆與規則相同的「意見」，
        # 之後規則改了就再也套不到這顆峰上。實際發生過：規則一度靜靜失效，使用者
        # 在那段時間點過的峰全部存成 `user_active=True`（實測 3 個檔 75 顆）；
        # 規則修好之後那 75 顆變成「使用者堅持要」，`top_n` 對它們完全無效。
        want = not L.effective_active(p)
        p["user_active"] = None if want == bool(p.get("rule_active", True)) else want
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
        col = self.tree_peaks.identify_column(event.x)
        if col not in ("#2", "#9"):            # #2 = On 欄、#9 = ▶ 欄
            return None
        row = self.tree_peaks.identify_row(event.y)
        if row == "":
            return None
        i = int(row)
        if col == "#2":
            self._toggle_index(i)
        elif L.effective_active(self.peaks[i]):
            self._open_candidates(i)           # 取消勾選的峰不開，▶ 也是灰的
        return "break"

    def _open_candidates(self, i):
        """列出這顆峰的候選化合物（同第一支應用的 ▶ 面板）。

        一顆峰只開一個視窗：已經開著就把它拉到前面,不疊第二個。
        """
        p = self.peaks[i]
        win = self._match_wins.get(i)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return
        self._apply_cached_matches(self.current, self.peaks)
        if "matches" not in p:
            if not self._ensure_libraries(notify=True):
                self.status.config(text="化合物庫載入中，好了會自動填上…")
                return
            self.status.config(text="第 %d 顆峰比對中…" % (i + 1))
            self._autofill_matches()
            return
        self._render_candidates(i, p)

    def _render_candidates(self, i, p):
        res = p.get("matches") or {}
        combined = res.get("combined_matches") or []
        gc_hits = res.get("gc_matches") or []
        ims_hits = res.get("ims_matches") or []
        comb_cas = {c.get("CAS") for c in combined}
        gc_only = [h for h in gc_hits if h.get("CAS") not in comb_cas]
        ims_only = [h for h in ims_hits if h.get("CAS") not in comb_cas]

        win = tk.Toplevel(self.root)
        win.title("第 %d 顆峰 — 可能的化合物" % (i + 1))
        win.geometry("860x620")
        self._match_wins[i] = win
        win.bind("<Destroy>",
                 lambda e, w=win, k=i: (self._match_wins.pop(k, None)
                                        if e.widget is w else None))

        hdr = ttk.Frame(win, padding=(10, 8, 10, 2))
        hdr.pack(fill="x")
        ri = p.get("ri")
        ttk.Label(hdr, text="第 %d 顆峰" % (i + 1),
                  font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ttk.Label(
            hdr, foreground="#555",
            text="Drift rel. RIP %s　RI %s%s　保留時間 %s s　強度 %s"
                 % (self._fmt(p.get("drift_relative"), 3),
                    self._fmt(ri, 1),
                    "*（外插）" if p.get("ri_extrapolated") else "",
                    self._fmt(p.get("retention_s"), 1),
                    self._fmt(p.get("intensity"), 0))).pack(anchor="w")
        # GC 走 RI 還是退到 RT、IMS 走 K0 還是 RIPrel 漂移——**兩條都要講**，
        # 否則兩個不同座標系在畫面上長得一模一樣。
        ttk.Label(
            hdr, foreground="#777",
            text="GC 維度：%s　IMS 維度：%s" % (
                {"ri": "保留指數 RI", "rt": "保留時間（秒）",
                 None: "無"}.get(res.get("gc_dimension"),
                                 str(res.get("gc_dimension"))),
                {"drift_rel": "相對 RIP 漂移", "k0": "K0",
                 None: "容差內沒有漂移命中"}.get(res.get("ims_dimension"),
                                                str(res.get("ims_dimension")))
            )).pack(anchor="w", pady=(2, 0))

        cols = ("kind", "name", "cas", "formula", "libval", "delta", "src")
        heads = {"kind": "維度", "name": "化合物", "cas": "CAS", "formula": "分子式",
                 # 與共識表用同一套說法：「庫」是縮寫，寫全才不必猜。
                 # 這裡刻意不寫死 RI——依維度可能是 RI、秒或漂移值。
                 "libval": "資料庫值", "delta": "Δ", "src": "來源檔"}
        wids = {"kind": 68, "name": 250, "cas": 96, "formula": 84,
                "libval": 84, "delta": 74, "src": 130}
        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=10, pady=6)
        tree = ttk.Treeview(body, columns=cols, show="headings")
        for c in cols:
            tree.heading(c, text=heads[c])
            # 與其他表格一致：值置中，名稱靠左
            tree.column(c, width=wids[c], stretch=(c == "name"),
                        anchor="w" if c == "name" else "center")
        vs = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vs.set)
        tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        tree.tag_configure("combined", background="#e7f5e7")

        def _row(cand, label):
            dims = cand.get("match_dimensions", [])
            if "ri" in dims:
                libval, delta = cand.get("RI"), cand.get("delta_ri")
            elif "rt" in dims:
                libval, delta = cand.get("Rt[sec]"), cand.get("delta_rt")
            elif "drift_rel" in dims:
                libval, delta = cand.get("Dt[a.u.]"), cand.get("delta_drift_rel")
            else:
                libval, delta = cand.get("Dt[a.u.]"), cand.get("delta_k0")
            return (label,
                    cand.get("Name") or cand.get("NAME") or "?",
                    cand.get("CAS") or "—", cand.get("Formula") or "—",
                    "—" if libval is None else "%g" % libval,
                    "—" if delta is None else "%.3g" % delta,
                    cand.get("source_file") or cand.get("source_file_gc")
                    or cand.get("source_file_ims") or "—")

        # ±5 RI 在十幾萬列的庫裡可以回上百筆；每組只列最接近的 MAX_PER 筆
        # （已依 Δ 排序），否則視窗根本捲不完。
        MAX_PER = 150
        total = len(combined) + len(gc_only) + len(ims_only)
        show_c = tk.BooleanVar(value=True)
        show_g = tk.BooleanVar(value=True)
        show_i = tk.BooleanVar(value=True)
        foot = ttk.Label(win, foreground="#555", wraplength=820, justify="left")

        def refresh(*_):
            tree.delete(*tree.get_children())
            shown = 0
            if show_c.get():
                for c in combined:
                    tree.insert("", "end", values=_row(c, "GC+IMS"),
                                tags=("combined",))
                shown += len(combined)
            if show_g.get():
                for c in gc_only[:MAX_PER]:
                    tree.insert("", "end", values=_row(c, "GC"))
                shown += min(len(gc_only), MAX_PER)
            if show_i.get():
                for c in ims_only[:MAX_PER]:
                    tree.insert("", "end", values=_row(c, "IMS"))
                shown += min(len(ims_only), MAX_PER)
            foot.config(
                text="顯示 %d / %d 筆（每組上限 %d，依 Δ 排序）。"
                     "綠底＝GC 與 IMS 兩軸都同意，那一列才算鑑定；"
                     "只有 GC 的一列代表「RI 對得上」，±5 的窗裡永遠有上百個化合物。"
                     % (shown, total, MAX_PER))

        ctl = ttk.Frame(win, padding=(10, 0))
        ctl.pack(fill="x")
        for var, txt in ((show_c, "GC+IMS (%d)" % len(combined)),
                         (show_g, "只有 GC (%d)" % len(gc_only)),
                         (show_i, "只有 IMS (%d)" % len(ims_only))):
            ttk.Checkbutton(ctl, text=txt, variable=var,
                            command=refresh).pack(side="left", padx=(0, 12))
        ttk.Button(ctl, text="關閉", command=win.destroy).pack(side="right")
        foot.pack(fill="x", padx=10, pady=(4, 10))
        refresh()

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
    def _start_similarity(self, reason=""):
        """把相似度算出來。已經在忙、或檔案太少，就講明原因並回頭。

        **代價要先講。** 峰都備好時這一步只是量測 + 相關係數（實測十幾秒）；
        但只要有檔還沒找峰就是每檔約 55 秒，那種等待必須先問過。
        """
        targets = list(self.files)
        if len(targets) < 3:
            self.status.config(
                text="%s。相似度至少要 3 個檔（目前 %d 個）——"
                     "每個區域要跨檔標準化，兩個檔時每一欄都會被壓成 (+1, −1)，"
                     "相關係數恆為 −1 而與資料無關。"
                     % (reason or "無法計算相似度", len(targets)))
            return False
        if self.busy:
            self.status.config(text="%s。正在%s，等它跑完再算相似度。"
                                    % (reason, self.busy))
            return False
        cost = L.scan_cost(targets)
        if cost["n_need_detect"]:
            if not messagebox.askyesno(
                    "要先找峰",
                    "算相似度需要每個檔的峰，其中 %d 個檔還沒找過"
                    "（約 %.0f 分鐘）。\n\n現在開始嗎？"
                    % (cost["n_need_detect"], cost["est_seconds"] / 60.0)):
                self.status.config(
                    text="%s。相似度還沒算——%d 個檔需要先找峰。"
                         % (reason, cost["n_need_detect"]))
                return False
        self.busy = "計算相似度"
        self._progress_open("計算相似度",
                            "跨檔比對 %d 個檔…" % len(targets))
        self.status.config(text="%s　→　計算相似度中（%d 個檔）…"
                                % (reason, len(targets)))
        threading.Thread(target=self._scan_worker, args=(targets,),
                         daemon=True).start()
        return True

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
        self._progress_open("掃描", "找峰並計算相似度（%d 個檔）…" % len(targets))
        threading.Thread(target=self._scan_worker, args=(targets,),
                         daemon=True).start()

    def _scan_worker(self, targets):
        # `except BaseException`：`SystemExit` 之類不是 Exception，只攔 Exception 的話
        # 背景執行緒會無聲死掉、UI 永遠等不到訊息（第二支應用踩過這個坑）。
        try:
            for i, m in enumerate(targets, 1):
                self.q.put(("status", "找峰 %d/%d：%s"
                            % (i, len(targets), os.path.basename(m))))
                areas2.detect_one(m, self._rules_for(m), use_baseline=False,
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
                targets, self.rules_config, active_only=False, verbose=False,
                rules_for=self._rules_for)
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
        # 判定走 `L.group_stats()`——與模式 2 的面板**同一個來源**，否則面板說
        # 「可以彙整」而按下去卻跳警告，使用者不知道該信哪一個。
        st = self._group_stats()
        if st["verdict"] == "mixed":
            if not messagebox.askyesno(
                    "這些看起來不像同一個標本",
                    "組內最低相似度只有 %+.2f（%s ↔ %s）。\n\n"
                    "票數只有在同一標本的重複測量之間才有意義——不同標本混在一起，"
                    "真實化合物會因為只出現在其中幾個檔而被判為未達門檻。\n\n"
                    "還是要繼續嗎？"
                    % (st["min_r"], os.path.basename(st["min_pair"][0]),
                       os.path.basename(st["min_pair"][1]))):
                return
        elif st["verdict"] == "outlier":
            # 指名道姓，使用者才改得動——只說「最低 0.7」等於要他整組重來
            if not messagebox.askyesno(
                    "有一個檔看起來不同組",
                    "%s 對其他成員平均只有 %+.2f，而其餘兩兩相比皆 ≥ %+.2f。\n\n"
                    "把它移出這一組通常會讓共識更乾淨。現在就要彙整嗎？"
                    % (os.path.basename(st["outlier"]), st["outlier_mean_r"],
                       st["rest_min_r"])):
                return
        self.status.config(text="彙整中…")
        self.busy = "彙整"
        self._progress_open("彙整", "比對 %d 個檔的化合物候選…" % len(grp))
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

            def _apply_user_choices(mea, pk):
                """套用使用者在熱圖上的勾選——**在建區域之前**。

                原本是跑完一次 `consensus_regions()`、對回傳的 `per_file` 套用選取、
                再跑第二次。那兩件事都沒有效果：第二次會重新偵測並產生**全新的** dict
                （`detect_cached()` 不共用物件），剛套上的選取整批被丟掉；而且
                `state.load()` 只寫 `user_active`，不呼叫 `apply_effective()` 的話
                `active` 根本不會變。兩個加起來＝**使用者關掉的峰照樣進共識**，
                而且畫面上完全看不出來。
                """
                state_mod.load(mea, pk)
                L.apply_effective(pk)

            areas, per_file, _rep = L.consensus_regions(
                grp, self.rules_config, min_fraction=frac, active_only=True,
                ri_calibration=ri_cal, progress=_prog, verbose=False,
                on_peaks=_apply_user_choices, rules_for=self._rules_for)
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
        # **先清 dirty 再切模式**：`on_mode_change()` 會看這個旗標決定要不要重算，
        # 順序反了就會在剛算完之後立刻再算一次。
        self._cons_dirty = False
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
            name = (top["name"] or "?") if top else "（找不到符合的化合物）"
            self.tree_cmpd.insert(
                "", "end", iid=str(i), text=name,
                # 維度標籤走 `logic.DIMENSION_LABEL`，與 ▶ 面板同一份對照表——
                # 兩個面板各自寫死遲早會不一致。
                values=("%d/%d" % (r["votes"], r["votes_total"]),
                        L.DIMENSION_LABEL.get(r.get("match_dimension"), "—"),
                        len(r["candidates"]),
                        self._fmt(r.get("dr"), 3),
                        self._fmt(r.get("ri"), 1),
                        self._fmt(r.get("rt"), 1)),
                tags=("t%d" % r["vote_tier"],))
        self.right_note.config(
            text="每一列＝熱圖上的一個位置（Drift rel = x 軸、RI = y 軸），"
                 "名稱是那個位置最可能的那一個。\n"
                 "票數＝幾個重複在這裡有選取的峰，講的是**這顆峰是不是真的**。"
                 "可能數＝這個位置有幾個化合物對得上，講的是**知不知道它是什麼**"
                 "——**數字越大越不確定**：1 表示只有一種可能，38 表示這個名字只是"
                 "38 種可能之中排最前面的那一個（雙擊展開看全部）。兩欄各講一件事，"
                 "票數再高也不會讓 38 變成答案。\n"
                 "維度 GC+IMS＝兩軸都同意（才算鑑定）；GC＝只有 RI 對上；"
                 "IMS＝只有漂移對上（漂移庫僅涵蓋 84 個化合物）；混合＝各檔不一致。"
                 "底色＝票數佔比，灰＝未達門檻（保留顯示不刪除）。"
                 + ("　" + self.ri_note if self.ri_note else ""))

    def show_candidates(self, _e=None):
        sel = self.tree_cmpd.focus()
        if sel == "":
            return
        r = self.consolidated[int(sel)]
        w = tk.Toplevel(self.root)
        # 標題就是這個位置的座標——熱圖的 y 軸是 RI，所以 RI 一定要在
        w.title("可能的化合物 — Drift rel %s / RI %s / RT %s s"
                % (self._fmt(r.get("dr"), 3), self._fmt(r.get("ri"), 1),
                   self._fmt(r.get("rt"), 1)))
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
        t = ttk.Treeview(w, columns=("s", "dim", "name", "cas", "ri", "d"),
                         show="headings")
        # **維度要逐候選顯示**，不能只在區域那一列給一個標籤：同一個區域裡，
        # 有些候選是兩軸都對上、有些只有 RI 對上，證據強度差很多。
        for col, txt, wid in (("s", "支持", 62), ("dim", "維度", 76),
                              ("name", "化合物", 300),
                              # 「庫」是縮寫，要先知道有「化合物庫」這回事才讀得懂
                              # ——使用者問過那是什麼。寫成「資料庫 RI」不必猜。
                              ("cas", "CAS", 100), ("ri", "資料庫 RI", 86),
                              ("d", "|ΔRI|", 72)):
            t.heading(col, text=txt)
            t.column(col, width=wid, anchor="w" if col == "name" else "center")
        t.tag_configure("combined", background="#e7f5e7")
        t.pack(fill="both", expand=True, padx=8, pady=6)
        for c in r["candidates"]:
            dim = c.get("dimension")
            t.insert("", "end", values=(
                "%d/%d" % (c["n_support"], c["n_files_with_peak"]),
                L.DIMENSION_LABEL.get(dim, "—"),
                c["name"] or "—", c["cas"],
                c["library_ri"] if c["library_ri"] is not None else "—",
                self._fmt(c["mean_abs_delta_ri"], 2)),
                tags=("combined",) if dim == "combined" else ())
        ttk.Label(w, foreground="#777", wraplength=820, justify="left",
                  text=("支持 = 有幾個重複在這個位置比中了這個化合物。　"
                        "資料庫 RI = 化合物庫（.ril / .iml）記載的參考值，"
                        "**不是你量到的**；你量到的是上一層表格的 RI 欄。　"
                        "|ΔRI| = 兩者相差多少（跨重複取平均），比對窗是 ±5。\n"
                        "Δ 小只表示它落在窗的中間，不表示它是唯一解——庫裡相鄰"
                        "化合物本來就差 80–92 RI，而重複之間只差 0.29（中位）。"
                        "要看「分不分得出來」請看上一層的可能數。\n"
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
                    self._progress_update(payload)
                elif kind == "error":
                    self.busy = None
                    self._progress_close()
                    self.status.config(text=payload)
                    messagebox.showerror("出錯了", payload)
                elif kind == "enable_scan":
                    self.busy = None
                    self._progress_close()
                elif kind == "busy_clear":
                    self.busy = None
                    self._progress_close()
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
                    self.busy = None
                    self._progress_close()
                    corr, files, n_areas, n_used = payload
                    self.corr, self.corr_files = corr, files
                    # 使用者可能在還沒有相似度時就先選了基準——算完要補挑一次，
                    # 否則他得再點一次基準才看得到結果。
                    if self.base:
                        self.set_base(self.base)
                    self._refresh_files()
                    self.status.config(
                        text="相似度已算出（%d 個共識區域，用了 %d 維）。"
                             "切到模式 2 點一個檔，建議同組就會整組帶進來。"
                             % (n_areas, n_used))
                elif kind == "regenerated":
                    self._refresh_files()          # 燈號要跟著變
                    self.status.config(text="%s 已重新產生。"
                                            % os.path.basename(payload))
                    if payload == self.current and self.mode.get() == MODE_HEATMAP:
                        self._show_loaded(payload)
                elif kind == "lib_ready":
                    self.ril, self.iml, self.lib_info = payload
                    self.lib_state = "ready"
                    self.status.config(text="化合物庫已載入（.ril %d 列、.iml %d 列）"
                                            % (len(self.ril), len(self.iml)))
                    self._autofill_matches()      # 載入前排隊的那一批補跑
                elif kind == "lib_fail":
                    self.lib_state = "unavailable"
                    self.status.config(text="化合物庫載入失敗：%s" % payload)
                elif kind == "matched":
                    path, results = payload
                    for key, res in results:
                        self.match_cache[key] = res
                    # 使用者可能已經切到別的檔——結果照樣進快取（下次切回來就免算），
                    # 但只有還停在同一個檔時才重畫，否則畫面會被舊檔的結果蓋掉。
                    if path == self.current:
                        self._apply_cached_matches(path, self.peaks)
                        self._refresh_peak_rows()
                elif kind == "consolidated":
                    self.busy = None
                    self._progress_close()
                    self._show_consolidated(payload["rows"], payload["ri_mode"],
                                            payload["assumed"])
        except queue.Empty:
            pass
        self.root.after(100, self._drain)


def main():
    root = tk.Tk()
    ConsensusApp(root)
    # Ctrl+C 不會被 Tk 接住，Python 會吐一整段 traceback，看起來像當掉。
    # 峰的選取是**每次切換就存**（`state_mod.save`），中斷不會丟掉。
    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("\n已中斷。峰的選取每次切換就存過了，不會丟。")
        try:
            root.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":
    main()

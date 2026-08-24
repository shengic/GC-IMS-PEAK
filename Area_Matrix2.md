# 區域強度矩陣 — 第二支應用（`areas2.py` / `main2.py`）

**Version: 1.0 — by Albert Sheng**

> 第二支應用有**自己的版本序列**，從 1.0 起算。與第一支應用的 3.x 無關——
> 兩者各自獨立演進，共用的只有底層模組。

> **這份文件是自足的。** 第二支應用刻意不修改 `status.md` / `README.md` / `CLAUDE.md`
> 或任何既有 `.py`，所以那些文件不會提到它。要了解第二支應用，讀這一份就夠。
> 之後若想讓新的 session 找得到它，在 `status.md` 加一行指標即可（尚未做）。

---

## 1. 為什麼有第二支應用

第一支應用（`main.py`）一次分析**一個** `.mea`：熱圖 → 自動找峰 → 逐峰比對化合物。

拆解 `GAS/Coffee-bean/rawbean-20260625.gasprj` 後發現 VOCal 的模型不同，而且差別是
**結構性**的，不是介面差異：

| | 第一支應用 | VOCal / 第二支應用 |
|---|---|---|
| 一次處理 | 1 個 `.mea` | 一整批（該專案 15 個） |
| 峰怎麼來 | 每個檔案各自偵測 | **同一組座標**套用到每個檔案 |
| 輸出 | 該檔的峰清單 + 候選化合物 | **區域 × 檔案 的強度矩陣**（57 × 15） |
| 命名 | 每顆峰都想找出是什麼 | **選配**：57 個區域只有 27 個有名字 |

**關鍵在為什麼要「同一組座標」**：各檔獨立找出來的峰清單彼此對不齊——A 樣品的第 12 顆
峰和 B 樣品的第 12 顆峰未必是同一個化合物。要比較 A/B/C/D/E 五組（每組 3 重複），必須
在**相同位置**上量每一個樣品。這也是為什麼矩陣裡**沒偵測到峰的格子照樣要有值**：
「這個樣品在這個位置沒有東西」本身就是一項資訊，缺值則不是。

### `.gasprj` 的結構（實測）

一個 `.gasprj` 是**跨多個 `.mea` 的專案**，不是每個 `.mea` 一份：

```
Project.Data.Entries[]   15 筆，每筆一個 .mea + Class（A 1-1 / B 1-2 …）+ 儀器設定
MeasAreas.Data[]         57 個區域定義（見下）
RI_Normalization         整個專案共用的一條 RI 校正曲線
Compounds[]              操作者標註過的化合物（.iml 列格式）
```

區域定義長這樣，而**兩個座標本專案都已經有了**：

```json
{"Name": "2-Butanone",
 "DriftCenter": 1.2748, "DriftRange": 0.0223, "DriftValType": "RipRel",
 "ElutionStart": 2524,  "ElutionEnd": 2595,   "ElutionValType": "SpecNum"}
```

- `RipRel` **就是**我們的 `drift_relative`（相對 RIP 的無因次比值）
- `SpecNum` **就是**我們的 `rt_index`（chunk 序號）

實測驗證：方框中心 × `(averages+1) × trigger_repetition` 能重現 VOCal 自己報的
`Rt[sec]`，22 個化合物比值 **0.992–1.005**。（順帶成為 `(averages+1)` 這條公式的第四
條獨立佐證。）

---

## 2. 隔離規則（**改動前必讀**）

**不修改任何既有 `.py` / `.md`。** 既有模組一律 import 後呼叫。新檔一律帶 `2` 尾綴：

```
areas2.py            邏輯，headless，可 CLI 執行
main2.py             Tk 介面
test/test_areas2.py  測試
Area_Matrix2.md      本文件
```

兩支應用共用 `results/` 與 `GAS/`，因此有三個真實的碰撞風險，各自的處理方式：

**1. 扣過基線的資料絕不寫回共用的 `.npz`。**
基線扣除只在記憶體內做。若寫回 `<base>.npz`，`main.py` 之後會把扣過基線的資料當成
原始值載入——圖與峰都變了卻沒有任何跡象。這正是本專案一再防的無聲污染（同
`mea_source` / `rt_axis_version` 那一類）。由
`test_baseline_never_written_back_to_npz` 把關。

**2. 不寫任何第一支應用的逐檔產物。**
`areas2.py` 以**函式呼叫** `peaks.detect_peaks()` / `select_from_maxima()`，**不**用
subprocess 跑 `peaks.py`——那支 CLI 會寫 `_peaks.json` / `_peaks.csv` / `_maxima.npz` /
`_bg.png` / `_bg.json` / `_overlay.png`，那些屬於第一支應用。第二支應用**讀**
`_maxima.npz`（快路徑）但絕不寫。

**3. 不寫任何東西進 `GAS/`。**
`resolve_calibrations_cached(..., use_sidecar=False)`——與 `main.py` 相同的呼叫——
所以不會產生或更新 `_folder_calibration.json`。`.mea` 與 `.gasprj` 一律唯讀。

**共用的是 `<base>.npz`**（原始強度矩陣）。共用是安全的：內容與哪支應用寫的無關，
而且省下重複約 480 MB。

### 產物

```
results/<base>_peaks2.json            逐檔找峰結果 + 參數
results/<folder>_areas2.json          區域定義 + 完整 provenance
results/<folder>_area_matrix2.csv     矩陣
```

---

## 3. 管線

**A. 逐檔找峰** — 沿用既有管線，一行未改。
`.npz`（缺就建）→ `load_surface` → 選配 AsLS 基線（**記憶體內**）→ `rip.find_rip` →
`detect_peaks`（R004/R006 仍在突出度門檻之前）→ `attach_coords` →
`attach_drift_relative` → 快取到 `<base>_peaks2.json`。

**B. 資料夾校正** — `resolve_calibrations_cached()`，與 `main.py` 相同。
四層 RI 來源全部照常運作，包含 `batch_own_std`（6 點酮校正）與 `vocal_project_table`。

**C. 共識區域**（唯一的新演算法）
把所有檔案的峰匯集後在 `(drift_relative, retention_s)` 空間群聚。

> **為什麼用秒而不是 `rt_index`**：同批各檔 chunk 數不同（實測 Coffee-bean：20413 ×10、
> 20414 ×4、另一支只有 18372），`rt_index` 不能直接跨檔比；而 `rt_step` 相同、
> `drift_relative` 又已用各檔自己的 RIP 正規化過，兩者都是跨檔可比的物理量。

- 依突出度降冪貪婪群聚，容差 `Δdrift_rel ≤ 0.03`、`Δrt ≤ 10 s`
  ——**取自 VOCal 自己畫的方框中位數**（DriftRange 0.031、寬度 19.4 s），不是憑感覺選的
- **共識過濾**：只留出現在 ≥ `min_files`（預設 2）個檔案的群集。單檔雜訊因此被擋掉，
  而真化合物在重複樣品裡本來就會重複出現——這是跨檔相對於逐檔真正多出來的資訊
- **方框大小由資料決定**：群集實際範圍 + 邊距，下限 `±0.02` / `±8 s`。這樣自然吃得下
  該化合物在這批樣品裡的保留時間漂移，不必手調（VOCal 的方框畫得寬也是同一個理由）

**D. 量測** — 每個區域 × 每個檔案。
`volume`（方框內高於 floor 的總和）、`max`、`mean` 三個都算。
**量不到回 `None` 不是 `0`**——「沒量到」與「量到零」在比較群組時意義完全不同。

**E. 命名** — `match.match_all()` 比對區域中心。比不到就維持 `area N`。
由 `.gasprj` 匯入時**保留 VOCal 的名字**，我們的比對另存 `matched_name` / `matched_cas`
——那是操作者的判定，蓋掉它會讓「拿 `.gasprj` 對照」失去意義。

---

## 4. 用法

```bash
# CLI：整批跑完並寫出矩陣
python areas2.py "GAS/Coffee-bean"

# 改用 VOCal 畫好的區域（逐格對照用）
python areas2.py "GAS/Coffee-bean" --from-gasprj "GAS/Coffee-bean/rawbean-20260625.gasprj"

# 常用旗標
#   --metric volume|max|mean   CSV 寫哪個（三個都會算並存進 JSON）
#   --no-baseline              停用 AsLS（預設啟用；體積積分需要它）
#   --min-files 2              區域至少要在幾個檔案出現
#   --drift-tol / --rt-tol     群聚容差
#   --limit N                  只跑前 N 個檔案（試跑）

# UI
python main2.py
```

### UI（`main2.py`）逐項

| 控制項 | 作用 |
|---|---|
| **1. Browse batch folder** | 選含 `.mea` 的資料夾。**注意**：預設開在 `GAS/`，而 `GAS/` 底下只有子資料夾沒有 `.mea`——選到它會明確提示並列出該去哪個子資料夾。 |
| **2. Run batch** | 直接開跑，不再有確認對話框。 |
| **Stop** | 在目前這個檔案跑完後停下。 |
| **files: all / 1 / 2 / 5** | 只跑前 N 個檔案。**第一次先用 2**，一兩分鐘就看得到結果。 |
| **Baseline (AsLS)** | 扣掉隨溫度上升的傾斜基線；體積積分需要它（每檔 +16 秒）。 |
| **metric** | 每格顯示什麼：`volume` 總和（定量）· `max` 峰高 · `mean` 平均。三個都會算，切換不需重算。 |
| **Compare with .gasprj areas** | **預設關閉。** 改用 VOCal 畫好的方框，僅供對照驗證——本專案的目標是取代 VOCal，不是依賴它。 |
| **view: summary / all files** | **摘要**（預設）＝每個實驗組一欄（該組平均）；**all files**＝逐檔一欄。 |
| **Fast: skip peak detection** | 跳過找峰直接量測，**快約 50 倍**。只在勾了上一項時可用（見下）。 |
| **Export CSV** | 另存目前 metric 的矩陣。跑完也會自動寫一份到 `results/`。 |

#### 為什麼預設是「摘要」視圖

18 個檔案逐欄擠在一起讀不了，而這批的設計本來就是 **A/B/C/D/E 五組 × 3 重複**——要比
的是組與組。摘要視圖每組一欄（該組平均），11 欄；**逐檔完整數值一格不少地在雙擊列開
的視窗裡**。

分組是從 `.gasprj` 的 `Class` 欄取**第一個 token** 得到的：該欄存的是每個重複樣品的
代號（`A 1-1` / `A 1-2` / `A 1-3`），照字面分會變成 13 組、每組 n=1，完全失去比較的
意義。取第一個 token 才還原成那五組。

#### Fast 模式（跳過找峰）——什麼時候該用

實測單檔耗時分佈：

| 步驟 | 秒 | 佔比 |
|---|---|---|
| `compute_prominence`（union-find，在 `peaks.py`） | 55.4 | **74.9%** |
| AsLS 基線（stride 8） | 16.8 | 22.7% |
| 平滑／載入／floor／find_rip | 1.8 | 2.4% |
| **合計** | **73.9** | |

找峰佔了四分之三，而**方框若來自 `.gasprj`，找峰只被用來填 `n_det` 欄**——方框本身
完全不需要它。跳過之後單檔 74 秒 → **1.5 秒**（實測 15 檔 12 秒）。

兩道防呆：搭配共識模式會**直接報錯**（那時候區域正是從偵測到的峰長出來的，靜靜回一個
空矩陣會糟得多）；跳過時 `n_det` 記為 **`None` 不是 `0`**——「沒去看」與「每個樣品都
沒偵測到」是兩種截然不同的陳述。

| 你要什麼 | 設定 | 15 檔 |
|---|---|---|
| 先看到矩陣就好 | `.gasprj` + Fast + 不扣基線 | ~25 秒 |
| 要定量的體積 | `.gasprj` + Fast + 扣基線 | ~5 分鐘 |
| 用自己的區域（不靠 VOCal） | 共識 + 扣基線 | ~18 分鐘（僅首次） |

**左側檔案清單會即時上色**，看得出現在算到哪一個檔案、卡在哪一個階段：
琥珀＝偵測中（慢，約 83 秒）· 淡藍＝已偵測 · 橘＝量測中（快）· 綠＝完成。
兩個階段分開標色是有理由的：只用一種「處理中」顏色的話，第二輪量測會看起來像
又從頭跑了一次。用 `files: N` 限制檔案數時，**沒被處理到的檔案不會被塗綠**。

**Progress log 面板**（右下）顯示執行中的所有訊息，**包含 `peaks.py` / `readGAS.py`
自己 print 的內容**——那才是最花時間的部分。狀態列另外每秒更新經過秒數，因為
union-find 那一步要跑約 50 秒且中間完全不輸出，沒有計時器會看起來像當掉。

矩陣表格：欄標題帶 `Class`（同組重複樣品並排），有名字的區域淺綠底，雙擊任一列看
該區域的座標、命名與逐檔數值。空白格 = 沒量到（不是 0）；`n_det` = 有幾個檔案在那裡
真的偵測到峰。

**成本**：冷跑約每檔 2 分鐘（讀 13 s + 偵測 83 s + 基線 16 s + 量測 3 s），
Coffee-bean 15 檔約 30 分鐘；之後走快取近乎即時。`.npz` 約 32 MB/檔。

---

## 5. 實測結果（Coffee-bean，2 檔試跑）

```
矩陣：57 區域 × 2 檔案
  RI  : batch_own_std   ⚠ RI 可能為極性管柱尺標…（既有的 provenance 警語照常傳遞）
  K0  : standard_based
  區域: imported_from_gasprj（命名 30/57）
  格子: 114/114 有值
```

**一項意外的交叉驗證**：我們用 6 點酮校正算出的區域 RI，與 VOCal 自己曲線給的值高度
一致——`1- butanol` 1144.1 vs 1144.9、`2-Butanone` 903.2 vs 909.6、
`ethyl acetate-M` 894.6 vs 900.2。兩條完全獨立的校正路徑差在 ~6 RI 以內。

---

## 6. 1.0 開發期間，使用回報修掉的問題

| 症狀 | 真正的原因 |
|---|---|
| 按 Run batch 沒反應 | 檔案選擇器預設開在 `GAS/`，而它底下只有子資料夾、沒有 `.mea` → 0 個樣品 → 按鈕靜靜變灰。現在會明講並列出該去哪個子資料夾。 |
| 矩陣視窗永遠空白、也沒有錯誤 | `build_matrix` 原本 `raise SystemExit`（`BaseException`），背景執行緒的 `except Exception` 接不到 → 執行緒無聲死掉、佇列永遠空著、UI 一直等。改用 `NoSamplesFound`（Exception），worker 改攔 `BaseException`。 |
| 看不出程式有沒有在跑 | 訊息面板只接得到 `areas2` 自己的十來行，而最花時間的 `peaks.py` 找峰用的是普通 `print()`。現在**攔 stdout**，底層訊息全部看得到；狀態列另外每秒更新經過秒數（union-find 那 50 秒完全不輸出）。 |
| 欄位標題全是 `?` | 兩個原因疊在一起：(a) `Class` 只在「用 .gasprj 區域」時才讀，共識模式整組遺失 → 現在無論區域從哪來都會讀；(b) 標題寫成 `f"{class}
{檔名}"`，而 ttk.Treeview 的 heading **只畫一行**，第二行整個看不見。 |
| 字太小、欄位太多讀不了 | 字型放大（`ttk.Style` 設 `Treeview` / `Treeview.Heading`，因為 Treeview 不吃 `font=`），並加上摘要視圖。 |
| 批次跑很久 | 見上面的耗時分佈與 Fast 模式。 |

> **踩過的坑（與本專案無關但值得記）**：暫存資料夾裡放了名為 `grp.py` 的臨時腳本，
> 而 `pathlib` 會 `import grp`——於是 scipy 的匯入鏈在深處炸出 `AssertionError`，
> 看起來像 scipy 壞了。**不要用標準函式庫的模組名當臨時檔名**。

---

## 7. 已知限制

- **沒有跨檔保留時間對齊**（retention time alignment）。目前靠方框夠寬吸收漂移，
  與 VOCal 手畫寬框的作法相同。若某批漂移大到超出方框，需要真正的對齊演算法。
- **`Class` 標籤與檔名在 Coffee-bean 有 3/15 不一致**（例如 `E_1_2` 被標成 `E 1-1`、
  `C_1_2` 標成 `C 1-1`）。看起來是手打的分組標籤漂掉了。本應用照抄 `.gasprj` 的值，
  不去修正——那是原始資料的問題，靜默改掉會更糟。
- **`volume` 用的 floor 是該檔的第 85 百分位**，與偵測階段同一條線。不是嚴謹的峰體積
  積分（沒有做峰形擬合或邊界判定），是方框內的簡單加總。
- **K0 沒有用在區域命名上**：K0 需要逐峰的 `dt_index`，區域層級沒有單一 `dt_index`，
  所以命名走 RIPrel 漂移那條路。
- RI 的既有疑慮（管柱極性）原樣傳遞到本應用的 provenance，不會因為換一支應用就消失。

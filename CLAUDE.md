# GC-IMS-PEAK — 專案指引

讀 G.A.S. FlavourSpec® 的 `.mea`,偵測 2D 熱圖上的峰,再比對化合物。

## ⚠ 這個 repo 裡有**兩支應用**,先確認在講哪一支

帶 `2` 尾綴的檔案**全部屬於第二支應用**,不是第一支的新版本。兩支各有自己的版本序列。

| | 第一支 | 第二支 |
|---|---|---|
| 進入點 | `main.py` | `main2.py` |
| 版本 | 3.x | 1.x |
| 一次處理 | **1 個** `.mea` | **一整批** |
| 做什麼 | 熱圖 → 找峰 → 逐峰比對化合物 | 在所有檔案上量**同一組區域** → 區域 × 檔案的強度矩陣 |
| 邏輯模組 | `peaks` / `calibration` / `identify` / `match` … | `areas2.py`(呼叫左邊那些,**不修改**) |
| 測試 | `test/`(194) | `test2/`(44) |
| 進度 | `status.md` | `status2.md` |

第二支是**新增**的,不取代第一支;它把既有模組當函式庫用。要動第二支之前先讀
`status2.md` 的「隔離規則」——它的產物一律帶 `2`,而且刻意不寫任何第一支的檔案。

## 開始新工作前先讀 status.md / status2.md

**進度權威**:第一支看 `status.md`,第二支看 `status2.md`。兩份都記錄目前做到哪、
已決定什麼、還卡在什麼。本檔只放「每次都需要、且查起來昂貴」的少數事實;細節一律
去那兩份,不要在這裡複製。

文件分工:

| 檔案 | 角色 |
|---|---|
| `status.md` | **第一支的進度與交接**(先讀這份) |
| `GC-IMS_Identify_Workflow.md` | 化合物比對的**設計權威**,以 draft.N 推進 |
| `GC-IMS_Pipeline_Implementation.md` | 實作細節、產物格式、CLI 用法 |
| `UI.md` | 第一支的 Tk UI 規格 |
| `ketone_RI_provenance.md` | STD 的身分、RI 數值來源與可信度 |
| `GC-IMS_Peak_Finding_Workflow.md` | 早期架構藍本(**影像模式,前提已不成立**),僅供方法論參考 |
| `status2.md` | **第二支的進度與交接** |
| `README2.md` | 第二支的用法 |
| `Area_Matrix2.md` | 第二支的設計 + `.gasprj` 格式解析 |

## 軸向約定 —— 最常搞混的一件事

**講到軸一定要寫明是哪一條**,不要只說「橫的/縱的」:

| 軸 | 儀器 | 量 |
|---|---|---|
| **y(縱)** | GC | 保留時間 RT → 保留指數 **RI** |
| **x(橫)** | IMS | 漂移時間 → **drift_relative**(除以 RIP) |

`RI` 屬於 GC、屬於 y 軸;`drift_relative` 屬於 IMS、屬於 x 軸。

## 容易踩到的不變量

這幾條是踩過坑才建立的,改動前先確認理由是否仍成立:

- **保留時間軸**:`rt_step_ms = (averages + 1) × trigger_repetition`。少了 `+1`
  會讓整條 RT 軸短 16.7%,而且**不會有任何錯誤訊息**。產物帶
  `rt_axis_version`(現為 2),載入舊產物會警告。
- **RI 不是單一比例**:它是 `log10(RT)` 上的分段線性內插。全域斜率約
  804 RI/decade 只能當摘要;用單一直線換算最大偏差 14.5 RI,超過 ±5 的比對容差。
  換算一律走 `calibration.make_rt_to_ri()`。
- **強制規則 R004/R006 必須在突出度門檻「之前」套用**。門檻是相對值,RIP 會把它
  墊高數倍並誤殺真峰。`test/test_select_from_maxima.py` 會擋住這個回歸。
- **峰的選取狀態以 `(rt_index, dt_index)` 為鍵**,不是 `peak_id` —— 後者是基準集
  內的突出度排名,規則參數一改就重新編號。
- **`.npz` 帶 `mea_source`**:RI 校正靠「原始 `.mea` **所在資料夾**」解析。指錯會讓
  校正靜默失效、y 軸無聲退回保留時間。
- **RI 有四層來源,`.gasprj` 是其中一層**:STD → 該資料夾的 `.gasprj`
  (`RI_Normalization` 區塊)→ registry → 無。所以 **`.gasprj` 是輸入資料,不是
  VOCal 的殘留檔**,不得刪除或搬移。每個峰帶 `ri_mode` 標明實際用了哪一層。
- **選庫的極性跟著「實際在用的 RI 尺標」走,不是表頭的管柱極性**。比對是拿峰的 RI
  對庫的 RI,兩者不同尺標時,±5 命中的是「另一個化合物的 RI 恰好等於本峰的 RI」
  ——錯得穩定而非隨機。由 `library.detect_ri_scale_polarity()` 依 library_data 投票
  判定;判不出來就退回表頭,不猜。
- **沒有 RI 時 GC 比對會退到保留時間**,而保留時間不跨儀器/管柱/方法轉移。欄位標題
  必須跟著變成 `GC (RT s)` —— 把秒數掛在 RI 名義下是這裡踩過的坑。

## 環境

專案根目錄至今搬過四個磁碟機(`J:` → `K:` → `F:` → 現在的 `C:\GC-IMS-PEAK`),寫死
絕對路徑的指令每次都要跟著改而且會無聲失敗,所以一律用**相對於專案根目錄**的寫法
(PowerShell 與 bash 皆可直接執行):

```bash
.venv/Scripts/python.exe -m pytest -q           # 全套 238 項(test/ 194 + test2/ 44)
.venv/Scripts/python.exe -m pytest test/ -q     # 只跑第一支應用
.venv/Scripts/python.exe -m pytest test2/ -q    # 只跑第二支應用
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

**測試分兩個根目錄**：`test/` 是第一支應用、`test2/` 是第二支應用。`pytest.ini` 的
`testpaths` 讓**光打 `pytest` 就兩邊都收**——這是防呆：舊文件寫的是 `pytest test/`，
照那個跑會靜靜漏掉 44 項而毫無徵兆。

`results/` 已 gitignore。**`GAS/` 底下的 `.mea` 與 `.gasprj` 任何程式都不得修改或
刪除**(後者存著 RI 校正表,見上)。

## 工作方式

- UI 改動分小批交付,每批請使用者實跑 `python main.py` 回報。
- 不要硬寫資料路徑,用 `library.resolve_data_dir()`。
- 產出數字時一併帶 provenance 標記(`k0_mode` / `ri_mode` / `assumed_unverified`),
  不確定的值要能被下游辨識,不靜默產生看起來合理的數字。

# GC-IMS-PEAK — 專案指引

桌面工具:讀 G.A.S. FlavourSpec® 的 `.mea`,偵測 2D 熱圖上的峰,再比對化合物。

## 開始新工作前先讀 status.md

**`status.md` 是進度權威**,記錄目前做到哪、已決定什麼、還卡在什麼。本檔只放
「每次都需要、且查起來昂貴」的少數事實;細節一律去 status.md,不要在這裡複製。

文件分工:

| 檔案 | 角色 |
|---|---|
| `status.md` | **進度與交接**(先讀這份) |
| `GC-IMS_Identify_Workflow.md` | 化合物比對的**設計權威**,以 draft.N 推進 |
| `GC-IMS_Pipeline_Implementation.md` | 實作細節、產物格式、CLI 用法 |
| `UI.md` | Tk UI 規格 |
| `ketone_RI_provenance.md` | STD 的身分、RI 數值來源與可信度 |
| `GC-IMS_Peak_Finding_Workflow.md` | 早期架構藍本(**影像模式,前提已不成立**),僅供方法論參考 |

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
- **`.npz` 帶 `mea_source`**:RI 校正靠「原始 `.mea` 所在資料夾裡的 STD」解析。
  指錯會讓校正靜默失效、y 軸無聲退回保留時間。

## 環境

```bash
"F:/GC-IMS-PEAK/.venv/Scripts/python.exe" -m pytest test/ -q     # 全套測試
"F:/GC-IMS-PEAK/.venv/Scripts/python.exe" -m pip install -r requirements.txt
```

`results/` 已 gitignore。`.mea` 原始檔任何程式都不得修改或刪除。

## 工作方式

- UI 改動分小批交付,每批請使用者實跑 `python main.py` 回報。
- 不要硬寫資料路徑,用 `library.resolve_data_dir()`。
- 產出數字時一併帶 provenance 標記(`k0_mode` / `ri_mode` / `assumed_unverified`),
  不確定的值要能被下游辨識,不靜默產生看起來合理的數字。

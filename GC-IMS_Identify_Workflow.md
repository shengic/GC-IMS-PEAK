# GC-IMS 化合物比對工作流程 —— 第一版草稿

**Version: draft.13 — by Albert Sheng**
**狀態：草稿，持續更新中，尚未定案**

本文件是 `GC-IMS-PEAK` 專案在 `readGAS.py` → `peaks.py` 之後，銜接「化合物比對」這一段的規劃文件。內容依據對 VOCal（G.A.S. 官方軟體）多支 plugin jar 反編譯逆向出的邏輯整理，逐項標明來源可信度：

- **[VOCal 反編譯驗證]**：原始碼裡直接看到、或用真實資料測試過，可信度高
- **[VOCal 資料格式驗證]**：從 `.gasprj`/`.iml`/`.ril` 實際資料檔案結構反推，非原始碼但有實際資料佐證
- **[設計沿用，非逐字反編譯]**：精神上參考 VOCal 的做法，但具體實作是本專案自己設計
- **[待決策]**：需要使用者提供資料或做決定才能繼續

---

## 現狀基準

```
readGAS.py  → .mea 解析 → intensity 矩陣 + drift_ms/retention_s 軸 → .npz/.csv/熱圖
peaks.py    → .npz → 突出度找峰 → peak_id/rt_index/dt_index/retention_s/drift_ms/intensity/prominence/flatness
main.py     → Tkinter UI，串起上面兩支 + peak_with_number.py 疊字顯示
```

這條線輸出的 `_peaks.json` 是接下來所有新模組的共同輸入。**[本輪確認，非「不動」]**：這條線本身有一個確認過的缺口需要修改，見下方，不是完全不動的既有基礎。

### 缺口：`.npz` 遺失 metadata，需新增 `_meta.json` 側車檔

**[本輪使用者提出並確認的具體修改]**

**問題**：`readGAS.py` 的 `read_mea()` 回傳 `(data, header, axes)`，但 `export_npz()` 只存 `intensity`/`drift_ms`/`retention_s` 三項，**完整的 `header` 字典（樣品名稱、機型、管柱資訊等）沒有存進 `.npz`**。連帶地，`peaks.py` 的 `load_surface()` 走 `.npz` 分支時，回傳的 `meta` 只有 `{"source": path}`，`machine`/`sample` 欄位是空的（相對地，走 `.mea` 分支時因為重新讀了表頭，這兩個欄位是有值的——同一支程式對兩種輸入的待遇不一致）。

**解法：`<檔名>_meta.json` 側車檔**，跟 `<檔名>.npz` 同資料夾、同 base name。以下是**本輪用真實 `.mea` 檔案驗證過的實際欄位範例**（取代先前用猜測值寫的版本）：

```json
{
  "source_mea": "260625_141215_STD.mea",
  "sample": "STD",
  "machine_type": "FlavourSpec®",
  "n_rt": 20413,
  "n_dt": 3150,
  "sample_rate_khz": 150.0,
  "chunk_averages": 6.0,
  "dt_step_ms": 0.006667,
  "rt_step_ms": 126.0,
  "status": "doubtful",
  "gc_column_raw": "FS-SE-54-CB-1, L: 30.00m, ID: 0.53mm, FT: 0.50µm, POLARITY: np",
  "drift_gas": "nitrogen",
  "header_raw": { "...": "表頭所有 key=value，原樣照存，不篩選" }
}
```

**`header_raw` 完整保留原始表頭，不做篩選**——即使目前還不確定用得到哪些欄位（例如懷疑存在但未證實的 `POLARITY:`，見第三階段），先整包存下來，避免將來發現有用的欄位時得回頭重新讀 `.mea`，違背「不用回去讀 mea 檔案」這個側車檔存在的目的。

**具體修改點**：

1. `readGAS.py` 的 `read_mea()` 內部，`axes` 字典目前只放算好的 `dt_step_ms`/`rt_step_ms`，**應該把原始的 `sample_rate_khz`/`trig_rep_ms`/`averages` 三個數字也放進 `axes`**，供 `export_meta_json()` 直接取用，不用反推
2. 新增 `export_meta_json(header, axes, mea_path, path)` 函式，跟 `export_npz()` 平行呼叫，**兩者必須同時產生，不能只做一半**
3. `peaks.py` 的 `load_surface()` 的 `.npz` 分支，補讀同 base name 的 `_meta.json`（若存在），**整包放進 `meta["source_meta"]`，不要逐一挑欄位**（避免重蹈 `write_json()` 目前只挑 `machine`/`sample` 兩個欄位、future-proof 不足的問題）
4. `peaks.py` 的 `write_json()` 對應調整：把 `meta.get("source_meta")` 整包寫進 `_peaks.json`，取代目前逐一挑 `machine`/`sample` 的做法

**相容性**：`_meta.json` 若不存在（例如已轉換的舊 `.npz`），`load_surface()` 用 `os.path.exists()` 判斷後優雅跳過，`meta["source_meta"]` 缺席但不報錯——舊檔案仍可運作，只是 `_peaks.json` 品質較低（無法自動判斷極性等後續資訊），不強迫全部重轉。

**與第九階段批次轉檔的關聯**：`batch_convert.py`「跳過已轉換檔案」的判斷邏輯，須同時檢查 `.npz` 與 `_meta.json` **兩個檔案都存在且夠新**，缺一即視為未轉換完整，兩個要一起重新產生，避免產生「有 `.npz` 沒有對應 `_meta.json`」的半套產物。

### 已除錯並修復：`plot_heatmap()`／`write_overlay()` 在真實尺寸資料上會 OOM 崩潰

**[本輪實際除錯並已修復，非理論推測]**：用使用者提供的真實 `.mea` 檔案（`260625_141215_STD.mea`，矩陣 `20413×3150`）實際跑過 `readGAS.py`／`peaks.py` 全部功能，發現並定位一個會重現的當機：`readGAS.py` 的 `plot_heatmap()` 與 `peaks.py` 的 `write_overlay()` 兩處，皆在 `fig.savefig()` 這一步被系統 OOM killer 砍掉（returncode 137）。

**根本原因**：兩處計算色階範圍（`vmin`/`vmax`）時都有先對矩陣做子取樣（`sub = img[::N, ::N]`），**但真正丟給 `ax.imshow()` 光柵化的矩陣是完整未降採樣的原始資料**。輸出圖片在預設 `dpi=150`、`figsize=(8,9)` 下實際只有約 `1200×1350` 像素，而矩陣本身有 6400 萬個資料點（47 倍於輸出所需）——multiplot 的 Agg 後端在 `savefig()` 光柵化整個陣列時記憶體暴增。已用 `resource.getrusage` 實測逐步定位：`savefig()` 呼叫前記憶體僅約 739MB，問題確實發生在這一步，不是資料讀取或 `imshow()` 呼叫本身。

**修復方式**：兩個檔案各自新增 `_downsample_for_display(img, figsize, dpi, margin=1.5)` 函式（兩處各自維護一份，避免引入跨檔案 import 依賴），在丟進 `ax.imshow()` 之前，先把矩陣降到略高於輸出解析度（留 1.5 倍餘裕避免鋸齒）。`extent`（座標軸物理範圍）與 `peaks.py` 疊加的紅圈座標（`ax.scatter()`）皆維持使用原始未降採樣的軸資料，不受影響——只有背景熱圖本身的取樣密度改變，圖片內容與精確度不受影響。

**驗證結果**：修復後同一份真實資料，`plot_heatmap()` 存圖僅需約 5 秒（原本會直接崩潰無法完成）；完整跑過 `read_mea → detect_peaks → attach_coords → write_overlay` 全流程也順利跑完，`detect_peaks()` 找到 276,713 個原始局部極大，經預設篩選（`floor_pct=85`、`prom_frac=0.02`）後剩 47 個峰，全程 84.8 秒。

**兩點連帶發現，記錄供後續參考**：
1. **`detect_peaks()` 在真實尺寸資料上耗時約 83 秒**（此前只用較小範例資料測試過，沒有這個量級的真實耗時參考值）——若第十階段規劃「找完峰立刻在背景自動跑比對」，需將這個等級的基礎找峰時間也一併納入使用者體感等待時間的評估，不能只算比對本身的時間。
2. **276,713 個原始局部極大，經預設篩選後只剩 47 個**——這印證了第七階段規則引擎的設計方向是對的（候選數量差距懸殊，僅靠使用者手動逐一取消勾選不可行），但也代表**若規則引擎的篩選比預設參數更寬鬆，UI 端仍可能需要面對數千甚至數萬候選的極端情況**，第八階段的 Canvas 效能設計不能只以「47 個」這種樂觀情況為基準。

### 已發現但尚未修復：`peak_with_number.py` 有同一個 OOM bug

**[本輪發現，未修復]**：畫 UI mockup 時盤點既有檔案，發現 `peak_with_number.py`（疊峰編號顯示）的 `write_overlay_numbered()` 同樣是 `ax.imshow(intensity, ...)` 直接丟未降採樣的完整矩陣，跟上面修好的兩處是**同一個 bug、同一個成因**，只是這輪除錯時漏掉了這一支。**[本輪修正]**：此檔案的角色**不是**第八階段互動主畫面的來源（見第八階段「架構修正」一節，兩者架構互不相容），而是提供給第十一階段「Generate Report」匯出用的靜態標號圖——優先度因此不像先前筆記講的那麼高，但仍是待修的既有 bug。**待辦**：套用同樣的 `_downsample_for_display()` 修法，動工時一併處理。

---

## 第一階段：RIP 正規化

**建議模組名稱**：`rip.py`

**[現況確認，本輪使用者提出]**：目前 `peaks.py` **尚未執行正規化**——輸出的 `drift_ms` 是原始毫秒值，沒有 `drift_relative` 欄位。本階段描述的邏輯目前只存在於這份文件與規劃中，**尚未寫進 `peaks.py`**。這件事在 `R004`（排除 RIP 柱狀帶）與 `R005` 的 `steepness_ratio`（用到 `prominence`，不受影響）之中，`R004` 依賴 `drift_relative`，因此**在 `rip.py` 實際完成串接、`_peaks.json` 真的有 `drift_relative` 欄位之前，`R004` 無法運作**，套用規則庫時需要能偵測並提示這個相依性缺失，而不是靜默略過或報錯中斷。

1. **輸入**：`readGAS.py` 產出的 intensity 矩陣（或直接吃 `.npz`）
2. **核心函式** `find_rip(intensity, start=200)`：
   - 取第一列（RT=0，`intensity[0, :]`）
   - 跳過前 `start` 個取樣點
   - 取剩餘部分的最大值索引
   - **[VOCal 反編譯驗證]** 依據 `CleanMEA.getRIP()`（`MEA2Images.jar`）
3. **[待決策]** `start=200` 這個跳過值是否要依實際儀器取樣率等比例調整
4. **輸出**：`rip_index`（原始取樣點索引）
5. **串接點**：對每個峰的 `dt_index` 多算一個 `drift_relative = dt_index / rip_index` 欄位，寫回 `_peaks.json`
6. **[本輪確認的重要限制] `rip_index` 必須逐檔案獨立計算，不可快取或共用**：RIP 的實際物理位置會因溫度/壓力等量測條件在不同批次間漂移，即使同一批樣品，每個 `.mea` 也要各自跑一次 `find_rip()`。**批次處理時尤其要注意**：`batch_convert.py`／未來的批次比對腳本，`rip_index` 這個變數必須放在每個檔案的迴圈內部重新計算，寫在迴圈外算一次沿用，會讓所有檔案的漂移軸比對出現系統性偏移，是容易犯但後果嚴重的錯誤。
7. **[本輪確認]** `sample_rate_khz`（換算 `rip_index` → 實際毫秒值時要用）是**從每個 `.mea` 表頭讀出來的**（`readGAS.py` 的 `hnum(header, "Chunk sample rate", 150.0)`），不是全域固定值，預設值 150 只是常見情況，不可假設所有檔案都相同——`InstrumentConstants`（第二階段）等後續模組引用取樣率時，須逐檔案讀取實際值，不能套統一常數。

---

## 第二階段：K0 換算

**建議模組名稱**：`rip.py`（併入）或獨立 `dt_convert.py`

**[本輪重大設計調整，且明確標注：此問題尚未完全解決，以下是架構設計，不是已驗證的最終答案]**

### 問題重新定位：不是「讀對表頭欄位」，是「機器差異能否被消除」

上一輪確認：K0 公式裡的 `L`/`T`/`P` 即使全部從表頭讀對，讀到的也只是**標稱值（nominal）或控制器設定值**，不是這台機器的實際物理狀態——這代表即使第一版設計（直接讀表頭四個常數代入公式）技術上能跑，算出來的 K0 仍帶有這台機器特有的殘留誤差，跟資料庫（多半是別的實驗室、別的機台量出來的）比對時可信度有限。**這件事的正確解法，需要用已知 K0 文獻值的標準品在這台機器上實測校準，而不是依賴表頭讀值**——但這需要使用者提供校準標準品資料，目前沒有，所以以下是**設計好、但尚未有真實資料驗證過的架構**。

### 雙模式設計：`calibration_profile.json`

新增一個獨立於單一 `.mea`／`.npz` 之外的設定檔，綁定「儀器＋管柱＋方法」這個組合，同組合下所有樣品共用：

```json
{
  "profile_name": "FlavourSpec_5H4-00123_FS-SE-54-CB-1_COFFEE-40RAW",
  "k0_calibration": {
    "mode": "standard_based",
    "instrument_constant": 12345.6,
    "calibrated_from": {"compound": "2,4,6-trimethylpyridine", "known_k0": 1.85, "date": "2026-06-01"}
  }
}
```

`mode` 三種可能值：

1. **`"standard_based"`（首選）**：已用已知 K0 標準品在這台機器上實測校準，反推出一個合併的 `instrument_constant`（把 `ah × L²` 整團當一個數字解出來，不再分別依賴 `L`/`T`/`P` 個別是否準確）：
   ```python
   def k0_from_instrument_constant(dt_raw, sample_rate_khz, instrument_constant, U):
       t_d_s = (dt_raw / sample_rate_khz) / 1000.0
       return instrument_constant / (t_d_s * U)
   ```
   優勢：完全不需要確認六個 `Start temp` 哪個對應漂移管、`EPC` 壓力是否等於管內實際壓力——這些個別欄位的不確定性被「整體校準」一次吸收掉。**[待決策，卡住這條路的關鍵前提]**：需要使用者提供或執行一次已知 K0 標準品的實測。

2. **`"raw_parameters"`（退路）**：沒有校準標準品時，直接讀表頭四個常數代入原公式（即第一版設計）：
   ```python
   def k0_from_raw_params(dt_raw, sample_rate_khz, L, T, P, U):
       t_d_s = (dt_raw / sample_rate_khz) / 1000.0
       ah = (273.0 / (273.0 + T)) * (P / 1013.0)
       K0 = ah * L**2 / (t_d_s * U)
       return 1.0 / K0
   ```
   **[待決策，本輪部分澄清，未解決]**：`T`/`P` 該讀哪個表頭欄位仍不確定——表頭有 `Start temp 1` 到 `Start temp 6` 六個溫度欄位（本輪實測分別為 45/60/80/80/45/off），哪一個對應漂移管本身尚未確認；`'Start pressure EPC IMS'` 是否等於管內實際壓力也未確認。`L`（`nom Drift Tube Length`）、`U`（`nom Drift Potential Difference`）本輪已確認可直接從表頭讀取。此模式算出的 K0 帶有已知但無法量化的殘留誤差。

3. **`"unavailable"`**：兩者皆無時，K0 相關比對（`.iml` 那條路、`R004`）直接跳過，`identify_peak()` 的 `ims` 分支回傳空清單並標注原因，不靜默產生不可信的數字。

### 串接方式

```
identify.py 讀取順序：
  1. 依 .mea 表頭的機型/管柱/方法組合，找對應的 calibration_profile.json
     （找不到 → 視保守程度選擇整批用 raw_parameters，或直接標記 unavailable，待決策）
  2. 讀 k0_calibration.mode，呼叫對應換算函式
  3. 每個峰的 k0_value 連同 "k0_mode" 這個 provenance 標記一起寫進 _peaks.json，
     供後續分析區分「這個 K0 是哪種可信度算出來的」，不同 mode 產生的數值不應混為一談比較
```

### 明確的未解決狀態聲明

**此問題尚未完全解決**，目前只有架構設計，缺兩個關鍵前提尚未到位：(a) 是否有可行的 K0 校準標準品或方法（決定能否啟用 `standard_based` 模式）；(b) 若暫時只能用 `raw_parameters` 退路，`T`/`P` 對應的表頭欄位仍需人工確認才能定案。在這兩點解決之前，第二階段的輸出（`k0_value`）應視為**暫定值，非最終可信數字**，任何依賴 K0 的下游判斷（`R004`、`.iml` 比對）都應該連帶標注同樣的不確定性。

---

## 第三階段：資料庫讀取

**建議模組名稱**：`library.py`

1. **`.ril` 讀取器** —— **[VOCal 反編譯驗證]** 欄位順序來自 `EDITOR_StartLibraryEditor.java`，21 欄：
   ```
   CAS/NAME/Formula/RI/ColumnType/ColumnPolarity/ColumnName/ColumnLength/
   CarrierGas/Substrate/ColumnDiameter/PhaseThickness/DataType/ProgramType/
   StartT/EndT/HeatRate/StartTime/EndTime/Programm/LiteratureIndex
   ```
2. **`.iml` 讀取器** —— 同上驗證，16 欄：
   ```
   Name/CAS/Formula/MW/RI/Rt[sec]/Dt[a.u.]/Command/DeviceTimestamp/
   p(IMS)/T(IMS)/L(IMS)/U(IMS)/RIP[a.u.]/DtMode/EditEvent
   ```
   **注意**：`DtMode` 欄位原始檔案裡實際存的字串是 `"1/K0"`（含斜線），跟第二階段模式命名要做對應，不可直接字串比較。
3. **要內建哪些 `.ril`/`.iml` 子集——[本輪大幅確認，不再是純待決策]**：

   實際讀取一份真實 `.mea` 表頭，確認 `'GC Column'` 這個 key 存在，內容格式為逗號分隔字串：
   ```
   'GC Column': 'FS-SE-54-CB-1, L: 30.00m, ID: 0.53mm, FT: 0.50µm, POLARITY: np'
   ```
   **這一個欄位就包含五個資訊**：管柱型號名稱（`FS-SE-54-CB-1`）、管柱長度（`30.00m`）、內徑（`0.53mm`）、膜厚（`0.50µm`）、極性（`np`）——比原先設想的「只有極性可用」豐富得多。**注意 key 名稱是 `GC Column`，不是原先反編譯 `W.java` 時猜測的獨立 `POLARITY` 欄位**，需要另外寫一個小的解析函式（用逗號分割字串、找 `POLARITY:` 開頭的片段取值）才能取出極性，不能直接 `header.get("POLARITY")`。

   **這帶來比原設計更精確的選檔策略**：
   - **優先**：直接用管柱型號名稱（`FS-SE-54-CB-1`）去比對 `.ril`/`.iml` 檔名或內部 `column_name` 欄位做**精確匹配**（例如對應到資料夾裡類似 `SE-54`/`apiezon` 命名的檔案），比只按極性分類精確得多
   - **次選（找不到精確對應管柱時的退路）**：依極性（`np`/`p`）分類，退回 VOCal 原本的做法，載入 `AVERAGE LOW POLAR | HP-5 | DB-5`（非極性）或 `DB WAX | HP WAX | Carbowax`（極性）這類同極性合併檔案
   - **`.iml` 額外可交叉核對載流氣體**：同一份表頭裡 `'Drift Gas': 'nitrogen'`，與 `.iml` 資料裡的 `[+][nitrogen]`/`[+][N2]` 標記一致，選 `.iml` 子集時應一併篩選載流氣體種類相符的條目，不只看化合物名稱
   - **[待決策]** 管柱型號名稱字串不保證與 `.ril` 檔名/內部欄位命名完全一致（可能有拼寫或格式差異，之前整理 `.ril` 檔名時就發現同類命名有不一致的情況），精確匹配需要做模糊比對或人工維護一份對照表，不能單純假設字串完全相等

4. **[本輪新增，UI 溯源需求] 每一筆讀進來的資料需附加 `source_file` 欄位（僅檔名，不含路徑）**：第三階段的選檔策略確定會同時載入多個 `.ril`/`.iml`（精確匹配 + 極性退路合併檔），合併進同一個查詢清單後，**若不記錄來源，使用者在候選結果裡會無法分辨某筆候選是從哪個資料庫檔案來的**，也就無法追溯／核對原始資料。`load_ril(path)`/`load_iml(path)` 讀取時，每一筆 row 需附加 `row["source_file"] = os.path.basename(path)`；由於比對函式（第五階段）是直接複製整個 row 字典再附加 `delta` 欄位，這個欄位會自動隨比對結果傳遞到第六階段輸出與第十階段面板，不需要在比對邏輯裡另外處理。

---

## 第四階段：RT→RI 轉換 —— 目前最大的缺口

**建議模組名稱**：`calibration.py`

1. **問題**：VOCal 原始碼裡沒找到「現場計算」的公式，只找到已經算好、存在專案檔（`.gasprj`）裡的校準表：

   **[VOCal 資料格式驗證]**：
   ```json
   "RI_Normalization": {
     "Values": [{"ColNormY": RI值, "ColNormX": log(Rt)值}, ...6 組],
     "ColNormisLog": true
   }
   ```

2. **[本輪驗證確認] `ColNormX` 的單位是 `log10(保留時間秒數)`，不是取樣索引，與 RT 軸解析度無關**：實際用專案檔裡 `Compounds` 清單的 `ethanol-M` 真實資料（`RI=420.9`、`Rt=44.358秒`）反向驗證——把 `log10(44.358)=1.6470` 代入校準表最低兩點做線性外插，算出 `RI=420.89`，與專案檔記錄值誤差僅 `0.013`，確認吻合。這代表校準表存的是真實物理時間（秒），**不是「第幾條光譜」這種會隨取樣密度（`Chunk sample rate`/`Chunk averages`）改變的索引值**——只要兩次量測的秒數準確，換算結果就能跨儀器/跨取樣設定穩定重現，不需要擔心 RT 軸解析度不一致的問題。此結論僅適用本階段（y 軸/RT 的 RI 校準），**與第一階段 RIP 正規化（x 軸/DT）是兩個獨立機制，不要混淆**——RIP 正規化本身也與解析度無關，但原因不同（見第一階段），兩者不要互相援引對方的理由。

3. **[待決策]** 前置實驗步驟（跟寫程式無關）：
   - 準備一批已知碳數的正構烷烴標準品
   - 在跟樣品**完全相同**的 GC 條件（管柱、升溫程序）下跑一次
   - 記錄每個烷烴的實際 `Rt`（保留時間秒數）

4. **有校準資料後的計算邏輯**（Van den Dool–Kratz，業界標準公式，非 VOCal 專屬）：
   ```
   RI = 100 × n + 100 × (Rt - Rt_n) / (Rt_(n+1) - Rt_n)
   ```
   n 為 Rt 落在的兩個相鄰烷烴碳數之間的下界

5. **沒有校準資料時的退路**：直接用 `.iml` 庫內建的 `Rt[sec]` 欄位做保留時間比對（精度較低，且只能對 `.iml`，因為 `.ril` 沒有存 Rt 只有存 RI）

6. **[待決策]** 是否已有現成的烷烴校準跑資料——這是整條鏈路能否做到「真正的 RI 比對」的關鍵前提。

---

## 第五階段：容許窗比對

**建議模組名稱**：`match.py`

1. **核心邏輯** **[VOCal 反編譯驗證]**（`S.h()`）：`center ± tolerance` 區間判斷
2. **兩個獨立維度**：
   - RI 維度：`peak_ri` vs `.ril`/`.iml` 的 `RI` 欄位
   - K0/Dt 維度：`peak_k0` vs `.iml` 的 `Dt[a.u.]` 欄位（須先篩選 `DtMode` 相符項目才比較）
3. **輸出三個候選清單** **[設計沿用，非逐字反編譯]**：
   - `gc_matches`：只有 RI 命中
   - `ims_matches`：只有 K0 命中
   - `combined_matches`：RI 與 K0 都命中且為同一 CAS 的交集
4. **[待決策]** 容許窗寬度：
   - VOCal 是使用者手動輸入，本專案可考慮做成固定值、或依 `peaks.py` 已算出的 `flatness`/`prominence` 動態調整
   - 沒有「正確答案」，需要拿幾個已知化合物的真實資料試跑校準

---

## 第六階段：整合輸出

**建議模組名稱**：`identify.py`

1. **輸入**：`_peaks.json`（來自 `peaks.py`）+ 選定的 `.ril`/`.iml` 檔案 + 儀器常數 + （若有）校準表
2. **對每個峰**：依序跑過第一到五階段
3. **輸出**：`_peaks_identified.json`，每個峰附上三分支候選清單，**每筆候選自動帶有 `source_file`**（第三階段附加，比對時原樣傳遞，見第三階段第 4 點），供第十階段面板顯示溯源資訊
4. **[設計沿用，非逐字反編譯]** 可選機制：仿 VOCal 的「加入清單」做法，建 `confirmed_compounds.json`，讓使用者手動確認候選是否正確，這筆資料未來可回頭擴充自己的 `.iml`/`.ril` 資料庫（VOCal 資料庫成長的閉環設計，值得沿用）。

---

## 第七階段：候選峰篩選規則庫（可插拔規則引擎）

**建議模組名稱**：`rules.py`

**[設計沿用，非逐字反編譯]**——此設計呼應 `GC-IMS_Peak_Finding_Workflow.md` 原本就定義的「三層參數結構」中**第三層（篩選裁決）**：`peaks.py` 維持 measure-only（只測量、不剔除），把「要不要留下這個候選峰」的裁決邏輯，從寫死在程式裡的固定門檻（`floor_pct`/`prom_frac`），改成一個**可持續新增、關閉、調整參數的規則庫**，不需要改動 `peaks.py` 或互動介面本身。

### 為什麼獨立成庫，不寫死在 `peaks.py` 或 `main.py` 裡

- 篩選標準會隨校準進度演變（先寬鬆看全貌、後收緊對齊人工判讀），寫死在主流程裡每次調整都要改核心程式碼，風險高
- 不同資料集/管柱/樣品類型可能需要不同規則組合，規則庫讓這件事變成「切換設定檔」而非「改程式」
- 呼應您專案既有的 A/B 兩階段校準哲學（先複製人工結果，後挖掘人工漏標）——規則庫本身就是這個哲學的具體實作載體

### 架構：規則編號、函式名稱、說明三者綁定管理

**編號原則**：每條規則指定一個固定的規則編號（格式 `R` + 三位數，如 `R001`），編號在建立時**依序遞增、一旦指定不重複使用、不因規則停用或改名而重新編號**——這是為了讓 `rules_config.json`、UI 面板、文件三者能長期用同一個編號互相對照，不受函式改名或說明文字調整影響。函式名稱與規則說明可以修訂，**編號本身視為穩定識別碼，不隨意變動**。

**目前規則清單**（含由 `peaks.py` 既有寫死邏輯遷移過來的兩條，見下方「與現有 `peaks.py` 規則的對應」）：

| 規則編號 | 函式名稱 | 規則說明 | 狀態/來源 |
|---|---|---|---|
| R001 | `rule_min_prominence` | 突出度低於門檻（相對於全圖最大突出度的比例）剔除 | 遷移自 `peaks.py` 現有 `prom_frac` 邏輯 |
| R002 | `rule_max_candidates` | 依突出度排序，只保留前 N 名 | 遷移自 `peaks.py` 現有 `top_n` 邏輯 |
| R003 | `rule_max_flatness` | 平坦度高於門檻視為 plateau，剔除 | 本專案新規則，`draft.02` 提出 |
| R004 | `rule_exclude_rip_band` | 漂移相對值落在 RIP 位置（x=1）附近的峰予以剔除，避免誤把 RIP 本身或其容許窗當成分析物峰 | 本專案新規則，`draft.04` 提出 |
| R005 | `rule_min_relative_steepness` | 突出度相對於峰自身峰高的比例低於門檻則剔除（相鄰峰之間鞍點不夠深，形似駝峰未明顯下凹） | 本專案新規則，`draft.06` 提出 |

```python
RULE_REGISTRY = {}

def register_rule(rule_number, name, description):
    def decorator(fn):
        RULE_REGISTRY[rule_number] = {"fn": fn, "name": name, "description": description}
        return fn
    return decorator

@register_rule("R001", "最小突出度", "突出度低於門檻的候選峰剔除")
def rule_min_prominence(peak, params):
    return peak["prominence"] >= params.get("threshold", 0)

@register_rule("R002", "前 N 名上限", "依突出度排序，只保留前 N 名候選峰")
def rule_max_candidates(peaks_sorted, params):
    top_n = params.get("top_n", 0)
    if not top_n or top_n <= 0:
        return peaks_sorted
    return peaks_sorted[:top_n]

@register_rule("R003", "最大平坦度", "平坦度高於門檻視為 plateau，剔除")
def rule_max_flatness(peak, params):
    return peak["flatness"] <= params.get("threshold", 1.0)

@register_rule("R004", "排除 RIP 柱狀帶", "漂移相對值落在 RIP 位置（x=1）附近的峰予以剔除")
def rule_exclude_rip_band(peak, params):
    rip_rel = peak.get("drift_relative")
    if rip_rel is None:
        return True   # 尚未算出 RIP 相對值時不做判斷，避免誤刪；見下方「執行順序依賴」
    half_width = params.get("half_width", 0.02)
    return abs(rip_rel - 1.0) > half_width

@register_rule("R005", "相鄰峰谷深比（駱駝背過濾）", "突出度相對於峰自身峰高的比例低於門檻則剔除")
def rule_min_relative_steepness(peak, params, floor):
    peak_height = peak["intensity"] - floor
    if peak_height <= 0:
        return False   # 峰高在 floor 以下，理論上不該出現在候選清單，保守剔除
    steepness_ratio = peak["prominence"] / peak_height
    return steepness_ratio >= params.get("min_ratio", 0.3)
```

**`R005` 規則說明——為什麼不需要新的量測邏輯**：「駝峰沒有陡降」這個現象，數學上就是**突出度相對於峰自身峰高的比例太低**。`R001` 是拿突出度跟「全圖最強突出度」比（全域相對值，`prom_frac`），`R005` 是拿突出度跟「峰自己的高度」比（自身相對值），兩者是不同信號：一個矮但孤立、乾淨下凹的峰，`R001` 可能因為全圖有更強的峰而被判定突出度不夠，但 `R005` 會判定它「相對自己夠陡」而保留；反過來，一個高但緊貼著更高峰、鞍點很淺的峰（典型駝峰），`R001` 可能因為全圖尺度下它的突出度還算可以而放行，但 `R005` 會抓出「相對自己峰高而言，這個突出度太小」而剔除。兩條規則互補，不是重複。

`peaks.py` 既有的突出度演算法（union-find 淹水模型）在計算過程中，鞍點值本來就會算出來（兩峰盆地相連時的鞍點高度），只是目前只保留了 `prominence = 峰高 − 鞍點高` 這個相對值，沒有另外存鞍點本身。**這條規則不需要 `peaks.py` 新增任何量測邏輯**——`prominence`（每峰已有）與 `floor`（`_peaks.json` 的 `stats.floor`，整個檔案共用一個值，非逐峰欄位）都已經存在，只是規則引擎套用這條規則時，除了 `params` 之外還要多傳入 `floor` 這個**影像層級常數**。

**[待決策]** `min_ratio=0.3` 是佔位預設值，未校準；此外「鞍點是否確實對應到*相鄰*的峰」這件事，嚴格說是拓樸上「盆地合併時遇到的第一個更高峰」，多數情況下就是空間上相鄰的峰，但理論上不保證絕對等同於歐氏距離最近的峰——這個誤差在實務上通常可忽略，先採用此簡化版本，未來若發現誤判案例再考慮改用更嚴謹的「顯式尋找最近鄰峰間鞍點」做法（需要重新掃描原始強度矩陣，成本較高）。

**規則函式簽名不只一種型態**：
- `R001`/`R003`/`R004`：「逐峰判斷」型，輸入單一峰 + 該規則參數，回傳留/不留的布林值
- `R002`：「整批處理」型，輸入排序後的整批峰清單 + 參數，回傳截斷後的清單，不能用逐峰判斷的方式表達
- `R005`：「逐峰判斷 + 影像層級常數」型，除了單一峰與參數，還需要 `floor` 這類每個檔案共用一個值、但不屬於任何單一峰的常數

**[待決策]** 規則引擎需要能區分並正確傳參給這三種型態，套用順序上建議「逐峰判斷」類規則（含需要影像層級常數的）先跑完，最後才跑「整批處理」型規則（如 `R002`），細節待實作時定案。

**`R004` 規則說明**：`peak["drift_relative"]`（第一階段 RIP 正規化的產出，`dt_index / rip_index`）在 RIP 本身位置理論上恰為 `1.0`。此規則以 `1.0 ± half_width` 定義一個容許帶，落在帶內的候選峰視為 RIP 本身或其緊鄰的寬容區，一律剔除。**[待決策]** `half_width=0.02` 只是佔位預設值，非校準過的數字，實際寬度需要拿真實資料試跑後調整——這條規則跟第五階段容許窗比對是同一類「需要真實資料校準」的參數，沒有理論上的正確答案。

1. **每條逐峰規則**：一個純函式，輸入單一峰的完整指標字典 + 該規則自己的參數，輸出布林值（留/不留）
2. **規則清單設定檔**（建議 `rules_config.json`）：紀錄每條規則的 `enabled` 狀態與參數值，用**規則編號**當 key，與規則本體（程式碼）分開存放
   ```json
   [
     {"rule_number": "R001", "enabled": true, "params": {"threshold": 50}},
     {"rule_number": "R002", "enabled": false, "params": {"top_n": 200}},
     {"rule_number": "R003", "enabled": false, "params": {"threshold": 0.7}},
     {"rule_number": "R004", "enabled": true, "params": {"half_width": 0.02}},
     {"rule_number": "R005", "enabled": true, "params": {"min_ratio": 0.3}}
   ]
   ```
3. **套用邏輯** `apply_rules(peaks, config) -> filtered_peaks`：預設對所有**已啟用**的逐峰規則做 AND（全部通過才保留），整批型規則最後套用
4. **[待決策]** 是否需要 AND 以外的組合方式（例如某些規則做 OR、或改成加權評分制而非二元判斷）——建議先做最簡單的 AND，有實際校準需求再擴充，避免過度設計
5. **新增規則的成本**：新規則只需寫一個函式 + `@register_rule` 裝飾器登記（**取下一個未使用的編號**），不需要碰觸既有規則或套用邏輯，符合「持續新增」的需求

### 與現有 `peaks.py` 規則的對應（架構調整）

`peaks.py` 的 `detect_peaks()` 目前內建四道寫死的篩選邏輯，其中兩道屬於「篩選裁決」性質，本輪確定遷移進規則庫，另外兩道留在原地：

| `peaks.py` 現有邏輯 | 是否遷移進規則庫 | 理由 |
|---|---|---|
| `floor_pct`（海平面門檻） | 否，留在 `peaks.py` | 屬於偵測層參數，決定候選極大值有幾個，不是「找到之後留不留」的裁決 |
| `min_distance`（最小間距去重） | 否，留在 `peaks.py` | 同上，避免同一峰因雜訊產生重複候選，屬偵測層邏輯 |
| `prom_frac`（相對突出度門檻） | 是 → `R001` | 本質是篩選裁決 |
| `top_n`（前 N 名上限） | 是 → `R002` | 本質是篩選裁決 |

遷移後，`peaks.py` 的 CLI 參數 `--prom-frac`／`--top-n` 應予移除，改由 `rules_config.json` 的 `R001`/`R002` 參數控制；`peaks.py` 本身回歸單純 measure-only（只留 `floor_pct`/`min_distance` 兩個偵測層參數），完整候選清單交給規則庫做裁決。



### 對應的 UI 功能：規則管理面板

**[本輪確認的介面設計，含 mockup]**——獨立彈窗，一條規則一列：

```
[勾選框] R00N · 規則名稱          一句話說明        [參數輸入框]
```

- 勾選框切換 `enabled`；**未勾選時該列的參數輸入框一併變灰、禁用編輯**（避免使用者誤以為關掉的規則參數還有作用）
- 儲存回 `rules_config.json`
- 面板底部即時顯示「套用後剩幾個候選」（如「276,713 candidates → 47 after rules」），讓使用者調參數時立刻看到篩選效果，不用盲猜再重新整理
- 套用規則後，只有通過篩選的候選峰才會在第八階段的互動介面上畫圈——**這代表規則庫是第八階段圈選介面的前置步驟**，兩者是上下游關係：規則庫決定「有哪些圈可以選」，第八階段的 pick/unpick 決定「使用者要不要留下規則庫選出來的圈」，是兩層獨立但銜接的裁決

### 與 `identify.py`（第十階段）的關係

**[待決策]**：規則庫篩選掉的候選峰，是否要一併排除在背景自動比對（第十階段）的運算範圍之外？建議是——沒被規則庫留下的峰，使用者不會在介面上看到，對它們預先算比對結果是浪費運算資源，`identify.py` 的輸入應該是「規則庫篩選後」的峰清單，不是 `peaks.py` 的全部原始輸出。

### 執行順序依賴：規則庫必須排在第一階段（RIP 正規化）之後

**這是本輪新增規則帶出的一個重要限制**：`R004`（`rule_exclude_rip_band`）這類規則要吃 `drift_relative` 欄位，而這是第一階段的產出，不是 `peaks.py` 原生就有的欄位。這代表完整管線的執行順序必須是：

```
peaks.py（measure-only 找峰）
   ↓
第一階段 rip.py（算 rip_index，把 drift_relative 寫回每個峰）
   ↓
第七階段 rules.py（套用規則，此時 drift_relative 已存在，規則才能正確判斷）
   ↓
第八階段 UI 圈選（只對通過規則的峰畫圈）
```

**[待決策]** 若未來還有規則需要依賴 K0（第二階段）或比對結果（第五、六階段）才能判斷，這個順序還要再往後延——規則庫設計上雖然是「可插拔」，但**個別規則吃哪個階段的欄位，會反過來限制規則庫本身在整條管線裡能多早被呼叫**，這點在新增規則時需要留意，不是所有規則都能在找完峰後立刻套用。

---

## 第八階段：UI 互動——峰的選取／取消選取（pick/unpick）

**建議調整模組**：`main.py`（顯示元件）

### 心智模型：Photoshop 圖層

等高線熱圖與峰位圈選是**兩個獨立圖層**，圈選圖層疊在熱圖圖層之上，兩者互不干擾，未來要加新的視覺標示（例如比對信心分數）也是再疊一層，不動下層邏輯。

### 主視窗版面配置變更：單一主圖 + 原圖彈出按鈕

**[本輪確認]**：現行 `main.py` 主視窗**並排顯示兩張圖**（原始熱圖、疊圈熱圖）。新版改為：

- 主視窗**只顯示疊圈圖**（Canvas 圖層架構，見下方）
- 工具列新增「View Original Heatmap」按鈕，點擊後**彈出一個新視窗**（`tk.Toplevel`，沿用既有 `ImageViewerDialog` 元件）顯示原始（無標註）熱圖，可縮放/拖曳
- 「View Original Heatmap」只需要 `readGAS.py` 讀檔完成即可使用，不需要等找峰完成——讀檔跟找峰現在是分開的兩個就緒時機，見下方工具列時序

### 架構修正：`peak_with_number.py` 不是互動主畫面的來源

**[本輪自我修正]**：曾經筆記錯誤地認為 `peak_with_number.py` 的靜態 PNG 會是新版主畫面的來源——**這與下方 Canvas 原生物件架構互不相容**（靜態 PNG 的圈是烤進像素的，無法個別 toggle）。修正後的角色分工：

- **互動主畫面**：PIL 背景圖 + `canvas.create_oval`（圈，Canvas 原生物件）+ `canvas.create_text`（數字，Canvas 原生物件）——即時互動用，不經過 matplotlib
- **`peak_with_number.py`**：僅供第十一階段「Generate Report」匯出靜態存檔圖使用，跟互動主畫面是兩條獨立的圖片產生路徑

### 工具列重新設計與時序

**[本輪確認]**：

```
[Browse Folder]  [View Original Heatmap]  [Show Detected Peaks]  [Rules]  [Generate Report]
```

- **「Read File」按鈕取消**，改成選檔案時自動背景讀檔（`readGAS.py`，實測約 13 秒），不需要使用者額外點擊；讀檔完成後「View Original Heatmap」變為可點擊狀態
- **「Detect Peaks」改名為「Show Detected Peaks」**——命名原則改成「看得到什麼」而非「做了什麼」，點擊觸發找峰（`peaks.py`，真實資料實測約 83 秒）並直接顯示疊圈結果，因為新版只有一張圖，沒有「找完峰後才切換畫面」這個中間狀態需要獨立命名
- **維持獨立按鈕、不做成選檔案後自動觸發**：找峰耗時達 83 秒量級，若選檔案就默默背景觸發，容易讓使用者誤以為程式卡住；讀檔（13 秒）跟找峰（83 秒）耗時量級差一個數量級，前者可以接受隱性等待，後者需要明確的使用者動作
- 「Rules」開啟第七階段的規則管理面板
- 「Generate Report」見第十一階段（按鈕位置本輪定案，內容格式待後續討論）

### 架構決策：Tkinter Canvas 原生物件，不用 matplotlib 嵌入

**[設計沿用，非逐字反編譯]**（此處無 VOCal 對應邏輯，純本專案設計）

- 熱圖背景：`canvas.create_image()` 貼一次，之後不重繪
- 每個峰：`canvas.create_oval()` 疊在熱圖之上，圈與峰是獨立的 Canvas 物件，各自有 `item_id`
- **點擊判定**：`canvas.find_closest(x, y)` 內建最近物件搜尋，不需自己實作最近鄰演算法
- **切換選取狀態**：`canvas.itemconfig(oval_id, outline="gray")` / `outline="red"`，只改動被點擊的那一個物件，熱圖背景與其他圈完全不重繪，效能 O(1)
- **圖層可見度控制**：`canvas.itemconfig(item_id, state="hidden"/"normal")`，可整批用 tag 操作（例如一鍵隱藏所有圈，回到乾淨熱圖，呼應 §1.3 影像模式文件裡「乾淨影像 vs 標註影像分離」的原則）

### 峰編號標示——半透明簡化方案，取代防碰撞演算法

**[本輪確認，簡化設計]**：原本考慮貪婪式標籤避讓演算法（右上→左上→右下→左下依序嘗試不碰撞的位置），**改為半透明數字，放棄避讓演算法**：

- 數字固定放在圈的右上角（不做位置嘗試）
- 半透明只解決「數字蓋住圈本身」的問題（底下圈的顏色/邊框仍隱約可見）；**無法解決兩個數字彼此重疊**——這種情況目前接受殘留風險，理由是規則引擎（第七階段）已大幅減少候選數量、`R004`/`R005` 也會篩掉緊貼在一起的峰，實際發生機率低，有實際案例再考慮加避讓邏輯（Rule 8：不為還沒發生的問題預先寫複雜功能）
- **[本輪發現的技術限制，待評估]**：`canvas.create_text` 沒有原生透明度屬性，「半透明」需要用顏色模擬（效果不保證在深淺不一的熱圖底色上一致）或改用帶 alpha 通道的小 PNG 逐一貼圖（效果正確但物件開銷較高）。先採顏色模擬版，效果不佳再評估換方案。

### 資料模型與雙向同步

1. `_peaks.json` 每個峰加欄位 `"active": true`（預設全選）
2. 點擊圈 → 切換該峰 `active` 布林值 → 圈變色 + 對應列表列變灰（`ttk.Treeview` 的 `tag_configure`）
3. **[本輪確認] 雙向同步，且延伸到表格內建的核取方塊**：峰表最後新增一欄「On」核取方塊（預設全勾），三處互相連動——點圖上的圈、點表格列、勾/取消勾核取方塊，任一動作都會同步更新其餘兩處，共用同一個 `toggle_peak(peak_id)` 函式
4. **取消勾選的連帶效果**：`active=false` 時，圖上對應圈變灰 **且** 該列的 ▶ 觸發鈕（見第十階段）**一併變成不可點擊**（`cursor="arrow"`、視覺淡化），使用者已決定不要的峰不需要再讓他點進去看比對細節
5. **狀態持久化**：切換結果須寫回 `_peaks.json`（或獨立 `_peaks_state.json`），否則關閉程式重開後選取狀態遺失
6. **[待決策]** 全選/全不選按鈕、鍵盤操作（方向鍵切換當前峰、空白鍵切換 active）——大量候選峰時，逐一滑鼠點擊小圈容易誤觸，鍵盤操作作為輔助

### 峰表欄位擴充

**[本輪確認]**：欄位由原本 `#/Dt/Rt/Intensity` 擴充為：

```
# | RT | DT | Intensity | On(核取方塊) | GC×IMS | GC | IMS | ▶
```

- **`GC×IMS` 欄**：顯示信心最高候選的**名稱**（兩維度都命中的第一名），沒有候選時顯示 `—`
- **`GC`／`IMS` 欄**：顯示**候選數量**（純數字），且**不含**已經算進 `GC×IMS` 那欄的項目——代表「只在單一維度命中、另一維度沒有一起命中」的候選有幾個。數字愈高代表這個峰在該單一維度上愈不獨特（見第十階段面板裡的完整候選清單）
- **`—` 與 `0` 的區分**：`k0_mode` 為 `unavailable`（第二階段尚未解決）時，`IMS` 欄顯示 `—`；真正算過但零命中才顯示數字 `0`——兩者意義不同（前者是「這個維度沒法用」，後者是「這個維度可信但沒命中」），不可混用同一個符號
- **[本輪簡化，取代先前設計]** 這三欄**皆為純文字顯示，不可點擊**——先前一度設計成「點不同欄位跳轉比對面板的不同分頁」，已作廢（見第十階段），改成單一 ▶ 觸發，欄位本身只負責顯示、不承擔互動

### 候選峰數量控制——已由第七階段規則庫解決

原本此處是待決策項目（候選峰數量級未知，可能影響圈選介面可用性）。此問題已改由**第七階段的可插拔規則引擎**解決：互動介面只對通過已啟用規則的候選峰畫圈，不是對 `peaks.py` 的全部 measure-only 輸出畫圈，數量由規則組合動態控制，不需要在此另訂固定門檻或猜測數量級。

---

## 第九階段：批次轉檔（`.mea` → `.npz`）

**建議模組名稱**：`batch_convert.py`

**[設計沿用，非逐字反編譯]**，與找峰/比對邏輯完全解耦，獨立於主要工作流程之外。

1. 走訪指定資料夾（含子資料夾）所有 `.mea`，逐一呼叫既有的 `readGAS.read_mea()` + `export_npz()`
2. **跳過已轉換的檔案**：比對目標 `.npz` 是否存在且比來源 `.mea` 新（檔案 mtime），避免每次整批重跑
3. **平行化**：用 `multiprocessing.Pool`，此步驟是 CPU-bound 的檔案 I/O + reshape，多核心平行化效益明顯
4. **排程**：不在 Python 內自建排程邏輯，交給作業系統原生機制（如 Windows 工作排程器排定期執行）；若需要「新檔案一出現就自動轉」而非定時跑，可用 `watchdog` 套件監控資料夾事件——**[待決策]** 是否需要，先確認排程式是否已夠用
5. **[本輪新發現] 可選的品質前置過濾——`.mea` 表頭自帶儀器自我品管標記**：實際讀取真實 `.mea` 表頭時發現 `'Status': 'doubtful'` 與對應的 `'Status comment'`（本輪樣本記錄了三筆待處理診斷：`FlowGClow`/`SeptumDurability`/`NoValidSnapshot`），這是儀器自己標記「這次量測可能有問題」的免費品管訊號，完全在原先規劃之外。**建議** `batch_convert.py` 提供一個可選參數（例如 `--skip-doubtful`），依 `Status` 欄位值決定是否跳過標記為可疑的檔案，或至少把 `Status`/`Status comment` 完整保留進 `_meta.json` 的 `header_raw`（依現有設計本來就會保留，此處純粹是強調這個欄位的實際用途，不是新的儲存需求）。**[待決策]** 是否要做成強制排除、或只是保留供人工檢視後自行決定，尚未定案。

---

## 第十階段：自動比對觸發與面板串接（箭頭按鈕）

**建議調整模組**：`main.py` + `identify.py`

**關鍵設計決策**：比對運算在背景**自動**進行，▶ 觸發鈕點擊只是開啟已算好結果的面板，不是觸發運算本身——確保使用者點擊到看到結果是瞬間的。

1. **時機**：`peaks.py` 找完峰、使用者尚未操作任何圈選之前，就在背景把所有峰的比對結果算好，存成 `_peaks_identified.json`
2. **呼叫方式**：`main.py` 直接 `import identify` 當函式庫呼叫（`identify.py` 裡的核心函式本來就是函式庫形式，這裡不需要調整），**不透過 `subprocess`**，避免額外的行程啟動與序列化開銷
3. **[本輪簡化，取代先前分頁設計]** **單一觸發、單一面板**：每個峰只有一個進入點——峰表最後一欄的 ▶。原本設計過「表格 `GC×IMS`/`GC`/`IMS` 三個儲存格各自可點、依欄位跳轉面板不同分頁」，**已作廢**：改成三欄純文字顯示（見第八階段），面板本身也**不用分頁**，三段結果**上下堆疊在同一個彈窗裡一次顯示完**，不需要分頁切換的狀態管理
4. **[本輪確認] 面板內容結構**（`tk.Toplevel` 彈窗）：
   ```
   Peak #N · Rt ... · Dt ...
   ─────────────────────────
   GC × IMS
     [信心圓點] 候選名稱 / CAS / RI Δ / K0 Δ / 來源檔案      [Confirm]
   ─────────────────────────
   GC only (N)
     候選名稱 / RI Δ / 來源檔案
     候選名稱 / RI Δ / 來源檔案
     +N more…
   ─────────────────────────
   IMS only
     候選名稱 / K0 Δ / 來源檔案       （或不可用時顯示原因文字，見下）
   ─────────────────────────
                                            [Close]
   ```
5. **信心圓點顏色**：teal（兩維度誤差皆小）／amber（有一維度誤差較大）——**[待決策]** 確切的顏色門檻（多小算「小」）需要拿真實資料校準，跟容許窗寬度是同一類問題，沒有理論值
6. **`Confirm`／`Close`**：對應第四點「自動比對、點擊只是雙重確認」——`Confirm` 才真正寫入 `confirmed_compounds.json`（見第六階段），不是被動接受第一名候選
7. **不可用狀態的呈現**：若某分支因故無法計算（例如 `k0_mode=unavailable`），該段落**不留空、不省略**，改顯示一行說明文字（如「K0 not calculated — this peak is missing calibration data」），讓使用者清楚知道「沒算」跟「算了沒命中」的差別，不是靠表格欄位的 `—`／`0` 區分就結束（表格只是提示，面板內文字才是完整說明）
8. **[本輪新增] 每筆候選顯示來源檔案（`source_file`）**：對應第三階段新增的欄位，供使用者追溯這筆候選是從哪個 `.ril`/`.iml` 檔案查到的——`GC×IMS` 因為橫跨兩個資料庫，會列出兩個來源檔名；`GC only`/`IMS only` 各自只有一個來源
9. **[本輪確認] 與「On」核取方塊的連動**：`active=false`（該列核取方塊未勾）時，▶ 不可點擊——已決定不要的峰不需要開面板查看細節
10. **[待決策]** 圈選狀態變動（使用者取消選取某峰）是否需要即時反映在面板上、或面板只在按鈕點擊當下讀取一次快照——建議先做快照版（簡單），若後續體驗不佳再改即時同步

---

## 第十一階段：匯出

**[本輪部分定案，內容格式仍待後續討論]**

- **觸發點確定**：工具列新增「Generate Report」按鈕（見第八階段工具列設計），與其他功能按鈕視覺區隔（例如加粗），代表這是整個流程的終點動作
- **[待決策]** 匯出內容與格式——包含哪些欄位（僅 `active=true` 的峰？還是全部連同被規則庫篩掉的都留紀錄？）、要不要內嵌 `peak_with_number.py` 產生的標號圖、輸出成 CSV／JSON／排版過的文件（如 docx/PDF），這些留待使用者有具體需求後再定案，本輪只先確定按鈕位置與觸發時機

---

## 其他建議功能與待確認的邏輯疑點

**建議追加**：

1. **品質指標的視覺編碼**：`peaks.py` 已算出 `prominence`/`flatness`，但目前圈選設計只用二元（選中/取消）視覺狀態，可用顏色深淺或外框粗細額外編碼這兩個指標，讓使用者一眼看出「演算法自己覺得這個候選峰可不可靠」，輔助人工判斷——可做成獨立圖層（見第八階段的圖層心智模型），不影響選取邏輯本身。
2. Java→Python 移植範圍**已於本輪確認**：僅移植與找峰/比對相關的核心邏輯（RIP 演算法、K0/RipRel 換算、`.ril`/`.iml` 讀取、雙軸容許窗比對），VOCal 裡其餘功能（`DynamicPCA`/`reporter`/`galerie_sort` 等）不在移植範圍內。

**已解決的邏輯疑點**：

1. ~~候選峰數量級問題~~ —— 已由**第七階段（可插拔規則引擎）**解決，不需另訂固定門檻或猜測數量級，詳見該階段。

---

## 待實作清單（設計已定案，尚未寫成程式碼）

**目的**：這幾輪對話裡確認的設計，很大一部分還停在「文件寫定案、程式碼沒動」的狀態，集中列在這裡，避免只存在對話記憶裡、之後遺漏。

### 待修的既有 bug（3 個，2 個已修）

| # | 檔案 | 狀態 |
|---|---|---|
| 1 | `readGAS.py` `plot_heatmap()` | ✅ 已修復並驗證（draft.11） |
| 2 | `peaks.py` `write_overlay()` | ✅ 已修復並驗證（draft.11） |
| 3 | `peak_with_number.py` `write_overlay_numbered()` | ❌ 尚未修復（draft.12 發現），同樣的 `_downsample_for_display()` 修法可直接套用 |

### 已設計但尚未寫成程式碼的新模組（第一～七、十階段）

`rip.py`／`dt_convert.py`／`library.py`／`calibration.py`／`match.py`／`rules.py`——邏輯設計、公式、資料格式皆已確認（部分已有對應 QC 測試骨架，見 `test/` 資料夾），但檔案本身都還不存在。`identify.py` 有骨架（早期版本），但**尚未更新**以下本輪新增的設計：`source_file` 溯源欄位（第三、六階段）、雙模式 K0 校準（`calibration_profile.json`，第二階段）。

### 已設計但尚未寫成程式碼的 UI（`main.py`）

- 工具列重新設計（按鈕改名、順序、取消 Read File 獨立按鈕）
- 主畫面改為 Canvas 原生物件（PIL 背景 + `create_oval` + `create_text`），取代現有的 matplotlib 靜態圖顯示
- 峰表擴充四欄（`On`／`GC×IMS`／`GC`／`IMS`）與 ▶ 觸發鈕
- 圈／表格列／核取方塊三方雙向同步（`toggle_peak()`）
- 半透明數字標示（本輪確認用顏色模擬版，即方案 (a)，效果未經驗證）
- 規則管理面板（第七階段 mockup 已定案）
- 化合物比對面板（第十階段 mockup 已定案：單一 ▶、三段堆疊、信心圓點、來源檔案顯示）
- 「Generate Report」按鈕（位置已定案，內容格式範例見 `Report_Content_Example.md`，實際匯出格式仍待決）

### 卡住整條鏈路的三個關鍵決策

| # | 問題 | 卡住哪個階段 |
|---|---|---|
| 1 | 儀器常數（L/U/T/P）從哪來？ | 第二階段（K0 換算）——`L`/`U` 本輪已可從表頭取得，`T`/`P` 仍不確定 |
| 2 | 有沒有烷烴校準跑資料？ | 第四階段（RI 轉換），沒有的話整條鏈路退化成只能用 Rt 秒數粗略比對 |
| 3 | 有沒有 K0 校準標準品資料？ | 第二階段（`standard_based` 模式），沒有的話只能用帶殘留誤差的 `raw_parameters` 退路 |

這些不是技術問題，是需要先盤點手上實際擁有的資料才能回答。

---

## 修訂記錄

- **draft.01**：初版，六階段拆解 + 三個關鍵決策點。
- **draft.02**：新增第七～十階段（峰選取/取消選取 UI、批次轉檔、自動比對觸發與面板串接、匯出待議），確認 Java→Python 移植範圍，補充其他建議功能與一項待確認邏輯疑點（候選峰數量級）。
- **draft.03**：新增第七階段（可插拔候選峰篩選規則庫），解決 draft.02 遺留的候選峰數量級疑點；原第七～十階段依序後移為第八～十一階段。
- **draft.04**：新增具體規則 `exclude_rip_band`（排除 RIP 柱狀帶，x=1 附近的峰不予選取），並補上「規則庫執行順序依賴第一階段 RIP 正規化」這項架構限制。
- **draft.05**：導入正式規則編號制（`R001`起，固定不重複使用），為既有四條規則逐一編號；確定 `peaks.py` 現有 `prom_frac`/`top_n` 遷移進規則庫（分別為 `R001`/`R002`），`floor_pct`/`min_distance` 留在 `peaks.py` 本身；補充規則庫需同時支援「逐峰判斷」與「整批處理」兩種規則型態。
- **draft.06**：新增 `R005`（相鄰峰谷深比／駱駝背過濾），複用 `peaks.py` 既有的 `prominence` 與 `floor`，不需新增量測邏輯；補充規則引擎需支援第三種型態——「逐峰判斷 + 影像層級常數」。
- **draft.07**：標注 `peaks.py` 目前尚未實作正規化（`drift_relative` 欄位缺失，`R004` 暫時無法運作），並記錄 UI 版面變更：主視窗改為單一疊圈圖 + 原圖彈出按鈕（取代原本並排雙圖顯示）。
- **draft.08**：「現狀基準」由「已完成不動」修正為含明確待修改項目——新增 `.npz` 遺失 metadata 的缺口說明與 `_meta.json` 側車檔設計（含 `readGAS.py`/`peaks.py` 具體修改點）；第一階段補充 `rip_index`/`sample_rate_khz` 須逐檔案計算、不可快取共用的提醒；第四階段補上 `ColNormX` 單位驗證結果（`log10(Rt秒數)`，經 `ethanol-M` 實測資料交叉驗證，與 RT 軸解析度無關）。
- **draft.09**：用使用者提供的真實 `.mea` 檔案（`260625_141215_STD.mea`）實際執行 `readGAS.read_mea()`，驗證/修正多項先前假設：(1) 第二階段——`L(IMS)`/`U(IMS)` 確認可從表頭 `nom Drift Tube Length`/`nom Drift Potential Difference` 讀取，`T(IMS)`/`P(IMS)` 對應仍不確定，需人工判斷；(2) 第三階段——極性資訊實際藏在 `GC Column` 欄位（逗號分隔字串）而非獨立 `POLARITY` key，且同一欄位含管柱型號/長度/內徑/膜厚，可做比純極性分類更精確的資料庫選檔，並確認 `Drift Gas` 欄位可交叉核對 `.iml` 載流氣體類型；(3) 第九階段——新發現 `Status`/`Status comment` 欄位是儀器自帶的品管標記，可作為批次轉檔的可選前置過濾依據；(4) `_meta.json` 範例改用本次驗證過的真實欄位與數值。
- **draft.10**：第二階段大幅改版——確認「機器差異無法被正規化完全消除」（表頭給的是標稱值/控制器設定值，非實測物理值），改採雙模式設計：新增 `calibration_profile.json`，`k0_calibration.mode` 分 `standard_based`（用已知 K0 標準品現場校準反推儀器等效常數，首選但需使用者提供校準資料，目前無）／`raw_parameters`（沿用表頭常數的退路，`T`/`P` 對應仍未確認）／`unavailable`（兩者皆無時跳過 K0 比對，不產生不可信數字）；`_peaks.json` 的 `k0_value` 須連同 `k0_mode` provenance 標記一併輸出。**明確聲明：此問題尚未完全解決**，目前僅為架構設計，卡在使用者是否有 K0 校準標準品，以及 `raw_parameters` 模式下 `T`/`P` 表頭欄位對應仍需人工確認兩個前提。
- **draft.11**：實際除錯並修復 `readGAS.py`/`peaks.py` 的 OOM 崩潰——`plot_heatmap()`/`write_overlay()` 在真實尺寸矩陣（20413×3150）上會於 `fig.savefig()` 光柵化時記憶體暴增被系統砍掉，已定位根本原因（丟進 `imshow()` 的矩陣未降採樣）並修復、驗證通過。附帶記錄兩項真實數字：`detect_peaks()` 在此量級資料上耗時約 83 秒；預設參數下 276,713 個原始局部極大經篩選後剩 47 個峰，兩者皆補充進第七/八/十階段的效能評估依據。
- **draft.12**：大幅定案第八/十階段 UI 設計（含 mockup 反覆確認）：工具列改為「Browse Folder / View Original Heatmap / Show Detected Peaks / Rules / Generate Report」，取消獨立「Read File」按鈕（併入自動背景讀檔）；峰表擴充 `On`（核取方塊）/`GC×IMS`/`GC`/`IMS` 四欄，三個比對欄位純顯示不可點擊；峰編號標示簡化為半透明方案（取代防碰撞演算法），並記錄 Tkinter Canvas 無原生透明度的技術限制；比對面板簡化為單一 ▶ 觸發、三段堆疊顯示（取代分頁設計），新增信心圓點與 `source_file` 溯源顯示；第三/六階段新增 `source_file` 欄位設計，供比對結果溯源至來源 `.ril`/`.iml` 檔案；發現並記錄 `peak_with_number.py` 有與 draft.11 相同的 OOM bug（尚未修復），同時自我修正其角色定位（僅供第十一階段匯出用，非互動主畫面來源，避免與 Canvas 原生物件架構衝突）；第十一階段（匯出）確定按鈕位置與觸發時機，內容格式仍待後續討論。
- **draft.13**：確認半透明數字採方案 (a)（顏色模擬，非真透明合成）；新增 `Report_Content_Example.md` 示範第十一階段報告內容結構（樣品/儀器資訊、找峰參數、校準狀態誠實揭露、已識別峰清單）；新增「待實作清單」章節，集中列出尚未修復的 bug、尚未寫成程式碼的模組與 UI 設計、三個關鍵決策現況，避免僅存在對話記憶中。

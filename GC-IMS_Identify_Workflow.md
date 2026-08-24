# GC-IMS 化合物比對工作流程 —— 第一版草稿

**Version: draft.27 — by Albert Sheng**（專案版本 **3.3**）
**狀態：草稿，持續更新中，尚未定案**

> **兩套編號並存是刻意的**：`draft.N` 是本規格自己的推進序號，被本檔內文引用 81 處、
> `status.md` 引用 25 處（如「draft.14 的排序決策」「draft.24 更正」）。把它改寫成
> 3.3 會讓那 106 處交叉引用全部失效，所以保留 `draft.N`，並在此標註它對應的專案
> 版本。程式碼版本看 `3.3`，規格條目的沿革看 `draft.N`。

> **[實作狀態 2026-07-27 — 程式已 tag v3]** 第一~七階段皆已實作並可端到端執行。
> **第四階段（RT→RI）已落地**：`calibration.py` + `reference_series.py` 自動從批次
> STD 挑 6 個酮的錨點（DT_rel 階梯）、做 `log10(RT)` 分段線性內插（範圍外外插+標記），
> 資料夾層級三態解析 + 快取，並串進 `identify.py` 與桌面 UI（RI 欄、線性 RI 熱圖軸；
> x 軸 drift 正規化不動）。6 個 RI 值目前為**借用值**（`assumed_unverified`，見
> `ketone_RI_provenance.md`）。數學細節見 `RT_to_RI_normalization_math.md`。
> 本規劃文件仍為設計權威；此處只標「已實作到哪」，設計內容不因實作而改寫。

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

### 已修復：`peak_with_number.py` 的同一個 OOM bug

**[draft.12 發現，已於 `peak_with_number.py` ver.02 修復]**：畫 UI mockup 時盤點既有檔案，發現 `peak_with_number.py`（疊峰編號顯示）的 `write_overlay_numbered()` 同樣是 `ax.imshow(intensity, ...)` 直接丟未降採樣的完整矩陣，跟上面修好的兩處是**同一個 bug、同一個成因**，只是那輪除錯時漏掉了這一支。**已修**：`write_overlay_numbered()` 現在在 `ax.imshow()` 之前呼叫 `peaks._downsample_for_display(intensity, figsize, dpi)`（直接重用 `peaks.py` 那一份，因為本模組本來就已硬相依 `peaks` 取 `load_surface`/`RESULTS_DIR`，不需要第三份複製）。

此檔案的角色**不是**第八階段互動主畫面的來源（見第八階段「架構修正」一節，兩者架構互不相容），而是提供給第十一階段「Generate Report」匯出用的靜態標號圖。

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
   優勢：完全不需要確認六個 `Start temp` 哪個對應漂移管、`EPC` 壓力是否等於管內實際壓力——這些個別欄位的不確定性被「整體校準」一次吸收掉。

   > **✅ [draft.26 解決] 這條路已經打通，而且不需要新的實測。**
   >
   > 卡住的前提原本寫成「需要使用者提供或執行一次已知 K0 標準品的實測」。實際上兩個
   > 要素早就都在手上，只是沒被放在一起看：
   >
   > 1. **已知 K0 值**：`library_data/GAS BASE 3H_IMS K0.iml`（G.A.S. 官方基礎庫，
   >    `DtMode=1/K0`）本來就帶著這六個酮的 K0 參考值。
   > 2. **在這台機器上的實測**：本批的 STD 就是。它跑的正是這六個化合物，
   >    `match_anchors_by_dt()` 已經能把峰對到化合物身上（見第四階段）。
   >
   > 於是 `calibration.derive_k0_instrument_constant()` 逐錨解 `IC = K0_ref · t · U`，
   > 六個錨點各得一個 IC，取平均：
   >
   > | | |
   > |---|---|
   > | instrument_constant | **25.0808** |
   > | 六錨的 CV | **0.133%** |
   > | 各錨殘差 | < 0.25% |
   >
   > CV 0.133% 是這個結果可信的關鍵——六個獨立化合物、跨越整個漂移範圍，解出來的
   > 常數一致到千分之一,代表「把 `ah × L²` 當一個數字整體校準」這個假設在這台機器上
   > 確實成立。函式在 CV 超過 `max_cv` 時**拒絕回傳**而非給一個看起來合理的數字，
   > 避免拿錯庫或配錯峰時靜默產生假的常數。
   >
   > `build_k0_profile_from_std()` 把它包成本節定義的 profile 格式。
   >
   > **✅ [v3.2] 已接上使用端。** `resolve_calibrations_cached()` 一次解出 RI 與 K0
   > （K0 沿用 RI 選中的那支 STD），`identify.py` 與 UI 都改走這條路。實測 STD：
   > 37 顆峰全部 `k0_mode=standard_based`，IMS 比對維度由 RIPrel 轉為 **k0**。

2. **`"raw_parameters"`（退路）**：沒有校準標準品時，直接讀表頭四個常數代入原公式（即第一版設計）：
   ```python
   def k0_from_raw_params(dt_raw, sample_rate_khz, L, T, P, U):
       t_d_s = (dt_raw / sample_rate_khz) / 1000.0
       ah = (273.0 / (273.0 + T)) * (P / 1013.0)
       K0 = ah * L**2 / (t_d_s * U)
       return 1.0 / K0        # ← ⚠ draft.24 更正：應為 return K0，見下方
   ```

   > **⚠ [draft.24 更正] 最後那行 `return 1.0 / K0` 是錯的，已於 `dt_convert.py` 修正為回傳 K0 本身。**
   > 本設計稿的這一行讓 `raw_parameters` 回傳逆值，而上方 `standard_based` 的
   > `instrument_constant / (t_d_s * U)` 回傳 K0——同一台機器、同一顆峰，兩個模式的數字
   > 互為倒數，卻都被 `attach_k0()` 寫進同一個 `peak["k0_value"]`，且沒有任何地方會報錯。
   >
   > **外部佐證**：`gc-ims-tools` 0.1.10（Food Chemistry 2022）的
   > `Spectrum.calc_reduced_mobility()` 用
   > `K0 = L²·T₀·p / (dt·Ud·T·p₀)`（預設 `L = 5.3 cm`，與本專案自表頭讀出的值一致），
   > 引 Ahrens & Zimmermann, *Anal Bioanal Chem* 413, 1009–1016 (2021)。展開即
   > `(T₀/T)·(p/p₀)·L²/(t·U)`，與本公式同形，**而它回傳 K0，不是倒數**。
   >
   > **修正方式**：兩個模式一律回傳 K0；取倒數移到比對端**顯式**處理——
   > `match.match_k0()` 依 `DtMode` 判斷庫值是 `K0` 還是 `1/K0`（實測 `library_data/`
   > 的 203 筆該類 row 字面就是 `"1/K0"`），換算到 K0 空間再比，換算後的值另存
   > `k0_library_value`。換算慣例屬於「這個庫怎麼存」，與「峰的 K0 怎麼算」是兩回事，
   > 混在一起正是本 bug 的成因。由 `test_both_modes_return_the_same_quantity()` 鎖住。

   > **✅ [draft.26 解決] `T`/`P` 該讀哪個欄位——反編譯 VOCal 得到答案。**
   >
   > 這一項卡了很久，因為六個 `Start temp`（45/60/80/80/45/off）沒有一個標示用途，
   > 而兩個壓力欄位（`Start ambient pressure` 100.393 kPa、`Start pressure EPC IMS`
   > 1.45 kPa）數量級差了 70 倍，挑哪個都像是猜的。把儀器隨附的 VOCal `.jar`
   > 反編譯（使用者自有軟體的互通性還原工程）之後：
   >
   > | 參數 | 取法 | 本批數值 |
   > |---|---|---|
   > | `T` | `Start temp 1` | 45 °C |
   > | `P` | **`10 × (Start ambient pressure + Start pressure EPC IMS)`** | 1018.43 mbar |
   >
   > 出處 `BufferedMEA.java:313`，已寫進 `dt_convert.extract_raw_tp()` 回傳的
   > provenance dict 裡。
   >
   > **關鍵在於壓力是「和」不是任一個**：EPC 是相對於環境的表壓（gauge），管內絕對
   > 壓力等於環境壓力加上它。先前所有「該選哪一個」的討論方向本身就錯了——正確答案
   > 不在選項裡。`compute_k0()` 現在在呼叫端沒給 T/P 時自動導出，不再需要 `--raw-tp`。
   >
   > **但這只解決了正確性，沒解決準確度。** 用這條路算出來的 K0 與
   > `standard_based` 的結果差 **+3.5%**——而相鄰同系物的 K0 間距約 8%，也就是說
   > 誤差已達一個同系物間距的 **43%**，足以把化合物配到隔壁去。故
   > `raw_parameters` 仍**不適合用於鑑定**，只適合在完全沒有標準品時當粗略參考。
   > 這正是本階段開頭「讀對表頭欄位 ≠ 消除機器差異」那個判斷的量化證實。

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

### 狀態聲明 —— draft.26 改寫

**原本卡住的兩個前提都已解決**，此處保留原文的判斷結構以便對照：

| | 原本的未決項 | 現況 |
|---|---|---|
| (a) | 是否有可行的 K0 校準標準品或方法 | ✅ **已解決**——不需新測量。`GAS BASE 3H_IMS K0.iml` 的已知 K0 ＋ 本批 STD ＝ `IC = 25.0808`，CV 0.133% |
| (b) | `raw_parameters` 的 `T`/`P` 欄位對應 | ✅ **已解決**——反編譯 VOCal：`Start temp 1`；壓力為 ambient 與 EPC 之**和** |

**但 `k0_value` 目前仍然是 `None`，原因換了一個**：不再是「不知道怎麼算」，而是
「算得出來卻還沒接上」——`resolve_ri_calibration()` 的資料夾解析／快取路徑只產生 RI
profile，沒有產生 K0 profile，所以 `identify.py` 與 UI 拿到的 `k0_mode` 依然是
`unavailable`。**這是目前整條鏈路上單一價值最高的待辦**：接上之後 `.iml` 的 K0 維度
（本專案 `library_data/` 裡 203 筆 `1/K0` 資料）就能參與比對，IMS 軸從只有 RIPrel
一個維度變成兩個。

在接上之前，第二階段的輸出維持 `unavailable`，下游（`R004`、`.iml` 比對）照舊只用
`drift_relative`——這一點沒有變化，也不影響現有結果的可信度：`R004` 本來就用
`drift_relative` 而非 K0。

**兩個模式的可信度不對等，不應混用**：`standard_based` 可用於鑑定；`raw_parameters`
偏 +3.5%（≈ 同系物間距的 43%）僅供粗略參考。`k0_mode` 這個 provenance 標記存在的
理由就是讓下游能分辨，切勿把兩種模式產生的數值放在一起比較。

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

   > **[實作修正] `.iml` 不套用管柱/極性選檔，改全部載入**：上面的管柱優先／極性退路
   > 策略**只適用 `.ril`**。`.iml` 走的是漂移維度，`DtMode=="RIPrel"` 存的是相對 RIP
   > 的無因次比值，屬儀器層級的量，與樣品跑哪根 GC 管柱無關；用管柱名或極性去篩 `.iml`
   > 只會製造 false negative。單位混用的風險由 `match.match_drift_rel()`／`match_k0()`
   > 的 `DtMode` 過濾承擔，不靠檔名。實作於 `identify.select_library_files()`，CLI 與 UI
   > 共用（先前只有 UI 這樣做，CLI 仍在篩，同一批資料兩條路徑候選集不同）。
   >
   > **[實作修正] 載流氣體核對採保守語意**：`library.filter_iml_rows_by_drift_gas()`
   > 預設只排除「明確標記為別種氣體」的 row，**未標記者保留**。實測 `library_data/`
   > 的 201 筆 `RIPrel` row 只有 162 筆帶標記，其餘 39 筆來自欄位會偏移的舊格式檔
   > （見本階段開頭的已知 caveat）——那是「沒記錄氣體」不是「記錄了別種氣體」，嚴格
   > 篩選會拿缺漏的 metadata 去否定實際可用的資料。要嚴格模式傳 `keep_untagged=False`。
   - **[待決策]** 管柱型號名稱字串不保證與 `.ril` 檔名/內部欄位命名完全一致（可能有拼寫或格式差異，之前整理 `.ril` 檔名時就發現同類命名有不一致的情況），精確匹配需要做模糊比對或人工維護一份對照表，不能單純假設字串完全相等

4. **[本輪新增，UI 溯源需求] 每一筆讀進來的資料需附加 `source_file` 欄位（僅檔名，不含路徑）**：第三階段的選檔策略確定會同時載入多個 `.ril`/`.iml`（精確匹配 + 極性退路合併檔），合併進同一個查詢清單後，**若不記錄來源，使用者在候選結果裡會無法分辨某筆候選是從哪個資料庫檔案來的**，也就無法追溯／核對原始資料。`load_ril(path)`/`load_iml(path)` 讀取時，每一筆 row 需附加 `row["source_file"] = os.path.basename(path)`；由於比對函式（第五階段）是直接複製整個 row 字典再附加 `delta` 欄位，這個欄位會自動隨比對結果傳遞到第六階段輸出與第十階段面板，不需要在比對邏輯裡另外處理。

---

## 第四階段：RT→RI 轉換 —— 已實作（v3）

**模組**：`calibration.py` + `reference_series.py`

> **[已實作 draft.22 — 對應 tag v3] 本階段已從「最大缺口」變成可端到端執行。**
> 以下 1–14 點的設計與逆向調查全部保留（是決策的依據），此摘要說明「最後長成什麼樣」：
>
> - **找 6 個錨點（自動、免模板）**：`select_homolog_ladder(peaks, 6)` 從 STD 偵測到的
>   ~9–14 個峰中，挑「DT_rel 嚴格遞增、間距最均勻、突出度最大」的 6 個同系物錨點
>   （呼應第 5、8 點）。實測從 141215 STD 還原出文獻的 6 點（282/334/400/522/697/949s，
>   DT_rel 間距 std 0.0034）。另有 `pin_anchors()` 可用已知 RT 模板釘定（第 5 點）。
> - **化合物身分與 RI 值**：STD 為 C4–C9 的**酮**（第 13 點；draft.23 更正——先前
>   寫「甲基酮」是本專案推論，已撤回）。6 個 RI 值
>   `[589.4,688.6,784.2,892.2,996.5,1095.6]` 目前為**借用值**（`assumed=True`、
>   `confidence="borrowed_cross_referenced"`，只借 RI/Y、Rt/X 用本批實測），完整來源與
>   升級條件見 `ketone_RI_provenance.md`。系列定義在 `reference_series.py`
>   （`ketone`/`n_alkane`/`custom`，第 8 點的 `series_key` 架構）。
> - **內插與外插**：`build_calibration()` 存 `log10(RT)` 錨點；`make_rt_to_ri()` 是
>   **唯一共用**的 `RT→(RI, extrapolated)` 函式（peak 值與熱圖軸都走它，避免不一致）。
>   範圍外**外插並標記**（`ri_extrapolated`），不 clamp（draft.21 定案）。數學與檢查清單
>   見 `RT_to_RI_normalization_math.md`。
> - **資料夾三態解析 + 快取**（第 12 點）：`resolve_ri_calibration()` =
>   `batch_own_std` / `borrowed_from_registry`(`days_gap`) / `unavailable`，
>   session + `_folder_calibration.json` sidecar 快取，跨檔案共用。品質前置過濾
>   （第 6 點）用峰數 + 強度擋掉訊號缺失的 STD（如 012251）。
> - **provenance**：`assumed_unverified` / `ri_confidence` / `ri_mode` / `ri_source`
>   隨校正表與每個 peak 一路傳進 `_peaks.json`，與 Stage 2 `k0_mode` 對稱。
> - **UI/顯示**：`identify.py` 串入本階段；桌面 UI 選資料夾即背景解析（STD 未偵測會
>   自動先偵測）、峰表新增 **RI 欄**、所有熱圖 y 軸重採樣成**線性 RI 軸**
>   （`warp_rows_to_ri`，非 log；x 軸 drift 正規化不動；`_bg.json` 記 `y_axis` 供 UI 擺圈）。
>
> **仍未定案**：RI 為借用值，升級成 verified 見 provenance 文件。以下原始設計/調查記錄保留。

1. **問題**：VOCal 原始碼裡沒找到「現場計算」的公式，只找到已經算好、存在專案檔（`.gasprj`）裡的校準表：

   **[VOCal 資料格式驗證]**：
   ```json
   "RI_Normalization": {
     "Values": [{"ColNormY": RI值, "ColNormX": log(Rt)值}, ...6 組],
     "ColNormisLog": true
   }
   ```

2. **[本輪驗證確認] `ColNormX` 的單位是 `log10(保留時間秒數)`，不是取樣索引，與 RT 軸解析度無關**：實際用專案檔裡 `Compounds` 清單的 `ethanol-M` 真實資料（`RI=420.9`、`Rt=44.358秒`）反向驗證——把 `log10(44.358)=1.6470` 代入校準表最低兩點做線性外插，算出 `RI=420.89`，與專案檔記錄值誤差僅 `0.013`，確認吻合。這代表校準表存的是真實物理時間（秒），**不是「第幾條光譜」這種會隨取樣密度（`Chunk sample rate`/`Chunk averages`）改變的索引值**——只要兩次量測的秒數準確，換算結果就能跨儀器/跨取樣設定穩定重現，不需要擔心 RT 軸解析度不一致的問題。此結論僅適用本階段（y 軸/RT 的 RI 校準），**與第一階段 RIP 正規化（x 軸/DT）是兩個獨立機制，不要混淆**——RIP 正規化本身也與解析度無關，但原因不同（見第一階段），兩者不要互相援引對方的理由。

3. **[VOCal 反編譯驗證，本輪新增] `Dlg_EditColumnNorm` 對話框（`bj.java`/`B.java`/`aV.java`）：VOCal 不記錄、也無法重建校正標準品的化合物身分**。CFR 反編譯 `VOCal_412_obf.jar` 後追蹤 `ColNormX`/`ColNormY` 的寫入路徑，實際對應到一個標題為 `"RI normalization"` 的 `JDialog`。從 `messages_EN.properties` 撈出的介面文字（`Dlg_EditColumnNorm.*`）顯示：這是一個**純數值 X-Y 點編輯器**——操作者在圖上點選或用剪貼簿貼上 `(RT, RI)` 數值對，介面上沒有化合物名稱欄位、沒有系列選單（不是從烷烴/酮類清單挑選），不連任何化學資料庫。**結論：標準品的化合物身分只存在操作者的外部記憶或紀錄裡，VOCal 的資料結構本身沒有、也不可能有這個欄位**——這不是資料遺失，是系統設計上的空缺。

4. **[VOCal 反編譯驗證，本輪新增，修正前一輪錯誤推論] `Use Fit` 核取方塊會把原始輸入值改寫成擬合值，非整百 RI 不能用來判斷標準品系列**：`bj.java` 依核取方塊狀態，決定寫入 `aV.s`/`aV.t`（最終存檔為 `ColNormX`/`ColNormY`）的來源是原始手動輸入點，還是 `B.java` 內部由擬合函式重新取樣、四捨五入到小數點後三位的「擬合點」：
   ```java
   // bj.java —— 依 Use Fit 核取方塊決定來源
   if (!b.isSelected()) { aV.s[n2] = bj2.c.p.get(n2); aV.t[n2] = bj2.c.q.get(n2); }  // 原始點
   if ( b.isSelected()) { aV.s[n2] = bj2.c.n.get(n2); aV.t[n2] = bj2.c.o.get(n2); }  // 擬合點
   // B.java —— 擬合點的產生邏輯
   n.add(0.001 * Math.round(fitFn.a(x)));  o.add(0.001 * Math.round(fitFn2.a(x)));
   ```
   **本輪對話中途曾主張「已知某支 `.gasprj` 校準表的六個 RI 值非整百（例如 589.4、688.6），因此推測標準品不是烷烴系列」——這個推論已確認不成立**：只要存檔當下勾選了 `Use Fit`，即使原始標準品確實是精確整百 RI 的烷烴，存檔值也會被擬合演算法改成帶小數的近似輸出。**非整百這個特徵無法用來判斷標準品系列，後續不應再以此做篩選依據。**

5. **[本輪實測，draft.18 更新為 6 點] 用真實 `260625_141215_STD.mea`（`COFFEE-40RAW` 方法）驗證讀取與找峰流程，確認 6 個乾淨候選錨點**：

   二進位格式：純文字表頭以 `nom Drift Tube Length` 那一行的換行符號結尾，資料區起點為該換行符號位置 **+2**（+1 換行本身，+1 一個額外對齊位元組，已用兩支檔案交叉驗證），矩陣為 `int16` little-endian、形狀 `(20413, 3150)`，與表頭 `Chunks count`/`Chunk sample count` 一致。RIP index=680（drift time 4.533ms，`find_rip()` 邏輯不變）。

   **⚠ 下表 RT 為修正前的舊保留時間軸**（少了 `(averages+1)` 的 +1，是實際值的 6/7；見 `readGAS.RT_AXIS_VERSION` 與 `ketone_RI_provenance.md` §0.0 的換算表）。DT_rel 不受影響。

   | RT (s) | 強度 | DT_rel（相對 RIP） |
   |---|---|---|
   | 282.0 | 4937 | 1.104 |
   | 334.3 | 2500 | 1.234 |
   | 400.3 | 3230 | 1.356 |
   | 521.8 | 3189 | 1.487 |
   | 697.0 | 2932 | 1.613 |
   | 949.0 | 2213 | 1.737 |

   ~~**訊號範圍確認只落在 RT 258–949s**，此範圍外（含 RT<250s 與 RT>950s 直到檔案結束的 2572s）皆為平坦背景，殘差雜訊僅個位數，無真實峰。~~

   > **⚠ [draft.24 撤回] 此判定不成立。** `260625_141215_STD_peaks.json` 在該範圍外有 3 個真實峰（舊軸 1305.7 / 1308.0 / 2445.8），**其中 1305.7 就是 C9（2-nonanone）**。這個錯誤的「平坦背景」結論正是 C9 長期未被納入錨點的原因之一。詳見第 13 點。

   **[draft.18 新增] 347.9s 這個候選點改標記為「疑似拖尾偽影，予以排除」**：原本另有一個候選點在 RT=347.9s（強度 2025），與 334.3s 同屬待釐清雙峰。定量比較兩種六點組合的間距均勻度後（`np.diff().std()`）：納入 334.3、排除 347.9 的組合，DT_rel 間距標準差僅 0.0034（六點依序 1.104/1.234/1.356/1.487/1.613/1.737，間距 0.130/0.122/0.131/0.126/0.124，近乎等差），比納入 347.9 的組合（DT_rel 標準差 0.0797）小約 23 倍。且 347.9s 這個候選點的 DT_rel（1.104）與 282.0s 完全相同（不是接近，是同值），不符合同系物序列該遞增的預期，較合理解釋是 282.0s 那個全域最強峰（4937）的拖尾/肩峰，非獨立化合物。**此判斷依據為 DT_rel 間距均勻度的定量比較，非化合物身分確認**，log10(RT) 間距在此六點組合下反而略不如原 5 點組合均勻（標準差 0.0135→0.0247），推測與方法本身的多段升溫程式（`Program` 欄位）有關、GC 保留時間不保證嚴格 log-linear，但此推論未經進一步驗證，維持為推測層級。

   334.3s/347.9s 這組雙峰最初考慮是否為同一化合物的 monomer/dimer——原先假設「M/D 的 RT 應幾乎相同」已被另一支真實 `.gasprj`（`Auto_Project_Backup.gasprj`）的 `Compounds` 清單推翻：`ethanol-M`（Rt=44.358s）與 `ethanol-D`（Rt=47.53s）相差達 3.17 秒，證明這只是經驗傾向非保證。目前依 DT_rel 均勻度判斷排除 347.9、保留 334.3 作為第六個錨點。

6. **[本輪實測] 品質前置過濾的實例驗證**：同一天另一支 `260625_012251_STD.mea` 表頭同樣標記 `Status="doubtful"`，`Status comment` 列出 `FlowGClow`/`SeptumDurability`/`NoValidSnapshot` 三項未解決診斷，實測只找到 3 個峰（RT 330/342/412s），連 141215 最強的 282s/4937 強度峰都完全缺失。**這是第九階段設計的 `Status` 前置過濾邏輯在真實資料上的具體驗證案例**：012251 應予排除，不與 141215 做時間加權的 bracket 校準內插，否則會把壞資料的誤差線性摻進所有中間時間點樣品的校正結果裡。

7. **[待決策，未變]** 化合物身分是整條鏈路唯一真正卡住的缺口：需要 STD 標準品證書、採購記錄，或操作者當初配製/操作 VOCal 時留下的對照紀錄，才能把上表的 5–7 個錨點指派絕對 RI 值。無法從 RT/DT 座標、`.gasprj` 資料結構、或 VOCal UI 反推——GC-IMS 沒有質譜，這是技術上的硬限制，不是資料不齊全。

8. **[設計提案，本輪對話討論，非反編譯驗證] `series_key` 可插拔架構**：在化合物身分確認之前，把「標準品是哪個系列」做成可替換的設定，`calibration.py` 核心邏輯不綁定任何特定系列：

   ```python
   # reference_series.py
   REFERENCE_SERIES = {
       "n_alkane":      {"assumed": True, "ri_formula": lambda n: 100*n, "assumed_start_carbon": 6},
       "ketone": {"assumed": False, "ri_values": None},   # 待對照表
       "custom":        {"assumed": False, "ri_values": None},   # 使用者直接指定
   }
   ```
   `assumed_unverified` 這個信心標記需比照 Stage 2 `k0_mode` 的 provenance 原則，全程跟隨 RI 值寫進 `_peaks.json`，下游比對不可把不同信心等級的結果混為一談。

9. **[設計提案，本輪對話討論，優先於烷烴假設] `single_point_relative` 降級模式**：在化合物對照表到位之前，建議先實作這個模式打通全流程——只用 141215 這支 STD 的 6 個乾淨錨點（282/334.3/400.3/521.8/697/949s）做內部相對位置比對，`known_ri_available=False` 明確標記，不指派任何絕對 RI 值，避免把未驗證假設偷渡成既定事實。等對照表到位後，改用 `series_key` 指定實際系列重跑一次即可升級，`calibration.py` 核心邏輯不需更動。

10. **有校準資料後的計算邏輯**：等化合物對照表確認、`series_key` 指定實際系列後，套用第 2 點已驗證的 `log10(Rt)` 分段線性內插（**不是** Van den Dool–Kratz 那個線性 Rt 版本——FlavourSpec 為等溫操作，且第 2 點已用真實資料驗證校準表存的是 `log10(Rt)` 空間，故應使用對應的 Kovats log 形式：`RI = 100n + 100×[log(Rt)-log(Rt_n)]/[log(Rt_(n+1))-log(Rt_n)]`，先前版本記錄的線性公式與此處已驗證的資料格式不一致，以此為準）。

11. **沒有校準資料時的另一條退路**：直接用 `.iml` 庫內建的 `Rt[sec]` 欄位做保留時間比對（精度較低，且只能對 `.iml`，因為 `.ril` 沒有存 Rt 只有存 RI）。

12. **[設計提案，本輪對話討論，非反編譯驗證] 批次資料夾內沒有 STD 檔案時的三層解析邏輯**：判斷「有沒有 STD」不應依賴檔名慣例（操作者可能漏打/打錯），改讀表頭 `Sample` 欄位是否為 `"STD"`：

    ```python
    def resolve_ri_calibration(folder_path, instrument, column, method,
                                registry_path="ri_calibration_registry.json"):
        std_files = scan_folder_for_std(folder_path)  # 依表頭 Sample=="STD" 判斷，非檔名
        if std_files:
            return build_calibration_from_std(std_files), "batch_own_std"

        registry = load_registry(registry_path)
        key = f"{instrument}|{column}|{method}"
        if key in registry:
            entry = registry[key]
            days_gap = (today() - entry["built_date"]).days
            return entry["calibration"], f"borrowed_from_registry(days_gap={days_gap})"

        return None, "unavailable"
    ```

    三種情境：**(a) 批次內有 STD**——走第 5–9 點已定案的品質過濾 + `single_point_relative`/`series_key` 流程；**(b) 批次內沒有，但同一套「儀器＋管柱＋方法」組合過去建過校正**——新增持久化的 `ri_calibration_registry.json`（比照 Stage 2 `calibration_profile.json` 綁定同組合共用的精神），借用舊校正曲線，但信心標記必須帶 `days_gap`（校正曲線建立至今的天數），下游依此分級，不能跟本批次自建的校正混為同等品質，`days_gap` 過大時（門檻無理論值，需實測資料校準）應視同不可用；**(c) 兩者皆無**——走第 11 點的 `.iml` 直接比對退路，或標記 `ri_mode="unavailable"`，讓 Stage 5 比對邏輯跳過 RI 維度、只靠 K0 維度（若可用）。此設計與 Stage 2 `k0_mode` 的 `standard_based`/`raw_parameters`/`unavailable` 三態對稱，RI 與 K0 兩個維度應各自獨立標記 provenance，不可因其中一個缺失就整峰放棄比對：

    ```json
    {
      "peak_id": 12,
      "ri_value": 892.3,
      "ri_mode": "borrowed_from_registry",
      "ri_confidence_note": "days_gap=45, 非本批次自建校正",
      "k0_value": null,
      "k0_mode": "unavailable"
    }
    ```

13. **[draft.24 定案] 化合物身分已確認：2-alkanone（甲基酮）系列，C4–C9** —— 由經理提供的對照表 `kintonemixed-C4-C9.xlsx` 確認，六列各帶 CAS，核對無誤（78-93-3 / 107-87-9 / 591-78-6 / 110-43-0 / 111-13-7 / 821-55-6）。

    **這解決了本階段第 7 點長期列為「整條鏈路唯一真正卡住的缺口」的化合物身分問題**——該缺口原判定為「GC-IMS 沒有質譜，技術上無法從專案自身資料反推」，最終確實不是靠專案資料解決的，而是靠外部文件。第 7 點可標記為已解決。

    身分認定經過三個版本，記錄於此以免日後誤讀：

    | 版本 | 認定 | 依據 |
    |---|---|---|
    | draft.19 | C4–C9 甲基酮，2-butanone→2-nonanone | 使用者說「酮、C4–C9」＋**本專案推論**（DT_rel 呈等差階梯 → 猜 2-alkanone） |
    | draft.23 | 只認到「C4–C9 的酮」，撤回結構指派 | 使用者更正：材料是酮，不是甲基酮 |
    | **draft.24（現行）** | **2-alkanone，C4–C9，已確認** | 經理對照表 + CAS |

    系列 key 維持 `ketone`（對應混標品名），成員標籤使用確認後的具體化合物名並附 CAS/Formula/MW。

    **[draft.24] 對照表同時提供了 `Dt [a.u.]`，據此發現 `select_homolog_ladder()` 挑錯了錨點**（詳見 `ketone_RI_provenance.md` 第 3 節）。

    第一輪只把 Dt 拿來對「程式已挑出的那六個錨點」，得到的是**碳數整體錯位一格**的結論：現行「6 錨點 = C4…C9」平均 |Δ| = 0.1264，「第 2–6 個 = C4…C8」平均 |Δ| = 0.0028（差 45 倍）。第二輪改用 Dt 去**全部 37 個偵測峰**裡配對（不預設錨點就在那六個之中），才看到完整答案——**六個化合物全部都在，包括 C9**：

    > ⚠ 據此撤回第一輪衍生的「C9 從未被偵測到、本批可用錨點只有 5 個（C4–C8）」這個說法。那是「只在既有六個錨點裡找對應」這個錯誤前提造成的，不是資料本身的結論。

    | 化合物 | 對照表 Dt | 實測 RT (s) | 實測 DT_rel | Δ | 強度 | 現行指派 |
    |---|---|---|---|---|---|---|
    | 2-butanone (C4) | 1.23938 | 389.7 | 1.234 | 0.0056 | 2498 | 誤標為 C5 |
    | 2-pentanone (C5) | 1.35521 | 467.0 | 1.356 | 0.0007 | 3230 | 誤標為 C6 |
    | 2-hexanone (C6) | 1.49035 | 609.5 | 1.487 | 0.0036 | 3189 | 誤標為 C7 |
    | 2-heptanone (C7) | 1.61390 | 813.4 | 1.613 | 0.0007 | 2927 | 誤標為 C8 |
    | 2-octanone (C8) | 1.73359 | 1107.2 | 1.737 | 0.0032 | 2213 | 誤標為 C9 |
    | 2-nonanone (C9) | 1.85714 | **1523.4** | 1.854 | 0.0027 | 1028 | **未被選入** |

    RT 與 DT_rel 皆嚴格遞增，平均 |Δ| = 0.0027。**正確錨點集 = `[389.7, 467.0, 609.5, 813.4, 1107.2, 1523.4]`，仍是 6 點**；修正前程式挑的是 `[329.6, 389.7, 467.0, 609.5, 813.4, 1107.2]`——多納入不屬於此序列的 329.6 s（DT_rel 1.104，全圖最強峰 I=4936），並漏掉 RT 1523.4 s 的 C9。

    **⚠ 本表 RT 為 2026-08-12 修正後的保留時間軸**（`(averages+1)×trigger`，見 `readGAS.RT_AXIS_VERSION`）。本階段第 5 點與 draft.16/18 記錄的 282.0/334.3/400.3/521.8/697.0/949.0 是修正前的舊軸，為同一批峰的 6/7 倍表示，換算表見 `ketone_RI_provenance.md` §0.0。

    **⚠ 連帶更正本階段第 5 點的一項實測記錄**：該點寫「訊號範圍確認只落在 RT 258–949s，此範圍外（含 RT>950s 直到檔案結束的 2572s）皆為平坦背景，殘差雜訊僅個位數，無真實峰」。實際上 `260625_141215_STD_peaks.json` 在 RT>1000 s 有 **3 個峰**（新軸）：1523.4（DT_rel 1.854，I=1028）、1526.0（1.406，I=1188）、2853.4（1.143，I=335）。**C9 就在其中**，「平坦背景」的判定不成立。

    **為什麼啟發式會挑錯（影響 draft.18 的方法論結論）**：錯誤組合的 DT_rel 間距標準差是 **0.0034**，正確組合是 0.0046；且錯誤組合含突出度 4508 的最強峰，突出度總和也較大——**兩個評分準則都指向錯誤答案**。draft.18 當初以 std=0.0034（比替代方案小 23 倍）作為六點定案的強證據，現在看來間距均勻度無法分辨「真正的同系物階梯」與「恰好等距的混合物」：碳數指派需要外部依據，不能靠形狀推論。（draft.18 排除 347.9、保留 334 的判斷仍是對的，只是理由不完整。）

    **[已修正] 新增 `calibration.match_anchors_by_dt()`，取代間距啟發式**。`build_from_std_peaks()` 的分支改為：系列帶 `dt_values` → 走 Dt 配對；沒有（如 `n_alkane`）或配對結果不單調 → 退回 `select_homolog_ladder()`。`anchor_selection.mode` 新增 `dt_matched`，配對明細記於 `anchor_selection.dt_match`。實測 `dt_matched`、6/6 命中、平均 |Δ|=0.00273、單調，錨點為 `[334.0, 400.3, 522.4, 697.2, 949.0, 1305.7]`。部分命中時 RI 依 `_dt_index` 取對應項，不會錯位。**RT 涵蓋上界由 949 s 延伸到 1305.7 s**，`ri_extrapolated` 的峰因此變少；**既有 `_peaks.json` 的 `ri` 需重跑才會正確**。`test_ketone_dt_values_expose_the_anchor_off_by_one()` 保留為迴歸護欄（記錄舊挑法錯在哪的算術）。

    **[原始記錄，draft.19]** 精確 RI 值仍缺：酮的 RI 依定義不是整百（不像正構烷烴 RI=100n 是定義本身），是**相對於烷烴尺實測出來的**數值，查過 NIST WebBook 只取得 2-butanone 在極性管柱（DB-Wax）的 Kovats RI（~917–950），與本專案的 SE-54（非極性/中等非極性）管柱不可比，未查到可用的非極性管柱數值。*（「`ri_values` 查找表仍是空的」已被 draft.22 推翻。而 draft.24 值得注意的巧合是：對照表的 2-butanone = 916.84，幾乎正中此處記錄的**極性管柱** 917–950 區間——這正是第 2 節管柱極性疑慮的由來。）*

14. **[本輪對話補充，draft.20] RT→RI 套用步驟的數學細節，獨立成檔** `RT_to_RI_normalization_math.md`：內容涵蓋分段線性內插的五步驟推導（建表需先取 `log10(RT)`，查詢新峰時同一個峰的 RT 也要先取 `log10` 才能查表——這是實作最常漏掉的一步）、手算範例（`RT=450s → RI=644.1`，可直接當單元測試 ground truth）、`np.interp`/`scipy.interp1d` 的 clamp vs 外插行為差異，與五項實作檢查清單。**外插時該 clamp 還是真的沿邊界斜率外插，此處未定案**，需要之後另外決定並補回此節。

---

## 第五階段：容許窗比對

**模組**：`match.py`

> **[已實作，本輪更新] 漂移軸改走 RIPrel（免 K0）。** 掃描 `.iml` 庫發現漂移多以
> `DtMode="RIPrel"`（相對 RIP 的漂移）儲存——與峰的 `drift_relative` 同一物理量、
> 單位一致。因此 IMS 維度**不必等 K0 校準**即可運作：`match_drift_rel()` 直接用
> `peak.drift_relative` 比對 `DtMode=="RIPrel"` 的 `Dt[a.u.]`（±0.05 佔位）。`match_all`
> 的 IMS 分支優先 K0（有 `k0_value` 時，比對 `DtMode` 含 "K0"），否則走 RIPrel 漂移；
> 回傳新增 `ims_dimension`（`"k0"`/`"drift_rel"`/None）。`.iml` 對 IMS 而言不綁 GC 管柱，
> 故比對時載入**全部** `.iml`（DtMode 篩選確保 RIPrel 與 1/K0 不混比）。實測咖啡樣品
> 33 個峰因此取得 combined 命中（如 RI 1097/漂移 1.139 → Guaiacol）。UI 三欄
> GC(RI)/IMS/GC×IMS 對應顯示：最近 RI 值、最近漂移值、兩軸皆中的化合物。

1. **核心邏輯** **[VOCal 反編譯驗證]**（`S.h()`）：`center ± tolerance` 區間判斷
2. **兩個獨立維度**：
   - GC/RI 維度：`peak_ri` vs `.ril`/`.iml` 的 `RI` 欄位（無 RI 時退 `Rt[sec]`）
   - IMS 維度：優先 `peak_k0` vs `Dt[a.u.]`（`DtMode` 含 K0）；否則
     `peak.drift_relative` vs `Dt[a.u.]`（`DtMode=="RIPrel"`）——**免 K0 校準**
3. **輸出三個候選清單** **[設計沿用，非逐字反編譯]**：
   - `gc_matches`：只有 GC（RI/RT）命中
   - `ims_matches`：只有 IMS（K0 或 RIPrel 漂移）命中
   - `combined_matches`：GC 與 IMS 都命中且為同一 CAS 的交集（**最可信**，兩軸交集
     把 RI 單軸的數百候選收斂到少數）
4. **[待決策]** 容許窗寬度：
   - VOCal 是使用者手動輸入，本專案可考慮做成固定值、或依 `peaks.py` 已算出的 `flatness`/`prominence` 動態調整
   - 沒有「正確答案」，需要拿幾個已知化合物的真實資料試跑校準

### 5.1 漂移軸（IMS）比對怎麼找到化合物——數學式與流程圖

**先講清楚一個關鍵：兩邊比的是同一個「相對 RIP 的無因次比值」，不是絕對漂移時間。**

**（a）峰的漂移相對值 $d_p$（第一階段產出）**

反應離子峰（RIP）在漂移軸上的取樣點索引：

$$r = \arg\max_{j \ge 200} \; I[0,\,j]$$

（取 RT=0 那一列、跳過前 200 點後的最大值位置。）每個峰用自己的漂移索引 $j_p$（`dt_index`）除以 $r$，得到相對值：

$$d_p = \frac{j_p}{r}$$

所以 RIP 本身 $d_p = 1.0$；$d_p = 1.13$ 代表「比 RIP 多跑 13%」。這是**無因次比值**，不受取樣率、管長、溫壓影響。

**（b）資料庫的漂移值 $d_\ell$**

`.iml` 庫每一列的 `Dt[a.u.]` 存的漂移值，其單位由同列的 `DtMode` 標記。同一個化合物可能以不同慣例出現：

| `DtMode` | `Dt[a.u.]` 的意義 | 能不能直接跟 $d_p$ 比 |
| --- | --- | --- |
| `RIPrel` | 相對 RIP 的比值（庫自己量的 $j_\ell / r_\ell$） | **可以**，同為無因次比值 |
| `1/K0` | 逆約化遷移率 | 不行，要先做 K0 校準 |
| `ms` | 原始漂移時間（毫秒） | 不行，單位不同 |

因此比對前先用 `DtMode == "RIPrel"` 過濾，只留下與 $d_p$ **同單位**的列（`match_drift_rel()` 的第一道關卡）。

**（c）比對條件（容許窗）**

對每一個通過 `RIPrel` 過濾的庫列，計算誤差並判斷是否落窗：

$$\Delta_d(\ell) = \bigl|\, d_\ell - d_p \,\bigr| \qquad\text{命中} \iff \Delta_d(\ell) \le \tau_d,\quad \tau_d = 0.05$$

命中的列依 $\Delta_d$ 由小到大排序，最相近的排最前——那一筆的 $d_\ell$ 就填進 UI 的 **IMS** 欄（如 `1.139 (Δ0.026)`）。

**（d）雙軸交集 → 真正的 GC×IMS 鑑定**

單看漂移只說「有化合物漂移位置接近」；可信的鑑定來自和 GC/RI 軸取交集。RI 軸的命中條件同型：

$$\Delta_{RI}(\ell) = \bigl|\, RI_\ell - RI_p \,\bigr| \le \tau_{RI},\quad \tau_{RI} = 5$$

`combine_by_cas()` 以 **CAS 號**為鍵，找「同一個 CAS 同時出現在 RI 命中清單與漂移命中清單」的化合物：

$$
\text{combined} = \Bigl\{\, c \;\Big|\;
\underbrace{\exists\,\ell_{gc}:\ \mathrm{CAS}(\ell_{gc})=c,\ \Delta_{RI}(\ell_{gc}) \le \tau_{RI}}_{\text{GC 軸中}}
\ \wedge\
\underbrace{\exists\,\ell_{ims}:\ \mathrm{CAS}(\ell_{ims})=c,\ \Delta_{d}(\ell_{ims}) \le \tau_{d}}_{\text{IMS 軸中}}
\,\Bigr\}
$$

交集結果依綜合誤差 $\Delta_{RI} + \Delta_d$ 排序，填進 **GC×IMS** 欄。兩軸同時吻合，把 RI 單軸動輒數百個候選收斂到少數幾個。

**流程圖（`match_all` 對單一峰）**

```mermaid
flowchart TD
    P["峰 peak<br/>d_p = drift_relative<br/>RI_p = ri"] --> GC{"有 RI_p ?"}

    subgraph GCaxis["GC / RI 軸"]
        GC -- 是 --> MRI["match_ri：掃 .ril + .iml<br/>命中 ⟺ |RI_ℓ − RI_p| ≤ 5<br/>存 Δ_RI，依 Δ_RI 排序"]
        GC -- 否，但有 retention_s --> MRT["match_rt 退路<br/>|Rt_ℓ − Rt_p| ≤ 5s"]
    end

    P --> IMS{"有 k0_value ?"}
    subgraph IMSaxis["IMS / 漂移軸"]
        IMS -- 是 --> MK0["match_k0：DtMode 含 K0<br/>|Dt_ℓ − k0_p| ≤ 0.05<br/>（需 K0 校準，目前休眠）"]
        IMS -- "否，但有 d_p" --> MDR["match_drift_rel<br/>① 先篩 DtMode == RIPrel<br/>② 命中 ⟺ |Dt_ℓ − d_p| ≤ 0.05<br/>存 Δ_d，依 Δ_d 排序"]
    end

    MRI --> GCH["gc_matches"]
    MRT --> GCH
    MK0 --> IMSH["ims_matches"]
    MDR --> IMSH

    GCH --> CB["combine_by_cas<br/>取兩清單「同 CAS」交集<br/>依 Δ_RI + Δ_d 排序"]
    IMSH --> CB
    CB --> OUT["combined_matches → GC×IMS 欄<br/>（最可信：兩軸皆中）"]
```

> **目前狀態**：K0 分支休眠（無校準標準品），所以 IMS 軸實際走 `match_drift_rel`（RIPrel）。$\tau_d = 0.05$ 與 $\tau_{RI} = 5$ 皆為未校準佔位值，需拿已知化合物真實資料驗證。

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

| 規則編號 | 函式名稱 | 規則說明 | 強制 | 套用時機 | 狀態/來源 |
|---|---|---|---|---|---|
| R001 | `rule_min_prominence` | 突出度低於門檻剔除（**實作為絕對值**，見下方說明） | 否 | 偵測後 | 遷移自 `peaks.py` 現有 `prom_frac` 邏輯 |
| R002 | `rule_max_candidates` | 依突出度排序，只保留前 N 名 | 否 | 偵測後 | 遷移自 `peaks.py` 現有 `top_n` 邏輯 |
| R003 | `rule_max_flatness` | 平坦度高於門檻視為 plateau，剔除 | 否 | 偵測後 | 本專案新規則，`draft.02` 提出 |
| R004 | `rule_exclude_rip_band` | 漂移相對值落在 RIP 位置（x=1）± `half_width` 內的峰予以剔除，避免誤把 RIP 本身或其容許窗當成分析物峰 | **是** | **門檻前** | 本專案新規則，`draft.04` 提出；`draft.14` 改為強制且移到門檻前 |
| R005 | `rule_min_relative_steepness` | 突出度相對於峰自身峰高的比例低於門檻則剔除（相鄰峰之間鞍點不夠深，形似駝峰未明顯下凹） | 否 | 偵測後 | 本專案新規則，`draft.06` 提出 |
| R006 | `rule_exclude_before_rip` | `drift_relative ≤ boundary`（預設 1.0）的峰剔除：跑得比反應離子還快，物理上不是分析物 | **是** | **門檻前** | 本專案新規則，`draft.14` 提出 |

### `draft.14` 新增的兩個概念：強制規則與「門檻前」套用時機

**[本輪使用者決策 + 實測驗證]**

**(1) 強制規則（`mandatory`）**：`R004`／`R006` 不可停用。理由不是技術限制，而是**峰編號的基準線就建立在這兩條規則套用之後的集合上**——若允許關閉，整組編號會跟著重排，使用者在調參數時失去穩定的參照，第八階段要持久化的勾選狀態也會錯位。

強制必須落在 `rules.load_config()`／`save_config()` 這一層（`_enforce_mandatory()`），涵蓋「手動把 `enabled` 改成 `false`」與「整條從 `rules_config.json` 刪掉」兩種繞過方式。**只做成 UI 上的 disabled 核取方塊是假象**。被強制的是「這條規則會跑」，**不含它的參數**——`half_width`／`boundary` 仍可調整（但調整會重排編號）。

**(2)「門檻前」套用時機**：`R004`／`R006` 必須在 `max_prominence` 計算**之前**生效，不能等偵測完再篩。

原因是突出度門檻是**相對值**（`thresh = prom_frac × max_prominence`），而 RIP 通常就是全圖最大突出度。**實測 `260623_161351_A_1_3.mea`**：

| 設定 | 門檻前剔除 | `max_prominence` | 門檻 | 最終峰數 | `drift_relative` 範圍 |
|---|---|---|---|---|---|
| 兩條都關 | 0 | 5051.8（**是 RIP 本身**） | 101.0 | 37（其中 14 個是 RIP 柱狀帶） | 0.000 – 1.600 |
| 只有 R004 | 2,858 | 1545.2 | 30.9 | 37 | 0.000 – 1.600 |
| R004 + R006 | 101,973 | 1545.2 | 30.9 | **31** | **1.028 – 1.600** |

RIP 把門檻墊高了約 **3.3 倍**，正在把真實分析物峰壓在門檻底下砍掉。若把 `R004` 接在偵測之後，圈看起來一樣會消失，但**被墊高的門檻誤殺的弱峰救不回來**——這是「同一條規則、擺在不同位置，結果不等價」的實例。`test/test_select_from_maxima.py` 用合成資料把這個意圖鎖住：若有人日後把 `R004` 搬回偵測後，該測試會失敗。

`R004` 與 `R006` 同時啟用時，兩者的聯集等價於單邊切 `drift_relative > 1.0 + half_width`，實作上直接換算成 `dt_index` 的範圍做遮罩，不必為 25 萬筆候選建立 peak 字典。

**`R001` 的絕對值／相對值分歧**：本表原先描述為「相對於全圖最大突出度的比例」，但本節的範例程式與 `rules_config.json` 範例都用絕對值 `threshold`，實作照範例程式走**絕對值**（見 `rules.py` 內註解）。目前 `peaks.py` 內建的 `prom_frac`（相對值）尚未遷出，因此存在「相對門檻在前、絕對門檻在後」的雙層狀態，`R001` 填小於當前 `prom_frac` 門檻的值不會有任何效果。

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

> **[實作現況：此段尚未執行]** `--prom-frac`／`--top-n` 目前仍留在 `peaks.py`，而且
> `prom_frac` 已經因為 `R004`/`R006` 的「門檻前」需求被綁進 `select_from_maxima()`
> 的核心（門檻是相對值，必須在裁切之後、編號之前算），不再是可以單純刪掉的 CLI 旗標。
> 因此現況是**雙層門檻並存**：`prom_frac`（相對值，偵測時）在前，`R001`（絕對值，
> 偵測後）在後。實際後果是 **`R001` 填任何小於當前 `prom_frac` 門檻的值都不會有效果**
> ——那些候選在 `R001` 有機會看到它們之前就已經被相對門檻濾掉了。
> 這不是 bug，是 draft.14 的順序決策帶來的必然結果，但本段原文寫得像已完成，故補註。
> 要真正收斂成單層，得先決定 `R001` 改採相對語意、或讓 `prom_frac` 降為純粹的
> 「偵測層下限」而由 `R001` 承擔裁決——屬未定案項目，見 `status.md` open decisions。



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

**[draft.14 修正]** 上圖對 `R004`／`R006` 已不適用——這兩條強制規則必須排在 `peaks.py`
**內部**的突出度門檻之前，不能等 `peaks.py` 整支跑完。實際順序是：

```
peaks.py
  ├─ rip.find_rip()                       算 rip_index
  ├─ compute_prominence()                 25 萬個原始局部極大
  ├─ R004 + R006（門檻前裁切）            ← 強制，決定編號基準集合
  ├─ max_prominence → 突出度門檻          ← 門檻是相對值，故必須排在裁切之後
  ├─ min_distance 去重 → top_n
  ├─ 指派 peak_id = 1..N                  ← 編號基準線在此固定
  └─ rules.apply_rules()                  R001/R002/R003/R005，只做剔除、不重編號
        ↓
第八階段 UI 圈選（圈為 Canvas 原生物件，見第八階段）
```

`peaks.py` 因此不再是純粹的 measure-only——它現在會讀 `rules_config.json`。這是
`R004`／`R006` 的相對門檻依賴帶來的必然結果，記錄於此以免日後被誤認為架構退化。

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

### 既有 bug（3 個，皆已修復）

| # | 檔案 | 狀態 |
|---|---|---|
| 1 | `readGAS.py` `plot_heatmap()` | ✅ 已修復並驗證（draft.11） |
| 2 | `peaks.py` `write_overlay()` | ✅ 已修復並驗證（draft.11） |
| 3 | `peak_with_number.py` `write_overlay_numbered()` | ✅ 已修復（draft.12 發現，ver.02 修復）——重用 `peaks._downsample_for_display()`，不另建第三份複製 |

### 新模組（第一～七、十階段）—— ✅ **全部已實作（draft.26 更新）**

本節原文寫「檔案本身都還不存在」，那是 draft.13 時的狀態，早已不成立。現況：

| 模組 | 階段 | 狀態 |
|---|---|---|
| `rip.py` | 1 | ✅ 完成 |
| `dt_convert.py` | 2 | ✅ 完成，T/P 欄位對應已由反編譯確認；v3.2 起已接上資料夾解析路徑，UI 與 CLI 皆走 `standard_based` |
| `library.py` | 3 | ✅ 完成，含 drift gas 交叉檢查（保守語意） |
| `calibration.py` + `reference_series.py` | 4 | ✅ 完成，錨點改用 `match_anchors_by_dt()` |
| `match.py` | 5 | ✅ 完成，2-D（RI × drift_relative） |
| `identify.py` | 6 | ✅ 完成，`source_file` 溯源已串接；CLI 與 UI 共用 `load_libraries()` |
| `rules.py` | 7 | ✅ 完成，R001–R006，強制規則於突出度門檻前套用 |

原文提到 `identify.py` 尚未更新的兩項設計：`source_file` 溯源**已完成**；雙模式 K0
校準**也已完整接上**——`resolve_calibrations_cached()` 由同一支 STD 一次解出 RI 與
K0，CLI 與 UI 共用（v3.2）。*（此段先前寫「profile 還沒由資料夾解析路徑產生」，
該敘述在 v3.2 落地後即已過期。）*

**[draft.27] 第四階段新增第四層來源**：資料夾沒有 STD 時，改讀該資料夾 `.gasprj` 的
`RI_Normalization` 表（`ri_mode="vocal_project_table"`），排在 `batch_own_std` 之後、
`borrowed_from_registry` 之前。另注意 **`borrowed_from_registry` 目前不可能觸發**
（沒有 registry 檔、production code 從未呼叫 `save_registry()`、`main.py` 不傳 `dims`）
——設計是完整的，但接線缺一段，見 `status.md` open decision 8。

### UI（`main.py`）—— draft.26 更新：僅剩 Batch 8

- ✅ 工具列重新設計（按鈕改名、順序、取消 Read File 獨立按鈕）
- ✅ 主畫面改為 Canvas 原生物件（PIL 背景 + `create_oval` + `create_text`），取代 matplotlib 靜態圖顯示。實測圈位中位偏差 **1.2 px**
- ✅ 峰表擴充四欄（`On`／`GC×IMS`／`GC`／`IMS`）與 ▶ 觸發鈕
- ✅ 圈／表格列／核取方塊三方雙向同步（`toggle_peak()`），狀態以 `(rt_index, dt_index)` 為鍵持久化
- ◐ 半透明數字標示——數字用 Tk `-stipple` 達成；圈只能改用顏色模擬，因為 `-outlinestipple` 在 Windows 上被靜默忽略
- ✅ 規則管理面板（第七階段 mockup 已定案），改動後約 4 ms 重算整條漏斗
- ✅ **[draft.26 新增] 「ⓘ 軸說明」對話框**：說明兩軸的正規化，文字唯一來源為 `calibration.axis_explanation()`，故不可能與實際校正值不一致。RIP 數值取自 `_bg.json`（`rip_index` / `rip_drift_ms`）而非重算，確保講的就是畫面上這張圖
- ✅ 化合物比對面板（第十階段）——▶ 開啟候選清單，表格 `GC×IMS`／`GC`／`IMS` 三欄自動填值，不需點 ▶。**[draft.27]** 版面修正：表頭改一行一對欄位；底部計數與 Close 改為先佔位，預設視窗尺寸下即可見（先前被表格擠出畫面外）
- ✅ **[draft.27] 校準 STD 在檔案清單中標示**（依表頭 `Sample=="STD"`，非檔名）並排在最後；Generate Report 拒絕它——它是樣品所依據的尺標，報告自己等於循環論證
- ✅ **[draft.27] GC 欄標題反映實際維度**：無 RI 校準時 `match_all()` 退到保留時間比對，標題改為 `GC (RT s)`、格子帶單位、狀態列說明保留時間不可跨儀器／管柱／方法轉移。先前一律顯示 `GC (RI)`，等於把秒數掛在 RI 的名義下
- ⬜ 「Generate Report」按鈕（位置已定案，內容格式範例見 `Report_Content_Example.md`，實際匯出格式仍待決）**← 唯一未實作的功能**

### 卡住整條鏈路的三個關鍵決策 —— draft.26：三項全部解決

| # | 問題 | 現況 |
|---|---|---|
| 1 | ~~儀器常數（L/U/T/P）從哪來？~~ | ✅ **已解決（draft.26）**——`L`/`U` 早已可從表頭取得；`T`/`P` 由反編譯 VOCal 確認：`Start temp 1`，壓力為 ambient 與 EPC 之**和**。見第二階段 |
| 2 | ~~STD 標準品的化合物身分~~ | ✅ **已解決（draft.24）**——經理對照表帶 CAS，確認為 2-alkanone C4–C9。衍生項：(a) 對照表 RI 的**管柱極性未確認**（疑為極性，本批為非極性，差約 +302）**← 仍未解**；(b) ~~錨點碳數指派錯位一格~~ ✅ 已由 `match_anchors_by_dt()` 修正（draft.24 同日），6/6 命中、平均 \|Δ\|=0.00273。**[draft.27] (c) 新增同型問題**：走 `vocal_project_table` 的資料夾，其 `.gasprj` 校正表的**原始錨點與管柱極性同樣不可考**，且 `藝妓咖啡` 是**另一台儀器**（1H1-00088），答案未必與 (a) 相同 |
| 3 | ~~有沒有 K0 校準標準品資料？~~ | ✅ **已解決（draft.26）**——有，且不必新測：`GAS BASE 3H_IMS K0.iml` 的已知 K0 ＋ 本批 STD ＝ IC 25.0808、CV 0.133% |

原文寫「這些不是技術問題，是需要先盤點手上實際擁有的資料才能回答」——這句話後來被證明
是對的，而且**比預期更對**。三項裡有兩項（1 和 3）的答案完全不需要新資料：一項在儀器
隨附軟體的位元組碼裡，一項在已經放在 `library_data/` 裡的官方庫檔加上早就跑過的 STD。
真正需要外部輸入的只剩 2(a) 管柱極性，而那必須由產出對照表的人回答。

---

## 修訂記錄

- **draft.27**（2026-08-24，程式 tag **v3.3**）：**第四階段多一層來源，並修掉兩個「靜默降級」。**
  (1) **`vocal_project_table`：沒有 STD 的資料夾也能有 RI。** RI 無法從量測本身導出——它的定義就是拿已知 RI 的化合物內插，而 GC-IMS 沒有質譜，峰的身分推不出來。但 VOCal 早就替那些批次存了一份尺標：資料夾內 `.gasprj` 的頂層 `RI_Normalization` 區塊，格式 `{ColNormY=RI, ColNormX=log10(Rt), ColNormisLog=true}`，**與本階段所用的 Kovats log 形式完全相同**，故其點可直接作為錨點、不需任何換算。層級置於 `batch_own_std` 之後、`borrowed_from_registry` 之前：`.gasprj` 是**本批次自己**的尺標（同儀器、同管柱、同方法、同一次量測），registry 是**別批**的；兩者皆低於 STD，因為只有那條路的錨點是本專案認得且可驗證的。實測四個資料夾：兩個走 (a)，`鱸魚` 6 錨、`藝妓咖啡` 182 錨走 (a2)。⚠ 該表是 VOCal **重採樣後**的等距格點（`log10(Rt)` 每 0.01），**原始錨點不可考**，故 `assumed_unverified=True` 且標籤為 `vocal_project_table_anchors_not_recoverable`——與對照表的「管柱極性存疑」是不同性質的不確定性。短保留時間端為 VOCal 在自身第一個真實錨點以下的外插，實測最低 **RI = −631**；依 Kovats 定義（甲烷 = 100）截去前導段，**這是定義線不是調出來的門檻**（斜率法落點僅差 2 秒，但那會是配出來的數字）。截去的區段仍可查詢並自動標 `ri_extrapolated`，沿用既有機制而非另造一套。`ColNormisLog=false` 直接拒絕，不臆測換算——猜錯會產生整條錯位卻看起來正常的 RI，而手上沒有該格式的樣本可驗證。
  (2) **GC 欄先前把「秒」掛在 RI 的名義下。** `match_all()` 在 `peak["ri"]` 為 None 時退到 `match_rt()`，拿峰的 `retention_s` 比庫的 `Rt[sec]`（±5 s），兩條路徑寫進同一個 `gc_matches` 且呈現方式相同，而欄位標題寫死 `GC (RI)`——於是 RI 欄顯示 `—`、GC 欄顯示秒數，兩者並列卻無從分辨。**保留時間不跨儀器／管柱／方法轉移，這正是 RI 存在的理由**；實測該批每個峰在 ±5 s 內有 9–24 個候選，最接近者包括 2,6-dichlorophenol（Δ0.01 s）與 hexamethyldisiloxane（矽氧烷，其實是管柱流失）。**改為標示而非移除**（使用者裁決）：庫與樣品確實同方法時 RT 比對合理，能判斷的是使用者。
  (3) **`.ril` 選檔對舊版表頭靜默回傳 0 筆。** 舊排版 `GC Column` 沒有 `POLARITY:` 欄位，極性因此為 None，極性退路整條不啟動。新增 `infer_polarity_from_column_name()`——**對應關係取自儀器自己的講法**（同一台機器在新版表頭裡就把 SE-54 標成 `POLARITY: np`），非本專案的化學判斷；明寫值一律優先，來源記於 `polarity_source`；認不出的固定相不猜，因為載錯極性的庫比沒有庫更糟。實測 0 → 13 檔 / 117,329 列。
  (4) UI：校準 STD 在檔案清單中標示（依表頭非檔名）並被 Generate Report 拒絕；化合物面板版面修正。詳見待實作清單 UI 段。

- **draft.26**：**第二階段 K0 兩個卡了很久的未決項同時解決，且都不是靠新測量。**(1) **`raw_parameters` 的 `T`/`P` 欄位對應**——把儀器隨附的 VOCal `.jar` 反編譯（使用者自有軟體的互通性還原工程）後確認：`T` 取 `Start temp 1`（45 °C）；`P` 是 **`10 × (Start ambient pressure + Start pressure EPC IMS)`** ＝ 1018.43 mbar，出處 `BufferedMEA.java:313`。**壓力是「和」而非任一個**，因為 EPC 記的是相對環境的表壓，管內絕對壓力等於環境壓力加上它——這代表先前所有「六個 temp 挑哪個、兩個壓力挑哪個」的討論方向本身就錯了，正確答案不在選項裡。已寫進 `dt_convert.extract_raw_tp()`，`compute_k0()` 在未給 T/P 時自動導出。**但這只解決正確性，未解決準確度**：此路徑與標準品校準結果差 +3.5%，而相鄰同系物 K0 間距約 8%，誤差達一個間距的 43%，故 `raw_parameters` 仍不適合用於鑑定——這正是本階段開頭「讀對表頭欄位 ≠ 消除機器差異」該判斷的量化證實。(2) **`standard_based` 的校準標準品**——不需使用者提供。`library_data/GAS BASE 3H_IMS K0.iml`（G.A.S. 官方基礎庫，`DtMode=1/K0`）本就帶著這六個酮的已知 K0，而本批 STD 就是這六個化合物在這台機器上的實測；兩者早就都在手上，只是沒被放在一起看。`calibration.derive_k0_instrument_constant()` 逐錨解 `IC = K0_ref · t · U`，得 **IC = 25.0808、六錨 CV = 0.133%**、各錨殘差 < 0.25%。CV 千分之一是可信的關鍵：六個獨立化合物跨越整個漂移範圍仍一致，代表「把 `ah × L²` 整體校準」的假設在這台機器上成立。CV 超出 `max_cv` 時拒絕回傳，避免拿錯庫時靜默產生假常數。**尚未完成**：`resolve_ri_calibration()` 的資料夾／快取路徑還沒產生 K0 profile，故 UI 的 `k0_mode` 仍是 `unavailable`——常數已存在但未接上使用端，這是目前單一價值最高的待辦（接上後 IMS 軸從 RIPrel 一維變兩維）。(3) **保留時間軸公式更正**：`rt_step_ms` 應為 `(averages + 1) × trigger_repetition`，舊式少了 +1 使整條 RT 軸短 16.7%。四條獨立佐證：方法總長（新式落在整數分鐘）、經理對照表 `Rt[sec]` 殘差由 −16.7% 降到 ±1%、`gc-ims-tools` 0.1.10 的 `read_mea()`、VOCal `BufferedMEA.java:316`。強度矩陣、漂移軸、找峰、**RI 皆不受影響**（錨點與查詢值同時平移 `log10(7/6)`，分段線性內插對 x 平移不變，實測差 ~1e-13）。新增 `readGAS.RT_AXIS_VERSION`，隨產物寫入並在載入舊產物時警告——真正的風險是新舊靜默混用。(4) **修正一個靜默失效**：`peaks.load_surface()` 從 `.npz` 載入時把 `meta["source"]` 設成 `.npz` 自己，導致 RI 校正解析到 `results/`（沒有 STD）而退回保留時間軸卻不報錯，產出的圖與有 RI 的那批外觀無異、座標系卻不同。改由 `export_npz()` 存 `mea_source`。(5) **UI 新增「ⓘ 軸說明」**，見待實作清單 UI 段。
- **draft.25**：版號同步（隨程式碼 v3.1 一併調整），內容無變更。
- **draft.24**：**經理提供 `kintonemixed-C4-C9.xlsx`，一次解決一個舊缺口、揭露一個新錯誤。** (1) **化合物身分確認**——六列各帶 CAS，核對無誤，組成確為 2-alkanone（甲基酮）C4–C9；第四階段第 7 點「整條鏈路唯一真正卡住的缺口」就此解決（如當初所判定，不是靠專案自身資料解決的，而是靠外部文件）。draft.23 撤回的結構指派因此恢復，成員標籤改回具體化合物名並補 CAS/Formula/MW，系列 key 維持 `ketone`。(2) **RI 數值改用對照表**（916.8372…1392.9），取代借自鱸魚專案的 589.4…1095.6；兩組差值 +289~+327（均值 +302，跨六點高度一致），而 2-butanone 的 916.84 幾乎正中 NIST 的**極性管柱** 917–950，本批管柱卻是非極性 SE-54——**管柱極性疑慮未解**，使用者知悉後裁決採用，`assumed=True` 保留、`confidence` 改為 `supplier_table_column_polarity_unverified`。(3) **[重要] 用對照表的 `Dt` 發現 `select_homolog_ladder()` 挑錯錨點**：拿 Dt 去全部 37 個偵測峰配對，六個化合物全部命中（平均 |Δ|=0.0027，RT/DT 皆嚴格遞增），正確錨點集為 `[389.7, 467.0, 609.5, 813.4, 1107.2, 1523.4]`（新軸）；修正前挑的 `[329.6, 389.7, 467.0, 609.5, 813.4, 1107.2]` 多納入了不屬於此序列的最強峰、漏掉 RT 1523.4 s 的 C9。連帶更正第四階段第 5 點「訊號只落在 RT 258–949s、其外為平坦背景」的實測記錄（RT>1000s 實有 3 個峰）。**啟發式失效的診斷**：錯誤組合的間距 std 0.0034 反而優於正確組合的 0.0046，且突出度總和更大——兩個準則都指向錯誤答案，故 draft.18 用間距均勻度定案碳數的方法論不足以支撐該結論。**已修正**：新增 `calibration.match_anchors_by_dt()` 取代間距啟發式，`build_from_std_peaks()` 在系列帶 `dt_values` 時優先走 Dt 配對（無則退回啟發式），`anchor_selection.mode` 新增 `dt_matched`。實測 6/6 命中、平均 |Δ|=0.00273。既有 `_peaks.json` 的 `ri` 需重跑。完整分析見 `ketone_RI_provenance.md`。**(4) 另補一項外部佐證**：反編譯 `gc-ims-tools` 0.1.10（Food Chemistry 2022）的 `Spectrum.calc_reduced_mobility()`，其公式為 `K0 = L²·T₀·p /(dt·Ud·T·p₀)`（預設 L=5.3 cm，與本專案表頭讀值一致），展開與本專案 `dt_convert.k0_from_raw_params()` 的 `ah·L²/(t·U)` 同形——**該函式回傳 K0 本身，而我們回傳其倒數**，佐證第二階段那個左右不對稱是實作瑕疵（見 status.md open decision 5）。其 `riprel()` 取第 0 列 argmax，亦與本專案 `rip.find_rip()` 一致。
- **draft.23**：**化合物身分更正——標準品是「酮」，不是「甲基酮」。** 使用者指出 draft.19 記錄的「C4–C9 甲基酮同系物（2-butanone→2-nonanone）」超出了他實際確認的範圍：他確認的只有「酮 + C4–C9」，2-alkanone 這個結構指派是本專案自己從 DT_rel 等差階梯推出來的猜測，予以撤回。連帶修改：`reference_series.py` 系列名 `methyl_ketone` → `ketone`、成員標籤改為不宣稱結構的 `C4 ketone`…`C9 ketone`、借用 RI 的來源化合物移入獨立欄位 `ri_source_compounds`（數值溯源與身分宣告分離，由 `test_ketone_labels_do_not_claim_a_structural_subtype()` 鎖住）；`methyl_ketone_RI_provenance.md` 更名為 `ketone_RI_provenance.md` 並補上**第二層假設**的說明——借來的六個 RI 是在 2-alkanone 上量的，套到只確認到「酮」的本批 STD 上，等於跨批次借用之外再疊一層跨結構亞型借用，而這個誤差**不會**在 DT_rel 階梯均勻度上顯現（階梯只證明每多一個 CH₂，不辨官能基位置），現有自動錨點機制偵測不到。不受影響：碳數範圍、同系物性質、`select_homolog_ladder()` 的挑錨點邏輯、六個實測 RT/DT_rel。
- **draft.22（對應程式 tag v3）**：第四階段由「最大缺口」改標為**已實作**，章節開頭新增「已實作」摘要（設計 1–14 點與逆向調查全數保留）。實際落地：`calibration.py` + `reference_series.py`——`select_homolog_ladder()` 自動挑 6 個酮的錨點（DT_rel 階梯，免模板；另有 `pin_anchors()` 模板釘定）；`ketone` 借用 6 個 RI 值（`assumed_unverified`，見 `ketone_RI_provenance.md`）；`make_rt_to_ri()` 為唯一共用 RT→RI 函式（外插+標記）；`resolve_ri_calibration()` 三態 + `_folder_calibration.json` 快取；串進 `identify.py` 與桌面 UI（RI 欄、`warp_rows_to_ri()` 線性 RI 熱圖軸，x 軸 drift 正規化不動）。測試 `test/test_calibration.py`（全套 125 項通過）。同步更新 `status.md`/`README.md`/`GC-IMS_Pipeline_Implementation.md`/`UI.md`。
- **draft.21**：第四階段外插行為**定案**——邊界外「外插並標記」（`scipy.interp1d(fill_value='extrapolate')` + `ri_extrapolated` 旗標），不 clamp；理由是 clamp 會讓範圍外的峰全部壓到邊界值、失去 RI 區分度（悄悄丟資訊），外插保留區分度並由旗標讓下游分級。要求 `attach_ri()` 與熱圖軸共用同一個 `make_rt_to_ri()`/`interp_fn`，避免「峰表外插、軸 clamp」不一致。`RT_to_RI_normalization_math.md` 的參考實作與檢查清單同步改用 scipy 外插版、並標明手算範例（RT=450→644.1）為「內插演算法測試、非校準驗證」。
- **draft.20**：第四階段新增第 14 點——RT→RI 套用步驟的數學細節獨立成 `RT_to_RI_normalization_math.md`（分段線性內插五步驟、手算範例可當單元測試、外插行為待決策），此處僅存指標與摘要，不重複完整推導。**同時修正 draft.19 編輯時誤刪的「## 第五階段：容許窗比對」標題**（上一版 str_replace 操作疏失，標題與內容被誤合併，本版已修復，第五階段以下章節內容本身未受影響）。

- **draft.19**：第四階段新增第 13 點——使用者確認 STD 標準品為 C4–C9 的酮（非烷烴），依 RT 對應六個錨點。**[draft.23 更正]** 本版原記為「甲基酮同系物，2-butanone→2-nonanone」，該結構指派是本專案的推論而非使用者提供，已於 draft.23 撤回。明確標注精確 RI 值仍缺（甲基酮 RI 非定義式整百，需查證非極性管柱文獻值或證書），`series_key="ketone"` 可切換但 `ri_values` 查找表尚空，`single_point_relative` 仍是唯一可運作模式。

- **draft.18**：第四階段第 5、9 點更新——用 DT_rel 間距均勻度定量比較（`np.diff().std()`），將 141215 STD 的錨點從 5 點升級為 6 點：新納入 334.3s，原本待釐清的 347.9s 改標記為「疑似拖尾偽影」予以排除（理由：DT_rel 與 282.0s 完全重疊、不符合序列遞增預期，且納入 334.3 後 DT_rel 間距標準差降至 0.0034，比排除方案小 23 倍）。同時誠實記錄一個未解的張力：此六點組合的 log10(RT) 間距均勻度反而略遜於原 5 點組合，推測與方法多段升溫程式有關，但未驗證，維持推測層級。`single_point_relative` 提案的錨點數同步更新為 6。
- **draft.17**：第四階段新增第 12 點——批次資料夾內沒有 STD 檔案時的三層解析邏輯（`resolve_ri_calibration()`）。判斷依據改為表頭 `Sample` 欄位而非檔名慣例；新增跨批次共用的 `ri_calibration_registry.json`（比照 Stage 2 `calibration_profile.json` 綁定「儀器＋管柱＋方法」組合的精神），借用舊校正曲線時強制帶 `days_gap` 信心標記；完全無可用校正時降級走 `.iml` 直接比對或標記 `ri_mode="unavailable"`，與 Stage 2 `k0_mode` 三態設計對稱，RI/K0 兩維度 provenance 各自獨立標記。
- **draft.16**：第四階段大幅補充，共六項本輪確認。**(1)** CFR 反編譯 `VOCal_412_obf.jar` 成功，追蹤 `ColNormX`/`ColNormY` 寫入路徑至 `Dlg_EditColumnNorm` 對話框（`bj.java`/`B.java`/`aV.java`），確認這是純數值 X-Y 點編輯器，**VOCal 的資料結構與 UI 都不記錄校正標準品的化合物身分**，此資訊只能來自操作者外部紀錄。**(2)** 發現並確認 `Use Fit` 核取方塊會把原始輸入值改寫成擬合後的近似值（四捨五入至小數點後三位），**推翻本輪對話中途「非整百 RI 值代表標準品非烷烴系列」的錯誤推論**——即使標準品確實是整百 RI 的烷烴，勾選 Use Fit 後存檔值仍會帶小數，此特徵不可再用於系列判斷。**(3)** 用真實 `260625_141215_STD.mea` 二進位資料實測（非目測讀圖），得到精確的 RIP index（680）與 7 個候選峰座標，確認訊號範圍僅落在 RT 258–949s，此範圍外皆為平坦背景。**(4)** 用另一支同日 `260625_012251_STD.mea` 實測驗證第九階段 `Status` 前置過濾邏輯的實際必要性——該支同樣標記 `doubtful`，峰數與強度明顯遠低於 141215，應予排除，不做 bracket 平均。**(5)** 新增 `series_key` 可插拔架構設計（`reference_series.py`），與 `single_point_relative` 降級模式提案，作為化合物身分未確認前的可執行路徑，避免把未驗證假設當作既定事實輸出。**(6)** 修正第 10 點公式：原記錄的 Van den Dool–Kratz（線性 Rt）與第 2 點已驗證的 `log10(Rt)` 資料格式不一致，改為對應的 Kovats log 形式。
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
- **draft.15**：第八階段（UI 互動）落實完成，並修正一項規則語意。**(1) 規則由「移除」改為「標記」**：新增 `rules.mark_rules()`，被可選規則否決的峰不再從清單消失，而是設 `rule_active=False` 留在畫面上（灰圈 + 灰列），與使用者手動取消選取的外觀完全一致——使用者因此看得見規則做了什麼，也才能覆寫。`apply_rules()` 保留原契約（標記後過濾）供 `identify.py` 使用。**(2) 手動選擇覆寫規則判定**：`effective_active = user_active if 使用者碰過 else rule_active`，呼應校準哲學「人工是偵測的標準」；被救回的峰以虛線環標示。**(3) 勾選狀態持久化以座標為 key**：存進 `<name>_peaks_state.json`，key 為 `(rt_index, dt_index)` 而非 `peak_id`——後者是基準集合內的突出度名次，`R004.half_width`／`R006.boundary` 一改就整組重排，綁編號會讓存下來的選擇靜默套到別的峰身上。**(4) Canvas 原生圈落地**：新增無圈背景圖 `<name>_bg.png` 與幾何側車檔 `<name>_bg.json`（由畫圖者負責寫，確保幾何與圖一致）；修復 `highlight_peak_on_overlay()` 忽略 matplotlib 邊界的既有座標 bug。**(5) Tk 平台限制記錄**：`-outlinestipple` 在 Windows 上被靜默忽略（文字的 `-stipple` 有效），故圈以顏色而非網點表示未選取狀態——這比 draft.12 記的「Canvas 無原生透明度」更具體。**(6) `.npz` 重用**：選檔時若已有 `.npz` 即詢問是否重用；新增 `peaks.py --bg-only`，可單靠 `.npz` 重建顯示用背景圖，不需回頭讀 `.mea`。**(7) 全解析度 CSV 改為可選**（`readGAS.py --write-csv`）：實測每檔 0.8–1.5 GB 且管線中無人讀取，`.npz` 已含相同資料且無損。
- **draft.14**：第七/八階段大幅落實為程式碼，並新增兩個設計概念。**(1) 新增 `R006`（排除 RIP 之前）**：`drift_relative ≤ 1.0` 的峰跑得比反應離子快，物理上不是分析物，一律剔除。**(2) 新增「強制規則」概念**：`R004`／`R006` 不可停用，強制落在 `load_config()`/`save_config()` 層而非 UI 層，因為峰編號基準線建立在這兩條之後的集合上。**(3) 新增「門檻前」套用時機**：兩條強制規則移到 `max_prominence` 計算之前——實測 `A_1_3` 顯示 RIP 把相對門檻墊高 3.3 倍（101.0 vs 30.9），擺在偵測後無法救回被誤殺的弱峰；此意圖由 `test/test_select_from_maxima.py` 鎖住。**(4) 編號規則定案**：`peak_id` 在強制規則套用後指派一次，其餘規則只剔除、不重編號（跳號視為資訊，代表該峰被某條規則篩掉）；實測三種可選規則組合下，存活峰的編號改動數為 0。**(5) 第八階段 Canvas 原生物件落地**：新增 `<name>_maxima.npz`（快取 25 萬筆原始局部極大，2.4 MB）使規則面板能在記憶體重跑整條漏斗（含門檻重算）約 4 ms，五條規則全部即時；新增無圈背景圖 `<name>_bg.png` 與 `canvas_geometry`（資料區在 PNG 內的 bbox 與軸範圍），圈與編號改為 `create_oval`/`create_text`。**(6) 修復既有座標 bug**：`main.py` 的 `highlight_peak_on_overlay()` 原本假設資料區佔滿整張 PNG，忽略 matplotlib 邊界（實測左緣即佔 8.5%），標記位置一直偏移。

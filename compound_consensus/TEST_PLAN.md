# 第三支應用（`main3.py`）測試計畫

**Version: 1.2 — by Albert Sheng**
**最後更新**：2026-09-07

涵蓋 `main3.py` 進入點、`compound_consensus/` 套件,以及「加了第三支不會弄壞前兩支」。
第一支的測試在 `test/`,第二支在 `test2/`,三份互不覆蓋。

```bash
.venv/Scripts/python.exe -m pytest -q            # 全套 486 項
.venv/Scripts/python.exe -m pytest test3/ -q     # 只跑第三支（248 項）
.venv/Scripts/python.exe -m pytest test3/test_main3.py -q   # 只跑進入點與並存
```

---

## 0. 這份計畫的原則

**每一項測試都要對應一個「會發生、而且不會自己叫」的失敗。** 本專案踩過的坑幾乎
都是**無聲**的：圈一個都不畫但沒有例外、RT 軸短 16.7% 但沒有警告、GC 欄放的是秒
卻標著 RI。湊覆蓋率的測試擋不住這一類,所以下面每一節都寫明**它守的是哪個症狀**。

---

## 1. 進入點與三支並存 — `test3/test_main3.py`（20 項）

| 測試 | 守什麼症狀 |
|---|---|
| `test_main3_exists_at_the_project_root` | 進入點不在根目錄,使用者找不到 |
| `test_main3_imports_without_opening_a_window` | import 就開視窗 → 任何 import 都卡在 `mainloop()` |
| `test_main3_makes_the_root_modules_importable` | `sys.path[0]` 不對 → `No module named 'peaks'` |
| `test_running_the_package_file_directly_is_the_broken_path` | **說明性測試**：把「為什麼需要 main3.py」釘成可執行的事實 |
| `test_main3_works_from_another_working_directory` | 捷徑/排程/exe 從別的 cwd 啟動 |
| `test_existing_modules_still_import`（×10） | 加第三支弄壞既有模組 |
| `test_third_app_does_not_modify_the_first_two` | 第三支就地換掉既有模組的函式（隔離規則 1） |
| `test_the_three_apps_have_separate_entry_points` | `main3` 變成 `main` 的變體 |
| `test_state_files_of_the_three_apps_do_not_collide` | 寫 `_peaks_state.json` → 無聲改掉第一支的選取（隔離規則 2） |

> **為什麼用子行程**：啟動方式的問題全出在 `sys.path[0]`,而那在 pytest 行程裡
> 早已設好,同一個行程內永遠複現不出來。

---

## 2. 介面 — `test3/test_app.py`（112 項）

### 2.1 熱圖與圈
畫不出圈（RI 沒掛上去,**零例外**）、圈的座標沒扣掉 matplotlib 邊界、
黃色選取環、滾輪縮放時游標下的點要固定、拖曳不可以誤觸切換、雙擊空白回全圖。

### 2.2 版面
`_build()` 曾被插進 `quit_app` 而切成兩半——**語法正確、import 成功**,但視窗只剩
一條工具列。`test_build_creates_all_three_panels` 實際建構才抓得到。
`test_window_opens_maximised` 用 `state()` 判斷而**不是**比對 geometry 高度：最大化
之後回報的是工作區高度,比螢幕矮一個工作列(實測 889 vs 960)。

### 2.3 逐峰化合物比對欄（本輪新增）
| 測試 | 守什麼症狀 |
|---|---|
| `test_peak_table_has_the_compound_columns` | 欄位序號變動 → `on_peaks_click` 的 `#2`/`#9` 分派打到別欄 |
| `test_uncompared_peak_shows_pending_not_a_dash` | 「還沒比對」顯示成「—」→ 使用者以為沒有候選就把峰關掉 |
| `test_gc_column_marks_seconds_when_it_falls_back_to_retention_time` | **無聲降級**：沒有 RI 時格子是秒,標題還寫 RI |
| `test_gc_column_shows_ri_when_calibrated` | 有校準時反被標成 RT |
| `test_gc_ims_column_reports_how_many_other_candidates_share_the_hit` | 只顯示第一名 → 以為鑑定是唯一的 |
| `test_trigger_is_blank_for_a_deselected_peak` | 取消勾選的峰仍可開 ▶ → 以為它還算數 |
| `test_match_results_are_cached_by_coordinate_not_peak_id` | 用 `peak_id` 當鍵 → 規則一改重新編號,快取黏到別顆峰 |
| `test_matches_for_another_file_do_not_hijack_the_view` | 背景結果回來時使用者已切檔,畫面被舊檔蓋掉 |
| `test_library_failure_is_remembered_and_not_retried` | 每換一個檔再失敗一次,狀態列被洗掉 |

### 2.4 共識表的維度（本輪新增）
`ims_only` 要標 IMS(不可以跟「只有 RI」混為一談)、`mixed` 要標混合(不可以挑一個
當代表)、**每一個候選**在明細裡都要有自己的維度欄——同一個區域裡有些候選兩軸都
對上、有些只有 RI,證據強度差很多。

### 2.4b 群聚容差（1.2 新增）

預設 **20 秒 / 0.03**、兩個都是 `readonly` 的下拉選單（輸入框打錯字可能靜靜存成 0,
而 0 會讓每顆峰自成一區、票數全變 1/N）、換了值要標 dirty、
**使用者選的值真的離開主執行緒進到背景工作**(檢查 `Thread(args=...)`,
不是 worker 內部——worker 要真的跑起來得有一整個資料夾)、
IMS 的可選值全部小於 `MD_MIN_DRIFT_GAP`(0.10,否則單體/二聚體會被併成一區)、
體檢面板同時印出兩個容差、相似度是用舊容差算的時候面板要講出來。

`test_logic.py` 另有一項守著 `consensus_regions()` **明著**把兩個容差傳給
`areas2.build_consensus_areas()`,並釘住 `areas2.DEFAULT_RT_TOL_S == 10.0`
(第二支的預設不能被順手改掉)。

### 2.5 規則面板、選取狀態
參數可編輯且即時重新標記、型別跟著原值、非法值被拒絕而不是靜靜歸零、必要規則
不可關閉、規則否決的峰在熱圖上要變灰(`rule_active` 與 `active` 曾沒接起來,
表格變了熱圖沒變,而且**共識照樣把那些峰算進去**)、使用者的選擇覆蓋規則並標色、
存檔只留使用者**明確表示過**的(三態 `user_active`)。

---

## 3. 共識邏輯 — `test3/test_consensus.py`（53 項）

樣品挑選(STD/空白**以詞為單位**,`"blk" in name` 抓不到 `Fish meat blank`)、
共識區域、票數佔比分級、支持度的分母是「有偵測到峰的檔」、
相似度(逐檔置中去掉進樣量;**n<3 直接拒絕**——兩個檔的逐區域標準化會把每欄壓成
`(+1,−1)`,相關係數與資料無關,永遠 −1.00)、monomer/dimer 配對、
維度標籤(`best_dimension` 的強弱序、`ims_only` 要回報而不是「（無候選）」)。

---

## 3.5 逐檔規則 — `test3/test_rules_store.py`（19）＋ `test3/test_rules_per_file.py`（20）

兩層：`rules_config.json` 預設，`results/<base>_rules3.json` 逐檔覆寫。

| 測試 | 守什麼症狀 |
|---|---|
| `test_editing_applies_to_every_file_by_default` | 改規則只影響目前這個檔，於是調一次參數要逐檔重調 18 次 |
| `test_rules_reset_when_the_program_restarts` | 規則殘留到下一次執行，使用者納悶峰數為什麼跟預設不同 |
| `test_opening_the_panel_does_not_pin_the_file` | 打開面板看一眼＝把那個檔釘成自訂 |
| `..._with_no_file_loaded_edits_the_default` | 沒載入檔案時卻寫出莫名其妙的覆寫 |
| `..._do_not_share_one_config_object` | 共用 dict → 改 A 檔把 B 檔一起改掉 |
| `test_detection_changing_rules_give_different_fingerprints` | 指紋不分開 → 快取把 A 檔的峰當成 B 檔的 |
| `test_marking_only_rules_do_not_invalidate_the_cache` | 反向：調一下 R002 害 18 檔各重跑 55 秒（約 16 分鐘）換來一模一樣的峰 |
| `test_rules_never_touch_the_disk` | 規則寫成檔案 → 殘留、半清乾淨的狀態 |
| `test_apply_leaves_unselected_files_alone` | 「套用到選取的檔」動到沒勾的檔 |
| `test_reset_puts_the_file_back_on_the_default` | 自訂過就再也回不去預設 |
| `test_files_with_custom_rules_are_marked_in_the_list` | 兩個檔同樣參數卻不同峰數，看起來像程式壞了 |
| `test_consolidation_resolves_rules_per_file` | 彙整沒傳 `rules_for` → 自訂規則被無聲忽略 |
| `..._does_not_override_files_that_customised` | 存成預設卻以為全部都變了 |

---

## 4. 快取與預處理 — `test3/test_logic.py`（12）＋ `test3/test_preprocess.py`（12）

參數指紋只涵蓋**會改變偵測**的參數且與順序無關、`--dry-run` **絕不寫檔**
(採信既有快取會補寫指紋)、估時看 `_peaks2.json` 而不是第一支的 `_maxima.npz`
(預告 8 分鐘實際跑 16 分鐘)、選到只有子資料夾的上層要往下找、
**壞掉的 `.mea` 要回報成失敗而不是靜靜算成「完成」**。

---

## 5. 自動測試**涵蓋不到**的部分 — 必須實跑

自動測試不能證明「看起來對」。每次改介面後,依序做這一輪：

```bash
python main3.py
```

| # | 步驟 | 通過條件 |
|---|---|---|
| 1 | 開啟 | 視窗**攤滿整個螢幕**;左中右三個面板都在;工具列**沒有編號按鈕**,只有設定:「選資料夾/門檻/GC 容差/IMS 容差/Rules/結束」 |
| 1b | 還沒選基準 | 模式 2、3 是**灰的**,點不下去 |
| 1c | 看檔名前面的圓點 | **綠/黃/紅三色分得出來**(不是三顆一樣的灰點);狀態欄同時有文字 |
| 2 | 選資料夾 `GAS/藝妓咖啡` | 列出 13 個樣品;**壞檔 `FREE_GG_..._GG_2.mea` 報成失敗**,不是靜靜跳過 |
| 3 | 模式 1 點一個檔 | 熱圖出現紅圈與編號;圈與峰對得上 |
| 4 | 放大到 400% 再看 | **圈仍然貼在峰上**(縮放時對位曾經跑掉) |
| 5 | 點 `On` 欄 | 該圈變灰,表格與圈同步 |
| 6 | 看 GC×IMS / GC / IMS 三欄 | 先是「…」,幾秒後填上值或「—」 |
| 7 | 按某一列的 ▶ | 候選視窗開啟,**維度欄分得出 GC+IMS / GC / IMS**;綠底是兩軸都對上 |
| 8 | 關掉一顆峰再看它的 ▶ | ▶ 變空白,點了沒有反應 |
| 8b | 點某一列的「基準」欄 ◉ | 只有那一列是 ◉;**進度視窗出現**,跑完自己消失;整欄相似度填上數字;模式 2/3 變成可點 |
| 8c | 點另一個檔的 ◉ | 基準換過去,**整組重挑**(不是累加);欄位標題跟著改成 `相似度 vs <新基準>` |
| 9 | 模式 2「選擇相似 mea」 | ≥0.80 的已自動勾選;用「組 ✓」增刪都能動 |
| 10 | 切到模式 3(門檻 1/2) | **不必按任何按鈕就開始彙整**;有進度視窗;依票數排序 |
| 10b | 模式 3 → 模式 1 → 模式 3 | **不重算**(什麼都沒變);改門檻或組員之後再切才重算 |
| 11 | 看 `維度` 欄 | 出現 `GC+IMS` / `GC` / `IMS` / `混合`,**不是** `2D` / `RI` |
| 11b | 看 `Drift rel` 與 `RI` 兩欄 | 有值;對得回熱圖上的位置(x = drift、y = RI) |
| 11c | 看那一欄的標題 | 是**可能數**,不是「候選」;下方說明寫明「越大越不確定」 |
| 12 | 雙擊任一列 | 明細每一列都有自己的維度欄;欄名是**資料庫 RI**(不是「庫 RI」),說明講明那是參考值、不是量到的 |
| 13 | 改門檻重按 | 達門檻的數量跟著變;未達門檻的**保留顯示(灰)不刪除** |
| 13b | 把 **GC 容差** 從 20 改成 45,切到模式 3 | **自動重算**(不必按任何按鈕);列數通常變少、票數變高——原本被拆開的併回來了 |
| 13c | 改回 20,看模式 2 的體檢面板 | 有一行「群聚容差　GC 20 秒　IMS 0.03」;若相似度是用別的容差算的,**下面會多一行講明**,而不是無聲混用 |
| 13d | 把 **IMS 容差** 改成 0.08 | 一樣自動重算;可選值裡**沒有 0.10 以上**(那會把單體/二聚體併成一區) |
| 14 | 模式 2/3 點熱圖上的圈 | **不會**改變選取,左上角有「唯讀」標記,狀態列說要切到模式 1 |
| 15 | 模式 2/3 滾輪縮放 | 照舊可以——唯讀只擋改狀態,不擋瀏覽 |
| 16 | 模式 2 右側面板 | 顯示最低/平均相似度、**離群的是哪一個檔**、門檻換算成幾個檔;不再重列成員 |
| 17 | 規則面板：範圍留在「全部檔案」，把 `R002 top_n` 改成 10 | **每一個檔**的峰表都跟著只剩 10 顆;沒有任何檔出現 `[自訂規則]` |
| 18 | 把範圍切成「只有目前這個檔」再改一次 | 只有這個檔變;它出現 `[自訂規則]` |
| 19 | 按「套用到選取的檔（N）」 | 整組都出現 `[自訂規則]`;被套的檔狀態燈轉黃（指紋失效要重跑） |
| 20 | 按「本檔改回預設」 | 標記消失，參數回到預設 |
| 20b | **關掉程式再開** | 「全部檔案」的改動歸零(回 `rules_config.json`);**自訂過的檔仍帶 `[自訂規則]` 並保留自己那份**;同標本分組是空的 |
| 20c | 打開規則面板但什麼都不改，關掉 | **不可以**有檔案冒出 `[自訂規則]` |
| 21 | 跑 `python main.py`、`python main2.py` | 兩支都照常開得起來 |

**最後一步不可省略**——使用者的要求原話:「任何你改或加的程式碼,請確保既有的
程式仍可運作」。

---

## 6. 已知不測、且刻意不測的

- **鑑定是否正確**。庫的涵蓋率是硬上限(漂移庫 84 個 vs RI 庫 9,958 個),
  測試守的是**流程誠實**——維度標對、provenance 帶著走、降級不無聲,
  不是「答案對不對」。理由見 `README.md`。
- **K0**。解得出來但掛上去候選從 401 暴增到 1507、2D 命中的區域從 46 掉到 36,
  刻意不用,所以也不測。
- **組間統計**。前提問題未解(三個重複是分析重複,沒有標本間變異)。

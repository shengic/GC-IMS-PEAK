# test2/ — 第二支應用的測試

`areas2.py`（邏輯）與 `main2.py`（介面）的測試。第一支應用的測試在 `test/`。

```bash
pytest -q            # 全套 238 項（test/ 194 + test2/ 44）
pytest test2/ -q     # 只跑這裡（44 項）
pytest test2/test_main2.py -q
```

> **不要只跑 `pytest test/`**。專案文件長年寫的是那個指令，分家之後照著跑會靜靜漏掉
> 這裡的 44 項——而且不會有任何徵兆。根目錄的 `pytest.ini` 設了
> `testpaths = test test2`，所以**光打 `pytest` 就兩邊都收**，這是防呆用的。

## 內容

| 檔案 | 測什麼 | 項數 |
|---|---|---|
| `test_areas2.py` | 邏輯：共識群聚、方框量測、`.gasprj` 匯入、隔離規則、輸出格式、`Class` 檢查 | 31 |
| `test_main2.py` | 介面：資料夾選取、背景執行緒錯誤回報、矩陣表格、檔案上色、stdout 攔截 | 13 |

`test_main2.py` 需要 Tk display，無視窗環境會整支 skip（同 `test/test_match_panel.py`）。

## 這些測試在守什麼

**不是覆蓋率，是**「這一條再壞掉一次就會很難查」。每一項都對應一個實際發生過的問題：

- **`.npz` 不可被扣過基線的資料污染** —— 否則 `main.py` 會把它當原始值載入而毫無跡象。
- **背景執行緒不可無聲死掉** —— `SystemExit` 是 `BaseException`，`except Exception`
  接不到，畫面會永遠空白且沒有錯誤訊息。
- **欄位標題不可是 `?`** —— `Class` 漏讀 + ttk.Treeview 的 heading 只畫一行，兩件事疊
  在一起造成的。
- **`done` 不可把沒處理的檔案也塗綠** —— 用 `files: N` 限制時會讓人以為整批跑完了。
- **量不到要回 `None` 不是 `0`** ——「沒量到」與「量到零」在比較分組時意義完全不同。
- **`Class` 與檔名不一致要回報但不自動修正** —— 哪一邊對是原始資料的問題。

不用 `test/conftest.py` 的 fixture（只用 pytest 內建的 `tmp_path`），所以兩個測試根
目錄之間沒有相依。

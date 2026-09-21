# docs/manual_src — 手冊的建置原始碼

`docs/` 底下的 `.docx` / `.pdf` 是**產物**，不要手改——改了下次重建就沒了。
要改內容一律改這裡的 `.py`，再重新建置。

## 三份產物、三支腳本

| 產物（寫進 `docs/`） | 建置腳本 | 內容模組 |
|---|---|---|
| `GC-IMS 簡介與使用手冊 main.py.docx`（45 頁） | `make_manual.py` | `manual_content*.py` |
| `GC-IMS 簡介與使用手冊 main3.py.docx`（42 頁） | `make_manual3.py` | `m3_content*.py` |
| `GC-IMS_請協助確認.pdf`（3 頁） | `make_questions_pdf.py` | 自帶 |

兩份手冊共用 `docx_kit.py`（排版 + OMML 公式），**內容完全不共用**：
`main.py` 那份不提第三支應用的功能，`main3` 那份不提前兩支，兩份都不提 `.gasprj`
——使用者明確要求過。

```bash
cd docs/manual_src
../../.venv/Scripts/python.exe make_manual.py       # 輸出直接落在 docs/
../../.venv/Scripts/python.exe make_manual3.py
```

腳本**直接寫進 `docs/`**，不是寫在旁邊再手動搬。手動搬那一步遲早會漏掉，
而 `docs/` 裡一份舊的看起來和新的一模一樣。

## 交付前一定要跑的兩個檢查

### 1. 頁數與空白頁（用 Word 實際排版，不是估的）

```powershell
.\check.ps1 -Path "..\GC-IMS 簡介與使用手冊 main3.py.docx"
# TOTAL=42 P1=3 P2=23 BLANK=
```

`P1` / `P2` 是兩部各自的起始頁；`BLANK` 要是空的。目前的要求是**兩部各至少 20 頁**。

空白頁的成因固定是同一個：帶分頁符的空段落會落在下一頁的頂端。
所以 `docx_kit` 用 `_Breaker` + `page_break_before`，而且整份文件**共用一個
`_Breaker`**——每段 `render()` 各自插一個分頁符的話，接縫處就會冒出空白頁。

### 2. Iansui 缺字

Iansui 缺 `≥` `⌈` `⌉` `✓` `₊` `ₙ` `☐` `☑` 等字，缺字時 Word 會**無聲**換成別的字型。
新增內容後掃一次：

```python
from fontTools.ttLib import TTFont
f = TTFont(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts\Iansui-Regular.ttf")
cps = set().union(*[t.cmap.keys() for t in f["cmap"].tables])
# 把內容裡的非 CJK 字元逐一對照 cps
```

已知的代換：`≥`→`≧`、`⌈ ⌉`→改寫成「無條件進位」、`✓`→拿掉。

## 踩過的坑

- **`w:eastAsia` 一定要設。** `run.font.name` 只寫 ascii/hAnsi，Word 遇到中文會走
  「東亞字型」那一欄——沒設的話整份中文退回新細明體，而 Iansui 只套在英數字上。
- **用 Write/Edit 工具改這些檔，不要用 shell heredoc。** 這個專案踩過很多次：
  `\n` 被轉義，檔案靜靜壞掉。
- **`check.ps1` 不會關掉您自己開著的 Word。** COM 會接到已經在跑的實例，
  `Quit()` 收的是整個應用程式（踩過一次，使用者正在讀的文件被關掉）。
  腳本現在會先記錄自己來之前有沒有 Word 在跑。
- **建置失敗跳 `PermissionError`** ＝那份 `.docx` 正被 Word 開著。關掉再跑，
  不要改檔名繞過——那只會在 `docs/` 裡留下兩份。

Version: 1.0 — by Albert Sheng（手冊建置，2026-09-21）

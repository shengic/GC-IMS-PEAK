# RT → RI 正規化實作說明 —— 供 Claude Code CLI 參考

**Version: 3.3 — by Albert Sheng**

**背景**：六個校正錨點已經確認（見 `GC-IMS_Identify_Workflow.md` draft.21 第四階段第 5、13 點），本文件只講「拿到六個錨點之後，怎麼把任何一個峰的 RT 換算成 RI」這一步的數學與實作，其他背景（化合物身分、STD 檔案解析等）不重複，請對照 workflow md 一起看。

**[draft.21 更新] 邊界外查詢已定案為「外插並標記」，不是 clamp**——見第 15 點決策，本文件下方「參考實作」與檢查清單已同步更新為 `scipy.interp1d(..., fill_value='extrapolate')`，不要再用先前版本示範的 `np.interp`（那是 clamp 行為，跟已定案的決策不一致）。

---

## 數學步驟

輸入：6 個錨點 $(RT_1,RI_1),\dots,(RT_6,RI_6)$，依 $RT$ 遞增排序。

**Step 1 —— 建表時，把 RT 轉成 log 空間**：
$$x_i = \log_{10}(RT_i)$$
錨點表存的是 $(x_i, RI_i)$，不是 $(RT_i, RI_i)$。

**Step 2 —— 查詢新峰時，該峰的 RT 也要先取 log10**（最容易漏掉的一步）：
$$x_{query} = \log_{10}(RT_{query})$$

**Step 3 —— 找出 $x_{query}$ 落在哪一段**：
$$x_i \le x_{query} \le x_{i+1}$$

**Step 4 —— 算內插權重**：
$$\alpha = \frac{x_{query}-x_i}{x_{i+1}-x_i}$$

**Step 5 —— 算 RI**：
$$RI = RI_i + \alpha \cdot (RI_{i+1}-RI_i)$$

### 為什麼要先取 log10 才能線性內插

同系物（每多一個 CH₂）的滯留時間在等溫層析條件下，跟碳數 $n$ 呈指數關係 $t'_R \propto q^n$（$q$ 為常數），不是線性關係；取 log 之後 $\log_{10}(t'_R)$ 才與 $n$ 呈線性關係。RI 定義上與碳數線性相關，所以也要在 $\log_{10}(RT)$ 空間內插才對得上物理機制，不能在原始 RT 秒數空間直接做線性內插。

---

## 手算範例（供對照，非真實 RI 值）

錨點 RT 為本專案當時的實測數字（**修正前的舊保留時間軸**，見 `readGAS.RT_AXIS_VERSION`；新軸為 7/6 倍），RI 用 400/500/600/700/800/900 純示範算術。**此例的用途是驗證內插演算法本身，與軸的絕對尺度無關**——內插在 log10 空間進行，錨點與查詢值同時平移不改變結果，所以這個手算範例在新軸下依然成立：

| $i$ | $RT_i$ (s) | $x_i=\log_{10}(RT_i)$ | $RI_i$（示範值） |
|---|---|---|---|
| 1 | 282.0 | 2.4502 | 400 |
| 2 | 334.3 | 2.5241 | 500 |
| 3 | 400.3 | 2.6024 | 600 |
| 4 | 521.8 | 2.7175 | 700 |
| 5 | 697.0 | 2.8432 | 800 |
| 6 | 949.0 | 2.9773 | 900 |

查詢 $RT_{query}=450$ 秒：

1. $x_{query}=\log_{10}(450)=2.6532$
2. 落在第 3、4 錨點之間：$x_3=2.6024 \le 2.6532 \le x_4=2.7175$
3. $\alpha = \dfrac{2.6532-2.6024}{2.7175-2.6024} = 0.4414$
4. $RI = 600 + 0.4414\times(700-600) = 644.1$

**這組 $(450 \to 644.1)$ 可以當「內插演算法」的單元測試 ground truth，但不代表校準已驗證**——這裡的 RI 值（400/500/600/700/800/900）是純示範算術，不是本專案真實的酮校準值（真實值仍待補，見 workflow md 第 13 點）。這個測試只驗證 `log10` 轉換、區間搜尋、加權內插三步驟有沒有寫對，測試命名跟註解要明確標示這一點，不要讓人誤以為校準結果已經驗證過（見 workflow md draft.21 第 16 點）。

---

## 參考實作

```python
import numpy as np
from scipy.interpolate import interp1d

def build_ri_lookup(anchors_rt, anchors_ri):
    """anchors_rt, anchors_ri: 長度相同的 list，順序不要求已排序，函式內部會排序"""
    pairs = sorted(zip(anchors_rt, anchors_ri), key=lambda p: p[0])
    rt_sorted = np.array([p[0] for p in pairs])
    ri_sorted = np.array([p[1] for p in pairs])
    log_rt = np.log10(rt_sorted)
    interp_fn = interp1d(log_rt, ri_sorted, kind='linear',
                          bounds_error=False, fill_value='extrapolate')
    return log_rt, ri_sorted, interp_fn

def rt_to_ri(rt_query, log_rt_anchors, interp_fn):
    """
    回傳 (ri_value, extrapolated: bool)
    extrapolated=True 代表 rt_query 落在錨點涵蓋範圍外，結果為外插（非 clamp），信心較低，
    下游（peak table 輸出、熱圖 y 軸刻度）必須一起帶這個標記，見 workflow md draft.21 第 15 點。
    """
    x_query = np.log10(rt_query)
    extrapolated = bool(x_query < log_rt_anchors[0] or x_query > log_rt_anchors[-1])
    ri_value = float(interp_fn(x_query))
    return ri_value, extrapolated
```

**[draft.21 已定案] 邊界外一律外插並標記，不 clamp**：`scipy.interp1d(..., fill_value='extrapolate')` 沿最外側那一段的斜率線性延伸，不是把值夾在邊界。選外插而非 clamp 的理由：clamp 會讓範圍外的峰全部壓到邊界值、彼此失去 RI 區分度，等於悄悄丟棄資訊；外插保留區分度，用 `extrapolated` 標記讓下游自行判斷是否採信，呼應本專案一貫的 provenance 原則（`assumed_unverified`／`k0_mode`／`ri_mode`）。**`attach_ri()` 與 `ri_yticks`（或任何其他讀取校準結果的函式）必須共用同一個 `rt_to_ri()`／同一個 `interp_fn`，不可各自維護一份判斷邏輯**，否則會出現峰表用外插、軸刻度用 clamp 這種顯示與資料不一致的情況——這是 draft.21 明確記錄要避免的狀況。

---

## 實作檢查清單

- [ ] 建錨點表時，`x` 欄位存的是 `log10(RT)`，不是 `RT` 本身
- [ ] **查詢新峰的 RT 時，也對它取了 `log10`，不是直接拿原始秒數去查表**（最常見的漏洞）
- [ ] 錨點依 `RT`（或等價地依 `x`）遞增排序過，不是照輸入順序
- [ ] 找區間用的是 `x_query` 落在哪兩個 `x_i` 之間，不是直接拿 `RT_query` 去跟 `RT_i` 比
- [ ] 用的是 `scipy.interp1d(..., fill_value='extrapolate')`，不是 `np.interp`（後者是 clamp，跟 draft.21 已定案的決策不一致）
- [ ] 超出 `[x_1, x_6]` 範圍的查詢有標記 `extrapolated=True`（或等價欄位），不是悄悄算出一個數字混在正常結果裡
- [ ] `attach_ri()` 與 `ri_yticks`（及任何其他讀取校準結果的地方）共用同一個 `rt_to_ri()`／同一個 `interp_fn` 實例，沒有各自維護一份重複邏輯
- [ ] 用上面的手算範例（`RT=450 → RI=644.1`）跑過一次單元測試，測試命名與註解需明確標示「這是演算法測試，非校準驗證」（見 workflow md draft.21 第 16 點）

"""第三支應用的進入點 —— 跨重複測量的化合物共識。

    python main3.py

**為什麼需要這個檔**：這支應用住在 `compound_consensus/` 套件裡，正確的啟動方式
是 `python -m compound_consensus.app`。直接 `python compound_consensus/app.py`
會把**子資料夾**放進 `sys.path[0]`，根目錄的 `peaks` / `calibration` / `match`
就 import 不到，而 Python 報的是 `ModuleNotFoundError: No module named 'peaks'`
——看不出真正的原因是啟動方式。從專案根目錄跑 `python main3.py` 時
`sys.path[0]` 就是根目錄，那條路自然不會踩到。

與 `main.py`（單檔）、`main2.py`（整批量同一組區域）並存，三支互不取代。

Version: 1.1 — by Albert Sheng
"""
import os
import sys

# 從別的工作目錄呼叫（例如捷徑、或打包後的 exe）時，根目錄未必在 sys.path 上。
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from compound_consensus.app import main  # noqa: E402

if __name__ == "__main__":
    main()

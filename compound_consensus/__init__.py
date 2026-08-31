"""第三支應用：跨重複樣品彙整化合物候選。

必須以 `python -m compound_consensus.app` 啟動（不是 `python compound_consensus/app.py`）
——後者會把本資料夾放到 sys.path[0]，根目錄的 peaks / calibration / match 就 import 不到，
而且錯誤訊息（ModuleNotFoundError: peaks）看不出真正原因。實測確認過兩種寫法的差異。
"""

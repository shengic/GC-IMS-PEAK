"""
gas_utils.py  —  GC-IMS 專案共用小工具
Version: ver.01 — by Albert Sheng

目前提供：
  - pick_mea_file(): 跳出檔案總管讓使用者選 .mea，預設開在本專案 GAS/ 資料夾。

test_readGAS.py（探測器）與 readGAS.py（正式解析+繪圖）皆 import 此模組，
確保兩邊選檔行為完全一致（單一來源）。
"""

import os
import sys

# 本模組所在資料夾 = 專案根目錄（兩個腳本都放這層）
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
GAS_DIR = os.path.join(PROJECT_DIR, "GAS")


DEFAULT_FILETYPES = [("G.A.S. mea 檔", "*.mea"), ("所有檔案", "*.*")]


def pick_mea_file(title="選擇 .mea 檔", initialdir=None, filetypes=None):
    """跳出檔案總管選檔，回傳路徑字串；使用者取消則回傳空字串。

    預設開在專案的 GAS/ 資料夾（不存在則退回專案根目錄），預設過濾 *.mea。
    initialdir / filetypes 可覆寫，供其他腳本（如 peaks.py 選 .npz）共用。
    若此 Python 沒有 tkinter，會以 SystemExit 結束並提示改用命令列給路徑。
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        sys.exit("此 Python 沒有 tkinter，無法開檔案總管；請改用命令列直接給路徑。")

    if initialdir is None:
        initialdir = GAS_DIR if os.path.isdir(GAS_DIR) else PROJECT_DIR
    if filetypes is None:
        filetypes = DEFAULT_FILETYPES

    root = tk.Tk()
    root.withdraw()                     # 不顯示主視窗，只要對話框
    root.attributes("-topmost", True)   # 讓對話框跳到最前面
    path = filedialog.askopenfilename(
        title=title,
        initialdir=initialdir,
        filetypes=filetypes,
    )
    root.destroy()
    return path  # 取消會回傳空字串


def resolve_input_path(cli_path=None, title="選擇 .mea 檔",
                       initialdir=None, filetypes=None):
    """統一的輸入路徑取得邏輯：有給命令列路徑就用它，否則跳檔案總管。

    回傳一個確定存在的檔案路徑；找不到 / 未選擇則以 SystemExit 結束。
    各腳本共用，行為一致。
    """
    path = cli_path
    if not path:
        print("未指定路徑 → 開啟檔案總管選檔...")
        path = pick_mea_file(title=title, initialdir=initialdir, filetypes=filetypes)
        if not path:
            sys.exit("未選擇檔案，結束。")
        print(f"已選擇：{path}\n")
    if not os.path.isfile(path):
        sys.exit(f"找不到檔案：{path}")
    return path

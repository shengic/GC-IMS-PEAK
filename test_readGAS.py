"""
test_readGAS.py  —  G.A.S. .mea 檔案結構探測器 (probe / diagnostic)
Version: ver.01 — by Albert Sheng

目的：在寫正式的 readGAS.py（含繪圖）之前，先「看清楚」這個 .mea 到底長怎樣。
本程式 **不繪圖、不假設格式**，只做檢查並把結果印出來，讓你把輸出貼回給我，
我再據此寫正式的 parser + 繪圖。

特性：
  - 純標準函式庫即可跑；若有 numpy 會多印強度矩陣統計（沒有也能跑）。
  - 安全：只讀檔、不修改原檔。大檔也只讀前面一段做 hexdump，不會把整檔塞進記憶體做傻事。
  - 自動偵測 gzip / zlib / 純二進位三種情況。

用法：
    python test_readGAS.py                # 不給路徑 → 跳出檔案總管讓你選（預設開在 GAS/）
    python test_readGAS.py "檔案.mea"      # 也可直接給路徑
    python test_readGAS.py "檔案.mea" --bytes 512        # 調整 hexdump 長度
    python test_readGAS.py "檔案.mea" --dump-header out.txt  # 把 ASCII 表頭存檔
"""

import argparse
import gzip
import os
import string
import struct
import sys
import zlib

from gas_utils import resolve_input_path

PRINTABLE = set(bytes(string.printable, "ascii")) - set(b"\x0b\x0c")


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n/1:,.0f} {unit}" if False else f"{n:.2f} {unit}"
        n /= 1024


def hexdump(data, width=16, limit=256):
    out = []
    for off in range(0, min(len(data), limit), width):
        chunk = data[off:off + width]
        hx = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if b in PRINTABLE and b >= 0x20 else "." for b in chunk)
        out.append(f"  {off:08x}  {hx:<{width*3}}  {asc}")
    if len(data) > limit:
        out.append(f"  ... (僅顯示前 {limit} bytes)")
    return "\n".join(out)


def detect_compression(head):
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    # zlib header: 0x78 followed by 0x01/0x9c/0xda (常見)
    if head[:1] == b"\x78" and head[1:2] in (b"\x01", b"\x5e", b"\x9c", b"\xda"):
        return "zlib"
    return "raw"


def printable_ratio(data):
    if not data:
        return 0.0
    good = sum(1 for b in data if b in PRINTABLE)
    return good / len(data)


def find_header_boundary(data, min_binary_run=32):
    """找出『前段可讀 ASCII 表頭』與『後段二進位資料』的分界 offset。

    策略：從頭往後掃，找到第一段夠長的連續『非可印字元』，視為二進位起點。
    """
    run = 0
    for i, b in enumerate(data):
        if b in PRINTABLE:
            run = 0
        else:
            run += 1
            if run >= min_binary_run:
                return i - run + 1
    return len(data)  # 整個都像文字


def parse_kv(text):
    """把表頭嘗試解析成 key=value / key:value 對照，回傳 list[(k, v)]。"""
    pairs = []
    for raw in text.replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        for sep in ("=", ":", "\t"):
            if sep in line:
                k, v = line.split(sep, 1)
                pairs.append((k.strip(), v.strip()))
                break
    return pairs


def guess_dims_from_header(pairs):
    """從表頭找看起來像維度的數字（保留/漂移點數、chunk 數等）。"""
    hints = {}
    KEYWORDS = ("chunk", "point", "sample", "drift", "retention", "spectra",
                "rows", "cols", "size", "length", "count", "values", "ims")
    for k, v in pairs:
        kl = k.lower()
        if any(w in kl for w in KEYWORDS):
            num = "".join(ch for ch in v if ch.isdigit())
            if num:
                hints[k] = int(num)
    return hints


def try_reshape(binary, dims, dtype_name, struct_fmt, item_size):
    """嘗試把 binary 用 dtype 解出總點數，並看能不能對上 dims 的乘積。"""
    n_items = len(binary) // item_size
    leftover = len(binary) - n_items * item_size
    print(f"    dtype={dtype_name:<7} item={item_size}B  -> {n_items:,} 個值 (剩 {leftover} bytes)")
    # 看 dims 兩兩相乘有沒有等於 n_items
    vals = list(dims.values())
    for i in range(len(vals)):
        for j in range(len(vals)):
            if i != j and vals[i] * vals[j] == n_items:
                ki = list(dims.keys())[i]
                kj = list(dims.keys())[j]
                print(f"      ✓ 命中：{ki}({vals[i]}) × {kj}({vals[j]}) = {n_items}")
    return n_items, leftover


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def probe(path, hexbytes, dump_header):
    print("=" * 70)
    print(f"檔案：{path}")
    size = os.path.getsize(path)
    print(f"大小：{size:,} bytes ({human(size)})")
    print("=" * 70)

    with open(path, "rb") as f:
        head = f.read(max(hexbytes, 64))

    # 1) 前段 hexdump（原始） ------------------------------------------------ #
    print("\n[1] 原始檔頭 hexdump")
    print(hexdump(head, limit=hexbytes))

    # 2) 壓縮偵測 ------------------------------------------------------------ #
    comp = detect_compression(head)
    print(f"\n[2] 壓縮偵測：{comp}")

    # 3) 取得『解壓後／原始』內容（大檔保護：最多解 64 MB） ------------------ #
    CAP = 64 * 1024 * 1024
    if comp == "gzip":
        with gzip.open(path, "rb") as g:
            content = g.read(CAP + 1)
        print(f"[3] gzip 解壓成功，讀入 {len(content):,} bytes" +
              (" (達上限，已截斷)" if len(content) > CAP else ""))
    elif comp == "zlib":
        with open(path, "rb") as f:
            raw = f.read()
        content = zlib.decompress(raw)[:CAP + 1]
        print(f"[3] zlib 解壓成功，讀入 {len(content):,} bytes")
    else:
        with open(path, "rb") as f:
            content = f.read(CAP + 1)
        print(f"[3] 未壓縮，讀入 {len(content):,} bytes" +
              (" (達上限，已截斷)" if len(content) > CAP else ""))

    content = content[:CAP]

    # 4) 可印字元比例 + 表頭/二進位分界 ------------------------------------- #
    boundary = find_header_boundary(content)
    print(f"\n[4] ASCII 表頭分界 offset ≈ {boundary:,} "
          f"(前段可印比例={printable_ratio(content[:boundary]):.2f})")

    header_text = content[:boundary].decode("latin-1", errors="replace")
    print("\n[4a] 表頭文字（前 2000 字）：")
    print("-" * 70)
    print(header_text[:2000])
    print("-" * 70)

    if dump_header:
        with open(dump_header, "w", encoding="utf-8") as fo:
            fo.write(header_text)
        print(f"  完整表頭已存到：{dump_header}")

    # 5) key=value 解析 ------------------------------------------------------ #
    pairs = parse_kv(header_text)
    print(f"\n[5] 解析出 {len(pairs)} 組 key/value（顯示前 40 組）：")
    for k, v in pairs[:40]:
        vshow = v if len(v) <= 60 else v[:57] + "..."
        print(f"    {k!r:<35} = {vshow!r}")

    dims = guess_dims_from_header(pairs)
    print(f"\n[6] 疑似維度欄位：")
    if dims:
        for k, v in dims.items():
            print(f"    {k} = {v}")
    else:
        print("    （沒抓到明顯維度關鍵字，需人工看表頭）")

    # 7) 二進位資料嘗試 dtype 解讀 ------------------------------------------ #
    binary = content[boundary:]
    print(f"\n[7] 二進位資料區：{len(binary):,} bytes，嘗試各種 dtype：")
    if binary:
        try_reshape(binary, dims, "int16-LE", "<h", 2)
        try_reshape(binary, dims, "int32-LE", "<i", 4)
        try_reshape(binary, dims, "uint16",   "<H", 2)
        try_reshape(binary, dims, "float32",  "<f", 4)
        try_reshape(binary, dims, "float64",  "<d", 8)

        # 印前 16 個值，幫忙肉眼判斷哪種 dtype 合理
        n = min(16, len(binary) // 2)
        i16 = struct.unpack("<" + "h" * n, binary[:n * 2])
        print(f"\n    前 {n} 個值 (int16-LE)：{i16}")
        if len(binary) >= 16 * 4:
            i32 = struct.unpack("<" + "i" * 8, binary[:32])
            print(f"    前 8 個值  (int32-LE)：{i32}")
            f32 = struct.unpack("<" + "f" * 8, binary[:32])
            print(f"    前 8 個值  (float32) ：{tuple(round(x,3) for x in f32)}")

    # 8) numpy 統計（若有） -------------------------------------------------- #
    try:
        import numpy as np
        print("\n[8] numpy 在場 → 額外統計（以 int16-LE 為例）：")
        arr = np.frombuffer(binary[: (len(binary)//2)*2], dtype="<i2")
        print(f"    n={arr.size:,}  min={arr.min()}  max={arr.max()}  "
              f"mean={arr.mean():.1f}  std={arr.std():.1f}")
        # 若 dims 能對上，順手 reshape 印 shape
        vals = list(dims.values())
        for i in range(len(vals)):
            for j in range(len(vals)):
                if i != j and vals[i]*vals[j] == arr.size:
                    print(f"    可 reshape 成 ({vals[i]}, {vals[j]})")
    except ImportError:
        print("\n[8] 未安裝 numpy → 跳過矩陣統計（不影響結構判讀）")

    print("\n" + "=" * 70)
    print("完成。請把以上輸出（尤其 [4a] 表頭、[6] 維度、[7] 命中行）貼回，")
    print("我就能據此寫正式的 readGAS.py（解析 + 繪圖）。")
    print("=" * 70)


def main():
    ap = argparse.ArgumentParser(description="G.A.S. .mea 結構探測器（不繪圖）")
    ap.add_argument("path", nargs="?", default=None,
                    help="要探測的 .mea 檔路徑（省略則跳出檔案總管選檔）")
    ap.add_argument("--bytes", type=int, default=256, dest="hexbytes",
                    help="hexdump 顯示的位元組數 (預設 256)")
    ap.add_argument("--dump-header", default=None,
                    help="把解析出的 ASCII 表頭完整存到這個檔")
    args = ap.parse_args()

    path = resolve_input_path(args.path, title="選擇要探測的 .mea 檔")
    probe(path, args.hexbytes, args.dump_header)


if __name__ == "__main__":
    main()

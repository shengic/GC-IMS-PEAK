# -*- coding: utf-8 -*-
"""組裝 main.py 使用手冊（.docx，公式為 OMML）。

Version: 1.0 — by Albert Sheng（手冊建置，2026-09-21）
"""
import os

from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.shared import Pt

import manual_content as C
import manual_content2 as C2
import manual_content3 as C3
import manual_content4 as C4
import manual_content5 as C5
import manual_content6 as C6
import manual_content7 as C7

TITLE = "GC-IMS 簡介與使用手冊"
COMPANY = "慧技科學有限公司"
from docx_kit import (ACCENT, GREY, INK, _Breaker, new_document, render,
                      rich, set_font)


def cover(doc, brk):
    for _ in range(5):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(p.add_run("慧技科學有限公司"), 13, ACCENT)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(6)
    set_font(p.add_run("簡介與使用手冊"), 34, ACCENT, bold=True)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(p.add_run("GC-IMS 峰偵測與化合物鑑定　main.py"), 12, GREY)

    for _ in range(3):
        doc.add_paragraph()

    for line in ("第一部　功能與介面詳解",
                 "第二部　操作教學"):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(4)
        set_font(p.add_run(line), 12, INK)

    for _ in range(6):
        doc.add_paragraph()
    for line in ("版本 3.3", "Albert Sheng", "以日常語言撰寫，第一次接觸也讀得懂"):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(2)
        set_font(p.add_run(line), 10, GREY)
    brk.arm()


def how_to_read(doc, brk):
    render(doc, [
        ("h2", "這份手冊怎麼讀"),
        ("p", "分成兩部，**建議照順序看**："),
        ("table", (["", "內容", "什麼時候看"],
                   [["**第一部**", "功能與介面詳解",
                     "想知道畫面上某個按鈕、某一欄數字是什麼意思"],
                    ["**第二部**", "操作教學",
                     "想知道「我現在該按哪裡」——照著做就會"]],
                   [2.6, 5.0, 8.9])),
        ("p", "如果您是第一次接觸這台儀器，"
              "**請務必先看第一部的第 2 節「先看懂兩條軸」**。"
              "那一節只有兩頁，但沒看懂的話後面每一個數字都會像亂碼。"),
        ("note", ("三種色塊的意思", [
            "**藍色**＝補充說明，知道了會更順手。",
            "**綠色**＝這是刻意的設計，不是問題。",
            "**橘色**＝請特別注意，多半和「結果可以相信到什麼程度」有關。"])),
        ("warn", ("先講清楚這個程式不做什麼", [
            "**不做定量**——它告訴您「有這個訊號」，不告訴您「濃度多少」。",
            "**不保證鑑定正確**——它給的是候選名單，不是答案。"
            "原因在第一部第 6.1 節。",
            "**不會修改您的原始資料**——所有產物都寫在 results/ 資料夾。"])),
        ("pagebreak", None),
    ], brk)


def main():
    # **輸出直接寫進 docs/**，不是寫在腳本旁邊再手動搬。
    # 手動搬那一步遲早會漏掉，然後 docs/ 裡就是一份舊的而沒有人發現。
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(os.path.dirname(here), "GC-IMS 簡介與使用手冊 main.py.docx")
    doc = new_document(TITLE, COMPANY)
    # **一個共用的分頁器**：換頁狀態要跨 render() 呼叫傳遞，
    # 否則每段各自插一個帶分頁符的空段落，接縫處就會冒出空白頁。
    brk = _Breaker()
    cover(doc, brk)
    how_to_read(doc, brk)
    for blocks in (C.part1(), C2.part1_more(), C3.part1_end(), C4.part1_tail(),
                   C7.part1_extra()):
        render(doc, blocks, brk)
    brk.arm()
    for blocks in (C.part2(), C2.part2_more(), C3.part2_end(), C4.part2_tail(),
                   C2.appendix(), C6.appendix_e(), C5.appendix_d()):
        render(doc, blocks, brk)
    doc.save(out)
    return out


if __name__ == "__main__":
    print("wrote:", main())

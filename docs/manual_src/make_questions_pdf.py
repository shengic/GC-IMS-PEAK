"""產生「請主管確認」的選擇題 PDF（繁體中文）。

字型用 Windows 內建的微軟正黑體（msjh.ttc / msjhbd.ttc）——不必另外安裝。
核取方塊**畫成向量方框**而不是用 ☐ 字元：字型缺字時 ☐ 會變成空白或豆腐格，
而且不會有任何錯誤訊息，等看到 PDF 才發現就來不及了。

Version: 1.0 — by Albert Sheng（手冊建置，2026-09-21）
"""
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, KeepTogether,
                                PageTemplate, Paragraph, Spacer, Table,
                                TableStyle)

FONT, FONT_BD = "JhengHei", "JhengHeiBd"
pdfmetrics.registerFont(TTFont(FONT, r"C:\Windows\Fonts\msjh.ttc", subfontIndex=0))
pdfmetrics.registerFont(TTFont(FONT_BD, r"C:\Windows\Fonts\msjhbd.ttc", subfontIndex=0))

INK = colors.HexColor("#1a1a1a")
GREY = colors.HexColor("#5a5a5a")
RULE = colors.HexColor("#c8c8c8")
BAND = colors.HexColor("#f2f4f7")
ACCENT = colors.HexColor("#1f4e79")

S_TITLE = ParagraphStyle("t", fontName=FONT_BD, fontSize=17, leading=23,
                         textColor=ACCENT, spaceAfter=3)
S_SUB = ParagraphStyle("s", fontName=FONT, fontSize=9.5, leading=14,
                       textColor=GREY, spaceAfter=10)
S_Q = ParagraphStyle("q", fontName=FONT_BD, fontSize=11.5, leading=16,
                     textColor=INK, spaceBefore=2, spaceAfter=4)
S_CTX = ParagraphStyle("c", fontName=FONT, fontSize=8.8, leading=13.2,
                       textColor=GREY, leftIndent=6, rightIndent=6,
                       spaceBefore=1, spaceAfter=2)
S_OPT = ParagraphStyle("o", fontName=FONT, fontSize=10, leading=14.5,
                       textColor=INK, alignment=TA_LEFT)
S_FOOT = ParagraphStyle("f", fontName=FONT, fontSize=9.5, leading=14,
                        textColor=INK)


class Box(Flowable):
    """一個實際畫出來的核取方框（不依賴字型有沒有 ☐）。"""

    def __init__(self, size=9):
        super().__init__()
        self.size = size
        self.width = size
        self.height = size

    def draw(self):
        c = self.canv
        c.setStrokeColor(colors.HexColor("#555555"))
        c.setLineWidth(0.9)
        c.rect(0, 0, self.size, self.size, stroke=1, fill=0)


def context(text):
    """灰底的一行背景說明。"""
    t = Table([[Paragraph(text, S_CTX)]], colWidths=[165 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BAND),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT),
    ]))
    return t


def options(items):
    """方框 + 選項文字。用 Table 才能讓長選項自動換行且對齊。"""
    rows = [[Box(), Paragraph(x, S_OPT)] for x in items]
    t = Table(rows, colWidths=[9 * mm, 156 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (0, -1), 10),
        ("LEFTPADDING", (1, 0), (1, -1), 0),
    ]))
    return t


def question(num, title, ctx, opts, star=False):
    """一整題綁在一起，不要被分頁切開。"""
    head = "Q%s. %s" % (num, title)
    if star:
        head += '  <font color="#b8860b">★ 最重要</font>'
    parts = [Paragraph(head, S_Q)]
    if ctx:
        parts += [context(ctx), Spacer(1, 3)]
    parts += [options(opts), Spacer(1, 11)]
    return KeepTogether(parts)


def rule():
    t = Table([[""]], colWidths=[165 * mm], rowHeights=[1])
    t.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.6, RULE)]))
    return t


def build(path):
    doc = BaseDocTemplate(path, pagesize=A4,
                          leftMargin=22 * mm, rightMargin=23 * mm,
                          topMargin=18 * mm, bottomMargin=18 * mm,
                          title="GC-IMS 專案 — 請協助確認",
                          author="Albert Sheng")
    frame = Frame(doc.leftMargin, doc.bottomMargin, 165 * mm,
                  doc.height, id="f")

    def footer(canv, _doc):
        canv.saveState()
        canv.setFont(FONT, 8)
        canv.setFillColor(GREY)
        canv.drawRightString(A4[0] - 23 * mm, 11 * mm, "第 %d 頁" % canv.getPageNumber())
        canv.drawString(22 * mm, 11 * mm, "GC-IMS 化合物鑑定專案 — 請協助確認")
        canv.restoreState()

    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=footer)])

    f = []
    f.append(Paragraph("GC-IMS 化合物鑑定專案 — 請協助確認", S_TITLE))
    f.append(Paragraph(
        "程式已經可以讀資料、找出峰、比對化合物候選。以下幾件事<b>只有實際操作或"
        "提供資料的人知道</b>，從檔案本身查不出來。每題打勾即可，"
        "<b>不確定也是有用的答案</b>。", S_SUB))

    who = Table([[Paragraph("填寫人：＿＿＿＿＿＿＿＿＿＿", S_FOOT),
                  Paragraph("日期：＿＿＿＿＿＿＿＿", S_FOOT)]],
                colWidths=[100 * mm, 65 * mm])
    who.setStyle(TableStyle([("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    f += [who, rule(), Spacer(1, 9)]

    f.append(question(
        "1", "那六個酮類的保留指數（RI），是在哪種管柱上量的？",
        "檔案：<b>kintonemixed-C4-C9.xlsx</b>。我們的資料看起來像極性管柱，但需要確認。"
        "如果是，目前所有 RI 都高了約 300——而且各化合物差的量不一樣（283～654），"
        "沒辦法用一個固定數字修正。",
        ["<b>極性</b>管柱（如 DB-Wax、Carbowax 類）",
         "<b>非極性</b>管柱（如 DB-5、HP-5、SE-54 類）",
         "不確定，但我可以去問提供這份表的人",
         "不確定，也查不到了"], star=True))

    f.append(question(
        "2a", "藝妓咖啡／鱸魚的 VOCal 專案檔裡，那份 RI 對照表當初跑的標準品是？",
        "專案檔只存了軟體重算後的曲線，<b>原始定位點沒有存進去</b>，還原不出來。"
        "這兩批用的是另一台儀器（1H1-00088），跟 Q1 那台（5H4-00123）不同，答案不一定要一樣。",
        ["跟 Q1 同一支酮類混標", "n-烷類標準品",
         "其他：＿＿＿＿＿＿＿＿＿＿＿＿", "不確定"]))

    f.append(question(
        "2b", "同上，那次用的管柱極性是？", None,
        ["極性", "非極性", "不確定"]))

    f.append(question(
        "3", "這台儀器可以跑一組 n-烷類標準品嗎？",
        "目前的 RI 是<b>借用外面來的一份對照表</b>。自己跑一次 n-烷類就能建立這台機器"
        "自己的尺標，不必再依賴來源不明的數值。",
        ["可以，隨時可以安排", "可以，但需要先採購標準品",
         "目前不可行（原因：＿＿＿＿＿＿＿＿＿＿）", "不確定"]))

    f.append(question(
        "4", "有沒有更完整的「漂移時間」資料庫？",
        "資料庫有 <b>9,958</b> 個化合物登記了保留指數，但只有 <b>84 個</b>同時有漂移時間。"
        "最可靠的判定要兩者都對上，所以<b>其餘 97% 永遠確認不了</b>。"
        "這是資料的上限，跟程式寫得好不好無關。",
        ["有更完整的版本，我可以提供",
         "這就是 G.A.S. 官方的全部了",
         "沒有，但<b>可以另外量測特定目標化合物</b>的漂移時間",
         "不確定，我去問 G.A.S."]))

    f.append(question(
        "5", "VOCal 專案檔裡操作者標註的化合物，是確認過的嗎？",
        "那些標註含 CAS 號、RI、漂移值。如果是確認過的，就是我們唯一能<b>對答案</b>的依據，"
        "可以檢驗程式比對得準不準；如果只是軟體自動猜的，就不能拿來驗證。從檔案裡看不出來。",
        ["<b>已確認</b>（有用標準品或其他方法驗證過）",
         "<b>軟體自動比對的建議</b>，沒有另外驗證",
         "部分確認、部分是建議", "不確定"]))

    f.append(question(
        "6", "檔名像 A_1_1、A_1_2、A_1_3 的三個檔是？",
        "這決定<b>能不能做統計檢定</b>。我們從數據推測是同一份打三次："
        "三個重複之間的相似度約 0.88～0.98，而同材料重新製備一次只有 0.56～0.67。",
        ["<b>同一份</b>前處理好的樣品，連續打三次",
         "<b>分別製備三份</b>，各打一次",
         "視批次而定，不一定", "不確定"]))

    f.append(question(
        "7", "檔名中間那個數字（A_<u>1</u>_1）代表什麼？",
        "我們手上的檔案裡它永遠是 1。",
        ["樣品編號（只是這批剛好都是 1）", "批次／製備次數",
         "其他：＿＿＿＿＿＿＿＿＿＿＿＿", "沒有特別意義"]))

    f.append(question(
        "8", "有沒有實驗記錄可以確認「哪些檔是同一個樣品」？",
        "程式自己判斷的準確度是 43/45（96%）。判斷錯的是把 <b>EHM30</b> 歸到 <b>EHM15</b>"
        "——同一種添加物的不同劑量。有正確答案才知道錯在哪。",
        ["有記錄，我可以提供", "沒有另外的記錄，<b>檔名就是答案</b>", "不確定"]))

    f.append(question(
        "9", "當時用的溶劑是？",
        "標準品裡<b>訊號最強</b>的那個峰（保留時間 329.6 秒）不是那六個酮類中的任何一個，"
        "我們分不出是溶劑、儀器本身的訊號、還是污染物。",
        ["溶劑名稱：＿＿＿＿＿＿＿＿＿＿＿＿",
         "頂空進樣，沒有用溶劑", "不確定"]))

    f.append(question(
        "10", "有特定要找的目標化合物嗎？",
        "「確認特定幾種在不在」跟「從近萬種裡面找」是兩種做法，程式的判定標準會不一樣。",
        ["有，清單如附", "沒有，就是要看樣品裡有什麼",
         "有大方向（例如只關心某一類），但沒有明確清單"]))

    f.append(rule())
    f.append(Spacer(1, 7))
    f.append(Paragraph(
        "<b>如果時間有限，只回 Q1 就很有幫助。</b>　其次是 Q4（決定能確認到什麼程度）"
        "與 Q5（決定我們有沒有辦法知道自己判斷得對不對）。", S_FOOT))

    doc.build(f)
    return path


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(os.path.dirname(here), "GC-IMS_請協助確認.pdf")
    print("wrote:", build(out))

#!/usr/bin/env python3
"""阶段 0 · 生成最小合成样例用于引擎可达性预验证。

在真实企业文档就位前，先用 4 份带中文表格/标题层级/页眉的合成文档
验证 MinerU / Docling / Unstructured 在本机的可达性（能跑通、输出格式对）。
输出到 data/poc/samples/ (gitignore)。
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

OUT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "poc" / "samples"


def gen_pdf_cn(path: Path) -> None:
    """中文 PDF：标题层级 + 表格 + 页眉。"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    # 注册中文字体（STHeiti / PingFang），兼容 .ttc / .ttf

    font_paths = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
    ]
    chosen = None
    for fp in font_paths:
        try:
            pdfmetrics.registerFont(TTFont("CJK", fp))
            chosen = fp
            break
        except Exception as e:  # TTFontError / isTTF issues with .ttc
            print(f"  (字体注册失败 {fp}: {e.__class__.__name__})")
    if chosen is None:
        # 兜底：无中文字体也生成本表，中文可能显示为方框但文本仍可提取
        base = "Helvetica"
    else:
        base = "CJK"

    from reportlab.pdfbase.pdfmetrics import registerFontFamily

    registerFontFamily(base, normal=base, bold=base, italic=base, boldItalic=base)

    doc = SimpleDocTemplate(str(path), pagesize=A4)
    title = ParagraphStyle(
        "t", parent=ParagraphStyle("x"), fontName=base, fontSize=22, leading=28
    )
    h1 = ParagraphStyle(
        "h1", parent=ParagraphStyle("x"), fontName=base, fontSize=16, leading=22
    )
    body = ParagraphStyle(
        "b",
        parent=ParagraphStyle("x"),
        fontName=base,
        fontSize=11,
        leading=16,
        wordWrap="CJK",
    )

    story = [
        Paragraph("2026 年度企业经营分析报告", title),
        Spacer(1, 6 * mm),
        Paragraph("一、总体经营概况", h1),
        Paragraph(
            "本报告汇总企业 2026 年上半年的经营数据，涵盖营业收入、成本结构与利润变动。"
            "报告期内公司持续推进数字化转型，营业收入同比增长 12.5%，毛利率提升至 28.4%。",
            body,
        ),
        Spacer(1, 4 * mm),
        Paragraph("二、分季度收入对比（单位：万元）", h1),
    ]

    data = [
        ["季度", "营业收入", "营业成本", "净利润", "毛利率"],
        ["Q1", "3,240", "2,310", "930", "28.7%"],
        ["Q2", "3,860", "2,760", "1,100", "28.5%"],
        ["Q3", "4,150", "2,940", "1,210", "29.2%"],
        ["Q4", "4,520", "3,210", "1,310", "29.0%"],
    ]
    tbl = Table(data, colWidths=[30 * mm] * 5)
    tbl.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), base),
                ("FONTNAME", (0, 0), (-1, 0), base),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9E2F3")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story += [tbl, Spacer(1, 4 * mm)]
    story.append(
        Paragraph(
            "三、结论与展望 下半年预计保持稳健增长，重点投入人工智能与产品研发，"
            "目标全年营收突破 1.6 亿元，进一步优化成本结构，提升人均产出效率。",
            body,
        )
    )
    doc.build(story)


def gen_pdf_en(path: Path) -> None:
    """英文 PDF：多段 + 简单表格。"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    doc = SimpleDocTemplate(str(path), pagesize=A4)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Enterprise Software Product Requirements", styles["Title"]),
        Spacer(1, 4 * mm),
        Paragraph("Executive Summary", styles["Heading2"]),
        Paragraph(
            "This document specifies the functional and non-functional requirements for the "
            "enterprise knowledge platform, covering ingestion, retrieval, and graph analytics.",
            styles["BodyText"],
        ),
        Spacer(1, 3 * mm),
        Paragraph("Release Scope", styles["Heading2"]),
    ]
    data = [
        ["Module", "Version", "Status"],
        ["Ingestion", "2.1", "Active"],
        ["Retrieval", "3.0", "Beta"],
    ]
    tbl = Table(data, colWidths=[50 * mm] * 3)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#FCE4D6")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ]
        )
    )
    story += [tbl, Spacer(1, 3 * mm)]
    doc.build(story)


def gen_docx(path: Path) -> None:
    """DOCX：标题 + 段落 + 表格。"""
    from docx import Document

    doc = Document()
    doc.add_heading("供应商绩效评估表", level=0)
    doc.add_paragraph(
        "本报告对主要供应商 2026 年上半年的交付质量与准时率进行综合评估。"
    )
    doc.add_heading("一、交付准时率", level=1)
    table = doc.add_table(rows=4, cols=4)
    headers = ["供应商", "准时率", "合格率", "评级"]
    rows = [
        ["A 公司", "98.2%", "99.5%", "优"],
        ["B 公司", "95.0%", "97.8%", "良"],
        ["C 公司", "90.1%", "94.2%", "中"],
    ]
    for j, h in enumerate(headers):
        table.rows[0].cells[j].text = h
    for i, row in enumerate(rows, start=1):
        for j, v in enumerate(row):
            table.rows[i].cells[j].text = v
    doc.add_heading("二、结论", level=1)
    doc.add_paragraph(
        "整体供应体系稳定性良好，A 公司表现最优，C 公司需重点改善交付准时率。"
    )
    doc.save(str(path))


def gen_xlsx(path: Path) -> None:
    """XLSX：多行多列表格数据。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "营收数据"
    headers = ["月份", "营收", "成本", "利润"]
    ws.append(headers)
    for i in range(1, 13):
        ws.append([f"2026-{i:02d}", 300 + i * 10, 210 + i * 5, 90 + i * 5])
    wb.save(str(path))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    batch = {
        "sample_report_zh.pdf": gen_pdf_cn,
        "sample_product_en.pdf": gen_pdf_en,
        "sample_vendor.docx": gen_docx,
        "sample_revenue.xlsx": gen_xlsx,
    }
    for name, fn in batch.items():
        target = OUT_DIR / name
        try:
            fn(target)
            print(f"  [OK] {name} ({target.stat().st_size} bytes)")
        except Exception as e:  # pragma: no cover
            print(f"  [ERR] {name}: {e.__class__.__name__}: {e}")
    print(f"\n合成样例目录: {OUT_DIR}")


if __name__ == "__main__":
    main()

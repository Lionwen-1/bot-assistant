"""Create local draft PDFs for a software copyright application.

Outputs are intentionally ignored by Git. The applicant must verify ownership,
dates, identity documents, and current filing requirements before submission.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.7.1"
PRODUCT = "bot助手多账号连接与 AstrBot 项目运维软件"
SOURCE_FILES = [
    ROOT / "run.pyw",
    *sorted((ROOT / "bot_assistant").glob("*.py")),
    ROOT / "server" / "manager.py",
    ROOT / "server" / "thinking_plugin" / "main.py",
]
LINES_PER_PAGE = 50


def register_fonts() -> tuple[str, str]:
    fonts = Path("C:/Windows/Fonts")
    try:
        pdfmetrics.registerFont(TTFont("SourceCN", str(fonts / "simfang.ttf")))
        pdfmetrics.registerFont(TTFont("HeadingCN", str(fonts / "simhei.ttf")))
        return "SourceCN", "HeadingCN"
    except (OSError, ValueError):
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light", "STSong-Light"


def source_lines() -> tuple[list[tuple[str, int, str]], list[dict]]:
    lines: list[tuple[str, int, str]] = []
    manifest: list[dict] = []
    for path in SOURCE_FILES:
        raw = path.read_bytes()
        relative = path.relative_to(ROOT).as_posix()
        content = raw.decode("utf-8-sig").splitlines()
        manifest.append({"path": relative, "lines": len(content),
                         "sha256": hashlib.sha256(raw).hexdigest()})
        lines.extend((relative, number, value.expandtabs(4))
                     for number, value in enumerate(content, 1))
    return lines, manifest


def draw_source(output: Path, lines: list[tuple[str, int, str]],
                body_font: str, heading_font: str) -> dict:
    pages = [lines[index:index + LINES_PER_PAGE]
             for index in range(0, len(lines), LINES_PER_PAGE)]
    selected = (list(range(30)) + list(range(len(pages) - 30, len(pages)))
                if len(pages) > 60 else list(range(len(pages))))
    width, height = A4
    pdf = canvas.Canvas(str(output), pagesize=A4, pageCompression=1)
    pdf.setTitle(f"{PRODUCT} V{VERSION} 源程序鉴别材料草稿")
    pdf.setAuthor("Draft for applicant review")
    for submitted_page, source_page in enumerate(selected, 1):
        page = pages[source_page]
        pdf.setFont(heading_font, 12)
        pdf.drawString(34, height - 34, f"{PRODUCT} V{VERSION}")
        pdf.setFont(body_font, 8)
        pdf.drawString(34, height - 49,
                       f"源程序鉴别材料草稿 | 原始第 {source_page + 1}/{len(pages)} 页")
        pdf.setStrokeColor(colors.HexColor("#8b9a97"))
        pdf.line(34, height - 56, width - 34, height - 56)
        y = height - 75
        for relative, number, value in page:
            pdf.setFont("Courier", 6.7)
            pdf.setFillColor(colors.HexColor("#71817d"))
            pdf.drawRightString(54, y, str(number))
            pdf.setFillColor(colors.HexColor("#1d2c29"))
            max_width = width - 92
            size = 7.2
            while pdfmetrics.stringWidth(value, body_font, size) > max_width and size > 4.4:
                size -= 0.2
            if pdfmetrics.stringWidth(value, body_font, size) > max_width:
                raise ValueError(f"源程序行过长，无法完整排版：{relative}:{number}")
            pdf.setFont(body_font, size)
            pdf.drawString(66, y, value)
            y -= 13.7
        pdf.setStrokeColor(colors.HexColor("#8b9a97"))
        pdf.line(34, 52, width - 34, 52)
        pdf.setFont(body_font, 8)
        pdf.setFillColor(colors.HexColor("#667571"))
        pdf.drawString(34, 36, f"文件范围：{page[0][0]} — {page[-1][0]}")
        pdf.drawRightString(width - 34, 36, f"提交页 {submitted_page}/{len(selected)}")
        pdf.showPage()
    pdf.save()
    return {"all_source_lines": len(lines), "all_source_pages": len(pages),
            "submitted_pages": len(selected),
            "selected_original_pages": [index + 1 for index in selected]}


def manual_elements(markdown: str, body_font: str, heading_font: str):
    title = ParagraphStyle("title", fontName=heading_font, fontSize=17,
                           leading=27, alignment=TA_CENTER, spaceAfter=18,
                           keepWithNext=True)
    section = ParagraphStyle("section", fontName=heading_font, fontSize=12.5,
                             leading=20, spaceBefore=16, spaceAfter=9,
                             keepWithNext=True)
    body = ParagraphStyle("body", fontName=body_font, fontSize=10.2,
                          leading=17.5, alignment=TA_LEFT, spaceAfter=9)
    bullet = ParagraphStyle("bullet", parent=body, leftIndent=17,
                            firstLineIndent=-11, spaceAfter=5)
    elements = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            elements.append(Paragraph(html.escape(" ".join(paragraph)), body))
            paragraph.clear()

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            flush()
        elif line.startswith("# "):
            flush()
            elements.append(Paragraph(html.escape(line[2:]), title))
            elements.append(Paragraph("登记材料草稿 · 待权利人核对", body))
        elif line.startswith("## "):
            flush()
            elements.append(Paragraph(html.escape(line[3:]), section))
        elif line.startswith("- "):
            flush()
            elements.append(Paragraph("• " + html.escape(line[2:]), bullet))
        else:
            paragraph.append(line.replace("`", ""))
    flush()
    return elements


def draw_manual(output: Path, body_font: str, heading_font: str) -> None:
    markdown = (ROOT / "docs" / "SOFTWARE_MANUAL.md").read_text(encoding="utf-8")
    width, _height = A4

    def page_frame(pdf, doc):
        pdf.setFont(body_font, 8)
        pdf.setFillColor(colors.HexColor("#64736f"))
        pdf.drawString(48, A4[1] - 30, f"{PRODUCT} V{VERSION} · 使用说明书草稿")
        pdf.drawRightString(width - 48, 32, str(doc.page))

    document = SimpleDocTemplate(str(output), pagesize=A4,
                                 topMargin=62, bottomMargin=52,
                                 leftMargin=48, rightMargin=48,
                                 title=f"{PRODUCT} V{VERSION} 使用说明书草稿",
                                 author="Draft for applicant review")
    document.build(manual_elements(markdown, body_font, heading_font),
                   onFirstPage=page_frame, onLaterPages=page_frame)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "pdf")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    body_font, heading_font = register_fonts()
    lines, manifest = source_lines()
    source_pdf = output / f"bot-assistant-{VERSION}-source-excerpt.pdf"
    manual_pdf = output / f"bot-assistant-{VERSION}-user-manual.pdf"
    layout = draw_source(source_pdf, lines, body_font, heading_font)
    draw_manual(manual_pdf, body_font, heading_font)
    (output / f"bot-assistant-{VERSION}-source-manifest.json").write_text(
        json.dumps({"version": VERSION, "files": manifest, **layout},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"source={source_pdf}")
    print(f"manual={manual_pdf}")
    print(f"source_pages={layout['submitted_pages']}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
generate_pdfs.py — Converts EdgeIQ markdown files to PDFs.
Runs automatically at 11:59 PM ET via the Paper Trader Bot scheduler.
Regenerates: EdgeIQ_Private_Build_Notes.pdf, EdgeIQ_IP_Documentation.pdf
"""

import re
import os
from datetime import datetime
from fpdf import FPDF
from fpdf.enums import XPos, YPos

DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.local')

_UNICODE_MAP = str.maketrans({
    '\u2014': '--',
    '\u2013': '-',
    '\u2018': "'",
    '\u2019': "'",
    '\u201c': '"',
    '\u201d': '"',
    '\u2026': '...',
    '\u2022': '-',
    '\u2192': '->',
    '\u2190': '<-',
    '\u00a0': ' ',
    '\xb1': '+/-',
    '\xd7': 'x',
    '\xf7': '/',
})


def _sanitize(text: str) -> str:
    text = text.translate(_UNICODE_MAP)
    return text.encode('latin-1', errors='ignore').decode('latin-1')


def _strip_inline(text: str) -> str:
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    return _sanitize(text)


def _t(text: str) -> str:
    return _strip_inline(text)


def render_markdown_to_pdf(md_path: str, pdf_path: str, title: str):
    pdf = FPDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(18, 18, 18)
    pdf.add_page()

    ts = datetime.now().strftime('%B %d, %Y at %I:%M %p ET')

    # Cover block
    pdf.set_font('Helvetica', 'B', 20)
    pdf.set_text_color(15, 15, 15)
    pdf.multi_cell(0, 12, _sanitize(title), align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 7, f'Generated: {_sanitize(ts)}', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(180, 180, 180)
    pdf.cell(0, 7, 'EDGEIQ -- CONFIDENTIAL / TRADE SECRET', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)
    pdf.set_draw_color(80, 80, 80)
    pdf.set_line_width(0.4)
    pdf.line(18, pdf.get_y(), 192, pdf.get_y())
    pdf.set_line_width(0.2)
    pdf.ln(7)
    pdf.set_text_color(30, 30, 30)

    with open(md_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    in_code = False

    for line in lines:
        raw = _sanitize(line.rstrip('\n'))
        stripped = raw.strip()

        # Code block fence
        if stripped.startswith('```'):
            in_code = not in_code
            if in_code:
                pdf.ln(2)
            else:
                pdf.ln(3)
            continue

        if in_code:
            pdf.set_font('Courier', '', 8)
            pdf.set_text_color(50, 50, 50)
            pdf.set_fill_color(248, 248, 248)
            pdf.multi_cell(0, 5, raw, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            continue

        text = _t(raw)

        # Horizontal rule
        if re.match(r'^-{3,}$', stripped):
            pdf.ln(3)
            pdf.set_draw_color(200, 200, 200)
            pdf.line(18, pdf.get_y(), 192, pdf.get_y())
            pdf.set_draw_color(200, 200, 200)
            pdf.ln(5)
            continue

        # H1
        if re.match(r'^# [^#]', raw):
            pdf.set_font('Helvetica', 'B', 16)
            pdf.set_text_color(15, 15, 15)
            pdf.ln(5)
            pdf.multi_cell(0, 10, _t(raw[2:]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(1)
            continue

        # H2
        if re.match(r'^## ', raw):
            pdf.set_font('Helvetica', 'B', 13)
            pdf.set_text_color(25, 25, 25)
            pdf.ln(4)
            pdf.multi_cell(0, 9, _t(raw[3:]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(1)
            continue

        # H3
        if re.match(r'^### ', raw):
            pdf.set_font('Helvetica', 'B', 11)
            pdf.set_text_color(40, 40, 40)
            pdf.ln(3)
            pdf.multi_cell(0, 8, _t(raw[4:]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            continue

        # H4
        if re.match(r'^#### ', raw):
            pdf.set_font('Helvetica', 'B', 10)
            pdf.set_text_color(55, 55, 55)
            pdf.ln(2)
            pdf.multi_cell(0, 7, _t(raw[5:]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            continue

        # Bullet / numbered list
        m = re.match(r'^(\s*)([-*+]|\d+\.)\s+(.*)', raw)
        if m:
            indent = len(m.group(1))
            content = _t(m.group(3))
            left = 18 + (indent * 2.5) + 4
            pdf.set_font('Helvetica', '', 9.5)
            pdf.set_text_color(40, 40, 40)
            pdf.set_x(left)
            pdf.cell(4, 5.5, '-')
            pdf.multi_cell(0, 5.5, content, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            continue

        # Table row
        if stripped.startswith('|'):
            pdf.set_font('Courier', '', 8.5)
            pdf.set_text_color(50, 50, 50)
            pdf.multi_cell(0, 5.5, _t(stripped), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            continue

        # Blank line
        if stripped == '':
            pdf.ln(3)
            continue

        # Regular paragraph
        pdf.set_font('Helvetica', '', 10)
        pdf.set_text_color(40, 40, 40)
        pdf.multi_cell(0, 6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Footer on last page
    pdf.set_y(-14)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_text_color(160, 160, 160)
    pdf.cell(0, 8, f'Page {pdf.page} | Auto-generated {_sanitize(ts)} | EDGEIQ CONFIDENTIAL', align='C')

    pdf.output(pdf_path)
    size_kb = os.path.getsize(pdf_path) // 1024
    print(f'[PDF] {os.path.basename(pdf_path)} -- {size_kb} KB, {pdf.page} pages')
    return pdf.page


def generate_all_pdfs() -> list[str]:
    tasks = [
        (
            os.path.join(DOCS_DIR, 'build_notes.md'),
            os.path.join(DOCS_DIR, 'EdgeIQ_Private_Build_Notes.pdf'),
            'EdgeIQ -- Private Build Notes',
        ),
        (
            os.path.join(DOCS_DIR, 'ip_documentation.md'),
            os.path.join(DOCS_DIR, 'EdgeIQ_IP_Documentation.pdf'),
            'EdgeIQ -- Intellectual Property Documentation',
        ),
    ]

    results = []
    for md_path, pdf_path, title in tasks:
        if not os.path.exists(md_path):
            results.append(f'SKIP {os.path.basename(md_path)} not found')
            continue
        try:
            pages = render_markdown_to_pdf(md_path, pdf_path, title)
            results.append(f'OK {os.path.basename(pdf_path)} ({pages}pp)')
        except Exception as e:
            results.append(f'ERROR {os.path.basename(pdf_path)}: {e}')

    return results


if __name__ == '__main__':
    print(f'EdgeIQ PDF Generator -- {datetime.now().strftime("%Y-%m-%d %H:%M")} ET')
    for r in generate_all_pdfs():
        print(r)

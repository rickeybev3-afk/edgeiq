#!/usr/bin/env python3
"""
generate_pdfs.py — Converts EdgeIQ markdown files to PDFs.
Runs automatically at 11:59 PM ET via the Paper Trader Bot scheduler.

PDFs generated (all 9 documents):
  EdgeIQ_Public_Build_Notes.pdf                     <- build_notes_private.md
  EdgeIQ_Private_Build_Notes.pdf                    <- build_notes.md
  EdgeIQ_IP_Documentation.pdf                       <- ip_documentation.md
  EdgeIQ_Study_Notes.pdf                            <- edgeiq_study_notes.md        (if present)
  EdgeIQ_System_Documentation.pdf                   <- replit.md                    (if present)
  EdgeIQ_App_Source.pdf                             <- app.py + replit.md           (combined live source)
  EdgeIQ_Beta_Tester_Screening.pdf                  <- beta_tester_screening.md     (if present)
  EdgeIQ_Cognitive_Profiling_Interview_Methodology.pdf <- cognitive_profiling_interview_methodology.md (if present)
  EdgeIQ_NDA_Template.pdf                           <- nda_template.md              (if present)
"""

import re
import os
from datetime import datetime
from fpdf import FPDF
from fpdf.enums import XPos, YPos

DOCS_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.local')
PROJ_ROOT  = os.path.dirname(os.path.abspath(__file__))

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
    text = re.sub(r'\*(.+?)\*',     r'\1', text)
    text = re.sub(r'`(.+?)`',       r'\1', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    return _sanitize(text)


def _t(text: str) -> str:
    return _strip_inline(text)


# ── Position-aware spacer ───────────────────────────────────────────────────
# Skip the spacer when we're already near the top of a fresh page (Y < 40mm)
# or within 25mm of the auto-page-break margin — avoids near-blank page tops
# and orphaned headings.
def _ln(pdf: FPDF, n: float) -> None:
    """Add vertical space only when we're far enough from page edges."""
    page_h   = pdf.h                        # e.g. 297mm for A4
    margin   = 20                           # auto_page_break margin
    cur_y    = pdf.get_y()
    near_top = cur_y < 42                   # just past the cover / top margin
    near_end = cur_y > (page_h - margin - 25)

    if near_top or near_end:
        return
    pdf.ln(n)


def render_markdown_to_pdf(md_path: str, pdf_path: str, title: str):
    pdf = FPDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(18, 18, 18)
    pdf.add_page()

    ts = datetime.now().strftime('%B %d, %Y at %I:%M %p ET')

    # Cover block
    pdf.set_font('Helvetica', 'B', 20)
    pdf.set_text_color(15, 15, 15)
    pdf.multi_cell(0, 12, _sanitize(title), align='L',
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 7, f'Generated: {_sanitize(ts)}',
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(180, 180, 180)
    pdf.cell(0, 7, 'EDGEIQ -- CONFIDENTIAL / TRADE SECRET',
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)
    pdf.set_draw_color(80, 80, 80)
    pdf.set_line_width(0.4)
    pdf.line(18, pdf.get_y(), 192, pdf.get_y())
    pdf.set_line_width(0.2)
    pdf.ln(5)
    pdf.set_text_color(30, 30, 30)

    with open(md_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    in_code = False

    for line in lines:
        raw     = _sanitize(line.rstrip('\n'))
        stripped = raw.strip()

        # Code block fence
        if stripped.startswith('```'):
            in_code = not in_code
            if in_code:
                _ln(pdf, 1)
            else:
                _ln(pdf, 2)
            continue

        if in_code:
            pdf.set_font('Courier', '', 8)
            pdf.set_text_color(50, 50, 50)
            pdf.set_fill_color(248, 248, 248)
            pdf.multi_cell(0, 4.5, raw, fill=True,
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            continue

        text = _t(raw)

        # Horizontal rule
        if re.match(r'^-{3,}$', stripped):
            _ln(pdf, 2)
            pdf.set_draw_color(200, 200, 200)
            pdf.line(18, pdf.get_y(), 192, pdf.get_y())
            _ln(pdf, 3)
            continue

        # H1
        if re.match(r'^# [^#]', raw):
            _ln(pdf, 3)
            pdf.set_font('Helvetica', 'B', 16)
            pdf.set_text_color(15, 15, 15)
            pdf.multi_cell(0, 10, _t(raw[2:]),
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            continue

        # H2
        if re.match(r'^## ', raw):
            _ln(pdf, 2)
            pdf.set_font('Helvetica', 'B', 13)
            pdf.set_text_color(25, 25, 25)
            pdf.multi_cell(0, 9, _t(raw[3:]),
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            continue

        # H3
        if re.match(r'^### ', raw):
            _ln(pdf, 2)
            pdf.set_font('Helvetica', 'B', 11)
            pdf.set_text_color(40, 40, 40)
            pdf.multi_cell(0, 8, _t(raw[4:]),
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            continue

        # H4
        if re.match(r'^#### ', raw):
            _ln(pdf, 1)
            pdf.set_font('Helvetica', 'B', 10)
            pdf.set_text_color(55, 55, 55)
            pdf.multi_cell(0, 7, _t(raw[5:]),
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            continue

        # Bullet / numbered list
        m = re.match(r'^(\s*)([-*+]|\d+\.)\s+(.*)', raw)
        if m:
            indent  = len(m.group(1))
            content = _t(m.group(3))
            left    = 18 + (indent * 2.5) + 4
            pdf.set_font('Helvetica', '', 9.5)
            pdf.set_text_color(40, 40, 40)
            pdf.set_x(left)
            pdf.cell(4, 5.5, '-')
            pdf.multi_cell(0, 5.5, content,
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            continue

        # Table row
        if stripped.startswith('|'):
            pdf.set_font('Courier', '', 8.5)
            pdf.set_text_color(50, 50, 50)
            pdf.multi_cell(0, 5, _t(stripped),
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            continue

        # Blank line — use smart spacer (single small gap, not 3pt)
        if stripped == '':
            _ln(pdf, 2)
            continue

        # Regular paragraph
        pdf.set_font('Helvetica', '', 10)
        pdf.set_text_color(40, 40, 40)
        pdf.multi_cell(0, 6, text,
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Footer on last page
    pdf.set_y(-14)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_text_color(160, 160, 160)
    pdf.cell(
        0, 8,
        f'Page {pdf.page} | Auto-generated {_sanitize(ts)} | EDGEIQ CONFIDENTIAL',
        align='C',
    )

    pdf.output(pdf_path)
    size_kb = os.path.getsize(pdf_path) // 1024
    print(f'[PDF] {os.path.basename(pdf_path)} -- {size_kb} KB, {pdf.page} pages')
    return pdf.page


def render_app_source_to_pdf(pdf_path: str) -> int:
    """
    Generate EdgeIQ_App_Source.pdf by combining app.py (raw source) and
    replit.md (system docs) into a single timestamped PDF.
    """
    app_py   = os.path.join(PROJ_ROOT, 'app.py')
    replit_md = os.path.join(PROJ_ROOT, 'replit.md')

    sources_found = [p for p in (app_py, replit_md) if os.path.exists(p)]
    if not sources_found:
        raise FileNotFoundError('Neither app.py nor replit.md found')

    import tempfile
    ts_header = datetime.now().strftime('%B %d, %Y at %I:%M %p ET')

    with tempfile.NamedTemporaryFile(mode='w', suffix='.md',
                                     delete=False, encoding='utf-8') as tmp:
        tmp_path = tmp.name
        tmp.write(f'# EdgeIQ -- Application Source\n\n')
        tmp.write(f'*Snapshot: {ts_header}*\n\n')
        tmp.write('---\n\n')

        if os.path.exists(app_py):
            tmp.write('## app.py\n\n')
            tmp.write('```python\n')
            with open(app_py, 'r', encoding='utf-8') as f:
                tmp.write(f.read())
            tmp.write('\n```\n\n')

        if os.path.exists(replit_md):
            tmp.write('---\n\n')
            tmp.write('## replit.md\n\n')
            with open(replit_md, 'r', encoding='utf-8') as f:
                tmp.write(f.read())

    try:
        pages = render_markdown_to_pdf(tmp_path, pdf_path,
                                       'EdgeIQ -- Application Source')
    finally:
        os.unlink(tmp_path)

    return pages


def generate_all_pdfs() -> list[str]:
    """
    Build all PDFs from their markdown sources.
    Sources without a matching .md file are skipped gracefully.
    """
    tasks = [
        (
            os.path.join(DOCS_DIR, 'build_notes_private.md'),
            os.path.join(DOCS_DIR, 'EdgeIQ_Public_Build_Notes.pdf'),
            'EdgeIQ -- Public Build Notes',
        ),
        (
            os.path.join(DOCS_DIR, 'build_notes.md'),
            os.path.join(DOCS_DIR, 'EdgeIQ_Private_Build_Notes.pdf'),
            'EdgeIQ -- Private Build Notes (Confidential)',
        ),
        (
            os.path.join(DOCS_DIR, 'ip_documentation.md'),
            os.path.join(DOCS_DIR, 'EdgeIQ_IP_Documentation.pdf'),
            'EdgeIQ -- Intellectual Property Documentation',
        ),
        (
            os.path.join(DOCS_DIR, 'edgeiq_study_notes.md'),
            os.path.join(DOCS_DIR, 'EdgeIQ_Study_Notes.pdf'),
            'EdgeIQ -- Study Notes',
        ),
        (
            os.path.join(PROJ_ROOT, 'replit.md'),
            os.path.join(DOCS_DIR, 'EdgeIQ_System_Documentation.pdf'),
            'EdgeIQ -- System Documentation',
        ),
        (
            os.path.join(DOCS_DIR, 'beta_tester_screening.md'),
            os.path.join(DOCS_DIR, 'EdgeIQ_Beta_Tester_Screening.pdf'),
            'EdgeIQ -- Beta Tester Screening',
        ),
        (
            os.path.join(DOCS_DIR, 'cognitive_profiling_interview_methodology.md'),
            os.path.join(DOCS_DIR, 'EdgeIQ_Cognitive_Profiling_Interview_Methodology.pdf'),
            'EdgeIQ -- Cognitive Profiling Interview Methodology',
        ),
        (
            os.path.join(DOCS_DIR, 'nda_template.md'),
            os.path.join(DOCS_DIR, 'EdgeIQ_NDA_Template.pdf'),
            'EdgeIQ -- NDA Template',
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

    # App Source PDF — always regenerate from live app.py + replit.md
    app_source_pdf = os.path.join(DOCS_DIR, 'EdgeIQ_App_Source.pdf')
    try:
        pages = render_app_source_to_pdf(app_source_pdf)
        results.append(f'OK EdgeIQ_App_Source.pdf ({pages}pp)')
    except Exception as e:
        results.append(f'ERROR EdgeIQ_App_Source.pdf: {e}')

    return results


if __name__ == '__main__':
    print(f'EdgeIQ PDF Generator -- {datetime.now().strftime("%Y-%m-%d %H:%M")} ET')
    for r in generate_all_pdfs():
        print(r)

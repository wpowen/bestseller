"""Markdown and simple DOCX; intentionally independent."""
import os
from pathlib import Path
import re

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt

from .storage import save


def export(out, title, outline, chapters):
    out = Path(out)
    markdown = '# ' + title + '\n\n'
    for i, text in enumerate(chapters, 1):
        chapter = '## 第' + str(i) + '章 ' + outline[i - 1]['title'] + '\n\n' + text + '\n\n'
        save(out / f'chapter-{i:03d}.md', chapter)
        markdown += chapter
    save(out / 'book.md', markdown)
    document = Document()
    style = document.styles['Normal']
    style.font.name = 'Songti SC'
    style.font.size = Pt(11)
    style.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), 'Songti SC')
    for block in re.split(r'\n\s*\n', markdown.strip()):
        if block.startswith('# '):
            document.add_heading(block[2:], 0)
        elif block.startswith('## '):
            document.add_heading(block[3:], 1)
        elif block.strip():
            document.add_paragraph(block.strip())
    temp = out / 'book.tmp.docx'
    document.save(temp)
    os.chmod(temp, 0o600)
    temp.replace(out / 'book.docx')

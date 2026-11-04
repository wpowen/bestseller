"""DOCX and EPUB using existing python-docx and the standard library."""
from hashlib import sha256
from html import escape
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED


def docx_bytes(title, rows):
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt
    document = Document()
    style = document.styles['Normal']
    style.font.name = 'Songti SC'
    style.font.size = Pt(11)
    style.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), 'Songti SC')
    document.core_properties.title = title
    document.add_heading(title, 0)
    document.add_paragraph('目录')
    for _, label, _ in rows:
        document.add_paragraph(label)
    for _, label, body in rows:
        document.add_heading(label, 1)
        for line in body.split('\n'):
            document.add_paragraph(line)
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def xhtml(title, content):
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN">'
            f'<head><title>{escape(title)}</title><style>p {{white-space: pre-wrap; line-height: 1.8;}}'
            'body {font-family: serif;}</style></head><body>' + content + '</body></html>')


def epub_bytes(title, rows):
    title_xml = escape(title)
    identity = sha256(repr((title, rows)).encode('utf-8')).hexdigest()
    manifest = '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    manifest += '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    spine, links, points = [], [], []
    stream = BytesIO()
    with ZipFile(stream, 'w', compression=ZIP_DEFLATED) as z:
        z.writestr('mimetype', 'application/epub+zip', compress_type=ZIP_STORED)
        z.writestr('META-INF/container.xml', '<?xml version="1.0"?>'
                   '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                   '</rootfiles></container>')
        for order, (n, label, body) in enumerate(rows, 1):
            name = f'chapter-{n:03d}.xhtml'
            manifest += f'<item id="ch{n}" href="{name}" media-type="application/xhtml+xml"/>'
            spine.append(f'<itemref idref="ch{n}"/>')
            links.append(f'<li><a href="{name}">{escape(label)}</a></li>')
            points.append(f'<navPoint id="ch{n}" playOrder="{order}"><navLabel><text>{escape(label)}</text>'
                          f'</navLabel><content src="{name}"/></navPoint>')
            body_xml = escape(body).replace('\r', '&#13;')
            z.writestr('OEBPS/' + name, xhtml(label, f'<h1>{escape(label)}</h1><p>{body_xml}</p>'))
        nav = f'<h1>{title_xml}</h1><nav epub:type="toc" id="toc"><h2>目录</h2><ol>{"".join(links)}</ol></nav>'
        z.writestr('OEBPS/nav.xhtml', xhtml(title, nav))
        z.writestr('OEBPS/toc.ncx', '<?xml version="1.0" encoding="utf-8"?>'
                   '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                   f'<head><meta name="dtb:uid" content="{identity}"/></head><docTitle><text>{title_xml}</text>'
                   f'</docTitle><navMap>{"".join(points)}</navMap></ncx>')
        z.writestr('OEBPS/content.opf', '<?xml version="1.0" encoding="utf-8"?>'
                   '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="zh-CN">'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   f'<dc:identifier id="uid">{identity}</dc:identifier><dc:title>{title_xml}</dc:title>'
                   '<dc:language>zh-CN</dc:language><meta property="dcterms:modified">2000-01-01T00:00:00Z</meta>'
                   f'</metadata><manifest>{manifest}</manifest><spine toc="ncx">{"".join(spine)}</spine></package>')
    return stream.getvalue()

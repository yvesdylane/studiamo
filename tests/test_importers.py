"""Checks the note importers (no database, no AI).

    uv run python -m tests.test_importers
"""

from __future__ import annotations

import io
import sys
import zipfile

from src.ai.importers import ImportError_, read_upload
from tests.harness import Checks

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx(paragraphs: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f"<w:document {W}><w:body>{paragraphs}</w:body></w:document>")
    return buf.getvalue()


def p(text: str, style: str = "", bold: bool = False, numbered: bool = False) -> str:
    ppr = ""
    if style or numbered:
        ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + ('<w:numPr><w:ilvl w:val="0"/></w:numPr>' if numbered else "") + "</w:pPr>"
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f"<w:p>{ppr}<w:r>{rpr}<w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p>"


def pptx(slides: list[list[str]]) -> bytes:
    a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, texts in enumerate(slides, 1):
            body = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in texts)
            z.writestr(f"ppt/slides/slide{i}.xml", f"<p:sld xmlns:p=\"x\" {a}><a:txBody>{body}</a:txBody></p:sld>")
    return buf.getvalue()


def main() -> int:
    t = Checks()
    t.section("word")
    d = read_upload("week-1_notes.docx", docx(
        p("Pure functions", "Title") + p("Why it matters", "Heading1") + p("Easy to ") + p("test", bold=True)
        + p("first point", numbered=True) + p("second point", numbered=True) + p("print(1)", "Code") + p("x = 2", "Code")
        + '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Name</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Score</w:t></w:r></w:p></w:tc></w:tr>'
          '<w:tr><w:tc><w:p><w:r><w:t>Amina</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>15</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'))
    t.check("docx: title, heading, bold", d.kind == "docx" and "# Pure functions" in d.markdown and "## Why it matters" in d.markdown
            and "**test**" in d.markdown, d.markdown)
    t.check("docx: list items kept together", "- first point\n- second point" in d.markdown, d.markdown)
    t.check("docx: code style -> code box", '"""\nprint(1)\nx = 2\n"""' in d.markdown, d.markdown)
    t.check("docx: table", "| Name | Score |" in d.markdown and "| Amina | 15 |" in d.markdown)
    t.check("docx: plain text for the AI", "Pure functions" in d.text and "Amina" in d.text)
    t.check("title from the file name", d.title == "week 1 notes", d.title)

    t.section("powerpoint")
    s = read_upload("deck.pptx", pptx([["Intro", "What we do", "Why"], ["Second slide"]]))
    t.check("pptx: one section per slide", s.kind == "pptx" and "## Intro\n\n- What we do\n- Why" in s.markdown and "## Second slide" in s.markdown, s.markdown)

    t.section("pdf, html, text")
    pdf = read_upload("x.pdf", b"%PDF-1.4 hello")
    t.check("pdf kept for the AI (no plain conversion)", pdf.kind == "pdf" and pdf.pdf and pdf.markdown == "")
    h = read_upload("page.html", b"<html><body><h2>Loops</h2><p>Use <strong>for</strong>.</p><pre>for i in x:\n    print(i)</pre><script>alert(1)</script></body></html>")
    t.check("html -> markdown, scripts dropped", "Loops" in h.markdown and "**for**" in h.markdown and '"""\nfor i in x:' in h.markdown and "alert" not in h.markdown, h.markdown)
    txt = read_upload("notes.txt", "Hello — café".encode())
    t.check("text kept as it is (utf-8)", txt.markdown == "Hello — café")
    t.check("cp1252 text read", read_upload("old.txt", "caf\xe9".encode("cp1252")).text == "café")

    t.section("refusals")
    def refused(name, data):
        try:
            read_upload(name, data)
        except ImportError_:
            return True
        return False
    t.check("broken zip refused", refused("bad.docx", b"PK\x03\x04garbage"))
    t.check("zip that is not Word refused", refused("other.docx", docx("")[:0] or _zip_without_document()))
    t.check("too large refused", refused("big.txt", b"a" * (15 * 1024 * 1024 + 1)))
    return t.summary()


def _zip_without_document() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "hi")
    return buf.getvalue()


if __name__ == "__main__":
    sys.exit(main())

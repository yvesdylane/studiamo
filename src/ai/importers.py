"""Reading notes people already have, for "Import" in the note editor.

    doc = read_upload(filename, data)        # -> Imported(kind, text, pdf, markdown)

  .md .markdown .txt     the text as it is
  .html .htm             text with headings, lists and code kept as Markdown
  .docx (Word)           paragraphs, headings, lists, tables, code-like styles
  .pptx (PowerPoint)     one section per slide: title, then its text
  .pdf                   sent to Claude as the PDF itself (it reads the layout);
                         without AI, PDFs cannot be imported

`markdown` is a plain conversion that needs no AI: imports work even for
lecturers who did not add an API key. With a key, Claude turns the text (or the
PDF) into proper Studiamo Markdown (src/ai/NoteConverter.py).

Word/PowerPoint files are ZIP archives of XML; they are read with the standard
library only, and with limits (size, number of entries) against zip bombs.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

MAX_UPLOAD = 15 * 1024 * 1024
MAX_UNZIPPED = 60 * 1024 * 1024
MAX_TEXT = 300_000
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


class ImportError_(Exception):
    """The file cannot be imported (shown to the user)."""


@dataclass
class Imported:
    kind: str                 # md | text | html | docx | pptx | pdf
    text: str = ""            # plain text for the AI
    markdown: str = ""        # conversion without AI ("" for pdf)
    pdf: bytes | None = None  # pdf files: given to Claude as a document
    title: str = ""


def _clip(text: str) -> str:
    if len(text) > MAX_TEXT:
        raise ImportError_(f"The file has more than {MAX_TEXT:,} characters of text: split it into smaller notes.")
    return text


def _zip(data: bytes) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportError_("This file is damaged or not really a Word / PowerPoint file.")
    if len(z.infolist()) > 2000 or sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
        raise ImportError_("This file is too large once unpacked.")
    return z


# ------------------------------------------------------------------ docx
def _docx(data: bytes) -> tuple[str, str]:
    z = _zip(data)
    try:
        root = ET.fromstring(z.read("word/document.xml"))
    except KeyError:
        raise ImportError_("This is not a Word document (.docx).")
    body = root.find(f"{W}body")
    md, plain = [], []

    def run_text(p) -> str:
        out = []
        for r in p.iter(f"{W}r"):
            t = "".join((x.text or "") if x.tag == f"{W}t" else ("\t" if x.tag == f"{W}tab" else "\n" if x.tag == f"{W}br" else "")
                        for x in r)
            if not t:
                continue
            rpr = r.find(f"{W}rPr")
            bold = rpr is not None and rpr.find(f"{W}b") is not None
            ital = rpr is not None and rpr.find(f"{W}i") is not None
            mono = rpr is not None and any("Courier" in (f.get(f"{W}ascii") or "") or "Consolas" in (f.get(f"{W}ascii") or "")
                                           for f in rpr.findall(f"{W}rFonts"))
            if mono and t.strip():
                t = f"`{t}`"
            elif bold and ital and t.strip():
                t = f"***{t}***"
            elif bold and t.strip():
                t = f"**{t}**"
            elif ital and t.strip():
                t = f"*{t}*"
            out.append(t)
        return "".join(out)

    code_buf: list[str] = []

    def flush_code():
        if code_buf:
            md.append('"""\n' + "\n".join(code_buf) + '\n"""')
            code_buf.clear()

    for el in list(body) if body is not None else []:
        if el.tag == f"{W}p":
            ppr = el.find(f"{W}pPr")
            style = ""
            if ppr is not None and ppr.find(f"{W}pStyle") is not None:
                style = ppr.find(f"{W}pStyle").get(f"{W}val") or ""
            raw = "".join(t.text or "" for t in el.iter(f"{W}t"))
            plain.append(raw)
            if re.match(r"(?i)(code|html\s*code|source|macro)", style):
                code_buf.append(raw)
                continue
            flush_code()
            text = run_text(el).strip()
            if not text:
                continue
            m = re.match(r"(?i)heading\s*(\d)|titre\s*(\d)", style)
            if style.lower() == "title":
                md.append("# " + raw.strip())
            elif m:
                level = min(6, int(m.group(1) or m.group(2)) + 1)   # Heading 1 -> ## (the note keeps one #)
                md.append("#" * level + " " + raw.strip())
            elif ppr is not None and ppr.find(f"{W}numPr") is not None or style.lower().startswith("list"):
                lvl = 0
                if ppr is not None and ppr.find(f"{W}numPr/{W}ilvl") is not None:
                    lvl = int(ppr.find(f"{W}numPr/{W}ilvl").get(f"{W}val") or 0)
                md.append("  " * lvl + "- " + text)
            elif style.lower() in ("quote", "intensequote"):
                md.append("> " + text)
            else:
                md.append(text)
        elif el.tag == f"{W}tbl":
            flush_code()
            rows = []
            for tr in el.iter(f"{W}tr"):
                cells = [" ".join("".join(t.text or "" for t in p.iter(f"{W}t")) for p in tc.iter(f"{W}p")).strip().replace("|", "\\|")
                         for tc in tr.iter(f"{W}tc")]
                rows.append(cells)
                plain.append("\t".join(cells))
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                md.append("\n".join(["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
                                    + ["| " + " | ".join(r) + " |" for r in rows[1:]]))
    flush_code()
    # list items belong together; other blocks are separated by a blank line
    out = []
    for block in md:
        if out and block.lstrip().startswith("- ") and out[-1].lstrip().startswith("- "):
            out[-1] += "\n" + block
        else:
            out.append(block)
    return "\n".join(plain), "\n\n".join(out)


# ------------------------------------------------------------------ pptx
def _pptx(data: bytes) -> tuple[str, str]:
    z = _zip(data)
    names = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                   key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
    if not names:
        raise ImportError_("This is not a PowerPoint file (.pptx).")
    md, plain = [], []
    for i, name in enumerate(names, 1):
        root = ET.fromstring(z.read(name))
        paras = []
        for p in root.iter(f"{A}p"):
            t = "".join(x.text or "" for x in p.iter(f"{A}t")).strip()
            if t:
                paras.append(t)
        if not paras:
            continue
        plain.append(f"--- Slide {i} ---\n" + "\n".join(paras))
        md.append(f"## {paras[0]}" + ("\n\n" + "\n".join(f"- {t}" for t in paras[1:]) if paras[1:] else ""))
    return "\n\n".join(plain), "\n\n".join(md)


# ------------------------------------------------------------------ html
class _Html(HTMLParser):
    BLOCK = {"p", "div", "section", "article", "br", "tr", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.pre = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "nav", "footer"):
            self.skip += 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n\n" + "#" * min(6, int(tag[1]) + (0 if tag == "h1" else 0)) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag == "pre":
            self.pre = True
            self.out.append('\n\n"""\n')
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag == "code" and not self.pre:
            self.out.append("`")
        elif tag in self.BLOCK:
            self.out.append("\n\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "nav", "footer"):
            self.skip = max(0, self.skip - 1)
        elif tag == "pre":
            self.pre = False
            self.out.append('\n"""\n\n')
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag == "code" and not self.pre:
            self.out.append("`")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data if self.pre else re.sub(r"\s+", " ", data))


def _html(text: str) -> tuple[str, str]:
    p = _Html()
    p.feed(text)
    md = re.sub(r"\n{3,}", "\n\n", "".join(p.out)).strip()
    plain = re.sub(r"<[^>]+>", " ", text)
    return unescape(re.sub(r"\s+", " ", plain)), md


# ------------------------------------------------------------------ entry
def _decode(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):      # UTF-16 only with its byte-order mark
        return data.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ImportError_("The text could not be read (unknown encoding).")


def read_upload(filename: str, data: bytes) -> Imported:
    if len(data) > MAX_UPLOAD:
        raise ImportError_(f"Files can be at most {MAX_UPLOAD // (1024 * 1024)} MB.")
    name = (filename or "").lower()
    title = re.sub(r"[_-]+", " ", (filename or "Imported note").rsplit("/", 1)[-1].rsplit(".", 1)[0]).strip()[:200]
    if data[:5] == b"%PDF-":
        return Imported("pdf", pdf=data, title=title)
    if data[:2] == b"PK":
        if name.endswith(".pptx"):
            text, md = _pptx(data)
            return Imported("pptx", _clip(text), md, title=title)
        text, md = _docx(data)
        return Imported("docx", _clip(text), md, title=title)
    text = _clip(_decode(data))
    if name.endswith((".html", ".htm")) or re.match(r"\s*(<!doctype html|<html)", text, re.I):
        plain, md = _html(text)
        return Imported("html", plain, md, title=title)
    if name.endswith((".md", ".markdown")):
        return Imported("md", text, text, title=title)
    return Imported("text", text, text, title=title)


def from_pasted(text: str) -> Imported:
    text = _clip(text or "")
    return Imported("text", text, text, title="")

"""Studiamo Markdown: normal Markdown plus interactive blocks, for notes,
exercises and exams. Full guide for authors: docs/studiamo-markdown.md.

    html = render(text).html        # safe HTML (raw HTML in the text is not allowed)
    toc  = render(text).toc         # [(id, title)] of the ## headings, for "On this page"

The code box (the simple way to add code):

    \"\"\"python Try it yourself       python / javascript get a Run button;
    print("hi")                        any other language is shown as plain code.
    \"\"\"                             Start it with "--- main.py" lines for several
                                       files (a small IDE). The closing \"\"\" must be
                                       alone at the start of its line.

Interactive blocks are fenced code blocks with a keyword:

    ```run python "Try it yourself"      editable code with a Run button (python, javascript)
    ```ide python "A small project"      several files, a file tree, tabs and Run
    ```files                             a file tree (folders end with /; "<- note")
    ```diagram "Shopping"                boxes and arrows the reader can drag; boxes
                                         can hold numbers the reader edits and formulas
                                         that update live
    ```note / ```tip / ```warning        a highlighted box (Markdown inside)
    ```compare "In place" | "A copy"     two code snippets side by side ("---" between)

The server only produces HTML with data in it; static/js/smd.js makes it
interactive. Everything is escaped here, so authors cannot inject scripts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import escape

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin

RUN_LANGS = {"python": "Python", "py": "Python", "javascript": "JavaScript", "js": "JavaScript"}
LANG_KEY = {"py": "python", "js": "javascript"}
EXT_LANG = {".py": "python", ".js": "javascript", ".mjs": "javascript"}
CALLOUTS = {"note": "Note", "tip": "Tip", "warning": "Warning"}
MAX_DIAGRAM_NODES = 60
_ID = r"[A-Za-z_][A-Za-z0-9_]*"
_NODE_RE = re.compile(rf"^({_ID})\s*:\s*([^=@]*?)\s*(?:=\s*([^@]+?))?\s*(?:@\s*(-?\d+)\s*,\s*(-?\d+))?\s*$")
_EDGE_RE = re.compile(rf"^({_ID})\s*->\s*({_ID})\s*(?::\s*(.+))?$")
_NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_EXPR_OK = re.compile(rf"^[\s\d.+\-*/%^(),]*(?:(?:{_ID})[\s\d.+\-*/%^(),]*)*$")


@dataclass
class Rendered:
    html: str
    toc: list[tuple[str, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)   # shown to authors in the preview


def _title(info: str) -> str:
    match = re.search(r'"([^"]*)"', info)
    return match.group(1).strip() if match else ""


# ---------------------------------------------------------------- blocks
def _run_block(lang: str, code: str, title: str) -> str:
    key = LANG_KEY.get(lang, lang)
    label = RUN_LANGS.get(lang)
    if label is None:
        return (f'<figure class="smd-code"><figcaption>{escape(title or lang)}</figcaption>'
                f'<pre><code>{escape(code)}</code></pre></figure>')
    heading = escape(title or "Try it yourself")
    return (
        f'<section class="smd-run" data-lang="{key}">'
        f'<div class="smd-bar"><span>{heading} · {label}</span>'
        f'<span class="smd-actions"><button type="button" class="smd-reset">Reset</button>'
        f'<button type="button" class="smd-play">Run »</button></span></div>'
        f'<textarea class="smd-editor" spellcheck="false" aria-label="{heading}, {label} code">{escape(code.rstrip())}</textarea>'
        f'<div class="smd-out" hidden><p class="smd-out-label">OUTPUT</p><pre aria-live="polite"></pre></div>'
        f'</section>'
    )


def _ide_block(info_lang: str, body: str, title: str, problems: list[str]) -> str:
    entry = None
    files: list[tuple[str, list[str]]] = []
    for line in body.splitlines():
        if line.startswith("--- "):
            files.append((line[4:].strip(), []))
        elif not files and line.lower().startswith("entry:"):
            entry = line.split(":", 1)[1].strip()
        elif files:
            files[-1][1].append(line)
        elif line.strip():
            problems.append("ide: start every file with a line like '--- main.py'")
    if not files:
        problems.append("ide: no files (add '--- main.py' and its code)")
        return ""
    paths = [p for p, _ in files]
    entry = entry or paths[0]
    if entry not in paths:
        problems.append(f"ide: entry {entry!r} is not one of the files")
        entry = paths[0]
    lang = LANG_KEY.get(info_lang, info_lang) or EXT_LANG.get("." + entry.rsplit(".", 1)[-1], "")
    if lang not in ("python", "javascript"):
        problems.append("ide: language must be python or javascript (e.g. ```ide python)")
        lang = "python"
    heading = escape(title or "Project")
    file_html = "".join(
        f'<textarea class="smd-file" data-path="{escape(p)}" spellcheck="false" aria-label="{escape(p)}"'
        f'{"" if p == entry else " hidden"}>{escape(chr(10).join(lines).strip(chr(10)))}</textarea>'
        for p, lines in files
    )
    tree = "".join(f'<li><button type="button" class="smd-tab" data-path="{escape(p)}"'
                   f'{" aria-current=\"true\"" if p == entry else ""}>{escape(p)}</button></li>' for p in paths)
    return (
        f'<section class="smd-ide" data-lang="{lang}" data-entry="{escape(entry)}">'
        f'<div class="smd-bar"><span>{heading} · {RUN_LANGS[lang]}</span>'
        f'<span class="smd-actions"><button type="button" class="smd-reset">Reset</button>'
        f'<button type="button" class="smd-play">Run {escape(entry)} »</button></span></div>'
        f'<div class="smd-ide-body"><nav class="smd-tree" aria-label="Files"><p class="smd-out-label">FILES</p><ul>{tree}</ul></nav>'
        f'<div class="smd-files">{file_html}</div></div>'
        f'<div class="smd-out" hidden><p class="smd-out-label">OUTPUT</p><pre aria-live="polite"></pre></div>'
        f'</section>'
    )


def _files_block(body: str, title: str) -> str:
    lines = [l.rstrip() for l in body.splitlines() if l.strip()]
    if not lines:
        return ""
    # (depth, name, comment)
    items = []
    for raw in lines:
        indent = len(raw) - len(raw.lstrip(" "))
        text = raw.strip()
        comment = ""
        for mark in ("<-", "←", "#"):
            if mark in text:
                text, comment = [x.strip() for x in text.split(mark, 1)]
                break
        items.append((indent, text, comment))
    out: list[str] = []

    def build(i: int, indent: int) -> int:
        out.append("<ul>")
        while i < len(items) and items[i][0] >= indent:
            depth, name, comment = items[i]
            note = f' <span class="smd-file-note">← {escape(comment)}</span>' if comment else ""
            folder = name.endswith("/")
            has_children = i + 1 < len(items) and items[i + 1][0] > depth
            if folder or has_children:
                out.append(f'<li><details open><summary><span class="smd-folder">{escape(name)}</span>{note}</summary>')
                i = build(i + 1, items[i + 1][0]) if has_children else i + 1
                out.append("</details></li>")
            else:
                out.append(f'<li><span class="smd-leaf">{escape(name)}</span>{note}</li>')
                i += 1
        out.append("</ul>")
        return i

    build(0, items[0][0])
    caption = f"<figcaption>{escape(title)}</figcaption>" if title else '<figcaption>FILE TREE</figcaption>'
    return f'<figure class="smd-files-tree">{caption}{"".join(out)}</figure>'


def parse_diagram(body: str) -> tuple[dict, list[str]]:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    problems: list[str] = []
    for n, raw in enumerate(body.splitlines(), 1):
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        edge = _EDGE_RE.match(line)
        if edge:
            edges.append({"from": edge.group(1), "to": edge.group(2), "label": (edge.group(3) or "").strip()[:60]})
            continue
        node = _NODE_RE.match(line)
        if not node:
            problems.append(f"diagram line {n}: write 'id: Label', 'id: Label = 5', 'id: Label = a * b' or 'a -> b'")
            continue
        node_id, label, value, x, y = node.groups()
        item = {"id": node_id, "label": (label or node_id)[:60]}
        if value is not None:
            value = value.strip()
            if _NUMBER_RE.match(value):
                item["value"] = float(value)
            elif _EXPR_OK.match(value) and len(value) <= 200:
                item["expr"] = value
            else:
                problems.append(f"diagram line {n}: {value!r} is not a number or a formula")
        if x is not None:
            item["x"], item["y"] = int(x), int(y)
        nodes[node_id] = item
    for e in edges:
        for end in (e["from"], e["to"]):
            if end not in nodes:
                nodes[end] = {"id": end, "label": end}
    for item in nodes.values():
        for ref in re.findall(_ID, item.get("expr", "")):
            if ref in nodes and ref != item["id"] and not any(e["from"] == ref and e["to"] == item["id"] for e in edges):
                edges.append({"from": ref, "to": item["id"], "label": ""})
            elif ref not in nodes and ref not in ("min", "max", "abs", "sqrt", "round", "floor", "ceil", "pow"):
                problems.append(f"diagram: {item['id']} uses {ref!r}, which is not a box")
    if len(nodes) > MAX_DIAGRAM_NODES:
        problems.append(f"diagram: at most {MAX_DIAGRAM_NODES} boxes")
    return {"nodes": list(nodes.values())[:MAX_DIAGRAM_NODES], "edges": edges}, problems


def _diagram_block(body: str, title: str, problems: list[str]) -> str:
    data, issues = parse_diagram(body)
    problems.extend(issues)
    text = "; ".join(
        [f"{n['label']}" + (f" = {n.get('value', n.get('expr'))}" if ("value" in n or "expr" in n) else "") for n in data["nodes"]]
        + [f"{e['from']} → {e['to']}" for e in data["edges"]]
    )
    heading = escape(title or "Diagram")
    return (
        f'<figure class="smd-diagram" data-diagram="{escape(json.dumps(data))}">'
        f'<figcaption><span>{heading}</span><span class="smd-hint">Drag the boxes. Change the numbers to see what follows.</span>'
        f'<button type="button" class="smd-reset">Reset</button></figcaption>'
        f'<div class="smd-canvas" role="img" aria-label="{escape(heading)}: {escape(text)}"><noscript>{escape(text)}</noscript></div>'
        f'</figure>'
    )


def _compare_block(info: str, body: str) -> str:
    titles = re.findall(r'"([^"]*)"', info) or ["Before", "After"]
    parts = re.split(r"(?m)^---\s*$", body, maxsplit=1)
    left, right = parts[0], parts[1] if len(parts) > 1 else ""
    t1, t2 = (titles + ["", ""])[:2]
    return (
        f'<div class="smd-compare"><figure><figcaption>{escape(t1)}</figcaption><pre><code>{escape(left.strip())}</code></pre></figure>'
        f'<figure><figcaption>{escape(t2)}</figcaption><pre><code>{escape(right.strip())}</code></pre></figure></div>'
    )


# ---------------------------------------------------------- code boxes
_TQ_OPEN = re.compile(r'^"""([A-Za-z][\w+#.-]*)?[ \t]*(.*)$')
_FENCE = re.compile(r"^(`{3,}|~{3,})")


def expand_code_boxes(text: str) -> str:
    """\"\"\"lang title ... \"\"\" boxes -> ```code lang "title" fences, line for line
    (so line numbers stay the same for the editor's source map)."""
    lines = text.split("\n")
    out: list[str] = []
    i, fence = 0, None
    while i < len(lines):
        line = lines[i]
        if fence:                                   # inside a normal ``` fence: copy
            out.append(line)
            if line.startswith(fence):
                fence = None
            i += 1
            continue
        m_f = _FENCE.match(line)
        if m_f:
            fence = m_f.group(1)
            out.append(line)
            i += 1
            continue
        m = _TQ_OPEN.match(line) if line.startswith('"""') else None
        end = next((j for j in range(i + 1, len(lines)) if lines[j].rstrip() == '"""'), None) if m else None
        if m and end is not None:
            body = lines[i + 1:end]
            ticks = "`" * max(3, 1 + max((len(x) for x in re.findall(r"`{3,}", "\n".join(body))), default=0))
            lang, title = (m.group(1) or "").lower(), m.group(2).strip().replace('"', "'")
            out.append(f'{ticks}code {lang}' + (f' "{title}"' if title else ""))
            out.extend(body)
            out.append(ticks)
            i = end + 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _code_box(lang: str, body: str, title: str, problems: list[str]) -> str:
    first = next((l for l in body.splitlines() if l.strip()), "")
    if first.startswith("--- ") or first.lower().startswith("entry:"):
        return _ide_block(lang, body, title, problems)
    if lang in RUN_LANGS:
        return _run_block(lang, body, title)
    label = escape(title or (lang.upper() if lang else "CODE"))
    return (f'<figure class="smd-code"><figcaption>{label}</figcaption>'
            f'<pre><code{f" class=\"language-{escape(lang)}\"" if lang else ""}>{escape(body.rstrip())}</code></pre></figure>')


# ------------------------------------------------------------- renderer
def _make_parser(problems: list[str]) -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": True})
    md.enable(["table", "strikethrough"])
    md.use(anchors_plugin, min_level=2, max_level=3, permalink=False)
    default_fence = md.renderer.rules["fence"]

    def fence(tokens, idx, options, env):
        token = tokens[idx]
        info = token.info.strip()
        words = info.split()
        kind = words[0].lower() if words else ""
        lang = words[1].lower() if len(words) > 1 and not words[1].startswith('"') else ""
        title = _title(info)
        body = token.content
        html = _fence_html(kind, lang, info, title, body, tokens, idx, options, env)
        if env.get("source_map") and token.map and token.level == 0:
            return f'<div class="smd-src" data-src="{token.map[0]},{token.map[1]}">{html}</div>'
        return html

    def _fence_html(kind, lang, info, title, body, tokens, idx, options, env):
        if kind == "code":
            return _code_box(lang, body, title, problems)
        if kind == "run":
            return _run_block(lang or "python", body, title)
        if kind == "ide":
            return _ide_block(lang, body, title, problems)
        if kind == "files":
            return _files_block(body, title)
        if kind == "diagram":
            return _diagram_block(body, title, problems)
        if kind == "compare":
            return _compare_block(info, body)
        if kind in CALLOUTS:
            inner = render(body).html
            label = escape(title or CALLOUTS[kind])
            return f'<aside class="smd-callout smd-{kind}"><p class="smd-callout-title">{label}</p>{inner}</aside>'
        return default_fence(tokens, idx, options, env)

    md.renderer.rules["fence"] = fence
    return md


def render(text: str, *, source_map: bool = False) -> Rendered:
    """source_map=True (the note editor): every top-level block carries
    data-src="first,last" source lines, so edits in the preview can be written back."""
    problems: list[str] = []
    md = _make_parser(problems)
    tokens = md.parse(expand_code_boxes(text or ""))
    if source_map:
        for tok in tokens:
            if tok.level == 0 and tok.map and (tok.nesting == 1 or tok.type in ("hr", "code_block")):
                tok.attrSet("data-src", f"{tok.map[0]},{tok.map[1]}")
    toc = []
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.tag == "h2":
            toc.append((tok.attrGet("id") or "", tokens[i + 1].content))
    html = md.renderer.render(tokens, md.options, {"source_map": source_map})
    return Rendered(html=html, toc=toc, problems=problems)


# ---------------------------------------------------------- reading time
WORDS_PER_MINUTE = 200
BLOCK_SECONDS = {"run": 60, "code": 45, "ide": 120, "diagram": 60, "files": 20, "compare": 30}


def reading_seconds(text: str) -> int:
    """Estimated time to read a note once: prose at 200 words a minute, plus time
    for each interactive block (running code, playing with a diagram). At least 1 minute."""
    text = expand_code_boxes(text or "")
    seconds, words = 0, 0
    in_fence, kind = None, ""
    for line in text.split("\n"):
        m = _FENCE.match(line)
        if in_fence:
            if line.startswith(in_fence):
                in_fence = None
            elif kind in ("note", "tip", "warning"):
                words += len(line.split())
            continue
        if m:
            in_fence = m.group(1)
            kind = (line[len(in_fence):].strip().split() or [""])[0].lower()
            seconds += BLOCK_SECONDS.get(kind, 15)
            continue
        words += len(re.sub(r"[#>*_`|\[\]()-]", " ", line).split())
    return max(60, round(seconds + words * 60 / WORDS_PER_MINUTE))

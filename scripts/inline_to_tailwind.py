"""One-off migration: turn inline style="…" attributes in the Jinja templates
into Tailwind classes.

    uv run python scripts/inline_to_tailwind.py            # rewrite in place
    uv run python scripts/inline_to_tailwind.py --dry-run  # print what would change

Rules:
- email/ templates are skipped (mail clients ignore stylesheets).
- A declaration whose value holds Jinja ({{ … }}) stays inline: Tailwind only
  knows classes it can see in the source, never values made at runtime.
- Known properties get the usual utilities (flex, items-center, text-[1.25rem],
  text-accent, mt-[10px] …); anything else becomes an arbitrary property
  [prop:value], so nothing is dropped.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src" / "templates"

# colours with a theme name (src/styles/app.css: @theme inline)
COLORS = {
    "var(--purple)": "purple",
    "var(--accent-text)": "accent",
    "var(--accent-soft)": "accent-soft",
    "var(--lilac)": "accent-soft",
    "var(--ink)": "ink",
    "var(--muted)": "muted",
    "var(--bg)": "bg",
    "var(--line)": "line",
    "var(--line-strong)": "line-strong",
    "var(--code-bg)": "code",
    "var(--danger)": "danger",
    "var(--danger-bg)": "danger-bg",
    "var(--warn)": "warn",
    "var(--warn-text)": "warn-text",
    "var(--warn-bg)": "warn-bg",
    "var(--ok)": "ok",
    "var(--ok-bg)": "ok-bg",
    "var(--badge)": "badge",
    "var(--shadow)": "shadow",
    "var(--grey)": "grey",
    "var(--strip)": "strip",
    "#fff": "white",
    "#ffffff": "white",
    "#000": "black",
    "#000000": "black",
    "transparent": "transparent",
}

KEYWORDS = {
    ("display", "grid"): "grid",
    ("display", "flex"): "flex",
    ("display", "block"): "block",
    ("display", "inline"): "inline",
    ("display", "inline-block"): "inline-block",
    ("display", "inline-flex"): "inline-flex",
    ("display", "none"): "hidden",
    ("flex-direction", "column"): "flex-col",
    ("flex-direction", "row"): "flex-row",
    ("flex-wrap", "wrap"): "flex-wrap",
    ("flex-wrap", "nowrap"): "flex-nowrap",
    ("flex", "1"): "flex-1",
    ("flex", "none"): "flex-none",
    ("align-items", "center"): "items-center",
    ("align-items", "start"): "items-start",
    ("align-items", "flex-start"): "items-start",
    ("align-items", "end"): "items-end",
    ("align-items", "flex-end"): "items-end",
    ("align-items", "stretch"): "items-stretch",
    ("align-items", "baseline"): "items-baseline",
    ("align-self", "center"): "self-center",
    ("align-self", "start"): "self-start",
    ("align-self", "flex-start"): "self-start",
    ("align-self", "end"): "self-end",
    ("align-self", "flex-end"): "self-end",
    ("align-self", "stretch"): "self-stretch",
    ("justify-content", "space-between"): "justify-between",
    ("justify-content", "center"): "justify-center",
    ("justify-content", "flex-start"): "justify-start",
    ("justify-content", "start"): "justify-start",
    ("justify-content", "flex-end"): "justify-end",
    ("justify-content", "end"): "justify-end",
    ("text-align", "center"): "text-center",
    ("text-align", "left"): "text-left",
    ("text-align", "right"): "text-right",
    ("position", "relative"): "relative",
    ("position", "absolute"): "absolute",
    ("position", "sticky"): "sticky",
    ("position", "fixed"): "fixed",
    ("position", "static"): "static",
    ("cursor", "pointer"): "cursor-pointer",
    ("text-decoration", "underline"): "underline",
    ("text-decoration", "none"): "no-underline",
    ("white-space", "nowrap"): "whitespace-nowrap",
    ("white-space", "pre-wrap"): "whitespace-pre-wrap",
    ("white-space", "pre"): "whitespace-pre",
    ("overflow", "hidden"): "overflow-hidden",
    ("overflow", "auto"): "overflow-auto",
    ("overflow-x", "auto"): "overflow-x-auto",
    ("overflow-wrap", "anywhere"): "wrap-anywhere",
    ("border-collapse", "collapse"): "border-collapse",
    ("text-transform", "uppercase"): "uppercase",
    ("list-style", "none"): "list-none",
    ("font-weight", "700"): "font-bold",
    ("font-weight", "bold"): "font-bold",
    ("font-weight", "400"): "font-normal",
    ("font-weight", "normal"): "font-normal",
    ("width", "100%"): "w-full",
    ("height", "100%"): "h-full",
    ("height", "auto"): "h-auto",
    ("width", "auto"): "w-auto",
    ("min-width", "0"): "min-w-0",
    ("max-width", "none"): "max-w-none",
}

SIMPLE = {  # property -> utility prefix taking an arbitrary value
    "font-size": "text",
    "line-height": "leading",
    "max-width": "max-w",
    "min-width": "min-w",
    "width": "w",
    "height": "h",
    "min-height": "min-h",
    "max-height": "max-h",
    "letter-spacing": "tracking",
    "top": "top",
    "left": "left",
    "right": "right",
    "bottom": "bottom",
    "z-index": "z",
    "opacity": "opacity",
    "grid-template-columns": "grid-cols",
    "stroke-width": "stroke-w",
}

SIDES = {"top": "t", "right": "r", "bottom": "b", "left": "l"}


def arb(value: str) -> str:
    """A value usable inside [...] in a class name: spaces become _."""
    return re.sub(r"\s+", "_", value.strip())


def box(prefix: str, value: str) -> list[str]:
    """padding / margin shorthand -> p-*, px-*, pt-* …"""
    parts = value.split()

    def u(side: str, v: str) -> str:
        if v == "0":
            return f"{prefix}{side}-0"
        if v == "auto":
            return f"{prefix}{side}-auto"
        return f"{prefix}{side}-[{v}]"

    if len(parts) == 1:
        return [u("", parts[0])]
    if len(parts) == 2:
        t, r = parts
        return [u("y", t), u("x", r)] if t != r else [u("", t)]
    if len(parts) == 3:
        t, r, b = parts
        return [u("t", t), u("x", r), u("b", b)]
    t, r, b, l = parts
    if t == b and r == l:
        return box(prefix, f"{t} {r}")
    return [u("t", t), u("r", r), u("b", b), u("l", l)]


def color(prefix: str, value: str) -> str:
    name = COLORS.get(value.strip())
    return f"{prefix}-{name}" if name else f"{prefix}-[{arb(value)}]"


def border(side: str, value: str) -> list[str] | None:
    m = re.fullmatch(r"(\d+(?:\.\d+)?px)\s+(solid|dashed|dotted)\s+(.+)", value.strip())
    if not m:
        return None
    width, style, col = m.groups()
    s = f"-{SIDES[side]}" if side else ""
    name = COLORS.get(col.strip())
    col_cls = f"border{s}-{name}" if name else f"border{s}-[{arb(col)}]"
    return [f"border{s}-[{width}]", f"border-{style}", col_cls]


def convert(prop: str, value: str) -> list[str]:
    prop, value = prop.strip().lower(), value.strip()
    if (prop, value) in KEYWORDS:
        return [KEYWORDS[(prop, value)]]
    if prop in ("padding", "margin"):
        return box(prop[0], value)
    for side, s in SIDES.items():
        if prop in (f"padding-{side}", f"margin-{side}"):
            p = prop[0]
            return [f"{p}{s}-0" if value == "0" else f"{p}{s}-auto" if value == "auto" else f"{p}{s}-[{value}]"]
    if prop == "gap":
        parts = value.split()
        if len(parts) == 2:
            return [f"gap-y-[{parts[0]}]", f"gap-x-[{parts[1]}]"]
        return [f"gap-[{value}]"]
    if prop == "color":
        return [color("text", value)]
    if prop in ("background", "background-color") and (value in COLORS or re.fullmatch(r"#[0-9a-fA-F]{3,8}|var\(--[\w-]+\)", value)):
        return [color("bg", value)]
    if prop == "fill":
        return [color("fill", value)]
    if prop == "stroke":
        return [color("stroke", value)]
    if prop == "accent-color":
        return [color("accent", value)]
    if prop == "border-color":
        return [color("border", value)]
    if prop == "border":
        b = border("", value)
        if b:
            return b
    for side in SIDES:
        if prop == f"border-{side}":
            b = border(side, value)
            if b:
                return b
    if prop == "box-shadow":
        return [f"shadow-[{arb(value)}]"]
    if prop == "font-family" and value.startswith("var(--font-display)"):
        return ["font-display"]
    if prop == "font-family" and value.startswith("ui-monospace"):
        return ["font-mono"]
    if prop == "flex" and re.fullmatch(r"\d+", value):
        return [f"flex-[{value}]"]
    if prop in SIMPLE:
        p = SIMPLE[prop]
        if prop == "font-size" and value.startswith("var("):
            return [f"text-(length:{value[4:-1]})"]
        return [f"{p}-[{arb(value)}]"]
    return [f"[{prop}:{arb(value)}]"]


def split_decls(style: str) -> list[str]:
    """Split on ; outside (), {{ }} and {% %}."""
    out, buf, depth, i = [], "", 0, 0
    while i < len(style):
        two = style[i : i + 2]
        if two in ("{{", "{%"):
            depth += 1; buf += two; i += 2; continue
        if two in ("}}", "%}"):
            depth -= 1; buf += two; i += 2; continue
        c = style[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == ";" and depth == 0:
            out.append(buf); buf = ""
        else:
            buf += c
        i += 1
    out.append(buf)
    return [d for d in (x.strip() for x in out) if d]


def find_tags(src: str):
    """Yield (start, end) of every start tag, skipping over Jinja inside it."""
    i = 0
    while True:
        i = src.find("<", i)
        if i < 0:
            return
        if not re.match(r"<[a-zA-Z]", src[i : i + 2]):
            i += 1; continue
        j, quote = i + 1, None
        while j < len(src):
            if src.startswith("{{", j) or src.startswith("{%", j):
                k = src.find("}}" if src[j + 1] == "{" else "%}", j)
                j = k + 2; continue
            c = src[j]
            if quote:
                if c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c == ">":
                break
            j += 1
        yield i, j + 1
        i = j + 1


STYLE_RE = re.compile(r'\sstyle="((?:[^"{]|\{\{.*?\}\}|\{%.*?%\}|\{)*)"', re.S)
CLASS_RE = re.compile(r'\sclass="((?:[^"{]|\{\{.*?\}\}|\{%.*?%\}|\{)*)"', re.S)


def rewrite_tag(tag: str) -> str:
    m = STYLE_RE.search(tag)
    if not m or tag.startswith("<svg") and False:
        return tag
    keep, classes = [], []
    for decl in split_decls(m.group(1)):
        if ":" not in decl:
            keep.append(decl); continue
        prop, value = decl.split(":", 1)
        if "{{" in decl or "{%" in decl or prop.strip().startswith("--"):
            keep.append(decl.strip()); continue
        classes += convert(prop, value)
    new = tag[: m.start()] + (f' style="{";".join(keep)}"' if keep else "") + tag[m.end() :]
    if not classes:
        return new
    added = " ".join(dict.fromkeys(classes))
    c = CLASS_RE.search(new)
    if c:
        existing = c.group(1)
        return new[: c.start()] + f' class="{existing} {added}"' + new[c.end() :]
    # put class= right after the tag name
    name_end = re.match(r"<[a-zA-Z][\w-]*", new).end()
    return new[:name_end] + f' class="{added}"' + new[name_end:]


def rewrite(src: str) -> str:
    out, last = [], 0
    for s, e in find_tags(src):
        tag = src[s:e]
        if ' style="' in tag:
            out.append(src[last:s]); out.append(rewrite_tag(tag)); last = e
    out.append(src[last:])
    return "".join(out)


def main() -> None:
    dry = "--dry-run" in sys.argv
    total = 0
    for path in sorted(ROOT.rglob("*.html")):
        if "email" in path.relative_to(ROOT).parts:
            continue
        src = path.read_text()
        new = rewrite(src)
        if new != src:
            n = src.count(' style="') - new.count(' style="')
            total += n
            print(f"{path.relative_to(ROOT)}: {n} style attributes converted")
            if not dry:
                path.write_text(new)
    print(f"total: {total}")


if __name__ == "__main__":
    main()

"""One-off migration: wrap the visible English text of the Jinja templates in
_() so Flask-Babel can translate it.

    uv run python scripts/wrap_template_text.py --dry-run   # list the strings
    uv run python scripts/wrap_template_text.py             # rewrite in place

- Text between tags:   <p>Log in to continue.</p>  ->  <p>{{ _('Log in to continue.') }}</p>
- {{ … }} inside text becomes a named placeholder:
      Welcome back, {{ g.user.name }}.  ->  {{ _('Welcome back, %(name)s.', name=g.user.name) }}
- Attributes people read: placeholder, aria-label, title, alt.
- {% with label="…", placeholder="…", hint="…", g_label='…' %} values.
- Skipped: email/ templates (translated by hand), text in <script>, <style>,
  <pre>, <code>, <textarea>, <svg>, and anything that is not words.

Strings stay as HTML (entities such as &rsquo; are kept): Flask-Babel's
new-style gettext marks the result safe and escapes only the placeholders.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src" / "templates"
SKIP_INSIDE = {"script", "style", "pre", "code", "textarea", "svg"}
ATTRS = ("placeholder", "aria-label", "title", "alt")
WITH_KEYS = ("label", "placeholder", "hint", "g_label", "title", "text")

TOKEN = re.compile(r"(\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}|<!--.*?-->|<[a-zA-Z/!][^<>]*?(?:(?:\{\{.*?\}\}|\{%.*?%\})[^<>]*?)*>)", re.S)
WORDS = re.compile(r"[A-Za-z]{2,}")


def quote(s: str) -> str:
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    return "'" + s.replace("'", "\\'") + "'"


def var_name(expr: str, used: set[str]) -> str:
    base = expr.split("|")[0].strip()
    m = re.fullmatch(r"[\w.]+", base)
    name = base.rsplit(".", 1)[-1] if m else "value"
    name = re.sub(r"\W", "_", name) or "value"
    if not name[0].isalpha():
        name = "n_" + name
    cand, i = name, 2
    while cand in used:
        cand, i = f"{name}{i}", i + 1
    used.add(cand)
    return cand


def wrap_text(segment: str) -> str | None:
    """segment: text plus {{ }} pieces (no tags, no {% %}). None = leave it."""
    core = segment.strip()
    if not core:
        return None
    plain = re.sub(r"\{\{.*?\}\}", "", core, flags=re.S)
    if not WORDS.search(re.sub(r"&\w+;", "", plain)):
        return None
    lead = segment[: len(segment) - len(segment.lstrip())]
    trail = segment[len(segment.rstrip()) :]
    used: set[str] = set()
    args: list[str] = []

    def ph(m: re.Match) -> str:
        expr = m.group(1).strip()
        name = var_name(expr, used)
        args.append(f"{name}={expr}")
        return f"%({name})s"

    pieces = re.split(r"(\{\{.*?\}\})", core, flags=re.S)  # escape % in the text, not in expressions
    msg = "".join(p if p.startswith("{{") else p.replace("%", "%%") for p in pieces)
    msg = re.sub(r"\{\{(.*?)\}\}", ph, msg, flags=re.S)
    msg = re.sub(r"\s+", " ", msg)
    call = f"_({quote(msg)}" + "".join(f", {a}" for a in args) + ")"
    return f"{lead}{{{{ {call} }}}}{trail}"


def wrap_attr(tag: str) -> str:
    def one(m: re.Match) -> str:
        name, val = m.group(1), m.group(2)
        if "{{" in val or "{%" in val or not WORDS.search(val):
            return m.group(0)
        return f' {name}="{{{{ _({quote(val.replace("%", "%%"))}) }}}}"'

    return re.sub(r'\s(' + "|".join(ATTRS) + r')="([^"]*)"', one, tag)


def wrap_with(stmt: str) -> str:
    if not re.match(r"\{%-?\s*(with|set)\b", stmt):
        return stmt

    def one(m: re.Match) -> str:
        key, q, val = m.group(1), m.group(2), m.group(3)
        if not WORDS.search(val) or val.startswith("you@") or "/" in val:
            return m.group(0)
        return f"{key}=_({q}{val.replace('%', '%%')}{q})"

    return re.sub(r"\b(" + "|".join(WITH_KEYS) + r")\s*=\s*(['\"])(.*?)\2", one, stmt)


def rewrite(src: str) -> tuple[str, list[str]]:
    parts = TOKEN.split(src)
    out, found, skip, buf = [], [], [], []

    def flush() -> None:
        if not buf:
            return
        seg = "".join(buf)
        buf.clear()
        new = None if skip else wrap_text(seg)
        if new:
            found.append(re.search(r"_\((.*)\)", new, re.S).group(1)[:140])
        out.append(new or seg)

    for i, p in enumerate(parts):
        if i % 2 == 0:  # plain text
            buf.append(p)
            continue
        if p.startswith("{{"):
            buf.append(p)  # expressions join the sentence around them
            continue
        flush()
        if p.startswith("<") and not p.startswith(("<!", "</")):
            name = re.match(r"<([a-zA-Z][\w-]*)", p).group(1).lower()
            if name in SKIP_INSIDE and not p.endswith("/>"):
                skip.append(name)
            elif not skip:
                p = wrap_attr(p)
        elif p.startswith("</"):
            name = p[2:-1].strip().lower()
            if skip and skip[-1] == name:
                skip.pop()
        elif p.startswith("{%") and not skip:
            p = wrap_with(p)
        out.append(p)
    flush()
    return "".join(out), found


def main() -> None:
    dry = "--dry-run" in sys.argv
    total = 0
    for path in sorted(ROOT.rglob("*.html")):
        if "email" in path.relative_to(ROOT).parts:
            continue
        src = path.read_text()
        new, found = rewrite(src)
        total += len(found)
        if dry:
            for f in found:
                print(f"{path.relative_to(ROOT)}: {f}")
        elif new != src:
            path.write_text(new)
    print(f"total strings: {total}", file=sys.stderr)


if __name__ == "__main__":
    main()

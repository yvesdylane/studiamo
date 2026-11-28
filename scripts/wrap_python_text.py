"""One-off migration: wrap the user-facing English strings of src/web in
gettext calls (flask_babel), so they can be translated.

    uv run python scripts/wrap_python_text.py --dry-run
    uv run python scripts/wrap_python_text.py

Targets: flash(…); error = …; msg = …; errors[…] = …; errors.update({…});
jsonify(error=…); abort(…, description=…); and the module-level message
constants named in MODULE_CONSTANTS (lazy_gettext, read when used).
f"…{x}…" becomes _("…%(x)s…", x=x).
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "src" / "web"
SKIP_FILES = {"mcp.py", "i18n.py"}  # mcp: text for AI tools, not people
MODULE_CONSTANTS = {"TERMS_REQUIRED", "EMAIL_TAKEN", "LECTURER_PENDING", "MESSAGES", "AUTO_MESSAGES"}
LOCAL_NAMES = {"error", "msg", "message", "notice"}
WORDS = re.compile(r"[A-Za-z]{2,}")


def name_for(expr: ast.expr, used: set[str]) -> str:
    if isinstance(expr, ast.Name):
        base = expr.id
    elif isinstance(expr, ast.Attribute):
        base = expr.attr
    else:
        base = "value"
    base = base.lstrip("_") or "value"
    cand, i = base, 2
    while cand in used:
        cand, i = f"{base}{i}", i + 1
    used.add(cand)
    return cand


class Rewriter(ast.NodeVisitor):
    def __init__(self, src: str) -> None:
        self.src = src
        self.edits: list[tuple[int, int, str]] = []  # (start offset, end offset, text)
        self.lines = src.splitlines(keepends=True)
        self.starts = [0]
        for ln in self.lines:
            self.starts.append(self.starts[-1] + len(ln))
        self.depth = 0
        self.found: list[str] = []

    def off(self, line: int, col: int) -> int:
        # ast columns are utf-8 byte offsets
        text = self.lines[line - 1].encode()[:col].decode()
        return self.starts[line - 1] + len(text)

    def seg(self, node: ast.AST) -> str:
        return ast.get_source_segment(self.src, node)

    # ---- turning an expression into a translated one
    def wrap(self, node: ast.expr, lazy: bool) -> None:
        fn = "_l" if lazy else "_"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if not WORDS.search(node.value) or node.value.startswith(("http", "/")):
                return
            self.found.append(node.value[:100])
            self.replace(node, f"{fn}({self.seg(node)})")
        elif isinstance(node, ast.JoinedStr):
            used: set[str] = set()
            text, args = "", []
            for v in node.values:
                if isinstance(v, ast.Constant):
                    text += v.value.replace("%", "%%")
                else:
                    name = name_for(v.value, used)
                    expr = self.seg(v.value)
                    if v.format_spec is not None or v.conversion != -1:
                        expr = f"f{self.seg(v)!r}" if False else ast.unparse(ast.JoinedStr(values=[v]))
                    args.append(f"{name}={expr}")
                    text += f"%({name})s"
            if not WORDS.search(re.sub(r"%\(\w+\)s", "", text)):
                return
            self.found.append(text[:100])
            self.replace(node, f"{fn}({text!r}" + "".join(f", {a}" for a in args) + ")")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            self.wrap(node.left, lazy); self.wrap(node.right, lazy)
        elif isinstance(node, ast.IfExp):
            self.wrap(node.body, lazy); self.wrap(node.orelse, lazy)
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Dict):
            for v in node.value.values:
                self.wrap(v, lazy)
        elif isinstance(node, ast.Dict):
            for v in node.values:
                self.wrap(v, lazy)

    def replace(self, node: ast.AST, text: str) -> None:
        s = self.off(node.lineno, node.col_offset)
        e = self.off(node.end_lineno, node.end_col_offset)
        self.edits.append((s, e, text))

    # ---- where to look
    def visit_FunctionDef(self, node):
        self.depth += 1; self.generic_visit(node); self.depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node):
        for t in node.targets:
            if self.depth == 0 and isinstance(t, ast.Name) and t.id in MODULE_CONSTANTS:
                self.wrap(node.value, lazy=True)
            elif self.depth and isinstance(t, ast.Name) and t.id in LOCAL_NAMES:
                self.wrap(node.value, lazy=False)
            elif self.depth and isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == "errors":
                self.wrap(node.value, lazy=False)
        self.generic_visit(node)

    def visit_Call(self, node):
        f = node.func
        fname = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
        if self.depth:
            if fname == "flash" and node.args:
                self.wrap(node.args[0], lazy=False)
            elif fname == "update" and isinstance(f, ast.Attribute) and getattr(f.value, "id", "") == "errors" and node.args:
                self.wrap(node.args[0], lazy=False)
            for kw in node.keywords:
                if (fname == "jsonify" and kw.arg == "error") or (fname == "abort" and kw.arg == "description"):
                    self.wrap(kw.value, lazy=False)
        self.generic_visit(node)


def process(path: Path, dry: bool) -> int:
    src = path.read_text()
    r = Rewriter(src)
    r.visit(ast.parse(src))
    if not r.edits:
        return 0
    if dry:
        for f in r.found:
            print(f"{path.name}: {f}")
        return len(r.found)
    out = src
    for s, e, text in sorted(r.edits, reverse=True):
        out = out[:s] + text + out[e:]
    need = []
    if re.search(r"(?<![\w.])_\(", out) and "gettext as _" not in out:
        need.append("gettext as _")
    if "_l(" in out and "lazy_gettext as _l" not in out:
        need.append("lazy_gettext as _l")
    if need:
        imp = f"from flask_babel import {', '.join(need)}\n"
        m = re.search(r"^from flask import .*\n", out, re.M) or re.search(r"^(?:from|import) .*\n", out, re.M)
        out = out[: m.end()] + imp + out[m.end() :]
    ast.parse(out)  # still valid Python
    path.write_text(out)
    return len(r.found)


def main() -> None:
    dry = "--dry-run" in sys.argv
    total = sum(process(p, dry) for p in sorted(WEB.glob("*.py")) if p.name not in SKIP_FILES)
    print(f"total: {total}", file=sys.stderr)


if __name__ == "__main__":
    main()

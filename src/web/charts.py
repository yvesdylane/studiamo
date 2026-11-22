"""Geometry for the skill graph (a radar of four 0-100 axes), drawn as SVG by
templates/partials/skill_graph.html."""

from __future__ import annotations

import math

from flask import Flask

AXES = [  # key, label, what 100% means (tooltip / table) - the design's order
    ("reading", "READING", "3x the estimated reading time on every note you opened"),
    ("practice", "PRACTICE", "100% on every exercise you took"),
    ("exams", "EXAMS", "100% on every exam you took"),
    ("subjects", "SUBJECTS", "every course you started fully done"),
    ("streak", "STREAK", "a 45-day streak"),
    ("time", "TIME", "100 hours of study (notes, exercises, exams)"),
    ("badges", "BADGES", "every badge collected"),
]
CX, CY, R = 230, 172, 108


def _point(i: int, fraction: float) -> tuple[float, float]:
    angle = -math.pi / 2 + i * 2 * math.pi / len(AXES)        # first axis at the top, clockwise
    return round(CX + R * fraction * math.cos(angle), 1), round(CY + R * fraction * math.sin(angle), 1)


def radar(skills: dict) -> dict:
    axes = []
    for i, (key, label, full) in enumerate(AXES):
        value = skills.get(key)
        x, y = _point(i, (value or 0) / 100)
        lx, ly = _point(i, 1.26)
        anchor = "middle" if abs(lx - CX) < 5 else ("start" if lx > CX else "end")
        axes.append({"key": key, "label": label, "full": full, "value": value, "x": x, "y": y,
                     "end": _point(i, 1), "lx": lx, "ly": ly + (4 if ly > CY else 0), "anchor": anchor})
    rings = [" ".join(f"{px},{py}" for px, py in (_point(i, f) for i in range(len(AXES)))) for f in (0.25, 0.5, 0.75, 1)]
    return {"axes": axes, "rings": rings, "shape": " ".join(f"{a['x']},{a['y']}" for a in axes),
            "cx": CX, "cy": CY, "any": any(a["value"] is not None for a in axes)}


def init_app(app: Flask) -> None:
    app.jinja_env.globals.update(radar=radar)

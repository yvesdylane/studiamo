"""Public pages: the home page."""

from __future__ import annotations

from flask import Blueprint, g, render_template

from src.course.dto.CourseDto import CourseCatalogItem
from src.course.service.CourseService import CourseService

bp = Blueprint("pages", __name__)

HOME_COURSE_LIMIT = 8


def catalog() -> list[CourseCatalogItem]:
    """The course list, read once per request (nav, home and footer share it)."""
    if "catalog" not in g:
        from src.app import get_db

        g.catalog = CourseService(get_db()).catalog(limit=HOME_COURSE_LIMIT)
    return g.catalog


@bp.get("/")
def home():
    """Guests see an example skill graph; logged-in students see their own."""
    skills = level = None
    if g.get("user") is not None and "student" in g.roles:
        from src.web import deps

        skills = deps.progress().skill_graph(g.user.id)
        try:
            level = deps.players().get_level(g.user.id)
        except Exception:
            level = None
    return render_template("home.html", courses=catalog(), skills=skills, level=level)

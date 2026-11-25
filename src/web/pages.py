"""Public pages: the home page."""

from __future__ import annotations

from flask import Blueprint, g, redirect, render_template, url_for

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
    """Logged in (any role, admins too): the player's own lobby. Guests: the welcome page."""
    if g.get("user") is not None:
        from src.web.learn import lobby_context

        return render_template("learn/lobby.html", **lobby_context(g.user.id))
    return render_template("home.html", courses=catalog())

"""Sessions, the logged-in user and CSRF protection for the HTML pages.

The session is Flask's signed cookie and holds only the user id. The user is
re-read from the database on every request, so a deleted account or changed
roles take effect at once.

    @bp.get("/dashboard")
    @login_required
    def dashboard(): ...           # g.user is a UserRead

Every POST form must include {{ csrf_field() }}.

Admins are not stored in the database. A user is an admin for the current
session only when ALL of these hold:

  1. they logged in with Google (never with a password), and
  2. the email in Google's signed id_token is verified and listed in
     ADMIN_EMAILS in .env (comma separated), and
  3. it is still the email of the account they are logged in as.

So nobody with database access can make an admin: they can edit rows, but
not Google's signature, .env, or the session cookie (signed with SECRET_KEY,
which must stay secret). Removing an email from ADMIN_EMAILS revokes it at
the next restart. Admin sessions never use "keep me logged in".

    @bp.get("/admin/lecturers")
    @admin_required
    def pending(): ...

Acting role: an admin session acts ONLY as admin. A student or lecturer who
logs in as admin gets admin rights and loses their student/lecturer rights
for that session (no exams, no exp, no lecturer tools); logging in again
without admin gives them back. g.roles holds what the session may do:

    {"admin"}                    admin session
    {"student"} / {"student", "lecturer"}   from the account's roles otherwise

    @roles_required("student")              # students only (never an admin session)
    @roles_required("lecturer")             # lecturers only
"""

from __future__ import annotations

import hmac
import os
import secrets
from collections.abc import Callable
from datetime import timedelta
from functools import wraps
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, abort, current_app, g, redirect, request, session, url_for
from flask_babel import gettext as _
from markupsafe import Markup

from src.user.dto.UserDto import UserRead
from src.user.service.UserErrors import UserNotFoundError
from src.user.service.UserService import UserService

SESSION_USER_KEY = "uid"
SESSION_ADMIN_KEY = "admin"
CSRF_KEY = "_csrf"
REMEMBER_FOR = timedelta(days=30)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def users() -> UserService:
    from src.app import get_db  # late import: src.app imports this module

    return UserService(get_db())


# ------------------------------------------------------------------ csrf
def csrf_token() -> str:
    token = session.get(CSRF_KEY)
    if token is None:
        token = session[CSRF_KEY] = secrets.token_urlsafe(32)
    return token


def csrf_field() -> Markup:
    return Markup(f'<input type="hidden" name="csrf_token" value="{csrf_token()}">')


def _check_csrf() -> None:
    if request.method not in UNSAFE_METHODS or current_app.config.get("CSRF_DISABLED"):
        return
    if request.path in current_app.config.get("CSRF_EXEMPT_PATHS", ()):
        return   # e.g. /mcp: authenticated by a Bearer key, never by the session cookie
    expected = session.get(CSRF_KEY)
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not expected or not sent or not hmac.compare_digest(expected, sent):
        abort(400, description=_("The form expired. Go back, reload the page and try again."))


# ------------------------------------------------------------- the user
def admin_emails() -> frozenset[str]:
    return current_app.config.get("ADMIN_EMAILS", frozenset())


ADMIN = "admin"


def _load_user() -> None:
    g.user = None
    g.is_admin = False
    g.roles = frozenset()
    user_id = session.get(SESSION_USER_KEY)
    if user_id is None:
        return
    try:
        g.user = users().get(int(user_id))
    except (UserNotFoundError, ValueError):
        session.clear()
        return
    admin_email = session.get(SESSION_ADMIN_KEY)
    g.is_admin = bool(admin_email) and admin_email in admin_emails() and g.user.email == admin_email
    g.roles = frozenset({ADMIN}) if g.is_admin else frozenset(r.value for r in g.user.roles)


def login_user(user: UserRead, *, remember: bool = False) -> None:
    """Start a fresh session (a new CSRF token too) for user."""
    session.clear()
    session[SESSION_USER_KEY] = user.id
    session.permanent = remember  # remember: cookie lasts REMEMBER_FOR, else until the browser closes
    g.user = user
    g.is_admin = False
    g.roles = frozenset(r.value for r in user.roles)


def grant_admin_if_listed(email: str, *, email_verified: bool) -> bool:
    """Call right after login_user, only from the Google callback, with the
    email from Google's verified id_token. True if this session is now admin."""
    email = email.strip().lower()
    if not email_verified or email not in admin_emails() or g.user is None or g.user.email != email:
        return False
    session[SESSION_ADMIN_KEY] = email
    session.permanent = False   # admin sessions end when the browser closes
    g.is_admin = True
    g.roles = frozenset({ADMIN})
    current_app.logger.warning("admin session started for %s from %s", email, request.remote_addr)
    return True


def admin_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """404 for everyone but a current admin (does not reveal that the page exists)."""

    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not g.get("is_admin"):
            abort(404)
        return view(*args, **kwargs)

    return wrapper


def roles_required(*allowed: str) -> Callable[..., Any]:
    """The session must act as one of allowed ("student", "lecturer", "admin").
    Not logged in -> log-in page; logged in without the role -> 403."""

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if g.get("user") is None:
                return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
            if not g.roles & set(allowed):
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return decorator


def logout_user() -> None:
    session.clear()
    g.user = None
    g.is_admin = False
    g.roles = frozenset()


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if g.get("user") is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapper


def safe_next(target: str | None, default: str) -> str:
    """target if it is a path on this site, default otherwise (no open redirects)."""
    if not target:
        return default
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return default
    return target


def init_app(app: Flask) -> None:
    # Flask pre-fills these keys, so setdefault would not change them
    app.config["PERMANENT_SESSION_LIFETIME"] = REMEMBER_FOR
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["ADMIN_EMAILS"] = frozenset(
        e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()
    )
    app.before_request(_load_user)
    app.before_request(_check_csrf)
    app.jinja_env.globals.update(csrf_field=csrf_field, csrf_token=csrf_token)
    app.jinja_env.globals.update(acting_as=lambda role: role in g.get("roles", frozenset()))

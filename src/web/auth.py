"""Log in, log out and sign-up.

    /register?role=student|lecturer   one page: role boxes, then a two-step form
                                      (password, or "Sign up with Google": see
                                      src/web/google.py)
"""

from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from pydantic import ValidationError

from src.user.dto.LecturerApplicationDto import LecturerApplicationCreate
from src.user.dto.UserDto import UserCreate, UserRead
from src.user.dto.UserIdentityDto import UserIdentityCreate
from src.user.service.LecturerApplicationService import LecturerApplicationService
from src.user.service.UserErrors import (
    EmailNotVerifiedError,
    EmailTakenError,
    IdentityTakenError,
    InvalidCredentialsError,
)
from src.web import google, verify
from src.web.forms import field_errors
from src.web.security import admin_emails, login_user, logout_user, safe_next, users

bp = Blueprint("auth", __name__)

CLASSES = ["HND 1", "HND 2", "Other"]
TERMS_REQUIRED = "You need to accept the terms to create an account."
EMAIL_TAKEN = "An account with this email already exists. Log in instead."
LECTURER_PENDING = "Your lecturer request was sent. Until it is approved, your account works as a student account."


def _form(*names: str) -> dict[str, str]:
    return {n: request.form.get(n, "").strip() for n in names}


def _lecturers() -> LecturerApplicationService:
    from src.app import get_db

    return LecturerApplicationService(get_db())


# ------------------------------------------------------------------ log in
@bp.route("/login", methods=["GET", "POST"])
def login():
    next_url = safe_next(request.values.get("next"), url_for("pages.home"))
    if g.user is not None:
        return redirect(next_url)

    values = {"email": request.form.get("email", "").strip()}
    error = None
    unverified = False
    if request.method == "POST":
        password = request.form.get("password", "")
        if not values["email"] or not password:
            error = "Enter your email and password."
        else:
            try:
                user = users().authenticate(values["email"], password)
            except InvalidCredentialsError:
                error = "Wrong email or password."
            except EmailNotVerifiedError:
                error = "Your email is not verified yet. Open the link we emailed you, or get a new one below."
                unverified = True
            else:
                google.clear_pending()
                login_user(user, remember=request.form.get("remember") == "on")
                flash(f"Welcome back, {user.name}." + (" Your daily tasks are on your dashboard." if "student" in [r.value for r in user.roles] else ""))
                if user.email in admin_emails():
                    flash("Admin access needs Google: log out and use 'Continue with Google'.")
                return redirect(next_url)
    status = 401 if error else 200
    return render_template(
        "auth/login.html", values=values, error=error, unverified=unverified, next=next_url
    ), status


@bp.post("/logout")
def logout():
    logout_user()
    flash("You are logged out.")
    return redirect(url_for("pages.home"))


# ---------------------------------------------------------------- sign-up
ROLES = ("student", "lecturer")
STEP_ONE_FIELDS = {"name", "email", "password"}   # the rest (and role) is step two


@bp.route("/register", methods=["GET", "POST"])
def register():
    """One page: Student / Lecturer boxes on top, then a two-step form.

    Step one: name, email, password (or "Sign up with Google"). Step two: the
    role's own fields and the terms. The steps switch in the browser; the
    server checks everything on submit. After Google, the same page shows the
    Google email in place of the email and password fields.
    """
    if g.user is not None:
        return redirect(url_for("pages.home"))
    profile = google.pending_profile()
    role = request.values.get("role")
    role = role if role in ROLES else None

    values = _form("name", "email", "class_name", "institution", "courses_taught")
    if profile is not None:
        values["email"] = profile.email          # always Google's, whatever was posted
        if request.method == "GET":
            values["name"] = profile.name[:100]

    errors: dict[str, str] = {}
    if request.method == "POST":
        if role is None:
            errors["role"] = "Choose whether you are a student or a lecturer."
        else:
            lecturer = role == "lecturer"
            account, application, errors = _validate(values, lecturer=lecturer, with_password=profile is None)
            if not errors:
                identity = UserIdentityCreate(provider=google.PROVIDER, provider_uid=profile.sub) if profile else None
                try:
                    user = _create(account, application, identity)
                except EmailTakenError:
                    errors["email"] = EMAIL_TAKEN
                except IdentityTakenError:
                    google.clear_pending()
                    flash("This Google account already has an account here. Log in with Google.")
                    return redirect(url_for("auth.login"))
                else:
                    google.clear_pending()
                    if lecturer:
                        flash(LECTURER_PENDING)
                    if profile is None:
                        return verify.after_signup(user)   # password sign-up: verify the email first
                    login_user(user)                        # Google already verified it
                    if not lecturer:
                        flash(f"Welcome to Studiamo, {user.name}. Your account is ready.")
                    return redirect(url_for("pages.home"))

    step = 2 if errors and not (STEP_ONE_FIELDS & errors.keys()) and "role" not in errors else 1
    return render_template(
        "auth/register.html", values=values, errors=errors, classes=CLASSES, role=role,
        google_profile=profile, step=step,
    ), (400 if errors else 200)


@bp.post("/register/google/cancel")
def register_google_cancel():
    google.clear_pending()
    return redirect(url_for("auth.register", role=request.form.get("role") or None))


# old addresses, kept so links and bookmarks still work
@bp.get("/register/student")
def register_student():
    return redirect(url_for("auth.register", role="student"))


@bp.get("/register/lecturer")
def register_lecturer():
    return redirect(url_for("auth.register", role="lecturer"))


def _validate(values: dict[str, str], *, lecturer: bool, with_password: bool):
    """(UserCreate, LecturerApplicationCreate | None, errors by form field)."""
    errors: dict[str, str] = {}
    account = application = None
    if not lecturer and values.get("class_name") not in CLASSES:
        errors["class_name"] = "Pick your class."
    if request.form.get("terms") != "on":
        errors["terms"] = TERMS_REQUIRED
    try:
        account = UserCreate(
            name=values["name"],
            email=values["email"],
            password=request.form.get("password", "") if with_password else None,
            class_name=None if lecturer else (values.get("class_name") or None),
            accepted_terms=True,
        )
    except ValidationError as exc:
        errors = {**field_errors(exc), **errors}
    if lecturer:
        try:
            application = LecturerApplicationCreate(
                institution=values["institution"], courses_taught=values["courses_taught"]
            )
        except ValidationError as exc:
            errors = {**field_errors(exc), **errors}
    return account, application, errors


def _create(
    account: UserCreate, application: LecturerApplicationCreate | None, identity: UserIdentityCreate | None
) -> UserRead:
    if application is not None:
        user, _ = _lecturers().register(account, application, identity)
        return user
    if identity is not None:
        return users().create_with_identity(account, identity)
    return users().create(account)

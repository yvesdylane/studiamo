"""Profiles and settings.

  /users/<id>      a player's public profile (level, rank, exp; courses for lecturers)
  /settings        your own account: profile, password, Google, lecturer request, delete
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from pydantic import ValidationError

from src.ai.providers import PROVIDERS as AI_PROVIDERS
from src.player.service.PlayerErrors import PlayerNotFoundError
from src.user.dto.LecturerApplicationDto import LecturerApplicationCreate
from src.user.dto.UserDto import UserUpdate
from src.user.service.LecturerApplicationErrors import LecturerApplicationExistsError
from src.user.service.UserErrors import (
    InvalidCredentialsError,
    LastLoginMethodError,
    PlayerNameTakenError,
    UserHasContentError,
    UserNotFoundError,
)
from src.web import deps
from src.web.auth import CLASSES
from src.web.forms import field_errors
from src.web.security import login_required, logout_user

bp = Blueprint("account", __name__)


@bp.get("/users/<int:user_id>")
@login_required
def profile(user_id: int):
    try:
        user = deps.users().get(user_id)
    except UserNotFoundError:
        abort(404)
    try:
        level = deps.players().get_level(user_id)
        position = deps.players().position_of(user_id)
    except PlayerNotFoundError:
        level = position = None
    teaching = [c for c in deps.courses().list(limit=100) if c.created_by == user_id] if "lecturer" in [r.value for r in user.roles] else []
    marks = deps.progress().exercise_marks(user_id) + deps.progress().exam_marks(user_id)
    me = user_id == g.user.id
    # players who are anonymous or hidden on the ranking keep their stats private
    private = user.ranking_visibility != "public" and not me and not g.is_admin
    from src.web.security import admin_emails

    return render_template("account/profile.html", user=user, level=level, position=position, teaching=teaching,
                           me=me, private=private, is_admin_account=user.email in admin_emails(), done_notes=len(deps.progress().completed_notes(user_id)),
                           good_marks=sum(1 for m in marks if m.best_score_pct >= 75), marks=len(marks))


TABS = ["profile", "appearance", "notifications", "ranking", "ai", "security", "lecturer", "delete"]
SECTION_TAB = {"profile": "profile", "appearance": "appearance", "ranking": "ranking", "password": "security",
               "unlink": "security", "lecturer": "lecturer", "delete": "delete", "ai_create": "ai", "ai_revoke": "ai",
               "ai_key_save": "ai", "ai_key_model": "ai", "ai_key_delete": "ai", "ai_default": "ai", "ai_refresh": "ai", "notifications": "notifications",
               "photo": "profile", "photo_remove": "profile"}


@bp.post("/settings/theme")
@login_required
def save_theme():
    """The header's light/dark switch (fetch, CSRF header)."""
    choice = request.form.get("theme")
    if choice in ("system", "light", "dark"):
        deps.users().update(g.user.id, UserUpdate(theme=choice))
    return ("", 204)


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    me = g.user
    errors: dict[str, str] = {}
    section = request.form.get("section")
    tab = SECTION_TAB.get(section or "", request.args.get("tab", "profile"))
    if tab not in TABS:
        tab = "profile"
    if request.method == "POST":
        if section == "profile":
            player = request.form.get("player_name", "").strip()
            cls = request.form.get("class_name", "")
            try:
                fields = {"name": request.form.get("name", "").strip(), "player_name": player or None}
                if cls in CLASSES:
                    fields["class_name"] = cls
                deps.users().update(me.id, UserUpdate(**fields))
                flash("Profile saved.")
                return redirect(url_for("account.settings", tab=tab))
            except ValidationError as exc:
                errors = field_errors(exc)
            except PlayerNameTakenError:
                errors["player_name"] = "Someone already uses this player name."
        elif section == "appearance":
            from src.web.appearance import BRAND, FONTS, TEXT_SIZES, clean_colour, clean_secondary

            raw = request.form.get("custom_color") if request.form.get("accent") == "custom" else request.form.get("accent")
            theme = request.form.get("theme")
            colour = clean_colour(raw)
            sec_raw = request.form.get("custom_secondary") if request.form.get("secondary") == "custom" else request.form.get("secondary")
            secondary = clean_secondary(sec_raw) if sec_raw and sec_raw != "default" else None
            font = request.form.get("font")
            size = request.form.get("text_size")
            if raw and colour is None:
                errors["accent"] = "Choose a darker colour: white text must stay readable on it (contrast 4.5:1)."
            elif sec_raw and sec_raw != "default" and secondary is None:
                errors["secondary"] = "That is not a colour."
            else:
                deps.users().update(me.id, UserUpdate(
                    accent_color=None if colour in (None, BRAND) else colour,
                    secondary_color=secondary,
                    theme=theme if theme in ("system", "light", "dark") else None,
                    font=font if font in FONTS else None,
                    text_size=size if size in TEXT_SIZES else None))
                flash("Appearance saved.")
                resp = redirect(url_for("account.settings", tab="appearance"))
                if theme in ("light", "dark"):
                    resp.set_cookie("theme", theme, max_age=31536000, samesite="Lax")
                elif theme == "system":
                    resp.delete_cookie("theme")
                return resp
        elif section == "ai_create":
            from src.user.service.ApiTokenService import ApiTokenService, TooManyKeysError

            try:
                new_key, _ = ApiTokenService(deps.db()).create(me.id, request.form.get("key_name", ""))
            except TooManyKeysError as exc:
                flash(str(exc) + ".")
                return redirect(url_for("account.settings", tab="ai"))
            # shown once, in this answer only (never stored, never in a cookie)
            return _settings_page(deps.users().get(me.id), {}, section, "ai", new_key=new_key)
        elif section in ("ai_key_save", "ai_key_model", "ai_key_delete", "ai_default", "ai_refresh"):
            from src.ai.NoteConverter import PROVIDERS, AiKeyError

            conv = deps.converter()
            provider = request.form.get("provider") or "anthropic"
            if provider not in PROVIDERS:
                abort(400)
            name = PROVIDERS[provider].name
            try:
                if section == "ai_key_delete":
                    conv.delete_key(me.id, provider)
                    flash(f"Your {name} key was removed from Studiamo.")
                elif section == "ai_key_model":
                    conv.set_model(me.id, provider, request.form.get("model", ""))
                    flash(f"{name} model saved.")
                elif section == "ai_default":
                    conv.set_default(me.id, provider)
                    flash(f"{name} is now the AI Studiamo uses by default.")
                elif section == "ai_refresh":
                    n = len(conv.refresh_models(me.id, provider))
                    flash(f"{name}: {n} model{'s' if n != 1 else ''} available to your key.")
                else:
                    conv.save_key(me.id, provider, request.form.get("api_key", ""), request.form.get("model") or None)
                    flash(f"{name} key checked with {PROVIDERS[provider].company} and saved (encrypted).")
            except AiKeyError as exc:
                if section != "ai_key_save":
                    flash(str(exc))
                    return redirect(url_for("account.settings", tab="ai"))
                errors["api_key"] = str(exc)
                return _settings_page(deps.users().get(me.id), errors, section, "ai", ai_form_provider=provider)
            return redirect(url_for("account.settings", tab="ai") + "#ai-" + provider)
        elif section == "ai_revoke":
            from src.user.service.ApiTokenService import ApiTokenNotFoundError, ApiTokenService

            try:
                ApiTokenService(deps.db()).revoke(me.id, int(request.form.get("token_id", "0")))
                flash("Key revoked: apps using it are disconnected.")
            except (ApiTokenNotFoundError, ValueError):
                flash("That key does not exist.")
            return redirect(url_for("account.settings", tab="ai"))
        elif section in ("photo", "photo_remove"):
            import secrets

            from src.web.media import media_dir, sniff

            new_name = None
            if section == "photo":
                f = request.files.get("photo")
                data = f.read(2 * 1024 * 1024 + 1) if f else b""
                ext = sniff(data[:16]) if data else None
                if not data:
                    errors["photo"] = "Choose a picture."
                elif len(data) > 2 * 1024 * 1024:
                    errors["photo"] = "The picture can be at most 2 MB."
                elif ext is None:
                    errors["photo"] = "Use a PNG, JPEG, GIF or WebP picture."
                if errors:
                    return _settings_page(deps.users().get(me.id), errors, section, "profile")
                new_name = f"av_{secrets.token_urlsafe(12)}.{ext}"
                (media_dir() / new_name).write_bytes(data)
            previous = deps.users().set_avatar(me.id, new_name)
            if previous and previous.startswith("av_"):
                (media_dir() / previous).unlink(missing_ok=True)
            flash("Profile photo updated." if new_name else "Profile photo removed.")
            return redirect(url_for("account.settings", tab="profile"))
        elif section == "notifications":
            try:
                secs = int(request.form.get("toast_seconds") or 10)
            except ValueError:
                secs = 10
            deps.users().update(me.id, UserUpdate(toast_enabled=request.form.get("toast_enabled") == "on",
                                                  toast_seconds=max(3, min(60, secs))))
            flash("Notification settings saved.")
            return redirect(url_for("account.settings", tab="notifications"))
        elif section == "ranking":
            choice = request.form.get("ranking_visibility")
            if choice in ("public", "anonymous", "hidden"):
                deps.users().update(me.id, UserUpdate(ranking_visibility=choice))
                flash({"public": "Your name is shown on the ranking.",
                       "anonymous": "You now appear on the ranking as “Anonymous player”.",
                       "hidden": "You are no longer on the ranking."}[choice])
            return redirect(url_for("account.settings", tab="ranking"))
        elif section == "password":
            new, again = request.form.get("new_password", ""), request.form.get("new_password2", "")
            if new != again:
                errors["new_password2"] = "The two new passwords are different."
            elif len(new) < 8:
                errors["new_password"] = "Use at least 8 characters."
            else:
                try:
                    deps.users().change_password(me.id, request.form.get("current_password") or None, new)
                    flash("Password changed.")
                    return redirect(url_for("account.settings", tab=tab))
                except InvalidCredentialsError:
                    errors["current_password"] = "That is not your current password."
        elif section == "unlink":
            try:
                deps.users().unlink_identity(me.id, int(request.form.get("identity_id", "0")))
                flash("Google is no longer linked.")
            except LastLoginMethodError:
                flash("Set a password first: otherwise you could not log in any more.")
            return redirect(url_for("account.settings", tab=tab))
        elif section == "lecturer":
            try:
                deps.lecturer_apps().apply(me.id, LecturerApplicationCreate(
                    institution=request.form.get("institution", ""), courses_taught=request.form.get("courses_taught", "")))
                flash("Request sent. An admin will check it.")
                return redirect(url_for("account.settings", tab=tab))
            except ValidationError as exc:
                errors = field_errors(exc)
            except LecturerApplicationExistsError:
                flash("You already sent a request.")
        elif section == "delete":
            if request.form.get("confirm_email", "").strip().lower() != me.email:
                errors["confirm_email"] = "Type your email exactly to confirm."
            else:
                try:
                    deps.users().delete(me.id)
                except UserHasContentError:
                    flash("You wrote courses, questions or exams. Ask an admin to move them before deleting your account.")
                    return redirect(url_for("account.settings", tab=tab))
                logout_user()
                flash("Your account was deleted.")
                return redirect(url_for("pages.home"))
    return _settings_page(deps.users().get(me.id), errors, section, tab)


def _settings_page(me, errors, section, tab, new_key: str | None = None, ai_form_provider: str | None = None):
    from src.user.service.ApiTokenService import ApiTokenService

    return render_template(
        "account/settings.html", me=me, errors=errors, section=section, tab=tab, classes=CLASSES,
        has_password=deps.users().has_password(me.id), identities=deps.users().list_identities(me.id),
        application=deps.lecturer_apps().for_user(me.id), keys=ApiTokenService(deps.db()).list(me.id),
        new_key=new_key, mcp_url=url_for("mcp.endpoint", _external=True),
        ai=deps.converter().settings(me.id), ai_providers=AI_PROVIDERS, ai_form_provider=ai_form_provider,
    ), (400 if errors else 200)

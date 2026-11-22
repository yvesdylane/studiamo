"""Sign in / sign up with Google (OpenID Connect, through Authlib).

Turned on when GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set. The redirect
URI is the site's own address + /auth/google/callback, so it follows HOST/PORT
in .env (e.g. http://localhost:3000/auth/google/callback). That exact URI must
be one of the client's "Authorised redirect URIs" in the Google Cloud console.
GOOGLE_CALLBACK_URL, if set, overrides it (useful behind a proxy).

The flow:

  log in page      "Continue with Google"  -> /auth/google?intent=login
  sign-up pages    "Sign up with Google"   -> /auth/google?intent=register&role=student|lecturer
  /auth/google/callback
      known Google account, or a verified email that already has an account
          -> logged in (the Google identity is linked to that account)
      new person
          -> their Google name/email are parked in the session for 15 minutes;
             they finish on /register (role preselected if they picked one
             before going to Google; otherwise they pick it there).
             Nobody gets an account before choosing a role.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Protocol

from flask import Blueprint, Flask, abort, current_app, flash, redirect, request, session, url_for
from werkzeug.wrappers import Response

from src.user.service.UserErrors import EmailTakenError
from src.web.security import grant_admin_if_listed, login_user, safe_next, users

bp = Blueprint("google", __name__)

PROVIDER = "google"
ROLES = ("student", "lecturer")
FLOW_KEY = "google_flow"          # intent / role / next, set before leaving for Google
PENDING_KEY = "google_signup"     # the Google profile of someone who has no account yet
PENDING_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class GoogleProfile:
    sub: str               # Google's stable id for the person
    email: str
    email_verified: bool
    name: str


class GoogleAuthError(Exception):
    """Google refused, the user cancelled, or the response could not be verified."""


class GoogleClient(Protocol):
    def authorize_redirect(self, redirect_uri: str) -> Response: ...
    def fetch_profile(self) -> GoogleProfile: ...


class AuthlibGoogleClient:
    """The real client. Authlib keeps state and nonce in the session and checks
    the id_token's signature, issuer, audience, expiry and nonce."""

    def __init__(self, app: Flask, client_id: str, client_secret: str) -> None:
        from authlib.integrations.flask_client import OAuth

        oauth = OAuth(app)
        self._google = oauth.register(
            name=PROVIDER,
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )

    def authorize_redirect(self, redirect_uri: str) -> Response:
        return self._google.authorize_redirect(redirect_uri, prompt="select_account")

    def fetch_profile(self) -> GoogleProfile:
        from authlib.integrations.base_client.errors import OAuthError

        try:
            token = self._google.authorize_access_token()
        except OAuthError as exc:
            raise GoogleAuthError(exc.description or exc.error) from exc
        info = token.get("userinfo") or {}
        if not info.get("sub") or not info.get("email"):
            raise GoogleAuthError("Google did not send an email address")
        return GoogleProfile(
            sub=str(info["sub"]),
            email=str(info["email"]),
            email_verified=bool(info.get("email_verified")),
            name=str(info.get("name") or info["email"].split("@")[0]),
        )


# ------------------------------------------------------------------ helpers
def client() -> GoogleClient | None:
    return current_app.extensions.get("google")


def enabled() -> bool:
    return client() is not None


def pending_profile() -> GoogleProfile | None:
    """The parked Google profile of a person who has not finished signing up."""
    data = session.get(PENDING_KEY)
    if not data:
        return None
    if data.get("exp", 0) < time.time():
        session.pop(PENDING_KEY, None)
        return None
    return GoogleProfile(sub=data["sub"], email=data["email"], email_verified=True, name=data["name"])


def clear_pending() -> None:
    session.pop(PENDING_KEY, None)


def _park(profile: GoogleProfile) -> None:
    session[PENDING_KEY] = {
        "sub": profile.sub,
        "email": profile.email,
        "name": profile.name,
        "exp": time.time() + PENDING_TTL_SECONDS,
    }


# ------------------------------------------------------------------- routes
@bp.get("/auth/google")
def start():
    google = client()
    if google is None:
        abort(404)
    intent = request.args.get("intent", "login")
    role = request.args.get("role")
    session[FLOW_KEY] = {
        "intent": "register" if intent == "register" else "login",
        "role": role if role in ROLES else None,
        "next": safe_next(request.args.get("next"), url_for("pages.home")),
    }
    callback_url = current_app.config.get("GOOGLE_CALLBACK_URL") or url_for("google.callback", _external=True)
    return google.authorize_redirect(callback_url)


@bp.get("/auth/google/callback")
def callback():
    google = client()
    if google is None:
        abort(404)
    flow = session.pop(FLOW_KEY, None) or {"intent": "login", "role": None, "next": url_for("pages.home")}
    back = url_for("auth.login") if flow["intent"] == "login" else url_for("auth.register")

    if request.args.get("error"):
        flash("Google sign-in was cancelled.")
        return redirect(back)
    try:
        profile = google.fetch_profile()
    except GoogleAuthError:
        current_app.logger.warning("Google sign-in failed", exc_info=True)
        flash("Google sign-in did not work. Please try again.")
        return redirect(back)
    if not profile.email_verified:
        flash("Your Google account's email is not verified, so it cannot be used here.")
        return redirect(back)

    try:
        user = users().find_for_provider_login(
            PROVIDER, profile.sub, email=profile.email, email_verified=profile.email_verified
        )
    except EmailTakenError:  # only reachable with an unverified email, refused above
        flash("An account with this email already exists. Log in with your password.")
        return redirect(url_for("auth.login"))

    if user is not None:
        clear_pending()
        login_user(user)
        if grant_admin_if_listed(profile.email, email_verified=profile.email_verified):
            flash("You are logged in as an admin.")
            return redirect(flow["next"])
        if flow["intent"] == "register":
            flash(f"You already have an account with {profile.email}, so you are now logged in.")
        else:
            flash(f"Welcome back, {user.name}.")
        return redirect(flow["next"])

    _park(profile)
    if flow["role"] is None:
        flash(f"No account uses {profile.email} yet. Choose student or lecturer to create one.")
    return redirect(url_for("auth.register", role=flow["role"]))


def init_app(app: Flask) -> None:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    # Normally unset: the callback is built from the address the site is served
    # on (http://localhost:<PORT>/auth/google/callback), so it follows PORT. Set
    # GOOGLE_CALLBACK_URL only to force one, e.g. behind a proxy in production.
    app.config["GOOGLE_CALLBACK_URL"] = os.getenv("GOOGLE_CALLBACK_URL") or None
    if client_id and client_secret:
        app.extensions["google"] = AuthlibGoogleClient(app, client_id, client_secret)
    else:
        app.logger.info("Google sign-in is off: set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to turn it on")
    app.jinja_env.globals.update(google_enabled=enabled, google_pending=pending_profile)
    app.register_blueprint(bp)

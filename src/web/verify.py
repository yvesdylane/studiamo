"""Email verification after a password sign-up.

    sign-up      -> no log-in; a link is emailed; /verify/sent says "check your inbox"
    /verify/<t>  -> link opened: email marked verified, user logged in
    log in       -> refused while unverified, with a "send the link again" button
    /verify/resend (POST) -> sends a new link (at most one a minute, same answer
                             whether or not the email has an account)

The link is a token signed with SECRET_KEY (itsdangerous) holding the user id
and the email, valid VERIFY_MAX_AGE. Nothing is stored for it: a changed email,
or a deleted and re-created account, makes old links useless.
"""

from __future__ import annotations

from flask import Blueprint, Flask, current_app, flash, g, redirect, render_template, request, session, url_for
from flask_babel import gettext as _
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from src.mail import Mailer as mail
from src.user.dto.UserDto import UserRead
from src.user.service.UserErrors import VerificationLinkError
from src.web.security import login_user, users

bp = Blueprint("verify", __name__)

VERIFY_MAX_AGE = 24 * 60 * 60   # seconds a link stays valid
SALT = "studiamo-verify-email"
PENDING_EMAIL_KEY = "verify_email"   # shown on /verify/sent; not a log-in


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=SALT)


def make_token(user: UserRead) -> str:
    return _serializer().dumps({"uid": user.id, "email": user.email})


def send_link(user: UserRead) -> bool:
    """Email a verification link to user. False if sending failed (logged)."""
    link = url_for("verify.confirm", token=make_token(user), _external=True)
    hours = VERIFY_MAX_AGE // 3600
    email = mail.Email(
        to=user.email,
        subject=_("Confirm your email for Studiamo"),
        text=_(
            "Hello %(name)s,\n\n"
            "Confirm your email address to finish creating your Studiamo account:\n\n%(link)s\n\n"
            "The link works for %(hours)s hours. If you did not sign up, ignore this email: "
            "the account stays locked and is removed when the real owner signs up.\n",
            name=user.name, link=link, hours=hours,
        ),
        html=render_template("email/verify.html", user=user, link=link, hours=hours),
    )
    try:
        current_app.extensions["mailer"].send(email)
    except mail.MailError:
        current_app.logger.exception("could not send the verification email to %s", user.email)
        return False
    return True


def after_signup(user: UserRead):
    """Response for a fresh password sign-up: email the link, show 'check your inbox'."""
    users().claim_verification_send(user.email)   # starts the resend cooldown
    sent = send_link(user)
    session[PENDING_EMAIL_KEY] = user.email
    if not sent:
        flash(_("We could not send the email just now. Use the button below to try again."))
    return redirect(url_for("verify.sent"))


@bp.get("/verify/sent")
def sent():
    return render_template("auth/verify.html", state="sent", email=session.get(PENDING_EMAIL_KEY))


@bp.get("/verify/<token>")
def confirm(token: str):
    try:
        data = _serializer().loads(token, max_age=VERIFY_MAX_AGE)
        user = users().mark_email_verified(int(data["uid"]), str(data["email"]))
    except SignatureExpired:
        return render_template("auth/verify.html", state="expired", email=None), 400
    except (BadSignature, VerificationLinkError, KeyError, TypeError, ValueError):
        return render_template("auth/verify.html", state="invalid", email=None), 400
    session.pop(PENDING_EMAIL_KEY, None)
    if g.user is None or g.user.id != user.id:
        login_user(user)
    flash(_("Your email is verified. Welcome to Studiamo!"))
    return redirect(url_for("pages.home"))


@bp.post("/verify/resend")
def resend():
    email = (request.form.get("email") or session.get(PENDING_EMAIL_KEY) or "").strip()
    if email:
        user = users().claim_verification_send(email)
        if user is not None:
            send_link(user)
        session[PENDING_EMAIL_KEY] = email
    # same answer either way, so this does not reveal which emails have accounts
    flash(_("If that email has an account waiting for verification, a new link is on its way. "
          "You can ask again after a minute."))
    return redirect(url_for("verify.sent"))


def init_app(app: Flask) -> None:
    app.extensions.setdefault("mailer", mail.from_env())
    app.register_blueprint(bp)

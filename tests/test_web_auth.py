"""Drives the home, log-in and sign-up pages (password and Google) through
Flask's test client, against the real database in .env.

    uv run python -m tests.test_web_auth

Google is replaced by FakeGoogle, so no Google account or credentials are
needed. Every account it creates has an email ending in @studiamo-test.example
and is deleted at the end, even if a check fails.
"""

from __future__ import annotations

import re
import sys
import uuid

import sqlalchemy as sa
from flask import redirect

from src.app import create_app
from src.course.dto.CourseDto import CourseCreate
from src.course.service.CourseService import CourseService
from src.user.models.LecturerApplication import LecturerApplicationStatus
from src.user.models.User import UserRole
from src.user.service.LecturerApplicationErrors import LecturerApplicationReviewedError
from src.user.service.LecturerApplicationService import LecturerApplicationService
from src.user.service.UserService import UserService
from src.web.google import GoogleAuthError, GoogleProfile
from src.web.security import admin_required, roles_required
from tests.harness import Checks

DOMAIN = "studiamo-test.example"
TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def token(client, path: str = "/register") -> str:
    match = TOKEN_RE.search(client.get(path).get_data(as_text=True))
    assert match, f"no csrf token on {path}"
    return match.group(1)


def text(client, path: str) -> str:
    return client.get(path).get_data(as_text=True)


class FakeGoogle:
    """Stands in for Google: 'going to Google' jumps straight to the callback,
    which returns the profile the test set."""

    def __init__(self) -> None:
        self.profile: GoogleProfile | None = None

    def authorize_redirect(self, redirect_uri: str):
        return redirect(redirect_uri + "?code=fake&state=fake")

    def fetch_profile(self) -> GoogleProfile:
        if self.profile is None:
            raise GoogleAuthError("no profile set")
        return self.profile


def via_google(client, fake: FakeGoogle, profile: GoogleProfile, query: str = "intent=login"):
    """Click a Google button; returns the callback's response."""
    fake.profile = profile
    start = client.get(f"/auth/google?{query}")
    assert start.status_code == 302, start.status_code
    return client.get(start.headers["Location"])


class FakeMailer:
    """Keeps the emails instead of sending them."""

    def __init__(self) -> None:
        self.sent: list = []

    def send(self, email) -> None:
        self.sent.append(email)

    def to(self, address: str) -> list:
        return [m for m in self.sent if m.to == address.lower()]


LINK_RE = re.compile(r"http://localhost(/verify/[A-Za-z0-9_.\-]+)")


def open_link(client, mailer: FakeMailer, address: str):
    """Open the verification link of the latest email sent to address."""
    mails = mailer.to(address)
    assert mails, f"no email to {address}"
    path = LINK_RE.search(mails[-1].text).group(1)
    return client.get(path)


def users_password_fails(users, email: str, password: str) -> bool:
    from src.user.service.UserErrors import InvalidCredentialsError

    try:
        users.authenticate(email, password)
    except InvalidCredentialsError:
        return True
    return False


def logout(client) -> None:
    client.post("/logout", data={"csrf_token": token(client, "/")})


def main() -> int:
    app = create_app()
    mailer = FakeMailer()
    app.extensions["mailer"] = mailer

    @app.get("/_admin_probe")   # an admin-only page, for the admin checks
    @admin_required
    def _admin_probe():
        return "admin ok"

    @app.get("/_student_probe")  # a student-only page
    @roles_required("student")
    def _student_probe():
        return "student ok"

    db = app.extensions["db"]
    users, apps, courses = UserService(db), LecturerApplicationService(db), CourseService(db)
    t = Checks()
    run = uuid.uuid4().hex[:6]
    student_email = f"student-{run}@{DOMAIN}"
    lecturer_email = f"lecturer-{run}@{DOMAIN}"
    password = "correct-horse-9"
    course_ids: list[int] = []
    try:
        c = app.test_client()

        t.section("pages render")
        for path in ("/", "/login", "/register"):
            r = c.get(path)
            t.check(f"GET {path} -> 200", r.status_code == 200, r.status_code)
        home = text(c, "/")
        t.check("home links to log in and sign up", 'href="/login"' in home and 'href="/register"' in home)
        r = c.get("/register/lecturer")
        t.check("old /register/lecturer -> /register?role=lecturer", r.status_code == 302 and r.headers["Location"] == "/register?role=lecturer", r.headers.get("Location"))

        t.section("sign-up page: role boxes, then two steps on one page")
        page = text(c, "/register")
        t.check("two role boxes at the top", 'id="role-student"' in page and 'id="role-lecturer"' in page)
        t.check("no role checked until the user picks", "checked" not in page.split("</fieldset>")[0])
        t.check("both steps in the same page", 'data-step="1"' in page and 'data-step="2"' in page and 'id="step-next"' in page)
        t.check("?role=lecturer pre-selects lecturer", 'value="lecturer" checked' in text(c, "/register?role=lecturer"))
        real_google = app.extensions.pop("google", None)   # .env may turn it on: test "off" first
        page_off = text(c, "/register")
        t.check("google off: no button", "WITH GOOGLE" not in page_off and "WITH GOOGLE" not in text(c, "/login"))
        t.check("google off: /auth/google -> 404", c.get("/auth/google").status_code == 404)
        t.check("google on in .env -> real client built" if real_google else "google not configured in .env", True)

        t.section("csrf")
        t.check("POST without csrf token -> 400", c.post("/login", data={"email": "a", "password": "b"}).status_code == 400)
        t.check("POST with a wrong token -> 400", c.post("/login", data={"email": "a", "password": "b", "csrf_token": "x"}).status_code == 400)

        t.section("student sign-up")
        tok = token(c)
        r = c.post("/register", data={"csrf_token": tok, "name": "Amina Test", "email": student_email, "password": password, "terms": "on"})
        t.check("no role -> 400 'choose'", r.status_code == 400 and "Choose whether you are a student or a lecturer." in r.get_data(as_text=True))
        r = c.post("/register", data={"csrf_token": tok, "role": "student", "name": "", "email": "not-an-email",
                                      "class_name": "HND 9", "password": "short"})
        body = r.get_data(as_text=True)
        t.check("invalid form -> 400", r.status_code == 400, r.status_code)
        for msg in ("This field is required.", "Enter a valid email address.", "Pick your class.", "Use at least 8 characters.", "accept the terms"):
            t.check(f"error shown: {msg}", msg in body)
        t.check("step-one error -> page opens on step 1", 'data-step="1" novalidate' in body or 'data-step="1"' in body.split('id="signup"')[1][:200])
        t.check("email kept, password never echoed", 'value="not-an-email"' in body and 'value="short"' not in body)
        r = c.post("/register", data={"csrf_token": tok, "role": "student", "name": "Amina Test", "email": student_email,
                                      "class_name": "", "password": password})
        t.check("only step-two errors -> page opens on step 2", 'data-step="2"' in r.get_data(as_text=True).split('id="signup"')[1][:200])
        t.check("nothing saved", users.get_by_email(student_email) is None)

        r = c.post("/register", data={"csrf_token": tok, "role": "student", "name": "Amina Test", "email": student_email.upper(),
                                      "class_name": "HND 1", "password": password, "terms": "on"})
        t.check("valid sign-up -> 'check your inbox'", r.status_code == 302 and r.headers["Location"] == "/verify/sent", (r.status_code, r.headers.get("Location")))
        amina = users.get_by_email(student_email)
        t.check("saved: email lower-cased, class, terms time, student role",
                amina is not None and amina.email == student_email and amina.class_name == "HND 1"
                and amina.terms_accepted_at is not None and amina.roles == [UserRole.STUDENT])
        t.section("email verification")
        t.check("not logged in before verifying", "Log out</button>" not in text(c, "/"))
        t.check("inbox page names the email", student_email in text(c, "/verify/sent"))
        mails = mailer.to(student_email)
        t.check("one verification email sent", len(mails) == 1 and "/verify/" in mails[0].text and "/verify/" in mails[0].html, len(mails))
        t.check("account saved as not verified", not amina.email_verified)
        r = c.post("/login", data={"csrf_token": token(c, "/login"), "email": student_email, "password": password})
        body = r.get_data(as_text=True)
        t.check("log in refused until verified, with a resend button", r.status_code == 401 and "not verified" in body and "SEND THE LINK AGAIN" in body)
        r = c.post("/verify/resend", data={"csrf_token": token(c, "/verify/sent"), "email": student_email})
        t.check("resend within a minute: same answer, no new email", r.status_code == 302 and len(mailer.to(student_email)) == 1)
        r = c.post("/verify/resend", data={"csrf_token": token(c, "/verify/sent"), "email": f"nobody-{run}@{DOMAIN}"})
        t.check("resend for an unknown email: same answer, nothing sent", r.status_code == 302 and not mailer.to(f"nobody-{run}@{DOMAIN}"))
        t.check("tampered link -> refused", c.get("/verify/abc.def.ghi").status_code == 400)
        r = open_link(c, mailer, student_email)
        t.check("link -> verified and logged in", r.status_code == 302 and users.get(amina.id).email_verified)
        home = text(c, "/")
        t.check("logged in (initials + log out) and welcomed", "AT <span" in home and "Log out</button>" in home and "email is verified" in home)
        t.check("link works only for its account (opening it again is harmless)", open_link(c, mailer, student_email).status_code == 302)
        t.check("sign-up page redirects when logged in", c.get("/register").status_code == 302)
        logout(c)
        t.check("logged out", "Log out</button>" not in text(c, "/"))

        r = c.post("/register", data={"csrf_token": token(c), "role": "student", "name": "Again", "email": student_email,
                                      "class_name": "HND 2", "password": password, "terms": "on"})
        t.check("same email again -> 400 'already exists'", r.status_code == 400 and "already exists" in r.get_data(as_text=True))

        t.section("lecturer sign-up")
        c4 = app.test_client()
        tok = token(c4)
        r = c4.post("/register", data={"csrf_token": tok, "role": "lecturer", "name": "Dr. Test", "email": lecturer_email,
                                       "institution": "", "courses_taught": "", "password": password, "terms": "on"})
        t.check("missing institution/courses -> 400", r.status_code == 400 and r.get_data(as_text=True).count("This field is required.") >= 2)
        r = c4.post("/register", data={"csrf_token": tok, "role": "lecturer", "name": "Dr. Test", "email": lecturer_email,
                                       "institution": "  IUT Test  ", "courses_taught": "Factual Programming, HCI",
                                       "class_name": "HND 1", "password": password, "terms": "on"})
        t.check("valid -> 'check your inbox'", r.status_code == 302 and r.headers["Location"] == "/verify/sent", r.headers.get("Location"))
        t.check("pending notice shown", "works as a student account" in text(c4, "/verify/sent"))
        open_link(c4, mailer, lecturer_email)
        lecturer = users.get_by_email(lecturer_email)
        application = apps.for_user(lecturer.id) if lecturer else None
        t.check("student role only, no class (student field ignored)", lecturer is not None and lecturer.roles == [UserRole.STUDENT] and lecturer.class_name is None)
        t.check("pending application, institution trimmed", application is not None and application.status == LecturerApplicationStatus.PENDING and application.institution == "IUT Test")
        t.check("lecturer verified and logged in", lecturer.email_verified and "Log out</button>" in text(c4, "/"))
        approved = apps.approve(application.id, reviewer_id=amina.id)
        t.check("approve -> lecturer role added", approved.status == LecturerApplicationStatus.APPROVED and users.get(lecturer.id).roles == [UserRole.STUDENT, UserRole.LECTURER])
        t.expect_error("cannot review twice", LecturerApplicationReviewedError, lambda: apps.reject(application.id, amina.id))

        t.section("log in")
        tok = token(c, "/login")
        r = c.post("/login", data={"csrf_token": tok, "email": student_email, "password": "wrong-password"})
        t.check("wrong password -> 401, generic message", r.status_code == 401 and "Wrong email or password." in r.get_data(as_text=True))
        r = c.post("/login", data={"csrf_token": tok, "email": f"nobody-{run}@{DOMAIN}", "password": password})
        t.check("unknown email -> same 401", r.status_code == 401 and "Wrong email or password." in r.get_data(as_text=True))
        r = c.post("/login", data={"csrf_token": tok, "email": student_email, "password": password, "next": "https://evil.example/x"})
        cookie = r.headers.get("Set-Cookie", "")
        t.check("off-site next ignored", r.status_code == 302 and r.headers["Location"] == "/", r.headers.get("Location"))
        t.check("browser-session cookie, HttpOnly, SameSite=Lax", "Expires=" not in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie, cookie)
        logout(c)
        c2 = app.test_client()
        r = c2.post("/login", data={"csrf_token": token(c2, "/login"), "email": student_email, "password": password,
                                    "remember": "on", "next": "/register?role=lecturer"})
        t.check("local next followed", r.headers.get("Location") == "/register?role=lecturer", r.headers.get("Location"))
        t.check("remember me -> cookie has Expires", "Expires=" in r.headers.get("Set-Cookie", ""))

        t.section("deleted account is logged out")
        other = f"gone-{run}@{DOMAIN}"
        c3 = app.test_client()
        c3.post("/register", data={"csrf_token": token(c3), "role": "student", "name": "Gone Soon", "email": other,
                                   "class_name": "Other", "password": password, "terms": "on"})
        open_link(c3, mailer, other)
        t.check("(logged in after verifying)", "Log out</button>" in text(c3, "/"))
        users.delete(users.get_by_email(other).id)
        t.check("next request shows logged out", "Log out</button>" not in text(c3, "/"))
        r = c3.get("/_student_probe")
        t.check("role page when logged out -> log in", r.status_code == 302 and r.headers["Location"].startswith("/login"))

        t.section("someone signs up with an email that is not theirs")
        victim = f"victim-{run}@{DOMAIN}"
        sq = app.test_client()
        sq.post("/register", data={"csrf_token": token(sq), "role": "student", "name": "Squatter", "email": victim,
                                   "class_name": "Other", "password": "squatter-pass-1", "terms": "on"})
        squat = users.get_by_email(victim)
        owner = app.test_client()
        r = owner.post("/register", data={"csrf_token": token(owner), "role": "student", "name": "Real Owner", "email": victim,
                                          "class_name": "HND 1", "password": "owner-pass-12", "terms": "on"})
        mine = users.get_by_email(victim)
        t.check("real owner can still sign up (unverified account replaced)", r.headers.get("Location") == "/verify/sent"
                and mine is not None and mine.id != squat.id and mine.name == "Real Owner")
        old = owner.get(LINK_RE.search(mailer.to(victim)[0].text).group(1))
        t.check("the first account's link no longer works", old.status_code == 400 and not users.get(mine.id).email_verified)
        open_link(owner, mailer, victim)
        t.check("owner verifies with their own link", users.get(mine.id).email_verified)
        r = sq.post("/login", data={"csrf_token": token(sq, "/login"), "email": victim, "password": "squatter-pass-1"})
        t.check("squatter's password does not work", r.status_code == 401 and "Wrong email or password." in r.get_data(as_text=True))

        # ----------------------------------------------------------- google
        fake = FakeGoogle()
        app.extensions["google"] = fake
        g_email = f"google-{run}@{DOMAIN}"
        gina_g = GoogleProfile(sub=f"g-{run}-1", email=g_email, email_verified=True, name="Gina Google")

        t.section("google: buttons")
        g = app.test_client()
        t.check("log in page: 'Continue with Google'", "CONTINUE WITH GOOGLE" in text(g, "/login"))
        page = text(g, "/register?role=student")
        t.check("sign-up page: 'Sign up with Google' carrying the chosen role", "SIGN UP WITH GOOGLE" in page and "intent=register&amp;role=student" in page)

        t.section("google: log in with no account -> pick a role on the sign-up page")
        r = via_google(g, fake, gina_g)
        t.check("redirected to /register", r.status_code == 302 and r.headers["Location"] == "/register", r.headers.get("Location"))
        t.check("no account created before a role is chosen", users.get_by_email(g_email) is None)
        page = text(g, "/register")
        t.check("page shows the Google email, no email/password fields", g_email in page and 'name="password"' not in page and 'name="email"' not in page)
        t.check("name prefilled from Google", 'value="Gina Google"' in page)
        tok = token(g)
        r = g.post("/register", data={"csrf_token": tok, "name": "Gina Google", "class_name": "HND 2", "terms": "on"})
        t.check("still no role -> 400", r.status_code == 400 and "Choose whether" in r.get_data(as_text=True))
        r = g.post("/register", data={"csrf_token": tok, "role": "student", "name": "Gina Google", "class_name": "HND 2",
                                      "terms": "on", "email": f"forged-{run}@{DOMAIN}"})
        t.check("student chosen -> account created", r.status_code == 302, r.status_code)
        gina = users.get_by_email(g_email)
        t.check("uses Google's email, not the posted one", gina is not None and users.get_by_email(f"forged-{run}@{DOMAIN}") is None)
        t.check("student, class saved, Google identity linked",
                gina is not None and gina.roles == [UserRole.STUDENT] and gina.class_name == "HND 2"
                and [i.provider for i in users.list_identities(gina.id)] == ["google"])
        t.check("logged in", "Log out</button>" in text(g, "/"))
        logout(g)
        t.check("parked Google profile cleared", g_email not in text(g, "/register"))

        t.section("google: log in again / sign up again")
        r = via_google(g, fake, gina_g, "intent=login&next=/register")
        t.check("known Google account -> logged in, next followed", r.headers.get("Location") == "/register" and "Log out</button>" in text(g, "/"))
        logout(g)
        r = via_google(g, fake, gina_g, "intent=register&role=lecturer")
        t.check("sign-up with an existing Google account just logs in", r.headers.get("Location") == "/" and "already have an account" in text(g, "/"))
        t.check("did not become a lecturer applicant", apps.for_user(gina.id) is None)
        logout(g)

        t.section("google: role chosen before Google")
        lec_email = f"google-lec-{run}@{DOMAIN}"
        g2 = app.test_client()
        r = via_google(g2, fake, GoogleProfile(sub=f"g-{run}-2", email=lec_email, email_verified=True, name="Prof Google"),
                       "intent=register&role=lecturer")
        t.check("back on /register with lecturer selected", r.headers.get("Location") == "/register?role=lecturer"
                and 'value="lecturer" checked' in text(g2, "/register?role=lecturer"), r.headers.get("Location"))
        r = g2.post("/register", data={"csrf_token": token(g2), "role": "lecturer", "name": "Prof Google",
                                       "institution": "IUT", "courses_taught": "HCI", "terms": "on"})
        prof = users.get_by_email(lec_email)
        t.check("lecturer application pending + Google identity",
                r.status_code == 302 and prof is not None and apps.for_user(prof.id) is not None and len(users.list_identities(prof.id)) == 1)

        t.section("google: 'use another account'")
        g3 = app.test_client()
        via_google(g3, fake, GoogleProfile(sub=f"g-{run}-6", email=f"switch-{run}@{DOMAIN}", email_verified=True, name="S"))
        r = g3.post("/register/google/cancel", data={"csrf_token": token(g3), "role": "student"})
        t.check("cancel -> back to the normal form", r.headers.get("Location") == "/register?role=student" and 'name="password"' in text(g3, "/register"))

        t.section("google: existing password account")
        g4 = app.test_client()
        r = via_google(g4, fake, GoogleProfile(sub=f"g-{run}-3", email=student_email.upper(), email_verified=True, name="Amina"))
        t.check("verified email -> linked and logged in", r.headers.get("Location") == "/" and "Log out</button>" in text(g4, "/"))
        t.check("password still works, identity added", users.authenticate(student_email, password).id == amina.id
                and [i.provider_uid for i in users.list_identities(amina.id)] == [f"g-{run}-3"])

        t.section("admins come from ADMIN_EMAILS + Google, never from the database")
        admin_email = f"boss-{run}@{DOMAIN}"
        saved_admins = app.config["ADMIN_EMAILS"]
        app.config["ADMIN_EMAILS"] = frozenset({admin_email})
        try:
            ga = app.test_client()
            ga.post("/register", data={"csrf_token": token(ga), "role": "student", "name": "Boss", "email": admin_email,
                                       "class_name": "Other", "password": password, "terms": "on"})
            open_link(ga, mailer, admin_email)
            boss = users.get_by_email(admin_email)
            t.check("listed email signs up as a normal student", boss is not None and boss.roles == [UserRole.STUDENT])
            probe = "/_admin_probe"
            t.check("sign-up alone is not admin", ga.get(probe).status_code == 404)
            logout(ga)
            r = ga.post("/login", data={"csrf_token": token(ga, "/login"), "email": admin_email, "password": password})
            t.check("password log-in is never admin", ga.get(probe).status_code == 404)
            t.check("...so they act as a student", ga.get("/_student_probe").status_code == 200)
            t.check("...and says Google is needed", "Admin access needs Google" in text(ga, "/"))
            logout(ga)
            via_google(ga, fake, GoogleProfile(sub=f"g-{run}-boss", email=admin_email.upper(), email_verified=True, name="Boss"))
            t.check("Google log-in with a listed email -> admin", ga.get(probe).status_code == 200)
            t.check("an admin session has no student rights", ga.get("/_student_probe").status_code == 403)
            cookie = ga.get_cookie("session")
            t.check("admin session ends with the browser", cookie is not None and cookie.expires is None)
            logout(ga)
            t.check("log out drops admin", ga.get(probe).status_code == 404)

            gb = app.test_client()
            via_google(gb, fake, gina_g)
            t.check("Google log-in with an unlisted email -> not admin", gb.get(probe).status_code == 404)

            # someone with database access points Gina's Google identity at the boss's row
            db.execute(sa.text("update user_identities set user_id = :b where provider_uid = :u"), {"b": boss.id, "u": gina_g.sub})
            gc = app.test_client()
            via_google(gc, fake, gina_g)
            t.check("DB tampering: logged in as boss's row, but NOT admin", "Log out</button>" in text(gc, "/") and gc.get(probe).status_code == 404)

            app.config["ADMIN_EMAILS"] = frozenset()
            gd = app.test_client()
            via_google(gd, fake, GoogleProfile(sub=f"g-{run}-boss", email=admin_email, email_verified=True, name="Boss"))
            t.check("email removed from ADMIN_EMAILS -> not admin", gd.get(probe).status_code == 404)
        finally:
            app.config["ADMIN_EMAILS"] = saved_admins

        t.section("google on an unverified password account")
        pre = f"pre-{run}@{DOMAIN}"
        gp = app.test_client()
        gp.post("/register", data={"csrf_token": token(gp), "role": "student", "name": "Pre", "email": pre,
                                   "class_name": "Other", "password": "someone-elses-pw", "terms": "on"})
        via_google(gp, fake, GoogleProfile(sub=f"g-{run}-pre", email=pre, email_verified=True, name="Pre"))
        pre_user = users.get_by_email(pre)
        t.check("Google proves the email: account verified, logged in", pre_user.email_verified and "Log out</button>" in text(gp, "/"))
        t.check("the password set before verification stops working", users_password_fails(users, pre, "someone-elses-pw"))

        t.section("google: refusals")
        g5 = app.test_client()
        r = via_google(g5, fake, GoogleProfile(sub=f"g-{run}-4", email=f"unverified-{run}@{DOMAIN}", email_verified=False, name="U"))
        t.check("unverified email refused, nothing created", r.headers.get("Location") == "/login"
                and "not verified" in text(g5, "/login") and users.get_by_email(f"unverified-{run}@{DOMAIN}") is None)
        g5.get("/auth/google?intent=login")
        r = g5.get("/auth/google/callback?error=access_denied")
        t.check("cancelled at Google -> message", r.headers.get("Location") == "/login" and "cancelled" in text(g5, "/login"))
        via_google(g5, fake, GoogleProfile(sub=f"g-{run}-5", email=f"late-{run}@{DOMAIN}", email_verified=True, name="Late"))
        text(g5, "/register")   # shows (and uses up) the flash message, which names the email
        with g5.session_transaction() as sess:
            sess["google_signup"] = {**sess["google_signup"], "exp": 0}   # reassign: nested edits are not saved
        t.check("after 15 minutes the Google sign-in is forgotten", f"late-{run}@" not in text(g5, "/register") and 'name="password"' in text(g5, "/register"))
        del app.extensions["google"]

        t.section("home shows courses from the database")
        course = courses.create(CourseCreate(code=f"T{run}", title=f"Test course {run}"), created_by=lecturer.id)
        course_ids.append(course.id)
        home = text(c, "/")
        t.check("course listed, COMING SOON without material", f"TEST COURSE {run.upper()}" in home and f'id="course-{course.id}" class="card dim"' in home)
    finally:
        app.extensions.pop("google", None)
        for cid in course_ids:
            courses.delete(cid)
        db.execute(sa.text("delete from courses where created_by in (select id from users where email like :d)"), {"d": f"%@{DOMAIN}"})
        db.execute(sa.text("delete from users where email like :d"), {"d": f"%@{DOMAIN}"})
        left = db.scalar(sa.text("select count(*) from users where email like :d"), {"d": f"%@{DOMAIN}"})
        print(f"\ncleanup: {left} test users left")
    return t.summary()


if __name__ == "__main__":
    sys.exit(main())

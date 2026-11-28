"""Joining an exam with its code: logged-in students, and guests without an account.

    /join                 the code (guests also give their name and email)
    /join/verify          guests: the 6-digit code emailed to them
    /join/exam            guests: the exam's start page
    /join/attempt/<id>    guests: the exam itself (timer, reload-safe)
    /join/done            guests: "your result will be emailed to you"

A lecturer switches the code on in the exam's settings (Studio), for anyone
with the code or only for a class list of emails. Logged-in students go to the
normal exam page. Guests are known only by their name and checked email; the
verified guest is kept in the signed session cookie. They earn no exp or rank,
and get their result by email: as soon as it is graded, or when the lecturer
presses "Email results to guests" (the exam's guest_results setting).
"""

from __future__ import annotations

from datetime import UTC, datetime

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from flask_babel import gettext as _
from pydantic import ValidationError

from src.attempt.AttemptStatus import AttemptStatus
from src.attempt.service.AttemptErrors import (
    AttemptAlreadySubmittedError,
    AttemptNotFoundError,
    AttemptNotYoursError,
    ExamAttemptsExhaustedError,
    NoQuestionsError,
)
from src.base.ServiceErrors import ServiceError
from src.exam.service.ExamErrors import ExamNotOpenError, ExamNotPublishedError
from src.exam.service.ExamService import is_open
from src.exam.service.GuestService import GuestError, GuestService
from src.mail import Mailer as mail
from src.web import deps

bp = Blueprint("join", __name__)

PENDING_KEY = "join_pending"   # {exam_id, email, name}: waiting for the emailed code
GUEST_KEY = "join_guest"       # {exam_id, guest_id}: a verified guest


def _guests() -> GuestService:
    return GuestService(deps.db())


def _send(to: str, subject: str, text: str, html: str) -> bool:
    try:
        current_app.extensions["mailer"].send(mail.Email(to=to, subject=subject, text=text, html=html))
        return True
    except mail.MailError:
        current_app.logger.exception("could not email %s", to)
        return False


def _guest_exam():
    """The verified guest in this browser and their exam, or (None, None)."""
    data = session.get(GUEST_KEY)
    if not data:
        return None, None
    try:
        guest = _guests().get(int(data["guest_id"]))
        exam = deps.exams().get(int(data["exam_id"]))
    except (ServiceError, KeyError, ValueError):
        session.pop(GUEST_KEY, None)
        return None, None
    if not guest.verified or guest.exam_id != exam.id:
        session.pop(GUEST_KEY, None)
        return None, None
    return guest, exam


# ------------------------------------------------------------------- code
@bp.route("/join", methods=["GET", "POST"])
def join():
    values = {"code": (request.values.get("code") or "").strip(), "name": request.form.get("name", "").strip(),
              "email": request.form.get("email", "").strip()}
    error = None
    if request.method == "POST":
        exam = deps.exams().find_by_code(values["code"])
        if exam is None:
            error = _("This code does not work. Check it with your lecturer: it may be wrong or switched off.")
        elif g.user is not None:
            # logged in: the normal exam page (exp and rank count)
            if g.get("is_admin") or "student" not in g.roles:
                error = _("An admin session cannot take exams. Log in with your password to take it.")
            elif not deps.exams().may_join(exam, g.user.email):
                error = _("Your email is not on this exam's class list. Ask your lecturer.")
            else:
                session["joined_exams"] = sorted(set(session.get("joined_exams", [])) | {exam.id})[-50:]
                return redirect(url_for("tests.exam", exam_id=exam.id))
        elif not deps.exams().may_join(exam, values["email"]):
            error = _("This email is not on the exam's class list. Use the email your lecturer has, or ask them.")
        elif exam.closes_at is not None and exam.closes_at <= datetime.now(UTC):
            error = _("This exam has closed.")
        else:
            try:
                guest, code = _guests().request_code(exam.id, values["name"], values["email"])
            except GuestError as exc:
                error = str(exc)
            else:
                html = render_template("email/join_code.html", name=guest.name, code=code, exam=exam)
                sent = _send(guest.email, _("Your code for the exam “%(title)s”", title=exam.title),
                             _("Hello %(name)s,\n\nYour code to join “%(title)s” on Studiamo: %(code)s\n\n"
                               "It works for 15 minutes. If you did not ask for it, ignore this email.\n",
                               name=guest.name, title=exam.title, code=code), html)
                session[PENDING_KEY] = {"exam_id": exam.id, "email": guest.email, "name": guest.name}
                if not sent:
                    flash(_("We could not send the email just now. Ask for a new code in a minute."))
                return redirect(url_for("join.verify"))
    return render_template("join/join.html", values=values, error=error), (400 if error else 200)


@bp.route("/join/verify", methods=["GET", "POST"])
def verify():
    pending = session.get(PENDING_KEY)
    if not pending:
        return redirect(url_for("join.join"))
    error = None
    if request.method == "POST":
        try:
            guest = _guests().verify(pending["exam_id"], pending["email"], request.form.get("code", ""))
        except GuestError as exc:
            error = str(exc)
        else:
            session.pop(PENDING_KEY, None)
            session[GUEST_KEY] = {"exam_id": guest.exam_id, "guest_id": guest.id}
            return redirect(url_for("join.guest_exam"))
    return render_template("join/verify.html", email=pending["email"], error=error), (400 if error else 200)


@bp.post("/join/resend")
def resend():
    pending = session.get(PENDING_KEY)
    if not pending:
        return redirect(url_for("join.join"))
    exam = deps.exams().get(pending["exam_id"])
    try:
        guest, code = _guests().request_code(exam.id, pending["name"], pending["email"])
    except GuestError as exc:
        flash(str(exc))
    else:
        _send(guest.email, _("Your code for the exam “%(title)s”", title=exam.title),
              _("Hello %(name)s,\n\nYour new code to join “%(title)s”: %(code)s\n", name=guest.name, title=exam.title, code=code),
              render_template("email/join_code.html", name=guest.name, code=code, exam=exam))
        flash(_("A new code was sent."))
    return redirect(url_for("join.verify"))


# ------------------------------------------------------------- the exam
@bp.get("/join/exam")
def guest_exam():
    guest, exam = _guest_exam()
    if guest is None:
        return redirect(url_for("join.join"))
    attempts = [a for a in deps.attempts().list_attempts_for_exam(exam.id) if a.guest_id == guest.id]
    open_attempt = next((a for a in attempts if a.status == AttemptStatus.IN_PROGRESS), None)
    left = None if exam.max_attempts is None else max(0, exam.max_attempts - len(attempts))
    return render_template("join/exam.html", guest=guest, exam=exam, attempts=attempts, open_attempt=open_attempt,
                           left=left, is_open=is_open(exam), pool=deps.exams().pool_size(exam.id))


@bp.post("/join/start")
def guest_start():
    guest, exam = _guest_exam()
    if guest is None:
        return redirect(url_for("join.join"))
    if not exam.join_enabled:
        flash(_("The lecturer switched this exam's code off."))
        return redirect(url_for("join.guest_exam"))
    try:
        started = deps.attempts().start_exam(None, exam.id, guest_id=guest.id)
    except (ExamNotOpenError, ExamNotPublishedError, ExamAttemptsExhaustedError, NoQuestionsError) as exc:
        flash(str(exc))
        return redirect(url_for("join.guest_exam"))
    return redirect(url_for("join.guest_attempt", attempt_id=started.attempt.id))


@bp.route("/join/attempt/<int:attempt_id>", methods=["GET", "POST"])
def guest_attempt(attempt_id: int):
    from src.web.tests import _answers, _rendered

    guest, exam = _guest_exam()
    if guest is None:
        return redirect(url_for("join.join"))
    svc = deps.attempts()
    try:
        started = svc.resume_exam(attempt_id, None, guest_id=guest.id)
    except AttemptAlreadySubmittedError:
        return redirect(url_for("join.done"))
    except (AttemptNotFoundError, AttemptNotYoursError):
        abort(404)
    if request.method == "POST":
        try:
            auto = request.form.get("auto_submit") or None
            result = svc.submit_exam(attempt_id, None, _answers(started.questions), guest_id=guest.id, auto_submitted=auto)
        except ValidationError:
            flash(_("Some answers could not be read. Please try again."))
        except AttemptAlreadySubmittedError:
            return redirect(url_for("join.done"))
        else:
            if not result.pending_manual and exam.guest_results == "graded":
                send_guest_result(attempt_id)
            session["join_done_late"] = bool(result.late)
            session["join_done_auto"] = auto if auto in ("left", "back") else None
            return redirect(url_for("join.done"))
    return render_template("tests/take.html", kind="exam", title=started.exam.title, started=started,
                           questions=started.questions, bodies=_rendered(started.questions),
                           action=url_for("join.guest_attempt", attempt_id=attempt_id), deadline=started.deadline,
                           back=url_for("join.guest_exam"), event_url=url_for("join.guest_event", attempt_id=attempt_id))


@bp.post("/join/attempt/<int:attempt_id>/event")
def guest_event(attempt_id: int):
    from src.web.tests import _log_event

    guest, _unused = _guest_exam()
    if guest is None:
        abort(404)
    return _log_event(lambda event: deps.attempts().log_exam_event(attempt_id, None, event, guest_id=guest.id))


@bp.get("/join/done")
def done():
    guest, exam = _guest_exam()
    if guest is None:
        return redirect(url_for("join.join"))
    from src.web.tests import AUTO_MESSAGES

    auto = session.pop("join_done_auto", None)
    return render_template("join/done.html", guest=guest, exam=exam, late=session.pop("join_done_late", False),
                           auto_message=AUTO_MESSAGES.get(auto))


# ---------------------------------------------------------- result email
def send_guest_result(attempt_id: int, *, only_if_unsent: bool = False) -> bool:
    """Email a guest their graded result. True if an email went out."""
    attempt = deps.attempts().get_exam_attempt(attempt_id)
    if attempt.guest_id is None or attempt.status != AttemptStatus.GRADED:
        return False
    guest = _guests().get(attempt.guest_id)
    if only_if_unsent and guest.results_sent_at is not None:
        return False
    exam = deps.exams().get(attempt.exam_id)
    text = _("Hello %(name)s,\n\nYour result for “%(title)s”: %(score)s%% "
             "(%(earned)s of %(possible)s points).\n\n"
             "Create a free Studiamo account with this email to keep your results and earn exp next time.\n",
             name=guest.name, title=exam.title, score=f"{attempt.score_pct:g}",
             earned=f"{attempt.points_earned:g}", possible=f"{attempt.points_possible:g}")
    html = render_template("email/guest_result.html", guest=guest, exam=exam, attempt=attempt)
    if _send(guest.email, _("Your result: %(title)s", title=exam.title), text, html):
        _guests().mark_results_sent(guest.id)
        return True
    return False


def init_app(app) -> None:
    app.register_blueprint(bp)

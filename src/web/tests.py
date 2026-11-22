"""Taking exercises and exams (students), and seeing the results.

  /exercises/<id>             overview: best score, attempts, Start (anyone may look)
  /exercises/<id>/take        the questions (starts, or resumes the open attempt)
  /exams/<id>                 lobby: rules, attempts left, Start
  /exams/attempts/<id>        the exam, with a timer; reload-safe (resume)
  /results/<kind>/<id>        score, exp and the review (the attempt record)

Answers are graded by AttemptService; the review is the attempt's md record,
rendered with Studiamo Markdown. A scheduled exam's review (correct answers)
is only shown once the exam has closed, so it cannot leak to others.
"""

from __future__ import annotations

from datetime import UTC, datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from pydantic import ValidationError

from src.attempt.AttemptStatus import AttemptStatus
from src.attempt.AttemptSubmitDto import AnswerSubmit, AttemptSubmit
from src.attempt.service.AttemptErrors import (
    AttemptAlreadySubmittedError,
    AttemptNotFoundError,
    AttemptNotYoursError,
    ExamAttemptsExhaustedError,
    NoQuestionsError,
)
from src.base.ServiceErrors import ServiceError
from src.content.StudiamoMarkdown import render
from src.exam.models.Exam import ExamKind
from src.exam.service.ExamErrors import ExamNotOpenError, ExamNotPublishedError
from src.exam.service.ExamService import is_open
from src.exercise.service.ExerciseErrors import ExerciseNotPublishedError
from src.question.models.Question import QuestionType
from src.web import deps
from src.web.security import login_required, roles_required
from src.web.studio_tests import strip_marker

bp = Blueprint("tests", __name__)


def _answers(questions) -> AttemptSubmit:
    answers = []
    for q in questions:
        if q.type == QuestionType.MCQ:
            ids = [int(x) for x in request.form.getlist(f"q{q.id}") if x.isdigit()]
            if ids:
                answers.append(AnswerSubmit(question_id=q.id, selected_option_ids=ids))
        else:
            text = (request.form.get(f"q{q.id}") or "").replace("\r\n", "\n")
            if text.strip():
                answers.append(AnswerSubmit(question_id=q.id, answer_text=text))
    return AttemptSubmit(answers=answers)


def _rendered(questions) -> dict[int, str]:
    return {q.id: render(q.body_md).html for q in questions}


# ================================================================ exercises
@bp.get("/exercises/<int:exercise_id>")
def exercise(exercise_id: int):
    try:
        ex = deps.exercises().get(exercise_id)
    except ServiceError:
        abort(404)
    topic = deps.topics().get(ex.topic_id)
    course = deps.course_or_404(topic.course_id)
    editor = deps.can_edit_course(course)
    if not ex.published and not editor:
        abort(404)
    mark = attempts = None
    if g.user and "student" in g.roles:
        mark = deps.progress().exercise_mark(g.user.id, exercise_id)
        attempts = deps.attempts().list_exercise_attempts(g.user.id, exercise_id=exercise_id)
    return render_template("tests/exercise.html", ex=ex, topic=topic, course=course, editor=editor, mark=mark, attempts=attempts or [])


@bp.route("/exercises/<int:exercise_id>/take", methods=["GET", "POST"])
@roles_required("student")
def take_exercise(exercise_id: int):
    try:
        started = deps.attempts().start_exercise(g.user.id, exercise_id)
    except (ExerciseNotPublishedError, NoQuestionsError) as exc:
        flash(str(exc))
        return redirect(url_for("tests.exercise", exercise_id=exercise_id))
    except ServiceError:
        abort(404)
    ex = deps.exercises().get(exercise_id)
    if request.method == "POST" and request.form.get("attempt_id") == str(started.attempt.id):
        try:
            result = deps.attempts().submit_exercise(started.attempt.id, g.user.id, _answers(started.questions))
        except ValidationError:
            flash("Some answers could not be read. Please try again.")
        except AttemptAlreadySubmittedError:
            pass
        else:
            _flash_result(result)
            return redirect(url_for("tests.result", kind="exercise", attempt_id=started.attempt.id))
    return render_template("tests/take.html", kind="exercise", title=ex.title, started=started, questions=started.questions,
                           bodies=_rendered(started.questions), action=url_for("tests.take_exercise", exercise_id=exercise_id),
                           deadline=None, back=url_for("tests.exercise", exercise_id=exercise_id))


def _flash_result(result) -> None:
    if result.late:
        flash("Submitted after the time was up: the answers could not be counted.")
    elif result.pending_manual:
        flash(f"Submitted. Some answers are graded by your lecturer; your score and exp follow then.")
    else:
        flash(f"Submitted: {result.attempt.score_pct:g}% · +{result.exp_awarded} exp.")


# ==================================================================== exams
@bp.get("/exams/<int:exam_id>")
def exam(exam_id: int):
    try:
        exam = deps.exams().get_public(exam_id)
    except ServiceError:
        abort(404)
    course = deps.course_or_404(exam.course_id)
    editor = deps.can_edit_course(course)
    if not exam.published and not editor:
        abort(404)
    attempts, mark, open_attempt = [], None, None
    if g.user and "student" in g.roles:
        attempts = deps.attempts().list_exam_attempts(g.user.id, exam_id=exam_id)
        mark = deps.progress().exam_mark(g.user.id, exam_id)
        open_attempt = next((a for a in attempts if a.status == AttemptStatus.IN_PROGRESS), None)
    now = datetime.now(UTC)
    full = deps.exams().get(exam_id)
    left = None if exam.max_attempts is None else max(0, exam.max_attempts - len(attempts))
    return render_template("tests/exam.html", exam=exam, course=course, editor=editor, attempts=attempts, mark=mark,
                           open_attempt=open_attempt, left=left, is_open=is_open(full, now), now=now,
                           pool=deps.exams().pool_size(exam_id))


@bp.post("/exams/<int:exam_id>/start")
@roles_required("student")
def start_exam(exam_id: int):
    try:
        started = deps.attempts().start_exam(g.user.id, exam_id)
    except (ExamNotOpenError, ExamNotPublishedError, ExamAttemptsExhaustedError, NoQuestionsError) as exc:
        flash(str(exc))
        return redirect(url_for("tests.exam", exam_id=exam_id))
    return redirect(url_for("tests.take_exam", attempt_id=started.attempt.id))


@bp.route("/exams/attempts/<int:attempt_id>", methods=["GET", "POST"])
@roles_required("student")
def take_exam(attempt_id: int):
    svc = deps.attempts()
    try:
        started = svc.resume_exam(attempt_id, g.user.id)
    except AttemptAlreadySubmittedError:
        return redirect(url_for("tests.result", kind="exam", attempt_id=attempt_id))
    except (AttemptNotFoundError, AttemptNotYoursError):
        abort(404)
    if request.method == "POST":
        try:
            result = svc.submit_exam(attempt_id, g.user.id, _answers(started.questions))
        except ValidationError:
            flash("Some answers could not be read. Please try again.")
        except AttemptAlreadySubmittedError:
            return redirect(url_for("tests.result", kind="exam", attempt_id=attempt_id))
        else:
            _flash_result(result)
            return redirect(url_for("tests.result", kind="exam", attempt_id=attempt_id))
    return render_template("tests/take.html", kind="exam", title=started.exam.title, started=started, questions=started.questions,
                           bodies=_rendered(started.questions), action=url_for("tests.take_exam", attempt_id=attempt_id),
                           deadline=started.deadline, back=url_for("tests.exam", exam_id=started.exam.id))


# ================================================================== results
@bp.get("/results/<kind>/<int:attempt_id>")
@login_required
def result(kind: str, attempt_id: int):
    if kind not in ("exercise", "exam"):
        abort(404)
    svc = deps.attempts()
    try:
        attempt = svc.get_exercise_attempt(attempt_id) if kind == "exercise" else svc.get_exam_attempt(attempt_id)
    except AttemptNotFoundError:
        abort(404)
    if kind == "exercise":
        thing = deps.exercises().get(attempt.exercise_id)
        course = deps.course_or_404(deps.topics().get(thing.topic_id).course_id)
        back = url_for("tests.exercise", exercise_id=thing.id)
        show_review = True
    else:
        thing = deps.exams().get(attempt.exam_id)
        course = deps.course_or_404(thing.course_id)
        back = url_for("tests.exam", exam_id=thing.id)
        closed = thing.kind == ExamKind.SYSTEM or (thing.closes_at is not None and thing.closes_at <= datetime.now(UTC))
        show_review = closed
    mine = g.user.id == attempt.student_id
    if not mine and not deps.can_edit_course(course):
        abort(404)
    if attempt.status == AttemptStatus.IN_PROGRESS:
        return redirect(url_for("tests.take_exam", attempt_id=attempt_id) if kind == "exam"
                        else url_for("tests.take_exercise", exercise_id=thing.id))
    record = svc.read_record(kind, attempt_id) if (show_review or not mine) else ""
    return render_template("tests/result.html", kind=kind, attempt=attempt, thing=thing, course=course, back=back,
                           review=render(strip_marker(record)) if record else None, show_review=show_review or not mine)

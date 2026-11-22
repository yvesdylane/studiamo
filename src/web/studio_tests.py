"""Studio, part 2: the question bank, exercises and exams, and grading by hand.

Question texts and explanations are Studiamo Markdown, so a question can show
runnable code, a file tree or a live diagram.

Dates in forms are in APP_TIMEZONE (.env, default UTC); stored in UTC.

Review: lecturers never set exp, and their exercises and practice (system)
exams are published by an admin after review (src/review/ReviewService.py).
Their scheduled exams they publish themselves. A lecturer's change to approved
content (or to a question it uses) sends it back to review.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from pydantic import ValidationError

from src.attempt.AttemptResultDto import ManualGrade
from src.attempt.service import Grading
from src.attempt.service.AttemptErrors import AttemptNotAwaitingGradingError, AttemptNotFoundError, ManualGradeInvalidError
from src.base.ServiceErrors import ServiceError
from src.content.StudiamoMarkdown import render
from src.exam.dto.ExamDto import ExamCreate, ExamUpdate
from src.exam.dto.ExamQuestionDto import ExamQuestionBase
from src.exam.models.Exam import ExamKind
from src.exercise.dto.ExerciseDto import ExerciseCreate, ExerciseUpdate
from src.exercise.dto.ExerciseQuestionDto import ExerciseQuestionBase
from src.question.dto.QuestionDto import QuestionCreate, QuestionUpdate
from src.question.dto.QuestionOptionDto import QuestionOptionCreate
from src.question.dto.QuestionTestCaseDto import QuestionTestCaseCreate
from src.question.models.Question import CodeLanguage, QuestionType
from src.question.service.QuestionErrors import QuestionInUseError, QuestionInvalidError, QuestionNotFoundError
from src.web import deps
from src.progress.service.ExpRules import DEFAULT_EXP
from src.web.security import login_required
from src.web.studio import review_action

bp = Blueprint("studio_tests", __name__, url_prefix="/studio")

RUNNABLE = {CodeLanguage.PYTHON, CodeLanguage.JAVASCRIPT}


def tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("APP_TIMEZONE", "UTC"))


def parse_local(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=tz()).astimezone(UTC)


def to_local(value: datetime | None) -> str:
    return value.astimezone(tz()).strftime("%Y-%m-%dT%H:%M") if value else ""


@bp.before_request
@login_required
def _only_authors():
    if not deps.can_author():
        abort(404)


def _error_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())
    return str(exc)


def _int(name: str, default: int | None = None) -> int | None:
    raw = (request.form.get(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _float(name: str, default: float | None = None) -> float | None:
    raw = (request.form.get(name) or "").strip().replace(",", ".")
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _course_topics(course_id: int):
    return deps.topics().list_for_course(course_id)


# ============================================================ question bank
@bp.get("/courses/<int:course_id>/questions")
def bank(course_id: int):
    course = deps.require_course_editor(course_id)
    qs = deps.questions().list(course_id=course_id, limit=100)
    return render_template("studio/bank.html", course=course, questions=qs, topics={t.id: t for t in _course_topics(course_id)})


def _question_form() -> dict:
    """The question editor's fields, as entered."""
    labels = request.form.getlist("opt_label")
    correct = set(request.form.getlist("opt_correct"))
    options = [{"label": l.strip(), "is_correct": str(i) in correct} for i, l in enumerate(labels) if l.strip()]
    inputs, expected = request.form.getlist("tc_input"), request.form.getlist("tc_expected")
    weights, hidden = request.form.getlist("tc_weight"), set(request.form.getlist("tc_hidden"))
    cases = []
    for i, exp in enumerate(expected):
        if not exp.strip() and not (inputs[i] if i < len(inputs) else "").strip():
            continue
        try:
            w = float((weights[i] if i < len(weights) else "1") or 1)
        except ValueError:
            w = 1.0
        cases.append({"input": (inputs[i] if i < len(inputs) else "").replace("\r\n", "\n"),
                      "expected_output": exp.replace("\r\n", "\n"), "is_hidden": str(i) in hidden, "weight": w})
    return {
        "type": request.form.get("type", "mcq"),
        "body_md": request.form.get("body_md", "").replace("\r\n", "\n").strip(),
        "explanation_md": request.form.get("explanation_md", "").replace("\r\n", "\n").strip() or None,
        "points": _float("points", 1.0),
        "difficulty": _int("difficulty"),
        "topic_id": _int("topic_id"),
        "code_language": request.form.get("code_language") or None,
        "starter_code": request.form.get("starter_code", "").replace("\r\n", "\n") or None,
        "is_active": request.form.get("is_active") == "on" if request.form.get("is_active_sent") else True,
        "options": options,
        "test_cases": cases,
    }


def _clean(f: dict) -> dict:
    """Drop what does not belong to the chosen type (the form keeps every section)."""
    if f["type"] != "mcq":
        f["options"] = []
    if f["type"] != "code":
        f["test_cases"], f["code_language"], f["starter_code"] = [], None, None
    f["allow_multiple"] = sum(o["is_correct"] for o in f["options"]) > 1
    return f


def _after_save_target(question_id: int, course_id: int) -> str:
    """Return to the exercise/exam that asked for the question, adding it there."""
    add_to = request.form.get("add_to") or ""
    kind, _, ident = add_to.partition(":")
    if kind == "exercise" and ident.isdigit():
        ex = deps.exercises().get(int(ident))
        if question_id not in [q.question_id for q in ex.questions]:
            deps.exercises().add_question(ex.id, question_id)
        return url_for("studio_tests.edit_exercise", exercise_id=ex.id) + "#questions"
    if kind == "exam" and ident.isdigit():
        exam = deps.exams().get(int(ident))
        if exam.kind == ExamKind.SCHEDULED and question_id not in [q.question_id for q in exam.questions]:
            deps.exams().set_questions(exam.id, [ExamQuestionBase(question_id=q.question_id, points=q.points) for q in exam.questions]
                                       + [ExamQuestionBase(question_id=question_id)])
        return url_for("studio_tests.edit_exam", exam_id=exam.id) + "#questions"
    return url_for("studio_tests.bank", course_id=course_id)


@bp.route("/courses/<int:course_id>/questions/new", methods=["GET", "POST"])
def new_question(course_id: int):
    course = deps.require_course_editor(course_id)
    error = None
    form = {"type": request.args.get("type", "mcq"), "body_md": "", "points": 1, "topic_id": _int_arg("topic_id"),
            "options": [{"label": "", "is_correct": True}, {"label": "", "is_correct": False}], "test_cases": [],
            "code_language": "python", "is_active": True}
    if request.method == "POST":
        form = _clean(_question_form())
        try:
            q = deps.questions().create(QuestionCreate(**form), author_id=g.user.id)
        except (ValidationError, QuestionInvalidError, ServiceError) as exc:
            error = _error_text(exc)
        else:
            flash("Question saved." + _reopened_note(q.id, q.topic_id))
            return redirect(_after_save_target(q.id, course_id))
    return _question_page(course, form, None, error)


def _int_arg(name: str) -> int | None:
    v = request.args.get(name)
    return int(v) if v and v.isdigit() else None


@bp.route("/questions/<int:question_id>", methods=["GET", "POST"])
def edit_question(question_id: int):
    try:
        q = deps.questions().get(question_id)
    except QuestionNotFoundError:
        abort(404)
    if q.topic_id is None:
        abort(404)   # questions without a topic belong to no course here
    course = deps.require_course_editor(deps.topics().get(q.topic_id).course_id)
    error = None
    form = q.model_dump(mode="json")
    if request.method == "POST":
        form = _clean(_question_form())
        form["type"] = q.type.value   # the type cannot change
        form = _clean(form)
        try:
            deps.questions().update(question_id, QuestionUpdate(**{k: form[k] for k in (
                "body_md", "explanation_md", "points", "difficulty", "topic_id", "code_language", "starter_code",
                "is_active", "allow_multiple")}))
            deps.questions().replace_answers(
                question_id,
                [QuestionOptionCreate(**o) for o in form["options"]],
                [QuestionTestCaseCreate(**t) for t in form["test_cases"]],
            )
        except (ValidationError, QuestionInvalidError, ServiceError) as exc:
            error = _error_text(exc)
        else:
            flash("Question saved." + _reopened_note(question_id, form["topic_id"]))
            return redirect(_after_save_target(question_id, course.id))
    return _question_page(course, form, q, error)


def _reopened_note(question_id: int, topic_id: int | None) -> str:
    if deps.is_admin():
        return ""
    n = deps.review().reopen_for_question(question_id, topic_id)
    return f" {n} approved exercise(s)/exam(s) using it went back to admin review." if n else ""


def _question_page(course, form, question, error):
    return render_template(
        "studio/question_edit.html", course=course, form=form, question=question, error=error,
        topics=_course_topics(course.id), types=list(QuestionType), languages=list(CodeLanguage), runnable=RUNNABLE,
        add_to=request.values.get("add_to", ""), preview=render(form.get("body_md") or ""),
    ), (400 if error else 200)


@bp.post("/questions/<int:question_id>/delete")
def delete_question(question_id: int):
    q = deps.questions().get(question_id)
    course = deps.require_course_editor(deps.topics().get(q.topic_id).course_id)
    try:
        deps.questions().delete(question_id)
        flash("Question deleted.")
    except QuestionInUseError:
        deps.questions().deactivate(question_id)
        flash("This question is used in exercises or exams, so it was hidden instead of deleted.")
    return redirect(url_for("studio_tests.bank", course_id=course.id))


# ================================================================ exercises
def _exercise_and_course(exercise_id: int):
    try:
        ex = deps.exercises().get(exercise_id)
    except ServiceError:
        abort(404)
    topic = deps.topics().get(ex.topic_id)
    return ex, topic, deps.require_course_editor(topic.course_id)


@bp.post("/courses/<int:course_id>/exercises")
def new_exercise(course_id: int):
    deps.require_course_editor(course_id)
    topic_id = _int("topic_id")
    if topic_id not in [t.id for t in _course_topics(course_id)]:
        abort(400)
    try:
        ex = deps.exercises().create(ExerciseCreate(topic_id=topic_id, title=request.form.get("title", "").strip() or "New exercise",
                                                   max_exp=DEFAULT_EXP["exercise"]))
    except ValidationError as exc:
        flash(_error_text(exc))
        return redirect(url_for("studio.course", course_id=course_id) + "#exercises")
    return redirect(url_for("studio_tests.edit_exercise", exercise_id=ex.id))


@bp.route("/exercises/<int:exercise_id>", methods=["GET", "POST"])
def edit_exercise(exercise_id: int):
    ex, topic, course = _exercise_and_course(exercise_id)
    if request.method == "POST":
        action = request.form.get("action", "save")
        try:
            if action == "publish":
                review_action(deps.review(), "exercise", exercise_id, ex.title, ex.published, ex.review_status)
                return redirect(url_for("studio_tests.edit_exercise", exercise_id=exercise_id))
            if action == "save":
                fields = {"title": request.form.get("title", "").strip(), "retake_decay": _float("retake_decay", 0.5)}
                if deps.is_admin():
                    fields["max_exp"] = _int("max_exp", 0)
                deps.exercises().update(exercise_id, ExerciseUpdate(**fields))
                wanted = {int(x) for x in request.form.getlist("note_ids") if x.isdigit()}
                current = {n.id for n in deps.notes().list_for_course(course.id) if n.id in set(deps.exercises().note_ids_for_exercise(exercise_id))}
                for nid in wanted - current:
                    deps.exercises().attach_to_note(nid, exercise_id)
                for nid in current - wanted:
                    deps.exercises().detach_from_note(nid, exercise_id)
                flash("Exercise saved.")
            elif action == "add":
                qid = _int("question_id")
                if qid:
                    deps.exercises().add_question(exercise_id, qid)
            elif action in ("up", "down", "remove"):
                qid = _int("question_id")
                ids = [q.question_id for q in sorted(ex.questions, key=lambda q: q.position)]
                if qid in ids:
                    i = ids.index(qid)
                    if action == "remove":
                        ids.pop(i)
                    else:
                        j = i - 1 if action == "up" else i + 1
                        if 0 <= j < len(ids):
                            ids[i], ids[j] = ids[j], ids[i]
                    deps.exercises().set_questions(exercise_id, [ExerciseQuestionBase(question_id=q, position=p) for p, q in enumerate(ids)])
            elif action == "delete":
                deps.exercises().delete(exercise_id)
                flash("Exercise deleted.")
                return redirect(url_for("studio.course", course_id=course.id) + "#exercises")
            if not deps.is_admin() and deps.review().reopen_after_edit("exercise", exercise_id):
                flash("It was approved, so it is hidden again until an admin checks your changes.")
        except (ValidationError, ServiceError) as exc:
            flash("Not saved: " + _error_text(exc))
        return redirect(url_for("studio_tests.edit_exercise", exercise_id=exercise_id) + ("#questions" if action != "save" else ""))
    ordered = sorted(ex.questions, key=lambda q: q.position)
    qs = {q.id: q for q in deps.questions().get_many([q.question_id for q in ordered])}
    bank = [q for q in deps.questions().list(course_id=course.id, active_only=True, limit=100) if q.id not in qs]
    notes = deps.notes().list_for_course(course.id)
    attached = set(deps.exercises().note_ids_for_exercise(exercise_id))
    return render_template("studio/exercise_edit.html", ex=ex, topic=topic, course=course, ordered=ordered, qs=qs,
                           bank=bank, notes=notes, attached=attached, topics={t.id: t for t in _course_topics(course.id)})


# ==================================================================== exams
def _exam_and_course(exam_id: int):
    try:
        exam = deps.exams().get(exam_id)
    except ServiceError:
        abort(404)
    return exam, deps.require_course_editor(exam.course_id)


def _exam_form(kind: ExamKind) -> dict:
    data = {
        "title": request.form.get("title", "").strip(),
        "opens_at": parse_local(request.form.get("opens_at")),
        "closes_at": parse_local(request.form.get("closes_at")),
        "duration_minutes": _int("duration_minutes"),
        "questions_per_attempt": _int("questions_per_attempt"),
        "shuffle_questions": request.form.get("shuffle_questions") == "on",
        "shuffle_options": request.form.get("shuffle_options") == "on",
    }
    if deps.is_admin():          # only admins set exp
        data["max_exp"] = _int("max_exp", 0)
    if kind == ExamKind.SYSTEM:
        data["opens_at"] = data["closes_at"] = None
    return data


def _picked_questions() -> list[ExamQuestionBase]:
    picked = []
    for qid in request.form.getlist("question_ids"):
        if qid.isdigit():
            pts = _float(f"points_{qid}")
            picked.append(ExamQuestionBase(question_id=int(qid), points=pts))
    return picked


@bp.route("/courses/<int:course_id>/exams/new", methods=["GET", "POST"])
def new_exam(course_id: int):
    course = deps.require_course_editor(course_id)
    error = None
    kind = ExamKind(request.values.get("kind", "scheduled")) if request.values.get("kind") in ("scheduled", "system") else ExamKind.SCHEDULED
    if request.method == "POST":
        try:
            data = _exam_form(kind)
            picked = _picked_questions()
            topic_ids = [int(t) for t in request.form.getlist("topic_ids") if t.isdigit()]
            max_attempts = 1 if kind == ExamKind.SCHEDULED else _int("max_attempts")
            data.setdefault("max_exp", DEFAULT_EXP["scheduled_exam" if kind == ExamKind.SCHEDULED else "system_exam"])
            exam = deps.exams().create(ExamCreate(
                course_id=course_id, kind=kind, max_attempts=max_attempts, **data,
                question_ids=[p.question_id for p in picked] if kind == ExamKind.SCHEDULED else [],
                topic_ids=topic_ids if kind == ExamKind.SYSTEM else [],
            ), created_by=g.user.id)
            if kind == ExamKind.SCHEDULED and any(p.points for p in picked):
                deps.exams().set_questions(exam.id, picked)
        except (ValidationError, ServiceError, ValueError) as exc:
            error = _error_text(exc)
        else:
            flash("Exam created as a draft. Publish it when it is ready.")
            return redirect(url_for("studio_tests.edit_exam", exam_id=exam.id))
    return _exam_page(course, None, kind, error)


@bp.route("/exams/<int:exam_id>", methods=["GET", "POST"])
def edit_exam(exam_id: int):
    exam, course = _exam_and_course(exam_id)
    error = None
    if request.method == "POST":
        action = request.form.get("action", "save")
        try:
            if action == "save":
                data = _exam_form(exam.kind)
                deps.exams().update(exam_id, ExamUpdate(**data))
                if exam.kind == ExamKind.SCHEDULED:
                    deps.exams().set_questions(exam_id, _picked_questions())
                else:
                    deps.exams().set_topics(exam_id, [int(t) for t in request.form.getlist("topic_ids") if t.isdigit()])
                msg = "Exam saved."
                if exam.kind == ExamKind.SYSTEM and not deps.is_admin() and deps.review().reopen_after_edit("exam", exam_id):
                    msg += " It was approved, so it is hidden again until an admin checks your changes."
                flash(msg)
            elif action in ("publish", "unpublish"):
                if exam.kind == ExamKind.SCHEDULED and not deps.is_admin():
                    # a lecturer's exam for their own students: no review needed
                    if action == "publish":
                        deps.review().publish_direct("exam", exam_id, published=True)
                        flash("Published: your students can see this exam.")
                    else:
                        deps.review().hide("exam", exam_id)
                        flash("Back to draft.")
                else:
                    review_action(deps.review(), "exam", exam_id, exam.title, exam.published, exam.review_status)
            elif action == "delete":
                deps.exams().delete(exam_id)
                flash("Exam deleted.")
                return redirect(url_for("studio.course", course_id=course.id) + "#exams")
        except (ValidationError, ServiceError, ValueError) as exc:
            error = _error_text(exc)
        if not error:
            return redirect(url_for("studio_tests.edit_exam", exam_id=exam_id))
        exam = deps.exams().get(exam_id)
    return _exam_page(course, exam, exam.kind, error)


def _exam_page(course, exam, kind, error):
    bank = deps.questions().list(course_id=course.id, active_only=True, limit=100)
    chosen = {q.question_id: q.points for q in exam.questions} if exam else {}
    return render_template(
        "studio/exam_edit.html", course=course, exam=exam, kind=kind, error=error, bank=bank, chosen=chosen,
        topics=_course_topics(course.id), chosen_topics={t.topic_id for t in exam.topics} if exam else set(),
        to_local=to_local, tzname=os.getenv("APP_TIMEZONE", "UTC"),
        pool=deps.exams().pool_size(exam.id) if exam else None,
    ), (400 if error else 200)


# ================================================================== results
@bp.get("/exams/<int:exam_id>/results")
def exam_results(exam_id: int):
    exam, course = _exam_and_course(exam_id)
    attempts = deps.attempts().list_attempts_for_exam(exam_id)
    names = {}
    for a in attempts:
        if a.student_id not in names:
            try:
                names[a.student_id] = deps.users().get_public(a.student_id)
            except ServiceError:
                names[a.student_id] = None
    return render_template("studio/exam_results.html", exam=exam, course=course, attempts=attempts, names=names,
                           marks=deps.progress().exam_results(exam_id))


@bp.get("/courses/<int:course_id>/grading")
def grading(course_id: int):
    course = deps.require_course_editor(course_id)
    return render_template(
        "studio/grading.html", course=course,
        exercise_attempts=deps.attempts().pending_exercise_attempts(course_id=course_id),
        exam_attempts=deps.attempts().pending_exam_attempts(course_id=course_id),
    )


@bp.route("/grade/<kind>/<int:attempt_id>", methods=["GET", "POST"])
def grade(kind: str, attempt_id: int):
    if kind not in ("exercise", "exam"):
        abort(404)
    svc = deps.attempts()
    try:
        attempt = svc.get_exercise_attempt(attempt_id) if kind == "exercise" else svc.get_exam_attempt(attempt_id)
    except AttemptNotFoundError:
        abort(404)
    if kind == "exercise":
        topic = deps.topics().get(deps.exercises().get(attempt.exercise_id).topic_id)
        course = deps.require_course_editor(topic.course_id)
    else:
        course = deps.require_course_editor(deps.exams().get(attempt.exam_id).course_id)
    record = svc.read_record(kind, attempt_id)
    pending = Grading.pending_ids(record)
    error = None
    if request.method == "POST":
        try:
            points = {qid: float((request.form.get(f"points_{qid}") or "").replace(",", ".")) for qid in pending}
            grade_data = ManualGrade(points=points, comment_md=request.form.get("comment_md") or None)
            if kind == "exercise":
                svc.grade_exercise_attempt(attempt_id, grade_data, grader_id=g.user.id)
            else:
                svc.grade_exam_attempt(attempt_id, grade_data, grader_id=g.user.id)
        except (ValueError, ValidationError, ManualGradeInvalidError, AttemptNotAwaitingGradingError) as exc:
            error = "Enter points for every question: " + _error_text(exc)
        else:
            flash("Graded. The student's mark, exp and rank are updated.")
            return redirect(url_for("studio_tests.grading", course_id=course.id))
    return render_template("studio/grade.html", kind=kind, attempt=attempt, course=course, pending=pending,
                           record=render(strip_marker(record)), error=error)


def strip_marker(record: str) -> str:
    """The attempt record without its machine-readable first line."""
    return "\n".join(l for l in record.splitlines() if not l.startswith("<!-- studiamo:pending"))

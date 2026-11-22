"""Pages for learners: dashboard, courses, the notes reader, exercise and exam
lists, the leaderboard.

Everyone, logged in or not, may browse and read published material; only the
dashboard needs an account. Exp is earned by logged-in students. Exp and Xexp are only given to
sessions acting as a student (an admin session never earns anything).
Editors (see deps.can_edit_course) also see unpublished notes, marked DRAFT.
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from src.content.StudiamoMarkdown import render
from src.course.service.TopicErrors import NoteNotFoundError
from src.player.service.PlayerErrors import PlayerNotFoundError
from src.web import deps
from src.web.pages import catalog
from src.web.security import login_required, roles_required

bp = Blueprint("learn", __name__)


def outline(course_id: int, *, drafts: bool) -> list[dict]:
    """[{topic, notes}] of a course, in order; drafts only for editors."""
    notes = deps.notes().list_for_course(course_id, published_only=not drafts)
    by_topic: dict[int, list] = {}
    for n in notes:
        by_topic.setdefault(n.topic_id, []).append(n)
    return [
        {"topic": t, "notes": sorted(by_topic.get(t.id, []), key=lambda n: n.position)}
        for t in deps.topics().list_for_course(course_id)
    ]


def _completed_ids(course_id: int | None = None) -> set[int]:
    if g.get("user") is None or "student" not in g.roles:
        return set()
    return {c.note_id for c in deps.progress().completed_notes(g.user.id, course_id)}


# ------------------------------------------------------------------ lobby
def lobby_context(user_id: int) -> dict:
    """Everything about this player for their lobby (home page when logged in, /dashboard)."""
    me = deps.users().get(user_id)
    try:
        level = deps.players().get_level(user_id)
        position = None if me.ranking_visibility == "hidden" else deps.players().position_of(user_id)
        hidden_position = deps.players().position_of(user_id) if me.ranking_visibility == "hidden" else None
    except PlayerNotFoundError:
        level = position = hidden_position = None
    my_course_ids = deps.progress().course_ids_of(user_id)
    courses = [c for c in catalog() if c.available]
    progress = {c.id: deps.progress().course_progress(user_id, c.id) for c in courses}
    done = _completed_ids()
    resume = None   # the first unread note, in the courses this player already started first
    for c in sorted(courses, key=lambda c: (c.id not in my_course_ids, c.code)):
        for part in outline(c.id, drafts=False):
            n = next((n for n in part["notes"] if n.id not in done), None)
            if n:
                resume = {"course": c, "topic": part["topic"], "note": n, "started": c.id in my_course_ids,
                          "pct": progress[c.id].completion_pct}
                break
        if resume:
            break
    skills = deps.progress().skill_graph(user_id)
    eng = skills["engagement"]
    return {
        "me": me, "level": level, "position": position, "hidden_position": hidden_position,
        "courses": sorted(courses, key=lambda c: (c.id not in my_course_ids, -progress[c.id].completion_pct, c.code)),
        "progress": progress, "my_course_ids": set(my_course_ids), "resume": resume, "skills": skills, "eng": eng,
        "recent_badges": sorted((b for b in eng["badges"] if b["earned"]), key=lambda b: b["earned_at"], reverse=True),
        "ledger": deps.progress().ledger(user_id, limit=8),
        **_role_sections(me),
    }


def _role_sections(me) -> dict:
    """What goes under the player's own information, for each role they have."""
    from src.web.security import admin_emails

    account_roles = {r.value for r in me.roles}
    out = {"is_lecturer": "lecturer" in account_roles, "is_admin_account": me.email in admin_emails(),
           "teaching": [], "standalone": [], "teach_stats": {}, "to_grade": 0, "my_reviews": [], "admin": None}
    if out["is_lecturer"]:
        teaching = [c for c in deps.courses().list(limit=100) if c.created_by == me.id]
        out["teaching"] = teaching
        out["standalone"] = deps.exams().list_standalone(me.id)
        out["to_grade"] = sum(len(deps.attempts().pending_exam_attempts(exam_id=x.id)) for x in out["standalone"])
        ids = [c.id for c in teaching]
        if ids:
            out["teach_stats"] = {r["course_id"]: r for r in deps.db().fetch_all(
                """select c.id as course_id,
                          (select count(*) from topic_notes n join course_topics t on t.id = n.topic_id where t.course_id = c.id) as notes,
                          (select count(*) from exercises e join course_topics t on t.id = e.topic_id where t.course_id = c.id) as exercises,
                          (select count(*) from exams x where x.course_id = c.id) as exams,
                          (select count(distinct s.student_id) from student_courses s where s.course_id = c.id) as students
                     from courses c where c.id = any(:ids)""", {"ids": ids})}
            out["to_grade"] += sum(len(deps.attempts().pending_exercise_attempts(course_id=c)) +
                                  len(deps.attempts().pending_exam_attempts(course_id=c)) for c in ids)
            out["my_reviews"] = deps.db().fetch_all(
                """select kind, title, review_status, review_comment, id from (
                       select 'note' as kind, n.title, n.review_status, n.review_comment, n.id, t.course_id
                         from topic_notes n join course_topics t on t.id = n.topic_id
                       union all
                       select 'exercise', e.title, e.review_status, e.review_comment, e.id, t.course_id
                         from exercises e join course_topics t on t.id = e.topic_id
                       union all
                       select 'exam', x.title, x.review_status, x.review_comment, x.id, x.course_id from exams x
                   ) r where course_id = any(:ids) and review_status in ('pending', 'rejected')
                   order by review_status desc, title limit 12""", {"ids": ids})
    if out["is_admin_account"]:
        from src.user.models.LecturerApplication import LecturerApplicationStatus

        out["admin"] = {"queue": len(deps.review().pending()),
                        "requests": len(deps.lecturer_apps().list(status=LecturerApplicationStatus.PENDING, limit=100)),
                        "acting": bool(g.get("is_admin"))}
    return out


@bp.get("/dashboard")
@login_required
def dashboard():
    return render_template("learn/lobby.html", **lobby_context(g.user.id))


# ----------------------------------------------------------------- courses
@bp.get("/courses")
def courses():
    return render_template("learn/courses.html", courses=deps.courses().catalog(limit=100))


@bp.get("/courses/<int:course_id>")
def course(course_id: int):
    course = deps.course_or_404(course_id)
    editor = deps.can_edit_course(course)
    parts = outline(course_id, drafts=editor)
    cp = deps.progress().course_progress(g.user.id, course_id) if g.user and "student" in g.roles else None
    return render_template(
        "learn/course.html", course=course, parts=parts, editor=editor, done=_completed_ids(course_id), cp=cp,
        exercises=deps.exercises().list_for_course(course_id, published_only=not editor),
        exams=deps.exams().list_for_course(course_id, published_only=not editor),
    )


@bp.get("/courses/<int:course_id>/start")
def course_start(course_id: int):
    """The course strip's links: straight to the lesson to read. A student goes
    to their first unread note, everyone else to the first note; the course
    page when there is no note yet."""
    course = deps.course_or_404(course_id)
    notes = [n for p in outline(course_id, drafts=False) for n in p["notes"]]
    if not notes:
        return redirect(url_for("learn.course", course_id=course_id))
    done = _completed_ids(course_id)
    target = next((n for n in notes if n.id not in done), notes[0])
    return redirect(url_for("learn.note", note_id=target.id))


@bp.get("/exercises")
def exercises():
    groups = []
    for c in deps.courses().catalog(limit=100):
        items = deps.exercises().list_for_course(c.id, published_only=True)
        if items:
            groups.append({"course": c, "entries": items})
    best = {}
    if g.user and "student" in g.roles:
        best = {m.exercise_id: m for m in deps.progress().exercise_marks(g.user.id)}
    return render_template("learn/exercises.html", groups=groups, best=best)


@bp.get("/exams")
def exams():
    from datetime import UTC, datetime

    from src.exam.service.ExamService import is_open

    now = datetime.now(UTC)
    groups = []
    for c in deps.courses().catalog(limit=100):
        items = deps.exams().list_for_course(c.id, published_only=True)
        if items:
            groups.append({"course": c, "entries": [(x, is_open(x, now)) for x in items]})
    best = {}
    if g.user and "student" in g.roles:
        best = {m.exam_id: m for m in deps.progress().exam_marks(g.user.id)}
    return render_template("learn/exams.html", groups=groups, best=best)


# -------------------------------------------------------------------- notes
def _note_or_404(note_id: int):
    try:
        note = deps.notes().get(note_id)
    except NoteNotFoundError:
        abort(404)
    topic = deps.topics().get(note.topic_id)
    course = deps.course_or_404(topic.course_id)
    editor = deps.can_edit_course(course)
    if not note.published and not editor:
        abort(404)
    return note, topic, course, editor


@bp.get("/notes/<int:note_id>")
def note(note_id: int):
    note, topic, course, editor = _note_or_404(note_id)
    try:
        text = deps.notes().read_content(note_id)
    except FileNotFoundError:
        text = ""
    page = render(text)
    parts = outline(course.id, drafts=editor)
    flat = [n for p in parts for n in p["notes"]]
    i = next((k for k, n in enumerate(flat) if n.id == note.id), 0)
    return render_template(
        "learn/note.html", note=note, topic=topic, course=course, editor=editor, page=page, parts=parts,
        prev=flat[i - 1] if i > 0 else None, next=flat[i + 1] if i + 1 < len(flat) else None,
        done=_completed_ids(course.id), exercises=deps.exercises().list_for_note(note_id, published_only=not editor),
    )


@bp.post("/notes/<int:note_id>/complete")
@roles_required("student")
def complete_note(note_id: int):
    note, topic, course, editor = _note_or_404(note_id)
    if not note.published:
        abort(404)
    first = not deps.progress().is_note_completed(g.user.id, note_id)
    done = deps.progress().complete_note(g.user.id, note_id)
    if first:
        flash(f"Marked as read: +{done.exp_awarded} exp, +{done.xexp_awarded:g} Xexp.")
    nxt = request.form.get("next")
    return redirect(url_for("learn.note", note_id=int(nxt)) if nxt and nxt.isdigit() else url_for("learn.note", note_id=note_id))


@bp.post("/notes/<int:note_id>/time")
@roles_required("student")
def reading_time(note_id: int):
    """Active reading time from the note page (every ~30 s, and when leaving)."""
    note, topic, course, editor = _note_or_404(note_id)
    if not note.published:
        return ("", 204)
    try:
        seconds = int(request.form.get("seconds", "0"))
    except ValueError:
        return ("", 400)
    deps.progress().add_reading_time(g.user.id, note_id, seconds)
    return ("", 204)


# -------------------------------------------------------------- leaderboard
@bp.get("/leaderboard")
def leaderboard():
    board = deps.players().leaderboard(limit=50, viewer_id=g.user.id if g.user else None)
    me = deps.users().get(g.user.id) if g.user else None
    return render_template("learn/leaderboard.html", board=board, me=me)

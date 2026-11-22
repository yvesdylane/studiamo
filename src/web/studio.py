"""The studio: where lecturers (their own courses) and admins (every course)
write courses, topics and notes. Exercises and exams: studio_tests.py.

Review (src/review/ReviewService.py): a lecturer cannot publish a note or set
its exp. They submit it for review; an admin sets the exp and approves it.
A lecturer editing an approved note sends it back to review (unpublished).
Admins publish directly.

Notes are written in Studiamo Markdown (src/content/StudiamoMarkdown.py) with
a live preview; the text is saved as an .md file (NoteService + MarkdownStore).
"""

from __future__ import annotations

import uuid

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for
from pydantic import ValidationError

from src.content.StudiamoMarkdown import render
from src.course.dto.CourseDto import CourseCreate, CourseUpdate
from src.course.dto.NoteDto import NoteCreate, NoteUpdate
from src.course.dto.TopicDto import TopicCreate, TopicUpdate
from src.course.service.CourseErrors import CourseCodeTakenError
from src.course.service.TopicErrors import NoteNotFoundError, TopicNotFoundError
from src.web import deps
from src.base.ServiceErrors import ServiceError
from src.progress.service.ExpRules import DEFAULT_EXP
from src.web.forms import field_errors
from src.web.security import login_required

bp = Blueprint("studio", __name__, url_prefix="/studio")

MAX_NOTE_CHARS = 400_000

STARTER_NOTE = '''# {title}

A short introduction: what this lesson is about and why it matters.

## What you will learn

- the first idea
- the second idea

## Try it

"""python Try it yourself
numbers = [1, 2, 3]
print([n * 2 for n in numbers])
"""

```note
Readers can change the code and press **Run**.
```
'''


@bp.before_request
@login_required
def _only_authors():
    if not deps.can_author():
        abort(404)


def _next_url(default: str) -> str:
    target = request.form.get("next") or ""
    return target if target.startswith("/studio/") else default


# ------------------------------------------------------------------ courses
@bp.get("/")
def index():
    all_courses = deps.courses().list(limit=100)
    mine = [c for c in all_courses if deps.can_edit_course(c)]
    return render_template("studio/index.html", courses=mine, errors={}, values={}, standalone=_standalone())


def _standalone():
    """Exams without a course: the lecturer's own (all of them for an admin)."""
    return deps.exams().list_standalone(None if deps.is_admin() else g.user.id)


@bp.post("/courses")
def create_course():
    values = {k: request.form.get(k, "").strip() for k in ("code", "title", "description")}
    errors: dict[str, str] = {}
    try:
        data = CourseCreate(code=values["code"], title=values["title"], description=values["description"] or None)
    except ValidationError as exc:
        errors = field_errors(exc)
    else:
        try:
            course = deps.courses().create(data, created_by=g.user.id)
        except CourseCodeTakenError:
            errors["code"] = "Another course already uses this code."
        else:
            flash(f"Course {course.code} created. Add its first topic below.")
            return redirect(url_for("studio.course", course_id=course.id))
    mine = [c for c in deps.courses().list(limit=100) if deps.can_edit_course(c)]
    return render_template("studio/index.html", courses=mine, errors=errors, values=values, standalone=_standalone()), 400


@bp.route("/courses/<int:course_id>", methods=["GET", "POST"])
def course(course_id: int):
    course = deps.require_course_editor(course_id)
    errors: dict[str, str] = {}
    if request.method == "POST":
        values = {k: request.form.get(k, "").strip() for k in ("code", "title", "description")}
        try:
            data = CourseUpdate(code=values["code"], title=values["title"], description=values["description"] or None)
            course = deps.courses().update(course_id, data)
            flash("Course details saved.")
            return redirect(url_for("studio.course", course_id=course_id))
        except ValidationError as exc:
            errors = field_errors(exc)
        except CourseCodeTakenError:
            errors["code"] = "Another course already uses this code."
    parts = []
    notes = deps.notes().list_for_course(course_id)
    for t in deps.topics().list_for_course(course_id):
        parts.append({"topic": t, "notes": sorted([n for n in notes if n.topic_id == t.id], key=lambda n: n.position)})
    return render_template(
        "studio/course.html", course=course, parts=parts, errors=errors,
        exercises=deps.exercises().list_for_course(course_id),
        exams=deps.exams().list_for_course(course_id),
    ), (400 if errors else 200)


@bp.post("/courses/<int:course_id>/delete")
def delete_course(course_id: int):
    course = deps.require_course_editor(course_id)
    if request.form.get("confirm_code", "").strip() != course.code:
        flash("Type the course code to confirm. Nothing was deleted.")
        return redirect(url_for("studio.course", course_id=course_id))
    deps.courses().delete(course_id)
    flash(f"Course {course.code} deleted.")
    return redirect(url_for("studio.index"))


# ------------------------------------------------------------------- topics
def _topic_and_course(topic_id: int):
    try:
        topic = deps.topics().get(topic_id)
    except TopicNotFoundError:
        abort(404)
    return topic, deps.require_course_editor(topic.course_id)


@bp.post("/courses/<int:course_id>/topics")
def add_topic(course_id: int):
    deps.require_course_editor(course_id)
    title = request.form.get("title", "").strip()
    existing = deps.topics().list_for_course(course_id)
    try:
        deps.topics().create(TopicCreate(course_id=course_id, title=title, position=len(existing)))
    except ValidationError:
        flash("Give the topic a title (up to 200 characters).")
    return redirect(url_for("studio.course", course_id=course_id) + "#topics")


@bp.post("/topics/<int:topic_id>/rename")
def rename_topic(topic_id: int):
    topic, course = _topic_and_course(topic_id)
    try:
        deps.topics().update(topic_id, TopicUpdate(title=request.form.get("title", "").strip()))
    except ValidationError:
        flash("A topic needs a title.")
    return redirect(url_for("studio.course", course_id=course.id) + f"#topic-{topic_id}")


@bp.post("/topics/<int:topic_id>/move")
def move_topic(topic_id: int):
    topic, course = _topic_and_course(topic_id)
    ids = [t.id for t in deps.topics().list_for_course(course.id)]
    i = ids.index(topic_id)
    j = i - 1 if request.form.get("dir") == "up" else i + 1
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
        deps.topics().reorder(course.id, ids)
    return redirect(url_for("studio.course", course_id=course.id) + f"#topic-{topic_id}")


@bp.post("/topics/<int:topic_id>/delete")
def delete_topic(topic_id: int):
    topic, course = _topic_and_course(topic_id)
    deps.topics().delete(topic_id)
    flash(f"Topic “{topic.title}” deleted with its notes and exercises.")
    return redirect(url_for("studio.course", course_id=course.id) + "#topics")


# -------------------------------------------------------------------- notes
def _note_and_course(note_id: int):
    try:
        note = deps.notes().get(note_id)
    except NoteNotFoundError:
        abort(404)
    topic = deps.topics().get(note.topic_id)
    return note, topic, deps.require_course_editor(topic.course_id)


@bp.post("/topics/<int:topic_id>/notes")
def add_note(topic_id: int):
    topic, course = _topic_and_course(topic_id)
    title = request.form.get("title", "").strip() or ("Imported note" if request.form.get("then") == "import" else "New note")
    position = len(deps.notes().list_for_topic(topic_id))
    try:
        note = deps.notes().create(NoteCreate(
            topic_id=topic_id, title=title[:200], position=position,
            md_path=f"notes/{course.id}/{uuid.uuid4().hex}.md", max_exp=DEFAULT_EXP["note"],
        ))
    except ValidationError:
        flash("A note needs a title.")
        return redirect(url_for("studio.course", course_id=course.id))
    if request.form.get("then") == "import":   # an empty note, and the import dialog opens
        deps.notes().save_content(note.id, "")
        return redirect(url_for("studio.edit_note", note_id=note.id, **{"import": "1"}))
    deps.notes().save_content(note.id, STARTER_NOTE.format(title=note.title))
    return redirect(url_for("studio.edit_note", note_id=note.id))


@bp.route("/notes/<int:note_id>", methods=["GET", "POST"])
def edit_note(note_id: int):
    note, topic, course = _note_and_course(note_id)
    errors: dict[str, str] = {}
    if request.method == "POST":
        body = request.form.get("body", "").replace("\r\n", "\n")
        if len(body) > MAX_NOTE_CHARS:
            errors["body"] = f"The note is too long (at most {MAX_NOTE_CHARS:,} characters)."
        admin = deps.is_admin()
        try:
            fields = {"title": request.form.get("title", "").strip()}
            if admin:   # only admins set exp and publish directly
                fields["max_exp"] = int(request.form.get("max_exp") or 0)
            update = NoteUpdate(**fields)
        except (ValidationError, ValueError) as exc:
            errors.update(field_errors(exc) if isinstance(exc, ValidationError) else {"max_exp": "Enter a whole number."})
        if not errors:
            changed = body != _read(note_id) or update.title != note.title
            deps.notes().save_content(note_id, body)
            note = deps.notes().update(note_id, update)
            msg = "Saved."
            if admin:
                deps.review().publish_direct("note", note_id, published=request.form.get("published") == "on")
            elif changed and deps.review().reopen_after_edit("note", note_id):
                msg += " It was approved, so it is hidden again until an admin checks your changes."
            elif request.form.get("action") in ("submit", "withdraw"):
                from src.review.ReviewService import ReviewStateError

                try:
                    if request.form["action"] == "submit":
                        deps.review().submit("note", note_id)
                        msg += " Sent to an admin for review."
                    else:
                        deps.review().withdraw("note", note_id)
                        msg += " Taken out of the review queue."
                except ReviewStateError as exc:
                    msg += f" {exc}."
            problems = render(body).problems
            if problems:
                msg += f" {len(problems)} block problem(s) shown in the preview."
            flash(msg)
            return redirect(url_for("studio.edit_note", note_id=note_id))
        text = body
    else:
        text = _read(note_id)
    page = render(text, source_map=True)
    return render_template(
        "studio/note_edit.html", note=note, topic=topic, course=course, text=text, page=page, errors=errors,
        ai_key=deps.converter().settings(g.user.id), open_import=request.args.get("import") == "1",
    ), (400 if errors else 200)


def _read(note_id: int) -> str:
    try:
        return deps.notes().read_content(note_id)
    except FileNotFoundError:
        return ""


@bp.post("/notes/<int:note_id>/move")
def move_note(note_id: int):
    note, topic, course = _note_and_course(note_id)
    ids = [n.id for n in sorted(deps.notes().list_for_topic(topic.id), key=lambda n: n.position)]
    i = ids.index(note_id)
    j = i - 1 if request.form.get("dir") == "up" else i + 1
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
        deps.notes().reorder(topic.id, ids)
    return redirect(url_for("studio.course", course_id=course.id) + f"#topic-{topic.id}")


@bp.post("/notes/<int:note_id>/publish")
def publish_note(note_id: int):
    """Admins: publish / unpublish. Lecturers: submit for review / withdraw / hide."""
    note, topic, course = _note_and_course(note_id)
    review_action(deps.review(), "note", note_id, note.title, note.published, note.review_status)
    return redirect(_next_url(url_for("studio.course", course_id=course.id) + f"#topic-{topic.id}"))


def review_action(review, kind: str, item_id: int, title: str, published: bool, status: str) -> None:
    """The one publish button: what it does depends on who presses it."""
    from src.review.ReviewService import ReviewStateError

    action = request.form.get("action", "")
    try:
        if deps.is_admin():
            review.publish_direct(kind, item_id, published=not published)
            flash(f"“{title}” is now {'hidden' if published else 'published'}.")
        elif action == "withdraw" or status == "pending":
            review.withdraw(kind, item_id)
            flash(f"“{title}” was taken out of the review queue.")
        elif published:
            review.hide(kind, item_id)
            flash(f"“{title}” is hidden from students. Submit it again to publish it.")
        else:
            review.submit(kind, item_id)
            flash(f"“{title}” was sent to an admin for review.")
    except (ReviewStateError, ServiceError) as exc:
        flash(str(exc))


@bp.post("/notes/<int:note_id>/delete")
def delete_note(note_id: int):
    note, topic, course = _note_and_course(note_id)
    deps.notes().delete(note_id, delete_content=True)
    flash(f"Note “{note.title}” deleted.")
    return redirect(url_for("studio.course", course_id=course.id) + f"#topic-{topic.id}")


# ------------------------------------------------------------------- import
@bp.post("/notes/<int:note_id>/import")
def import_note(note_id: int):
    """Turn an uploaded file (or pasted text) into Studiamo Markdown for the editor.

    use_ai=1 and a saved Claude key: Claude converts it (src/ai/NoteConverter.py).
    Otherwise: the plain conversion (src/ai/importers.py). Nothing is saved
    here: the editor shows the result, and the lecturer saves it."""
    from src.ai.NoteConverter import AiKeyError
    from src.ai.importers import ImportError_, from_pasted, read_upload

    note, topic, course = _note_and_course(note_id)
    upload = request.files.get("file")
    try:
        if upload is not None and upload.filename:
            doc = read_upload(upload.filename, upload.read(16 * 1024 * 1024))
        elif (request.form.get("text") or "").strip():
            doc = from_pasted(request.form["text"])
        else:
            return jsonify(error="Choose a file or paste some text."), 400
    except ImportError_ as exc:
        return jsonify(error=str(exc)), 400
    use_ai = request.form.get("use_ai") == "1"
    if use_ai:
        try:
            result = deps.converter().convert(g.user.id, doc, title=request.form.get("title") or note.title)
        except AiKeyError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(markdown=result.markdown, problems=result.problems, used_ai=True, model=result.model)
    if doc.kind == "pdf":
        return jsonify(error="PDF files need the AI: add your Claude API key in Settings > AI assistant, "
                             "or copy the text out of the PDF and paste it."), 400
    return jsonify(markdown=doc.markdown, problems=render(doc.markdown).problems, used_ai=False, model=None)


# ------------------------------------------------------------------ preview
@bp.post("/preview")
def preview():
    """Live preview for the editors: Studiamo Markdown -> HTML (JSON)."""
    text = (request.form.get("body") or "")[:MAX_NOTE_CHARS]
    page = render(text, source_map=request.form.get("source_map") == "1")
    return jsonify(html=page.html, problems=page.problems, toc=page.toc)


@bp.get("/guide")
def guide():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "docs" / "studiamo-markdown.md").read_text(encoding="utf-8")
    return render_template("studio/guide.html", page=render(text), source=text)

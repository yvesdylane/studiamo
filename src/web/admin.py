"""Admin pages (admin sessions only, see security.py): the review queue
(lecturers' notes, exercises and practice exams), lecturer requests and users.

Admins never appear in the database as admins. From here they can approve or
reject lecturer requests and give or take the lecturer role.
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from src.user.dto.UserDto import UserRolesUpdate
from src.user.models.LecturerApplication import LecturerApplicationStatus
from src.user.models.User import UserRole
from src.user.service.LecturerApplicationErrors import LecturerApplicationNotFoundError, LecturerApplicationReviewedError
from src.user.service.UserErrors import UserNotFoundError
from src.web import deps
from src.web.security import admin_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@admin_required
def _only_admins():
    return None


@bp.get("/")
def index():
    pending = deps.lecturer_apps().list(status=LecturerApplicationStatus.PENDING, limit=100)
    people = {a.user_id: deps.users().get(a.user_id) for a in pending}
    query = request.args.get("q", "").strip()
    found = deps.users().search(query, limit=50) if query else deps.users().list(limit=50)
    return render_template("admin/index.html", pending=pending, people=people, query=query, found=found,
                           user_count=deps.users().count(), course_count=deps.courses().count(),
                           queue=deps.review().pending())


# ------------------------------------------------------------------ review
@bp.route("/review/<kind>/<int:item_id>", methods=["GET", "POST"])
def review_item(kind: str, item_id: int):
    from src.base.ServiceErrors import ServiceError
    from src.content.StudiamoMarkdown import render

    if kind not in ("note", "exercise", "exam"):
        abort(404)
    rv = deps.review()
    error = None
    if request.method == "POST":
        decision = request.form.get("decision")
        try:
            exp = int(request.form.get("max_exp") or 0)
            if decision == "approve":
                rv.approve(kind, item_id, reviewer_id=g.user.id, max_exp=exp)
                flash("Approved and published.")
            elif decision == "reject":
                rv.reject(kind, item_id, reviewer_id=g.user.id, comment=request.form.get("comment", ""))
                flash("Sent back to the lecturer with your comment.")
            else:
                abort(400)
            return redirect(url_for("admin.index") + "#review")
        except ValueError:
            error = "Exp must be a whole number."
        except ServiceError as exc:
            error = str(exc)
    try:
        if kind == "note":
            item = deps.notes().get(item_id)
            course = deps.courses().get(deps.topics().get(item.topic_id).course_id)
            try:
                body = render(deps.notes().read_content(item_id))
            except FileNotFoundError:
                body = render("")
            questions = []
        elif kind == "exercise":
            item = deps.exercises().get(item_id)
            course = deps.courses().get(deps.topics().get(item.topic_id).course_id)
            body = None
            questions = deps.questions().get_many([q.question_id for q in sorted(item.questions, key=lambda q: q.position)])
        else:
            item = deps.exams().get(item_id)
            course = deps.courses().get(item.course_id)
            body = None
            with deps.db().session() as session:
                from src.exam.service.ExamService import ExamService

                ids = ExamService.pool_question_ids(session, ExamService.find(session, item_id))
            questions = deps.questions().get_many(ids)
    except ServiceError:
        abort(404)
    rendered = {q.id: (render(q.body_md).html, render(q.explanation_md or "").html) for q in questions}
    return render_template("admin/review.html", kind=kind, item=item, course=course, body=body, questions=questions,
                           rendered=rendered, error=error)


@bp.post("/applications/<int:application_id>")
def review(application_id: int):
    decision = request.form.get("decision")
    try:
        if decision == "approve":
            deps.lecturer_apps().approve(application_id, reviewer_id=g.user.id)
            flash("Approved: the account is now a lecturer.")
        elif decision == "reject":
            deps.lecturer_apps().reject(application_id, reviewer_id=g.user.id)
            flash("Request rejected.")
        else:
            abort(400)
    except LecturerApplicationNotFoundError:
        abort(404)
    except LecturerApplicationReviewedError as exc:
        flash(str(exc))
    return redirect(url_for("admin.index"))


@bp.post("/users/<int:user_id>/roles")
def set_roles(user_id: int):
    roles = [UserRole.STUDENT]
    if request.form.get("lecturer") == "on":
        roles.append(UserRole.LECTURER)
    try:
        user = deps.users().set_roles(user_id, UserRolesUpdate(roles=roles))
    except UserNotFoundError:
        abort(404)
    flash(f"{user.name} is now: {', '.join(r.value for r in user.roles)}.")
    return redirect(url_for("admin.index", q=request.form.get("q", "")))

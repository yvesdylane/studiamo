"""Services for the pages, and who may edit what.

    notes().read_content(7)
    require_course_editor(course)      # 404 unless the session may edit this course

Editing rights: an admin session may edit every course. A lecturer may edit
the courses they created. Students never edit.
"""

from __future__ import annotations

from flask import abort, current_app, g

from src.attempt.service.AttemptService import AttemptService
from src.course.dto.CourseDto import CourseRead
from src.course.service.CourseErrors import CourseNotFoundError
from src.course.service.CourseService import CourseService
from src.course.service.NoteService import NoteService
from src.course.service.TopicService import TopicService
from src.db.DB import Db
from src.exam.service.ExamService import ExamService
from src.exercise.service.ExerciseService import ExerciseService
from src.player.service.PlayerService import PlayerService
from src.progress.service.ProgressService import ProgressService
from src.question.service.QuestionService import QuestionService
from src.storage.MarkdownStore import LocalMarkdownStore, MarkdownStore
from src.user.service.LecturerApplicationService import LecturerApplicationService
from src.user.service.UserService import UserService


def db() -> Db:
    return current_app.extensions["db"]


def store() -> MarkdownStore:
    if "md_store" not in current_app.extensions:
        current_app.extensions["md_store"] = LocalMarkdownStore.from_env()
    return current_app.extensions["md_store"]


def courses() -> CourseService: return CourseService(db())
def topics() -> TopicService: return TopicService(db())
def notes() -> NoteService: return NoteService(db(), store())
def questions() -> QuestionService: return QuestionService(db())
def exercises() -> ExerciseService: return ExerciseService(db())
def exams() -> ExamService: return ExamService(db())
def attempts() -> AttemptService: return AttemptService(db(), store(), current_app.extensions.get("code_runner"))
def progress() -> ProgressService: return ProgressService(db())
def players() -> PlayerService: return PlayerService(db())
def users() -> UserService: return UserService(db())
def lecturer_apps() -> LecturerApplicationService: return LecturerApplicationService(db())
def review():
    from src.review.ReviewService import ReviewService

    return ReviewService(db())


def converter():
    from src.ai.NoteConverter import NoteConverter

    return NoteConverter(db(), current_app.config["SECRET_KEY"])


def is_admin() -> bool:
    return bool(g.get("is_admin"))


# ------------------------------------------------------------ permissions
def can_author() -> bool:
    """May this session open the studio at all (lecturer or admin)?"""
    return bool({"lecturer", "admin"} & g.get("roles", frozenset()))


def can_edit_course(course: CourseRead) -> bool:
    if g.get("user") is None:
        return False
    if g.get("is_admin"):
        return True
    return "lecturer" in g.roles and course.created_by == g.user.id


def course_or_none(course_id: int) -> CourseRead | None:
    try:
        return courses().get(course_id)
    except CourseNotFoundError:
        return None


def course_or_404(course_id: int) -> CourseRead:
    try:
        return courses().get(course_id)
    except CourseNotFoundError:
        abort(404)


def require_course_editor(course_id: int) -> CourseRead:
    """The course, if this session may edit it; 404 otherwise (does not reveal it)."""
    course = course_or_404(course_id)
    if not can_edit_course(course):
        abort(404)
    return course

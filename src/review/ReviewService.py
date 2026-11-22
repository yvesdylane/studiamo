"""Admin review of what lecturers write (migration 0008).

    review = ReviewService(get_db())
    review.submit("note", 7)                         # lecturer: draft/rejected -> pending
    review.withdraw("note", 7)                       # lecturer: pending -> draft
    review.pending()                                 # admin's queue, oldest first
    review.approve("note", 7, reviewer_id=1, max_exp=30)   # admin: -> approved + published
    review.reject("note", 7, reviewer_id=1, comment="Fix the diagram")
    review.reopen_after_edit("note", 7)              # a lecturer changed approved content
    review.publish_direct("exam", 3, published=True) # admins, and lecturers' scheduled exams

kinds: "note" (topic_notes), "exercise", "exam". Only admins approve, reject
and set exp; the routes check who is asking (services never check roles).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from src.base.ServiceErrors import InvalidOperationError, NotFoundError, ServiceError
from src.course.modles.Course import Course
from src.course.modles.Note import Note
from src.course.modles.Topic import Topic
from src.db.DB import Db
from src.exam.models.Exam import Exam
from src.exam.service.ExamErrors import ExamInvalidError
from src.exam.service.ExamService import ExamService
from src.exercise.models.Exercise import Exercise

Kind = Literal["note", "exercise", "exam"]
MODELS = {"note": Note, "exercise": Exercise, "exam": Exam}


class ReviewStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewError(ServiceError):
    pass


class ReviewItemNotFoundError(ReviewError, NotFoundError):
    def __init__(self, kind: str, item_id: int) -> None:
        super().__init__(f"{kind} {item_id} does not exist")


class ReviewStateError(ReviewError, InvalidOperationError):
    def __init__(self, kind: str, status: str, action: str) -> None:
        super().__init__(f"This {kind} is {status}: it cannot be {action}")


@dataclass(frozen=True)
class PendingItem:
    kind: str
    id: int
    title: str
    course_id: int
    course_code: str
    course_title: str
    author_id: int | None
    submitted_at: datetime | None
    max_exp: int


def _now() -> datetime:
    return datetime.now(UTC)


class ReviewService:
    def __init__(self, db: Db) -> None:
        self._db = db

    # ---------------------------------------------------------- lecturer
    def submit(self, kind: Kind, item_id: int) -> ReviewStatus:
        with self._db.session() as session:
            item = self._find(session, kind, item_id)
            if item.review_status not in (ReviewStatus.DRAFT, ReviewStatus.REJECTED):
                raise ReviewStateError(kind, item.review_status, "submitted")
            item.review_status = ReviewStatus.PENDING.value
            item.submitted_at = _now()
            return ReviewStatus.PENDING

    def withdraw(self, kind: Kind, item_id: int) -> ReviewStatus:
        with self._db.session() as session:
            item = self._find(session, kind, item_id)
            if item.review_status != ReviewStatus.PENDING:
                raise ReviewStateError(kind, item.review_status, "withdrawn")
            item.review_status = ReviewStatus.DRAFT.value
            return ReviewStatus.DRAFT

    def hide(self, kind: Kind, item_id: int) -> None:
        """A lecturer takes their content down: unpublished, back to draft
        (publishing it again goes through review)."""
        with self._db.session() as session:
            item = self._find(session, kind, item_id)
            item.published = False
            item.review_status = ReviewStatus.DRAFT.value

    def reopen_after_edit(self, kind: Kind, item_id: int) -> bool:
        """A lecturer changed approved content: unpublish it and put it back in
        the queue. True if that happened (so the page can say so)."""
        with self._db.session() as session:
            item = self._find(session, kind, item_id)
            if item.review_status != ReviewStatus.APPROVED or not item.published:
                return False
            item.review_status = ReviewStatus.PENDING.value
            item.published = False
            item.submitted_at = _now()
            return True

    def reopen_for_question(self, question_id: int, topic_id: int | None) -> int:
        """A lecturer changed a question: approved exercises that use it, and
        approved practice exams drawing from its topic, go back to review."""
        from src.exam.models.Exam import ExamKind
        from src.exam.models.ExamTopic import ExamTopic
        from src.exercise.models.ExerciseQuestion import ExerciseQuestion

        with self._db.session() as session:
            items = list(session.scalars(
                sa.select(Exercise).join(ExerciseQuestion, ExerciseQuestion.exercise_id == Exercise.id)
                .where(ExerciseQuestion.question_id == question_id, Exercise.review_status == ReviewStatus.APPROVED.value,
                       Exercise.published)))
            if topic_id is not None:
                items += list(session.scalars(
                    sa.select(Exam).join(ExamTopic, ExamTopic.exam_id == Exam.id)
                    .where(ExamTopic.topic_id == topic_id, Exam.kind == ExamKind.SYSTEM,
                           Exam.review_status == ReviewStatus.APPROVED.value, Exam.published)))
            for item in items:
                item.review_status = ReviewStatus.PENDING.value
                item.published = False
                item.submitted_at = _now()
            return len(items)

    # ------------------------------------------------------------- admin
    def pending(self) -> list[PendingItem]:
        out: list[PendingItem] = []
        with self._db.session() as session:
            for kind, model in MODELS.items():
                q = sa.select(model, Course).where(model.review_status == ReviewStatus.PENDING.value)
                if kind == "exam":
                    q = q.join(Course, Course.id == Exam.course_id)
                else:
                    q = q.join(Topic, Topic.id == model.topic_id).join(Course, Course.id == Topic.course_id)
                for item, course in session.execute(q).all():
                    out.append(PendingItem(kind, item.id, item.title, course.id, course.code, course.title,
                                           course.created_by, item.submitted_at, item.max_exp))
        return sorted(out, key=lambda p: (p.submitted_at or _now(), p.kind, p.id))

    def approve(self, kind: Kind, item_id: int, reviewer_id: int, max_exp: int | None = None) -> None:
        with self._db.session() as session:
            item = self._find(session, kind, item_id, for_update=True)
            if max_exp is not None:
                if max_exp < 0:
                    raise ReviewStateError(kind, "given negative exp", "approved")
                item.max_exp = max_exp
            if kind == "exam":
                self._check_exam(session, item)
            item.review_status = ReviewStatus.APPROVED.value
            item.review_comment = None
            item.reviewed_by, item.reviewed_at = reviewer_id, _now()
            item.published = True

    def reject(self, kind: Kind, item_id: int, reviewer_id: int, comment: str) -> None:
        with self._db.session() as session:
            item = self._find(session, kind, item_id, for_update=True)
            item.review_status = ReviewStatus.REJECTED.value
            item.review_comment = (comment or "").strip() or "Please revise."
            item.reviewed_by, item.reviewed_at = reviewer_id, _now()
            item.published = False

    def publish_direct(self, kind: Kind, item_id: int, *, published: bool, max_exp: int | None = None) -> None:
        """Publish or unpublish without the queue (admins; lecturers for their
        own scheduled exams)."""
        with self._db.session() as session:
            item = self._find(session, kind, item_id, for_update=True)
            if max_exp is not None and max_exp >= 0:
                item.max_exp = max_exp
            if published:
                if kind == "exam":
                    self._check_exam(session, item)
                item.review_status = ReviewStatus.APPROVED.value
            item.published = published

    # ----------------------------------------------------------- helpers
    @staticmethod
    def _check_exam(session: Session, exam: Exam) -> None:
        pool = ExamService.pool_question_ids(session, exam)
        if not pool:
            raise ExamInvalidError("the exam's pool has no active questions")
        if exam.questions_per_attempt is not None and exam.questions_per_attempt > len(pool):
            raise ExamInvalidError(f"questions_per_attempt is {exam.questions_per_attempt} but the pool has {len(pool)}")

    @staticmethod
    def _find(session: Session, kind: str, item_id: int, *, for_update: bool = False):
        model = MODELS.get(kind)
        item = session.get(model, item_id, with_for_update=for_update) if model else None
        if item is None:
            raise ReviewItemNotFoundError(kind, item_id)
        return item

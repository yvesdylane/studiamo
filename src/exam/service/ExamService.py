"""Exams of a course.

  scheduled  made by a lecturer, explicit question pool (exam_questions), one
             attempt, only between opens_at and closes_at
  system     pool = every active question of the chosen topics (exam_topics),
             open any time, unlimited attempts unless max_attempts is set

    service = ExamService(get_db())
    exam = service.create(ExamCreate(course_id=1, title="Midterm", kind=ExamKind.SCHEDULED,
                                     opens_at=..., closes_at=..., question_ids=[1, 2, 3]),
                          created_by=lecturer.id)
    exam = service.create(ExamCreate(course_id=1, title="Practice", kind=ExamKind.SYSTEM,
                                     topic_ids=[4, 5], questions_per_attempt=10))
    open_now = service.list_available(course_id=1)      # what a student can take now

Taking an exam is AttemptService.start_exam / submit_exam.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.course.modles.Course import Course
from src.course.modles.Topic import Topic
from src.course.service.CourseErrors import CourseNotFoundError
from src.course.service.TopicErrors import TopicNotFoundError
from src.db.DB import Db
from src.exam.dto.ExamDto import ExamCreate, ExamPublic, ExamRead, ExamUpdate
from src.exam.dto.ExamQuestionDto import ExamQuestionBase
from src.exam.models.Exam import Exam, ExamKind
from src.exam.models.ExamQuestion import ExamQuestion
from src.exam.models.ExamTopic import ExamTopic
from src.exam.service.ExamErrors import (
    ExamInvalidError,
    ExamNotFoundError,
    ExamNotPublishedError,
    ExamTopicOutsideCourseError,
)
from src.question.models.Question import Question
from src.question.service.QuestionErrors import QuestionNotFoundError


def _now() -> datetime:
    return datetime.now(UTC)


def is_open(exam: Exam | ExamRead | ExamPublic, at: datetime | None = None) -> bool:
    """True when a student may start the exam at `at` (default: now)."""
    if not exam.published:
        return False
    if exam.kind == ExamKind.SYSTEM:
        return True
    at = at or _now()
    return exam.opens_at <= at < exam.closes_at


class ExamService:
    def __init__(self, db: Db) -> None:
        self._db = db

    # ------------------------------------------------------------- create
    def create(self, data: ExamCreate, created_by: int | None = None) -> ExamRead:
        """created_by is the logged-in lecturer (required for scheduled exams).
        System exams are stored without an author."""
        if data.kind == ExamKind.SCHEDULED and created_by is None:
            raise ExamInvalidError("scheduled exams need the lecturer who creates them")
        try:
            with self._db.session() as session:
                if session.get(Course, data.course_id) is None:
                    raise CourseNotFoundError(data.course_id)
                exam = Exam(
                    **data.model_dump(exclude={"question_ids", "topic_ids"}),
                    created_by=created_by if data.kind == ExamKind.SCHEDULED else None,
                )
                session.add(exam)
                session.flush()
                self._write_pool(
                    session,
                    exam,
                    [ExamQuestionBase(question_id=q) for q in data.question_ids],
                    data.topic_ids,
                )
                session.flush()
                session.refresh(exam)
                return ExamRead.model_validate(exam)
        except IntegrityError as exc:
            raise ExamInvalidError(f"exam settings rejected by the database: {exc.orig}") from exc

    # --------------------------------------------------------------- read
    def get(self, exam_id: int) -> ExamRead:
        """Lecturer view, with the pool."""
        with self._db.session() as session:
            return ExamRead.model_validate(self._find(session, exam_id))

    def get_public(self, exam_id: int) -> ExamPublic:
        """Student view: ExamNotPublishedError for drafts."""
        with self._db.session() as session:
            exam = self._find(session, exam_id)
            if not exam.published:
                raise ExamNotPublishedError(exam_id)
            return ExamPublic.model_validate(exam)

    def list_for_course(
        self, course_id: int, *, kind: ExamKind | None = None, published_only: bool = False
    ) -> list[ExamRead]:
        with self._db.session() as session:
            if session.get(Course, course_id) is None:
                raise CourseNotFoundError(course_id)
            query = sa.select(Exam).where(Exam.course_id == course_id)
            if kind is not None:
                query = query.where(Exam.kind == kind)
            if published_only:
                query = query.where(Exam.published)
            exams = session.scalars(query.order_by(Exam.opens_at.nulls_first(), Exam.id)).all()
            return [ExamRead.model_validate(e) for e in exams]

    def list_available(self, course_id: int | None = None, at: datetime | None = None) -> list[ExamPublic]:
        """Published exams a student can start at `at` (default now), optionally for one course."""
        at = at or _now()
        query = sa.select(Exam).where(
            Exam.published,
            sa.or_(
                Exam.kind == ExamKind.SYSTEM,
                sa.and_(Exam.opens_at <= at, Exam.closes_at > at),
            ),
        )
        if course_id is not None:
            query = query.where(Exam.course_id == course_id)
        with self._db.session() as session:
            exams = session.scalars(query.order_by(Exam.closes_at.nulls_last(), Exam.id)).all()
            return [ExamPublic.model_validate(e) for e in exams]

    def list_upcoming(self, course_id: int | None = None, at: datetime | None = None) -> list[ExamPublic]:
        """Published scheduled exams that have not opened yet."""
        at = at or _now()
        query = sa.select(Exam).where(Exam.published, Exam.kind == ExamKind.SCHEDULED, Exam.opens_at > at)
        if course_id is not None:
            query = query.where(Exam.course_id == course_id)
        with self._db.session() as session:
            exams = session.scalars(query.order_by(Exam.opens_at, Exam.id)).all()
            return [ExamPublic.model_validate(e) for e in exams]

    def pool_size(self, exam_id: int) -> int:
        with self._db.session() as session:
            return len(self.pool_question_ids(session, self._find(session, exam_id)))

    # ------------------------------------------------------------- update
    def update(self, exam_id: int, data: ExamUpdate) -> ExamRead:
        """Partial update: None means "leave it". The kind cannot change.

        For a system exam, opens_at, closes_at and duration_minutes can be
        cleared by sending None explicitly.
        """
        sent = data.model_fields_set
        try:
            with self._db.session() as session:
                exam = self._find(session, exam_id)
                for field, value in data.model_dump(exclude_none=True).items():
                    setattr(exam, field, value)
                if exam.kind == ExamKind.SYSTEM:
                    for field in ("opens_at", "closes_at", "duration_minutes"):
                        if field in sent and getattr(data, field) is None:
                            setattr(exam, field, None)
                if "questions_per_attempt" in sent and data.questions_per_attempt is None:
                    exam.questions_per_attempt = None
                self._check_rules(session, exam)
                session.flush()
                return ExamRead.model_validate(exam)
        except IntegrityError as exc:
            raise ExamInvalidError(f"exam settings rejected by the database: {exc.orig}") from exc

    def set_questions(self, exam_id: int, questions: list[ExamQuestionBase]) -> ExamRead:
        """Replace the pool of a scheduled exam (points override optional)."""
        with self._db.session() as session:
            exam = self._find(session, exam_id)
            if exam.kind != ExamKind.SCHEDULED:
                raise ExamInvalidError("system exams take their questions from topics: use set_topics")
            if not questions:
                raise ExamInvalidError("scheduled exams need at least one question")
            session.execute(sa.delete(ExamQuestion).where(ExamQuestion.exam_id == exam_id))
            self._write_pool(session, exam, questions, [])
            session.flush()
            session.expire(exam, ["questions"])
            self._check_rules(session, exam)
            return ExamRead.model_validate(exam)

    def set_topics(self, exam_id: int, topic_ids: list[int]) -> ExamRead:
        """Replace the topics a system exam draws its questions from."""
        with self._db.session() as session:
            exam = self._find(session, exam_id)
            if exam.kind != ExamKind.SYSTEM:
                raise ExamInvalidError("scheduled exams have an explicit pool: use set_questions")
            if not topic_ids:
                raise ExamInvalidError("system exams need at least one topic")
            session.execute(sa.delete(ExamTopic).where(ExamTopic.exam_id == exam_id))
            self._write_pool(session, exam, [], topic_ids)
            session.flush()
            session.expire(exam, ["topics"])
            return ExamRead.model_validate(exam)

    def publish(self, exam_id: int) -> ExamRead:
        """Publish after checking the pool can fill an attempt."""
        with self._db.session() as session:
            exam = self._find(session, exam_id)
            pool = self.pool_question_ids(session, exam)
            if not pool:
                raise ExamInvalidError("the exam's pool has no active questions")
            if exam.questions_per_attempt is not None and exam.questions_per_attempt > len(pool):
                raise ExamInvalidError(
                    f"questions_per_attempt is {exam.questions_per_attempt} but the pool has {len(pool)}"
                )
            exam.published = True
            session.flush()
            return ExamRead.model_validate(exam)

    def unpublish(self, exam_id: int) -> ExamRead:
        return self.update(exam_id, ExamUpdate(published=False))

    # ------------------------------------------------------------- delete
    def delete(self, exam_id: int) -> None:
        """Delete an exam with its attempts and marks. Exp already earned stays."""
        with self._db.session() as session:
            deleted = session.execute(sa.delete(Exam).where(Exam.id == exam_id)).rowcount
            if not deleted:
                raise ExamNotFoundError(exam_id)

    # ------------------------------------------------- used by attempts
    @staticmethod
    def pool_question_ids(session: Session, exam: Exam) -> list[int]:
        """Question ids an attempt can draw from, ordered by id.

        scheduled: the explicit pool, minus deactivated questions
        system:    active questions of the exam's topics
        """
        if exam.kind == ExamKind.SCHEDULED:
            query = (
                sa.select(Question.id)
                .join(ExamQuestion, ExamQuestion.question_id == Question.id)
                .where(ExamQuestion.exam_id == exam.id)
            )
        else:
            query = (
                sa.select(Question.id)
                .join(ExamTopic, ExamTopic.topic_id == Question.topic_id)
                .where(ExamTopic.exam_id == exam.id)
            )
        return list(session.scalars(query.where(Question.is_active).order_by(Question.id)))

    @staticmethod
    def find(session: Session, exam_id: int, *, for_update: bool = False) -> Exam:
        exam = session.get(Exam, exam_id, with_for_update=for_update)
        if exam is None:
            raise ExamNotFoundError(exam_id)
        return exam

    # ------------------------------------------------------------ helpers
    _find = find

    def _check_rules(self, session: Session, exam: Exam) -> None:
        """Same rules as ExamBase, on the merged (stored + updated) values."""
        if exam.opens_at and exam.closes_at and exam.closes_at <= exam.opens_at:
            raise ExamInvalidError("closes_at must be after opens_at")
        if exam.kind == ExamKind.SCHEDULED:
            if exam.opens_at is None or exam.closes_at is None:
                raise ExamInvalidError("scheduled exams need opens_at and closes_at")
            pool = session.scalar(
                sa.select(sa.func.count()).select_from(ExamQuestion).where(ExamQuestion.exam_id == exam.id)
            )
            if exam.questions_per_attempt is not None and exam.questions_per_attempt > pool:
                raise ExamInvalidError("questions_per_attempt is larger than the pool")

    @staticmethod
    def _write_pool(
        session: Session,
        exam: Exam,
        questions: Sequence[ExamQuestionBase],
        topic_ids: Sequence[int],
    ) -> None:
        q_ids = [q.question_id for q in questions]
        if len(q_ids) != len(set(q_ids)) or len(topic_ids) != len(set(topic_ids)):
            raise ExamInvalidError("the pool lists the same question or topic twice")
        if q_ids:
            found = set(session.scalars(sa.select(Question.id).where(Question.id.in_(q_ids))))
            missing = [i for i in q_ids if i not in found]
            if missing:
                raise QuestionNotFoundError(missing[0])
        if topic_ids:
            course_of = dict(
                session.execute(sa.select(Topic.id, Topic.course_id).where(Topic.id.in_(topic_ids))).tuples().all()
            )
            for tid in topic_ids:
                if tid not in course_of:
                    raise TopicNotFoundError(tid)
                if course_of[tid] != exam.course_id:
                    raise ExamTopicOutsideCourseError(tid, exam.course_id)
        session.add_all(
            ExamQuestion(exam_id=exam.id, question_id=q.question_id, points=q.points) for q in questions
        )
        session.add_all(ExamTopic(exam_id=exam.id, topic_id=t) for t in topic_ids)


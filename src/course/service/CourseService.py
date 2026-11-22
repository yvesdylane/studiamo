"""CRUD for courses.

Objects in, objects out: every method takes DTOs (CourseCreate, CourseUpdate)
and returns CourseRead, never dicts or ORM rows, so callers never touch the
session.

    service = CourseService(get_db())
    course  = service.create(CourseCreate(code="IGL236", title="Networks"), created_by=user.id)
    course  = service.get(course.id)
    courses = service.list(limit=20, offset=0)
    course  = service.update(course.id, CourseUpdate(title="Computer Networks"))
    service.delete(course.id)
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.course.dto.CourseDto import CourseCatalogItem, CourseCreate, CourseRead, CourseUpdate
from src.course.modles.Course import Course
from src.course.service.CourseErrors import (
    CourseCodeTakenError,
    CourseError,
    CourseCreatorNotFoundError,
    CourseNotFoundError,
)
from src.db.DB import Db

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# Constraint names Postgres gave the constraints in the initial migration.
# Used to tell which rule an insert/update broke.
UNIQUE_CODE_CONSTRAINT = "courses_code_key"
CREATED_BY_FK_CONSTRAINT = "courses_created_by_fkey"


class CourseService:
    def __init__(self, db: Db) -> None:
        self._db = db

    # ------------------------------------------------------------- create
    def create(self, data: CourseCreate, created_by: int) -> CourseRead:
        """Insert a course. created_by is the logged-in user's id, never client input."""
        course = Course(
            code=data.code,
            title=data.title,
            description=data.description,
            created_by=created_by,
        )
        try:
            with self._db.session() as session:
                session.add(course)
                session.flush()          # INSERT now, so constraint errors surface here
                session.refresh(course)  # load id and created_at set by the database
                return CourseRead.model_validate(course)
        except IntegrityError as exc:
            error = self._translate(exc, code=data.code, user_id=created_by)
            if error is None:
                raise
            raise error from exc

    # --------------------------------------------------------------- read
    def get(self, course_id: int) -> CourseRead:
        with self._db.session() as session:
            course = self._find(session, course_id)
            return CourseRead.model_validate(course)

    def get_by_code(self, code: str) -> CourseRead | None:
        with self._db.session() as session:
            course = session.scalars(sa.select(Course).where(Course.code == code)).first()
            return CourseRead.model_validate(course) if course is not None else None

    def list(self, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0) -> list[CourseRead]:
        """Courses ordered by code. limit is capped at MAX_PAGE_SIZE."""
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)
        with self._db.session() as session:
            courses = session.scalars(
                sa.select(Course).order_by(Course.code).limit(limit).offset(offset)
            ).all()
            return [CourseRead.model_validate(course) for course in courses]

    def count(self) -> int:
        with self._db.session() as session:
            return session.scalar(sa.select(sa.func.count()).select_from(Course)) or 0

    def catalog(self, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0) -> list[CourseCatalogItem]:
        """Courses ordered by code, each with its number of published notes,
        exercises and exams (what students can open right now)."""
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)
        sql = sa.text(
            """
            select c.id, c.code, c.title, c.description,
                   (select count(*) from topic_notes n join course_topics t on t.id = n.topic_id
                     where t.course_id = c.id and n.published) as published_notes,
                   (select count(*) from exercises e join course_topics t on t.id = e.topic_id
                     where t.course_id = c.id and e.published) as published_exercises,
                   (select count(*) from exams x where x.course_id = c.id and x.published) as published_exams
              from courses c
             order by c.code
             limit :limit offset :offset
            """
        )
        with self._db.session() as session:
            rows = session.execute(sql, {"limit": limit, "offset": offset}).mappings().all()
            return [CourseCatalogItem.model_validate(dict(r)) for r in rows]

    # ------------------------------------------------------------- update
    def update(self, course_id: int, data: CourseUpdate) -> CourseRead:
        """Partial update: only fields present in data change.

        code and title cannot be null, so None means "leave it". description can
        be cleared, so it is changed only when the caller actually sent it
        (CourseUpdate(description=None) clears it, CourseUpdate() keeps it).
        """
        try:
            with self._db.session() as session:
                course = self._find(session, course_id)

                if data.code is not None:
                    course.code = data.code
                if data.title is not None:
                    course.title = data.title
                if "description" in data.model_fields_set:
                    course.description = data.description

                session.flush()
                return CourseRead.model_validate(course)
        except IntegrityError as exc:
            error = self._translate(exc, code=data.code)
            if error is None:
                raise
            raise error from exc

    # ------------------------------------------------------------- delete
    def delete(self, course_id: int) -> None:
        """Delete a course. Its topics, notes and exams go with it (ON DELETE CASCADE)."""
        with self._db.session() as session:
            course = self._find(session, course_id)
            session.delete(course)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _find(session: Session, course_id: int) -> Course:
        course = session.get(Course, course_id)
        if course is None:
            raise CourseNotFoundError(course_id)
        return course

    @staticmethod
    def _translate(exc: IntegrityError, *, code: str | None = None, user_id: int | None = None) -> CourseError | None:
        """The CourseError matching a constraint error, or None if it is another one."""
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == UNIQUE_CODE_CONSTRAINT and code is not None:
            return CourseCodeTakenError(code)
        if constraint == CREATED_BY_FK_CONSTRAINT and user_id is not None:
            return CourseCreatorNotFoundError(user_id)
        return None

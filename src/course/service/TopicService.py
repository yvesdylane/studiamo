"""CRUD for course topics (the chapters of a course).

    service = TopicService(get_db())
    topic   = service.create(TopicCreate(course_id=1, title="TCP/IP", position=0))
    topics  = service.list_for_course(1)
    service.reorder(1, [t3.id, t1.id, t2.id])     # positions become 0, 1, 2
    service.delete(topic.id)                       # its notes and exercises go too

Positions are unique inside a course (TopicPositionTakenError).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.base.ServiceErrors import constraint_name, reraise
from src.course.dto.TopicDto import TopicCreate, TopicRead, TopicUpdate
from src.course.modles.Course import Course
from src.course.modles.Topic import Topic
from src.course.service.CourseErrors import CourseNotFoundError
from src.course.service.TopicErrors import (
    TopicError,
    TopicNotFoundError,
    TopicOrderMismatchError,
    TopicPositionTakenError,
)
from src.db.DB import Db

UNIQUE_POSITION_CONSTRAINT = "course_topics_course_id_position_key"


class TopicService:
    def __init__(self, db: Db) -> None:
        self._db = db

    def create(self, data: TopicCreate) -> TopicRead:
        topic = Topic(course_id=data.course_id, title=data.title, position=data.position)
        try:
            with self._db.session() as session:
                if session.get(Course, data.course_id) is None:
                    raise CourseNotFoundError(data.course_id)
                session.add(topic)
                session.flush()
                return TopicRead.model_validate(topic)
        except IntegrityError as exc:
            reraise(exc, self._translate(exc, data.course_id, data.position))

    def get(self, topic_id: int) -> TopicRead:
        with self._db.session() as session:
            return TopicRead.model_validate(self._find(session, topic_id))

    def list_for_course(self, course_id: int) -> list[TopicRead]:
        """Topics of a course ordered by position. CourseNotFoundError if the course is missing."""
        with self._db.session() as session:
            if session.get(Course, course_id) is None:
                raise CourseNotFoundError(course_id)
            topics = session.scalars(
                sa.select(Topic).where(Topic.course_id == course_id).order_by(Topic.position)
            ).all()
            return [TopicRead.model_validate(t) for t in topics]

    def update(self, topic_id: int, data: TopicUpdate) -> TopicRead:
        course_id = None
        try:
            with self._db.session() as session:
                topic = self._find(session, topic_id)
                course_id = topic.course_id  # still readable after a rollback
                if data.title is not None:
                    topic.title = data.title
                if data.position is not None:
                    topic.position = data.position
                session.flush()
                return TopicRead.model_validate(topic)
        except IntegrityError as exc:
            reraise(exc, self._translate(exc, course_id, data.position))

    def reorder(self, course_id: int, topic_ids: list[int]) -> list[TopicRead]:
        """Give the course's topics positions 0..n-1 in the order of topic_ids.

        topic_ids must contain every topic of the course exactly once.
        """
        with self._db.session() as session:
            if session.get(Course, course_id) is None:
                raise CourseNotFoundError(course_id)
            topics = {
                t.id: t
                for t in session.scalars(
                    sa.select(Topic).where(Topic.course_id == course_id).with_for_update()
                )
            }
            if len(topic_ids) != len(set(topic_ids)) or set(topic_ids) != set(topics):
                raise TopicOrderMismatchError(course_id)
            # two passes: going straight to the new positions could collide with
            # positions not yet moved (unique (course_id, position))
            for i, tid in enumerate(topic_ids):
                topics[tid].position = -1 - i
            session.flush()
            for i, tid in enumerate(topic_ids):
                topics[tid].position = i
            session.flush()
            return [TopicRead.model_validate(topics[tid]) for tid in topic_ids]

    def delete(self, topic_id: int) -> None:
        """Delete a topic. Its notes, exercises and exam links go with it;
        questions of the bank stay, with topic_id set to null."""
        with self._db.session() as session:
            session.delete(self._find(session, topic_id))

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _find(session: Session, topic_id: int) -> Topic:
        topic = session.get(Topic, topic_id)
        if topic is None:
            raise TopicNotFoundError(topic_id)
        return topic

    @staticmethod
    def _translate(exc: IntegrityError, course_id: int, position: int | None) -> TopicError | None:
        if constraint_name(exc) == UNIQUE_POSITION_CONSTRAINT and position is not None:
            return TopicPositionTakenError(course_id, position)
        return None

"""Errors raised by ExamService."""

from __future__ import annotations

from datetime import datetime

from src.base.ServiceErrors import InvalidOperationError, NotFoundError, ServiceError


class ExamError(ServiceError):
    """Base class: catch this to handle every exam error at once."""


class ExamNotFoundError(ExamError, NotFoundError):
    def __init__(self, exam_id: int) -> None:
        super().__init__(f"Exam {exam_id} does not exist")
        self.exam_id = exam_id


class ExamInvalidError(ExamError, InvalidOperationError):
    """The exam's settings break a rule (window order, pool size, kind rules...)."""


class ExamNotPublishedError(ExamError, InvalidOperationError):
    def __init__(self, exam_id: int) -> None:
        super().__init__(f"Exam {exam_id} is not published")
        self.exam_id = exam_id


class ExamNotOpenError(ExamError, InvalidOperationError):
    def __init__(self, exam_id: int, opens_at: datetime | None, closes_at: datetime | None) -> None:
        super().__init__(f"Exam {exam_id} is open from {opens_at} to {closes_at}, not now")
        self.exam_id = exam_id
        self.opens_at = opens_at
        self.closes_at = closes_at


class ExamTopicOutsideCourseError(ExamError, InvalidOperationError):
    def __init__(self, topic_id: int, course_id: int) -> None:
        super().__init__(f"Topic {topic_id} is not part of course {course_id}")
        self.topic_id = topic_id
        self.course_id = course_id

"""Errors raised by CourseService. Routes turn them into HTTP responses."""

from __future__ import annotations

from src.base.ServiceErrors import ConflictError, NotFoundError, ServiceError


class CourseError(ServiceError):
    """Base class: catch this to handle every course error at once."""


class CourseNotFoundError(CourseError, NotFoundError):
    def __init__(self, course_id: int) -> None:
        super().__init__(f"Course {course_id} does not exist")
        self.course_id = course_id


class CourseCodeTakenError(CourseError, ConflictError):
    def __init__(self, code: str) -> None:
        super().__init__(f"A course with code {code!r} already exists")
        self.code = code


class CourseCreatorNotFoundError(CourseError, NotFoundError):
    def __init__(self, user_id: int) -> None:
        super().__init__(f"User {user_id} does not exist, so they cannot create a course")
        self.user_id = user_id

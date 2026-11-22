"""Errors raised by TopicService and NoteService."""

from __future__ import annotations

from src.base.ServiceErrors import ConflictError, InvalidOperationError, NotFoundError, ServiceError


class TopicError(ServiceError):
    """Base class: catch this to handle every topic / note error at once."""


class TopicNotFoundError(TopicError, NotFoundError):
    def __init__(self, topic_id: int) -> None:
        super().__init__(f"Topic {topic_id} does not exist")
        self.topic_id = topic_id


class TopicPositionTakenError(TopicError, ConflictError):
    def __init__(self, course_id: int, position: int) -> None:
        super().__init__(f"Course {course_id} already has a topic at position {position}")
        self.course_id = course_id
        self.position = position


class TopicOrderMismatchError(TopicError, InvalidOperationError):
    def __init__(self, course_id: int) -> None:
        super().__init__(f"The new order must list every topic of course {course_id} exactly once")
        self.course_id = course_id


class NoteNotFoundError(TopicError, NotFoundError):
    def __init__(self, note_id: int) -> None:
        super().__init__(f"Note {note_id} does not exist")
        self.note_id = note_id


class NoteNotPublishedError(TopicError, InvalidOperationError):
    def __init__(self, note_id: int) -> None:
        super().__init__(f"Note {note_id} is not published")
        self.note_id = note_id


class NoteOrderMismatchError(TopicError, InvalidOperationError):
    def __init__(self, topic_id: int) -> None:
        super().__init__(f"The new order must list every note of topic {topic_id} exactly once")
        self.topic_id = topic_id

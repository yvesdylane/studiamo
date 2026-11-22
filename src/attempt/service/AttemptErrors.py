"""Errors raised by AttemptService."""

from __future__ import annotations

from src.base.ServiceErrors import (
    ConflictError,
    InvalidOperationError,
    NotFoundError,
    PermissionDeniedError,
    ServiceError,
)


class AttemptError(ServiceError):
    """Base class: catch this to handle every attempt error at once."""


class AttemptNotFoundError(AttemptError, NotFoundError):
    def __init__(self, kind: str, attempt_id: int) -> None:
        super().__init__(f"{kind.capitalize()} attempt {attempt_id} does not exist")
        self.kind = kind
        self.attempt_id = attempt_id


class AttemptNotYoursError(AttemptError, PermissionDeniedError):
    def __init__(self, attempt_id: int) -> None:
        super().__init__(f"Attempt {attempt_id} belongs to another student")
        self.attempt_id = attempt_id


class AttemptAlreadySubmittedError(AttemptError, ConflictError):
    def __init__(self, attempt_id: int) -> None:
        super().__init__(f"Attempt {attempt_id} was already submitted")
        self.attempt_id = attempt_id


class AttemptNotAwaitingGradingError(AttemptError, ConflictError):
    def __init__(self, attempt_id: int) -> None:
        super().__init__(f"Attempt {attempt_id} has no answers waiting for manual grading")
        self.attempt_id = attempt_id


class AnswerNotInAttemptError(AttemptError, InvalidOperationError):
    def __init__(self, question_id: int) -> None:
        super().__init__(f"Question {question_id} is not part of this attempt")
        self.question_id = question_id


class ManualGradeInvalidError(AttemptError, InvalidOperationError):
    """Points missing, extra, negative or above what the question is worth."""


class NoQuestionsError(AttemptError, InvalidOperationError):
    def __init__(self, what: str) -> None:
        super().__init__(f"{what} has no active questions to answer")


class ExamAttemptsExhaustedError(AttemptError, ConflictError):
    def __init__(self, exam_id: int, max_attempts: int) -> None:
        super().__init__(f"Exam {exam_id} allows {max_attempts} attempt(s), all used")
        self.exam_id = exam_id
        self.max_attempts = max_attempts

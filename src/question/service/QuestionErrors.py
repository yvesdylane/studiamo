"""Errors raised by QuestionService."""

from __future__ import annotations

from src.base.ServiceErrors import ConflictError, InvalidOperationError, NotFoundError, ServiceError


class QuestionError(ServiceError):
    """Base class: catch this to handle every question error at once."""


class QuestionNotFoundError(QuestionError, NotFoundError):
    def __init__(self, question_id: int) -> None:
        super().__init__(f"Question {question_id} does not exist")
        self.question_id = question_id


class QuestionOptionNotFoundError(QuestionError, NotFoundError):
    def __init__(self, option_id: int) -> None:
        super().__init__(f"Option {option_id} does not exist")
        self.option_id = option_id


class QuestionTestCaseNotFoundError(QuestionError, NotFoundError):
    def __init__(self, test_case_id: int) -> None:
        super().__init__(f"Test case {test_case_id} does not exist")
        self.test_case_id = test_case_id


class QuestionInvalidError(QuestionError, InvalidOperationError):
    """The change would leave the question unusable (e.g. an mcq without a correct option)."""


class QuestionInUseError(QuestionError, ConflictError):
    def __init__(self, question_id: int) -> None:
        super().__init__(
            f"Question {question_id} is used by an exercise, an exam or a past attempt; "
            "deactivate it (is_active=false) instead of deleting it"
        )
        self.question_id = question_id

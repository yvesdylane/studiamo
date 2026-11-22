"""DTOs for QuestionTestCase. ORM model: src/question/QuestionTestCase.py."""

from __future__ import annotations

from pydantic import Field

from src.base.BaseDto import DTO


class QuestionTestCaseBase(DTO):
    input: str = ""
    expected_output: str
    is_hidden: bool = True
    weight: float = Field(default=1, gt=0)


class QuestionTestCaseCreate(QuestionTestCaseBase):
    pass


class QuestionTestCaseUpdate(DTO):
    input: str | None = None
    expected_output: str | None = None
    is_hidden: bool | None = None
    weight: float | None = Field(default=None, gt=0)


class QuestionTestCaseRead(QuestionTestCaseBase):
    id: int
    question_id: int


class QuestionTestCasePublic(DTO):  # only the visible examples
    id: int
    input: str
    expected_output: str

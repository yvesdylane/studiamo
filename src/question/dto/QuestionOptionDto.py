"""DTOs for QuestionOption. ORM model: src/question/QuestionOption.py."""

from __future__ import annotations

from pydantic import Field

from src.base.BaseDto import DTO


class QuestionOptionBase(DTO):
    label: str = Field(min_length=1)
    is_correct: bool = False


class QuestionOptionCreate(QuestionOptionBase):
    pass


class QuestionOptionUpdate(DTO):
    label: str | None = Field(default=None, min_length=1)
    is_correct: bool | None = None


class QuestionOptionRead(QuestionOptionBase):  # author view, shows the answer
    id: int
    question_id: int


class QuestionOptionPublic(DTO):  # student view, no is_correct
    id: int
    label: str

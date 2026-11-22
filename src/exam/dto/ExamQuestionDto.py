"""DTOs for ExamQuestion. ORM model: src/exam/ExamQuestion.py."""

from __future__ import annotations

from pydantic import Field

from src.base.BaseDto import DTO


class ExamQuestionBase(DTO):
    question_id: int
    points: float | None = Field(default=None, gt=0)


class ExamQuestionRead(ExamQuestionBase):
    exam_id: int

"""DTOs for ExerciseQuestion. ORM model: src/exercise/ExerciseQuestion.py."""

from __future__ import annotations

from pydantic import Field

from src.base.BaseDto import DTO


class ExerciseQuestionBase(DTO):
    question_id: int
    position: int = Field(ge=0)


class ExerciseQuestionRead(ExerciseQuestionBase):
    exercise_id: int

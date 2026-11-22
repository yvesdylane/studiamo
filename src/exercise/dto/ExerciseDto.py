"""DTOs for Exercise. ORM model: src/exercise/Exercise.py."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from src.base.BaseDto import DTO
from src.exercise.dto.ExerciseQuestionDto import ExerciseQuestionBase, ExerciseQuestionRead


class ExerciseBase(DTO):
    title: str = Field(min_length=1, max_length=200)
    max_exp: int = Field(default=0, ge=0)
    retake_decay: float = Field(default=0.5, ge=0, le=1)  # exp multiplier per extra attempt
    published: bool = False


class ExerciseCreate(ExerciseBase):
    topic_id: int
    questions: list[ExerciseQuestionBase] = Field(default_factory=list)


class ExerciseUpdate(DTO):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    max_exp: int | None = Field(default=None, ge=0)
    retake_decay: float | None = Field(default=None, ge=0, le=1)
    published: bool | None = None


class ExerciseRead(ExerciseBase):
    id: int
    topic_id: int
    review_status: str = "draft"        # draft | pending | approved | rejected
    review_comment: str | None = None
    submitted_at: datetime | None = None
    questions: list[ExerciseQuestionRead] = Field(default_factory=list)

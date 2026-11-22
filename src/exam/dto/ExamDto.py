"""DTOs for Exam. ORM model: src/exam/Exam.py."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from src.base.BaseDto import DTO
from src.exam.models.Exam import ExamKind
from src.exam.dto.ExamQuestionDto import ExamQuestionRead
from src.exam.dto.ExamTopicDto import ExamTopicRead


class ExamBase(DTO):
    title: str = Field(min_length=1, max_length=200)
    kind: ExamKind
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    questions_per_attempt: int | None = Field(default=None, gt=0)  # None = whole pool
    shuffle_questions: bool = True
    shuffle_options: bool = True
    max_attempts: int | None = Field(default=None, gt=0)  # None = unlimited
    max_exp: int = Field(default=0, ge=0)
    published: bool = False

    @model_validator(mode="after")
    def _kind_rules(self):
        if self.opens_at and self.closes_at and self.closes_at <= self.opens_at:
            raise ValueError("closes_at must be after opens_at")
        if self.kind == ExamKind.SCHEDULED:
            if self.opens_at is None or self.closes_at is None:
                raise ValueError("scheduled exams need opens_at and closes_at")
            if self.max_attempts is None:
                self.max_attempts = 1
            elif self.max_attempts != 1:
                raise ValueError("scheduled exams allow exactly one attempt")
        return self


class ExamCreate(ExamBase):
    """created_by comes from the session; system exams are created by the service."""

    course_id: int
    question_ids: list[int] = Field(default_factory=list)  # pool of a scheduled exam
    topic_ids: list[int] = Field(default_factory=list)  # pool source of a system exam

    @model_validator(mode="after")
    def _pool_rules(self):
        if self.kind == ExamKind.SCHEDULED:
            if not self.question_ids:
                raise ValueError("scheduled exams need question_ids (the pool)")
            if self.topic_ids:
                raise ValueError("scheduled exams use question_ids, not topic_ids")
            if (
                self.questions_per_attempt is not None
                and self.questions_per_attempt > len(self.question_ids)
            ):
                raise ValueError("questions_per_attempt is larger than the pool")
        else:
            if not self.topic_ids:
                raise ValueError("system exams need topic_ids (the pool source)")
            if self.question_ids:
                raise ValueError("system exams use topic_ids, not question_ids")
        return self


class ExamUpdate(DTO):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    questions_per_attempt: int | None = Field(default=None, gt=0)
    shuffle_questions: bool | None = None
    shuffle_options: bool | None = None
    max_exp: int | None = Field(default=None, ge=0)
    published: bool | None = None

    @model_validator(mode="after")
    def _window_order(self):
        if self.opens_at and self.closes_at and self.closes_at <= self.opens_at:
            raise ValueError("closes_at must be after opens_at")
        return self


class ExamRead(ExamBase):  # lecturer view, includes the pool
    id: int
    course_id: int
    created_by: int | None = None
    review_status: str = "draft"        # draft | pending | approved | rejected
    review_comment: str | None = None
    submitted_at: datetime | None = None
    questions: list[ExamQuestionRead] = Field(default_factory=list)
    topics: list[ExamTopicRead] = Field(default_factory=list)


class ExamPublic(DTO):  # student view, no pool
    id: int
    course_id: int
    title: str
    kind: ExamKind
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    duration_minutes: int | None = None
    questions_per_attempt: int | None = None
    max_attempts: int | None = None
    max_exp: int
    published: bool

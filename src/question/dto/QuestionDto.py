"""DTOs for Question. ORM model: src/question/Question.py."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from src.base.BaseDto import DTO
from src.question.models.Question import CodeLanguage, QuestionType
from src.question.dto.QuestionOptionDto import (
    QuestionOptionCreate,
    QuestionOptionPublic,
    QuestionOptionRead,
)
from src.question.dto.QuestionTestCaseDto import (
    QuestionTestCaseCreate,
    QuestionTestCasePublic,
    QuestionTestCaseRead,
)


class QuestionBase(DTO):
    type: QuestionType
    body_md: str = Field(min_length=1)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    points: float = Field(default=1, gt=0)
    explanation_md: str | None = None
    is_active: bool = True
    allow_multiple: bool = False  # mcq with several correct options
    code_language: CodeLanguage | None = None
    starter_code: str | None = None

    @model_validator(mode="after")
    def _code_needs_language(self):
        if self.type == QuestionType.CODE and self.code_language is None:
            raise ValueError("code questions need a code_language")
        return self


class QuestionCreate(QuestionBase):  # author_id comes from the session
    topic_id: int | None = None
    options: list[QuestionOptionCreate] = Field(default_factory=list)
    test_cases: list[QuestionTestCaseCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_content(self):
        if self.type == QuestionType.MCQ:
            if len(self.options) < 2:
                raise ValueError("mcq questions need at least 2 options")
            correct = sum(o.is_correct for o in self.options)
            if correct < 1:
                raise ValueError("mcq questions need at least 1 correct option")
            if correct > 1 and not self.allow_multiple:
                raise ValueError("several correct options need allow_multiple=true")
        elif self.options:
            raise ValueError("only mcq questions have options")
        if self.type == QuestionType.CODE and not self.test_cases:
            raise ValueError("code questions need at least 1 test case")
        if self.type != QuestionType.CODE and self.test_cases:
            raise ValueError("only code questions have test cases")
        return self


class QuestionUpdate(DTO):  # options / test cases are edited through their own DTOs
    topic_id: int | None = None
    body_md: str | None = Field(default=None, min_length=1)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    points: float | None = Field(default=None, gt=0)
    explanation_md: str | None = None
    is_active: bool | None = None
    allow_multiple: bool | None = None
    code_language: CodeLanguage | None = None
    starter_code: str | None = None


class QuestionRead(QuestionBase):  # author view
    id: int
    topic_id: int | None = None
    author_id: int
    created_at: datetime
    options: list[QuestionOptionRead] = Field(default_factory=list)
    test_cases: list[QuestionTestCaseRead] = Field(default_factory=list)


class QuestionPublic(DTO):
    """Student view: no is_correct, no explanation, hidden test cases removed."""

    id: int
    type: QuestionType
    body_md: str
    difficulty: int | None = None
    points: float
    allow_multiple: bool
    code_language: CodeLanguage | None = None
    starter_code: str | None = None
    options: list[QuestionOptionPublic] = Field(default_factory=list)
    test_cases: list[QuestionTestCasePublic] = Field(default_factory=list)

    @field_validator("test_cases", mode="before")
    @classmethod
    def _only_visible(cls, value):
        def hidden(t):
            return t.get("is_hidden", True) if isinstance(t, dict) else getattr(t, "is_hidden", True)

        return [t for t in (value or []) if not hidden(t)]

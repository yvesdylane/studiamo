"""DTOs for ExamAttemptQuestion. ORM model: src/attempt/ExamAttemptQuestion.py."""

from __future__ import annotations

from src.base.BaseDto import DTO


class ExamAttemptQuestionRead(DTO):
    attempt_id: int
    question_id: int
    position: int

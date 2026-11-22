"""DTOs for ExamAttempt. ORM model: src/attempt/ExamAttempt.py."""

from __future__ import annotations

from datetime import datetime

from src.attempt.AttemptStatus import AttemptStatus
from src.base.BaseDto import DTO


class ExamAttemptCreate(DTO):
    exam_id: int


class ExamAttemptRead(DTO):
    id: int
    student_id: int | None = None   # None: a guest's attempt (guest_id)
    guest_id: int | None = None
    exam_id: int
    attempt_no: int
    status: AttemptStatus
    md_path: str | None = None
    points_earned: float | None = None
    points_possible: float | None = None
    score_pct: float | None = None
    started_at: datetime
    submitted_at: datetime | None = None
    duration_seconds: int | None = None

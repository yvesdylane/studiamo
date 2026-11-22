"""DTOs for ExerciseAttempt. ORM model: src/attempt/ExerciseAttempt.py."""

from __future__ import annotations

from datetime import datetime

from src.attempt.AttemptStatus import AttemptStatus
from src.base.BaseDto import DTO


class ExerciseAttemptCreate(DTO):  # student_id from the session, attempt_no by the server
    exercise_id: int


class ExerciseAttemptRead(DTO):
    id: int
    student_id: int
    exercise_id: int
    attempt_no: int
    status: AttemptStatus
    md_path: str | None = None  # questions + the student's answers
    points_earned: float | None = None
    points_possible: float | None = None
    score_pct: float | None = None
    started_at: datetime
    submitted_at: datetime | None = None
    duration_seconds: int | None = None

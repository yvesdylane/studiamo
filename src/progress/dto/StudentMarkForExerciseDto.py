"""DTOs for StudentMarkForExercise. ORM model: src/progress/StudentMarkForExercise.py."""

from __future__ import annotations

from src.base.BaseDto import DTO


class StudentMarkForExerciseRead(DTO):
    student_id: int
    exercise_id: int
    best_score_pct: float
    exp_earned: int
    xexp_earned: float = 0.0
    attempts_count: int

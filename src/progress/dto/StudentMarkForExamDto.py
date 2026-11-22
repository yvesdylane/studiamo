"""DTOs for StudentMarkForExam. ORM model: src/progress/StudentMarkForExam.py."""

from __future__ import annotations

from src.base.BaseDto import DTO


class StudentMarkForExamRead(DTO):
    student_id: int
    exam_id: int
    best_score_pct: float
    exp_earned: int
    xexp_earned: float = 0.0
    attempts_count: int

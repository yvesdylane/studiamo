"""What the attempt services return (computed, not stored as rows).

    ExerciseAttemptStarted / ExamAttemptStarted   attempt + the questions to show
    AttemptResult                                 grading outcome of a submission
    ManualGrade                                   lecturer's points for pending questions
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from src.attempt.ExamAttemptDto import ExamAttemptRead
from src.attempt.ExerciseAttemptDto import ExerciseAttemptRead
from src.base.BaseDto import DTO
from src.exam.dto.ExamDto import ExamPublic
from src.question.dto.QuestionDto import QuestionPublic
from src.question.models.Question import QuestionType


class ExerciseAttemptStarted(DTO):
    attempt: ExerciseAttemptRead
    questions: list[QuestionPublic]
    resumed: bool = False            # True when an unfinished attempt was returned


class ExamAttemptStarted(DTO):
    attempt: ExamAttemptRead
    exam: ExamPublic
    questions: list[QuestionPublic]  # points already include the exam's overrides
    deadline: datetime | None = None # submit before this (duration / closing time)
    resumed: bool = False


class QuestionResultRead(DTO):
    question_id: int
    position: int
    type: QuestionType
    answered: bool
    points_possible: float
    points_earned: float | None = None   # None while waiting for manual grading
    correct: bool | None = None          # None while waiting for manual grading
    pending_manual: bool = False
    test_cases_passed: int | None = None # code questions graded by a runner
    test_cases_total: int | None = None


class AttemptResult(DTO):
    attempt: ExerciseAttemptRead | ExamAttemptRead
    questions: list[QuestionResultRead]
    exp_awarded: int = 0
    pending_manual: bool = False         # status stays 'submitted' until graded by hand
    late: bool = False                   # exam submitted after its deadline: answers ignored


class ManualGrade(DTO):
    """Points for every question still waiting for a lecturer, by question id."""
    points: dict[int, float] = Field(min_length=1)
    comment_md: str | None = None        # added to the attempt record

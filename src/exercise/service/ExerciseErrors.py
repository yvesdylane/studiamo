"""Errors raised by ExerciseService."""

from __future__ import annotations

from src.base.ServiceErrors import ConflictError, InvalidOperationError, NotFoundError, ServiceError


class ExerciseError(ServiceError):
    """Base class: catch this to handle every exercise error at once."""


class ExerciseNotFoundError(ExerciseError, NotFoundError):
    def __init__(self, exercise_id: int) -> None:
        super().__init__(f"Exercise {exercise_id} does not exist")
        self.exercise_id = exercise_id


class ExerciseNotPublishedError(ExerciseError, InvalidOperationError):
    def __init__(self, exercise_id: int) -> None:
        super().__init__(f"Exercise {exercise_id} is not published")
        self.exercise_id = exercise_id


class ExerciseQuestionsInvalidError(ExerciseError, InvalidOperationError):
    """Duplicate or unknown question ids in an exercise's question list."""


class ExerciseQuestionAlreadyAddedError(ExerciseError, ConflictError):
    def __init__(self, exercise_id: int, question_id: int) -> None:
        super().__init__(f"Question {question_id} is already in exercise {exercise_id}")
        self.exercise_id = exercise_id
        self.question_id = question_id


class ExerciseQuestionNotInExerciseError(ExerciseError, NotFoundError):
    def __init__(self, exercise_id: int, question_id: int) -> None:
        super().__init__(f"Question {question_id} is not in exercise {exercise_id}")
        self.exercise_id = exercise_id
        self.question_id = question_id


class NoteExerciseCourseMismatchError(ExerciseError, InvalidOperationError):
    def __init__(self, note_id: int, exercise_id: int) -> None:
        super().__init__(f"Note {note_id} and exercise {exercise_id} belong to different courses")
        self.note_id = note_id
        self.exercise_id = exercise_id

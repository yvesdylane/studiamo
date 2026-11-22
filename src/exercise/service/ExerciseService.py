"""Exercises: graded practice sets attached to a topic (and optionally to notes).

    service = ExerciseService(get_db())
    ex = service.create(ExerciseCreate(topic_id=3, title="Sockets drill", max_exp=50,
                                       questions=[ExerciseQuestionBase(question_id=9, position=0)]))
    service.add_question(ex.id, question_id=10)          # appended at the end
    service.set_questions(ex.id, [ExerciseQuestionBase(question_id=10, position=0)])
    service.attach_to_note(note_id=4, exercise_id=ex.id) # "practice" button under a note
    service.list_for_note(4, published_only=True)

Taking an exercise is AttemptService.start_exercise / submit_exercise.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.course.modles.Note import Note
from src.course.modles.Topic import Topic
from src.course.service.TopicErrors import NoteNotFoundError, TopicNotFoundError
from src.db.DB import Db
from src.exercise.dto.ExerciseDto import ExerciseCreate, ExerciseRead, ExerciseUpdate
from src.exercise.dto.ExerciseQuestionDto import ExerciseQuestionBase
from src.exercise.dto.NoteExerciseDto import NoteExerciseRead
from src.exercise.models.Exercise import Exercise
from src.exercise.models.ExerciseQuestion import ExerciseQuestion
from src.exercise.models.NoteExercise import NoteExercise
from src.exercise.service.ExerciseErrors import (
    ExerciseNotFoundError,
    ExerciseNotPublishedError,
    ExerciseQuestionAlreadyAddedError,
    ExerciseQuestionNotInExerciseError,
    ExerciseQuestionsInvalidError,
    NoteExerciseCourseMismatchError,
)
from src.question.models.Question import Question
from src.question.service.QuestionErrors import QuestionNotFoundError


class ExerciseService:
    def __init__(self, db: Db) -> None:
        self._db = db

    # --------------------------------------------------------------- crud
    def create(self, data: ExerciseCreate) -> ExerciseRead:
        with self._db.session() as session:
            if session.get(Topic, data.topic_id) is None:
                raise TopicNotFoundError(data.topic_id)
            self._check_questions(session, data.questions)
            exercise = Exercise(**data.model_dump(exclude={"questions"}))
            session.add(exercise)
            session.flush()
            session.add_all(
                ExerciseQuestion(exercise_id=exercise.id, question_id=q.question_id, position=q.position)
                for q in data.questions
            )
            session.flush()
            session.refresh(exercise)
            return ExerciseRead.model_validate(exercise)

    def get(self, exercise_id: int) -> ExerciseRead:
        with self._db.session() as session:
            return ExerciseRead.model_validate(self._find(session, exercise_id))

    def get_published(self, exercise_id: int) -> ExerciseRead:
        """Student view: ExerciseNotPublishedError for drafts."""
        exercise = self.get(exercise_id)
        if not exercise.published:
            raise ExerciseNotPublishedError(exercise_id)
        return exercise

    def list_for_topic(self, topic_id: int, *, published_only: bool = False) -> list[ExerciseRead]:
        with self._db.session() as session:
            if session.get(Topic, topic_id) is None:
                raise TopicNotFoundError(topic_id)
            query = sa.select(Exercise).where(Exercise.topic_id == topic_id)
            return self._list(session, query, published_only)

    def list_for_course(self, course_id: int, *, published_only: bool = False) -> list[ExerciseRead]:
        with self._db.session() as session:
            query = (
                sa.select(Exercise)
                .join(Topic, Topic.id == Exercise.topic_id)
                .where(Topic.course_id == course_id)
            )
            return self._list(session, query, published_only)

    def update(self, exercise_id: int, data: ExerciseUpdate) -> ExerciseRead:
        """Partial update: None means "leave it"."""
        with self._db.session() as session:
            exercise = self._find(session, exercise_id)
            for field, value in data.model_dump(exclude_none=True).items():
                setattr(exercise, field, value)
            session.flush()
            return ExerciseRead.model_validate(exercise)

    def delete(self, exercise_id: int) -> None:
        """Delete an exercise with its attempts and marks. Exp already earned stays."""
        with self._db.session() as session:
            deleted = session.execute(sa.delete(Exercise).where(Exercise.id == exercise_id)).rowcount
            if not deleted:
                raise ExerciseNotFoundError(exercise_id)

    # ---------------------------------------------------------- questions
    def set_questions(self, exercise_id: int, questions: list[ExerciseQuestionBase]) -> ExerciseRead:
        """Replace the whole question list (and order) of an exercise."""
        with self._db.session() as session:
            exercise = self._find(session, exercise_id)
            self._check_questions(session, questions)
            session.execute(sa.delete(ExerciseQuestion).where(ExerciseQuestion.exercise_id == exercise_id))
            session.add_all(
                ExerciseQuestion(exercise_id=exercise_id, question_id=q.question_id, position=q.position)
                for q in questions
            )
            session.flush()
            session.expire(exercise, ["questions"])
            return ExerciseRead.model_validate(exercise)

    def add_question(self, exercise_id: int, question_id: int, position: int | None = None) -> ExerciseRead:
        """Add one question; position None puts it after the last one."""
        with self._db.session() as session:
            exercise = self._find(session, exercise_id)
            if any(q.question_id == question_id for q in exercise.questions):
                raise ExerciseQuestionAlreadyAddedError(exercise_id, question_id)
            if session.get(Question, question_id) is None:
                raise QuestionNotFoundError(question_id)
            if position is None:
                position = max((q.position for q in exercise.questions), default=-1) + 1
            session.add(ExerciseQuestion(exercise_id=exercise_id, question_id=question_id, position=position))
            session.flush()
            session.expire(exercise, ["questions"])
            return ExerciseRead.model_validate(exercise)

    def remove_question(self, exercise_id: int, question_id: int) -> ExerciseRead:
        with self._db.session() as session:
            exercise = self._find(session, exercise_id)
            removed = session.execute(
                sa.delete(ExerciseQuestion).where(
                    ExerciseQuestion.exercise_id == exercise_id,
                    ExerciseQuestion.question_id == question_id,
                )
            ).rowcount
            if not removed:
                raise ExerciseQuestionNotInExerciseError(exercise_id, question_id)
            session.expire(exercise, ["questions"])
            return ExerciseRead.model_validate(exercise)

    # -------------------------------------------------------- note links
    def attach_to_note(self, note_id: int, exercise_id: int) -> NoteExerciseRead:
        """Link an exercise to a note of the same course. Linking twice is a no-op."""
        with self._db.session() as session:
            note = session.get(Note, note_id)
            if note is None:
                raise NoteNotFoundError(note_id)
            exercise = self._find(session, exercise_id)
            note_course = session.get(Topic, note.topic_id).course_id
            exercise_course = session.get(Topic, exercise.topic_id).course_id
            if note_course != exercise_course:
                raise NoteExerciseCourseMismatchError(note_id, exercise_id)
            session.execute(
                pg_insert(NoteExercise)
                .values(note_id=note_id, exercise_id=exercise_id)
                .on_conflict_do_nothing()
            )
            return NoteExerciseRead(note_id=note_id, exercise_id=exercise_id)

    def detach_from_note(self, note_id: int, exercise_id: int) -> None:
        """Remove the link (the exercise itself stays). Unknown links are ignored."""
        with self._db.session() as session:
            session.execute(
                sa.delete(NoteExercise).where(
                    NoteExercise.note_id == note_id, NoteExercise.exercise_id == exercise_id
                )
            )

    def list_for_note(self, note_id: int, *, published_only: bool = False) -> list[ExerciseRead]:
        with self._db.session() as session:
            if session.get(Note, note_id) is None:
                raise NoteNotFoundError(note_id)
            query = (
                sa.select(Exercise)
                .join(NoteExercise, NoteExercise.exercise_id == Exercise.id)
                .where(NoteExercise.note_id == note_id)
            )
            return self._list(session, query, published_only)

    def note_ids_for_exercise(self, exercise_id: int) -> list[int]:
        with self._db.session() as session:
            self._find(session, exercise_id)
            return list(
                session.scalars(
                    sa.select(NoteExercise.note_id)
                    .where(NoteExercise.exercise_id == exercise_id)
                    .order_by(NoteExercise.note_id)
                )
            )

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _list(session: Session, query: sa.Select, published_only: bool) -> list[ExerciseRead]:
        if published_only:
            query = query.where(Exercise.published)
        exercises = session.scalars(query.order_by(Exercise.id)).all()
        return [ExerciseRead.model_validate(e) for e in exercises]

    @staticmethod
    def _find(session: Session, exercise_id: int) -> Exercise:
        exercise = session.get(Exercise, exercise_id)
        if exercise is None:
            raise ExerciseNotFoundError(exercise_id)
        return exercise

    @staticmethod
    def _check_questions(session: Session, questions: Sequence[ExerciseQuestionBase]) -> None:
        ids = [q.question_id for q in questions]
        if len(ids) != len(set(ids)):
            raise ExerciseQuestionsInvalidError("a question can appear only once in an exercise")
        if ids:
            found = set(session.scalars(sa.select(Question.id).where(Question.id.in_(ids))))
            missing = [i for i in ids if i not in found]
            if missing:
                raise QuestionNotFoundError(missing[0])

"""Taking exercises and exams: start, submit, grade, review.

    service = AttemptService(get_db(), LocalMarkdownStore.from_env())

    started = service.start_exercise(student_id=7, exercise_id=2)       # or resumes the open one
    result  = service.submit_exercise(started.attempt.id, 7, AttemptSubmit(answers=[...]))

    started = service.start_exam(student_id=7, exam_id=5)               # picks + shuffles questions
    result  = service.submit_exam(started.attempt.id, 7, AttemptSubmit(answers=[...]))

    # short answers (and code without a CodeRunner) wait for a lecturer:
    service.pending_exam_attempts(exam_id=5)
    service.read_record("exam", attempt_id)                             # the md with the answers
    service.grade_exam_attempt(attempt_id, ManualGrade(points={12: 1.5}), grader_id=lecturer.id)

When an attempt becomes 'graded', the student's mark, exp (exp_ledger +
player_stats.total_exp) and rank are updated in the same transaction.

Exam timing: the deadline is started_at + duration_minutes, and for
scheduled exams never later than closes_at. A submission more than
GRACE_SECONDS after it is accepted but its answers are ignored (score 0).
An unfinished attempt past its deadline is closed the same way the next time
the student starts the exam.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session, selectinload

from src.attempt.AttemptResultDto import (
    AttemptResult,
    ExamAttemptStarted,
    ExerciseAttemptStarted,
    ManualGrade,
    QuestionResultRead,
)
from src.attempt.AttemptStatus import AttemptStatus
from src.attempt.AttemptSubmitDto import AnswerSubmit, AttemptSubmit
from src.attempt.ExamAttempt import ExamAttempt
from src.attempt.ExamAttemptDto import ExamAttemptRead
from src.attempt.ExamAttemptQuestion import ExamAttemptQuestion
from src.attempt.ExerciseAttempt import ExerciseAttempt
from src.attempt.ExerciseAttemptDto import ExerciseAttemptRead
from src.attempt.service import Grading
from src.attempt.service.AttemptErrors import (
    AnswerNotInAttemptError,
    AttemptAlreadySubmittedError,
    AttemptNotAwaitingGradingError,
    AttemptNotFoundError,
    AttemptNotYoursError,
    ExamAttemptsExhaustedError,
    ManualGradeInvalidError,
    NoQuestionsError,
)
from src.attempt.service.CodeRunner import CodeRunner
from src.attempt.service.Grading import GradedAnswer, money
from src.base.Paging import DEFAULT_PAGE_SIZE, clamp
from src.course.modles.Topic import Topic
from src.db.DB import Db
from src.exam.dto.ExamDto import ExamPublic
from src.exam.models.Exam import Exam, ExamKind
from src.exam.models.ExamQuestion import ExamQuestion
from src.exam.service.ExamErrors import ExamNotOpenError, ExamNotPublishedError
from src.exam.service.ExamService import ExamService, is_open
from src.exercise.models.Exercise import Exercise
from src.exercise.models.ExerciseQuestion import ExerciseQuestion
from src.exercise.service.ExerciseErrors import ExerciseNotFoundError, ExerciseNotPublishedError
from src.progress.service.ProgressService import record_exam_result, record_exercise_result
from src.question.dto.QuestionDto import QuestionPublic
from src.question.models.Question import Question
from src.storage.MarkdownStore import MarkdownStore

GRACE_SECONDS = 120  # network / auto-submit slack after an exam's deadline

Kind = Literal["exercise", "exam"]
AnyAttempt = ExerciseAttempt | ExamAttempt


class AttemptService:
    def __init__(
        self,
        db: Db,
        store: MarkdownStore,
        code_runner: CodeRunner | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """store keeps the attempt records; code_runner grades code answers
        (None = lecturers grade them); clock is for tests."""
        self._db = db
        self._store = store
        self._runner = code_runner
        self._now = clock or (lambda: datetime.now(UTC))

    # ================================================================ exercises
    def start_exercise(self, student_id: int, exercise_id: int) -> ExerciseAttemptStarted:
        """Open a new attempt, or return the student's unfinished one."""
        with self._db.session() as session:
            exercise = session.get(Exercise, exercise_id, with_for_update=True)
            if exercise is None:
                raise ExerciseNotFoundError(exercise_id)
            if not exercise.published:
                raise ExerciseNotPublishedError(exercise_id)
            questions = self._exercise_questions(session, exercise_id)
            if not questions:
                raise NoQuestionsError(f"Exercise {exercise_id}")

            attempt = session.scalars(
                sa.select(ExerciseAttempt).where(
                    ExerciseAttempt.student_id == student_id,
                    ExerciseAttempt.exercise_id == exercise_id,
                    ExerciseAttempt.status == AttemptStatus.IN_PROGRESS,
                )
            ).first()
            resumed = attempt is not None
            if attempt is None:
                last = session.scalar(
                    sa.select(sa.func.max(ExerciseAttempt.attempt_no)).where(
                        ExerciseAttempt.student_id == student_id, ExerciseAttempt.exercise_id == exercise_id
                    )
                )
                attempt = ExerciseAttempt(student_id=student_id, exercise_id=exercise_id, attempt_no=(last or 0) + 1)
                session.add(attempt)
                session.flush()
                session.refresh(attempt)

            return ExerciseAttemptStarted(
                attempt=ExerciseAttemptRead.model_validate(attempt),
                questions=[QuestionPublic.model_validate(q) for q in questions],
                resumed=resumed,
            )

    def submit_exercise(self, attempt_id: int, student_id: int, data: AttemptSubmit) -> AttemptResult:
        with self._db.session() as session:
            attempt = self._own_open_attempt(session, ExerciseAttempt, attempt_id, student_id)
            exercise = session.get(Exercise, attempt.exercise_id)
            questions = self._exercise_questions(session, exercise.id)
            graded = self._grade_all(questions, {q.id: Decimal(q.points) for q in questions}, data.answers)
            return self._finish(session, "exercise", attempt, exercise.title, graded, late=False)

    def get_exercise_attempt(self, attempt_id: int) -> ExerciseAttemptRead:
        with self._db.session() as session:
            return ExerciseAttemptRead.model_validate(self._find(session, ExerciseAttempt, attempt_id))

    def list_exercise_attempts(
        self, student_id: int, exercise_id: int | None = None, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> list[ExerciseAttemptRead]:
        """A student's exercise attempts, newest first."""
        limit, offset = clamp(limit, offset)
        query = sa.select(ExerciseAttempt).where(ExerciseAttempt.student_id == student_id)
        if exercise_id is not None:
            query = query.where(ExerciseAttempt.exercise_id == exercise_id)
        with self._db.session() as session:
            rows = session.scalars(
                query.order_by(ExerciseAttempt.started_at.desc(), ExerciseAttempt.id.desc()).limit(limit).offset(offset)
            ).all()
            return [ExerciseAttemptRead.model_validate(r) for r in rows]

    def pending_exercise_attempts(self, course_id: int | None = None) -> list[ExerciseAttemptRead]:
        """Exercise attempts waiting for manual grading, oldest first."""
        query = sa.select(ExerciseAttempt).where(ExerciseAttempt.status == AttemptStatus.SUBMITTED)
        if course_id is not None:
            query = (
                query.join(Exercise, Exercise.id == ExerciseAttempt.exercise_id)
                .join(Topic, Topic.id == Exercise.topic_id)
                .where(Topic.course_id == course_id)
            )
        with self._db.session() as session:
            rows = session.scalars(query.order_by(ExerciseAttempt.submitted_at)).all()
            return [ExerciseAttemptRead.model_validate(r) for r in rows]

    def grade_exercise_attempt(self, attempt_id: int, grade: ManualGrade, grader_id: int) -> AttemptResult:
        with self._db.session() as session:
            attempt = self._find(session, ExerciseAttempt, attempt_id, for_update=True)
            exercise = session.get(Exercise, attempt.exercise_id)
            questions = self._exercise_questions(session, exercise.id, active_only=False)
            possible = {q.id: money(q.points) for q in questions}
            return self._grade_manually(session, "exercise", attempt, possible, grade, grader_id)

    # ==================================================================== exams
    def start_exam(self, student_id: int, exam_id: int) -> ExamAttemptStarted:
        """Open a new attempt (drawing its questions), or resume the unfinished one."""
        with self._db.session() as session:
            exam = ExamService.find(session, exam_id, for_update=True)  # serialises starts
            if not exam.published:
                raise ExamNotPublishedError(exam_id)
            now = self._now()

            attempt = session.scalars(
                sa.select(ExamAttempt).where(
                    ExamAttempt.student_id == student_id,
                    ExamAttempt.exam_id == exam_id,
                    ExamAttempt.status == AttemptStatus.IN_PROGRESS,
                )
            ).first()
            if attempt is not None and self._is_late(exam, attempt, now):
                self._close_expired(session, exam, attempt)
                attempt = None
            if attempt is not None:
                return self._exam_started(session, exam, attempt, resumed=True)

            # refusals are raised after the session commits, so an expired
            # attempt closed above stays closed
            used = session.scalar(
                sa.select(sa.func.count())
                .select_from(ExamAttempt)
                .where(ExamAttempt.student_id == student_id, ExamAttempt.exam_id == exam_id)
            )
            pool = ExamService.pool_question_ids(session, exam)
            if not is_open(exam, now):
                refusal: Exception = ExamNotOpenError(exam_id, exam.opens_at, exam.closes_at)
            elif exam.max_attempts is not None and used >= exam.max_attempts:
                refusal = ExamAttemptsExhaustedError(exam_id, exam.max_attempts)
            elif not pool:
                refusal = NoQuestionsError(f"Exam {exam_id}")
            else:
                return self._open_exam_attempt(session, exam, student_id, used + 1, pool)
        raise refusal

    def _open_exam_attempt(
        self, session: Session, exam: Exam, student_id: int, attempt_no: int, pool: list[int]
    ) -> ExamAttemptStarted:
        """Draw the attempt's questions and store them in exam_attempt_questions."""
        rng = random.SystemRandom()
        count = min(exam.questions_per_attempt or len(pool), len(pool))
        chosen = rng.sample(pool, count) if count < len(pool) else list(pool)
        if exam.shuffle_questions:
            rng.shuffle(chosen)

        attempt = ExamAttempt(student_id=student_id, exam_id=exam.id, attempt_no=attempt_no)
        session.add(attempt)
        session.flush()
        session.add_all(
            ExamAttemptQuestion(attempt_id=attempt.id, question_id=qid, position=i) for i, qid in enumerate(chosen)
        )
        session.flush()
        session.refresh(attempt)
        return self._exam_started(session, exam, attempt, resumed=False)

    def submit_exam(self, attempt_id: int, student_id: int, data: AttemptSubmit) -> AttemptResult:
        with self._db.session() as session:
            attempt = self._own_open_attempt(session, ExamAttempt, attempt_id, student_id)
            exam = session.get(Exam, attempt.exam_id)
            late = self._is_late(exam, attempt, self._now())
            questions = self._exam_questions(session, attempt)
            points = self._exam_points(session, exam, questions)
            graded = self._grade_all(questions, points, [] if late else data.answers)
            return self._finish(session, "exam", attempt, exam.title, graded, late=late, exam=exam)

    def get_exam_attempt(self, attempt_id: int) -> ExamAttemptRead:
        with self._db.session() as session:
            return ExamAttemptRead.model_validate(self._find(session, ExamAttempt, attempt_id))

    def resume_exam(self, attempt_id: int, student_id: int) -> ExamAttemptStarted:
        """The questions of an unfinished attempt again (page reload)."""
        with self._db.session() as session:
            attempt = self._own_open_attempt(session, ExamAttempt, attempt_id, student_id)
            return self._exam_started(session, session.get(Exam, attempt.exam_id), attempt, resumed=True)

    def list_exam_attempts(
        self, student_id: int, exam_id: int | None = None, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> list[ExamAttemptRead]:
        limit, offset = clamp(limit, offset)
        query = sa.select(ExamAttempt).where(ExamAttempt.student_id == student_id)
        if exam_id is not None:
            query = query.where(ExamAttempt.exam_id == exam_id)
        with self._db.session() as session:
            rows = session.scalars(
                query.order_by(ExamAttempt.started_at.desc(), ExamAttempt.id.desc()).limit(limit).offset(offset)
            ).all()
            return [ExamAttemptRead.model_validate(r) for r in rows]

    def list_attempts_for_exam(self, exam_id: int) -> list[ExamAttemptRead]:
        """Every attempt at one exam (lecturer view), oldest first."""
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(ExamAttempt).where(ExamAttempt.exam_id == exam_id).order_by(ExamAttempt.started_at)
            ).all()
            return [ExamAttemptRead.model_validate(r) for r in rows]

    def pending_exam_attempts(self, exam_id: int | None = None, course_id: int | None = None) -> list[ExamAttemptRead]:
        """Exam attempts waiting for manual grading, oldest first."""
        query = sa.select(ExamAttempt).where(ExamAttempt.status == AttemptStatus.SUBMITTED)
        if exam_id is not None:
            query = query.where(ExamAttempt.exam_id == exam_id)
        if course_id is not None:
            query = query.join(Exam, Exam.id == ExamAttempt.exam_id).where(Exam.course_id == course_id)
        with self._db.session() as session:
            rows = session.scalars(query.order_by(ExamAttempt.submitted_at)).all()
            return [ExamAttemptRead.model_validate(r) for r in rows]

    def grade_exam_attempt(self, attempt_id: int, grade: ManualGrade, grader_id: int) -> AttemptResult:
        with self._db.session() as session:
            attempt = self._find(session, ExamAttempt, attempt_id, for_update=True)
            exam = session.get(Exam, attempt.exam_id)
            questions = self._exam_questions(session, attempt)
            possible = {qid: money(p) for qid, p in self._exam_points(session, exam, questions).items()}
            return self._grade_manually(session, "exam", attempt, possible, grade, grader_id, exam=exam)

    # =================================================================== record
    def read_record(self, kind: Kind, attempt_id: int, student_id: int | None = None) -> str:
        """The attempt's md record. Pass student_id to make sure it is theirs.
        Empty string while the attempt is still in progress."""
        model = ExerciseAttempt if kind == "exercise" else ExamAttempt
        with self._db.session() as session:
            attempt = self._find(session, model, attempt_id)
            if student_id is not None and attempt.student_id != student_id:
                raise AttemptNotYoursError(attempt_id)
            md_path = attempt.md_path
        return self._store.read(md_path) if md_path else ""

    # ================================================================== helpers
    @staticmethod
    def _find(session: Session, model: type[AnyAttempt], attempt_id: int, *, for_update: bool = False) -> AnyAttempt:
        attempt = session.get(model, attempt_id, with_for_update=for_update)
        if attempt is None:
            raise AttemptNotFoundError("exercise" if model is ExerciseAttempt else "exam", attempt_id)
        return attempt

    def _own_open_attempt(self, session: Session, model: type[AnyAttempt], attempt_id: int, student_id: int) -> AnyAttempt:
        attempt = self._find(session, model, attempt_id, for_update=True)
        if attempt.student_id != student_id:
            raise AttemptNotYoursError(attempt_id)
        if attempt.status != AttemptStatus.IN_PROGRESS:
            raise AttemptAlreadySubmittedError(attempt_id)
        return attempt

    @staticmethod
    def _load_questions(session: Session, ids: Sequence[int]) -> list[Question]:
        """Questions with options and test cases, in the order of ids."""
        if not ids:
            return []
        found = {
            q.id: q
            for q in session.scalars(
                sa.select(Question)
                .options(selectinload(Question.options), selectinload(Question.test_cases))
                .where(Question.id.in_(ids))
            )
        }
        return [found[i] for i in ids if i in found]

    def _exercise_questions(self, session: Session, exercise_id: int, *, active_only: bool = True) -> list[Question]:
        query = sa.select(ExerciseQuestion.question_id).where(ExerciseQuestion.exercise_id == exercise_id)
        if active_only:
            query = query.join(Question, Question.id == ExerciseQuestion.question_id).where(Question.is_active)
        ids = list(session.scalars(query.order_by(ExerciseQuestion.position, ExerciseQuestion.question_id)))
        return self._load_questions(session, ids)

    def _exam_questions(self, session: Session, attempt: ExamAttempt) -> list[Question]:
        ids = list(
            session.scalars(
                sa.select(ExamAttemptQuestion.question_id)
                .where(ExamAttemptQuestion.attempt_id == attempt.id)
                .order_by(ExamAttemptQuestion.position)
            )
        )
        return self._load_questions(session, ids)

    @staticmethod
    def _exam_points(session: Session, exam: Exam, questions: Sequence[Question]) -> dict[int, Decimal]:
        """Points per question: the exam's override if any, else the question's own."""
        overrides = dict(
            session.execute(
                sa.select(ExamQuestion.question_id, ExamQuestion.points).where(
                    ExamQuestion.exam_id == exam.id, ExamQuestion.points.is_not(None)
                )
            ).tuples().all()
        )
        return {q.id: Decimal(overrides.get(q.id, q.points)) for q in questions}

    def _exam_started(self, session: Session, exam: Exam, attempt: ExamAttempt, *, resumed: bool) -> ExamAttemptStarted:
        questions = self._exam_questions(session, attempt)
        points = self._exam_points(session, exam, questions)
        rng = random.Random(attempt.id)  # same option order every time the attempt is shown
        public = []
        for q in questions:
            item = QuestionPublic.model_validate(q).model_copy(update={"points": float(points[q.id])})
            if exam.shuffle_options and item.options:
                options = list(item.options)
                rng.shuffle(options)
                item = item.model_copy(update={"options": options})
            public.append(item)
        return ExamAttemptStarted(
            attempt=ExamAttemptRead.model_validate(attempt),
            exam=ExamPublic.model_validate(exam),
            questions=public,
            deadline=self._deadline(exam, attempt),
            resumed=resumed,
        )

    @staticmethod
    def _deadline(exam: Exam, attempt: ExamAttempt) -> datetime | None:
        ends = []
        if exam.duration_minutes:
            ends.append(attempt.started_at + timedelta(minutes=exam.duration_minutes))
        if exam.kind == ExamKind.SCHEDULED and exam.closes_at is not None:
            ends.append(exam.closes_at)
        return min(ends) if ends else None

    def _is_late(self, exam: Exam, attempt: ExamAttempt, now: datetime) -> bool:
        deadline = self._deadline(exam, attempt)
        return deadline is not None and now > deadline + timedelta(seconds=GRACE_SECONDS)

    def _close_expired(self, session: Session, exam: Exam, attempt: ExamAttempt) -> None:
        """Finish an abandoned attempt past its deadline with no answers."""
        questions = self._exam_questions(session, attempt)
        graded = self._grade_all(questions, self._exam_points(session, exam, questions), [])
        self._finish(session, "exam", attempt, exam.title, graded, late=True, exam=exam)

    def _grade_all(
        self, questions: Sequence[Question], points: dict[int, Decimal], answers: Sequence[AnswerSubmit]
    ) -> list[GradedAnswer]:
        by_question = {a.question_id: a for a in answers}
        unknown = set(by_question) - {q.id for q in questions}
        if unknown:
            raise AnswerNotInAttemptError(min(unknown))
        return [
            Grading.grade(q, by_question.get(q.id), points[q.id], position, self._runner)
            for position, q in enumerate(questions)
        ]

    def _finish(
        self,
        session: Session,
        kind: Kind,
        attempt: AnyAttempt,
        title: str,
        graded: list[GradedAnswer],
        *,
        late: bool,
        exam: Exam | None = None,
    ) -> AttemptResult:
        """Write the record, store the scores, and award exp if nothing is pending."""
        now = self._now()
        record = Grading.render_record(
            title=title,
            student_id=attempt.student_id,
            attempt_id=attempt.id,
            attempt_no=attempt.attempt_no,
            started_at=attempt.started_at,
            submitted_at=now,
            graded=graded,
            late=late,
        )
        attempt.md_path = self._store.save(self._record_key(kind, attempt), record)
        attempt.submitted_at = now
        attempt.points_possible = money(sum((g.points_possible for g in graded), Decimal(0)))
        attempt.points_earned = money(sum((g.points_earned or Decimal(0) for g in graded), Decimal(0)))
        pending = any(g.pending for g in graded)

        exp = 0
        if pending:
            attempt.status = AttemptStatus.SUBMITTED
            attempt.score_pct = None
        else:
            exp = self._mark_graded(session, kind, attempt, exam)
        session.flush()
        session.refresh(attempt)  # duration_seconds is computed by the database
        return self._result(kind, attempt, graded, exp, pending, late)

    def _mark_graded(self, session: Session, kind: Kind, attempt: AnyAttempt, exam: Exam | None) -> int:
        attempt.status = AttemptStatus.GRADED
        attempt.score_pct = money(attempt.points_earned * 100 / attempt.points_possible)
        if kind == "exercise":
            exercise = session.get(Exercise, attempt.exercise_id)
            return record_exercise_result(session, attempt.student_id, exercise, attempt.attempt_no, attempt.score_pct)
        return record_exam_result(session, attempt.student_id, exam, attempt.score_pct)

    def _grade_manually(
        self,
        session: Session,
        kind: Kind,
        attempt: AnyAttempt,
        possible: dict[int, Decimal],
        grade: ManualGrade,
        grader_id: int,
        *,
        exam: Exam | None = None,
    ) -> AttemptResult:
        if attempt.status != AttemptStatus.SUBMITTED or not attempt.md_path:
            raise AttemptNotAwaitingGradingError(attempt.id)
        record = self._store.read(attempt.md_path)
        pending = Grading.pending_ids(record)
        if set(grade.points) != set(pending):
            raise ManualGradeInvalidError(f"points are needed for exactly these questions: {sorted(pending)}")
        points: dict[int, Decimal] = {}
        for qid, value in grade.points.items():
            value = money(value)
            limit = possible.get(qid, Decimal(0))
            if value < 0 or value > limit:
                raise ManualGradeInvalidError(f"question {qid} is worth between 0 and {limit} points")
            points[qid] = value

        attempt.points_earned = money(attempt.points_earned + sum(points.values(), Decimal(0)))
        self._store.save(
            attempt.md_path, Grading.append_manual_grading(record, points, grader_id, grade.comment_md)
        )
        exp = self._mark_graded(session, kind, attempt, exam)
        session.flush()
        session.refresh(attempt)
        model = ExerciseAttemptRead if kind == "exercise" else ExamAttemptRead
        return AttemptResult(attempt=model.model_validate(attempt), questions=[], exp_awarded=exp)

    @staticmethod
    def _record_key(kind: Kind, attempt: AnyAttempt) -> str:
        parent = attempt.exercise_id if kind == "exercise" else attempt.exam_id
        return f"attempts/{kind}/{parent}/{attempt.student_id}/{attempt.id}.md"

    @staticmethod
    def _result(
        kind: Kind, attempt: AnyAttempt, graded: list[GradedAnswer], exp: int, pending: bool, late: bool
    ) -> AttemptResult:
        model = ExerciseAttemptRead if kind == "exercise" else ExamAttemptRead
        questions = []
        for g in graded:
            runs = g.test_outcomes
            questions.append(
                QuestionResultRead(
                    question_id=g.question.id,
                    position=g.position,
                    type=g.question.type,
                    answered=g.answered,
                    points_possible=float(g.points_possible),
                    points_earned=None if g.pending else float(g.points_earned),
                    correct=g.correct,
                    pending_manual=g.pending,
                    test_cases_passed=sum(r.passed for r in runs) if runs else None,
                    test_cases_total=len(runs) if runs else None,
                )
            )
        return AttemptResult(
            attempt=model.model_validate(attempt),
            questions=questions,
            exp_awarded=exp,
            pending_manual=pending,
            late=late,
        )

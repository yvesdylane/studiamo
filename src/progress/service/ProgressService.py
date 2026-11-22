"""Student progress: completed notes, marks, the exp ledger and course progress.

    service = ProgressService(get_db())
    done = service.complete_note(student_id=7, note_id=4)     # awards the note's exp once
    service.exercise_mark(7, exercise_id=2)                   # best score, exp, attempts
    service.ledger(7)                                         # every exp award, newest first
    service.course_progress(7, course_id=1)
    service.course_ids_of(7)                                  # courses the student is in

Exercise and exam results are recorded by AttemptService when an attempt is
graded; it calls the record_* functions below inside its own transaction so
the attempt, the mark, the ledger and total_exp change together.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.attempt.AttemptStatus import AttemptStatus
from src.attempt.ExerciseAttempt import ExerciseAttempt
from src.base.Paging import DEFAULT_PAGE_SIZE, clamp
from src.course.modles.Note import Note
from src.course.modles.Topic import Topic
from src.course.service.TopicErrors import NoteNotFoundError, NoteNotPublishedError
from src.db.DB import Db
from src.exam.models.Exam import Exam
from src.exercise.models.Exercise import Exercise
from src.player.PlayerStats import PlayerStats
from src.progress.dto.CourseProgressDto import CourseProgressRead
from src.progress.dto.ExpLedgerDto import ExpLedgerRead
from src.progress.dto.StudentCompletedNoteDto import StudentCompletedNoteRead
from src.progress.dto.StudentMarkForExamDto import StudentMarkForExamRead
from src.progress.dto.StudentMarkForExerciseDto import StudentMarkForExerciseRead
from src.progress.models.ExpLedger import ExpLedger
from src.progress.models.StudentCompletedNote import StudentCompletedNote
from src.progress.models.StudentMarkForExam import StudentMarkForExam
from src.progress.models.StudentMarkForExercise import StudentMarkForExercise
from src.progress.service import Engagement, ExpRules


# ====================================================================
# Building blocks used inside another service's transaction
# ====================================================================
def award_exp(
    session: Session, student_id: int, source: str, source_id: int, amount: int, xexp: Decimal = Decimal(0)
) -> None:
    """Write one ledger line (exp and Xexp) and add both to player_stats.
    Nothing is written when both are <= 0. Call refresh_rank afterwards."""
    amount = max(0, amount)
    xexp = max(Decimal(0), Decimal(xexp))
    if amount == 0 and xexp == 0:
        return
    session.add(ExpLedger(student_id=student_id, source=source, source_id=source_id, amount=amount, xexp=xexp))
    # the row normally exists (trigger on users); upsert in case it was removed
    session.execute(
        pg_insert(PlayerStats)
        .values(user_id=student_id, total_exp=amount, total_xexp=xexp)
        .on_conflict_do_update(
            index_elements=[PlayerStats.user_id],
            set_={"total_exp": PlayerStats.total_exp + amount, "total_xexp": PlayerStats.total_xexp + xexp},
        )
    )


def refresh_rank(session: Session, student_id: int) -> PlayerStats:
    """Set rank_level (and its letter) from the student's total Xexp."""
    session.flush()
    stats = session.get(PlayerStats, student_id, with_for_update=True, populate_existing=True)
    if stats is None:
        stats = PlayerStats(user_id=student_id, total_exp=0, total_xexp=Decimal(0))
        session.add(stats)
    level = ExpRules.rank_level_from_xexp(stats.total_xexp or 0)
    if stats.rank_level != level:
        stats.rank_updated_at = datetime.now(UTC)
    stats.rank_level = level
    stats.rank = ExpRules.rank_letter(level)
    return stats


def record_exercise_result(
    session: Session, student_id: int, exercise: Exercise, attempt_no: int, score_pct: Decimal
) -> int:
    """Update the student's mark for an exercise after a graded attempt. Returns exp awarded.

    exp: every attempt, shrinking with retake_decay. Xexp: from the best score
    only (>= 75%), so a retry adds Xexp only when it beats the best."""
    exp = ExpRules.exercise_exp(exercise.max_exp, score_pct, exercise.retake_decay, attempt_no)
    mark = session.get(StudentMarkForExercise, (student_id, exercise.id), with_for_update=True)
    if mark is None:
        mark = StudentMarkForExercise(
            student_id=student_id, exercise_id=exercise.id, best_score_pct=score_pct, exp_earned=0,
            xexp_earned=Decimal(0), attempts_count=0,
        )
        session.add(mark)
    mark.best_score_pct = max(Decimal(mark.best_score_pct), score_pct)
    xexp = ExpRules.xexp_due(exercise.max_exp, mark.best_score_pct, mark.xexp_earned or Decimal(0))
    mark.exp_earned += exp
    mark.xexp_earned = Decimal(mark.xexp_earned or 0) + xexp
    mark.attempts_count += 1
    award_exp(session, student_id, "exercise", exercise.id, exp, xexp)
    refresh_rank(session, student_id)
    Engagement.on_exercise_graded(session, student_id, score_pct)
    return exp


def record_exam_result(session: Session, student_id: int, exam: Exam, score_pct: Decimal) -> int:
    """Update the student's mark for an exam after a graded attempt. Returns exp awarded.
    Both exp and Xexp follow the best score: only the difference is added."""
    mark = session.get(StudentMarkForExam, (student_id, exam.id), with_for_update=True)
    if mark is None:
        mark = StudentMarkForExam(
            student_id=student_id, exam_id=exam.id, best_score_pct=score_pct, exp_earned=0,
            xexp_earned=Decimal(0), attempts_count=0,
        )
        session.add(mark)
    mark.best_score_pct = max(Decimal(mark.best_score_pct), score_pct)
    mark.attempts_count += 1
    exp = ExpRules.exam_exp_due(exam.max_exp, mark.best_score_pct, mark.exp_earned)
    xexp = ExpRules.xexp_due(exam.max_exp, mark.best_score_pct, mark.xexp_earned or Decimal(0))
    mark.exp_earned += exp
    mark.xexp_earned = Decimal(mark.xexp_earned or 0) + xexp
    award_exp(session, student_id, "exam", exam.id, exp, xexp)
    refresh_rank(session, student_id)
    Engagement.on_exam_graded(session, student_id, score_pct)
    return exp


# ====================================================================
# The service
# ====================================================================
class ProgressService:
    def __init__(self, db: Db) -> None:
        self._db = db

    # -------------------------------------------------------------- notes
    def complete_note(self, student_id: int, note_id: int) -> StudentCompletedNoteRead:
        """Mark a published note as read and award its max_exp (and 2.5% of it
        as Xexp). Calling it again returns the first completion and awards nothing more."""
        with self._db.session() as session:
            note = session.get(Note, note_id)
            if note is None:
                raise NoteNotFoundError(note_id)
            if not note.published:
                raise NoteNotPublishedError(note_id)
            xexp = ExpRules.note_xexp(note.max_exp)
            inserted = session.execute(
                pg_insert(StudentCompletedNote)
                .values(student_id=student_id, note_id=note_id, exp_awarded=note.max_exp, xexp_awarded=xexp)
                .on_conflict_do_nothing()
                .returning(StudentCompletedNote.note_id)
            ).first()
            if inserted is not None:
                award_exp(session, student_id, "note", note_id, note.max_exp, xexp)
                refresh_rank(session, student_id)
                Engagement.on_note_completed(session, student_id)
            done = session.get(StudentCompletedNote, (student_id, note_id), populate_existing=True)
            return StudentCompletedNoteRead.model_validate(done)

    def is_note_completed(self, student_id: int, note_id: int) -> bool:
        with self._db.session() as session:
            return session.get(StudentCompletedNote, (student_id, note_id)) is not None

    def completed_notes(self, student_id: int, course_id: int | None = None) -> list[StudentCompletedNoteRead]:
        query = sa.select(StudentCompletedNote).where(StudentCompletedNote.student_id == student_id)
        if course_id is not None:
            query = (
                query.join(Note, Note.id == StudentCompletedNote.note_id)
                .join(Topic, Topic.id == Note.topic_id)
                .where(Topic.course_id == course_id)
            )
        with self._db.session() as session:
            rows = session.scalars(query.order_by(StudentCompletedNote.completed_at)).all()
            return [StudentCompletedNoteRead.model_validate(r) for r in rows]

    # -------------------------------------------------------------- marks
    def exercise_mark(self, student_id: int, exercise_id: int) -> StudentMarkForExerciseRead | None:
        with self._db.session() as session:
            mark = session.get(StudentMarkForExercise, (student_id, exercise_id))
            return StudentMarkForExerciseRead.model_validate(mark) if mark else None

    def exam_mark(self, student_id: int, exam_id: int) -> StudentMarkForExamRead | None:
        with self._db.session() as session:
            mark = session.get(StudentMarkForExam, (student_id, exam_id))
            return StudentMarkForExamRead.model_validate(mark) if mark else None

    def exercise_marks(self, student_id: int, course_id: int | None = None) -> list[StudentMarkForExerciseRead]:
        query = sa.select(StudentMarkForExercise).where(StudentMarkForExercise.student_id == student_id)
        if course_id is not None:
            query = (
                query.join(Exercise, Exercise.id == StudentMarkForExercise.exercise_id)
                .join(Topic, Topic.id == Exercise.topic_id)
                .where(Topic.course_id == course_id)
            )
        with self._db.session() as session:
            rows = session.scalars(query.order_by(StudentMarkForExercise.exercise_id)).all()
            return [StudentMarkForExerciseRead.model_validate(r) for r in rows]

    def exam_marks(self, student_id: int, course_id: int | None = None) -> list[StudentMarkForExamRead]:
        query = sa.select(StudentMarkForExam).where(StudentMarkForExam.student_id == student_id)
        if course_id is not None:
            query = query.join(Exam, Exam.id == StudentMarkForExam.exam_id).where(Exam.course_id == course_id)
        with self._db.session() as session:
            rows = session.scalars(query.order_by(StudentMarkForExam.exam_id)).all()
            return [StudentMarkForExamRead.model_validate(r) for r in rows]

    def exam_results(self, exam_id: int) -> list[StudentMarkForExamRead]:
        """Every student's mark for one exam, best first (lecturer view)."""
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(StudentMarkForExam)
                .where(StudentMarkForExam.exam_id == exam_id)
                .order_by(StudentMarkForExam.best_score_pct.desc(), StudentMarkForExam.student_id)
            ).all()
            return [StudentMarkForExamRead.model_validate(r) for r in rows]

    # ------------------------------------------------------------- ledger
    def ledger(self, student_id: int, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0) -> list[ExpLedgerRead]:
        limit, offset = clamp(limit, offset)
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(ExpLedger)
                .where(ExpLedger.student_id == student_id)
                .order_by(ExpLedger.created_at.desc(), ExpLedger.id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [ExpLedgerRead.model_validate(r) for r in rows]

    # ------------------------------------------------------------ courses
    def course_ids_of(self, student_id: int) -> list[int]:
        """Courses the student is in (touched a note, exercise or exam): student_courses view."""
        rows = self._db.fetch_all(
            "select course_id from student_courses where student_id = :s order by course_id", {"s": student_id}
        )
        return [r["course_id"] for r in rows]

    def student_ids_in(self, course_id: int) -> list[int]:
        rows = self._db.fetch_all(
            "select student_id from student_courses where course_id = :c order by student_id", {"c": course_id}
        )
        return [r["student_id"] for r in rows]

    def course_progress(self, student_id: int, course_id: int) -> CourseProgressRead:
        row = self._db.fetch_one(
            """
            with course_notes as (
                select tn.id from topic_notes tn
                join course_topics ct on ct.id = tn.topic_id
                where ct.course_id = :c and tn.published
            ), course_exercises as (
                select e.id from exercises e
                join course_topics ct on ct.id = e.topic_id
                where ct.course_id = :c and e.published
            ), course_exams as (
                select x.id from exams x where x.course_id = :c and x.published
            )
            select
                (select count(*) from student_completed_notes s
                  where s.student_id = :s and s.note_id in (select id from course_notes))         as notes_completed,
                (select count(*) from course_notes)                                               as notes_total,
                (select count(*) from student_mark_for_exercise m
                  where m.student_id = :s and m.exercise_id in (select id from course_exercises)) as exercises_done,
                (select count(*) from course_exercises)                                           as exercises_total,
                (select count(*) from student_mark_for_exams m
                  where m.student_id = :s and m.exam_id in (select id from course_exams))         as exams_done,
                (select count(*) from course_exams)                                               as exams_total,
                coalesce((select sum(exp_awarded) from student_completed_notes s
                           join topic_notes tn on tn.id = s.note_id
                           join course_topics ct on ct.id = tn.topic_id
                          where s.student_id = :s and ct.course_id = :c), 0)
              + coalesce((select sum(exp_earned) from student_mark_for_exercise m
                           join exercises e on e.id = m.exercise_id
                           join course_topics ct on ct.id = e.topic_id
                          where m.student_id = :s and ct.course_id = :c), 0)
              + coalesce((select sum(exp_earned) from student_mark_for_exams m
                           join exams x on x.id = m.exam_id
                          where m.student_id = :s and x.course_id = :c), 0)                as exp_earned
            """,
            {"s": student_id, "c": course_id},
        )
        done = row["notes_completed"] + row["exercises_done"]
        total = row["notes_total"] + row["exercises_total"]
        return CourseProgressRead(
            student_id=student_id,
            course_id=course_id,
            **{k: int(v) for k, v in row.items()},
            completion_pct=round(100 * done / total, 2) if total else 0.0,
        )

    # ------------------------------------------------------- reading time
    MAX_TICK_SECONDS = 60          # the note page reports at most this much per call
    READING_FACTOR = 3             # Reading is full at 3x the estimated time ("repetition")

    def add_reading_time(self, student_id: int, note_id: int, seconds: int) -> int:
        """Add active reading time reported by the note page. Never more than
        MAX_TICK_SECONDS per call, nor more than the real time since the last
        report, so a page left open or a forged request cannot inflate it.
        Returns the note's total seconds for this student."""
        seconds = max(0, min(int(seconds), self.MAX_TICK_SECONDS))
        with self._db.session() as session:
            before = session.execute(sa.text("select seconds from student_note_time where student_id = :s and note_id = :n for update"),
                                     {"s": student_id, "n": note_id}).scalar() or 0
            row = session.execute(sa.text(
                """
                insert into student_note_time (student_id, note_id, seconds, updated_at)
                values (:s, :n, :sec, now())
                on conflict (student_id, note_id) do update
                   set seconds = student_note_time.seconds + least(
                           excluded.seconds,
                           greatest(0, ceil(extract(epoch from now() - student_note_time.updated_at)))::int + 2),
                       updated_at = now()
                returning seconds
                """), {"s": student_id, "n": note_id, "sec": seconds}).first()
            Engagement.on_reading(session, student_id, int(row[0]) - before)   # daily task "read 10 min"
            return int(row[0])

    # --------------------------------------------------------- skill graph
    def skill_graph(self, student_id: int) -> dict:
        """The four axes of the skill graph, each 0-100 (None = nothing to measure yet).

          reading   per note opened: time spent / (3 x estimated reading time), capped at 1
          practice  average best score over the exercises taken
          exams     average best score over the exams taken
          subjects  average completion over the courses started
        """
        from src.content.StudiamoMarkdown import reading_seconds

        row = self._db.fetch_one(
            """
            select
              (select avg(best_score_pct) from student_mark_for_exercise where student_id = :s) as practice,
              (select count(*)            from student_mark_for_exercise where student_id = :s) as exercises,
              (select avg(best_score_pct) from student_mark_for_exams    where student_id = :s) as exams,
              (select count(*)            from student_mark_for_exams    where student_id = :s) as exams_taken
            """, {"s": student_id})
        times = self._db.fetch_all(
            """select t.note_id, t.seconds, n.reading_seconds, n.md_path
                 from student_note_time t join topic_notes n on n.id = t.note_id
                where t.student_id = :s and t.seconds > 0""", {"s": student_id})
        ratios = []
        for t in times:
            estimate = t["reading_seconds"]
            if not estimate:   # older notes: estimate once from their text
                try:
                    from src.storage.MarkdownStore import LocalMarkdownStore

                    estimate = reading_seconds(LocalMarkdownStore.from_env().read(t["md_path"]))
                    self._db.execute("update topic_notes set reading_seconds = :r where id = :n", {"r": estimate, "n": t["note_id"]})
                except (OSError, ValueError):
                    estimate = 300
            ratios.append(min(1.0, t["seconds"] / (self.READING_FACTOR * estimate)))
        courses = self.course_ids_of(student_id)
        completions = [self.course_progress(student_id, c).completion_pct for c in courses]

        def pct(v):
            return None if v is None else round(float(v), 1)

        with self._db.session() as session:
            eng = Engagement.summary(session, student_id)
        return {
            "streak": pct(min(100.0, 100 * eng["streak"] / Engagement.STREAK_FULL)),
            "time": pct(min(100.0, 100 * eng["seconds"] / Engagement.TIME_FULL_SECONDS)),
            "badges": pct(100 * eng["badges_earned"] / len(Engagement.BADGES)),
            "engagement": eng,
            "reading": pct(100 * sum(ratios) / len(ratios)) if ratios else None,
            "practice": pct(row["practice"]),
            "exams": pct(row["exams"]),
            "subjects": pct(sum(completions) / len(completions)) if completions else None,
            "counts": {"notes": len(ratios), "exercises": int(row["exercises"]), "exams": int(row["exams_taken"]),
                       "courses": len(courses), "minutes": round(sum(t["seconds"] for t in times) / 60),
                       "streak": eng["streak"], "hours": round(eng["seconds"] / 3600, 1),
                       "badges": eng["badges_earned"], "badges_total": len(Engagement.BADGES)},
        }

    # --------------------------------------------------------------- rank
    def recalculate_rank(self, student_id: int) -> None:
        """Recompute rank_level and its letter from total Xexp (e.g. after changing ExpRules)."""
        with self._db.session() as session:
            refresh_rank(session, student_id)

    def graded_exercise_attempts(self, student_id: int, exercise_id: int) -> int:
        with self._db.session() as session:
            return session.scalar(
                sa.select(sa.func.count())
                .select_from(ExerciseAttempt)
                .where(
                    ExerciseAttempt.student_id == student_id,
                    ExerciseAttempt.exercise_id == exercise_id,
                    ExerciseAttempt.status == AttemptStatus.GRADED,
                )
            ) or 0

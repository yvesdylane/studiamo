"""End-to-end check of AttemptService + ProgressService + PlayerService
against the real database in .env: a student reads a note, takes exercises
and exams, gets graded (auto and by hand), earns exp and a rank.

    uv run python -m tests.test_attempt_flow

Creates a lecturer, two students and a TESTA_ course, all removed at the end.
Attempt records go to a temporary folder. Code answers are run by a fake
CodeRunner; exam deadlines are tested with a fake clock.
"""

from __future__ import annotations

import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv

import src.models  # noqa: F401
from src.attempt.AttemptResultDto import ManualGrade
from src.attempt.AttemptStatus import AttemptStatus
from src.attempt.AttemptSubmitDto import AnswerSubmit, AttemptSubmit
from src.attempt.service.AttemptErrors import (
    AnswerNotInAttemptError,
    AttemptAlreadySubmittedError,
    AttemptNotYoursError,
    ExamAttemptsExhaustedError,
    ManualGradeInvalidError,
)
from src.attempt.service.AttemptService import AttemptService
from src.attempt.service.CodeRunner import RunResult
from src.course.dto.CourseDto import CourseCreate
from src.course.dto.NoteDto import NoteCreate
from src.course.dto.TopicDto import TopicCreate
from src.course.service.CourseService import CourseService
from src.course.service.NoteService import NoteService
from src.course.service.TopicService import TopicService
from src.db.DB import Db
from src.exam.dto.ExamDto import ExamCreate
from src.exam.dto.ExamQuestionDto import ExamQuestionBase
from src.exam.models.Exam import ExamKind
from src.exam.service.ExamErrors import ExamNotOpenError
from src.exam.service.ExamService import ExamService
from src.exercise.dto.ExerciseDto import ExerciseCreate
from src.exercise.dto.ExerciseQuestionDto import ExerciseQuestionBase
from src.exercise.service.ExerciseService import ExerciseService
from src.player.PlayerStats import PlayerRank
from src.player.service.PlayerService import PlayerService
from src.progress.service.ProgressService import ProgressService
from src.question.dto.QuestionDto import QuestionCreate
from src.question.dto.QuestionOptionDto import QuestionOptionCreate
from src.question.dto.QuestionTestCaseDto import QuestionTestCaseCreate
from src.question.models.Question import CodeLanguage, QuestionType
from src.question.service.QuestionService import QuestionService
from src.storage.MarkdownStore import LocalMarkdownStore
from tests.harness import Checks


class FakeRunner:
    """Pretends to run Python: prints the sum of the numbers on stdin when the
    source mentions sum(, otherwise prints nothing."""

    def run(self, language: CodeLanguage, source: str, stdin: str) -> RunResult:
        if "sum(" in source:
            return RunResult(stdout=f"{sum(int(x) for x in stdin.split())}\n")
        return RunResult(stdout="", error="NameError: oops")


def mcq(topic_id: int, body: str) -> QuestionCreate:
    return QuestionCreate(type=QuestionType.MCQ, body_md=body, topic_id=topic_id,
                          options=[QuestionOptionCreate(label="right", is_correct=True), QuestionOptionCreate(label="wrong")])


def pick(question, correct: bool) -> AnswerSubmit:
    """Answer a QuestionPublic mcq by label (the student view has no is_correct)."""
    label = "right" if correct else "wrong"
    return AnswerSubmit(question_id=question.id, selected_option_ids=[next(o.id for o in question.options if o.label == label)])


def main() -> int:
    load_dotenv()
    db = Db.from_env()
    run = uuid.uuid4().hex[:6].upper()
    t = Checks()
    tmp = tempfile.TemporaryDirectory()
    store = LocalMarkdownStore(tmp.name)
    clock = [datetime.now(UTC)]
    print(f"Database: {db}")

    def new_user(name: str) -> int:
        return db.scalar("insert into users (name, email) values (:n, :e) returning id",
                         {"n": name, "e": f"{name.lower()}-{run.lower()}@studiamo-test.example"})

    lecturer, student, rival = new_user("Lecturer"), new_user("Student"), new_user("Rival")
    attempts = AttemptService(db, store, FakeRunner(), clock=lambda: clock[0])
    progress, players = ProgressService(db), PlayerService(db)
    questions, exams = QuestionService(db), ExamService(db)

    try:
        course = CourseService(db).create(CourseCreate(code=f"TESTA_{run}", title="Flow"), created_by=lecturer)
        topic = TopicService(db).create(TopicCreate(course_id=course.id, title="Basics", position=0))
        note = NoteService(db).create(NoteCreate(topic_id=topic.id, title="Read me", md_path="n.md", position=0, max_exp=10, published=True))
        q_mcq = questions.create(mcq(topic.id, "Pick right"), author_id=lecturer)
        q_mcq2 = questions.create(mcq(topic.id, "Pick right again"), author_id=lecturer)
        q_short = questions.create(QuestionCreate(type=QuestionType.SHORT_ANSWER, body_md="Explain", topic_id=None, points=2), author_id=lecturer)
        q_code = questions.create(QuestionCreate(
            type=QuestionType.CODE, body_md="Sum the input", topic_id=None, code_language=CodeLanguage.PYTHON,
            test_cases=[QuestionTestCaseCreate(input="1 2", expected_output="3", is_hidden=False, weight=1),
                        QuestionTestCaseCreate(input="5 5", expected_output="10", weight=3)]), author_id=lecturer)

        t.section("note")
        done = progress.complete_note(student, note.id)
        again = progress.complete_note(student, note.id)
        t.check("note exp awarded once", done.exp_awarded == 10 and again.completed_at == done.completed_at)
        t.check("total_exp 10", players.get_stats(student).total_exp == 10)

        t.section("exercise: auto + manual grading")
        ex = ExerciseService(db).create(ExerciseCreate(
            topic_id=topic.id, title="Drill", max_exp=50, retake_decay=0.5, published=True,
            questions=[ExerciseQuestionBase(question_id=q_mcq.id, position=0), ExerciseQuestionBase(question_id=q_short.id, position=1)]))
        started = attempts.start_exercise(student, ex.id)
        t.check("attempt 1 opened", started.attempt.attempt_no == 1 and not started.resumed)
        t.check("students see no answers", all("is_correct" not in o.model_dump() for q in started.questions for o in q.options))
        t.check("start again resumes", attempts.start_exercise(student, ex.id).attempt.id == started.attempt.id)
        mq = started.questions[0]
        t.expect_error("other student cannot submit", AttemptNotYoursError,
                       lambda: attempts.submit_exercise(started.attempt.id, rival, AttemptSubmit()))
        t.expect_error("answer to a question not in the attempt", AnswerNotInAttemptError,
                       lambda: attempts.submit_exercise(started.attempt.id, student,
                                                        AttemptSubmit(answers=[AnswerSubmit(question_id=q_code.id, answer_text="x")])))
        result = attempts.submit_exercise(started.attempt.id, student, AttemptSubmit(answers=[
            pick(mq, True), AnswerSubmit(question_id=q_short.id, answer_text="Because | reasons")]))
        t.check("short answer waits for a lecturer", result.pending_manual and result.attempt.status == AttemptStatus.SUBMITTED)
        t.check("no exp yet", result.exp_awarded == 0 and players.get_stats(student).total_exp == 10)
        t.check("duration computed by the database", result.attempt.duration_seconds is not None)
        t.expect_error("submit twice", AttemptAlreadySubmittedError,
                       lambda: attempts.submit_exercise(started.attempt.id, student, AttemptSubmit()))
        t.check("listed as pending", [a.id for a in attempts.pending_exercise_attempts(course.id)] == [started.attempt.id])
        record = attempts.read_record("exercise", started.attempt.id, student_id=student)
        t.check("record has the student's answer", "Because | reasons" in record and "Pick right" in record)
        t.expect_error("points above the question's worth", ManualGradeInvalidError,
                       lambda: attempts.grade_exercise_attempt(started.attempt.id, ManualGrade(points={q_short.id: 5}), lecturer))
        t.expect_error("points for a question that is not pending", ManualGradeInvalidError,
                       lambda: attempts.grade_exercise_attempt(started.attempt.id, ManualGrade(points={q_mcq.id: 1}), lecturer))
        graded = attempts.grade_exercise_attempt(started.attempt.id, ManualGrade(points={q_short.id: 1}, comment_md="Half right."), lecturer)
        t.check("graded 2/3 = 66.67%", graded.attempt.status == AttemptStatus.GRADED and graded.attempt.score_pct == 66.67, graded.attempt)
        t.check("exp floor(50 * 0.6667) = 33", graded.exp_awarded == 33, graded.exp_awarded)
        t.check("record keeps the manual grading", "Half right." in attempts.read_record("exercise", started.attempt.id))

        second = attempts.start_exercise(student, ex.id)
        result = attempts.submit_exercise(second.attempt.id, student, AttemptSubmit(answers=[pick(second.questions[0], True)]))
        t.check("attempt 2, blank short answer is just 0", result.attempt.attempt_no == 2 and not result.pending_manual)
        t.check("retake decay: floor(50 * 0.3333 * 0.5) = 8", result.exp_awarded == 8, result.exp_awarded)
        mark = progress.exercise_mark(student, ex.id)
        t.check("mark keeps best score, sums exp", (mark.best_score_pct, mark.exp_earned, mark.attempts_count) == (66.67, 41, 2), mark)

        t.section("scheduled exam with code runner")
        now = clock[0]
        sched = exams.create(ExamCreate(course_id=course.id, title="Midterm", kind=ExamKind.SCHEDULED, max_exp=100,
                                        opens_at=now - timedelta(hours=1), closes_at=now + timedelta(hours=1),
                                        duration_minutes=30, question_ids=[q_mcq.id, q_code.id]), created_by=lecturer)
        exams.set_questions(sched.id, [ExamQuestionBase(question_id=q_mcq.id, points=3), ExamQuestionBase(question_id=q_code.id)])
        exams.publish(sched.id)
        es = attempts.start_exam(student, sched.id)
        t.check("both pool questions drawn", {q.id for q in es.questions} == {q_mcq.id, q_code.id})
        t.check("points override shown", next(q.points for q in es.questions if q.id == q_mcq.id) == 3)
        t.check("deadline = start + 30 min", es.deadline == es.attempt.started_at + timedelta(minutes=30))
        again = attempts.start_exam(student, sched.id)
        t.check("resume keeps the same option order", again.resumed and again.questions == es.questions)
        exam_mcq = next(q for q in es.questions if q.id == q_mcq.id)
        result = attempts.submit_exam(es.attempt.id, student, AttemptSubmit(answers=[
            pick(exam_mcq, True), AnswerSubmit(question_id=q_code.id, answer_text="print(sum(map(int, input().split())))")]))
        code_result = next(r for r in result.questions if r.question_id == q_code.id)
        t.check("code graded by runner: 2/2 tests", (code_result.test_cases_passed, code_result.test_cases_total) == (2, 2))
        t.check("100% -> 100 exp", result.attempt.score_pct == 100 and result.exp_awarded == 100, result)
        t.expect_error("one attempt only", ExamAttemptsExhaustedError, lambda: attempts.start_exam(student, sched.id))
        rs = attempts.start_exam(rival, sched.id)
        result = attempts.submit_exam(rs.attempt.id, rival, AttemptSubmit(answers=[
            AnswerSubmit(question_id=q_code.id, answer_text="print(1)")]))
        code_result = next(r for r in result.questions if r.question_id == q_code.id)
        t.check("failing code earns 0", code_result.points_earned == 0 and result.attempt.score_pct == 0)
        t.check("exam_results best first", [m.student_id for m in progress.exam_results(sched.id)] == [student, rival])
        t.expect_error("closed exam cannot start", ExamNotOpenError, lambda: AttemptService(
            db, store, clock=lambda: now + timedelta(hours=2)).start_exam(lecturer, sched.id))

        t.section("system exam: random subset, best-score exp")
        system = exams.create(ExamCreate(course_id=course.id, title="Practice", kind=ExamKind.SYSTEM,
                                         topic_ids=[topic.id], questions_per_attempt=1, max_exp=40))
        exams.publish(system.id)
        s1 = attempts.start_exam(student, system.id)
        t.check("one question drawn from the topic", len(s1.questions) == 1 and s1.questions[0].id in {q_mcq.id, q_mcq2.id})
        r1 = attempts.submit_exam(s1.attempt.id, student, AttemptSubmit(answers=[pick(s1.questions[0], False)]))
        s2 = attempts.start_exam(student, system.id)
        r2 = attempts.submit_exam(s2.attempt.id, student, AttemptSubmit(answers=[pick(s2.questions[0], True)]))
        s3 = attempts.start_exam(student, system.id)
        r3 = attempts.submit_exam(s3.attempt.id, student, AttemptSubmit(answers=[pick(s3.questions[0], True)]))
        t.check("exp: 0, then 40, then nothing more", (r1.exp_awarded, r2.exp_awarded, r3.exp_awarded) == (0, 40, 0))
        t.check("unlimited attempts", s3.attempt.attempt_no == 3)

        t.section("deadlines")
        timed = exams.create(ExamCreate(course_id=course.id, title="Speed", kind=ExamKind.SYSTEM,
                                        topic_ids=[topic.id], duration_minutes=1))
        exams.publish(timed.id)
        clock[0] = datetime.now(UTC)
        late = attempts.start_exam(student, timed.id)
        clock[0] += timedelta(minutes=10)
        result = attempts.submit_exam(late.attempt.id, student,
                                      AttemptSubmit(answers=[pick(q, True) for q in late.questions]))
        t.check("late submission: answers ignored", result.late and result.attempt.score_pct == 0)
        abandoned = attempts.start_exam(student, timed.id)
        clock[0] += timedelta(minutes=10)
        fresh = attempts.start_exam(student, timed.id)
        closed = attempts.get_exam_attempt(abandoned.attempt.id)
        t.check("abandoned attempt closed on next start", closed.status == AttemptStatus.GRADED and fresh.attempt.id != abandoned.attempt.id)

        t.section("player and progress")
        stats = players.get_stats(student)
        t.check("total exp = 10 + 33 + 8 + 100 + 40", stats.total_exp == 191, stats.total_exp)
        ledger = progress.ledger(student, limit=100)
        t.check("ledger sums to total_exp", sum(x.amount for x in ledger) == 191 and ledger[-1].source == "note")
        # Xexp: note 10 * 2.5% = 0.25; exercise best 66.67% < 75% -> 0;
        # midterm 100 * 0.85 = 85; practice 40 * 0.85 = 34; speed 0  -> 119.25
        t.check("Xexp = 0.25 + 0 + 85 + 34", stats.total_xexp == 119.25, stats.total_xexp)
        t.check("ledger Xexp sums to total_xexp", round(sum(x.xexp for x in ledger), 2) == 119.25)
        t.check("exercise below 75% gives no Xexp", progress.exercise_mark(student, ex.id).xexp_earned == 0)
        # rank totals (growth 1.18): 0, 10, 20.9, 32.86, 46.08, 60.77, 77.21, 95.71, 116.63, 140.42
        #   -> 119.25 is rank level 8 = E4
        t.check("rank E4 (level 8, letter E)", (stats.rank_level, stats.rank_label, stats.rank) == (8, "E4", PlayerRank.E)
                and stats.rank_updated_at is not None, stats)
        level = players.get_level(student)
        # level totals 0, 20, 45, 78, 122, 182, 268 -> 191 is level 5, 9 into a level of 86
        t.check("level from exp (191 -> level 5)", (level.level, level.exp_in_level, level.exp_to_next_level) == (5, 9, 86), level)
        t.check("rank progress in the view", (level.rank_label, level.xexp_in_rank, level.xexp_to_next_rank) == ("E4", 2.62, 23.79), level)
        t.check("ahead of the rival", players.position_of(student) < players.position_of(rival))
        board = players.leaderboard(limit=100)
        t.check("leaderboard has public user info", any(e.user.id == student and e.total_exp == 191 for e in board))
        cp = progress.course_progress(student, course.id)
        t.check("course progress", (cp.notes_completed, cp.notes_total, cp.exercises_done, cp.exams_done, cp.exp_earned)
                == (1, 1, 1, 3, 191), cp)
        t.check("student is in the course (view)", course.id in progress.course_ids_of(student))
        t.check("course lists its students", set(progress.student_ids_in(course.id)) == {student, rival})
    finally:
        db.execute("delete from courses where code like :p", {"p": f"TESTA_{run}%"})
        db.execute("delete from questions where author_id = :u", {"u": lecturer})
        db.execute("delete from users where id in (:a, :b, :c)", {"a": lecturer, "b": student, "c": rival})
        db.close()
        tmp.cleanup()
        print("\ntest data removed")

    return t.summary()


if __name__ == "__main__":
    sys.exit(main())

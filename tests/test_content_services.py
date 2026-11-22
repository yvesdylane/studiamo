"""Checks TopicService, NoteService, QuestionService, ExerciseService and
ExamService against the real database in .env.

    uv run python -m tests.test_content_services

Creates one test lecturer and courses with codes starting TESTC_, removed at
the end even if a check fails. Note files go to a temporary folder.
"""

from __future__ import annotations

import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv
from pydantic import ValidationError

import src.models  # noqa: F401
from src.course.dto.CourseDto import CourseCreate
from src.course.dto.NoteDto import NoteCreate, NoteUpdate
from src.course.dto.TopicDto import TopicCreate, TopicUpdate
from src.course.service.CourseErrors import CourseNotFoundError
from src.course.service.CourseService import CourseService
from src.course.service.NoteService import NoteService
from src.course.service.TopicErrors import (
    NoteNotPublishedError,
    TopicNotFoundError,
    TopicOrderMismatchError,
    TopicPositionTakenError,
)
from src.course.service.TopicService import TopicService
from src.db.DB import Db
from src.exam.dto.ExamDto import ExamCreate, ExamUpdate
from src.exam.dto.ExamQuestionDto import ExamQuestionBase
from src.exam.models.Exam import ExamKind
from src.exam.service.ExamErrors import ExamInvalidError, ExamNotPublishedError, ExamTopicOutsideCourseError
from src.exam.service.ExamService import ExamService
from src.exercise.dto.ExerciseDto import ExerciseCreate, ExerciseUpdate
from src.exercise.dto.ExerciseQuestionDto import ExerciseQuestionBase
from src.exercise.service.ExerciseErrors import (
    ExerciseQuestionAlreadyAddedError,
    ExerciseQuestionsInvalidError,
    NoteExerciseCourseMismatchError,
)
from src.exercise.service.ExerciseService import ExerciseService
from src.question.dto.QuestionDto import QuestionCreate, QuestionUpdate
from src.question.dto.QuestionOptionDto import QuestionOptionCreate, QuestionOptionUpdate
from src.question.dto.QuestionTestCaseDto import QuestionTestCaseCreate
from src.question.models.Question import CodeLanguage, QuestionType
from src.question.service.QuestionErrors import QuestionInUseError, QuestionInvalidError, QuestionNotFoundError
from src.question.service.QuestionService import QuestionService
from src.storage.MarkdownStore import LocalMarkdownStore
from tests.harness import MISSING_ID, Checks


def mcq(topic_id: int, body: str = "2 + 2 = ?") -> QuestionCreate:
    return QuestionCreate(
        type=QuestionType.MCQ, body_md=body, topic_id=topic_id,
        options=[QuestionOptionCreate(label="4", is_correct=True), QuestionOptionCreate(label="5")],
    )


def main() -> int:
    load_dotenv()
    db = Db.from_env()
    run = uuid.uuid4().hex[:6].upper()
    t = Checks()
    tmp = tempfile.TemporaryDirectory()
    print(f"Database: {db}")

    lecturer = db.scalar("insert into users (name, email) values ('Content test', :e) returning id",
                         {"e": f"content-{run.lower()}@studiamo-test.example"})
    courses, topics, notes = CourseService(db), TopicService(db), NoteService(db, LocalMarkdownStore(tmp.name))
    questions, exercises, exams = QuestionService(db), ExerciseService(db), ExamService(db)

    try:
        course = courses.create(CourseCreate(code=f"TESTC_{run}A", title="Networks"), created_by=lecturer)
        other = courses.create(CourseCreate(code=f"TESTC_{run}B", title="Other"), created_by=lecturer)

        t.section("topics")
        t1 = topics.create(TopicCreate(course_id=course.id, title="Intro", position=0))
        t2 = topics.create(TopicCreate(course_id=course.id, title="TCP", position=1))
        t3 = topics.create(TopicCreate(course_id=course.id, title="UDP", position=2))
        other_topic = topics.create(TopicCreate(course_id=other.id, title="Elsewhere", position=0))
        t.check("list ordered by position", [x.id for x in topics.list_for_course(course.id)] == [t1.id, t2.id, t3.id])
        t.expect_error("position taken", TopicPositionTakenError,
                       lambda: topics.create(TopicCreate(course_id=course.id, title="dup", position=1)))
        t.expect_error("position taken on update", TopicPositionTakenError, lambda: topics.update(t3.id, TopicUpdate(position=0)))
        t.expect_error("unknown course", CourseNotFoundError,
                       lambda: topics.create(TopicCreate(course_id=MISSING_ID, title="x", position=0)))
        reordered = topics.reorder(course.id, [t3.id, t1.id, t2.id])
        t.check("reorder swaps positions without collisions", [(x.id, x.position) for x in reordered] == [(t3.id, 0), (t1.id, 1), (t2.id, 2)])
        t.expect_error("reorder must list every topic", TopicOrderMismatchError, lambda: topics.reorder(course.id, [t1.id]))
        t.check("rename", topics.update(t1.id, TopicUpdate(title="Introduction")).title == "Introduction")

        t.section("notes")
        n1 = notes.create(NoteCreate(topic_id=t1.id, title="What is a network", md_path=f"notes/{run}/n1.md", position=0, max_exp=10))
        n2 = notes.create(NoteCreate(topic_id=t1.id, title="Layers", md_path=f"notes/{run}/n2.md", position=1, published=True))
        t.check("create", n1.id > 0 and n1.created_at is not None and not n1.published)
        t.check("published_only filter", [n.id for n in notes.list_for_topic(t1.id, published_only=True)] == [n2.id])
        t.expect_error("draft hidden from students", NoteNotPublishedError, lambda: notes.get_published(n1.id))
        t.check("publish", notes.update(n1.id, NoteUpdate(published=True)).published)
        notes.save_content(n1.id, "# Networks\nComputers talking.")
        t.check("content round trip", notes.read_content(n1.id).startswith("# Networks"))
        t.check("reorder", [n.id for n in notes.reorder(t1.id, [n2.id, n1.id])] == [n2.id, n1.id])
        t.check("list_for_course", {n.id for n in notes.list_for_course(course.id)} == {n1.id, n2.id})
        t.check("move to another topic", notes.move(n2.id, t2.id, 0).topic_id == t2.id)
        t.expect_error("note in unknown topic", TopicNotFoundError,
                       lambda: notes.create(NoteCreate(topic_id=MISSING_ID, title="x", md_path="x.md", position=0)))

        t.section("questions")
        q1 = questions.create(mcq(t1.id), author_id=lecturer)
        t.check("mcq with options", len(q1.options) == 2 and q1.options[0].question_id == q1.id)
        public = questions.get_public(q1.id)
        t.check("public view hides is_correct", "is_correct" not in public.options[0].model_dump())
        code = questions.create(QuestionCreate(
            type=QuestionType.CODE, body_md="Print the sum", topic_id=t2.id, code_language=CodeLanguage.PYTHON,
            test_cases=[QuestionTestCaseCreate(input="1 2", expected_output="3", is_hidden=False),
                        QuestionTestCaseCreate(input="5 5", expected_output="10")]), author_id=lecturer)
        t.check("public code question shows only visible tests", len(questions.get_public(code.id).test_cases) == 1)
        short = questions.create(QuestionCreate(type=QuestionType.SHORT_ANSWER, body_md="Define latency", topic_id=t2.id, points=2), author_id=lecturer)
        t.expect_error("mcq needs a correct option (DTO)", ValidationError,
                       lambda: QuestionCreate(type=QuestionType.MCQ, body_md="x", options=[QuestionOptionCreate(label="a"), QuestionOptionCreate(label="b")]))
        t.expect_error("cannot remove below 2 options", QuestionInvalidError, lambda: questions.delete_option(q1.options[1].id))
        t.expect_error("second correct option needs allow_multiple", QuestionInvalidError,
                       lambda: questions.update_option(q1.options[1].id, QuestionOptionUpdate(is_correct=True)))
        questions.add_option(q1.id, QuestionOptionCreate(label="22"))
        t.check("add option", len(questions.get(q1.id).options) == 3)
        t.expect_error("code question keeps a test case", QuestionInvalidError,
                       lambda: [questions.delete_test_case(tc.id) for tc in code.test_cases])
        t.check("first delete kept, the last one refused", len(questions.get(code.id).test_cases) == 1)
        t.check("update + clear explanation",
                questions.update(q1.id, QuestionUpdate(explanation_md=None, difficulty=2)).difficulty == 2)
        t.check("list by course", {q.id for q in questions.list(course_id=course.id)} == {q1.id, code.id, short.id})
        t.check("list by type", [q.id for q in questions.list(course_id=course.id, type=QuestionType.CODE)] == [code.id])

        t.section("exercises")
        ex = exercises.create(ExerciseCreate(topic_id=t1.id, title="Drill", max_exp=50,
                                             questions=[ExerciseQuestionBase(question_id=q1.id, position=0)]))
        t.check("create with questions", [q.question_id for q in ex.questions] == [q1.id])
        t.check("add_question appends", [q.position for q in exercises.add_question(ex.id, short.id).questions] == [0, 1])
        t.expect_error("same question twice", ExerciseQuestionAlreadyAddedError, lambda: exercises.add_question(ex.id, short.id))
        t.expect_error("unknown question", QuestionNotFoundError, lambda: exercises.add_question(ex.id, MISSING_ID))
        t.expect_error("duplicates in set_questions", ExerciseQuestionsInvalidError,
                       lambda: exercises.set_questions(ex.id, [ExerciseQuestionBase(question_id=q1.id, position=0)] * 2))
        t.check("remove_question", [q.question_id for q in exercises.remove_question(ex.id, short.id).questions] == [q1.id])
        t.check("update", exercises.update(ex.id, ExerciseUpdate(published=True, retake_decay=0.25)).retake_decay == 0.25)
        exercises.attach_to_note(n1.id, ex.id)
        exercises.attach_to_note(n1.id, ex.id)  # idempotent
        t.check("attach to note", [e.id for e in exercises.list_for_note(n1.id)] == [ex.id])
        other_ex = exercises.create(ExerciseCreate(topic_id=other_topic.id, title="Elsewhere"))
        t.expect_error("note and exercise from different courses", NoteExerciseCourseMismatchError,
                       lambda: exercises.attach_to_note(n1.id, other_ex.id))
        t.expect_error("question used by an exercise cannot be deleted", QuestionInUseError, lambda: questions.delete(q1.id))
        exercises.detach_from_note(n1.id, ex.id)
        t.check("detach", exercises.list_for_note(n1.id) == [])

        t.section("exams")
        now = datetime.now(UTC)
        sched = exams.create(ExamCreate(course_id=course.id, title="Midterm", kind=ExamKind.SCHEDULED,
                                        opens_at=now - timedelta(hours=1), closes_at=now + timedelta(hours=1),
                                        duration_minutes=30, question_ids=[q1.id, short.id], max_exp=100),
                             created_by=lecturer)
        t.check("scheduled exam: one attempt, explicit pool", sched.max_attempts == 1 and len(sched.questions) == 2)
        system = exams.create(ExamCreate(course_id=course.id, title="Practice", kind=ExamKind.SYSTEM, topic_ids=[t1.id, t2.id]))
        t.check("system exam has no author", system.created_by is None and len(system.topics) == 2)
        t.check("system pool = active questions of topics", exams.pool_size(system.id) == 3)
        questions.deactivate(code.id)
        t.check("deactivated question leaves the pool", exams.pool_size(system.id) == 2)
        t.expect_error("scheduled exam needs an author", ExamInvalidError,
                       lambda: exams.create(ExamCreate(course_id=course.id, title="x", kind=ExamKind.SCHEDULED, opens_at=now,
                                                       closes_at=now + timedelta(hours=1), question_ids=[q1.id])))
        t.expect_error("topic of another course", ExamTopicOutsideCourseError,
                       lambda: exams.create(ExamCreate(course_id=course.id, title="x", kind=ExamKind.SYSTEM, topic_ids=[other_topic.id])))
        t.expect_error("closes before it opens (update)", ExamInvalidError,
                       lambda: exams.update(sched.id, ExamUpdate(closes_at=now - timedelta(hours=2))))
        t.expect_error("questions_per_attempt larger than pool", ExamInvalidError,
                       lambda: exams.update(sched.id, ExamUpdate(questions_per_attempt=5)))
        t.check("set_questions with points override",
                exams.set_questions(sched.id, [ExamQuestionBase(question_id=q1.id, points=3)]).questions[0].points == 3)
        t.expect_error("set_topics on a scheduled exam", ExamInvalidError, lambda: exams.set_topics(sched.id, [t1.id]))
        t.expect_error("draft hidden from students", ExamNotPublishedError, lambda: exams.get_public(sched.id))
        exams.publish(sched.id)
        exams.publish(system.id)
        t.check("list_available", {e.id for e in exams.list_available(course.id)} == {sched.id, system.id})
        t.check("not available after closing",
                {e.id for e in exams.list_available(course.id, at=now + timedelta(hours=2))} == {system.id})
        t.check("list_upcoming", [e.id for e in exams.list_upcoming(course.id, at=now - timedelta(hours=2))] == [sched.id])

        t.section("cascade")
        topics.delete(t2.id)
        t.check("topic delete keeps bank questions, topic cleared", questions.get(short.id).topic_id is None)
        courses.delete(other.id)
        t.expect_error("course delete removes its topics", TopicNotFoundError, lambda: topics.get(other_topic.id))
    finally:
        db.execute("delete from courses where code like :p", {"p": f"TESTC_{run}%"})
        db.execute("delete from questions where author_id = :u", {"u": lecturer})
        db.execute("delete from users where id = :u", {"u": lecturer})
        db.close()
        tmp.cleanup()
        print("\ntest data removed")

    return t.summary()


if __name__ == "__main__":
    sys.exit(main())

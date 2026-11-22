"""The question bank: questions with their mcq options and code test cases.

    service = QuestionService(get_db())
    q = service.create(QuestionCreate(type=QuestionType.MCQ, body_md="2+2?", topic_id=3,
                                      options=[QuestionOptionCreate(label="4", is_correct=True),
                                               QuestionOptionCreate(label="5")]),
                       author_id=user.id)
    q = service.get(q.id)              # QuestionRead, shows the answers (authors)
    p = service.get_public(q.id)       # QuestionPublic, safe for students
    qs = service.list(topic_id=3, active_only=True)
    service.add_option(q.id, QuestionOptionCreate(label="22"))
    service.deactivate(q.id)           # hide from new exams; keeps history intact

After every change the service checks the question is still usable: an mcq
keeps >= 2 options and >= 1 correct (only one unless allow_multiple), a code
question keeps >= 1 test case (QuestionInvalidError otherwise).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from src.base.Paging import DEFAULT_PAGE_SIZE, clamp
from src.base.ServiceErrors import constraint_name
from src.course.modles.Topic import Topic
from src.course.service.TopicErrors import TopicNotFoundError
from src.db.DB import Db
from src.question.dto.QuestionDto import QuestionCreate, QuestionPublic, QuestionRead, QuestionUpdate
from src.question.dto.QuestionOptionDto import (
    QuestionOptionCreate,
    QuestionOptionRead,
    QuestionOptionUpdate,
)
from src.question.dto.QuestionTestCaseDto import (
    QuestionTestCaseCreate,
    QuestionTestCaseRead,
    QuestionTestCaseUpdate,
)
from src.question.models.Question import Question, QuestionType
from src.question.models.QuestionOption import QuestionOption
from src.question.models.QuestionTestCase import QuestionTestCase
from src.question.service.QuestionErrors import (
    QuestionInUseError,
    QuestionInvalidError,
    QuestionNotFoundError,
    QuestionOptionNotFoundError,
    QuestionTestCaseNotFoundError,
)

# tables that point at questions without ON DELETE CASCADE
IN_USE_FK_CONSTRAINTS = {
    "exercise_questions_question_id_fkey",
    "exam_questions_question_id_fkey",
    "exam_attempt_questions_question_id_fkey",
}
_WITH_CHILDREN = (selectinload(Question.options), selectinload(Question.test_cases))


def check_question(q: Question) -> None:
    """QuestionInvalidError if q (with its options and test cases loaded) is unusable."""
    if q.type == QuestionType.MCQ:
        if len(q.options) < 2:
            raise QuestionInvalidError("mcq questions need at least 2 options")
        correct = sum(o.is_correct for o in q.options)
        if correct < 1:
            raise QuestionInvalidError("mcq questions need at least 1 correct option")
        if correct > 1 and not q.allow_multiple:
            raise QuestionInvalidError("several correct options need allow_multiple=true")
    elif q.options:
        raise QuestionInvalidError("only mcq questions have options")
    if q.type == QuestionType.CODE:
        if q.code_language is None:
            raise QuestionInvalidError("code questions need a code_language")
        if not q.test_cases:
            raise QuestionInvalidError("code questions need at least 1 test case")
    elif q.test_cases:
        raise QuestionInvalidError("only code questions have test cases")


class QuestionService:
    def __init__(self, db: Db) -> None:
        self._db = db

    # ------------------------------------------------------------- create
    def create(self, data: QuestionCreate, author_id: int) -> QuestionRead:
        """author_id is the logged-in lecturer's id, never client input."""
        fields = data.model_dump(exclude={"options", "test_cases"})
        question = Question(**fields, author_id=author_id)
        question.options = [QuestionOption(**o.model_dump()) for o in data.options]
        question.test_cases = [QuestionTestCase(**t.model_dump()) for t in data.test_cases]
        with self._db.session() as session:
            self._check_topic(session, data.topic_id)
            session.add(question)
            session.flush()
            session.refresh(question)
            return QuestionRead.model_validate(question)

    # --------------------------------------------------------------- read
    def get(self, question_id: int) -> QuestionRead:
        with self._db.session() as session:
            return QuestionRead.model_validate(self._find(session, question_id))

    def get_public(self, question_id: int) -> QuestionPublic:
        with self._db.session() as session:
            return QuestionPublic.model_validate(self._find(session, question_id))

    def get_many(self, question_ids: Sequence[int]) -> list[QuestionRead]:
        """The questions in the order of question_ids; unknown ids are skipped."""
        with self._db.session() as session:
            found = {q.id: q for q in self._load(session, question_ids)}
            return [QuestionRead.model_validate(found[i]) for i in question_ids if i in found]

    def list(
        self,
        *,
        topic_id: int | None = None,
        course_id: int | None = None,
        author_id: int | None = None,
        type: QuestionType | None = None,
        active_only: bool = False,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> list[QuestionRead]:
        """Search the bank. Every filter is optional; newest first."""
        limit, offset = clamp(limit, offset)
        query = sa.select(Question).options(*_WITH_CHILDREN)
        if topic_id is not None:
            query = query.where(Question.topic_id == topic_id)
        if course_id is not None:
            query = query.join(Topic, Topic.id == Question.topic_id).where(Topic.course_id == course_id)
        if author_id is not None:
            query = query.where(Question.author_id == author_id)
        if type is not None:
            query = query.where(Question.type == type)
        if active_only:
            query = query.where(Question.is_active)
        with self._db.session() as session:
            questions = session.scalars(
                query.order_by(Question.id.desc()).limit(limit).offset(offset)
            ).all()
            return [QuestionRead.model_validate(q) for q in questions]

    # ------------------------------------------------------------- update
    def update(self, question_id: int, data: QuestionUpdate) -> QuestionRead:
        """Partial update. The type cannot change (create a new question instead).

        topic_id, difficulty, explanation_md and starter_code can be cleared by
        sending them as None; the other fields treat None as "leave it".
        """
        sent = data.model_fields_set
        with self._db.session() as session:
            q = self._find(session, question_id)
            for field in ("topic_id", "difficulty", "explanation_md", "starter_code"):
                if field in sent:
                    setattr(q, field, getattr(data, field))
            for field in ("body_md", "points", "is_active", "allow_multiple", "code_language"):
                value = getattr(data, field)
                if value is not None:
                    setattr(q, field, value)
            if "topic_id" in sent:
                self._check_topic(session, data.topic_id)
            check_question(q)
            session.flush()
            return QuestionRead.model_validate(q)

    def deactivate(self, question_id: int) -> QuestionRead:
        """Hide the question from new exercises and system exams; history keeps it."""
        return self.update(question_id, QuestionUpdate(is_active=False))

    def activate(self, question_id: int) -> QuestionRead:
        return self.update(question_id, QuestionUpdate(is_active=True))

    # ------------------------------------------------------------- delete
    def delete(self, question_id: int) -> None:
        """Delete a question nobody uses yet. QuestionInUseError otherwise: use deactivate."""
        try:
            with self._db.session() as session:
                # plain DELETE: options / test cases go through ON DELETE CASCADE
                deleted = session.execute(sa.delete(Question).where(Question.id == question_id)).rowcount
                if not deleted:
                    raise QuestionNotFoundError(question_id)
        except IntegrityError as exc:
            if constraint_name(exc) in IN_USE_FK_CONSTRAINTS:
                raise QuestionInUseError(question_id) from exc
            raise

    def replace_answers(
        self,
        question_id: int,
        options: list[QuestionOptionCreate],
        test_cases: list[QuestionTestCaseCreate],
    ) -> QuestionRead:
        """Replace all options and test cases at once (the editor form sends the
        whole list). Checked as a whole, so it never fails half-way."""
        with self._db.session() as session:
            q = self._find(session, question_id)
            # explicit deletes: the relationships have no delete-orphan cascade
            session.execute(sa.delete(QuestionOption).where(QuestionOption.question_id == question_id))
            session.execute(sa.delete(QuestionTestCase).where(QuestionTestCase.question_id == question_id))
            session.expire(q, ["options", "test_cases"])
            q.options.extend(QuestionOption(**o.model_dump()) for o in options)
            q.test_cases.extend(QuestionTestCase(**t.model_dump()) for t in test_cases)
            check_question(q)
            session.flush()
            session.refresh(q)
            return QuestionRead.model_validate(self._find(session, question_id))

    # ------------------------------------------------------------ options
    def add_option(self, question_id: int, data: QuestionOptionCreate) -> QuestionOptionRead:
        with self._db.session() as session:
            q = self._find(session, question_id)
            option = QuestionOption(**data.model_dump())
            q.options.append(option)
            check_question(q)
            session.flush()
            return QuestionOptionRead.model_validate(option)

    def update_option(self, option_id: int, data: QuestionOptionUpdate) -> QuestionOptionRead:
        with self._db.session() as session:
            option = session.get(QuestionOption, option_id)
            if option is None:
                raise QuestionOptionNotFoundError(option_id)
            for field, value in data.model_dump(exclude_none=True).items():
                setattr(option, field, value)
            check_question(self._find(session, option.question_id))
            session.flush()
            return QuestionOptionRead.model_validate(option)

    def delete_option(self, option_id: int) -> None:
        with self._db.session() as session:
            option = session.get(QuestionOption, option_id)
            if option is None:
                raise QuestionOptionNotFoundError(option_id)
            q = self._find(session, option.question_id)
            q.options.remove(option)
            check_question(q)
            session.delete(option)

    # --------------------------------------------------------- test cases
    def add_test_case(self, question_id: int, data: QuestionTestCaseCreate) -> QuestionTestCaseRead:
        with self._db.session() as session:
            q = self._find(session, question_id)
            test_case = QuestionTestCase(**data.model_dump())
            q.test_cases.append(test_case)
            check_question(q)
            session.flush()
            return QuestionTestCaseRead.model_validate(test_case)

    def update_test_case(self, test_case_id: int, data: QuestionTestCaseUpdate) -> QuestionTestCaseRead:
        with self._db.session() as session:
            test_case = session.get(QuestionTestCase, test_case_id)
            if test_case is None:
                raise QuestionTestCaseNotFoundError(test_case_id)
            for field, value in data.model_dump(exclude_none=True).items():
                setattr(test_case, field, value)
            session.flush()
            return QuestionTestCaseRead.model_validate(test_case)

    def delete_test_case(self, test_case_id: int) -> None:
        with self._db.session() as session:
            test_case = session.get(QuestionTestCase, test_case_id)
            if test_case is None:
                raise QuestionTestCaseNotFoundError(test_case_id)
            q = self._find(session, test_case.question_id)
            q.test_cases.remove(test_case)
            check_question(q)
            session.delete(test_case)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _load(session: Session, question_ids: Sequence[int]) -> Sequence[Question]:
        if not question_ids:
            return []
        return session.scalars(
            sa.select(Question).options(*_WITH_CHILDREN).where(Question.id.in_(question_ids))
        ).all()

    @staticmethod
    def _find(session: Session, question_id: int) -> Question:
        q = session.get(Question, question_id, options=_WITH_CHILDREN)
        if q is None:
            raise QuestionNotFoundError(question_id)
        return q

    @staticmethod
    def _check_topic(session: Session, topic_id: int | None) -> None:
        if topic_id is not None and session.get(Topic, topic_id) is None:
            raise TopicNotFoundError(topic_id)

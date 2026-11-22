"""Grading one answer, and writing the attempt record (the md file).

Pure functions on ORM Question objects (options and test cases loaded): no
database access here, so the rules are easy to test and to change.

  mcq           all or nothing: the selected options must be exactly the
                correct ones
  short_answer  graded by a lecturer (the schema stores no expected answer)
  code          run against every test case by a CodeRunner, points split by
                test case weight; without a runner, graded by a lecturer

An unanswered question is worth 0 and never waits for manual grading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from src.attempt.AttemptSubmitDto import AnswerSubmit
from src.attempt.service.CodeRunner import CodeRunner, same_output
from src.question.models.Question import Question, QuestionType

CENT = Decimal("0.01")
PENDING_MARKER = "<!-- studiamo:pending {ids} -->"
_PENDING_RE = re.compile(r"<!-- studiamo:pending ([\d,]*) -->")


def money(value: Decimal | float | int) -> Decimal:
    """Round to 2 decimals like the numeric(7,2) columns."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class TestCaseOutcome:
    test_case_id: int
    passed: bool
    hidden: bool
    stdout: str = ""
    error: str | None = None


@dataclass
class GradedAnswer:
    question: Question
    position: int
    points_possible: Decimal
    answer: AnswerSubmit | None
    points_earned: Decimal | None = None        # None = pending manual grading
    test_outcomes: list[TestCaseOutcome] = field(default_factory=list)

    @property
    def answered(self) -> bool:
        return self.answer is not None

    @property
    def pending(self) -> bool:
        return self.points_earned is None

    @property
    def correct(self) -> bool | None:
        if self.pending:
            return None
        return self.points_earned >= self.points_possible


def grade(
    question: Question,
    answer: AnswerSubmit | None,
    points: Decimal,
    position: int,
    runner: CodeRunner | None,
) -> GradedAnswer:
    result = GradedAnswer(question=question, position=position, points_possible=money(points), answer=answer)
    if answer is None:
        result.points_earned = Decimal("0.00")
        return result

    if question.type == QuestionType.MCQ:
        correct = {o.id for o in question.options if o.is_correct}
        chosen = set(answer.selected_option_ids or [])
        result.points_earned = result.points_possible if chosen == correct else Decimal("0.00")

    elif question.type == QuestionType.CODE and runner is not None and (answer.answer_text or "").strip():
        total_weight = sum((Decimal(t.weight) for t in question.test_cases), Decimal(0))
        passed_weight = Decimal(0)
        for tc in question.test_cases:
            run = runner.run(question.code_language, answer.answer_text, tc.input)
            ok = run.error is None and same_output(run.stdout, tc.expected_output)
            if ok:
                passed_weight += Decimal(tc.weight)
            result.test_outcomes.append(
                TestCaseOutcome(tc.id, ok, tc.is_hidden, stdout=run.stdout, error=run.error)
            )
        result.points_earned = money(result.points_possible * passed_weight / total_weight) if total_weight else Decimal("0.00")

    elif question.type == QuestionType.MCQ or not (answer.answer_text or "").strip():
        result.points_earned = Decimal("0.00")
    # short answer, or code without a runner: stays pending

    return result


# ------------------------------------------------------------------ record
def _fence(text: str, lang: str = "") -> str:
    ticks = "````" if "```" in text else "```"
    return f"{ticks}{lang}\n{text.rstrip()}\n{ticks}"


def _quote(text: str) -> str:
    return "\n".join("> " + line for line in text.rstrip().splitlines()) or ">"


def render_record(
    *,
    title: str,
    student_id: int,
    attempt_id: int,
    attempt_no: int,
    started_at: datetime,
    submitted_at: datetime,
    graded: list[GradedAnswer],
    late: bool = False,
) -> str:
    """The md "attempt record": a snapshot of every question as the student saw
    it, their answer, the correct answer / test results and the points."""
    pending = [g.question.id for g in graded if g.pending]
    earned = sum((g.points_earned or Decimal(0) for g in graded), Decimal(0))
    possible = sum((g.points_possible for g in graded), Decimal(0))

    out = [
        PENDING_MARKER.format(ids=",".join(map(str, pending))),
        f"# {title} — attempt {attempt_no}",
        "",
        f"- Attempt id: {attempt_id}",
        f"- Student id: {student_id}",
        f"- Started: {started_at.isoformat()}",
        f"- Submitted: {submitted_at.isoformat()}",
        f"- Points: {money(earned)} / {money(possible)}" + (" (waiting for manual grading)" if pending else ""),
    ]
    if late:
        out.append("- **Submitted after the deadline: answers were not accepted.**")

    for g in sorted(graded, key=lambda x: x.position):
        q = g.question
        status = "pending" if g.pending else f"{g.points_earned} / {g.points_possible}"
        out += ["", f"## Question {g.position + 1} ({q.type.value}, id {q.id}) — {status}", "", q.body_md.rstrip(), ""]

        if q.type == QuestionType.MCQ:
            chosen = set(g.answer.selected_option_ids or []) if g.answer else set()
            out.append("| | Option | Correct |")
            out.append("|---|---|---|")
            for o in q.options:
                label = o.label.replace("|", "\\|").replace("\n", " ")
                out.append(f"| {'☑' if o.id in chosen else '☐'} | {label} | {'✔' if o.is_correct else ''} |")
        else:
            out.append("**Answer:**")
            out.append("")
            if not g.answered or not (g.answer.answer_text or "").strip():
                out.append("_No answer._")
            elif q.type == QuestionType.CODE:
                out.append(_fence(g.answer.answer_text, q.code_language.value if q.code_language else ""))
            else:
                out.append(_quote(g.answer.answer_text))
            if g.test_outcomes:
                out += ["", "| Test | Hidden | Result |", "|---|---|---|"]
                for i, t in enumerate(g.test_outcomes, 1):
                    verdict = "pass" if t.passed else ("error: " + t.error.splitlines()[0] if t.error else "wrong output")
                    out.append(f"| {i} | {'yes' if t.hidden else 'no'} | {verdict.replace('|', '/')} |")

        if q.explanation_md:
            out += ["", "**Explanation:**", "", q.explanation_md.rstrip()]

    return "\n".join(out) + "\n"


def pending_ids(record: str) -> list[int]:
    """Question ids still waiting for manual grading, read back from a record."""
    match = _PENDING_RE.search(record)
    if not match or not match.group(1):
        return []
    return [int(x) for x in match.group(1).split(",")]


def append_manual_grading(record: str, points: dict[int, Decimal], grader_id: int, comment_md: str | None) -> str:
    """Record the lecturer's points and clear the pending marker."""
    record = _PENDING_RE.sub(PENDING_MARKER.format(ids=""), record, count=1)
    lines = ["", "## Manual grading", "", f"- Graded by user {grader_id}"]
    lines += [f"- Question {qid}: {p}" for qid, p in points.items()]
    if comment_md:
        lines += ["", comment_md.rstrip()]
    return record.rstrip("\n") + "\n" + "\n".join(lines) + "\n"

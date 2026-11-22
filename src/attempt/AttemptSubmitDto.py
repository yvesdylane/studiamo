"""Request bodies for submitting an exercise / exam attempt.

These are NOT stored as rows. The service grades them and renders the
questions + the student's answers into the attempt's md file (md_path)."""

from __future__ import annotations

from pydantic import Field, model_validator

from src.base.BaseDto import DTO


class AnswerSubmit(DTO):
    question_id: int
    selected_option_ids: list[int] | None = None  # mcq
    answer_text: str | None = None  # short answer, or the code

    @model_validator(mode="after")
    def _has_answer(self):
        if not self.selected_option_ids and not (self.answer_text and self.answer_text.strip()):
            raise ValueError("an answer needs selected_option_ids or answer_text")
        return self


class AttemptSubmit(DTO):
    answers: list[AnswerSubmit] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_questions(self):
        ids = [a.question_id for a in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("each question can be answered only once")
        return self

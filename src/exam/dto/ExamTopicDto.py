"""DTOs for ExamTopic. ORM model: src/exam/ExamTopic.py."""

from __future__ import annotations

from src.base.BaseDto import DTO


class ExamTopicRead(DTO):
    exam_id: int
    topic_id: int

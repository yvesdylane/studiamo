"""Postgres enum type(s) used by the ORM: AttemptStatus."""

from __future__ import annotations

import enum


class AttemptStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    GRADED = "graded"

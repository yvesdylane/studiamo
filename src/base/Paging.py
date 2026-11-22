"""Shared paging limits, the same ones CourseService uses."""

from __future__ import annotations

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def clamp(limit: int, offset: int) -> tuple[int, int]:
    """limit forced into [1, MAX_PAGE_SIZE], offset into [0, ...)."""
    return max(1, min(limit, MAX_PAGE_SIZE)), max(0, offset)

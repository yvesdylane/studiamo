"""Errors raised by ProgressService."""

from __future__ import annotations

from src.base.ServiceErrors import ServiceError


class ProgressError(ServiceError):
    """Base class: catch this to handle every progress error at once."""

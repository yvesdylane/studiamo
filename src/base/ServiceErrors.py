"""Base classes shared by every service error.

Each module has its own errors (CourseNotFoundError, QuestionInUseError...),
and each of them also inherits one of these, so a route can map every service
error to an HTTP status in one place:

    NotFoundError          -> 404
    ConflictError          -> 409   (duplicate, already done, still in use)
    InvalidOperationError  -> 422   (breaks a business rule)
    PermissionDeniedError  -> 403
"""

from __future__ import annotations

from typing import NoReturn

from sqlalchemy.exc import IntegrityError


class ServiceError(Exception):
    """Base class of every error a service raises on purpose."""


class NotFoundError(ServiceError):
    pass


class ConflictError(ServiceError):
    pass


class InvalidOperationError(ServiceError):
    pass


class PermissionDeniedError(ServiceError):
    pass


def constraint_name(exc: IntegrityError) -> str | None:
    """Name of the Postgres constraint an IntegrityError broke, if any."""
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def reraise(exc: IntegrityError, error: ServiceError | None) -> NoReturn:
    """Inside `except IntegrityError as exc:` raise the matching service error,
    or exc itself when the broken constraint is not one the service knows."""
    if error is None:
        raise exc
    raise error from exc
